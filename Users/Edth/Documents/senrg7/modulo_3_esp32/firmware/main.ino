/**
 * main.ino — Firmware ESP32 para SENRG7
 * ─────────────────────────────────────
 * Lee corriente AC con sensor SCT-013 via ADS1115 (modo diferencial),
 * calcula el valor RMS, determina el estado de la máquina y publica
 * los datos por MQTT cada INTERVALO_MS milisegundos.
 *
 * Hardware:
 *   - ESP32 (cualquier variante con WiFi e I2C)
 *   - ADS1115 en dirección 0x48 (ADDR → GND)
 *   - SCT-013-030 conectado en AIN0 (+) y AIN1 (-)
 *
 * Librerías necesarias (Gestor de librerías Arduino IDE):
 *   - Adafruit ADS1X15   by Adafruit
 *   - PubSubClient       by Nick O'Leary
 *   - ArduinoJson        by Benoit Blanchon
 *
 * Calibración:
 *   1. Encender la máquina en operación normal.
 *   2. Medir corriente real con pinza amperimétrica → A_real
 *   3. Leer valor en Serial Monitor → A_medido
 *   4. FACTOR_CAL_nuevo = FACTOR_CAL × (A_real / A_medido)
 *   5. Actualizar FACTOR_CAL aquí y factor_calibracion en config.json
 */

// ── Includes ─────────────────────────────────────────────
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <Adafruit_ADS1X15.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ══════════════════════════════════════════════════════════
//  CONFIGURACIÓN — AJUSTAR ANTES DE FLASHEAR
// ══════════════════════════════════════════════════════════

// WiFi
const char* WIFI_SSID       = "NombreDetuRed";
const char* WIFI_PASSWORD   = "ContraseñaWiFi";

// MQTT — IP de la PC donde corre Mosquitto
const char* MQTT_BROKER     = "192.168.1.100";
const int   MQTT_PORT       = 1883;
const char* MQTT_USUARIO    = "";          // vacío = sin autenticación
const char* MQTT_PASSWORD_M = "";

// Identificación de esta máquina (número único, 1, 2, 3, 4...)
const int   MACHINE_ID      = 1;

// Medición
const int   N_MUESTRAS      = 100;         // muestras para cálculo RMS
const float FACTOR_CAL      = 30.0;        // calibrar con pinza amperimétrica
const float RUIDO_UMBRAL    = 0.10;        // lecturas < 0.1A se consideran cero

// Estados (deben coincidir con config.json → umbrales)
const float UMBRAL_APAGADA   = 0.5f;
const float UMBRAL_ENCENDIDA = 2.0f;
const float UMBRAL_BORDANDO  = 20.0f;     // < bordando = BORDANDO; >= = SOBRECARGA

// Temporización
const unsigned long INTERVALO_MS  = 1000;  // publicar cada 1 segundo
const unsigned long HEARTBEAT_MS  = 30000; // heartbeat cada 30 segundos

// ══════════════════════════════════════════════════════════
//  VARIABLES GLOBALES
// ══════════════════════════════════════════════════════════
Adafruit_ADS1115 ads;
WiFiClient       wifiClient;
PubSubClient     mqtt(wifiClient);

// Topics MQTT (calculados en setup)
char topicCorreinte[64];
char topicEvento[64];

// Estado previo para detectar cambios
String estadoPrevio = "";

// Temporizadores
unsigned long ultimaPublicacion = 0;
unsigned long ultimoHeartbeat   = 0;

// ══════════════════════════════════════════════════════════
//  PROTOTIPOS
// ══════════════════════════════════════════════════════════
void   conectarWiFi();
void   conectarMQTT();
float  calcularRMS();
String determinarEstado(float corriente);
void   publicarLectura(float corriente, const String& estado);
void   publicarEvento(const String& tipo, const String& descripcion);
void   publicarHeartbeat();

