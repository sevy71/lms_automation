# LMS Hybrid WhatsApp System

This document explains how to set up and run the hybrid WhatsApp messaging system for the Last Man Standing application.

## Architecture Overview

The hybrid system consists of two components:

1. **Cloud Application (Railway)**: Main Flask app handling the game logic, web interface, and message queue
2. **Local Worker (Your Computer)**: Selenium-based WhatsApp sender using your personal WhatsApp account

## Why Hybrid?

- **No Business Verification**: Uses your personal WhatsApp account via WhatsApp Web
- **Reliable Delivery**: Direct control over message sending
- **Cost Effective**: No API fees for WhatsApp messaging
- **Scalable**: Cloud hosting for the main application

## Setup Instructions

### 1. Deploy to Railway

1. Deploy your Flask app to Railway
2. Set these environment variables on Railway:
   ```
   SECRET_KEY=your_generated_secret_key
   DATABASE_URL=your_railway_postgres_url
   FOOTBALL_DATA_API_TOKEN=your_api_token
   WORKER_API_TOKEN=your_generated_worker_token
   ```

### 2. Configure Local Environment

1. Run the setup script:
   ```bash
   python setup_hybrid_system.py
   ```

2. Or manually create `.env` file:
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

### 3. Set Up Chrome Profile

1. Open Chrome and log into WhatsApp Web
2. Note the Chrome profile path (the setup script can find it automatically)
3. Keep this browser session active

### 4. Start Local Worker

```bash
cd lms_automation
python sender_worker.py
```

The worker will:
- Connect to your Railway app
- Poll for new messages every 30 seconds
- Send messages via WhatsApp Web using Selenium
- Report back success/failure status

## Usage

### Admin Dashboard

1. Go to your Railway app URL + `/admin_dashboard`
2. Create rounds and manage players as usual
3. Click "Send Links via WhatsApp" to queue messages
4. Monitor queue status in the dashboard

### Queue Status

The admin dashboard shows:
- **Pending**: Messages waiting to be sent
- **In Progress**: Messages currently being processed
- **Sent**: Successfully delivered messages
- **Failed**: Messages that failed to send

### Player Management

- Players marked as "unreachable" after 5 consecutive failures
- Unreachable players are automatically skipped
- You can manually manage player WhatsApp numbers

## Troubleshooting

### Worker Connection Issues

1. Check `BASE_URL` in `.env` matches your Railway app
2. Verify `WORKER_API_TOKEN` matches between Railway and local `.env`
3. Ensure your Railway app is running

### WhatsApp Issues

1. Keep Chrome/WhatsApp Web logged in
2. If disconnected, restart the worker after re-logging in
3. Check Chrome profile path in `.env`

### Message Delivery

1. Check admin dashboard for queue status
2. Failed messages will show error details
3. Worker logs show detailed processing information

## Files Structure

```
lms_automation/
├── app.py                 # Main Flask application
├── whatsapp_sender.py    # Selenium WhatsApp sender
├── sender_worker.py      # Local worker daemon
├── models.py             # Database models (includes SendQueue)
└── templates/            # Web templates

# Configuration
.env                      # Local environment variables
.env.example             # Template for environment variables
setup_hybrid_system.py  # Setup script
```

## Security Notes

- `WORKER_API_TOKEN` should be a strong, random token
- Keep your `.env` file secure and never commit it
- The local worker only connects to your Railway app
- No WhatsApp credentials are stored

## Monitoring

- Check worker logs for processing status
- Admin dashboard shows real-time queue statistics
- Failed messages include error details for debugging

This hybrid approach gives you the reliability of cloud hosting with the flexibility of personal WhatsApp messaging.