#!/usr/bin/env bash
# Bale Leave Management Bot - installer (with progress bar)
# Run from inside the folder that contains bot.py:
#   cd /home/bale_leave_bot
#   bash install.sh
set -e

if [ -t 1 ]; then
  C_RESET='\033[0m'; C_GOLD='\033[1;33m'; C_TEAL='\033[1;36m'
  C_RED='\033[1;31m'; C_GREEN='\033[1;32m'
else
  C_RESET= C_GOLD= C_TEAL= C_RED= C_GREEN=
fi

progress_bar() {
  local pct="${1:-0}" msg="${2:-}" width=28
  [ "$pct" -gt 100 ] 2>/dev/null && pct=100
  [ "$pct" -lt 0 ] 2>/dev/null && pct=0
  local filled=$(( pct * width / 100 )) empty=$(( width - filled )) bar="" i
  for ((i=0; i<filled; i++)); do bar="${bar}="; done
  for ((i=0; i<empty; i++)); do bar="${bar} "; done
  printf "\r${C_TEAL}[%s] %3d%%${C_RESET} %-40s" "$bar" "$pct" "$msg"
  [ "$pct" -ge 100 ] && printf "\n"
}

step_banner() {
  local n="$1" total="$2" title="$3"
  echo ""
  echo -e "${C_GOLD}==> Step ${n}/${total}: ${title}${C_RESET}"
  progress_bar $(( n * 100 / total )) "$title"
}

run_with_spinner() {
  local label="$1"; shift
  local log; log="$(mktemp 2>/dev/null || echo /tmp/bale-install.log)"
  "$@" >"$log" 2>&1 &
  local pid=$! spin='|/-\' i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    printf "\r${C_TEAL}[%c]${C_RESET} %s...   " "${spin:$i:1}" "$label"
    sleep 0.12
  done
  wait "$pid"; local rc=$?
  if [ $rc -eq 0 ]; then
    printf "\r${C_GREEN}[OK]${C_RESET} %s                \n" "$label"
  else
    printf "\r${C_RED}[FAIL]${C_RESET} %s              \n" "$label"
    tail -n 25 "$log" 2>/dev/null || true
  fi
  rm -f "$log"
  return $rc
}

printf '+------------------------------------------+\n'
printf '|  Bale Leave Management Bot - Installer   |\n'
ver="?"
[ -f VERSION ] && ver=$(cat VERSION)
printf '|  version %-31s |\n' "$ver"
printf '+------------------------------------------+\n'

if [ ! -f "bot.py" ]; then
  echo -e "${C_RED}[ERROR]${C_RESET} Run this script from inside the folder that contains bot.py."
  echo "  Example:"
  echo "    cd /home/bale_leave_bot"
  echo "    bash install.sh"
  exit 1
fi

echo "Working directory: $(pwd)"

# 1) Python
step_banner 1 5 "Check / install Python 3"
if ! command -v python3 >/dev/null 2>&1; then
  progress_bar 20 "python3 not found; installing..."
  if command -v pkg >/dev/null 2>&1; then
    pkg update -y && pkg install -y python
  elif command -v apt >/dev/null 2>&1; then
    sudo apt update && sudo apt install -y python3 python3-venv python3-pip
  else
    echo -e "${C_RED}[ERROR]${C_RESET} Cannot install python3 automatically."
    exit 1
  fi
fi
progress_bar 100 "Python ready: $(command -v python3)"

# 2) venv
step_banner 2 5 "Virtual environment"
USE_VENV=1
if [ ! -d "venv" ]; then
  progress_bar 30 "Creating venv..."
  if ! python3 -m venv venv 2>/dev/null; then
    echo -e "${C_GOLD}[WARN]${C_RESET} Could not create venv; continuing without it."
    USE_VENV=0
  fi
fi
if [ "$USE_VENV" = "1" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi
progress_bar 100 "venv ready"

# 3) deps
step_banner 3 5 "Install packages"
echo ""
echo "==> Dependencies"
if run_with_spinner "Upgrading pip" pip install --upgrade pip; then
  echo "[OK] pip upgraded"
else
  echo "[WARN] pip upgrade skipped"
fi
if run_with_spinner "Installing requirements" pip install -r requirements.txt; then
  progress_bar 100 "Dependencies installed"
else
  echo -e "${C_RED}[ERROR]${C_RESET} pip install failed"
  exit 1
fi

# 4) token
step_banner 4 5 "Bot token"
if [ ! -f ".env" ]; then
  read -rp "Enter your Bale bot token (from BotFather): " TOKEN
  echo "BALE_BOT_TOKEN=$TOKEN" > .env
  echo -e "${C_GREEN}[OK]${C_RESET} .env file created."
else
  echo -e "${C_TEAL}[INFO]${C_RESET} .env already exists; using it."
fi
set -a
# shellcheck disable=SC1091
source .env
set +a
if [ -z "$BALE_BOT_TOKEN" ]; then
  echo -e "${C_RED}[ERROR]${C_RESET} BALE_BOT_TOKEN is empty."
  exit 1
fi
progress_bar 100 "Token configured"

# 5) systemd
step_banner 5 5 "Service / finish"
if command -v systemctl >/dev/null 2>&1; then
  read -rp "Create a systemd service for 24/7 auto-restart on reboot? (y/n) " ANSWER
  if [[ "$ANSWER" == "y" || "$ANSWER" == "Y" ]]; then
    APP_DIR="$(pwd)"
    SERVICE_FILE="/etc/systemd/system/bale-leave-bot.service"
    PYTHON_BIN="$APP_DIR/venv/bin/python3"
    if [ "$USE_VENV" != "1" ]; then
      PYTHON_BIN="$(command -v python3)"
    fi
    sudo bash -c "cat > $SERVICE_FILE" <<SERVICEEOF
[Unit]
Description=Bale Leave Management Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$PYTHON_BIN $APP_DIR/bot.py
Restart=always
RestartSec=5
User=$(whoami)

[Install]
WantedBy=multi-user.target
SERVICEEOF
    sudo systemctl daemon-reload
    sudo systemctl enable bale-leave-bot
    sudo systemctl restart bale-leave-bot
    progress_bar 100 "Service running"
    echo ""
    echo -e "${C_GREEN}[OK]${C_RESET} Service created and started."
    echo "  Status:  sudo systemctl status bale-leave-bot"
    echo "  Logs:    sudo journalctl -u bale-leave-bot -f"
    echo "  Stop:    sudo systemctl stop bale-leave-bot"
    exit 0
  fi
fi

progress_bar 100 "Install finished"
echo ""
echo "To run it manually:"
if [ "$USE_VENV" = "1" ]; then
  echo "  cd $(pwd)"
  echo "  source venv/bin/activate"
fi
echo "  python3 bot.py"
