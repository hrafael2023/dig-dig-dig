#!/usr/bin/env python3
"""Servidor local do Dig Dig Dig!: mostra o relatório em http://127.0.0.1:8777
e roda o buscador quando o botão "Dig" da página é apertado.

Atende só o próprio Mac (127.0.0.1) e só entrega o relatório, nunca os
arquivos privados da pasta."""
import datetime as dt
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA = Path(__file__).resolve().parent
LOG = PASTA / "logs" / "buscador.log"
PORTA = 8777

estado = {"rodando": False, "inicio": None, "fim": None, "ok": None}
trava = threading.Lock()


def cavar():
    """Roda buscar.py até o fim, anotando o resultado em `estado`."""
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n--- Dig em {dt.datetime.now():%d/%m/%Y %H:%M:%S} ---\n")
        log.flush()
        r = subprocess.run([sys.executable, str(PASTA / "buscar.py")], cwd=PASTA,
                           stdout=log, stderr=subprocess.STDOUT)
    with trava:
        estado.update(rodando=False, fim=dt.datetime.now().isoformat(timespec="seconds"),
                      ok=r.returncode == 0)


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
        if caminho in ("/", "/index.html"):
            return self.responder(200, (PASTA / "index.html").read_bytes(), "text/html; charset=utf-8")
        self.responder(404, b"{}")

    def do_POST(self):
        # O cabeçalho próprio obriga outros sites a pedir permissão (CORS) antes,
        # o que este servidor nunca concede; só a própria página consegue disparar.
        if self.path != "/dig" or self.headers.get("X-Dig") != "1":
            return self.responder(403, b"{}")
        with trava:
            if not estado["rodando"]:
                estado.update(rodando=True, inicio=dt.datetime.now().isoformat(timespec="seconds"),
                              fim=None, ok=None)
                threading.Thread(target=cavar, daemon=True).start()
            self.responder(202, json.dumps(estado).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Dig Dig Dig! em http://127.0.0.1:{PORTA}", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORTA), Pedido).serve_forever()
