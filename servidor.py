#!/usr/bin/env python3
"""Servidor local do Dig Dig Dig!: mostra o relatório em http://127.0.0.1:8777
e roda o buscador quando o botão "Dig" é apertado — na página local ou na
página publicada, que avisa o Mac por um tópico do ntfy (gatilho= em
publicacao.txt).

Atende só o próprio Mac (127.0.0.1) e só entrega o relatório, nunca os
arquivos privados da pasta."""
import datetime as dt
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
LOG = PASTA / "logs" / "buscador.log"
ULTIMO = PASTA / "logs" / "gatilho-ultimo.txt"  # último aviso do ntfy já lido
PORTA = 8777
NTFY = "https://ntfy.sh"
INTERVALO = dt.timedelta(minutes=10)  # mínimo entre buscas pedidas pela página pública

estado = {"rodando": False, "inicio": None, "fim": None, "ok": None}
trava = threading.Lock()


def ler_gatilho():
    for linha in (PASTA / "publicacao.txt").read_text(encoding="utf-8").splitlines():
        chave, _, valor = linha.partition("=")
        if chave.strip() == "gatilho":
            return valor.strip()
    return ""


def avisar(texto):
    """Conta à página publicada como está a busca, pelo mesmo tópico do gatilho."""
    topico = ler_gatilho()
    if not topico:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{NTFY}/{urllib.parse.quote(topico)}", data=texto.encode(), method="POST"), timeout=20).close()
    except Exception as e:
        print(f"falha ao avisar o ntfy: {e}", file=sys.stderr)


def cavar():
    """Roda buscar.py até o fim, anotando o resultado em `estado`."""
    avisar("inicio")
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n--- Dig em {dt.datetime.now():%d/%m/%Y %H:%M:%S} ---\n")
        log.flush()
        r = subprocess.run([sys.executable, str(PASTA / "buscar.py")], cwd=PASTA,
                           stdout=log, stderr=subprocess.STDOUT)
    with trava:
        estado.update(rodando=False, fim=dt.datetime.now().isoformat(timespec="seconds"),
                      ok=r.returncode == 0)
    avisar("fim:ok" if r.returncode == 0 else "fim:falha")


def pedir_dig(remoto):
    """Começa uma busca, se nenhuma estiver rodando. Pedidos da página pública
    (remoto) respeitam o intervalo mínimo; os da página local, não."""
    with trava:
        if estado["rodando"]:
            return
        if remoto and estado["inicio"] and dt.datetime.now() - dt.datetime.fromisoformat(estado["inicio"]) < INTERVALO:
            threading.Thread(target=avisar, args=("recente",), daemon=True).start()
            return
        estado.update(rodando=True, inicio=dt.datetime.now().isoformat(timespec="seconds"),
                      fim=None, ok=None)
        threading.Thread(target=cavar, daemon=True).start()


def escutar():
    """Fica ligado no tópico do gatilho. Se a conexão cair (Mac dormindo, rede),
    reconecta e recebe também os pedidos feitos enquanto estava fora."""
    while True:
        topico = ler_gatilho()
        if not topico:
            time.sleep(60)
            continue
        desde = ULTIMO.read_text().strip() if ULTIMO.exists() else str(int(time.time()))
        try:
            url = f"{NTFY}/{urllib.parse.quote(topico)}/json?since={urllib.parse.quote(desde)}"
            # o ntfy manda um "keepalive" a cada ~45 s; sem nada em 2 min, a conexão morreu
            with urllib.request.urlopen(url, timeout=120) as resposta:
                for linha in resposta:
                    msg = json.loads(linha)
                    if msg.get("event") != "message":
                        continue
                    ULTIMO.parent.mkdir(exist_ok=True)
                    ULTIMO.write_text(msg["id"])
                    if msg.get("message", "").strip().lower() == "dig":
                        print(f"Dig pedido pela página publicada ({dt.datetime.now():%d/%m %H:%M})", file=sys.stderr)
                        pedir_dig(remoto=True)
        except Exception as e:
            print(f"ntfy: {e}; reconectando", file=sys.stderr)
            time.sleep(15)


class Pedido(BaseHTTPRequestHandler):
    def responder(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        caminho = self.path.split("?")[0]
        if caminho == "/estado":
            with trava:
                return self.responder(200, json.dumps(estado).encode())
        # só as páginas do relatório: a atual, o Arquivo e os Digs arquivados
        if caminho in ("/", "/index.html"):
            caminho = "/index.html"
        if caminho == "/index.html" or caminho == "/arquivo.html" or re.fullmatch(r"/relatorios/vinil-[\d-]+\.html", caminho):
            pagina = PASTA / caminho.lstrip("/")
            if pagina.exists():
                return self.responder(200, pagina.read_bytes(), "text/html; charset=utf-8")
        if caminho == "/busca.json" and (PASTA / "busca.json").exists():
            return self.responder(200, (PASTA / "busca.json").read_bytes())
        self.responder(404, b"{}")

    def do_POST(self):
        # O cabeçalho próprio obriga outros sites a pedir permissão (CORS) antes,
        # o que este servidor nunca concede; só a própria página consegue disparar.
        if self.path != "/dig" or self.headers.get("X-Dig") != "1":
            return self.responder(403, b"{}")
        pedir_dig(remoto=False)
        with trava:
            self.responder(202, json.dumps(estado).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Dig Dig Dig! em http://127.0.0.1:{PORTA}", file=sys.stderr)
    threading.Thread(target=escutar, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORTA), Pedido).serve_forever()
