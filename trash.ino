#include <ArduinoJson.h>
#include <ESP32Servo.h>
// #include <HTTPClient.h> // No longer needed
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <PubSubClient.h> // <-- ADDED
#include <time.h>

// --- NEW MQTT Configuration ---
// MQTT broker will be configured via web interface (stored in cfg_mqttURL)
const int mqtt_port = 1883; // Standard unencrypted MQTT port
// Define your topics
#define MQTT_TOPIC_TELEMETRY "esp32_trash/telemetry"
#define MQTT_TOPIC_COMMAND "esp32_trash/command"
// Create MQTT client objects
WiFiClient espClient;
PubSubClient mqttClient(espClient);
// ----------------------------

constexpr uint32_t NTP_VALID_EPOCH = 1609459200UL;         // 2021-01-01 (Unix timestamp to validate NTP sync)
constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 30000UL; // 30 seconds max to connect to WiFi
constexpr unsigned long ULTRASONIC_TIMEOUT_US = 20000UL;   // Max time to wait for echo (prevents blocking)

constexpr float SOUND_SPEED_CM_PER_US = 0.034f; // Speed of sound at room temp: 343 m/s = 0.034 cm/µs

constexpr int RED_CHANNEL = 0; // PWM channel assignments for RGB LED
constexpr int GREEN_CHANNEL = 1;
constexpr int BLUE_CHANNEL = 2;

constexpr int PWM_FREQ = 5000;    // 5kHz PWM frequency (above audible range)
constexpr int PWM_RESOLUTION = 8; // 8-bit resolution (0-255 brightness levels)

constexpr int SERVO_MIN = 0;
constexpr int SERVO_MAX = 180;

// --- HTTP Constants Removed ---
// constexpr int HTTP_TIMEOUT_SENSOR_MS = 2000;
// constexpr int HTTP_TIMEOUT_COMMAND_MS = 1500;
// const char *API_SENSOR_PATH = "/api/sensor-data";
// const char *API_COMMAND_PATH = "/api/command";

void loadConfig();
void setupAP();
void setupWebServer();
void connectSTA();
bool ensureAuth();
String buildTelemetryJson();
// void configureHttp(HTTPClient &http, const String &url, int timeoutMs); // REMOVED
inline bool isWifiConnected();
inline bool timeIsValid();
inline int clampServo(int angle);
void requestTargetPosition(int targetAngle);
void saveConfig(const String &nssid, const String &npass, const String &nmqtt,
                const String &ndevice);
void setColor(int red, int green, int blue);
void setupRGBLED();

// --- NEW MQTT Function Prototypes ---
void setupMQTT();
void mqttCallback(char *topic, byte *payload, unsigned int length);
void reconnectMQTT();
void publishTelemetry();
// ----------------------------------

Preferences prefs;    // Non-volatile storage for configuration persistence
WebServer server(80); // HTTP server for web-based configuration UI

// Default configuration values (overridden by saved preferences)
String cfg_ssid = "ssid";
String cfg_password = "password";
String cfg_mqttURL = "test.mosquitto.org"; // MQTT broker URL (configurable via web interface)
String cfg_deviceID = "esp32_trash";

// Access Point credentials (for configuration mode)
const char *apSsid = "trash";
const char *apPassword = "trash_123";
const char *apUser = "trash";

// GPIO pin assignments
static const int notifyPinRed = 25;   // RGB LED - Red channel
static const int notifyPinGreen = 26; // RGB LED - Green channel
static const int notifyPinBlue = 27;  // RGB LED - Blue channel
static const int servoPin = 13;       // Servo motor control (lid mechanism)
static const int trigPin = 5;         // Ultrasonic sensor trigger
static const int echoPin = 18;        // Ultrasonic sensor echo
static const int pirPin = 21;         // PIR motion sensor input
float distance;                       // Last measured distance in cm
Servo servo;

// State tracking
bool motionDetected = false; // Latest PIR sensor reading
bool autoMode = true;        // Auto mode: servo opens on motion, closes when no motion
// uint32_t lastCommandId = 0;  // No longer needed for MQTT

