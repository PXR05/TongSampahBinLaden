# ESP32 MQTT Configuration Guide

## Overview

The ESP32 trash bin controller now supports configurable MQTT broker settings through the web interface, with built-in authentication.

## Changes Made

### 1. Configurable MQTT Broker

- **Old:** Hardcoded to `test.mosquitto.org`
- **New:** Configurable via web interface (stored in `cfg_mqttURL`)

### 2. MQTT Authentication

- **Credentials:** Uses the same username/password as the AP configuration
  - Username: `trash`
  - Password: `trash_123`
- **Purpose:** Secures MQTT connection (useful for private brokers)

### 3. Configuration Storage

- MQTT broker URL is saved to ESP32 flash memory (Preferences)
- Persists across reboots
- Default value: `test.mosquitto.org`

## Web Configuration Interface

### Access the Configuration Page

1. Connect to the ESP32's WiFi Access Point:
   - **SSID:** `trash`
   - **Password:** `trash_123`
2. Open browser and go to: `http://192.168.4.1`
3. Login with:
   - **Username:** `trash`
   - **Password:** `trash_123`

### Configuration Fields

| Field           | Description                        | Example                                 |
| --------------- | ---------------------------------- | --------------------------------------- |
| WiFi SSID       | Your home/office WiFi network name | `MyHomeWiFi`                            |
| WiFi Password   | WiFi network password              | `MySecurePassword`                      |
| MQTT Broker URL | MQTT broker hostname or IP         | `test.mosquitto.org` or `192.168.1.100` |
| Device ID       | Unique identifier for this device  | `esp32_trash_kitchen`                   |

### MQTT Broker Examples

#### Public Brokers (No Auth)

- `test.mosquitto.org` - Mosquitto test server
- `broker.hivemq.com` - HiveMQ public broker
- `mqtt.eclipseprojects.io` - Eclipse IoT

⚠️ **Note:** These public brokers don't require authentication, but the ESP32 will still send credentials. This is harmless but unnecessary.

#### Private Brokers (With Auth)

If you're running your own MQTT broker (Mosquitto, HiveMQ, etc.) with authentication enabled:

**Mosquitto Setup:**

```bash
# Create password file
sudo mosquitto_passwd -c /etc/mosquitto/passwd trash

# Enter password: trash_123

# Edit /etc/mosquitto/mosquitto.conf
allow_anonymous false
password_file /etc/mosquitto/passwd

# Restart Mosquitto
sudo systemctl restart mosquitto
```

Then configure the ESP32 to use your broker's IP or hostname.

## MQTT Topics

The device uses these topics (independent of broker):

| Topic                   | Direction       | Purpose                               |
| ----------------------- | --------------- | ------------------------------------- |
| `esp32_trash/telemetry` | Device → Broker | Sensor data (distance, motion, servo) |
| `esp32_trash/command`   | Broker → Device | Commands (setAngle, auto, notify)     |

## Testing the Configuration

### Step 1: Configure via Web Interface

1. Set your MQTT broker URL
2. Click "Save & Reboot"
3. ESP32 will restart with new settings

### Step 2: Monitor Serial Output

```
WiFi connected!
IP address: 192.168.1.123
MQTT Client configured for broker: test.mosquitto.org
Attempting MQTT connection to test.mosquitto.org...connected
Subscribed to topic: esp32_trash/command
Telemetry published successfully
```

### Step 3: Test with MQTT Client

```bash
# Subscribe to telemetry (see sensor data)
mosquitto_sub -h YOUR_BROKER -t "esp32_trash/telemetry" -u trash -P trash_123 -v

# Send a command (test control)
mosquitto_pub -h YOUR_BROKER -t "esp32_trash/command" -u trash -P trash_123 \
  -m '{"action":"setAngle","targetPosition":90}'
```

## Troubleshooting

### "MQTT connection failed, rc=-2"

- **Cause:** Cannot resolve hostname or connect to broker
- **Fix:**
  - Check MQTT broker URL spelling
  - Verify broker is reachable: `ping YOUR_BROKER`
  - Ensure port 1883 is not blocked by firewall

