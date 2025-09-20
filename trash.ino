#include <ArduinoJson.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include <time.h>
#define SOUND_SPEED 0.034

const char *ssid = "cia_2.4";
const char *password = "27092004";

static const int servoPin = 13;
static const int trigPin = 5;
static const int echoPin = 18;
static const int pirPin = 15;
float distance;
Servo servo;

const char *serverURL = "http://192.168.1.5:5000";
const char *dataEndpoint = "/api/sensor-data";
const char *deviceID = "esp32_trash";

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

void sendSensorData() {
  if (WiFi.status() != WL_CONNECTED)
    return;

  HTTPClient http;
  http.begin(String(serverURL) + dataEndpoint);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "keep-alive");
  http.setReuse(true);
  http.setTimeout(2000);

  DynamicJsonDocument doc(256);
  doc["deviceId"] = deviceID;
  time_t nowSec = time(nullptr);
  if (nowSec > 1609459200) {
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

  int httpResponseCode = http.POST(jsonString);
  if (httpResponseCode > 0) {
    http.getString();
  }

  http.end();
}

void pollCommand() {
  if (WiFi.status() != WL_CONNECTED)
    return;

  HTTPClient http;
  String url = String(serverURL) + "/api/command?deviceId=" + deviceID +
               "&lastId=" + String(lastCommandId);
  http.begin(url);
  http.addHeader("Connection", "keep-alive");
  http.setReuse(true);
  http.setTimeout(1500);
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
          tgt = constrain(tgt, 0, 180);
          if (currentPosition == targetPosition) {
            targetPosition = tgt;
            autoMode = false;
            shouldActivateServo = (targetPosition != originalPosition);
          }
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

  WiFi.mode(WIFI_AP);
  WiFi.disconnect();
  delay(100);

  WiFi.begin(ssid, password);
  Serial.println("Connecting to WiFi");

  unsigned long wifiStartTime = millis();
  const unsigned long wifiTimeout = 30000;

  while (WiFi.status() != WL_CONNECTED &&
         (millis() - wifiStartTime) < wifiTimeout) {
    delay(500);
    Serial.print(".");
    yield();
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.println("WiFi connected!");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    for (int i = 0; i < 10; i++) {
      if (time(nullptr) > 1609459200)
        break;
      delay(100);
      yield();
    }
  } else {
    Serial.println();
    Serial.println("WiFi connection failed!");
  }
}

void readSensor() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  int duration = pulseIn(echoPin, HIGH, 20000);
  distance = duration * SOUND_SPEED / 2;
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

  if (currentTime - lastSensorReading >= sensorInterval) {
    readSensor();
    lastSensorReading = currentTime;
  }

  if (currentTime - lastPirCheck >= pirInterval) {
    checkMotion();
    lastPirCheck = currentTime;
  }

  if (currentTime - lastServoMove >= servoInterval) {
    moveServo();
    lastServoMove = currentTime;
  }

  if (currentTime - lastDataTransmission >= dataInterval) {
    sendSensorData();
    lastDataTransmission = currentTime;
  }

  if (currentTime - lastCommandPoll >= commandPollInterval) {
    pollCommand();
    lastCommandPoll = currentTime;
  }
}