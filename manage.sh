#!/usr/bin/env bash
# Bale Leave Management Bot - Manager (install / update / uninstall / config)
# Terminal messages are intentionally in English so mixed RTL text does not
# break in Termux and many SSH clients. The bot itself speaks Persian inside Bale.
#
# Usage:
#   bash manage.sh
#   or:  curl -fsSL https://raw.githubusercontent.com/silent4time/bale-leave-bot/main/manage.sh | bash
set -e

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_URL="${REPO_URL:-https://github.com/silent4time/bale-leave-bot.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/home/bale_leave_bot}"
SERVICE_NAME="bale-leave-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VERSION_FILE="VERSION"

# Colors (safe fallback if terminal has no color)
if [ -t 1 ]; then
  C_RESET='\033[0m'
  C_GOLD='\033[1;33m'
  C_TEAL='\033[1;36m'
  C_RED='\033[1;31m'
  C_GREEN='\033[1;32m'
  C_DIM='\033[2m'
else
  C_RESET= C_GOLD= C_TEAL= C_RED= C_GREEN= C_DIM=
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo -e "${C_TEAL}[INFO]${C_RESET} $*"; }
ok()    { echo -e "${C_GREEN}[OK]${C_RESET} $*"; }
warn()  { echo -e "${C_GOLD}[WARN]${C_RESET} $*"; }
err()   { echo -e "${C_RED}[ERROR]${C_RESET} $*"; }

