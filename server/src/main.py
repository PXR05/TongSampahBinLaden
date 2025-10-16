from __future__ import annotations
import json
import os
from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import datetime
from dotenv import load_dotenv
from typing import Callable, ParamSpec, TypeVar, cast


import requests
from functools import wraps


from flask import Flask, jsonify, render_template, request, Response
from .utils import (
    JSONLike,
    DEFAULT_THRESHOLD_CM,
    DEFAULT_EMPTY_THRESHOLD_CM,
    DEFAULT_ALERT_SUSTAIN_SEC,
    pbool,
    pint,
    pfloat,
    json_in,
    err,
    clamp_deg,
    arg_int,
    arg_str,
)
from .storage import (
    CSV_PATH,
    SETTINGS_PATH,
    CSVValue,
    csv_append,
    csv_last_device,
    csv_rows,
    csv_val,
)

success = load_dotenv()
if not success:
    print("Warning: .env file not found or could not be loaded.")

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False
app.config["JSON_SORT_KEYS"] = False

# In-memory storage for latest data from each device
device_data: dict[str, dict[str, JSONLike]] = {}
# Pending commands to be sent to devices (one per device, latest command only)
device_commands: dict[str, dict[str, JSONLike]] = {}
# Incrementing sequence number for commands per device
device_command_seq: defaultdict[str, int] = defaultdict(int)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK")

MAX_HISTORY_IN_MEMORY: int = 500

# Circular buffer storing recent history for each device (auto-discards old entries)
device_history: defaultdict[str, deque[dict[str, CSVValue]]] = defaultdict(
    lambda: deque(maxlen=MAX_HISTORY_IN_MEMORY)
)
# Alert tracking: timestamp when "full" condition first detected per device
alert_below_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
# Alert tracking: whether "full" alert has been sent (prevents duplicate alerts)
alert_sent: defaultdict[str, bool] = defaultdict(bool)
# Alert tracking: timestamp when "empty" condition first detected per device
alert_empty_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
# Alert tracking: whether "empty" alert has been sent
alert_empty_sent: defaultdict[str, bool] = defaultdict(bool)
# Alert tracking: timestamp when "partial" (3/4 full) condition first detected
alert_partial_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
# Alert tracking: whether "partial" alert has been sent
alert_partial_sent: defaultdict[str, bool] = defaultdict(bool)


def load_cfg() -> dict[str, float]:
    """Load settings from JSON file, with fallback to defaults if file missing or invalid."""
    default: dict[str, float] = {
        "thresholdCm": DEFAULT_THRESHOLD_CM,
        "alertSustainSec": DEFAULT_ALERT_SUSTAIN_SEC,
        "emptyThresholdCm": DEFAULT_EMPTY_THRESHOLD_CM,
    }
    if not os.path.exists(SETTINGS_PATH):
        return default
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = cast(Mapping[str, JSONLike], json.load(f))
            # Parse and validate each setting, using 'or' to handle None/0 cases
            t = (
                pfloat(data.get("thresholdCm"), DEFAULT_THRESHOLD_CM)
                or DEFAULT_THRESHOLD_CM
            )
            s = (
                pfloat(data.get("alertSustainSec"), DEFAULT_ALERT_SUSTAIN_SEC)
                or DEFAULT_ALERT_SUSTAIN_SEC
            )
            e = (
                pfloat(data.get("emptyThresholdCm"), DEFAULT_EMPTY_THRESHOLD_CM)
                or DEFAULT_EMPTY_THRESHOLD_CM
            )
            # Ensure all values are non-negative
            return {
                "thresholdCm": max(0.0, float(t)),
                "alertSustainSec": max(0.0, float(s)),
                "emptyThresholdCm": max(0.0, float(e)),
            }
    except Exception:
        return default


def save_cfg(s: dict[str, float]) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


settings: dict[str, float] = load_cfg()


