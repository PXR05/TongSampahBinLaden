#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <WebServer.h>
#include <WiFi.h>
#include <time.h>

// =============================================================================
// MQTT CONFIGURATION
// =============================================================================
const int MQTT_PORT = 1883;
const char *MQTT_TOPIC_TELEMETRY = "esp32_trash/telemetry";
const char *MQTT_TOPIC_COMMAND = "esp32_trash/command";

// =============================================================================
// HARDWARE PIN DEFINITIONS
// =============================================================================
// constexpr int notifyPinRed = 25;
// constexpr int notifyPinGreen = 26;
// constexpr int notifyPinBlue = 27;
constexpr int SERVO_PIN = 13;
constexpr int TRIG_PIN = 5;
constexpr int ECHO_PIN = 18;
constexpr int PIR_PIN = 21;

// =============================================================================
// CONSTANTS
// =============================================================================
// Timing constants
constexpr uint32_t NTP_VALID_EPOCH = 1609459200UL;         // 2021-01-01
constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 30000UL; // 30 seconds
constexpr unsigned long ULTRASONIC_TIMEOUT_US = 20000UL;

// Sensor constants
constexpr float SOUND_SPEED_CM_PER_US = 0.034f; // 343 m/s = 0.034 cm/µs

// PWM constants for RGB LED
constexpr int RED_CHANNEL = 0;
constexpr int GREEN_CHANNEL = 1;
constexpr int BLUE_CHANNEL = 2;
constexpr int PWM_FREQ = 5000;
constexpr int PWM_RESOLUTION = 8;

// Servo constants
constexpr int SERVO_MIN = 0;
constexpr int SERVO_MAX = 180;

// =============================================================================
// GLOBAL OBJECTS
// =============================================================================
Preferences prefs;
WebServer server(80);
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Servo servo;

// =============================================================================
// CONFIGURATION VARIABLES
// =============================================================================
String cfg_ssid = "ssid";
String cfg_password = "password";
String cfg_mqttURL = "test.mosquitto.org";
String cfg_deviceID = "esp32_trash";

constexpr char *SSID = "trash";
constexpr char *PASSWORD = "trash_123"; // for both AP and MQTT
constexpr char *USER = "trash";

// =============================================================================
// SENSOR & STATE VARIABLES
// =============================================================================
float distance = 0.0f;
bool motionDetected = false;
bool autoMode = true;

// =============================================================================
// TIMING VARIABLES (Non-blocking task scheduling)
// =============================================================================
unsigned long lastSensorReading = 0;
unsigned long lastServoMove = 0;
unsigned long lastPirCheck = 0;
unsigned long lastDataTransmission = 0;
unsigned long statusLedTime = 0;

unsigned long sensorInterval = 1000;
unsigned long servoInterval = 50;
unsigned long pirInterval = 100;
unsigned long dataInterval = 1000;

bool statusLedActive = false;

// =============================================================================
// SERVO CONTROL VARIABLES
// =============================================================================
int currentPosition = 0;
int targetPosition = 0;
int servoStep = 10;
bool shouldActivateServo = false;
int originalPosition = 0;
int activatedPosition = 90;

// =============================================================================
// FUNCTION PROTOTYPES
// =============================================================================
// Utility functions
inline bool shouldRun(const unsigned long now, unsigned long &last,
                      const unsigned long interval);
inline bool isWifiConnected();
inline bool timeIsValid();
inline int clampServo(int angle);

// MQTT functions
void setupMQTT();
void mqttCallback(char *topic, byte *payload, unsigned int length);
void reconnectMQTT();
void publishTelemetry();
String buildTelemetryJson();

// Sensor & actuator functions
void readSensor();
void checkMotion();
void moveServo();
void requestTargetPosition(int targetAngle);
void setColor(int red, int green, int blue);

// Configuration & network functions
void loadConfig();
void saveConfig(const String &nssid, const String &npass, const String &nmqtt,
                const String &ndevice);
void setupAP();
void setupWebServer();
void connectSTA();
bool ensureAuth();

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

// =============================================================================
// HELPER FUNCTIONS
// =============================================================================

inline bool shouldRun(const unsigned long now, unsigned long &last,
                      const unsigned long interval) {
  if (now - last >= interval) {
    last = now;
    return true;
  }
  return false;
}

inline bool isWifiConnected() { return WiFi.status() == WL_CONNECTED; }

inline bool timeIsValid() { return time(nullptr) > NTP_VALID_EPOCH; }

