# ESP32 IoT Trash Bin Server

Flask-based server for the ESP32 smart trash bin system. Receives sensor data from IoT devices, stores history, evaluates alerts, and provides a web dashboard for monitoring and control.

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) package manager (recommended) or pip

## Quick Start

### 1. Clone and Navigate

```bash
cd trash/server
```

### 2. Install Dependencies

**Using uv (recommended):**
```bash
uv sync
```

**Using pip:**
```bash
pip install -e .
```

### 3. Configure Environment (Optional)

Create a `.env` file in the `server` directory for Discord notifications:

```env
DISCORD_WEBHOOK=https://discord.com/api/webhooks/your-webhook-url
```

> **Note:** Discord notifications are optional. The server will run without this configuration.

### 4. Run the Server

**Using uv:**
```bash
uv run python -m src.main
```

**Using pip:**
```bash
python -m src.main
```

**Or run directly:**
```bash
python src/main.py
```

The server will start on `http://0.0.0.0:5000`

## Accessing the Dashboard

1. Open your browser and navigate to: `http://localhost:5000`
2. Login with credentials:
   - **Username:** `trash`
   - **Password:** `trash_123`

## API Endpoints

### Device Endpoints (for ESP32)
- `POST /api/sensor-data` - Receive sensor telemetry (requires Bearer token)
- `GET /api/command` - Poll for pending commands

### Dashboard Endpoints
- `GET /` - Main dashboard (requires auth)
- `GET /history` - Historical data view (requires auth)
- `GET /api/devices` - List all devices
- `GET /api/device-data` - Current device data
- `GET /api/history` - Time-series data for charts
- `GET /api/history-page` - Paginated history
- `GET/POST /api/settings` - Threshold and alert configuration
- `POST /api/command` - Send commands to devices (requires auth)

### Health Check
- `GET /health` - Server health status

## Configuration

### Server Settings

Settings are stored in `src/data/settings.json` and can be modified via the web dashboard:

- **thresholdCm** - Distance threshold for "full" status (default: 5 cm)
- **emptyThresholdCm** - Distance threshold for "empty" status (default: 15 cm)  
- **alertSustainSec** - Seconds a condition must persist before alerting (default: 3 sec)

### Data Storage

- **CSV History:** `src/data/sensor_data.csv` - Persistent sensor data log
- **Settings:** `src/data/settings.json` - Server configuration
- **In-Memory:** Last 500 readings per device (configurable via `MAX_HISTORY_IN_MEMORY`)

## ESP32 Configuration

Configure your ESP32 device to point to this server:

1. Connect to the ESP32 Access Point (SSID: `trash`)
2. Navigate to `http://192.168.4.1`
3. Enter your server URL: `http://<your-server-ip>:5000`
4. Set Device ID (e.g., `esp32_trash`)

## Authentication

- **Web Dashboard:** HTTP Basic Auth (`trash` / `trash_123`)
- **Device API:** Bearer token (`trash_123`)

> ⚠️ **Security Warning:** Change default credentials in production!

## Discord Notifications

To enable Discord alerts:

1. Create a Discord webhook in your server settings
2. Add the webhook URL to `.env`:
   ```env
   DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
   ```
3. Restart the server

## Development

### Project Structure

```
server/
├── src/
│   ├── main.py          # Main Flask application
│   ├── storage.py       # CSV storage utilities
│   ├── utils.py         # Helper functions
│   ├── data/            # Data storage directory
│   ├── static/          # Static assets
│   └── templates/       # HTML templates
├── pyproject.toml       # Project dependencies
└── README.md           # This file
```

### Running in Debug Mode

Edit `src/main.py` and change:
```python
app.run(host="0.0.0.0", port=5000, debug=True)
```