// Non-blocking task scheduling (millis-based timestamps)
unsigned long lastSensorReading = 0;    // Last ultrasonic sensor read time
unsigned long lastServoMove = 0;        // Last servo position update time
unsigned long lastPirCheck = 0;         // Last PIR motion check time
unsigned long lastDataTransmission = 0; // Last server data transmission time
unsigned long sensorInterval = 1000;    // Read distance every 1 second
unsigned long servoInterval = 50;       // Update servo every 50ms (smooth movement)
unsigned long pirInterval = 100;        // Check motion every 100ms
unsigned long dataInterval = 1000;      // Send data to server every 1 second
// unsigned long commandPollInterval = 500; // REMOVED
// unsigned long lastCommandPoll = 0;       // REMOVED
unsigned long statusLedTime = 0; // When status LED was activated
bool statusLedActive = false;    // Whether status LED is currently on

// Servo control state
int currentPosition = 0;          // Current servo angle (0-180°)
int targetPosition = 0;           // Desired servo angle
int servoStep = 10;               // Degrees to move per update (controls speed)
bool shouldActivateServo = false; // Whether servo should be in activated state
int originalPosition = 0;         // Closed/resting position (lid closed)
int activatedPosition = 90;       // Open position (lid open for trash disposal)

// Non-blocking task scheduler helper: checks if enough time has elapsed
inline bool shouldRun(const unsigned long now, unsigned long &last,
                      const unsigned long interval)
{
  if (now - last >= interval)
  {
    last = now; // Update timestamp for next interval
    return true;
  }
  return false;
}

inline bool isWifiConnected() { return WiFi.status() == WL_CONNECTED; }

inline bool timeIsValid() { return time(nullptr) > NTP_VALID_EPOCH; }

inline int clampServo(int angle)
{
  return constrain(angle, SERVO_MIN, SERVO_MAX);
}

void requestTargetPosition(int targetAngle)
{
  int clamped = clampServo(targetAngle);
  // Only accept new target if servo is not currently moving (avoids jerky motion)
  if (currentPosition == targetPosition)
  {
    targetPosition = clamped;
    autoMode = false;                                           // Manual command disables auto mode
    shouldActivateServo = (targetPosition != originalPosition); // Track if lid is open
  }
}

// void configureHttp(...) // REMOVED

String buildTelemetryJson()
{
  DynamicJsonDocument doc(256);
  doc["deviceId"] = cfg_deviceID.c_str();
  time_t nowSec = time(nullptr);
  if (nowSec > NTP_VALID_EPOCH)
  {
    doc["deviceTimestamp"] = (uint32_t)nowSec;
  }
  doc["deviceUptimeMs"] = millis();
  doc["distance"] = distance;
  doc["motion"] = motionDetected;
  doc["servoPosition"] = currentPosition;
  doc["targetPosition"] = targetPosition;
  doc["shouldActivateServo"] = shouldActivateServo;
  doc["autoMode"] = autoMode;
  // doc["lastCommandId"] = lastCommandId; // REMOVED

  String jsonString;
  serializeJson(doc, jsonString);
  return jsonString;
}

void setColor(int red, int green, int blue)
{
  // ini kalo kebalik yg shared pin nya common anode (+3,3V ON 0 OFF 255)
  // red   = 255 - red;
  // green = 255 - green;
  // blue  = 255 - blue;

  // kl yg ini GND Yg normal cathode
  ledcWrite(RED_CHANNEL, red);
  ledcWrite(GREEN_CHANNEL, green);
  ledcWrite(BLUE_CHANNEL, blue);
}

// void setupRGBLED() {
//   // --- UN-COMMENTED ---
//   ledcSetup(RED_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
//   ledcSetup(GREEN_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
//   ledcSetup(BLUE_CHANNEL, PWM_FREQ, PWM_RESOLUTION);

//   ledcAttachPin(notifyPinRed, RED_CHANNEL);
//   ledcAttachPin(notifyPinGreen, GREEN_CHANNEL);
//   ledcAttachPin(notifyPinBlue, BLUE_CHANNEL);
//   // -------------------

//   setColor(0, 0, 0);
// }

// void sendSensorData() // REMOVED

// void pollCommand() // REMOVED

// -----------------------------------------------------------------
// --- NEW MQTT Functions ---
// -----------------------------------------------------------------

/**
 * @brief This function is called automatically when a message arrives
 * on a topic the client is subscribed to.
 * This REPLACES pollCommand() and is INSTANT.
 */
