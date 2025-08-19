#!/bin/bash
# Stop WhatsApp Service

echo "🛑 Stopping LMS WhatsApp Service..."

# Check if PID file exists
if [ -f "whatsapp_service.pid" ]; then
    PID=$(cat whatsapp_service.pid)
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "✅ Service stopped (PID: $PID)"
        rm -f whatsapp_service.pid
    else
        echo "⚠️  Process not running (stale PID file removed)"
        rm -f whatsapp_service.pid
    fi
else
    # Try to find and kill by process name
    PID=$(pgrep -f "whatsapp_service.py")
    if [ ! -z "$PID" ]; then
        kill $PID
        echo "✅ Service stopped (PID: $PID)"
    else
        echo "ℹ️  Service is not running"
    fi
fi