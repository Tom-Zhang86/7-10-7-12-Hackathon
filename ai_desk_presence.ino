// AI Desk Presence firmware for a classic ESP32 Dev Module + LD2410C OUT pin.
// Wiring: LD2410C VCC -> ESP32 VIN/5V, GND -> GND, OUT -> D27/GPIO27.

constexpr uint8_t RADAR_PIN = 27;
constexpr unsigned long DEBOUNCE_MS = 250;
constexpr unsigned long HEARTBEAT_MS = 5000;

bool stablePresence = false;
bool candidatePresence = false;
unsigned long candidateSince = 0;
unsigned long lastReportAt = 0;

void reportPresence(bool present) {
  Serial.println(present ? "PRESENT" : "ABSENT");
  lastReportAt = millis();
}

void setup() {
  pinMode(RADAR_PIN, INPUT_PULLDOWN);
  Serial.begin(115200);
  delay(1000);

  stablePresence = digitalRead(RADAR_PIN) == HIGH;
  candidatePresence = stablePresence;
  candidateSince = millis();

  Serial.println("READY");
  reportPresence(stablePresence);
}

void loop() {
  const unsigned long now = millis();
  const bool reading = digitalRead(RADAR_PIN) == HIGH;

  if (reading != candidatePresence) {
    candidatePresence = reading;
    candidateSince = now;
  }

  if (candidatePresence != stablePresence &&
      now - candidateSince >= DEBOUNCE_MS) {
    stablePresence = candidatePresence;
    reportPresence(stablePresence);
  } else if (now - lastReportAt >= HEARTBEAT_MS) {
    // A heartbeat lets the desktop recover the current state after reconnecting.
    reportPresence(stablePresence);
  }

  delay(20);
}