void mqttCallback(char *topic, byte *payload, unsigned int length)
{
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");

  // Create a null-terminated string from the payload
  char message[length + 1];
  memcpy(message, payload, length);
  message[length] = '\0';
  Serial.println(message);

  // Parse the JSON payload
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, message);

  if (err)
  {
    Serial.print(F("deserializeJson() failed: "));
    Serial.println(err.c_str());
    return;
  }

  const char *action = doc["action"] | "";

  // Process the action
  if (strcmp(action, "auto") == 0)
  {
    Serial.println("Switched to auto mode");
    autoMode = true;
  }
  else if (strcmp(action, "setAngle") == 0)
  {
    int tgt = doc["targetPosition"] | currentPosition;
    Serial.printf("Set target position to %d\n", tgt);
    requestTargetPosition(tgt);
  }
  else if (strcmp(action, "notifyEmpty") == 0)
  {
    Serial.println("Notification: GREEN (Empty)");
    setColor(0, 255, 0);
  }
  else if (strcmp(action, "notifyPartial") == 0)
  {
    Serial.println("Notification: BLUE (Partial)");
    setColor(0, 0, 255);
  }
  else if (strcmp(action, "notifyFull") == 0)
  {
    Serial.println("Notification: RED (Full)");
    setColor(255, 0, 0);
  }
}

/**
 * @brief Configures the MQTT client and sets the callback function.
 */
void setupMQTT()
{
  mqttClient.setServer(cfg_mqttURL.c_str(), mqtt_port);
  mqttClient.setCallback(mqttCallback);
  Serial.print("MQTT Client configured for broker: ");
  Serial.println(cfg_mqttURL);
}

/**
 * @brief Attempts to reconnect to the MQTT broker with authentication.
 * This is non-blocking and will try once every 5 seconds.
 */
void reconnectMQTT()
{
  // Use a static variable to track last attempt time
  static unsigned long lastReconnectAttempt = 0;
  if (millis() - lastReconnectAttempt < 5000)
  {
    return; // Wait 5 seconds between attempts
  }
  lastReconnectAttempt = millis();

  Serial.print("Attempting MQTT connection to ");
  Serial.print(cfg_mqttURL);
  Serial.print("...");

  // Use cfg_deviceID as the unique client ID, with authentication
  if (mqttClient.connect(cfg_deviceID.c_str(), apUser, apPassword))
  {
    Serial.println("connected");
    // Subscribe to the command topic
    mqttClient.subscribe(MQTT_TOPIC_COMMAND);
    Serial.printf("Subscribed to topic: %s\n", MQTT_TOPIC_COMMAND);
  }
  else
  {
    Serial.print("failed, rc=");
    Serial.print(mqttClient.state());
    Serial.println(" try again in 5 seconds");
  }
}

/**
 * @brief Builds the JSON telemetry and publishes it to the telemetry topic.
 * This REPLACES sendSensorData() and is NON-BLOCKING.
 */
void publishTelemetry()
{
  if (!mqttClient.connected())
  {
    Serial.println("MQTT client not connected. Skipping publish.");
    return;
  }

  String payload = buildTelemetryJson();

  if (mqttClient.publish(MQTT_TOPIC_TELEMETRY, payload.c_str()))
  {
    Serial.println("Telemetry published successfully");
    setColor(0, 255, 0); // GREEN = Success
    statusLedTime = millis();
    statusLedActive = true;
  }
  else
  {
    Serial.println("Failed to publish telemetry");
    setColor(255, 0, 0); // RED = Fail
    statusLedTime = millis();
    statusLedActive = true;
  }
}

// -----------------------------------------------------------------

void setup()
{
  Serial.begin(115200);

  // setupRGBLED(); // RGB LED setup

  // Configure sensor and actuator pins
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(pirPin, INPUT);
  servo.attach(servoPin);
  servo.write(currentPosition); // Initialize servo to closed position

  // Enable both AP (for config) and STA (for cloud connectivity) modes simultaneously
  WiFi.mode(WIFI_AP_STA);
  WiFi.disconnect(); // Clear any previous connection attempts
  delay(100);

  loadConfig();     // Load saved WiFi/server settings from flash
  setupAP();        // Start Access Point for configuration interface
  setupWebServer(); // Start HTTP server for web UI
  connectSTA();     // Attempt to connect to configured WiFi network

  // --- NEW: Setup MQTT after WiFi connects ---
  if (isWifiConnected())
  {
    setupMQTT();
  }
  // ------------------------------------------
}

