# Skill: ESP32 Firmware

## Hardware de este proyecto
- **Microcontrolador:** ESP32 (cualquier variante con WiFi y I2C)
- **ADC externo:** ADS1115 en dirección I2C `0x48` (ADDR→GND)
- **Sensor de corriente:** SCT-013 conectado en modo diferencial (AIN0/AIN1)
- **Pines I2C:** SDA=GPIO21, SCL=GPIO22

## Librerías requeridas (instalar en Arduino IDE → Gestor de librerías)
```
Adafruit ADS1X15   by Adafruit          ← para ADS1115
PubSubClient       by Nick O'Leary      ← para MQTT
ArduinoJson        by Benoit Blanchon   ← para payload JSON
WiFi.h             incluida con ESP32   ← no instalar
```

## Estructura estándar del archivo .ino
```
1. Includes
2. Constantes de configuración en MAYÚSCULAS
3. Variables globales
4. Prototipos de funciones (opcional)
5. setup()
6. loop()
7. Implementación de funciones auxiliares
```

## Constantes de configuración (siempre al inicio, siempre MAYÚSCULAS)
```cpp
const char* WIFI_SSID       = "NombreRed";
const char* WIFI_PASSWORD   = "Contraseña";
const char* MQTT_BROKER     = "192.168.1.X";  // IP del servidor Windows
const int   MQTT_PORT       = 1883;
const char* MQTT_USUARIO    = "";              // vacío = sin auth
const char* MQTT_PASSWORD_M = "";
const int   MACHINE_ID      = 1;              // ID único por máquina

const int   N_MUESTRAS      = 100;            // muestras para RMS
const float FACTOR_CAL      = 30.0;           // ajustar en calibración

const float UMBRAL_APAGADA   = 0.5;
const float UMBRAL_ENCENDIDA = 2.0;
const float UMBRAL_BORDANDO  = 20.0;

const int   INTERVALO_MS     = 1000;           // publicar cada 1s
const int   HEARTBEAT_MS     = 30000;          // heartbeat cada 30s
```

## Cálculo RMS con ADS1115 diferencial
```cpp
float calcularRMS() {
    float suma = 0.0;
    for (int i = 0; i < N_MUESTRAS; i++) {
        int16_t raw     = ads.readADC_Differential_0_1();
        float   voltaje = ads.computeVolts(raw);
        float   amp     = voltaje * FACTOR_CAL;
        suma += amp * amp;
        delayMicroseconds(200);
    }
    float rms = sqrt(suma / N_MUESTRAS);
    return (rms < 0.1) ? 0.0 : rms;  // filtro de ruido
}
```

## Configuración del ADS1115
```cpp
ads.begin(0x48);
ads.setGain(GAIN_TWO);              // rango ±2.048V — ideal para SCT-013
ads.setDataRate(RATE_ADS1115_860SPS); // máxima velocidad
```

## Topics MQTT (formato fijo — no cambiar)
```
Publicar:
  senrg7/maquina/{MACHINE_ID}/corriente
    payload: {"maquina_id":1,"corriente":12.3,"estado":"BORDANDO","ts":123456}

  senrg7/maquina/{MACHINE_ID}/evento
    payload: {"maquina_id":1,"tipo":"CAMBIO_ESTADO","descripcion":"...","ts":123456}

  senrg7/sistema/heartbeat
    payload: "M1 | IP:192.168.1.X | RSSI:-55dBm"
```

## Reconexión automática (obligatoria en loop)
```cpp
void loop() {
    if (WiFi.status() != WL_CONNECTED) conectarWiFi();
    if (!mqtt.connected()) conectarMQTT();
    mqtt.loop();
    // ... resto del código
}
```

## Calibración del FACTOR_CAL
1. Encender la máquina bordadora en estado normal de operación
2. Medir corriente real con pinza amperimétrica → valor A_real
3. Ver corriente reportada en Serial Monitor → valor A_medido
4. Calcular: `FACTOR_CAL_nuevo = FACTOR_CAL_actual × (A_real / A_medido)`
5. Actualizar `FACTOR_CAL` en main.ino y también en `config.json` como `factor_calibracion`

## Debug
```cpp
Serial.begin(115200);  // en setup()
Serial.printf("[M%d] %.2fA | %s\n", MACHINE_ID, corriente, estado.c_str());
```
