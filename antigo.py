"""
Cronômetro de pista - Recepção via Bluetooth (ESP32 -> PC)
------------------------------------------------------------
Cada corredor faz 3 VOLTAS. O tempo final que vai para o pódio é a
SOMA das 3 voltas.

A cada volta concluída, o tempo é comparado com a mesma volta do
atual 1º colocado do pódio (referência de ritmo).

Janela dividida:
    - Lado ESQUERDO: corrida em andamento
      (piloto atual, volta a volta, com comparação ao líder).
    - Lado DIREITO: pódio
      (ranking pelo tempo total das 3 voltas).

O CSV grava, para cada corredor:
    Volta 1, Volta 2, Volta 3 e Total.

Comunicação:
    ESP32 -> Bluetooth -> Porta Serial Bluetooth -> Python

Requisitos:
    pip install pyserial

    tkinter normalmente já vem com o Python no Windows/Mac.
    Linux:
        sudo apt install python3-tk

Antes de rodar:
    1. Pareie o ESP32 ("ESP32_Cronometro") com o computador pelo
       Bluetooth do sistema operacional.

    2. Descubra a porta serial criada pelo pareamento.

       Windows:
           Gerenciador de Dispositivos
           -> Portas (COM e LPT)
           -> Standard Serial over Bluetooth link
           Exemplo: COM5

       Linux:
           Geralmente algo como /dev/rfcomm0

       Mac:
           Normalmente algo como /dev/tty.ESP32_Cronometro-XXXX

    3. Ajuste PORTA_BLUETOOTH abaixo.

Uso:
    - Digite o nome do piloto/kart no CONSOLE.
    - O programa aguarda as 3 voltas vindas do ESP32.
    - A janela mostra a corrida atual à esquerda e o pódio à direita.
    - F11 alterna tela cheia.
    - ESC sai da tela cheia.
    - No console:
          podio -> mostra o ranking
          sair  -> encerra e salva o CSV

Modo de teste:
    MODO_TESTE = True

    Nesse modo não é necessário ESP32.

    O programa simula exatamente o fluxo das 3 voltas:
        CRONÔMETRO INICIADO!
        TEMPO DE VOLTA: X.XXX s

    repetido 3 vezes para cada corredor.
"""

import tkinter as tk
import serial
import threading
import queue
import csv
import re
import time
import random
from datetime import datetime


# ======================= CONFIGURAÇÃO =======================

MODO_TESTE = False

# Porta serial criada pelo Bluetooth do sistema operacional.
# Exemplos:
# Windows: COM5
# Linux: /dev/rfcomm0
# Mac: /dev/tty.ESP32_Cronometro-XXXX
PORTA_BLUETOOTH = "/dev/rfcomm0"

BAUD_RATE = 115200

ARQUIVO_CSV = "tempos_volta.csv"

# Número de voltas que cada corredor fará.
NUM_VOLTAS = 3

# =============================================================


# ======================= ESTADO GLOBAL =======================

fila_mensagens = queue.Queue()

parar_thread = False
encerrar_app = False

# Lista dos corredores que já terminaram.
registros = []

lock_registros = threading.Lock()


# Informações do corredor que está correndo neste momento.
corrida_atual = {
    "nome": None,
    "voltas": [],
    "diferencas": [],
    "status": "aguardando",  # "aguardando", "correndo", "finalizado"
}

lock_corrida_atual = threading.Lock()

# Evento para controlar quando o próximo corredor pode começar
evento_proximo_corredor = threading.Event()
evento_proximo_corredor.set()  # Inicialmente liberado

# =============================================================


# ======================= MODO DE TESTE =======================

