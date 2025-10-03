from __future__ import annotations
import json
import os
from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import datetime
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


app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False
app.config["JSON_SORT_KEYS"] = False

device_data: dict[str, dict[str, JSONLike]] = {}
device_commands: dict[str, dict[str, JSONLike]] = {}
device_command_seq: defaultdict[str, int] = defaultdict(int)

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1418986890908008468/_T2UEJwR6s4xcsbwg7acLaAfBLgD763fEVxN2BqyuUtgHUeu147lY7-k5BrKVUnIA3hM"

MAX_HISTORY_IN_MEMORY: int = 500

device_history: defaultdict[str, deque[dict[str, CSVValue]]] = defaultdict(
    lambda: deque(maxlen=MAX_HISTORY_IN_MEMORY)
)
alert_below_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
alert_sent: defaultdict[str, bool] = defaultdict(bool)
alert_empty_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
alert_empty_sent: defaultdict[str, bool] = defaultdict(bool)
alert_partial_since: defaultdict[str, datetime | None] = defaultdict(lambda: None)
alert_partial_sent: defaultdict[str, bool] = defaultdict(bool)


def load_cfg() -> dict[str, float]:
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
    device_command_seq[device_id] += 1
    cmd_id = device_command_seq[device_id]
    command: dict[str, JSONLike] = {
        "deviceId": device_id,
        "commandId": cmd_id,
        **payload,
        "serverTimestamp": datetime.now().isoformat(),
    }
    device_commands[device_id] = command
    return command


def discord_send(content: str) -> None:
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
        pass
    except Exception:
        pass


def calculate_fill_status(
    distance: float | None, threshold: float, empty_threshold: float
) -> str:
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

    if d <= threshold:
        since = alert_below_since[device_id]
        if since is None:
            alert_below_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        if (sustain <= 0 or elapsed >= sustain) and not alert_sent[device_id]:
            msg = (
                f"Alert: Device {device_id} distance {d:.2f} cm "
                f"is at/below threshold {threshold:.2f} cm for "
                f"{elapsed:.1f}s (>= {sustain:.1f}s)."
            )
            discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyFull"})
            alert_sent[device_id] = True
        alert_empty_since[device_id] = None
        alert_empty_sent[device_id] = False
        alert_partial_since[device_id] = None
        alert_partial_sent[device_id] = False
    elif d <= partial_threshold:
        since = alert_partial_since[device_id]
        if since is None:
            alert_partial_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        if (sustain <= 0 or elapsed >= sustain) and not alert_partial_sent[device_id]:
            # msg = (
            #     f"Alert: Device {device_id} is 3/4 full - distance {d:.2f} cm "
            #     f"for {elapsed:.1f}s (>= {sustain:.1f}s)."
            # )
            # discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyPartial"})
            alert_partial_sent[device_id] = True
        if alert_sent[device_id]:
            alert_below_since[device_id] = None
            alert_sent[device_id] = False
        alert_empty_since[device_id] = None
        alert_empty_sent[device_id] = False
    elif d >= empty_threshold:
        since = alert_empty_since[device_id]
        if since is None:
            alert_empty_since[device_id] = now
            since = now
        elapsed = (now - since).total_seconds()
        if (sustain <= 0 or elapsed >= sustain) and not alert_empty_sent[device_id]:
            # msg = (
            #     f"Alert: Device {device_id} is empty - distance {d:.2f} cm "
            #     f"for {elapsed:.1f}s (>= {sustain:.1f}s)."
            # )
            # discord_send(msg)
            _ = enqueue_command(device_id, {"action": "notifyEmpty"})
            alert_empty_sent[device_id] = True
        if alert_sent[device_id]:
            alert_below_since[device_id] = None
            alert_sent[device_id] = False
        alert_partial_since[device_id] = None
        alert_partial_sent[device_id] = False
    else:
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
    if not os.path.exists(CSV_PATH):
        return []
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

    # newest-first
    rows.reverse()
    total = len(rows)
    if page_size <= 0:
        page_size = 25
    if page <= 0:
        page = 1
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], total


def pick_did(requested_id: str | None, strict_csv: bool) -> str | None:
    if requested_id:
        return requested_id
    if device_data:
        return next(iter(device_data.keys()))
    if strict_csv:
        csv_device = csv_last_device()
        if not csv_device:
            return None
        return csv_device
    return csv_last_device()


