from fastapi import FastAPI, Request, Body, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import uvicorn
from datetime import datetime
import os
from typing import Optional
from pydantic import BaseModel, ConfigDict
from .services import (
    settings,
    save_settings,
    device_data,
    device_commands,
    device_command_seq,
    device_history,
    append_csv,
    alert_check,
    get_last_device_from_csv,
    read_history_from_csv,
    read_history_page_from_csv,
    list_devices_from_csv,
    clamp_angle,
    compute_is_full,
    DeviceRow,
    HistoryPoint,
)

app = FastAPI()

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SensorDataIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    deviceId: Optional[str] = None
    deviceTimestamp: Optional[str] = None
    deviceUptimeMs: Optional[int] = None
    distance: Optional[float] = None
    motion: Optional[bool] = None
    servoPosition: Optional[int] = None
    targetPosition: Optional[int] = None
    shouldActivateServo: Optional[bool] = None


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deviceId: Optional[str] = None
    action: Optional[str] = None
    targetPosition: Optional[int] = None


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thresholdCm: Optional[float] = None


@app.post("/api/sensor-data")
def receive_sensor_data(payload: SensorDataIn | None = Body(None)):
    if not payload:
        return JSONResponse({"error": "No data provided"}, status_code=400)

    device_id = payload.deviceId or "unknown"
    now = datetime.now().isoformat()

    is_full = compute_is_full(payload.distance)

    row: DeviceRow = {
        "serverTimestamp": now,
        "deviceId": device_id,
        "deviceTimestamp": payload.deviceTimestamp,
        "deviceUptimeMs": payload.deviceUptimeMs,
        "distance": payload.distance,
        "motion": 1 if payload.motion else 0,
        "servoPosition": payload.servoPosition,
        "targetPosition": payload.targetPosition,
        "shouldActivateServo": 1 if (payload.shouldActivateServo is True) else 0,
        "isFull": is_full,
    }
    device_data[device_id] = row

    point: HistoryPoint = {
        "serverTimestamp": now,
        "deviceTimestamp": payload.deviceTimestamp,
        "deviceUptimeMs": payload.deviceUptimeMs,
        "distance": payload.distance,
        "servoPosition": payload.servoPosition,
        "targetPosition": payload.targetPosition,
        "motion": 1 if payload.motion else 0,
        "isFull": is_full,
    }
    device_history[device_id].append(point)

    try:
        alert_check(device_id, payload.distance)
    except Exception:
        pass

    try:
        append_csv(row)
    except Exception:
        pass

    return {"status": "ok", "deviceId": device_id, "serverTimestamp": now}


@app.post("/api/command")
def post_command(cmd: CommandIn | None = Body(None)):
    data = cmd.dict() if cmd else {}
    device_id = data.get("deviceId")
    if not device_id:
        return JSONResponse({"error": "deviceId required"}, status_code=400)

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
            target = clamp_angle(int(target))  # type: ignore
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "targetPosition required/int for setAngle"},
                status_code=400,
            )
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
    return {"status": "ok", **command}


@app.get("/api/command")
def get_command(deviceId: str | None = Query(None), lastId: int = Query(0)):
    device_id = deviceId
    if not device_id:
        if not device_data:
            return {}
        device_id = next(iter(device_data.keys()))
    last_id = lastId or 0
    cmd = device_commands.get(device_id)
    if not cmd:
        return {}
    if cmd.get("commandId", 0) <= last_id:
        return {}
    return cmd


@app.get("/api/history")
def get_history(deviceId: str | None = Query(None), limit: int = Query(100)):
    device_id = deviceId

    if not device_id:
        if device_data:
            device_id = next(iter(device_data.keys()))
        else:
            csv_device = get_last_device_from_csv()
            if not csv_device:
                return {}
            device_id = csv_device

    series = read_history_from_csv(device_id, limit)
    if not series:
        mem_series = list(device_history.get(device_id, []))
        if limit < len(mem_series):
            mem_series = mem_series[-limit:]
        if not mem_series:
            return {}
        series = [
            {
                "serverTimestamp": p.get("serverTimestamp"),
                "distance": p.get("distance"),
                "servoPosition": p.get("servoPosition"),
                "motion": p.get("motion"),
            }
            for p in mem_series
        ]

    return {
        "deviceId": device_id,
        "timestamps": [p.get("serverTimestamp") for p in series],
        "distance": [p.get("distance") for p in series],
        "servo": [p.get("servoPosition") for p in series],
        "motion": [p.get("motion") for p in series],
    }


@app.get("/api/history-page")
def get_history_page(
    deviceId: str | None = Query(None),
    page: int = Query(1),
    pageSize: int = Query(25),
):
    device_id = deviceId
    page_size = pageSize

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
    return {
        "deviceId": device_id,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": total_pages,
        "rows": rows,
    }


@app.get("/api/devices")
def list_devices():
    ids = list(device_data.keys())
    if not ids:
        ids = list_devices_from_csv(max_devices=None)
    return {"deviceIds": ids}


@app.get("/api/settings")
def get_settings():
    return {"thresholdCm": settings.get("thresholdCm", 5)}


@app.post("/api/settings")
def post_settings(payload: SettingsIn | None = Body(None)):
    data = payload.dict() if payload else {}
    t_val = data.get("thresholdCm")
    t = float(t_val) if t_val is not None else None
    if t is None:
        return JSONResponse({"error": "thresholdCm required (number)"}, status_code=400)
    t = max(0.0, float(t))
    settings["thresholdCm"] = t
    save_settings(settings)
    return {"status": "ok", "thresholdCm": t}


@app.get("/api/device-data")
def get_device_data():
    return device_data


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/history")
def history_view(request: Request):
    return templates.TemplateResponse("history.html", {"request": request})


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "devices_connected": len(device_data),
    }


def main():
    uvicorn.run(app, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