pause() {
  echo ""
  read -rp "Press Enter to continue..." _
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# Progress bar / status
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Progress bar / status  (ASCII so every terminal shows it)
# ---------------------------------------------------------------------------
progress_bar() {
  local pct="${1:-0}"
  local msg="${2:-}"
  local width=28
  [ "$pct" -gt 100 ] 2>/dev/null && pct=100
  [ "$pct" -lt 0 ] 2>/dev/null && pct=0
  local filled=$(( pct * width / 100 ))
  local empty=$(( width - filled ))
  local bar=""
  local i
  for ((i=0; i<filled; i++)); do bar="${bar}="; done
  for ((i=0; i<empty; i++)); do bar="${bar} "; done
  # pad message
  printf "\r${C_TEAL}[%s] %3d%%${C_RESET} %-40s" "$bar" "$pct" "$msg"
  if [ "$pct" -ge 100 ]; then
    printf "\n"
  fi
}

step_banner() {
  # step_banner N TOTAL "Title"
  local n="$1" total="$2" title="$3"
  local pct=$(( n * 100 / total ))
  echo ""
  echo -e "${C_GOLD}==> Step ${n}/${total}: ${title}${C_RESET}"
  progress_bar "$pct" "$title"
}

run_with_spinner() {
  local label="$1"; shift
  local log
  log="$(mktemp 2>/dev/null || echo /tmp/bale-bot-spin.log)"
  "$@" >"$log" 2>&1 &
  local pid=$!
  local spin='|/-\'
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    printf "\r${C_TEAL}[%c]${C_RESET} %s...   " "${spin:$i:1}" "$label"
    sleep 0.12
  done
  wait "$pid"
  local rc=$?
  if [ $rc -eq 0 ]; then
    printf "\r${C_GREEN}[OK]${C_RESET} %s                \n" "$label"
  else
    printf "\r${C_RED}[FAIL]${C_RESET} %s              \n" "$label"
    echo "---- log (last 25 lines) ----"
    tail -n 25 "$log" 2>/dev/null || true
  fi
  rm -f "$log"
  return $rc
}

ensure_python() {
  if have_cmd python3; then
    return 0
  fi
  info "python3 not found; trying to install..."
  if have_cmd pkg; then
    pkg update -y && pkg install -y python
  elif have_cmd apt-get; then
    sudo apt-get update -y && sudo apt-get install -y python3 python3-venv python3-pip
  elif have_cmd dnf; then
    sudo dnf install -y python3 python3-pip
  else
    err "Cannot install python3 automatically. Please install it and re-run."
    return 1
  fi
}

ensure_git() {
  if have_cmd git; then
    return 0
  fi
  info "git not found; trying to install..."
  if have_cmd pkg; then
    pkg install -y git
  elif have_cmd apt-get; then
    sudo apt-get update -y && sudo apt-get install -y git
  elif have_cmd dnf; then
    sudo dnf install -y git
  else
    err "git is required for install/update. Please install git."
    return 1
  fi
}

in_bot_dir() {
  [ -f "bot.py" ] && [ -f "database.py" ] && [ -f "requirements.txt" ]
}

find_install_dir() {
  if in_bot_dir; then
    pwd
    return
  fi
  if [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/bot.py" ]; then
    echo "$INSTALL_DIR"
    return
  fi
  echo ""
}

activate_venv() {
  local dir="$1"
  USE_VENV=0
  if [ -d "$dir/venv" ]; then
    # shellcheck disable=SC1091
    source "$dir/venv/bin/activate"
    USE_VENV=1
  fi
}

python_bin() {
  local dir="$1"
  if [ -x "$dir/venv/bin/python3" ]; then
    echo "$dir/venv/bin/python3"
  else
    command -v python3
  fi
}

service_running() {
  have_cmd systemctl && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null
}

stop_service() {
  if service_running; then
    info "Stopping service $SERVICE_NAME ..."
    sudo systemctl stop "$SERVICE_NAME" || true
  fi
}

restart_service() {
  if [ -f "$SERVICE_FILE" ]; then
    info "Restarting service $SERVICE_NAME ..."
    sudo systemctl daemon-reload
    sudo systemctl restart "$SERVICE_NAME"
    ok "Service restarted."
    echo "  Status: sudo systemctl status $SERVICE_NAME"
    echo "  Logs:   sudo journalctl -u $SERVICE_NAME -f"
  fi
}

# ---------------------------------------------------------------------------
# 1) Install
# ---------------------------------------------------------------------------
do_install() {
  echo ""
  echo "=============================================="
  echo "  Install"
  echo "=============================================="

  ensure_python || return 1
  ensure_git || return 1

  local target
  if in_bot_dir; then
    target="$(pwd)"
    info "Already inside bot folder: $target"
  else
    target="$INSTALL_DIR"
    if [ -d "$target/.git" ]; then
      warn "Folder already exists: $target"
      read -rp "Use existing folder and continue install? (y/n) " ANS
      if [[ "$ANS" != "y" && "$ANS" != "Y" ]]; then
        info "Cancelled."
        return 0
      fi
    else
      info "Downloading project files into $target ..."
      step_banner 1 5 "Download from GitHub"
      progress_bar 10 "Starting git clone..."
      if GIT_TERMINAL_PROMPT=0 git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$target"; then
        progress_bar 100 "Download complete"
        ok "Repository cloned to $target"
      else
        err "git clone failed"
        return 1
      fi
    fi
  fi

  cd "$target" || { err "Cannot enter $target"; return 1; }
  ok "Working directory: $(pwd)"
  info "All next steps run inside this folder."

  # venv
  step_banner 2 5 "Python virtualenv"
  if [ ! -d "venv" ]; then
    progress_bar 30 "Creating venv..."
    if ! python3 -m venv venv 2>/dev/null; then
      warn "Could not create venv (common on Termux). Continuing without it."
    else
      progress_bar 50 "venv created"
    fi
  else
    progress_bar 50 "venv already exists"
  fi
  activate_venv "$target"
  progress_bar 100 "Virtualenv ready"

  step_banner 3 5 "Install Python packages"
  progress_bar 10 "Upgrading pip..."
  run_with_spinner "Upgrading pip" pip install --upgrade pip || true
  progress_bar 40 "Installing requirements (this may take a minute)..."
  if run_with_spinner "Installing requirements" pip install -r requirements.txt; then
    progress_bar 100 "Dependencies installed"
  else
    err "pip install failed"
    return 1
  fi

  # .env / token
  step_banner 4 5 "Bot token (.env)"
  if [ ! -f ".env" ]; then
    echo ""
    read -rp "Enter your Bale bot token (from BotFather): " TOKEN
    if [ -z "$TOKEN" ]; then
      err "Token is required."
      return 1
    fi
    echo "BALE_BOT_TOKEN=$TOKEN" > .env
    ok ".env created."
  else
    info ".env already exists; keeping it."
  fi

  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  if [ -z "$BALE_BOT_TOKEN" ]; then
    err "BALE_BOT_TOKEN is empty. Use menu option 4 to set it."
    return 1
  fi

  # systemd (optional)
  step_banner 5 5 "Service / finish"
  if have_cmd systemctl; then
    echo ""
    read -rp "Create systemd service for 24/7 auto-restart? (y/n) " ANS
    if [[ "$ANS" == "y" || "$ANS" == "Y" ]]; then
      local PY
      PY="$(python_bin "$target")"
      sudo bash -c "cat > $SERVICE_FILE" <<SERVICEEOF
[Unit]
Description=Bale Leave Management Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$target
EnvironmentFile=$target/.env
ExecStart=$PY $target/bot.py
Restart=always
RestartSec=5
User=$(whoami)

[Install]
WantedBy=multi-user.target
SERVICEEOF
      sudo systemctl daemon-reload
      sudo systemctl enable "$SERVICE_NAME"
      sudo systemctl restart "$SERVICE_NAME"
      ok "Service created and started."
      echo "  Status: sudo systemctl status $SERVICE_NAME"
      echo "  Logs:   sudo journalctl -u $SERVICE_NAME -f"
      echo "  Stop:   sudo systemctl stop $SERVICE_NAME"
      return 0
    fi
  fi

  echo ""
  ok "Install finished."
  echo "To run manually:"
  if [ -d "venv" ]; then
    echo "  cd $target && source venv/bin/activate && python3 bot.py"
  else
    echo "  cd $target && python3 bot.py"
  fi
}

# ---------------------------------------------------------------------------
# 2) Update
# ---------------------------------------------------------------------------
do_update() {
  echo ""
  echo "=============================================="
  echo "  Update"
  echo "=============================================="

  local target
  target="$(find_install_dir)"
  if [ -z "$target" ]; then
    err "Bot not found. Run Install first (or cd into the bot folder)."
    return 1
  fi
  cd "$target"
  info "Updating in: $(pwd)"

  ensure_git || return 1

  if [ ! -d ".git" ]; then
    err "This folder is not a git clone. Cannot auto-update."
    echo "  Re-install from GitHub, or replace files manually."
    return 1
  fi

  stop_service

  info "Pulling latest code from origin/$REPO_BRANCH ..."
  git fetch origin
  git checkout "$REPO_BRANCH"
  git pull --ff-only origin "$REPO_BRANCH" || {
    warn "Fast-forward pull failed. You may have local changes."
    read -rp "Force reset to origin/$REPO_BRANCH? (y/n) " ANS
    if [[ "$ANS" == "y" || "$ANS" == "Y" ]]; then
      git reset --hard "origin/$REPO_BRANCH"
    else
      err "Update cancelled."
      return 1
    fi
  }

  activate_venv "$target"
  info "Updating Python dependencies..."
  progress_bar 20 "Upgrading pip..."
  run_with_spinner "Upgrading pip" pip install --upgrade pip || true
  progress_bar 50 "Updating requirements..."
  if run_with_spinner "Updating requirements" pip install -r requirements.txt; then
    progress_bar 100 "Dependencies updated"
  else
    err "pip install failed"
    return 1
  fi

  if [ -f "$VERSION_FILE" ]; then
    ok "Updated to version: $(cat "$VERSION_FILE")"
  else
    ok "Code updated."
  fi

  restart_service

  echo ""
  echo "If you run the bot manually, start it again with:"
  echo "  python3 bot.py"
}

# ---------------------------------------------------------------------------
# 3) Uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
  echo ""
  echo "=============================================="
  echo "  Uninstall"
  echo "=============================================="

  # Always try to remove systemd service
  if have_cmd systemctl && [ -f "$SERVICE_FILE" ]; then
    info "Stopping and removing systemd service..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload
    ok "systemd service removed."
  else
    info "No systemd service found."
  fi

  local target
  target="$(find_install_dir)"
  if [ -z "$target" ]; then
    info "Bot folder not found; only service (if any) was cleaned."
    return 0
  fi

  echo ""
  info "Install folder: $target"
  cd "$target"

  # Backup DB?
  local DB_FOUND=""
  for f in leave_bot.db *.db; do
    if [ -f "$f" ]; then
      DB_FOUND="$f"
      break
    fi
  done
  if [ -n "$DB_FOUND" ]; then
    read -rp "Back up database ($DB_FOUND) to \$HOME? (y/n) " ANS
    if [[ "$ANS" == "y" || "$ANS" == "Y" ]]; then
      local BACKUP="$HOME/${DB_FOUND}.backup.$(date +%Y%m%d_%H%M%S)"
      cp "$DB_FOUND" "$BACKUP"
      ok "Backup saved: $BACKUP"
    fi
  fi

  echo ""
  echo "Choose:"
  echo "  1) Only remove service (already done) — keep code + database"
  echo "  2) Remove venv and caches only"
  echo "  3) Delete entire install folder (code + database)"
  read -rp "Choice (1/2/3): " OPT

  case "$OPT" in
    2)
      rm -rf venv __pycache__
      find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
      ok "venv and caches removed."
      ;;
    3)
      read -rp "Type exactly DELETE to permanently remove '$target': " CONFIRM
      if [ "$CONFIRM" = "DELETE" ]; then
        cd ..
        rm -rf "$target"
        ok "Install folder removed."
      else
        info "Cancelled; nothing deleted."
      fi
      ;;
    *)
      ok "Service handled; files left untouched."
      ;;
  esac
}

