from __future__ import annotations

import os
import csv
import json
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple, Literal, TypedDict, NotRequired
import requests


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

HISTORY_LIMIT = 500


class Settings(TypedDict):
    thresholdCm: float


class HistoryPoint(TypedDict, total=False):
    serverTimestamp: str
    deviceTimestamp: Optional[str]
    deviceUptimeMs: Optional[int]
    distance: Optional[float]
    motion: int
    servoPosition: Optional[int]
    targetPosition: Optional[int]
    shouldActivateServo: Optional[int]
    isFull: int


class DeviceRow(TypedDict, total=False):
    serverTimestamp: str
    deviceId: str
    deviceTimestamp: Optional[str]
    deviceUptimeMs: Optional[int]
    distance: Optional[float]
    motion: int
    servoPosition: Optional[int]
    targetPosition: Optional[int]
    shouldActivateServo: Optional[int]
    isFull: int


class CommandDict(TypedDict, total=False):
    deviceId: str
    commandId: int
    action: Literal["auto", "setAngle"]
    serverTimestamp: str
    targetPosition: NotRequired[int]


# Global state (typed)
device_data: Dict[str, DeviceRow] = {}
device_commands: Dict[str, CommandDict] = {}
device_command_seq: defaultdict[str, int] = defaultdict(int)
device_history: defaultdict[str, Deque[HistoryPoint]] = defaultdict(
    lambda: deque(maxlen=HISTORY_LIMIT)
)
alert_below: defaultdict[str, bool] = defaultdict(bool)


# Parsers
def _parse_bool(val: object) -> int:
    if isinstance(val, bool):
        return 1 if val else 0
    if val is None:
        return 0
    s = str(val).strip().lower()
    return 1 if s in ("1", "true", "yes", "y", "on") else 0


def _parse_int(val: object, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _parse_float(val: object, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# Settings
def load_settings() -> Settings:
    default: Settings = {"thresholdCm": 5.0}
    if not os.path.exists(SETTINGS_PATH):
        return default
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            t = _parse_float(data.get("thresholdCm"), 5.0) or 5.0
            return {"thresholdCm": max(0.0, float(t))}
    except Exception:
        return default


def save_settings(s: Settings) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f)
    except Exception:
        pass


settings: Settings = load_settings()


def get_threshold() -> float:
    return _parse_float(settings.get("thresholdCm"), 5.0) or 5.0


def compute_is_full(distance: object) -> int:
    d = _parse_float(distance)
    if d is None:
        return 0
    return 1 if d <= get_threshold() else 0


def clamp_angle(n: int) -> int:
    return max(0, min(180, int(n)))


def resolve_device_id(preferred: Optional[str]) -> Optional[str]:
    if preferred:
        return preferred
    if device_data:
        return next(iter(device_data.keys()))
    return get_last_device_from_csv()


# CSV / History
def append_csv(row: DeviceRow) -> None:
    exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        safe: Dict[str, object] = {k: row.get(k, "") for k in CSV_FIELDS}
        writer.writerow(safe)


def get_last_device_from_csv() -> Optional[str]:
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


def read_history_from_csv(
    device_id: Optional[str], limit: int = 100
) -> List[HistoryPoint]:
    if not os.path.exists(CSV_PATH):
        return []
    buf: Deque[HistoryPoint] = deque(maxlen=limit)
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if device_id and row.get("deviceId") != device_id:
                    continue
                buf.append(
                    {
                        "serverTimestamp": row.get("serverTimestamp", ""),
                        "distance": _parse_float(row.get("distance")),
                        "servoPosition": _parse_int(row.get("servoPosition")),
                        "motion": _parse_bool(row.get("motion")),
                    }
                )
    except Exception:
        return []
    return list(buf)


def list_devices_from_csv(max_devices: Optional[int] = None) -> List[str]:
    if not os.path.exists(CSV_PATH):
        return []
    seen: List[str] = []
    seen_set: set[str] = set()
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
    device_id: Optional[str], page: int = 1, page_size: int = 25
) -> Tuple[List[DeviceRow], int]:
    if not os.path.exists(CSV_PATH):
        return [], 0
    rows: List[DeviceRow] = []
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if device_id and row.get("deviceId") != device_id:
                    continue
                rows.append(
                    {
                        "serverTimestamp": row.get("serverTimestamp", ""),
                        "deviceId": row.get("deviceId", ""),
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

    rows.reverse()  # newest-first
    total = len(rows)
    if page_size <= 0:
        page_size = 25
    if page <= 0:
        page = 1
    start = (page - 1) * page_size
    end = start + page_size
    return rows[start:end], total


# Alerts / notifications
def send_discord_message(content: str) -> None:
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


def alert_check(device_id: str, distance: object) -> None:
    d = _parse_float(distance)
    if d is None:
        return
    threshold = get_threshold()
    is_below = d <= threshold
    was_below = alert_below[device_id]
    if is_below and not was_below:
        msg = (
            f"Alert: Device {device_id} distance {d:.2f} cm is at/below threshold "
            f"{threshold:.2f} cm."
        )
        send_discord_message(msg)
    alert_below[device_id] = is_below
