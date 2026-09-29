 #include "BluetoothSerial.h"

// Pino do sensor de leitura do feixe
#define SENSOR_PIN 23

// Tempo de trava (debounce) em milissegundos para evitar leituras duplas (2 segundos)
#define TEMPO_TRAVA 2000

BluetoothSerial SerialBT;

volatile bool sensorDisparado = false;
bool cronometroAtivo = false;
unsigned long tempoInicio = 0;
unsigned long tempoVolta = 0;
unsigned long ultimoDisparo = 0;

// Função da interrupção (executada imediatamente quando o feixe é cortado)
void IRAM_ATTR trataPassagem() {
  sensorDisparado = true;
}

void setup() {
Serial.begin(115200);

// Inicializa o Bluetooth com o nome visível no celular/PC
  SerialBT.begin("ESP32_Cronometro");

// Configura o pino de sinal com resistor Pull-Up interno
  pinMode(SENSOR_PIN, INPUT_PULLUP);

// Configura a interrupção no pino 23 para disparar quando o sinal for para GND (FALLING)
  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), trataPassagem, FALLING);

  Serial.println("--- CRONÔMETRO PRONTO ---");
  SerialBT.println("--- CRONÔMETRO PRONTO ---");
}

void loop() {
// 1. PROCESSAMENTO DO CRONÔMETRO
  if (sensorDisparado) {

  sensorDisparado = false;
  unsigned long agora = millis();

// Filtra ruídos e picos de disparo repetidos no feixe
  if (agora - ultimoDisparo > TEMPO_TRAVA) {
  ultimoDisparo = agora;

  if (!cronometroAtivo) {
// Início da contagem
  tempoInicio = agora;
  cronometroAtivo = true;
  String msgInicio = "--> CRONÔMETRO INICIADO!";
  Serial.println(msgInicio);
  SerialBT.println(msgInicio);
  } else {
// Fim da contagem e cálculo da volta
  tempoVolta = agora - tempoInicio;
  cronometroAtivo = false;

  float segundos = tempoVolta / 1000.0;
  String msgResultado = "TEMPO DE VOLTA: " + String(segundos, 3) + " s";

  Serial.println(msgResultado);
  SerialBT.println(msgResultado);
  }
}
}

// 2. PONTE DE COMUNICAÇÃO SERIAL <-> BLUETOOTH (Para troca de mensagens no terminal)
if (SerialBT.available()) {
  Serial.write(SerialBT.read());
}

if (Serial.available()) {
SerialBT.write(Serial.read());
}
} 
