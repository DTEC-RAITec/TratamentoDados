import os
import csv
import re
import time
import serial
from datetime import datetime, timedelta

PORTA_BLUETOOTH = "COM5"  # Ajuste para a sua porta serial bluetooth
BAUD_RATE = 115200

pasta_dados = "dados"
ARQUIVO_RESULTADOS = os.path.join(pasta_dados, "resultados.csv")
ARQUIVO_FILA = os.path.join(pasta_dados, "fila_atual.txt")

def formatar_horario(dt):
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"

def obter_proxima_execucao(competidor_id, tentativa):
    if not os.path.exists(ARQUIVO_RESULTADOS): 
        return 1
    max_ex = 0
    with open(ARQUIVO_RESULTADOS, "r", encoding="utf-8") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            try:
                if int(linha["CompetidorID"]) == competidor_id and int(linha["Tentativa"]) == tentativa:
                    max_ex = max(max_ex, int(linha["Execucao"]))
            except (ValueError, KeyError):
                continue
    return max_ex + 1

def salvar_resultado(competidor_id, tentativa, tempo_segundos):
    inicio_dt = datetime.now() - timedelta(seconds=tempo_segundos)
    fim_dt = datetime.now()
    execucao = obter_proxima_execucao(competidor_id, tentativa)
    arquivo_existe = os.path.exists(ARQUIVO_RESULTADOS)
    
    # Sistema de retentativas para evitar conflito de acesso com o Electron
    for _ in range(5):
        try:
            with open(ARQUIVO_RESULTADOS, "a", newline="", encoding="utf-8") as f:
                escritor = csv.writer(f, delimiter=";", lineterminator="\n")
                if not arquivo_existe:
                    escritor.writerow(["CompetidorID", "Tentativa", "Execucao", "Inicio", "Fim", "Status", "Motivo"])
                escritor.writerow([
                    competidor_id, tentativa, execucao,
                    formatar_horario(inicio_dt), formatar_horario(fim_dt),
                    "VALIDA", ""
                ])
            print(f"[SUCESSO] ID {competidor_id} | T{tentativa} | Tempo: {tempo_segundos}s gravado!")
            break
        except PermissionError:
            time.sleep(0.1)

def main():
    print("="*50)
    print(" SERVIÇO AUTOMÁTICO DE PISTA (PYTHON)")
    print("="*50)
    
    try:
        ser = serial.Serial(PORTA_BLUETOOTH, BAUD_RATE, timeout=1)
        print("[OK] Bluetooth conectado com sucesso.")
    except Exception as e:
        print(f"[ERRO] Falha ao abrir porta serial {PORTA_BLUETOOTH}: {e}")
        return

    ultimo_conteudo = ""

    while True:
        try:
            # 1. Verifica se o Electron atualizou quem está na pista
            if os.path.exists(ARQUIVO_FILA):
                with open(ARQUIVO_FILA, "r", encoding="utf-8") as f:
                    conteudo = f.read().strip()
                    if conteudo and conteudo != ultimo_conteudo:
                        ultimo_conteudo = conteudo
                        # Envia o "ID_TENTATIVA" (Ex: "1_1") para a ESP32 via Bluetooth
                        ser.write((conteudo + "\n").encode("utf-8"))
                        print(f"[SINCRONIZADO] Carrinho enviado para a ESP32: {conteudo}")

            # 2. Lê os dados vindos da ESP32
            if ser.in_waiting:
                linha = ser.readline().decode("utf-8", errors="ignore").strip()
                if linha:
                    print(f"  [ESP32] {linha}")
                    if linha.startswith("TEMPO_FINAL:"):
                        # Formato: TEMPO_FINAL:ID_TENTATIVA:TEMPO (Ex: TEMPO_FINAL:1_1:8.532)
                        partes = linha.replace("TEMPO_FINAL:", "").split(":")
                        if len(partes) == 2:
                            ids_tentativa, tempo_str = partes
                            cid, tentativa = map(int, ids_tentativa.split("_"))
                            tempo_segundos = float(tempo_str)
                            salvar_resultado(cid, tentativa, tempo_segundos)
            else:
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nEncerrando...")
            break
        except Exception as e:
            print(f"[ERRO NO LOOP] {e}")
            time.sleep(1)

    ser.close()

if __name__ == "__main__":
    main()
