from __future__ import annotations

from typing import cast

from flask import Response, jsonify, request


JSONLike = str | int | float | bool | None | list["JSONLike"] | dict[str, "JSONLike"]


DEFAULT_THRESHOLD_CM: float = 5.0
DEFAULT_ALERT_SUSTAIN_SEC: float = 3.0


def pbool(val: object) -> int:
    if isinstance(val, bool):
        return 1 if val else 0
    if val is None:
        return 0
    s = str(val).strip().lower()
    return 1 if s in ("1", "true", "yes", "y", "on") else 0


def pint(val: object, default: int | None = None) -> int | None:
    if isinstance(val, (int, bool)):
        return int(val)
    try:
        return int(val) if isinstance(val, (float, str)) else default
    except (TypeError, ValueError):
        return default


def pfloat(val: object, default: float | None = None) -> float | None:
    if isinstance(val, (int, bool, float)):
        return float(val)
    try:
        return float(val) if isinstance(val, str) else default
    except (TypeError, ValueError):
        return default


def json_in() -> dict[str, JSONLike]:
    return cast(dict[str, JSONLike], request.get_json(silent=True) or {})


def err(message: str, status: int = 400) -> tuple[Response, int]:
    return jsonify({"error": message}), status


def clamp_deg(angle: int) -> int:
    return max(0, min(180, int(angle)))


def arg_int(name: str, default: int = 0) -> int:
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def arg_str(name: str, default: str | None = None) -> str | None:
    val = request.args.get(name)
    if val is None:
        return default
    s = val.strip()
    return s if s else default
