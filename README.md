# Dig Dig Dig!

Buscador de discos de vinil. Quando o botão **Dig** é apertado, ele visita os sites de
[`urls.txt`](urls.txt), guarda o que já viu em `vistos.json` e publica só os
anúncios novos em **https://hrafael2023.github.io/dig-dig-dig/**, com um aviso
no celular pelo ntfy.

- `buscar.py` — o buscador (`python3 buscar.py`)
- `urls.txt` / `termos.txt` — sites e termos de busca
- `modelo-relatorio.html` — visual do relatório
- `servidor.py` — página local em http://127.0.0.1:8777 com o botão **Dig**,
  que dispara a busca (só funciona no Mac; no celular a página não tem o botão)
- `instalar-servidor.sh` — deixa o servidor sempre ligado no macOS
- `credenciais.exemplo.txt`, `notificacao.exemplo.txt` — modelos dos arquivos
  privados (os reais ficam fora do git)