inline int clampServo(int angle) {
  return constrain(angle, SERVO_MIN, SERVO_MAX);
}

// =============================================================================
// SETUP & MAIN LOOP
// =============================================================================

void setup() {
  Serial.begin(115200);

  // Configure sensor and actuator pins
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(PIR_PIN, INPUT);
  servo.attach(SERVO_PIN);
  servo.write(currentPosition);

  // Enable both AP (for config) and STA (for cloud connectivity) modes
  WiFi.mode(WIFI_AP_STA);
  WiFi.disconnect();
  delay(100);

  loadConfig();
  setupAP();
  setupWebServer();
  connectSTA();

  if (isWifiConnected()) {
    setupMQTT();
  }
}

void loop() {
  unsigned long currentTime = millis();

  yield();
  server.handleClient();

  // MQTT connection management
  if (isWifiConnected()) {
    if (!mqttClient.connected()) {
      reconnectMQTT();
    }
    mqttClient.loop();
  }

  // Non-blocking task execution
  if (shouldRun(currentTime, lastSensorReading, sensorInterval)) {
    readSensor();
  }

  if (shouldRun(currentTime, lastPirCheck, pirInterval)) {
    checkMotion();
  }

  if (shouldRun(currentTime, lastServoMove, servoInterval)) {
    moveServo();
  }

  if (shouldRun(currentTime, lastDataTransmission, dataInterval)) {
    publishTelemetry();
  }

  // Auto-off status LED after 500ms
  if (statusLedActive && (currentTime - statusLedTime > 500)) {
    setColor(0, 0, 0);
    statusLedActive = false;
  }
}

// =============================================================================
// MQTT FUNCTIONS
// =============================================================================

void mqttCallback(char *topic, byte *payload, unsigned int length) {
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");

  char message[length + 1];
  memcpy(message, payload, length);
  message[length] = '\0';
  Serial.println(message);

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, message);

  if (err) {
    Serial.print(F("deserializeJson() failed: "));
    Serial.println(err.c_str());
    return;
  }

  const char *action = doc["action"] | "";

  if (strcmp(action, "auto") == 0) {
    Serial.println("Switched to auto mode");
    autoMode = true;
  } else if (strcmp(action, "setAngle") == 0) {
    int tgt = doc["targetPosition"] | currentPosition;
    Serial.printf("Set target position to %d\n", tgt);
    requestTargetPosition(tgt);
  } else if (strcmp(action, "notifyEmpty") == 0) {
    Serial.println("Notification: GREEN (Empty)");
    setColor(0, 255, 0);
  } else if (strcmp(action, "notifyPartial") == 0) {
    Serial.println("Notification: BLUE (Partial)");
    setColor(0, 0, 255);
  } else if (strcmp(action, "notifyFull") == 0) {
    Serial.println("Notification: RED (Full)");
    setColor(255, 0, 0);
  }
}

void setupMQTT() {
  mqttClient.setServer(cfg_mqttURL.c_str(), MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  Serial.print("MQTT Client configured for broker: ");
  Serial.println(cfg_mqttURL);
}

void reconnectMQTT() {
  static unsigned long lastReconnectAttempt = 0;
  if (millis() - lastReconnectAttempt < 5000) {
    return;
  }
  lastReconnectAttempt = millis();

  Serial.print("Attempting MQTT connection to ");
  Serial.print(cfg_mqttURL);
  Serial.print("...");

  if (mqttClient.connect(cfg_deviceID.c_str(), USER, PASSWORD)) {
    Serial.println("connected");
    mqttClient.subscribe(MQTT_TOPIC_COMMAND);
    Serial.printf("Subscribed to topic: %s\n", MQTT_TOPIC_COMMAND);
  } else {
    Serial.print("failed, rc=");
    Serial.print(mqttClient.state());
    Serial.println(" try again in 5 seconds");
  }
}

void publishTelemetry() {
  if (!mqttClient.connected()) {
    Serial.println("MQTT client not connected. Skipping publish.");
    return;
  }

  String payload = buildTelemetryJson();

  if (mqttClient.publish(MQTT_TOPIC_TELEMETRY, payload.c_str())) {
    Serial.println("Telemetry published successfully");
    setColor(0, 255, 0);
    statusLedTime = millis();
    statusLedActive = true;
  } else {
    Serial.println("Failed to publish telemetry");
    setColor(255, 0, 0);
    statusLedTime = millis();
    statusLedActive = true;
  }
}

String buildTelemetryJson() {
  DynamicJsonDocument doc(256);
  doc["deviceId"] = cfg_deviceID.c_str();
  time_t nowSec = time(nullptr);
  if (nowSec > NTP_VALID_EPOCH) {
    doc["deviceTimestamp"] = (uint32_t)nowSec;
  }
  doc["deviceUptimeMs"] = millis();
  doc["distance"] = distance;
  doc["motion"] = motionDetected;
  doc["servoPosition"] = currentPosition;
  doc["targetPosition"] = targetPosition;
  doc["shouldActivateServo"] = shouldActivateServo;
  doc["autoMode"] = autoMode;

  String jsonString;
  serializeJson(doc, jsonString);
  return jsonString;
}

// =============================================================================
// SENSOR & ACTUATOR FUNCTIONS
// =============================================================================

void readSensor() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long durationUs = pulseIn(ECHO_PIN, HIGH, ULTRASONIC_TIMEOUT_US);

  if (durationUs == 0) {
    Serial.println("Ultrasonic timeout");
    return;
  }

  float newDistance = (durationUs * SOUND_SPEED_CM_PER_US) / 2.0f;

  if (newDistance >= 2.0) {
    distance = newDistance;
  }
}