def enqueue_command(
    device_id: str, payload: dict[str, JSONLike]
) -> dict[str, JSONLike]:
    """
    Queue a command for a device. Each device only keeps the latest command.
    Commands have incrementing IDs so devices can detect new commands.
    """
    device_command_seq[device_id] += 1
    cmd_id = device_command_seq[device_id]
    command: dict[str, JSONLike] = {
        "deviceId": device_id,
        "commandId": cmd_id,
        **payload,
        "serverTimestamp": datetime.now().isoformat(),
    }
    # Overwrites any previous command for this device
    device_commands[device_id] = command
    return command


def discord_send(content: str) -> None:
    """Send notification to Discord webhook. Silently fails if webhook not configured or request fails."""
    if not DISCORD_WEBHOOK_URL:
        return
    payload = json.dumps({"content": content}).encode("utf-8")
    res = requests.post(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    try:
        res.raise_for_status()
    except requests.RequestException:
        pass  # Silently ignore Discord notification failures
    except Exception:
        pass


def calculate_fill_status(
    distance: float | None, threshold: float, empty_threshold: float
) -> str:
    """
    Determine trash bin fill status based on distance sensor reading.
    Lower distance = more full (sensor measures from top of bin to trash surface).
    """
    if distance is None:
        return "unknown"

    partial_threshold = threshold * 1.33

    if distance <= threshold:
        return "full"
    elif distance <= partial_threshold:
        return "partial"
    elif distance >= empty_threshold:
        return "empty"
    else:
        return "partial"


def alert_eval(device_id: str, distance: JSONLike) -> None:
    """
    Evaluate whether to send alerts based on bin fill level.
    Alerts require condition to persist for 'sustain' seconds to avoid false positives.
    State machine resets when transitioning between states to prevent alert spam.
    """
    d = pfloat(distance)
    if d is None:
        return
    threshold = (
        pfloat(settings.get("thresholdCm"), DEFAULT_THRESHOLD_CM)
        or DEFAULT_THRESHOLD_CM
    )
    sustain = (
        pfloat(settings.get("alertSustainSec"), DEFAULT_ALERT_SUSTAIN_SEC)
        or DEFAULT_ALERT_SUSTAIN_SEC
    )
    now = datetime.now()

    empty_threshold = (
        pfloat(settings.get("emptyThresholdCm"), DEFAULT_EMPTY_THRESHOLD_CM)
        or DEFAULT_EMPTY_THRESHOLD_CM
    )
    partial_threshold = threshold * 1.33

    # STATE 1: BIN IS FULL
    if d <= threshold:
        since = alert_below_since[device_id]
        if since is None:
            # Start tracking how long bin has been full
            alert_below_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        # Send alert only if sustained for required duration and not already sent
        if (sustain <= 0 or elapsed >= sustain) and not alert_sent[device_id]:
            msg = (
                f"Alert: Device {device_id} distance {d:.2f} cm "
                f"is at/below threshold {threshold:.2f} cm for "
                f"{elapsed:.1f}s (>= {sustain:.1f}s)."
            )
            discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyFull"})
            alert_sent[device_id] = True
        # Reset other alert states (can't be empty or partial if full)
        alert_empty_since[device_id] = None
        alert_empty_sent[device_id] = False
        alert_partial_since[device_id] = None
        alert_partial_sent[device_id] = False
    # STATE 2: BIN IS PARTIAL
    elif d <= partial_threshold:
        since = alert_partial_since[device_id]
        if since is None:
            alert_partial_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        if (sustain <= 0 or elapsed >= sustain) and not alert_partial_sent[device_id]:
            # Discord notification commented out, only device notification sent
            # msg = (
            #     f"Alert: Device {device_id} is 3/4 full - distance {d:.2f} cm "
            #     f"for {elapsed:.1f}s (>= {sustain:.1f}s)."
            # )
            # discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyPartial"})
            alert_partial_sent[device_id] = True
        # If transitioning from full to partial, reset full alert
        if alert_sent[device_id]:
            alert_below_since[device_id] = None
            alert_sent[device_id] = False
        alert_empty_since[device_id] = None
        alert_empty_sent[device_id] = False
    # STATE 3: BIN IS EMPTY
    elif d >= empty_threshold:
        since = alert_empty_since[device_id]
        if since is None:
            alert_empty_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        if (sustain <= 0 or elapsed >= sustain) and not alert_empty_sent[device_id]:
            # Discord notification commented out, only device notification sent
            # msg = (
            #     f"Alert: Device {device_id} is empty - distance {d:.2f} cm "
            #     f"for {elapsed:.1f}s (>= {sustain:.1f}s)."
            # )
            # discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyEmpty"})
            alert_empty_sent[device_id] = True
        # If transitioning from full to empty, reset full alert
        if alert_sent[device_id]:
            alert_below_since[device_id] = None
            alert_sent[device_id] = False
        alert_partial_since[device_id] = None
        alert_partial_sent[device_id] = False
    # STATE 4: NORMAL (between partial and empty thresholds)
    else:
        # Reset all alert states when in normal range
        if alert_sent[device_id]:
            alert_below_since[device_id] = None
            alert_sent[device_id] = False
        if alert_empty_sent[device_id]:
            alert_empty_since[device_id] = None
            alert_empty_sent[device_id] = False
        if alert_partial_sent[device_id]:
            alert_partial_since[device_id] = None
            alert_partial_sent[device_id] = False


def csv_hist(device_id: str, limit: int = 100) -> list[dict[str, CSVValue]]:
    """
    Retrieve history from CSV file for a specific device.
    Uses a deque with maxlen to automatically keep only the last N entries.
    """
    if not os.path.exists(CSV_PATH):
        return []
    # Circular buffer automatically discards oldest entries when limit reached
    buf: deque[dict[str, CSVValue]] = deque(maxlen=limit)
    try:
        for row in csv_rows() or []:
            if device_id and row.get("deviceId") != device_id:
                continue
            buf.append(
                {
                    "serverTimestamp": row.get("serverTimestamp"),
                    "distance": pfloat(row.get("distance")),
                    "servoPosition": pint(row.get("servoPosition")),
                    "motion": pbool(row.get("motion")),
                }
            )
    except Exception:
        return []
    return list(buf)


def csv_devices(max_devices: int | None = None) -> list[str]:
    """
    Extract list of unique device IDs from CSV file.
    Uses both a list (for order) and set (for O(1) duplicate checking).
    """
    if not os.path.exists(CSV_PATH):
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    try:
        for row in csv_rows() or []:
            did = (row.get("deviceId") or "").strip()
            if not did or did in seen_set:
                continue
            seen.append(did)
            seen_set.add(did)
            if max_devices and len(seen) >= max_devices:
                break
    except Exception:
        return []
    return seen


def csv_hist_page(
    device_id: str | None, page: int = 1, page_size: int = 25
) -> tuple[list[dict[str, CSVValue]], int]:
    """
    Retrieve paginated history from CSV file.
    Returns a tuple of (page_data, total_count) for pagination UI.
    """
    if not os.path.exists(CSV_PATH):
        return [], 0
    rows: list[dict[str, CSVValue]] = []
    try:
        for row in csv_rows() or []:
            if device_id and row.get("deviceId") != device_id:
                continue
            rows.append(
                {
                    "serverTimestamp": row.get("serverTimestamp"),
                    "deviceId": row.get("deviceId"),
                    "deviceTimestamp": row.get("deviceTimestamp"),
                    "deviceUptimeMs": pint(row.get("deviceUptimeMs")),
                    "distance": pfloat(row.get("distance")),
                    "motion": pbool(row.get("motion")),
                    "servoPosition": pint(row.get("servoPosition")),
                    "targetPosition": pint(row.get("targetPosition")),
                    "shouldActivateServo": pbool(row.get("shouldActivateServo")),
                    "isFull": pbool(row.get("isFull")),
                    "fillStatus": row.get("fillStatus", "unknown"),
                }
            )
    except Exception:
        return [], 0

    # Reverse to show newest entries first
    rows.reverse()
    total = len(rows)
    # Validate pagination parameters
    if page_size <= 0:
        page_size = 25
    if page <= 0:
        page = 1
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], total


