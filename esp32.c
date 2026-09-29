#include "BluetoothSerial.h"

#define SENSOR_PIN 23
#define TEMPO_TRAVA 2000
#define TIMEOUT_VOLTA 30000

BluetoothSerial SerialBT;

volatile bool sensorDisparado = false;
bool cronometroAtivo = false;
unsigned long tempoInicio = 0;
unsigned long tempoVolta = 0;
unsigned long ultimoDisparo = 0;

// Guarda a identificação recebida do computador (Ex: "1_1" -> ID 1, Tentativa 1)
String competidorAtual = "1_1"; 

void IRAM_ATTR trataPassagem() {
  sensorDisparado = true;
}

void setup() {
  Serial.begin(115200);
  SerialBT.begin("ESP32_Cronometro");
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), trataPassagem, FALLING);

  Serial.println("--- CRONÔMETRO PRONTO ---");
  SerialBT.println("--- CRONÔMETRO PRONTO ---");
}

void loop() {
  unsigned long agora = millis();

  // 1. LEITURA DE COMANDOS DO COMPUTADOR (Recebe o ID e Tentativa atualizados)
  if (SerialBT.available()) {
    String comando = SerialBT.readStringUntil('\n');
    comando.trim();
    if (comando.length() > 0) {
      competidorAtual = comando;
      Serial.println("Pista atualizada para: " + competidorAtual);
    }
  }

  // 2. TIMEOUT DE SEGURANÇA (Caso o carrinho capote, desiste após 30s)
  if (cronometroAtivo && (agora - tempoInicio > TIMEOUT_VOLTA)) {
    cronometroAtivo = false;
    Serial.println("ERRO: TIMEOUT! VOLTA ABORTADA.");
    SerialBT.println("ERRO: TIMEOUT! VOLTA ABORTADA.");
  }

  // 3. PROCESSAMENTO DO SENSOR
  if (sensorDisparado) {
    sensorDisparado = false;

    if (agora - ultimoDisparo > TEMPO_TRAVA) {
      ultimoDisparo = agora;

      if (!cronometroAtivo) {
        tempoInicio = agora;
        cronometroAtivo = true;
        Serial.println("--> CRONÔMETRO INICIADO [" + competidorAtual + "]");
      } else {
        tempoVolta = agora - tempoInicio;
        cronometroAtivo = false;

        float segundos = tempoVolta / 1000.0;
        // PACOTE AMARRADO: Devolve o ID, a Tentativa e o Tempo exato
        String msgResultado = "TEMPO_FINAL:" + competidorAtual + ":" + String(segundos, 3);

        Serial.println(msgResultado);
        SerialBT.println(msgResultado);
      }
    }
  }
}