def mem_hist(device_id: str, limit: int) -> list[dict[str, CSVValue]]:
    mem_series: list[dict[str, CSVValue]] = list(device_history.get(device_id, []))
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
    if device_id not in device_history:
        return [], 0
    mem_rows: list[dict[str, CSVValue]] = list(device_history.get(device_id, []))
    mem_rows.reverse()  # newest-first
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
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {SITE_AUTH_PASS}":
        return err("Unauthorized", 401)

    data = json_in()
    if not data:
        return err("No data provided", 400)

    device_id = str(data.get("deviceId") or "unknown")
    now = datetime.now().isoformat()

    data["serverTimestamp"] = now
    dist_val = pfloat(data.get("distance"))
    thr = pfloat(settings.get("thresholdCm"), 5) or 5
    empty_thr = pfloat(settings.get("emptyThresholdCm"), 15) or 15
    is_full = 1 if (dist_val is not None and dist_val <= thr) else 0
    fill_status = calculate_fill_status(dist_val, thr, empty_thr)
    data["isFull"] = is_full
    data["fillStatus"] = fill_status
    device_data[device_id] = data

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
    device_history[device_id].append(point)

    try:
        alert_eval(device_id, data.get("distance"))
    except Exception:
        pass

    try:
        csv_append(cast(Mapping[str, JSONLike], data))
    except Exception:
        pass

    return jsonify({"status": "ok", "deviceId": device_id, "serverTimestamp": now})


@app.route("/api/command", methods=["POST", "GET"])
def command_api():
    if request.method == "POST":
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

        if action in ("open", "activate") and target is None:
            target = 90
            action = "setAngle"
        elif action in ("close", "deactivate") and target is None:
            target = 0
            action = "setAngle"

        if action == "auto":
            payload: dict[str, JSONLike] = {"action": "auto"}
        elif action == "notify":
            payload = {"action": "notify"}
        else:
            t_raw = target
            if t_raw is None or not isinstance(t_raw, (int, float, str, bool)):
                return jsonify(
                    {"error": "targetPosition required/int for setAngle"}
                ), 400

            try:
                target_int = int(t_raw)
            except (TypeError, ValueError):
                return err("targetPosition required/int for setAngle", 400)

            target_int = clamp_deg(target_int)
            payload = {"action": "setAngle", "targetPosition": target_int}

        command = enqueue_command(device_id, payload)
        return jsonify({"status": "ok", **command})

    device_id = arg_str("deviceId")
    if not device_id:
        if not device_data:
            return jsonify({})
        device_id = next(iter(device_data.keys()))
    last_id = arg_int("lastId", 0)
    cmd = device_commands.get(device_id)
    if not cmd:
        return jsonify({})
    raw_cid = cmd.get("commandId", 0)
    if isinstance(raw_cid, (int, float, str, bool)):
        cmd_id_val = int(raw_cid)
    else:
        cmd_id_val = 0
    if cmd_id_val <= last_id:
        return jsonify({})
    return jsonify(cmd)


@app.route("/api/history")
def history_api():
    device_id = pick_did(arg_str("deviceId"), strict_csv=True)

    limit = arg_int("limit", 100)

    if not device_id:
        return jsonify({})

    series = csv_hist(device_id, limit)

    if not series:
        series = mem_hist(device_id, limit)
        if not series:
            return jsonify({})

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
    device_id = pick_did(arg_str("deviceId"), strict_csv=False)

    page = arg_int("page", 1)

    page_size = arg_int("pageSize", 25)

    rows, total = csv_hist_page(device_id, page, page_size)

    if not rows:
        rows, total = mem_hist_page(device_id, page, page_size)

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
    ids: list[str] = list(device_data.keys())

    if not ids:
        ids = csv_devices(max_devices=None)

    return jsonify({"deviceIds": ids})


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    if request.method == "GET":
        return jsonify(
            {
                "thresholdCm": settings.get("thresholdCm", 5),
                "emptyThresholdCm": settings.get("emptyThresholdCm", 15),
                "alertSustainSec": settings.get("alertSustainSec", 3),
            }
        )
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
SITE_AUTH_USER = "trash"
SITE_AUTH_PASS = "trash_123"


def auth_resp() -> Response:
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Restricted"'},
    )


def require_auth(f: Callable[P, R]) -> Callable[P, R]:
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