def pick_did(requested_id: str | None, strict_csv: bool) -> str | None:
    """
    Intelligently select a device ID when none is specified.
    Priority: requested > in-memory devices > CSV devices (if strict_csv).
    """
    if requested_id:
        return requested_id
    # Try to get first device from in-memory data
    if device_data:
        return next(iter(device_data.keys()))
    # If strict_csv, require CSV data; otherwise, try CSV as fallback
    if strict_csv:
        csv_device = csv_last_device()
        if not csv_device:
            return None
        return csv_device
    return csv_last_device()


def mem_hist(device_id: str, limit: int) -> list[dict[str, CSVValue]]:
    """
    Retrieve history from in-memory cache (faster than CSV but limited retention).
    Returns only the most recent 'limit' entries.
    """
    mem_series: list[dict[str, CSVValue]] = list(device_history.get(device_id, []))
    # Slice to last N entries if we have more than limit
    if limit < len(mem_series):
        mem_series = mem_series[-limit:]
    if not mem_series:
        return []
    return [
        {
            "serverTimestamp": p.get("serverTimestamp"),
            "distance": p.get("distance"),
            "servoPosition": p.get("servoPosition"),
            "motion": p.get("motion"),
        }
        for p in mem_series
    ]


def mem_hist_page(
    device_id: str | None, page: int, page_size: int
) -> tuple[list[dict[str, CSVValue]], int]:
    """
    Retrieve paginated history from in-memory cache.
    Fallback when CSV data is not available or for real-time data access.
    """
    if device_id not in device_history:
        return [], 0
    mem_rows: list[dict[str, CSVValue]] = list(device_history.get(device_id, []))
    mem_rows.reverse()  # Show newest entries first
    total = len(mem_rows)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    sliced = mem_rows[start:end]
    rows = [
        {
            "serverTimestamp": p.get("serverTimestamp"),
            "deviceId": device_id,
            "deviceTimestamp": p.get("deviceTimestamp"),
            "deviceUptimeMs": p.get("deviceUptimeMs"),
            "distance": p.get("distance"),
            "motion": p.get("motion"),
            "servoPosition": p.get("servoPosition"),
            "targetPosition": p.get("targetPosition"),
            "shouldActivateServo": p.get("shouldActivateServo"),
            "isFull": p.get("isFull"),
            "fillStatus": p.get("fillStatus", "unknown"),
        }
        for p in sliced
    ]
    return rows, total


