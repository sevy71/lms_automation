# LMS Telegram Automation - Complete System Documentation

## 🎯 Overview

The LMS (Last Man Standing) Telegram Automation system provides **fully automated game management** from round start to finish, including:

- ✅ Automatic token generation for all active players
- ✅ Telegram bot for pick submission
- ✅ Automated reminder distribution (4-hour and 2-hour warnings)
- ✅ Fixture result polling from Football API
- ✅ Automatic elimination processing
- ✅ **Rollover handling** when all players are eliminated
- ✅ Winner detection and announcement
- ✅ Cycle management (Round 20 completion with survivors)

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
pip install -r telegram_bot/requirements.txt
```

### 2. Configure Environment Variables

Create `.env` file:

```env
# Flask Configuration
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///lms.db
FLASK_ENV=development
BASE_URL=http://localhost:5000

# Football API
FOOTBALL_API_TOKEN=your-football-data-api-token

# Telegram Bot
TELEGRAM_BOT_TOKEN=your-telegram-bot-token

# Optional: Auto-reset on winner
AUTO_RESET_ON_WIN=false

# Notification Services (Optional)
TWILIO_ACCOUNT_SID=your-twilio-sid
TWILIO_AUTH_TOKEN=your-twilio-token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
SENDGRID_API_KEY=your-sendgrid-key
```

### 3. Apply Database Migrations

```bash
export FLASK_APP=lms_automation.app
flask db upgrade
```

### 4. Run the System

#### Option A: Development (Two terminals)

Terminal 1 - Flask app with scheduler:
```bash
python run_with_scheduler.py
```

Terminal 2 - Telegram bot:
```bash
python -m telegram_bot.bot.main
```

#### Option B: Production (with Procfile)

```bash
web: gunicorn lms_automation.app:app
worker: python run_with_scheduler.py
bot: python -m telegram_bot.bot.main
```

### 5. Test the Automation

Run the dry run test:
```bash
python test_automation.py
```

## 🔄 Automated Game Flow

### 1. **Round Activation**
When admin creates/activates a round:
- Scheduler automatically generates pick tokens for all active players
- Tokens are created with 7-day expiry or round deadline
- Each player can edit their pick up to 2 times

### 2. **Reminder Distribution**
The scheduler sends automated reminders:
- **4 hours before** first kickoff: Initial reminder
- **2 hours before** first kickoff: Final reminder
- Sent via Telegram (primary) or WhatsApp (fallback)
- Players receive pick link: `/pick/{token}`

### 3. **Pick Submission**
Players can submit picks via:
- **Telegram bot**: `/pick {token}` command
- **Web form**: Click the pick link
- **Inline buttons**: Select team directly in Telegram

### 4. **Deadline Processing**
At round deadline:
- Auto-picks applied for players who missed deadline
- Uses opposing team from previous losses (if available)
- Falls back to first available team alphabetically

### 5. **Fixture Updates**
Every 30 minutes:
- Polls Football API for match results
- Updates fixture scores in database
- Marks completed matches

### 6. **Elimination Processing**
When all fixtures complete:
- Processes win/loss for each pick
- Eliminates players with losing picks
- Updates player statuses

### 7. **Game State Management**

#### **Winner Scenario** (1 player remaining)
```python
if active_players == 1:
    - Mark player as winner
    - Send winner announcement to all
    - Admin can reset game for new cycle
```

#### **Rollover Scenario** (0 players remaining)
```python
if active_players == 0:
    - ALL PLAYERS ELIMINATED!
    - Reset all players to 'active'
    - Start new cycle with same players
    - All teams available again
```

#### **Cycle Complete** (Round 20 with 2+ survivors)
```python
if round_number == 20 and active_players >= 2:
    - Survivors advance to next cycle
    - Eliminated players stay eliminated
    - All teams available for survivors
```

## 📡 API Endpoints

### Telegram Bot Integration

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/picks/options/<token>` | GET | Get available teams for token |
| `/api/picks/submit` | POST | Submit pick via bot |
| `/api/register` | POST | Register player with telegram_id |