def gerador_dados_teste():
    """
    Simula o comportamento do ESP32.

    Para cada volta:
        1. envia CRONÔMETRO INICIADO!
        2. espera um pequeno intervalo
        3. envia TEMPO DE VOLTA

    Assim, cada uma das 3 voltas é simulada individualmente.

    O tempo da volta é escolhido antes do envio, mas o programa não
    precisa esperar o tempo real completo. Isso deixa os testes rápidos.
    """

    while not parar_thread:

        # Aguarda o sinal para começar a próxima corrida
        evento_proximo_corredor.wait()

        if parar_thread:
            break

        # Pequena espera antes de começar a próxima corrida.
        time.sleep(random.uniform(1.5, 2.5))

        if parar_thread:
            break

        # Simula as 3 voltas individualmente.
        for numero_volta in range(1, NUM_VOLTAS + 1):

            if parar_thread:
                break

            # -------------------------------------------------
            # EVENTO 1: início da volta
            # -------------------------------------------------

            fila_mensagens.put("--> CRONÔMETRO INICIADO!")

            # Tempo que essa volta teria no "ESP32".
            duracao = random.uniform(7.0, 15.0)

            # Pequena pausa para a interface conseguir
            # mostrar que a volta começou.
            time.sleep(random.uniform(2.0, 3.0))  # Aumentado o intervalo

            if parar_thread:
                break

            # -------------------------------------------------
            # EVENTO 2: fim da volta
            # -------------------------------------------------

            fila_mensagens.put(
                f"TEMPO DE VOLTA: {duracao:.3f} s"
            )

            # Intervalo entre uma volta e outra.
            time.sleep(random.uniform(1.5, 2.5))  # Aumentado o intervalo


# =============================================================


# ======================= LEITURA BLUETOOTH =======================

def leitor_serial(ser):
    """
    Thread dedicada a receber continuamente os dados vindos
    da porta serial Bluetooth.

    O ESP32 usa:

        SerialBT.println(...)

    Portanto, as mensagens chegam separadas por '\n'.

    Esta função monta as linhas corretamente antes de colocá-las
    na fila usada pela lógica principal.
    """

    global parar_thread

    buffer = ""

    while not parar_thread:

        try:

            if ser.in_waiting:

                dado = ser.read(
                    ser.in_waiting
                ).decode(
                    "utf-8",
                    errors="ignore"
                )

                buffer += dado

                # Processa todas as linhas completas recebidas.
                while "\n" in buffer:

                    linha, buffer = buffer.split(
                        "\n",
                        1
                    )

                    linha = linha.strip()

                    if linha:
                        fila_mensagens.put(linha)

            else:
                time.sleep(0.05)

        except Exception as e:

            fila_mensagens.put(
                f"[ERRO LEITURA] {e}"
            )

            break


# =============================================================


# ======================= EXTRAÇÃO DO TEMPO =======================

def extrair_tempo(linha):
    """
    Extrai o tempo de uma mensagem enviada pelo ESP32.

    Exemplo recebido:

        TEMPO DE VOLTA: 8.532 s

    Retorna:

        8.532
    """

    match = re.search(
        r"TEMPO DE VOLTA:\s*([\d.,]+)\s*s",
        linha
    )

    if match:

        valor = match.group(1).replace(
            ",",
            "."
        )

        try:
            return float(valor)

        except ValueError:
            return None

    return None


# =============================================================


# ======================= LÍDER =======================

def obter_lider():
    """
    Retorna o corredor que atualmente está em primeiro lugar.

    O ranking é baseado no tempo TOTAL das 3 voltas.

    Retorna None caso ninguém tenha terminado ainda.
    """

    with lock_registros:

        if not registros:
            return None

        return min(
            registros,
            key=lambda r: r["total"]
        )


# =============================================================


# ======================= CSV =======================

def salvar_csv(lista_registros):
    """
    Salva todos os corredores no CSV.

    A classificação é recalculada pelo tempo total.
    """

    if not lista_registros:

        print("Nenhum registro para salvar.")

        return

    ordenados = sorted(
        lista_registros,
        key=lambda r: r["total"]
    )

    with open(
        ARQUIVO_CSV,
        "w",
        newline="",
        encoding="utf-8"
    ) as f:

        escritor = csv.writer(f)

        cabecalho = [
            "Posicao",
            "Piloto_Kart",
        ]

        cabecalho += [
            f"Volta_{i + 1}_s"
            for i in range(NUM_VOLTAS)
        ]

        cabecalho += [
            "Total_s",
            "Data_Hora",
        ]

        escritor.writerow(cabecalho)

        for i, r in enumerate(
            ordenados,
            start=1
        ):

            linha = [
                i,
                r["nome"],
            ]

            linha += [
                f"{v:.3f}"
                for v in r["voltas"]
            ]

            linha += [
                f"{r['total']:.3f}",
                r["hora"],
            ]

            escritor.writerow(linha)

    print(
        f"CSV atualizado: {ARQUIVO_CSV}"
    )