### "MQTT connection failed, rc=5"

- **Cause:** Authentication failed
- **Fix:**
  - If using public broker, it may not support authentication
  - If using private broker, verify credentials in broker config
  - Default credentials are `trash`/`trash_123`

### "MQTT connection failed, rc=4"

- **Cause:** Invalid client ID or connection refused
- **Fix:** Ensure Device ID is unique across all your devices

### No data appearing

- **Fix:**
  - Check Serial Monitor for connection status
  - Verify broker is running: `mosquitto -v` (if local)
  - Test with mosquitto_sub to confirm topics are correct

## Integration with Server

### Option 1: Use Same Broker as Server

Configure both ESP32 and server to use the same MQTT broker:

**ESP32:** Set broker to your server's IP or public broker
**Server (`main.py`):** Update `MQTT_BROKER` constant

### Option 2: Bridge Brokers

Use MQTT broker bridging to connect local and cloud brokers.

### Option 3: Use Public Broker

Both ESP32 and server connect to `test.mosquitto.org` (easiest for testing).

## Security Considerations

### For Testing

- Public brokers are fine for development
- Data is visible to anyone who knows your topics

### For Production

1. **Use Private Broker** with authentication
2. **Enable TLS/SSL** for encrypted communication
3. **Use Strong Passwords** (not `trash_123`)
4. **Use Certificates** instead of passwords (more secure)
5. **Implement Topic ACLs** to restrict access

## Advanced Configuration

### Change MQTT Port

Edit `trash.ino`:

```cpp
const int mqtt_port = 1883; // Change to your port (e.g., 8883 for SSL)
```

### Change Authentication

Edit `trash.ino`:

```cpp
const char *apSsid = "trash";      // MQTT username
const char *apPassword = "trash_123"; // MQTT password
```

⚠️ **Note:** Changing these affects both AP and MQTT authentication.

### Use Different Credentials for MQTT

If you need separate MQTT credentials, add new config variables:

```cpp
String cfg_mqttUser = "mqtt_user";
String cfg_mqttPass = "mqtt_pass";
```

Then update `reconnectMQTT()`:

```cpp
mqttClient.connect(cfg_deviceID.c_str(), cfg_mqttUser.c_str(), cfg_mqttPass.c_str())
```

## Connection Flow Diagram

```
ESP32 Bootup
    ↓
Load Config (WiFi, MQTT Broker, Device ID)
    ↓
Connect to WiFi
    ↓
Configure MQTT Client (Broker URL from config)
    ↓
Attempt MQTT Connection (with trash/trash_123 auth)
    ↓
Subscribe to Command Topic
    ↓
Main Loop:
  - Publish telemetry every 1s
  - Listen for commands
  - Reconnect if disconnected (every 5s)
```

## Default Configuration

| Setting       | Default Value        | Stored In        |
| ------------- | -------------------- | ---------------- |
| WiFi SSID     | `ssid`               | Flash memory     |
| WiFi Password | `password`           | Flash memory     |
| MQTT Broker   | `test.mosquitto.org` | Flash memory     |
| Device ID     | `esp32_trash`        | Flash memory     |
| MQTT Username | `trash`              | Code (hardcoded) |
| MQTT Password | `trash_123`          | Code (hardcoded) |
| MQTT Port     | `1883`               | Code (hardcoded) |

## Summary of Changes

✅ **Removed:** Hardcoded MQTT broker URL  
✅ **Added:** Configurable MQTT broker via web interface  
✅ **Added:** MQTT authentication using AP credentials  
✅ **Updated:** Web UI to show MQTT broker field  
✅ **Updated:** Configuration storage to save MQTT URL  
✅ **Updated:** Serial output to show configured broker

---

**Quick Start:**

1. Connect to ESP32 AP: `trash` / `trash_123`
2. Configure MQTT broker at `http://192.168.4.1`
3. Save & Reboot
4. Monitor Serial output for connection status
5. Test with `mosquitto_sub` and `mosquitto_pub`