# ---------------------------------------------------------------------------
# 4) Edit inputs / settings
# ---------------------------------------------------------------------------
do_edit_config() {
  echo ""
  echo "=============================================="
  echo "  Edit settings"
  echo "=============================================="

  local target
  target="$(find_install_dir)"
  if [ -z "$target" ]; then
    err "Bot not found. Run Install first."
    return 1
  fi
  cd "$target"

  if [ ! -f ".env" ]; then
    echo "BALE_BOT_TOKEN=" > .env
    info "Created empty .env"
  fi

  echo ""
  echo "Current .env:"
  echo "----------------------------------------"
  cat .env
  echo "----------------------------------------"
  echo ""
  echo "  1) Change BALE_BOT_TOKEN"
  echo "  2) Change / set DB_PATH"
  echo "  3) Open .env in editor (nano/vi)"
  echo "  0) Back"
  read -rp "Choice: " C

  case "$C" in
    1)
      read -rp "New Bale bot token: " TOKEN
      if [ -z "$TOKEN" ]; then
        err "Empty token ignored."
        return 0
      fi
      if grep -q '^BALE_BOT_TOKEN=' .env 2>/dev/null; then
        # portable sed
        if sed --version >/dev/null 2>&1; then
          sed -i "s|^BALE_BOT_TOKEN=.*|BALE_BOT_TOKEN=$TOKEN|" .env
        else
          sed -i '' "s|^BALE_BOT_TOKEN=.*|BALE_BOT_TOKEN=$TOKEN|" .env
        fi
      else
        echo "BALE_BOT_TOKEN=$TOKEN" >> .env
      fi
      ok "Token updated."
      if [ -f "$SERVICE_FILE" ]; then
        read -rp "Restart systemd service now? (y/n) " ANS
        if [[ "$ANS" == "y" || "$ANS" == "Y" ]]; then
          restart_service
        fi
      fi
      ;;
    2)
      read -rp "DB_PATH (empty = default leave_bot.db next to bot.py): " DBP
      if grep -q '^DB_PATH=' .env 2>/dev/null; then
        if [ -z "$DBP" ]; then
          if sed --version >/dev/null 2>&1; then
            sed -i '/^DB_PATH=/d' .env
          else
            sed -i '' '/^DB_PATH=/d' .env
          fi
          ok "DB_PATH removed (using default)."
        else
          if sed --version >/dev/null 2>&1; then
            sed -i "s|^DB_PATH=.*|DB_PATH=$DBP|" .env
          else
            sed -i '' "s|^DB_PATH=.*|DB_PATH=$DBP|" .env
          fi
          ok "DB_PATH updated."
        fi
      else
        if [ -n "$DBP" ]; then
          echo "DB_PATH=$DBP" >> .env
          ok "DB_PATH set."
        fi
      fi
      ;;
    3)
      if have_cmd nano; then
        nano .env
      elif have_cmd vi; then
        vi .env
      else
        err "No nano/vi found. Edit .env manually."
      fi
      ;;
    *)
      return 0
      ;;
  esac
}

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
show_banner() {
  clear 2>/dev/null || true
  local ver="?"
  local target
  target="$(find_install_dir)"
  if [ -n "$target" ] && [ -f "$target/$VERSION_FILE" ]; then
    ver="$(cat "$target/$VERSION_FILE")"
  elif [ -f "$VERSION_FILE" ]; then
    ver="$(cat "$VERSION_FILE")"
  fi
  echo -e "${C_GOLD}"
  printf '  +------------------------------------------+\n'
  printf '  |  Bale Leave Management Bot — Manager     |\n'
  printf '  |  version %-31s |\n' "$ver"
  printf '  +------------------------------------------+\n'
  echo -e "${C_RESET}"
  if [ -n "$target" ]; then
    echo -e "  Install path: ${C_DIM}$target${C_RESET}"
    if service_running; then
      echo -e "  Service:      ${C_GREEN}running${C_RESET}"
    elif [ -f "$SERVICE_FILE" ]; then
      echo -e "  Service:      ${C_GOLD}installed (stopped)${C_RESET}"
    else
      echo -e "  Service:      ${C_DIM}not installed${C_RESET}"
    fi
  else
    echo -e "  Status:       ${C_DIM}not installed yet${C_RESET}"
  fi
  echo ""
}

main_menu() {
  while true; do
    show_banner
    echo "  1) Install"
    echo "  2) Update"
    echo "  3) Uninstall"
    echo "  4) Edit settings (token / DB path)"
    echo "  0) Exit"
    echo ""
    read -rp "  Select [0-4]: " CHOICE
    case "$CHOICE" in
      1) do_install; pause ;;
      2) do_update; pause ;;
      3) do_uninstall; pause ;;
      4) do_edit_config; pause ;;
      0|q|Q) echo "Bye."; exit 0 ;;
      *) warn "Invalid choice."; sleep 1 ;;
    esac
  done
}

# If script is piped (curl | bash) and not interactive, default to install
if [ ! -t 0 ] && [ "${FORCE_MENU:-}" != "1" ]; then
  # Non-interactive: only install makes sense
  do_install
else
  main_menu
fi
