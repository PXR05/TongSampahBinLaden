from flask import Flask, request, jsonify, render_template
from datetime import datetime
import os
import csv
import json
import requests
from collections import defaultdict, deque

app = Flask(__name__)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False
app.config["JSON_SORT_KEYS"] = False

device_data = {}
device_commands = {}
device_command_seq = defaultdict(int)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "sensor_data.csv")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1418986890908008468/_T2UEJwR6s4xcsbwg7acLaAfBLgD763fEVxN2BqyuUtgHUeu147lY7-k5BrKVUnIA3hM"

CSV_FIELDS = [
    "serverTimestamp",
    "deviceId",
    "deviceTimestamp",
    "deviceUptimeMs",
    "distance",
    "motion",
    "servoPosition",
    "targetPosition",
    "shouldActivateServo",
    "isFull",
]


def append_csv(row: dict):
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        safe = {k: row.get(k, "") for k in CSV_FIELDS}
        writer.writerow(safe)


HISTORY_LIMIT = 500
device_history = defaultdict(lambda: deque(maxlen=HISTORY_LIMIT))
alert_below = defaultdict(bool)


def _parse_bool(val):
    if isinstance(val, bool):
        return 1 if val else 0
    if val is None:
        return 0
    s = str(val).strip().lower()
    return 1 if s in ("1", "true", "yes", "y", "on") else 0


