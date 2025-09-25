#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include <time.h>

constexpr uint32_t NTP_VALID_EPOCH = 1609459200UL; // 2021-01-01
constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 30000UL;
constexpr unsigned long ULTRASONIC_TIMEOUT_US = 20000UL;
constexpr float SOUND_SPEED_CM_PER_US = 0.034f;

constexpr int SERVO_MIN = 0;
constexpr int SERVO_MAX = 180;
constexpr int HTTP_TIMEOUT_SENSOR_MS = 2000;
constexpr int HTTP_TIMEOUT_COMMAND_MS = 1500;
const char *API_SENSOR_PATH = "/api/sensor-data";
const char *API_COMMAND_PATH = "/api/command";

void loadConfig();
void setupAP();
void setupWebServer();
void connectSTA();
bool ensureAuth();
String buildTelemetryJson();
void configureHttp(HTTPClient &http, const String &url, int timeoutMs);
inline bool isWifiConnected();
inline bool timeIsValid();
inline int clampServo(int angle);
void requestTargetPosition(int targetAngle);
void saveConfig(const String &nssid, const String &npass, const String &nserver,
                const String &ndevice);

Preferences prefs;
WebServer server(80);

String cfg_ssid = "PXR";
String cfg_password = "27092004";
String cfg_serverURL = "http://192.168.1.10:5000";
String cfg_deviceID = "esp32_trash";

const char *apSsid = "trash";
const char *apPassword = "trash_123";
const char *apUser = "trash";

static const int servoPin = 13;
static const int trigPin = 5;
static const int echoPin = 18;
static const int pirPin = 15;
float distance;
Servo servo;

bool motionDetected = false;
bool autoMode = true;
uint32_t lastCommandId = 0;

unsigned long lastSensorReading = 0;
unsigned long lastServoMove = 0;
unsigned long lastPirCheck = 0;
unsigned long lastDataTransmission = 0;
unsigned long sensorInterval = 1000;
unsigned long servoInterval = 50;
unsigned long pirInterval = 100;
unsigned long dataInterval = 1000;
unsigned long commandPollInterval = 500;
unsigned long lastCommandPoll = 0;

int currentPosition = 0;
int targetPosition = 0;
int servoStep = 10;
bool shouldActivateServo = false;
int originalPosition = 0;
int activatedPosition = 90;

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

void requestTargetPosition(int targetAngle) {
  int clamped = clampServo(targetAngle);
  if (currentPosition == targetPosition) {
    targetPosition = clamped;
    autoMode = false;
    shouldActivateServo = (targetPosition != originalPosition);
  }
}

void configureHttp(HTTPClient &http, const String &url, int timeoutMs) {
  http.begin(url);
  http.addHeader("Connection", "keep-alive");
  http.setReuse(true);
  http.setTimeout(timeoutMs);
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
  doc["lastCommandId"] = lastCommandId;

  String jsonString;
  serializeJson(doc, jsonString);
  return jsonString;
}

void sendSensorData() {
  if (!isWifiConnected())
    return;

  HTTPClient http;
  configureHttp(http, String(cfg_serverURL) + API_SENSOR_PATH,
                HTTP_TIMEOUT_SENSOR_MS);
  http.addHeader("Content-Type", "application/json");

  String payload = buildTelemetryJson();

  int httpResponseCode = http.POST(payload);
  if (httpResponseCode > 0) {
    Serial.println(http.getString());
  }

  http.end();
}