@app.route("/api/sensor-data", methods=["POST"])
def sensor_in():
    """
    Main endpoint for IoT devices to post sensor data.
    Processes data, stores it in memory & CSV, evaluates alerts.
    """
    # Bearer token authentication for device API
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {SITE_AUTH_PASS}":
        return err("Unauthorized", 401)

    data = json_in()
    if not data:
        return err("No data provided", 400)

    device_id = str(data.get("deviceId") or "unknown")
    now = datetime.now().isoformat()

    # Augment device data with server-side calculations
    data["serverTimestamp"] = now
    dist_val = pfloat(data.get("distance"))
    thr = pfloat(settings.get("thresholdCm"), 5) or 5
    empty_thr = pfloat(settings.get("emptyThresholdCm"), 15) or 15
    is_full = 1 if (dist_val is not None and dist_val <= thr) else 0
    fill_status = calculate_fill_status(dist_val, thr, empty_thr)
    data["isFull"] = is_full
    data["fillStatus"] = fill_status
    # Store latest state in memory (overwrites previous)
    device_data[device_id] = data

    # Prepare point for history tracking
    point: dict[str, CSVValue] = {
        "serverTimestamp": now,
        "deviceTimestamp": csv_val(data.get("deviceTimestamp")),
        "deviceUptimeMs": csv_val(data.get("deviceUptimeMs")),
        "distance": csv_val(data.get("distance")),
        "servoPosition": csv_val(data.get("servoPosition")),
        "targetPosition": csv_val(data.get("targetPosition")),
        "motion": 1 if data.get("motion") else 0,
        "isFull": is_full,
        "fillStatus": fill_status,
    }
    # Add to circular buffer (auto-discards old entries)
    device_history[device_id].append(point)

    # Check if alerts should be triggered (non-blocking)
    try:
        alert_eval(device_id, data.get("distance"))
    except Exception:
        pass

    # Persist to CSV (non-blocking, failures silently ignored)
    try:
        csv_append(cast(Mapping[str, JSONLike], data))
    except Exception:
        pass

    return jsonify({"status": "ok", "deviceId": device_id, "serverTimestamp": now})