### Automation Control

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/automation/process-round/<id>` | POST | Process eliminations & check rollover |
| `/api/automation/start-new-cycle` | POST | Start new cycle after rollover |
| `/api/automation/generate-tokens/<id>` | POST | Generate tokens for round |

## 🤖 Telegram Bot Commands

### Player Commands
- `/start` - Register for the game
- `/pick <token>` - Submit your pick
- `/status` - Check your current status
- `/reminders on/off` - Toggle reminder preferences

### Admin Commands
- `/reminders` - View pending reminders
- `/mark_sent <id>` - Mark reminder as sent
- `/stats` - View game statistics

## 🔧 Background Jobs

The scheduler runs these jobs automatically:

| Job | Frequency | Purpose |
|-----|-----------|---------|
| `update_fixture_results` | Every 30 min | Poll Football API for results |
| `process_eliminations` | Every hour | Process eliminations and rollovers |
| `send_due_reminders` | Every 15 min | Send Telegram/WhatsApp reminders |
| `generate_round_tokens` | Every hour | Create tokens for new rounds |
| `apply_missed_picks` | Every hour | Auto-pick for deadline missers |

## 🔄 Rollover Handling

The system **automatically handles rollovers** when all players are eliminated:

1. **Detection**: After processing eliminations, if `active_players == 0`
2. **Reset**: All players set back to `status = 'active'`
3. **Notification**: All players receive rollover announcement
4. **New Cycle**: Increment `cycle_number` for tracking
5. **Teams Reset**: All 20 teams available for selection again

### Manual Rollover Testing

To test rollover scenario:
```python
# In Flask shell or admin panel:
from lms_automation.models import Player, db
Player.query.update({'status': 'eliminated'})
db.session.commit()
# Scheduler will detect and handle rollover automatically
```

## 📊 Database Schema

Key models with rollover support:

```python
class Round:
    cycle_number = Integer  # Tracks which cycle (handles rollovers)
    special_measure = String  # 'universal_bye' for special cases

class Player:
    telegram_id = String  # For Telegram notifications
    status = String  # 'active', 'eliminated', 'winner'
```

## 🐛 Troubleshooting

### Issue: Reminders not sending
- Check `TELEGRAM_BOT_TOKEN` is set
- Verify players have `telegram_id` populated
- Check scheduler is running: `ps aux | grep scheduler`

### Issue: Fixtures not updating
- Verify `FOOTBALL_API_TOKEN` is valid
- Check API rate limits
- Review logs: `tail -f logs/scheduler.log`

### Issue: Rollover not triggering
- Ensure scheduler job `process_eliminations` is running
- Check Round status is 'completed'
- Verify all fixtures have results

### Issue: Tokens not generating
- Check Round status is 'active'
- Verify players have status 'active'
- Review `generate_round_tokens` job logs

## 🚀 Production Deployment

### Railway/Heroku

1. Set environment variables in platform dashboard
2. Deploy with Procfile:
```
web: python -m flask --app lms_automation.app db upgrade && gunicorn lms_automation.app:app
worker: python run_with_scheduler.py
bot: python -m telegram_bot.bot.main
```

3. Scale dynos:
```bash
heroku ps:scale web=1 worker=1 bot=1
```

### Docker Deployment

Create `docker-compose.yml`:
```yaml
version: '3.8'
services:
  web:
    build: .
    command: gunicorn lms_automation.app:app
    ports:
      - "5000:5000"
    env_file: .env

  scheduler:
    build: .
    command: python run_with_scheduler.py
    env_file: .env

  bot:
    build: .
    command: python -m telegram_bot.bot.main
    env_file: .env

  postgres:
    image: postgres:14
    environment:
      POSTGRES_DB: lms
      POSTGRES_USER: lms
      POSTGRES_PASSWORD: secret
```

## ✅ System Features Checklist

- [x] Automated token generation
- [x] Telegram bot pick submission
- [x] WhatsApp fallback notifications
- [x] Football API integration
- [x] Automatic eliminations
- [x] **Rollover when all eliminated**
- [x] Winner detection
- [x] Cycle management
- [x] Auto-pick for missed deadlines
- [x] 4-hour and 2-hour reminders
- [x] Pick editing (max 2 times)
- [x] Admin dashboard
- [x] Player statistics
- [x] Excel/CSV exports

## 📝 License

MIT License - See LICENSE file for details

## 🤝 Support

For issues or questions about the automation system:
1. Check this documentation
2. Review test output: `python test_automation.py`
3. Check logs in Flask app and scheduler
4. Open an issue with error details

---

**The system is now fully automated and handles all scenarios including rollovers!** 🎉