# =============================================================


# ======================= PODIO NO CONSOLE =======================

def mostrar_podio(lista_registros):
    """
    Mostra o ranking completo no console.
    """

    if not lista_registros:

        print(
            "Nenhum corredor finalizado ainda."
        )

        return

    ordenados = sorted(
        lista_registros,
        key=lambda r: r["total"]
    )

    medalhas = [
        "1o",
        "2o",
        "3o",
    ]

    print(
        "\n===================== PODIO ====================="
    )

    for i, r in enumerate(
        ordenados[:3]
    ):

        voltas_str = " | ".join(
            f"V{j + 1}: {v:.3f}s"
            for j, v in enumerate(
                r["voltas"]
            )
        )

        print(
            f"{medalhas[i]} lugar: "
            f"{r['nome']} - "
            f"TOTAL {r['total']:.3f} s "
            f"({voltas_str})"
        )

    if len(ordenados) > 3:

        print(
            "\n--- Demais colocacoes ---"
        )

        for i, r in enumerate(
            ordenados[3:],
            start=4
        ):

            print(
                f"{i}o lugar: "
                f"{r['nome']} - "
                f"TOTAL {r['total']:.3f} s"
            )

    print(
        "===================================================\n"
    )


# =============================================================


# ======================= LÓGICA PRINCIPAL =======================