@app.route("/api/command", methods=["POST", "GET"])
def command_api():
    """
    Dual-purpose endpoint:
    POST: Web dashboard sends commands to be queued for devices
    GET: IoT devices poll for pending commands (long-polling pattern)
    """
    if request.method == "POST":
        # POST: Enqueue command from dashboard (requires auth)
        auth = request.authorization
        if (
            not auth
            or auth.username != SITE_AUTH_USER
            or auth.password != SITE_AUTH_PASS
        ):
            return auth_resp()

        data = json_in()
        device_id = data.get("deviceId")
        if not device_id or not isinstance(device_id, str):
            return err("deviceId required", 400)

        action = str(data.get("action") or "").strip() or "setAngle"
        target = data.get("targetPosition")

        # Normalize action aliases to standard commands
        if action in ("open", "activate") and target is None:
            target = 90
            action = "setAngle"
        elif action in ("close", "deactivate") and target is None:
            target = 0
            action = "setAngle"

        # Build command payload based on action type
        if action == "auto":
            payload: dict[str, JSONLike] = {"action": "auto"}
        elif action == "notify":
            payload = {"action": "notify"}
        else:
            # Default to setAngle with position validation
            t_raw = target
            if t_raw is None or not isinstance(t_raw, (int, float, str, bool)):
                return jsonify(
                    {"error": "targetPosition required/int for setAngle"}
                ), 400

            try:
                target_int = int(t_raw)
            except (TypeError, ValueError):
                return err("targetPosition required/int for setAngle", 400)

            target_int = clamp_deg(target_int)  # Ensure 0-180 degree range
            payload = {"action": "setAngle", "targetPosition": target_int}

        command = enqueue_command(device_id, payload)
        return jsonify({"status": "ok", **command})

    # GET: Device polling for new commands
    device_id = arg_str("deviceId")
    if not device_id:
        # Auto-select first available device if none specified
        if not device_data:
            return jsonify({})
        device_id = next(iter(device_data.keys()))
    last_id = arg_int("lastId", 0)
    cmd = device_commands.get(device_id)
    if not cmd:
        return jsonify({})
    # Extract command ID for comparison
    raw_cid = cmd.get("commandId", 0)
    if isinstance(raw_cid, (int, float, str, bool)):
        cmd_id_val = int(raw_cid)
    else:
        cmd_id_val = 0
    # Only return command if it's newer than what device last received
    if cmd_id_val <= last_id:
        return jsonify({})
    return jsonify(cmd)


@app.route("/api/history")
def history_api():
    """
    Return time-series data for charting/visualization.
    Data structured as parallel arrays for easy plotting.
    Tries CSV first, falls back to in-memory cache.
    """
    device_id = pick_did(arg_str("deviceId"), strict_csv=True)

    limit = arg_int("limit", 100)

    if not device_id:
        return jsonify({})

    # Try CSV first for longer history
    series = csv_hist(device_id, limit)

    # Fallback to in-memory if CSV unavailable
    if not series:
        series = mem_hist(device_id, limit)
        if not series:
            return jsonify({})

    # Return as parallel arrays (efficient for charting libraries)
    return jsonify(
        {
            "deviceId": device_id,
            "timestamps": [p.get("serverTimestamp") for p in series],
            "distance": [p.get("distance") for p in series],
            "servo": [p.get("servoPosition") for p in series],
            "motion": [p.get("motion") for p in series],
            "fillStatus": [p.get("fillStatus") for p in series],
        }
    )