void readSensor()
{
  // HC-SR04 ultrasonic sensor trigger sequence
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2); // Clean LOW pulse
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10); // 10µs HIGH pulse triggers sensor
  digitalWrite(trigPin, LOW);

  // Measure echo pulse width (time for sound to travel to object and back)
  unsigned long durationUs = pulseIn(echoPin, HIGH, ULTRASONIC_TIMEOUT_US);

  // Check for timeout or invalid pulse
  if (durationUs == 0)
  {
    Serial.println("Ultrasonic timeout");
    return; // Keep the last valid distance
  }

  float newDistance = (durationUs * SOUND_SPEED_CM_PER_US) / 2.0f;

  // Filter out junk "dead zone" readings (less than 2cm)
  if (newDistance >= 2.0)
  {
    distance = newDistance;
  }
  else
  {
    // Optional: Log the junk reading for debugging
    // Serial.printf("Junk reading filtered: %.2f cm\n", newDistance);
  }
}

void checkMotion()
{
  int pirReading = digitalRead(pirPin);
  motionDetected = (pirReading == HIGH); // PIR outputs HIGH when motion detected

  // Only control servo automatically if in auto mode
  if (!autoMode)
  {
    return;
  }

  // CASE 1: Servo is at rest (not moving)
  if (currentPosition == targetPosition)
  {
    if (motionDetected)
    {
      if (!shouldActivateServo)
      {
        shouldActivateServo = true;
        targetPosition = activatedPosition; // Open lid on motion
      }
    }
    else
    {
      if (shouldActivateServo)
      {
        shouldActivateServo = false;
        targetPosition = originalPosition; // Close lid when no motion
      }
    }
    // CASE 2: Servo is currently moving - update state based on motion
  }
  else
  {
    if (motionDetected)
    {
      shouldActivateServo = true; // Keep/set activated state
    }
    else
    {
      shouldActivateServo = false; // Keep/set deactivated state
    }
  }
}

void moveServo()
{
  if (currentPosition != targetPosition)
  {
    // Gradual movement for smooth operation (servoStep degrees per update)
    if (currentPosition < targetPosition)
    {
      currentPosition += servoStep;
      if (currentPosition > targetPosition)
      {
        currentPosition = targetPosition; // Clamp to exact target
      }
    }
    else
    {
      currentPosition -= servoStep;
      if (currentPosition < targetPosition)
      {
        currentPosition = targetPosition; // Clamp to exact target
      }
    }
    servo.write(currentPosition); // Apply new position
  }
}

void loop()
{
  unsigned long currentTime = millis();

  yield();               // Yield to WiFi/system tasks
  server.handleClient(); // Process incoming HTTP requests for config UI

  // --- NEW: MQTT Connection Management ---
  if (isWifiConnected())
  {
    if (!mqttClient.connected())
    {
      reconnectMQTT(); // Check connection and reconnect if needed
    }
    mqttClient.loop(); // *CRITICAL* - processes subscriptions and keepalives
  }
  // -------------------------------------

  // Non-blocking task execution
  if (shouldRun(currentTime, lastSensorReading, sensorInterval))
  {
    readSensor();
  }

  if (shouldRun(currentTime, lastPirCheck, pirInterval))
  {
    checkMotion();
  }

  if (shouldRun(currentTime, lastServoMove, servoInterval))
  {
    moveServo();
  }

  // --- MODIFIED: Use publishTelemetry ---
  if (shouldRun(currentTime, lastDataTransmission, dataInterval))
  {
    publishTelemetry(); // Replaces sendSensorData()
  }

  // --- REMOVED: pollCommand() is no longer needed ---
  // if (shouldRun(currentTime, lastCommandPoll, commandPollInterval)) {
  //   pollCommand();
  // }

  // Auto-off status LED after 500ms
  if (statusLedActive && (currentTime - statusLedTime > 500))
  {
    setColor(0, 0, 0); // Turn off LED
    statusLedActive = false;
  }
}

// -----------------------------------------------------------------
// --- Config & Web Server Functions (Unchanged) ---
// --- Note: cfg_serverURL is no longer used by MQTT ---
// --- but is left in the web UI for future use.     ---
// -----------------------------------------------------------------

void loadConfig()
{
  prefs.begin("trash", true);
  cfg_ssid = prefs.getString("ssid", cfg_ssid);
  cfg_password = prefs.getString("pass", cfg_password);
  cfg_mqttURL = prefs.getString("mqtt", cfg_mqttURL); // MQTT broker URL
  cfg_deviceID = prefs.getString("device", cfg_deviceID);
  prefs.end();
}

void setupAP()
{
  WiFi.softAP(apSsid, apPassword);
  IPAddress ip = WiFi.softAPIP();
  Serial.print("AP SSID: ");
  Serial.println(apSsid);
  Serial.print("AP IP: ");
  Serial.println(ip);
}