def logica_console(ser):
    """
    Controla a sequência das corridas.

    Para cada corredor:

        1. Recebe o nome.
        2. Aguarda início da volta.
        3. Aguarda resultado da volta.
        4. Registra a volta.
        5. Compara com a mesma volta do líder.
        6. Repete até completar NUM_VOLTAS.
        7. Soma as voltas.
        8. Adiciona o corredor ao ranking.
        9. Atualiza o CSV.

    O comportamento é o mesmo tanto para o ESP32 real quanto
    para o modo de teste.
    """

    global encerrar_app

    print(
        "\n=== CRONOMETRO CONECTADO ==="
    )

    print(
        f"Cada corredor faz {NUM_VOLTAS} voltas."
    )

    print(
        f"O tempo final e a soma das {NUM_VOLTAS} voltas."
    )

    print(
        "Comandos especiais: "
        "'podio' mostra o ranking | "
        "'sair' encerra tudo\n"
    )

    try:

        while not encerrar_app:

            # -------------------------------------------------
            # ESCOLHA DO CORREDOR
            # -------------------------------------------------

            nome = input(
                "Nome do piloto/kart "
                "(ou 'podio'/'sair'): "
            ).strip()

            if nome.lower() == "sair":

                break

            if nome.lower() == "podio":

                with lock_registros:
                    copia = list(registros)

                mostrar_podio(copia)

                continue

            if nome == "":
                continue

            # -------------------------------------------------
            # LIMPA A CORRIDA ATUAL APÓS O INPUT DO NOVO CORREDOR
            # -------------------------------------------------

            with lock_corrida_atual:

                corrida_atual["nome"] = nome
                corrida_atual["voltas"] = []
                corrida_atual["diferencas"] = []
                corrida_atual["status"] = "correndo"

            # Libera o gerador de dados para começar a nova corrida
            evento_proximo_corredor.set()

            # -------------------------------------------------
            # DEFINE O LÍDER DE REFERÊNCIA
            # -------------------------------------------------

            lider = obter_lider()

            # IMPORTANTE:
            # O líder é definido no começo da corrida.
            #
            # Dessa forma, todas as 3 voltas desse corredor
            # são comparadas com o MESMO líder.
            #
            # O líder não muda no meio da corrida.

            voltas_piloto = []

            corrida_abortada = False

            # -------------------------------------------------
            # 3 VOLTAS
            # -------------------------------------------------

            for numero_volta in range(
                1,
                NUM_VOLTAS + 1
            ):

                tempo = None

                iniciou_volta = False

                # -------------------------------------------------
                # ESPERA OS EVENTOS DA VOLTA
                # -------------------------------------------------

                while tempo is None:

                    if encerrar_app:

                        corrida_abortada = True

                        break

                    try:

                        linha = fila_mensagens.get(
                            timeout=0.5
                        )

                    except queue.Empty:

                        continue

                    print(
                        f"[ESP32] {linha}"
                    )

                    # ---------------------------------------------
                    # EVENTO DE INÍCIO
                    # ---------------------------------------------

                    if (
                        "CRONÔMETRO INICIADO"
                        in linha
                        or
                        "CRONOMETRO INICIADO"
                        in linha
                    ):

                        iniciou_volta = True

                        print(
                            f"Volta {numero_volta} "
                            f"iniciada para: {nome}"
                        )

                        # Não tentamos extrair tempo dessa mensagem.
                        continue

                    # ---------------------------------------------
                    # EVENTO DE FIM
                    # ---------------------------------------------

                    tempo_extraido = extrair_tempo(
                        linha
                    )

                    if tempo_extraido is not None:

                        tempo = tempo_extraido

                # -------------------------------------------------
                # CORRIDA ABORTADA
                # -------------------------------------------------

                if corrida_abortada:

                    break

                # -------------------------------------------------
                # REGISTRA A VOLTA
                # -------------------------------------------------

                voltas_piloto.append(
                    tempo
                )

                # -------------------------------------------------
                # COMPARAÇÃO COM O LÍDER
                # -------------------------------------------------

                diff = None

                if (
                    lider is not None
                    and
                    len(lider["voltas"])
                    >= numero_volta
                ):

                    diff = (
                        tempo
                        -
                        lider["voltas"][
                            numero_volta - 1
                        ]
                    )

                # -------------------------------------------------
                # ATUALIZA INTERFACE
                # -------------------------------------------------

                with lock_corrida_atual:

                    corrida_atual["voltas"] = list(
                        voltas_piloto
                    )

                    corrida_atual["diferencas"] = (
                        corrida_atual["diferencas"]
                        +
                        [diff]
                    )

                # -------------------------------------------------
                # MOSTRA NO CONSOLE
                # -------------------------------------------------

                msg = (
                    f"Volta {numero_volta}: "
                    f"{tempo:.3f}s"
                )

                if diff is not None:

                    sinal = (
                        "+"
                        if diff >= 0
                        else ""
                    )

                    msg += (
                        f"  ({sinal}"
                        f"{diff:.3f}s "
                        f"vs lider)"
                    )

                elif lider is None:

                    msg += (
                        "  "
                        "(definindo tempo de referencia)"
                    )

                print(msg)

            # -------------------------------------------------
            # SE A CORRIDA FOI CANCELADA
            # -------------------------------------------------

            if corrida_abortada:

                break

            # -------------------------------------------------
            # CALCULA TOTAL
            # -------------------------------------------------

            total = sum(
                voltas_piloto
            )

            # -------------------------------------------------
            # CRIA REGISTRO
            # -------------------------------------------------

            registro = {
                "nome": nome,

                "voltas": voltas_piloto,

                "total": total,

                "hora": datetime.now().strftime(
                    "%d/%m/%Y %H:%M:%S"
                ),
            }

            # -------------------------------------------------
            # ADICIONA AO RANKING
            # -------------------------------------------------

            with lock_registros:

                registros.append(
                    registro
                )

                # Copia para salvar sem manter o lock durante
                # a escrita do arquivo.
                copia_registros = list(
                    registros
                )

            # -------------------------------------------------
            # RESULTADO
            # -------------------------------------------------

            print(
                f"\nCorrida finalizada: "
                f"{nome} - "
                f"TOTAL {total:.3f}s"
            )

            # -------------------------------------------------
            # MARCA COMO FINALIZADO E BLOQUEIA O GERADOR
            # -------------------------------------------------

            with lock_corrida_atual:
                corrida_atual["status"] = "finalizado"

            # Bloqueia o gerador de dados até o próximo corredor
            evento_proximo_corredor.clear()

            # -------------------------------------------------
            # SALVA CSV IMEDIATAMENTE
            # -------------------------------------------------

            salvar_csv(
                copia_registros
            )

            print()

            # NOTA: Não limpamos a corrida_atual aqui!
            # Ela permanece visível até o próximo corredor ser digitado.

    except KeyboardInterrupt:

        print(
            "\nInterrompido pelo usuario."
        )

    finally:

        encerrar_app = True
        evento_proximo_corredor.set()  # Libera o gerador para encerrar

        if ser is not None:

            try:
                ser.close()

            except Exception:
                pass

        with lock_registros:

            copia = list(
                registros
            )

        mostrar_podio(
            copia
        )

        salvar_csv(
            copia
        )

        print(
            "Logica encerrada."
        )