@app.route("/api/history-page")
def history_page_api():
    """
    Return paginated history for table display in UI.
    Includes pagination metadata for building page controls.
    """
    device_id = pick_did(arg_str("deviceId"), strict_csv=False)

    page = arg_int("page", 1)

    page_size = arg_int("pageSize", 25)

    # Try CSV first
    rows, total = csv_hist_page(device_id, page, page_size)

    # Fallback to in-memory cache
    if not rows:
        rows, total = mem_hist_page(device_id, page, page_size)

    # Calculate total pages using ceiling division
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    return jsonify(
        {
            "deviceId": device_id,
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": total_pages,
            "rows": rows,
        }
    )


@app.route("/api/devices")
def devices_api():
    """Return list of all known device IDs (from memory or CSV)."""
    # Try in-memory devices first (currently active)
    ids: list[str] = list(device_data.keys())

    # Fallback to CSV for historical devices
    if not ids:
        ids = csv_devices(max_devices=None)

    return jsonify({"deviceIds": ids})


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    """
    GET: Retrieve current threshold and alert settings
    POST: Update settings and persist to disk
    """
    if request.method == "GET":
        return jsonify(
            {
                "thresholdCm": settings.get("thresholdCm", 5),
                "emptyThresholdCm": settings.get("emptyThresholdCm", 15),
                "alertSustainSec": settings.get("alertSustainSec", 3),
            }
        )
    # POST: Update settings
    data = cast(dict[str, JSONLike], request.get_json(silent=True) or {})
    updated_any = False

    if "thresholdCm" in data:
        t = pfloat(data.get("thresholdCm"))
        if t is None:
            return jsonify({"error": "thresholdCm must be a number"}), 400
        t = max(0.0, float(t))
        settings["thresholdCm"] = t
        updated_any = True

    if "emptyThresholdCm" in data:
        e = pfloat(data.get("emptyThresholdCm"))
        if e is None:
            return jsonify({"error": "emptyThresholdCm must be a number"}), 400
        e = max(0.0, float(e))
        settings["emptyThresholdCm"] = e
        updated_any = True

    if "alertSustainSec" in data:
        s = pfloat(data.get("alertSustainSec"))
        if s is None:
            return jsonify({"error": "alertSustainSec must be a number"}), 400
        s = max(0.0, float(s))
        settings["alertSustainSec"] = s
        updated_any = True

    if not updated_any:
        return jsonify({"error": "No valid settings provided"}), 400

    # Persist changes to disk
    save_cfg(settings)

    return jsonify(
        {
            "status": "ok",
            "thresholdCm": settings.get("thresholdCm", 5),
            "emptyThresholdCm": settings.get("emptyThresholdCm", 15),
            "alertSustainSec": settings.get("alertSustainSec", 3),
        }
    )


P = ParamSpec("P")
R = TypeVar("R")
SITE_AUTH_USER = os.getenv("SITE_AUTH_USER", "trash")
SITE_AUTH_PASS = os.getenv("SITE_AUTH_PASS", "trash_123")


def auth_resp() -> Response:
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Restricted"'},
    )


def require_auth(f: Callable[P, R]) -> Callable[P, R]:
    """Decorator to enforce HTTP Basic Auth on routes (for web dashboard access)."""

    @wraps(f)
    def decorated(*args: P.args, **kwargs: P.kwargs) -> R:
        auth = request.authorization

        if (
            not auth
            or auth.username != SITE_AUTH_USER
            or auth.password != SITE_AUTH_PASS
        ):
            return cast(R, auth_resp())

        return f(*args, **kwargs)

    return decorated


@app.route("/api/device-data")
def device_data_api():
    return jsonify(device_data)


@app.route("/")
@require_auth
def view_dashboard():
    return render_template("dashboard.html")


@app.route("/history")
@require_auth
def view_history():
    return render_template("history.html")


@app.route("/health")
def health_api():
    """Health check endpoint for monitoring/load balancers."""
    return jsonify(
        {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "devices_connected": len(device_data),
        }
    )


def run() -> None:
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    run()
