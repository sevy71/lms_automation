#!/bin/bash
# Start WhatsApp Service - Runs continuously in background

echo "🚀 Starting LMS WhatsApp Service..."

# Check if already running
if pgrep -f "whatsapp_service.py" > /dev/null; then
    echo "⚠️  WhatsApp service is already running!"
    echo "   Use 'bash stop_whatsapp_service.sh' to stop it first"
    exit 1
fi

# Start the service in background
python3 whatsapp_service.py &
PID=$!

echo "✅ WhatsApp service started successfully!"
echo "📊 Process ID: $PID"
echo "📱 Service will automatically process messages from Railway"
echo "🔍 Check logs: tail -f whatsapp_service.log"
echo "🛑 To stop: bash stop_whatsapp_service.sh"

# Save PID for stopping later
echo $PID > whatsapp_service.pid