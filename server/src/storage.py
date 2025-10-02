from __future__ import annotations

import csv
import os
from collections import deque
from collections.abc import Mapping
from typing import cast

from .utils import JSONLike


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CSV_PATH = os.path.join(DATA_DIR, "sensor_data.csv")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")


CSVValue = str | int | float | bool | None

CSV_FIELDS: list[str] = [
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
    "fillStatus",
]


def csv_val(v: JSONLike) -> CSVValue:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def csv_append(row: Mapping[str, JSONLike]) -> None:
    exists = os.path.exists(CSV_PATH)

    with open(CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)

        if not exists:
            writer.writeheader()

        safe: dict[str, CSVValue] = {k: csv_val(row.get(k, "")) for k in CSV_FIELDS}
        writer.writerow(safe)


def csv_last_device() -> str | None:
    if not os.path.exists(CSV_PATH):
        return None
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            last_row: deque[dict[str, str]] = deque(reader, maxlen=1)
            if not last_row:
                return None
            did = last_row[0].get("deviceId")
            return did if did else None
    except Exception:
        return None


def csv_rows():
    if not os.path.exists(CSV_PATH):
        return
    try:
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield cast(dict[str, str], row)
    except Exception:
        return