# =============================================================


# ======================= INTERFACE =======================

class JanelaPrincipal:
    """
    Janela principal dividida em duas colunas:

        ESQUERDA:
            corrida atual

        DIREITA:
            pódio
    """

    COR_FUNDO = "#0d0d0d"

    COR_PAINEL = "#141414"

    COR_TITULO = "#ffffff"

    COR_VERDE = "#3ddc57"

    COR_VERMELHO = "#ff4c4c"

    COR_CINZA = "#999999"

    CORES_PODIO = [
        "#FFD700",
        "#C0C0C0",
        "#CD7F32",
    ]

    LABELS_POS = [
        "1º",
        "2º",
        "3º",
    ]

    def __init__(self):

        self.root = tk.Tk()

        self.root.title(
            "Cronômetro da Pista"
        )

        self.root.configure(
            bg=self.COR_FUNDO
        )

        self.root.geometry(
            "1400x800"
        )

        self.tela_cheia = False

        self.root.bind(
            "<F11>",
            self.alternar_tela_cheia
        )

        self.root.bind(
            "<Escape>",
            self.sair_tela_cheia
        )

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.fechar_janela
        )

        # -------------------------------------------------
        # GRID PRINCIPAL
        # -------------------------------------------------

        self.root.columnconfigure(
            0,
            weight=1
        )

        self.root.columnconfigure(
            1,
            weight=1
        )

        self.root.rowconfigure(
            0,
            weight=1
        )

        # -------------------------------------------------
        # DICA
        # -------------------------------------------------

        dica = tk.Label(
            self.root,

            text=(
                "F11: tela cheia   |   "
                "ESC: sair da tela cheia"
            ),

            font=("Arial", 11),

            fg="#555555",

            bg=self.COR_FUNDO,
        )

        dica.grid(
            row=1,
            column=0,
            columnspan=2,
            pady=(0, 10)
        )

        # =================================================
        # PAINEL ESQUERDO
        # =================================================

        self.painel_esquerdo = tk.Frame(
            self.root,
            bg=self.COR_PAINEL
        )

        self.painel_esquerdo.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(30, 15),
            pady=30
        )

        tk.Label(
            self.painel_esquerdo,

            text="VOLTA ATUAL",

            font=(
                "Arial",
                32,
                "bold"
            ),

            fg=self.COR_TITULO,

            bg=self.COR_PAINEL,
        ).pack(
            pady=(30, 10)
        )

        # -------------------------------------------------
        # PILOTO ATUAL
        # -------------------------------------------------

        self.lbl_piloto_atual = tk.Label(
            self.painel_esquerdo,

            text="— aguardando piloto —",

            font=(
                "Arial",
                26,
                "bold"
            ),

            fg="#4da3ff",

            bg=self.COR_PAINEL,

            wraplength=550,
        )

        self.lbl_piloto_atual.pack(
            pady=(0, 30)
        )

        # -------------------------------------------------
        # VOLTAS
        # -------------------------------------------------

        self.linhas_voltas = []

        for i in range(NUM_VOLTAS):

            linha = tk.Frame(
                self.painel_esquerdo,
                bg=self.COR_PAINEL
            )

            linha.pack(
                pady=10,
                fill="x",
                padx=40
            )

            lbl_num = tk.Label(
                linha,

                text=f"Volta {i + 1}",

                font=(
                    "Arial",
                    20,
                    "bold"
                ),

                fg="white",

                bg=self.COR_PAINEL,

                width=9,

                anchor="w",
            )

            lbl_num.pack(
                side="left"
            )

            lbl_tempo = tk.Label(
                linha,

                text="--",

                font=(
                    "Arial",
                    22,
                    "bold"
                ),

                fg="white",

                bg=self.COR_PAINEL,

                width=10,

                anchor="w",
            )

            lbl_tempo.pack(
                side="left"
            )

            lbl_diff = tk.Label(
                linha,

                text="",

                font=(
                    "Arial",
                    18,
                    "bold"
                ),

                fg=self.COR_CINZA,

                bg=self.COR_PAINEL,

                anchor="w",
            )

            lbl_diff.pack(
                side="left",
                padx=(10, 0)
            )

            self.linhas_voltas.append(
                (
                    lbl_tempo,
                    lbl_diff
                )
            )

        # -------------------------------------------------
        # TOTAL
        # -------------------------------------------------

        self.lbl_total_atual = tk.Label(
            self.painel_esquerdo,

            text="",

            font=(
                "Arial",
                22,
                "bold"
            ),

            fg="#ffd700",

            bg=self.COR_PAINEL,
        )

        self.lbl_total_atual.pack(
            pady=(30, 10)
        )

        # -------------------------------------------------
        # STATUS
        # -------------------------------------------------

        self.lbl_status = tk.Label(
            self.painel_esquerdo,

            text="",

            font=(
                "Arial",
                20,
                "bold"
            ),

            fg=self.COR_VERDE,

            bg=self.COR_PAINEL,
        )

        self.lbl_status.pack(
            pady=(10, 20)
        )

        # =================================================
        # PAINEL DIREITO
        # =================================================

        self.painel_direito = tk.Frame(
            self.root,
            bg=self.COR_PAINEL
        )

        self.painel_direito.grid(
            row=0,
            column=1,
            sticky="nsew",
            padx=(15, 30),
            pady=30
        )

        tk.Label(
            self.painel_direito,

            text="🏆 PÓDIO 🏆",

            font=(
                "Arial",
                32,
                "bold"
            ),

            fg=self.COR_TITULO,

            bg=self.COR_PAINEL,
        ).pack(
            pady=(30, 20)
        )

        # -------------------------------------------------
        # TOP 3
        # -------------------------------------------------

        self.labels_podio = []

        for i in range(3):

            linha = tk.Frame(
                self.painel_direito,
                bg=self.COR_PAINEL
            )

            linha.pack(
                pady=12,
                fill="x",
                padx=30
            )

            lbl_pos = tk.Label(
                linha,

                text=self.LABELS_POS[i],

                font=(
                    "Arial",
                    24,
                    "bold"
                ),

                fg=self.CORES_PODIO[i],

                bg=self.COR_PAINEL,

                width=4,

                anchor="e",
            )

            lbl_pos.pack(
                side="left",
                padx=(0, 15)
            )

            lbl_valor = tk.Label(
                linha,

                text="— aguardando —",

                font=(
                    "Arial",
                    24,
                    "bold"
                ),

                fg="white",

                bg=self.COR_PAINEL,

                anchor="w",

                justify="left",
            )

            lbl_valor.pack(
                side="left"
            )

            self.labels_podio.append(
                lbl_valor
            )

        # -------------------------------------------------
        # SEPARADOR
        # -------------------------------------------------

        separador = tk.Frame(
            self.painel_direito,

            bg="#333333",

            height=2
        )

        separador.pack(
            fill="x",
            padx=40,
            pady=20
        )

        tk.Label(
            self.painel_direito,

            text="Demais colocações",

            font=(
                "Arial",
                16,
                "bold"
            ),

            fg="#aaaaaa",

            bg=self.COR_PAINEL,
        ).pack()

        # -------------------------------------------------
        # DEMAIS COLOCAÇÕES
        # -------------------------------------------------

        self.texto_demais = tk.Text(
            self.painel_direito,

            font=(
                "Arial",
                15
            ),

            fg="white",

            bg="#1a1a1a",

            height=10,

            width=45,

            bd=0,

            highlightthickness=0,
        )

        self.texto_demais.pack(
            pady=15,
            padx=20
        )

        self.texto_demais.config(
            state="disabled"
        )

        # -------------------------------------------------
        # COMEÇA ATUALIZAÇÃO
        # -------------------------------------------------

        self.atualizar()

    # =====================================================
    # TELA CHEIA
    # =====================================================

    def alternar_tela_cheia(
        self,
        event=None
    ):

        self.tela_cheia = not self.tela_cheia

        self.root.attributes(
            "-fullscreen",
            self.tela_cheia
        )

    def sair_tela_cheia(
        self,
        event=None
    ):

        self.tela_cheia = False

        self.root.attributes(
            "-fullscreen",
            False
        )

    # =====================================================
    # FECHAR
    # =====================================================

    def fechar_janela(self):

        global encerrar_app
        global parar_thread

        encerrar_app = True
        parar_thread = True
        evento_proximo_corredor.set()  # Libera o gerador para encerrar

        with lock_registros:

            copia = list(
                registros
            )

        salvar_csv(
            copia
        )

        self.root.destroy()

    # =====================================================
    # PAINEL ESQUERDO
    # =====================================================

    def _atualizar_painel_esquerdo(self):

        with lock_corrida_atual:

            nome = corrida_atual["nome"]

            voltas = list(
                corrida_atual["voltas"]
            )

            diffs = list(
                corrida_atual["diferencas"]
            )

            status = corrida_atual["status"]

        # -------------------------------------------------
        # NENHUMA CORRIDA
        # -------------------------------------------------

        if nome is None or status == "aguardando":

            self.lbl_piloto_atual.config(
                text="— aguardando piloto —",
                fg="#4da3ff"
            )

            for (
                lbl_tempo,
                lbl_diff
            ) in self.linhas_voltas:

                lbl_tempo.config(
                    text="--"
                )

                lbl_diff.config(
                    text="",
                    fg=self.COR_CINZA
                )

            self.lbl_total_atual.config(
                text=""
            )

            self.lbl_status.config(
                text=""
            )

            return

        # -------------------------------------------------
        # NOME
        # -------------------------------------------------

        self.lbl_piloto_atual.config(
            text=nome,
            fg="#4da3ff"
        )

        # -------------------------------------------------
        # VOLTAS
        # -------------------------------------------------

        for i, (
            lbl_tempo,
            lbl_diff
        ) in enumerate(
            self.linhas_voltas
        ):

            if i < len(voltas):

                lbl_tempo.config(
                    text=f"{voltas[i]:.3f}s"
                )

                diff = (
                    diffs[i]
                    if i < len(diffs)
                    else None
                )

                if diff is None:

                    with lock_registros:
                        tem_registros = bool(
                            registros
                        )

                    texto_ref = (
                        "(definindo referência)"
                        if not tem_registros
                        else ""
                    )

                    lbl_diff.config(
                        text=texto_ref,
                        fg=self.COR_CINZA
                    )

                else:

                    sinal = (
                        "+"
                        if diff >= 0
                        else ""
                    )

                    cor = (
                        self.COR_VERMELHO
                        if diff >= 0
                        else self.COR_VERDE
                    )

                    lbl_diff.config(
                        text=(
                            f"{sinal}"
                            f"{diff:.3f}s "
                            f"vs líder"
                        ),
                        fg=cor
                    )

            else:

                lbl_tempo.config(
                    text="--"
                )

                lbl_diff.config(
                    text="",
                    fg=self.COR_CINZA
                )

        # -------------------------------------------------
        # TOTAL PARCIAL
        # -------------------------------------------------

        if voltas:

            soma_parcial = sum(
                voltas
            )

            self.lbl_total_atual.config(
                text=(
                    f"Parcial: "
                    f"{soma_parcial:.3f}s"
                )
            )

        else:

            self.lbl_total_atual.config(
                text=""
            )

        # -------------------------------------------------
        # STATUS
        # -------------------------------------------------

        if status == "correndo":
            self.lbl_status.config(
                text="● CORRENDO",
                fg=self.COR_VERDE
            )
        elif status == "finalizado":
            self.lbl_status.config(
                text="✓ FINALIZADO",
                fg=self.COR_VERDE
            )

    # =====================================================
    # PAINEL DIREITO
    # =====================================================

    def _atualizar_painel_direito(self):

        with lock_registros:

            copia = list(
                registros
            )

        ordenados = sorted(
            copia,
            key=lambda r: r["total"]
        )

        # -------------------------------------------------
        # TOP 3
        # -------------------------------------------------

        for i in range(3):

            if i < len(ordenados):

                r = ordenados[i]

                voltas_str = "  ".join(
                    f"V{j + 1}:{v:.2f}s"
                    for j, v in enumerate(
                        r["voltas"]
                    )
                )

                texto = (
                    f"{r['nome']}\n"
                    f"Total: {r['total']:.3f}s\n"
                    f"{voltas_str}"
                )

                self.labels_podio[i].config(
                    text=texto
                )

            else:

                self.labels_podio[i].config(
                    text="— aguardando —"
                )

        # -------------------------------------------------
        # DEMAIS COLOCAÇÕES
        # -------------------------------------------------

        self.texto_demais.config(
            state="normal"
        )

        self.texto_demais.delete(
            "1.0",
            tk.END
        )

        if len(ordenados) > 3:

            for i, r in enumerate(
                ordenados[3:],
                start=4
            ):

                self.texto_demais.insert(
                    tk.END,

                    (
                        f"{i}º  "
                        f"{r['nome']}  —  "
                        f"{r['total']:.3f}s\n"
                    )
                )

        self.texto_demais.config(
            state="disabled"
        )

    # =====================================================
    # ATUALIZAÇÃO PERIÓDICA
    # =====================================================

    def atualizar(self):

        if encerrar_app:

            try:
                self.root.destroy()

            except tk.TclError:
                pass

            return

        self._atualizar_painel_esquerdo()

        self._atualizar_painel_direito()

        self.root.after(
            400,
            self.atualizar
        )

    # =====================================================
    # INICIAR
    # =====================================================

    def iniciar(self):

        self.root.mainloop()


