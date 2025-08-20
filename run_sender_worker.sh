#!/bin/zsh
set -euxo pipefail
cd "$(dirname "$0")"

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

echo "🚀 Starting enhanced worker with $PYTHON"
exec "$PYTHON" lms_automation/sender_worker_enhanced.py
