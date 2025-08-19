# Testing WhatsApp Locally

## The Issue
When testing the WhatsApp functionality on localhost, the worker tries to connect to Railway instead of your local server, so no messages get sent.

## Solution: Use Local Environment

### 1. For Local Development (Testing WhatsApp)

**Use `.env.local` instead of `.env`:**

```bash
# Backup current .env
mv .env .env.production

# Use local config
mv .env.local .env
```

This sets `BASE_URL=http://localhost:5000` so the worker connects to your local Flask server.

### 2. Start Your Local Server

```bash
cd lms_automation
python app.py
```

Your Flask server should be running on `http://localhost:5000`

### 3. Test WhatsApp Sending

1. Go to `http://localhost:5000/admin_dashboard`
2. Click "Send Links via WhatsApp" button
3. Worker will now connect to your local server and find the queued messages
4. Messages should be sent via your local Chrome/WhatsApp Web

### 4. For Production Deployment

**Switch back to production config:**

```bash
# Restore production config
mv .env .env.local
mv .env.production .env
```

This restores `BASE_URL=https://lmsautomation-production.up.railway.app/` for Railway deployment.

## Quick Commands

**Switch to Local:**
```bash
cp .env .env.production && cp .env.local .env
```

**Switch to Production:**
```bash
cp .env .env.local && cp .env.production .env
```

## Verification

Check which mode you're in:
```bash
grep "BASE_URL" .env
```

- Local: `BASE_URL=http://localhost:5000`
- Production: `BASE_URL=https://lmsautomation-production.up.railway.app/`