void checkMotion() {
  int pirReading = digitalRead(PIR_PIN);
  motionDetected = (pirReading == HIGH);

  if (!autoMode) {
    return;
  }

  if (currentPosition == targetPosition) {
    if (motionDetected) {
      if (!shouldActivateServo) {
        shouldActivateServo = true;
        targetPosition = activatedPosition;
      }
    } else {
      if (shouldActivateServo) {
        shouldActivateServo = false;
        targetPosition = originalPosition;
      }
    }
  } else {
    if (motionDetected) {
      shouldActivateServo = true;
    } else {
      shouldActivateServo = false;
    }
  }
}

void moveServo() {
  if (currentPosition != targetPosition) {
    if (currentPosition < targetPosition) {
      currentPosition += servoStep;
      if (currentPosition > targetPosition) {
        currentPosition = targetPosition;
      }
    } else {
      currentPosition -= servoStep;
      if (currentPosition < targetPosition) {
        currentPosition = targetPosition;
      }
    }
    servo.write(currentPosition);
  }
}

void requestTargetPosition(int targetAngle) {
  int clamped = clampServo(targetAngle);
  if (currentPosition == targetPosition) {
    targetPosition = clamped;
    autoMode = false;
    shouldActivateServo = (targetPosition != originalPosition);
  }
}

void setColor(int red, int green, int blue) {
  ledcWrite(RED_CHANNEL, red);
  ledcWrite(GREEN_CHANNEL, green);
  ledcWrite(BLUE_CHANNEL, blue);
}

// =============================================================================
// CONFIGURATION & NETWORK FUNCTIONS
// =============================================================================

void loadConfig() {
  prefs.begin("trash", true);
  cfg_ssid = prefs.getString("ssid", cfg_ssid);
  cfg_password = prefs.getString("pass", cfg_password);
  cfg_mqttURL = prefs.getString("mqtt", cfg_mqttURL);
  cfg_deviceID = prefs.getString("device", cfg_deviceID);
  prefs.end();
}

void saveConfig(const String &nssid, const String &npass, const String &nmqtt,
                const String &ndevice) {
  prefs.begin("trash", false);
  prefs.putString("ssid", nssid);
  prefs.putString("pass", npass);
  prefs.putString("mqtt", nmqtt);
  prefs.putString("device", ndevice);
  prefs.end();
  cfg_ssid = nssid;
  cfg_password = npass;
  cfg_mqttURL = nmqtt;
  cfg_deviceID = ndevice;
}

void setupAP() {
  WiFi.softAP(SSID, PASSWORD);
  IPAddress ip = WiFi.softAPIP();
  Serial.print("AP SSID: ");
  Serial.println(SSID);
  Serial.print("AP IP: ");
  Serial.println(ip);
}

void connectSTA() {
  if (cfg_ssid.length() == 0)
    return;
  WiFi.begin(cfg_ssid.c_str(), cfg_password.c_str());
  Serial.print("Connecting to WiFi: ");
  Serial.println(cfg_ssid);
  unsigned long wifiStartTime = millis();
  while (WiFi.status() != WL_CONNECTED &&
         (millis() - wifiStartTime) < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
    yield();
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("WiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    for (int i = 0; i < 10; i++) {
      if (timeIsValid())
        break;
      delay(100);
      yield();
    }
  } else {
    Serial.println("WiFi connection failed!");
  }
}

