#!/bin/zsh
# Agenda o buscador para rodar todo dia às 8h (LaunchAgent do macOS).
# Se o Mac estiver dormindo às 8h, roda assim que acordar.
# Para remover: ./instalar-agendamento.sh --remover
set -e
PASTA="${0:A:h}"
ROTULO="org.projetoninho.buscador-vinil"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"

launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
if [[ "$1" == "--remover" ]]; then
  rm -f "$PLIST"; echo "Agendamento removido."; exit 0
fi

mkdir -p "$PASTA/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key><array>
    <string>$(command -v python3)</string><string>$PASTA/buscar.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PASTA</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>$PASTA/logs/buscador.log</string>
  <key>StandardErrorPath</key><string>$PASTA/logs/buscador.log</string>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Agendado: todo dia às 8h → $PASTA/relatorios/"