// ══════════════════════════════════════════════════════════
//  SETUP
// ══════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.printf("\n\n=== SENRG7 — Máquina %d ===\n", MACHINE_ID);
  Serial.println("Iniciando...");

  // Construir topics con el ID de esta máquina
  snprintf(topicCorreinte, sizeof(topicCorreinte),
           "senrg7/maquina/%d/corriente", MACHINE_ID);
  snprintf(topicEvento, sizeof(topicEvento),
           "senrg7/maquina/%d/evento", MACHINE_ID);

  // Inicializar I2C y ADS1115
  Wire.begin(21, 22);  // SDA=GPIO21, SCL=GPIO22
  if (!ads.begin(0x48)) {
    Serial.println("[ERROR] ADS1115 no encontrado. Verificar cableado I2C.");
    // Bucle de error — parpadeo rápido del LED interno si disponible
    while (true) {
      delay(200);
    }
  }
  ads.setGain(GAIN_TWO);                    // rango ±2.048V — ideal para SCT-013
  ads.setDataRate(RATE_ADS1115_860SPS);     // velocidad máxima
  Serial.println("[OK] ADS1115 inicializado — ganancia ±2.048V, 860 SPS");

  // Conectar WiFi
  conectarWiFi();

  // Configurar servidor MQTT
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setBufferSize(512);       // payload JSON cabe sin problemas
  mqtt.setKeepAlive(60);

  // Conectar MQTT
  conectarMQTT();

  // Publicar evento de inicio
  publicarEvento("INICIO_SISTEMA",
                 String("Maquina ") + MACHINE_ID + " lista");

  Serial.printf("[OK] Setup completo. Publicando en %s\n", topicCorreinte);
}

// ══════════════════════════════════════════════════════════
//  LOOP PRINCIPAL
// ══════════════════════════════════════════════════════════
void loop() {
  // Mantener conexiones activas
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Conexión perdida, reconectando...");
    conectarWiFi();
  }
  if (!mqtt.connected()) {
    conectarMQTT();
  }
  mqtt.loop();  // procesar mensajes entrantes y keepalive

  unsigned long ahora = millis();

  // ── Publicar lectura de corriente ──
  if (ahora - ultimaPublicacion >= INTERVALO_MS) {
    ultimaPublicacion = ahora;

    float  corriente = calcularRMS();
    String estado    = determinarEstado(corriente);

    // Detectar cambio de estado y publicar evento
    if (estado != estadoPrevio && estadoPrevio != "") {
      String desc = estadoPrevio + " → " + estado;
      desc += " (" + String(corriente, 2) + "A)";
      publicarEvento("CAMBIO_ESTADO", desc);
    }
    estadoPrevio = estado;

    publicarLectura(corriente, estado);
    Serial.printf("[M%d] %.3fA | %s\n", MACHINE_ID, corriente, estado.c_str());
  }

  // ── Heartbeat periódico ──
  if (ahora - ultimoHeartbeat >= HEARTBEAT_MS) {
    ultimoHeartbeat = ahora;
    publicarHeartbeat();
  }
}

// ══════════════════════════════════════════════════════════
//  FUNCIONES AUXILIARES
// ══════════════════════════════════════════════════════════

/**
 * Conecta al WiFi y espera hasta obtener IP.
 * Bloquea hasta que la conexión sea exitosa.
 */
void conectarWiFi() {
  Serial.printf("[WiFi] Conectando a %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int intentos = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    intentos++;
    if (intentos > 40) {
      // 20 segundos sin conexión → reiniciar
      Serial.println("\n[WiFi] Timeout. Reiniciando ESP32...");
      ESP.restart();
    }
  }
  Serial.printf("\n[WiFi] Conectado. IP: %s\n", WiFi.localIP().toString().c_str());
}

/**
 * Conecta al broker MQTT con reintentos.
 * Usa client_id único por máquina para evitar conflictos.
 */
void conectarMQTT() {
  char clientId[32];
  snprintf(clientId, sizeof(clientId), "senrg7_m%d", MACHINE_ID);

  int intentos = 0;
  while (!mqtt.connected()) {
    Serial.printf("[MQTT] Conectando a %s:%d como %s...", MQTT_BROKER, MQTT_PORT, clientId);

    bool conectado;
    if (strlen(MQTT_USUARIO) > 0) {
      conectado = mqtt.connect(clientId, MQTT_USUARIO, MQTT_PASSWORD_M);
    } else {
      conectado = mqtt.connect(clientId);
    }

    if (conectado) {
      Serial.println(" OK");
      return;
    }

    Serial.printf(" Error rc=%d. Reintentando en 5s...\n", mqtt.state());
    intentos++;
    if (intentos > 10) {
      // Demasiados fallos → reiniciar WiFi y ESP
      Serial.println("[MQTT] Demasiados intentos. Reiniciando...");
      WiFi.disconnect();
      delay(1000);
      ESP.restart();
    }
    delay(5000);
  }
}

