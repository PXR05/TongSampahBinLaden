# MQTT Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TongSampahBinLaden System                        │
│                         MQTT Architecture                           │
└─────────────────────────────────────────────────────────────────────┘

                    test.mosquitto.org:1883
                    ┌─────────────────┐
                    │   MQTT Broker   │
                    │   (Public)      │
                    └────────┬────────┘
                             │
          ┌──────────────────┴──────────────────┐
          │                                     │
          ▼                                     ▼

  ┌──────────────┐                     ┌──────────────┐
  │   ESP32      │                     │   Flask      │
  │   Device     │                     │   Server     │
  │              │                     │              │
  │ • Ultrasonic │                     │ • Receives   │
  │ • PIR Sensor │                     │   sensor     │
  │ • Servo      │                     │   data       │
  │              │                     │ • Processes  │
  └──────┬───────┘                     │   alerts     │
         │                             │ • Stores CSV │
         │                             │ • ML Models  │
         │                             └──────┬───────┘
         │                                    │
         │  PUBLISHES                         │  HTTP API
         │  tongsampahbinladen/               │
         │  sensor/{deviceId}                 │
         │                                    ▼
         │  ┌────────────────────┐    ┌──────────────┐
         │  │  JSON Payload:     │    │   Web        │
         │  │  {                 │    │   Dashboard  │
         │  │   "distance": 8.5, │    │              │
         │  │   "servo": 45,     │    │ • Chart.js   │
         │  │   "motion": true   │    │ • Live Data  │
         │  │  }                 │    │ • Controls   │
         │  └────────────────────┘    └──────┬───────┘
         │                                    │
         │                                    │  User Sends
         │                                    │  Command
         │  SUBSCRIBES                        │
         │  tongsampahbinladen/               ▼
         │  command/{deviceId}         ┌─────────────┐
         │                             │  POST       │
         │  ┌────────────────────┐     │  /api/      │
         └──│  JSON Payload:     │◄────│  command    │
            │  {                 │     └─────────────┘
            │   "action":        │
            │     "setAngle",    │     Server publishes
            │   "targetPosition":│     command to MQTT
            │     90             │
            │  }                 │
            └────────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                          Message Flow                               │
└─────────────────────────────────────────────────────────────────────┘

  SENSOR DATA FLOW:
  ─────────────────

  ESP32 → MQTT Broker → Server → CSV/Memory → Dashboard
    │                       │
    │                       └──→ Alert System
    │                             (Discord, Device Notifications)
    │
    └──→ Every 1-2 seconds


  COMMAND FLOW:
  ─────────────

  Dashboard → HTTP POST → Server → MQTT Broker → ESP32
                            │
                            └──→ Instant delivery
                                 (no polling needed)


┌─────────────────────────────────────────────────────────────────────┐
│                        Topic Structure                              │
└─────────────────────────────────────────────────────────────────────┘

  tongsampahbinladen/
  │
  ├── sensor/
  │   ├── device_001      ← Device 1 publishes here
  │   ├── device_002      ← Device 2 publishes here
  │   └── device_xxx      ← Device N publishes here
  │
  └── command/
      ├── device_001      ← Server publishes commands for Device 1
      ├── device_002      ← Server publishes commands for Device 2
      └── device_xxx      ← Server publishes commands for Device N


┌─────────────────────────────────────────────────────────────────────┐
│                      Data Processing Pipeline                       │
└─────────────────────────────────────────────────────────────────────┘

  Raw Sensor Data
       ↓
  ┌──────────────────┐
  │ MQTT Handler     │  ← handle_mqtt_sensor_data()
  └────────┬─────────┘
           ↓
  ┌──────────────────┐
  │ Augment Data     │  ← Add serverTimestamp, compute fillStatus
  └────────┬─────────┘
           ↓
  ┌──────────────────┐
  │ ML Models        │  ← Fuzzy logic, Regression, Time prediction
  └────────┬─────────┘
           ↓
  ┌──────────────────┐
  │ Alert Evaluation │  ← Check thresholds, send notifications
  └────────┬─────────┘
           ↓
  ┌──────────────────┐
  │ Storage          │  ← CSV file + In-memory circular buffer
  └────────┬─────────┘
           ↓
  ┌──────────────────┐
  │ Dashboard API    │  ← Available via HTTP endpoints
  └──────────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                    Backward Compatibility                           │
└─────────────────────────────────────────────────────────────────────┘

  Old HTTP Polling Method:
  ────────────────────────

  Device ──(POST /api/sensor-data)──→ Server
     ↑                                    │
     │                                    │
     └──(GET /api/command?lastId=X)──────┘

     ⚠ Still works but deprecated
     ⚠ Requires constant polling (inefficient)


  New MQTT Method:
  ────────────────

  Device ──(Publish to sensor topic)──→ MQTT Broker ──→ Server
     ↑                                                      │
     │                                                      │
     └────(Subscribe to command topic)─── MQTT Broker ◄────┘

     ✓ Real-time (< 100ms latency)
     ✓ Lower bandwidth
     ✓ Event-driven (no polling)


┌─────────────────────────────────────────────────────────────────────┐
│                      Alert State Machine                            │
└─────────────────────────────────────────────────────────────────────┘

           distance > 15cm
          ┌─────────────┐
          │    EMPTY    │
          │   (Green)   │
          └──────┬──────┘
                 │
                 │ 5cm < distance < 15cm
                 ↓
          ┌─────────────┐
          │   PARTIAL   │──────→ notifyPartial
          │  (Yellow)   │        (3/4 full)
          └──────┬──────┘
                 │
                 │ distance <= 5cm
                 ↓
          ┌─────────────┐
          │    FULL     │──────→ notifyFull
          │    (Red)    │        + Discord Alert
          └─────────────┘


┌─────────────────────────────────────────────────────────────────────┐
│                         Key Features                                │
└─────────────────────────────────────────────────────────────────────┘

  ✓ Real-time bi-directional communication
  ✓ Multiple device support (unique device IDs)
  ✓ Backward compatible with HTTP endpoints
  ✓ Automatic alert system with sustain period
  ✓ Machine learning predictions (fuzzy, regression, time-to-full)
  ✓ Persistent storage (CSV + in-memory)
  ✓ Web dashboard with live updates
  ✓ Discord notifications
  ✓ Auto-mode with motion detection
  ✓ QoS 1 for reliable message delivery
```
