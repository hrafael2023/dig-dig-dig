# Dig Dig Dig!

Buscador de discos de vinil. Quando o botão **Dig** é apertado, ele visita os sites de
[`urls.txt`](urls.txt), guarda o que já viu em `vistos.json` e publica só os
anúncios novos em **https://hrafael2023.github.io/dig-dig-dig/**, com um aviso
no celular pelo ntfy.

- `buscar.py` — o buscador (`python3 buscar.py`)
- `arquivo.html` + `relatorios/` — o Arquivo: cada Dig vira uma página em
  `relatorios/vinil-AAAA-MM-DD-HHMM.html`, listada em `arquivo.html`
- `urls.txt` / `termos.txt` — sites e termos de busca
- `modelo-relatorio.html` — visual do relatório
- `servidor.py` — roda no Mac e dispara a busca quando o botão **Dig** é
  apertado: na página local (http://127.0.0.1:8777) ou na publicada, que chama
  o Mac pelo tópico ntfy `gatilho=` de `publicacao.txt` (no máximo uma busca a
  cada 10 minutos por esse caminho). O Mac precisa estar ligado.
- `instalar-servidor.sh` — deixa o servidor sempre ligado no macOS
- `credenciais.exemplo.txt`, `notificacao.exemplo.txt` — modelos dos arquivos
  privados (os reais ficam fora do git)