/**
 * Toma N_MUESTRAS lecturas diferenciales (AIN0 - AIN1),
 * calcula la corriente RMS y aplica el factor de calibración.
 *
 * Fórmula RMS: sqrt( sum(Ai²) / N )
 * Filtro: si el resultado es < RUIDO_UMBRAL, retorna 0.0
 */
float calcularRMS() {
  float suma = 0.0f;

  for (int i = 0; i < N_MUESTRAS; i++) {
    int16_t raw     = ads.readADC_Differential_0_1();
    float   voltaje = ads.computeVolts(raw);
    float   amperio = voltaje * FACTOR_CAL;
    suma += amperio * amperio;
    delayMicroseconds(200);  // pequeña pausa entre muestras
  }

  float rms = sqrt(suma / N_MUESTRAS);
  return (rms < RUIDO_UMBRAL) ? 0.0f : rms;
}

/**
 * Clasifica el estado de la máquina según la corriente medida.
 * Los umbrales deben coincidir con config.json del servidor.
 */
String determinarEstado(float corriente) {
  if (corriente >= UMBRAL_BORDANDO)  return "SOBRECARGA";
  if (corriente >= UMBRAL_ENCENDIDA) return "BORDANDO";
  if (corriente >= UMBRAL_APAGADA)   return "ENCENDIDA";
  return "APAGADA";
}

/**
 * Publica una lectura de corriente en formato JSON.
 * Topic: senrg7/maquina/{id}/corriente
 * Payload: {"maquina_id":1,"corriente":12.34,"estado":"BORDANDO","ts":123456}
 */
void publicarLectura(float corriente, const String& estado) {
  StaticJsonDocument<200> doc;
  doc["maquina_id"] = MACHINE_ID;
  doc["corriente"]  = round(corriente * 1000.0f) / 1000.0f;  // 3 decimales
  doc["estado"]     = estado;
  doc["ts"]         = millis() / 1000UL;  // segundos desde arranque

  char payload[200];
  serializeJson(doc, payload, sizeof(payload));

  if (!mqtt.publish(topicCorreinte, payload, false)) {
    Serial.println("[MQTT] Error publicando lectura (buffer lleno?)");
  }
}

/**
 * Publica un evento en formato JSON.
 * Topic: senrg7/maquina/{id}/evento
 * Payload: {"maquina_id":1,"tipo":"CAMBIO_ESTADO","descripcion":"...","ts":123}
 */
void publicarEvento(const String& tipo, const String& descripcion) {
  StaticJsonDocument<256> doc;
  doc["maquina_id"]  = MACHINE_ID;
  doc["tipo"]        = tipo;
  doc["descripcion"] = descripcion;
  doc["ts"]          = millis() / 1000UL;

  char payload[256];
  serializeJson(doc, payload, sizeof(payload));

  if (!mqtt.publish(topicEvento, payload, false)) {
    Serial.println("[MQTT] Error publicando evento");
  }
  Serial.printf("[EVENTO] %s — %s\n", tipo.c_str(), descripcion.c_str());
}

/**
 * Publica un heartbeat con estado de conectividad.
 * Topic: senrg7/sistema/heartbeat
 * Payload: string "M1 | IP:192.168.1.x | RSSI:-55dBm"
 */
void publicarHeartbeat() {
  char payload[128];
  snprintf(payload, sizeof(payload),
           "M%d | IP:%s | RSSI:%ddBm",
           MACHINE_ID,
           WiFi.localIP().toString().c_str(),
           WiFi.RSSI());
  mqtt.publish("senrg7/sistema/heartbeat", payload, false);
  Serial.printf("[HB] %s\n", payload);
}
