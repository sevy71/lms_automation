#!/bin/zsh
set -euxo pipefail
cd "$(dirname "$0")"

LOCK_FILE="/tmp/lms_manual_sender.lock"

if [ -f "$LOCK_FILE" ]; then
    echo "🔒 Manual sender is already running. Exiting."
    exit 1
fi

trap "rm -f $LOCK_FILE" EXIT
touch "$LOCK_FILE"

VENV_DIR="$PWD/venv"
PYTHON="$VENV_DIR/bin/python"

# Ensure venv exists
if [ ! -x "$PYTHON" ]; then
  echo "⚙️  Creating virtualenv at $VENV_DIR"
  /usr/bin/python3 -m venv "$VENV_DIR"
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -r lms_automation/requirements.txt
fi

# Load env
set -a
[ -f .env ] && source .env
set +a

echo "🚀 Starting manual sender with $PYTHON"
exec "$PYTHON" lms_automation/send_all_queued_messages.py