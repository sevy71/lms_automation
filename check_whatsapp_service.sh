#!/bin/bash
# Check WhatsApp Service Status

echo "🔍 LMS WhatsApp Service Status"
echo "=============================="

# Check if running
PID=$(pgrep -f "whatsapp_service.py")
if [ ! -z "$PID" ]; then
    echo "✅ Service is RUNNING (PID: $PID)"
    
    # Show uptime
    STARTED=$(ps -o lstart= -p $PID)
    echo "🕐 Started: $STARTED"
    
    # Show memory usage
    MEM=$(ps -o rss= -p $PID)
    echo "💾 Memory: ${MEM}KB"
    
    # Check if Chrome is running
    CHROME_PID=$(pgrep -f "Chrome")
    if [ ! -z "$CHROME_PID" ]; then
        echo "🌐 Chrome: Running"
    else
        echo "🌐 Chrome: Not running"
    fi
    
else
    echo "❌ Service is NOT running"
fi

echo ""
echo "🔧 Quick Actions:"
echo "  Start:  bash start_whatsapp_service.sh"
echo "  Stop:   bash stop_whatsapp_service.sh"
echo "  Logs:   tail -f whatsapp_service.log"