# Dig Dig Dig!

Buscador diário de discos de vinil. Todo dia às 13h ele visita os sites de
[`urls.txt`](urls.txt), guarda o que já viu em `vistos.json` e publica só os
anúncios novos em **https://hrafael2023.github.io/dig-dig-dig/**, com um aviso
no celular pelo ntfy.

- `buscar.py` — o buscador (`python3 buscar.py`)
- `urls.txt` / `termos.txt` — sites e termos de busca
- `modelo-relatorio.html` — visual do relatório
- `instalar-agendamento.sh` — agenda a execução diária no macOS
- `credenciais.exemplo.txt`, `notificacao.exemplo.txt` — modelos dos arquivos
  privados (os reais ficam fora do git)