bool ensureAuth()
{
  if (server.authenticate(apUser, apPassword))
  {
    return true;
  }
  server.requestAuthentication();
  return false;
}

void setupWebServer()
{
  server.on("/", HTTP_GET, []()
            {
    if (!ensureAuth())
      return;
    String html =
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>ESP32 Config</title>"
        "<style>"
        ":root{--bg:#f6f8fa;--card-bg:#fff;--border:#d0d7de;--muted:#57606a;--text:#24292e;--accent:#2563eb;}"
        "body{font-family:system-ui,-apple-system, Segoe UI, Roboto, Arial, sans-serif;margin:0;padding:20px;background:var(--bg);color:var(--text);}"
        ".card{background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:16px;max-width:1200px;margin:0 auto;}"
        ".card--narrow{max-width:800px;}"
        "h1{font-size:20px;margin:0 0 12px;}"
        ".row{display:flex;gap:12px;flex-wrap:wrap;align-itemsS:center;margin-bottom:12px;}"
        ".controls{padding:12px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;margin-top:12px;}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;}"
        ".control-group{display:flex;flex-direction:column;gap:8px;min-width:220px;flex:1}"
        "label{font-size:12px;color:var(--muted);margin-bottom:6px;display:block;}"
        "input[type='text'],input[type='password'],input[type='url']{width:100%;padding:8px;border:1px solid var(--border);border-radius:6px;background:#fff;color:var(--text);}"
        ".btn{appearance:none;border:1px solid var(--border);background:#fff;color:var(--text);padding:8px 12px;border-radius:8px;font-size:13px;line-height:1;cursor:pointer;transition:background .15s ease,border-color .15s ease,box-shadow .15s ease;}"
        ".btn:hover{background:#f6f8fa;border-color:#c5ced8;box-shadow:0 1px 0 rgba(27,31,36,.04);}"
        ".btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;}"
        ".btn.primary:hover{filter:brightness(0.95);}"
        ".muted{color:var(--muted);font-size:12px;}"
        ".mt12{margin-top:12px;}"
        "</style></head><body>";
    html += "<div class='card card--narrow'>";
    html += "<h1>ESP32 Configuration</h1>";
    html += "<p class='muted'>Configure WiFi and MQTT broker settings. Uses AP credentials (trash/trash_123) for MQTT authentication.</p>";
    html += "<form method='POST' action='/save' class='controls'>";
    html += "<div class='grid'>";
    html += "<div class='control-group'><label for='ssid'>WiFi SSID</label><input type='text' id='ssid' name='ssid' value='" + cfg_ssid + "'></div>";
    html += "<div class='control-group'><label for='pass'>WiFi Password</label><input type='password' id='pass' name='pass' value='" + cfg_password + "'></div>";
    html += "<div class='control-group'><label for='mqtt'>MQTT Broker URL</label><input type='text' id='mqtt' name='mqtt' value='" + cfg_mqttURL + "' placeholder='test.mosquitto.org'></div>";
    html += "<div class='control-group'><label for='device'>Device ID (MQTT Client ID)</label><input type='text' id='device' name='device' value='" + cfg_deviceID + "'></div>";
    html += "</div>";
    html += "<div class='row mt12'><button class='btn primary' type='submit'>Save & Reboot</button><span class='muted'>AP SSID: " + String(apSsid) + "</span></div>";
    html += "</form>";
    html += "</div>";
    html += "</body></html>";

    server.send(200, "text/html", html); });

  server.on("/save", HTTP_POST, []()
            {
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
    ESP.restart(); });

  server.onNotFound([]()
                    { server.send(404, "text/plain", "Not found"); });
  server.begin();
}

void connectSTA()
{
  if (cfg_ssid.length() == 0)
    return;
  WiFi.begin(cfg_ssid.c_str(), cfg_password.c_str());
  Serial.print("Connecting to WiFi: ");
  Serial.println(cfg_ssid);
  unsigned long wifiStartTime = millis();
  while (WiFi.status() != WL_CONNECTED &&
         (millis() - wifiStartTime) < WIFI_CONNECT_TIMEOUT_MS)
  {
    delay(500);
    Serial.print(".");
    yield();
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.println("WiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    for (int i = 0; i < 10; i++)
    {
      if (timeIsValid())
        break;
      delay(100);
      yield();
    }
  }
  else
  {
    Serial.println("WiFi connection failed!");
  }
}

void saveConfig(const String &nssid, const String &npass, const String &nmqtt,
                const String &ndevice)
{
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