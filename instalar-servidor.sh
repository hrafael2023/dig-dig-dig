#!/bin/zsh
# Deixa o servidor do Dig Dig Dig! sempre ligado no Mac (LaunchAgent do macOS):
# ele sobe junto com o login e volta sozinho se cair. A página fica em
# http://127.0.0.1:8777 e a busca só roda quando o botão "Dig" é apertado.
# Para remover: ./instalar-servidor.sh --remover
set -e
PASTA="${0:A:h}"
ROTULO="org.projetoninho.buscador-vinil"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"

launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
if [[ "$1" == "--remover" ]]; then
  rm -f "$PLIST"; echo "Servidor removido."; exit 0
fi

mkdir -p "$PASTA/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<FIM
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key><array>
    <string>$(command -v python3)</string><string>$PASTA/servidor.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PASTA</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$PASTA/logs/servidor.log</string>
  <key>StandardErrorPath</key><string>$PASTA/logs/servidor.log</string>
</dict></plist>
FIM
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Pronto: abra http://127.0.0.1:8777 e aperte Dig."