void pollCommand() {
  if (!isWifiConnected())
    return;

  HTTPClient http;
  String url = String(cfg_serverURL) + API_COMMAND_PATH +
               "?deviceId=" + cfg_deviceID + "&lastId=" + String(lastCommandId);
  configureHttp(http, url, HTTP_TIMEOUT_COMMAND_MS);
  int code = http.GET();
  if (code == 200) {
    String payload = http.getString();
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (!err) {
      uint32_t cmdId = doc["commandId"] | 0;
      const char *action = doc["action"] | "";
      if (cmdId > lastCommandId) {
        lastCommandId = cmdId;
        if (strcmp(action, "auto") == 0) {
          autoMode = true;
        } else if (strcmp(action, "setAngle") == 0) {
          int tgt = doc["targetPosition"] | currentPosition;
          requestTargetPosition(tgt);
        }
      }
    }
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(pirPin, INPUT);
  servo.attach(servoPin);
  servo.write(currentPosition);

  WiFi.mode(WIFI_AP_STA);
  WiFi.disconnect();
  delay(100);

  loadConfig();
  setupAP();
  setupWebServer();
  connectSTA();
}

void readSensor() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long durationUs = pulseIn(echoPin, HIGH, ULTRASONIC_TIMEOUT_US);
  distance = (durationUs * SOUND_SPEED_CM_PER_US) / 2.0f;
}

void checkMotion() {
  int pirReading = digitalRead(pirPin);
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

void loop() {
  unsigned long currentTime = millis();

  yield();
  server.handleClient();

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
    sendSensorData();
  }

  if (shouldRun(currentTime, lastCommandPoll, commandPollInterval)) {
    pollCommand();
  }
}

void loadConfig() {
  prefs.begin("trash", true);
  cfg_ssid = prefs.getString("ssid", cfg_ssid);
  cfg_password = prefs.getString("pass", cfg_password);
  cfg_serverURL = prefs.getString("server", cfg_serverURL);
  cfg_deviceID = prefs.getString("device", cfg_deviceID);
  prefs.end();
}

void setupAP() {
  WiFi.softAP(apSsid, apPassword);
  IPAddress ip = WiFi.softAPIP();
  Serial.print("AP SSID: ");
  Serial.println(apSsid);
  Serial.print("AP IP: ");
  Serial.println(ip);
}

bool ensureAuth() {
  if (server.authenticate(apUser, apPassword)) {
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
        "<!DOCTYPE html><html><head><meta charset='utf-8'><meta "
        "name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>ESP32 "
        "Config</"
        "title><style>body{font-family:sans-serif;max-width:720px;margin:24px "
        "auto;padding:0 "
        "12px}label{display:block;margin-top:12px}input{width:100%;padding:8px}"
        "button{margin-top:16px;padding:10px 14px}</style></head><body>";
    html += "<h2>ESP32 Configuration</h2>";
    html += "<form method='POST' action='/save'>";
    html +=
        "<label>WiFi SSID</label><input name='ssid' value='" + cfg_ssid + "'>";
    html += "<label>WiFi Password</label><input name='pass' type='password' "
            "value='" +
            cfg_password + "'>";
    html += "<label>Server URL</label><input name='server' value='" +
            cfg_serverURL + "'>";
    html += "<label>Device ID</label><input name='device' value='" +
            cfg_deviceID + "'>";
    html += "<button type='submit'>Save & Reboot</button></form>";
    html += "<p>AP SSID: " + String(apSsid) + "</p>";
    html += "</body></html>";
    server.send(200, "text/html", html);
  });

  server.on("/save", HTTP_POST, []() {
    if (!ensureAuth())
      return;

    String nssid = server.hasArg("ssid") ? server.arg("ssid") : "";
    String npass = server.hasArg("pass") ? server.arg("pass") : "";
    String nserver = server.hasArg("server") ? server.arg("server") : "";
    String ndevice = server.hasArg("device") ? server.arg("device") : "";

    saveConfig(nssid, npass, nserver, ndevice);

    server.send(200, "text/html",
                "<html><body><h3>Saved. Rebooting...</h3></body></html>");

    delay(1000);

    ESP.restart();
  });

  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();
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

void saveConfig(const String &nssid, const String &npass, const String &nserver,
                const String &ndevice) {
  prefs.begin("trash", false);
  prefs.putString("ssid", nssid);
  prefs.putString("pass", npass);
  prefs.putString("server", nserver);
  prefs.putString("device", ndevice);
  prefs.end();

  cfg_ssid = nssid;
  cfg_password = npass;
  cfg_serverURL = nserver;
  cfg_deviceID = ndevice;
}