# =============================================================


# ======================= MAIN =======================

def main():

    global parar_thread

    ser = None

    # =========================================================
    # MODO DE TESTE
    # =========================================================

    if MODO_TESTE:

        print(
            "=== MODO DE TESTE ATIVADO "
            "(sem ESP32) ==="
        )

        print(
            "As voltas serão simuladas "
            "individualmente.\n"
        )

        threading.Thread(
            target=gerador_dados_teste,
            daemon=True
        ).start()

    # =========================================================
    # BLUETOOTH REAL
    # =========================================================

    else:

        print(
            f"Conectando à porta Bluetooth "
            f"{PORTA_BLUETOOTH}..."
        )

        try:

            ser = serial.Serial(
                PORTA_BLUETOOTH,
                BAUD_RATE,
                timeout=1
            )

        except Exception as e:

            print(
                f"\nNão foi possível abrir "
                f"a porta {PORTA_BLUETOOTH}:"
            )

            print(
                f"{e}\n"
            )

            print(
                "Verifique se:"
            )

            print(
                "1. O ESP32 está ligado."
            )

            print(
                "2. O ESP32 está pareado "
                "com o computador."
            )

            print(
                "3. A porta serial Bluetooth "
                "está correta."
            )

            return

        print(
            "Bluetooth conectado!"
        )

        time.sleep(2)

        threading.Thread(
            target=leitor_serial,
            args=(ser,),
            daemon=True
        ).start()

    # =========================================================
    # THREAD DA LÓGICA
    # =========================================================

    threading.Thread(
        target=logica_console,
        args=(ser,),
        daemon=True
    ).start()

    # =========================================================
    # INTERFACE
    # =========================================================

    janela = JanelaPrincipal()

    janela.iniciar()

    # =========================================================
    # ENCERRAMENTO
    # =========================================================

    parar_thread = True
    evento_proximo_corredor.set()

    if ser is not None:

        try:
            ser.close()

        except Exception:
            pass

    print(
        "Programa encerrado."
    )


# =============================================================


if __name__ == "__main__":

    main()