bool ensureAuth() {
  if (server.authenticate(USER, PASSWORD)) {
    return true;
  }
  server.requestAuthentication();
  return false;
}

void setupWebServer() {
  server.on("/", HTTP_GET, []() {
    if (!ensureAuth())
      return;
    String html =
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>ESP32 Config</title>"
        "<style>"
        ":root{--bg:#f6f8fa;--card-bg:#fff;--border:#d0d7de;--muted:#57606a;--"
        "text:#24292e;--accent:#2563eb;}"
        "body{font-family:system-ui,-apple-system, Segoe UI, Roboto, Arial, "
        "sans-serif;margin:0;padding:20px;background:var(--bg);color:var(--"
        "text);}"
        ".card{background:var(--card-bg);border:1px solid "
        "var(--border);border-radius:8px;padding:16px;max-width:1200px;margin:"
        "0 auto;}"
        ".card--narrow{max-width:800px;}"
        "h1{font-size:20px;margin:0 0 12px;}"
        ".row{display:flex;gap:12px;flex-wrap:wrap;align-itemsS:center;margin-"
        "bottom:12px;}"
        ".controls{padding:12px;border:1px solid "
        "#e5e7eb;border-radius:12px;background:#f9fafb;margin-top:12px;}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,"
        "1fr));gap:12px;}"
        ".control-group{display:flex;flex-direction:column;gap:8px;min-width:"
        "220px;flex:1}"
        "label{font-size:12px;color:var(--muted);margin-bottom:6px;display:"
        "block;}"
        "input[type='text'],input[type='password'],input[type='url']{width:100%"
        ";padding:8px;border:1px solid "
        "var(--border);border-radius:6px;background:#fff;color:var(--text);}"
        ".btn{appearance:none;border:1px solid "
        "var(--border);background:#fff;color:var(--text);padding:8px "
        "12px;border-radius:8px;font-size:13px;line-height:1;cursor:pointer;"
        "transition:background .15s ease,border-color .15s ease,box-shadow "
        ".15s ease;}"
        ".btn:hover{background:#f6f8fa;border-color:#c5ced8;box-shadow:0 1px 0 "
        "rgba(27,31,36,.04);}"
        ".btn.primary{background:var(--accent);border-color:var(--accent);"
        "color:#fff;}"
        ".btn.primary:hover{filter:brightness(0.95);}"
        ".muted{color:var(--muted);font-size:12px;}"
        ".mt12{margin-top:12px;}"
        "</style></head><body>";
    html += "<div class='card card--narrow'>";
    html += "<h1>ESP32 Configuration</h1>";
    html += "<p class='muted'>Configure WiFi and MQTT broker settings.</p>";
    html += "<form method='POST' action='/save' class='controls'>";
    html += "<div class='grid'>";
    html += "<div class='control-group'><label for='ssid'>WiFi "
            "SSID</label><input type='text' id='ssid' name='ssid' value='" +
            cfg_ssid + "'></div>";
    html +=
        "<div class='control-group'><label for='pass'>WiFi "
        "Password</label><input type='password' id='pass' name='pass' value='" +
        cfg_password + "'></div>";
    html += "<div class='control-group'><label for='mqtt'>MQTT Broker "
            "URL</label><input type='text' id='mqtt' name='mqtt' value='" +
            cfg_mqttURL + "' placeholder='test.mosquitto.org'></div>";
    html +=
        "<div class='control-group'><label for='device'>Device ID (MQTT Client "
        "ID)</label><input type='text' id='device' name='device' value='" +
        cfg_deviceID + "'></div>";
    html += "</div>";
    html +=
        "<div class='row mt12'><button class='btn primary' type='submit'>Save "
        "& Reboot</button><span class='muted'>AP SSID: " +
        String(SSID) + "</span></div>";
    html += "</form>";
    html += "</div>";
    html += "</body></html>";

    server.send(200, "text/html", html);
  });

  server.on("/save", HTTP_POST, []() {
    if (!ensureAuth())
      return;
    String nssid = server.hasArg("ssid") ? server.arg("ssid") : "";
    String npass = server.hasArg("pass") ? server.arg("pass") : "";
    String nmqtt = server.hasArg("mqtt") ? server.arg("mqtt") : "";
    String ndevice = server.hasArg("device") ? server.arg("device") : "";

    saveConfig(nssid, npass, nmqtt, ndevice);
    server.send(200, "text/html",
                "<html><body><h3>Saved. Rebooting...</h3></body></html>");
    delay(1000);
    ESP.restart();
  });

  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();
}