def _parse_int(val, default=None):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_float(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def load_settings():
    default = {"thresholdCm": 5.0}
    if not os.path.exists(SETTINGS_PATH):
        return default
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            t = _parse_float(data.get("thresholdCm"), 5.0) or 5.0
            return {"thresholdCm": max(0.0, float(t))}
    except Exception:
        return default


def save_settings(s: dict):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


settings = load_settings()


def send_discord_message(content: str):
    if not DISCORD_WEBHOOK_URL:
        return
    payload = json.dumps({"content": content}).encode("utf-8")
    res = requests.post(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        res.raise_for_status()
    except requests.RequestException:
        pass
    except Exception:
        pass


def alert_check(device_id: str, distance):
    try:
        d = _parse_float(distance)
    except Exception:
        d = None
    if d is None:
        return
    threshold = _parse_float(settings.get("thresholdCm"), 5) or 5
    is_below = d <= threshold
    was_below = alert_below[device_id]
    if is_below and not was_below:
        msg = f"Alert: Device {device_id} distance {d:.2f} cm is at/below threshold {threshold:.2f} cm."
        send_discord_message(msg)
    alert_below[device_id] = is_below


def get_last_device_from_csv():
    if not os.path.exists(CSV_PATH):
        return None
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            last_row = deque(reader, maxlen=1)
            if not last_row:
                return None
            return last_row[0].get("deviceId") or None
    except Exception:
        return None


def read_history_from_csv(device_id, limit: int = 100):
    if not os.path.exists(CSV_PATH):
        return []
    buf = deque(maxlen=limit)
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if device_id and row.get("deviceId") != device_id:
                    continue
                buf.append(
                    {
                        "serverTimestamp": row.get("serverTimestamp"),
                        "distance": _parse_float(row.get("distance")),
                        "servoPosition": _parse_int(row.get("servoPosition")),
                        "motion": _parse_bool(row.get("motion")),
                    }
                )
    except Exception:
        return []
    return list(buf)


def list_devices_from_csv(max_devices: int | None = None):
    if not os.path.exists(CSV_PATH):
        return []
    seen = []
    seen_set = set()
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                did = row.get("deviceId") or ""
                if not did or did in seen_set:
                    continue
                seen.append(did)
                seen_set.add(did)
                if max_devices and len(seen) >= max_devices:
                    break
    except Exception:
        return []
    return seen


def read_history_page_from_csv(
    device_id: str | None, page: int = 1, page_size: int = 25
):
    if not os.path.exists(CSV_PATH):
        return [], 0
    rows = []
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if device_id and row.get("deviceId") != device_id:
                    continue
                rows.append(
                    {
                        "serverTimestamp": row.get("serverTimestamp"),
                        "deviceId": row.get("deviceId"),
                        "deviceTimestamp": row.get("deviceTimestamp"),
                        "deviceUptimeMs": _parse_int(row.get("deviceUptimeMs")),
                        "distance": _parse_float(row.get("distance")),
                        "motion": _parse_bool(row.get("motion")),
                        "servoPosition": _parse_int(row.get("servoPosition")),
                        "targetPosition": _parse_int(row.get("targetPosition")),
                        "shouldActivateServo": _parse_bool(
                            row.get("shouldActivateServo")
                        ),
                        "isFull": _parse_bool(row.get("isFull")),
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


@app.route("/api/sensor-data", methods=["POST"])
def receive_sensor_data():
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    device_id = data.get("deviceId", "unknown")
    now = datetime.now().isoformat()

    data["serverTimestamp"] = now
    dist_val = _parse_float(data.get("distance"))
    thr = _parse_float(settings.get("thresholdCm"), 5) or 5
    is_full = 1 if (dist_val is not None and dist_val <= thr) else 0
    data["isFull"] = is_full
    device_data[device_id] = data

    point = {
        "serverTimestamp": now,
        "deviceTimestamp": data.get("deviceTimestamp"),
        "deviceUptimeMs": data.get("deviceUptimeMs"),
        "distance": data.get("distance"),
        "servoPosition": data.get("servoPosition"),
        "targetPosition": data.get("targetPosition"),
        "motion": 1 if data.get("motion") else 0,
        "isFull": is_full,
    }
    device_history[device_id].append(point)

    try:
        alert_check(device_id, data.get("distance"))
    except Exception:
        pass

    try:
        append_csv(data)
    except Exception:
        pass

    return jsonify({"status": "ok", "deviceId": device_id, "serverTimestamp": now})


@app.route("/api/command", methods=["POST", "GET"])
def command_endpoint():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        device_id = data.get("deviceId")
        if not device_id:
            return jsonify({"error": "deviceId required"}), 400

        action = (data.get("action") or "").strip() or "setAngle"
        target = data.get("targetPosition")

        if action in ("open", "activate") and target is None:
            target = 90
            action = "setAngle"
        elif action in ("close", "deactivate") and target is None:
            target = 0
            action = "setAngle"

        if action == "auto":
            payload = {"action": "auto"}
        else:
            try:
                target = int(target)  # type: ignore
            except (TypeError, ValueError):
                return jsonify(
                    {"error": "targetPosition required/int for setAngle"}
                ), 400
            target = max(0, min(180, target))
            payload = {"action": "setAngle", "targetPosition": target}

        device_command_seq[device_id] += 1
        cmd_id = device_command_seq[device_id]
        command = {
            "deviceId": device_id,
            "commandId": cmd_id,
            **payload,
            "serverTimestamp": datetime.now().isoformat(),
        }
        device_commands[device_id] = command
        return jsonify({"status": "ok", **command})

    device_id = request.args.get("deviceId")
    if not device_id:
        if not device_data:
            return jsonify({})
        device_id = next(iter(device_data.keys()))
    try:
        last_id = int(request.args.get("lastId", 0))
    except ValueError:
        last_id = 0
    cmd = device_commands.get(device_id)
    if not cmd:
        return jsonify({})
    if cmd.get("commandId", 0) <= last_id:
        return jsonify({})
    return jsonify(cmd)


@app.route("/api/history")
def get_history():
    device_id = request.args.get("deviceId")
    limit = int(request.args.get("limit", 100))

    if not device_id:
        if device_data:
            device_id = next(iter(device_data.keys()))
        else:
            csv_device = get_last_device_from_csv()
            if not csv_device:
                return jsonify({})
            device_id = csv_device

    series = read_history_from_csv(device_id, limit)
    if not series:
        mem_series = list(device_history.get(device_id, []))
        if limit < len(mem_series):
            mem_series = mem_series[-limit:]
        if not mem_series:
            return jsonify({})
        series = [
            {
                "serverTimestamp": p.get("serverTimestamp"),
                "distance": p.get("distance"),
                "servoPosition": p.get("servoPosition"),
                "motion": p.get("motion"),
            }
            for p in mem_series
        ]

    return jsonify(
        {
            "deviceId": device_id,
            "timestamps": [p.get("serverTimestamp") for p in series],
            "distance": [p.get("distance") for p in series],
            "servo": [p.get("servoPosition") for p in series],
            "motion": [p.get("motion") for p in series],
        }
    )


@app.route("/api/history-page")
def get_history_page():
    device_id = request.args.get("deviceId")
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    try:
        page_size = int(request.args.get("pageSize", 25))
    except ValueError:
        page_size = 25

    if not device_id:
        if device_data:
            device_id = next(iter(device_data.keys()))
        else:
            device_id = get_last_device_from_csv()

    rows, total = read_history_page_from_csv(device_id, page, page_size)

    if not rows and device_id in device_history:
        mem_rows = list(device_history.get(device_id, []))
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
            }
            for p in sliced
        ]

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
def list_devices():
    ids = list(device_data.keys())
    if not ids:
        ids = list_devices_from_csv(max_devices=None)
    return jsonify({"deviceIds": ids})


@app.route("/api/settings", methods=["GET", "POST"])
def settings_endpoint():
    if request.method == "GET":
        return jsonify({"thresholdCm": settings.get("thresholdCm", 5)})
    data = request.get_json(silent=True) or {}
    t = _parse_float(data.get("thresholdCm"))
    if t is None:
        return jsonify({"error": "thresholdCm required (number)"}), 400
    t = max(0.0, float(t))
    settings["thresholdCm"] = t
    save_settings(settings)
    return jsonify({"status": "ok", "thresholdCm": t})


@app.route("/api/device-data")
def get_device_data():
    return jsonify(device_data)


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/history")
def history_view():
    """Render the history table view."""
    return render_template("history.html")


@app.route("/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "devices_connected": len(device_data),
        }
    )


def main():
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
