# ESP32 IoT Smart Trash Bin

IoT solution for smart waste management using ESP32, ultrasonic sensors, and a cloud server. The system automatically opens the trash bin lid when motion is detected, monitors fill levels, and sends alerts when the bin needs to be emptied.

## Features

- **Automatic Lid Control** - Servo opens lid when motion detected
- **Fill Level Monitoring** - Ultrasonic sensor measures trash level
- **Motion Detection** - PIR sensor for hands-free operation
- **Cloud Connectivity** - Syncs data to Flask server
- **Web Dashboard** - Monitor and control from browser
- **Smart Alerts** - Discord notifications when bin is full

## Project Structure

```
trash/
├── trash.ino           # ESP32 firmware (Arduino sketch)
├── server/             # Flask server application
│   └── README.md       # Server setup guide
└── README.md           # This file
```

## Hardware Requirements

- ESP32 Development Board
- HC-SR04 Ultrasonic Sensor
- PIR Motion Sensor
- SG90 Servo Motor
- Power Supply (5V)

### Pin Connections

| Component | ESP32 Pin |
|-----------|-----------|
| Ultrasonic Trigger | GPIO 5 |
| Ultrasonic Echo | GPIO 18 |
| PIR Motion | GPIO 21 |
| Servo Control | GPIO 13 |

## 🚀 Quick Start

### 1. Set Up the Server

See [server/README.md](server/README.md) for detailed server installation and configuration.

**Quick version:**
```bash
cd server
uv sync
uv run python -m src.main
```

Server runs on `http://0.0.0.0:5000`

### 2. Upload ESP32 Firmware

1. **Install Arduino IDE** and required libraries:
   - ESP32 Board Support
   - ArduinoJson
   - ESP32Servo
   - HTTPClient
   - Preferences
   - WebServer

2. **Open and Upload:**
   - Open `trash.ino` in Arduino IDE
   - Select your ESP32 board
   - Upload the sketch

3. **Configure Device:**
   - Connect to WiFi network: `trash` (password: `trash_123`)
   - Navigate to `http://192.168.4.1`
   - Enter your WiFi credentials and server URL
   - Click "Save & Reboot"

## Usage

### Access the Dashboard

1. Open `http://<server-ip>:5000` in your browser
2. Login with:
   - Username: `trash`
   - Password: `trash_123`

### Operating Modes

- **Auto Mode** (default): Lid opens on motion, closes when clear
- **Manual Mode**: Control servo angle from dashboard

### Alert System

The system sends alerts when:
- 🔴 **Full** - Distance ≤ threshold (default: 5cm)
- 🟡 **Partial** - Bin is 3/4 full
- 🟢 **Empty** - Distance ≥ empty threshold (default: 15cm)

## How It Works

1. **Motion Detection** → PIR sensor detects presence
2. **Lid Control** → Servo opens lid automatically (or via command)
3. **Fill Monitoring** → Ultrasonic sensor measures distance to trash
4. **Data Sync** → ESP32 sends telemetry to server every second
5. **Alert Evaluation** → Server checks thresholds and sends notifications
6. **Remote Control** → Dashboard sends commands back to device

## Security

⚠️ **Default credentials are for development only!**

For production:
- Change dashboard credentials in `server/src/main.py`
- Change AP password in `trash.ino`
- Update Bearer token to match

## 📚 Documentation

- **[Server Setup & API Reference](server/README.md)** - Detailed server documentation
- **Device Code** - See comments in `trash.ino` for firmware details
