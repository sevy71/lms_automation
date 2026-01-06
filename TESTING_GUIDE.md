# 🧪 LMS Telegram Automation - Complete Testing Guide

## Prerequisites

### 1. Create a Telegram Bot
1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name: e.g., "LMS Game Bot"
4. Choose a username: e.g., `lms_game_bot` (must end in 'bot')
5. Copy the token that looks like: `7234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

### 2. Set Up Environment

Create `.env` file in project root:
```env
# Required
TELEGRAM_BOT_TOKEN=7234567890:ABCdefGHIjklMNOpqrsTUVwxyz
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///lms.db

# Optional but recommended
FOOTBALL_API_TOKEN=get-from-football-data.org
BASE_URL=http://localhost:5000

# Optional WhatsApp via Twilio
TWILIO_ACCOUNT_SID=your-sid
TWILIO_AUTH_TOKEN=your-token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
```

## 📝 Step-by-Step Testing Process

### Step 1: Prepare the System

```bash
# 1. Navigate to project
cd ~/Projects/LMS2_telegram_experiment

# 2. Activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install APScheduler==3.10.4

# 4. Create database
cd lms_automation
python3 -c "
from lms_automation.app import app
from lms_automation.extensions import db
with app.app_context():
    db.create_all()
    print('✅ Database created!')
"
cd ..
```

### Step 2: Start the System (3 terminals needed)

**Terminal 1 - Flask App:**
```bash
cd lms_automation
python3 app.py
# Should see: Running on http://127.0.0.1:5000
```

**Terminal 2 - Scheduler:**
```bash
python3 run_with_scheduler.py
# Should see: Scheduler started with all jobs configured
```

**Terminal 3 - Telegram Bot:**
```bash
python3 -m telegram_bot.bot.main
# Should see: Bot started in polling mode
```

### Step 3: Register Players via Telegram

1. **Open Telegram** on your phone/desktop
2. **Search for your bot** (e.g., @lms_game_bot)
3. **Start conversation:**
   ```
   You: /start
   Bot: 👋 Welcome to Last Man Standing! What's your full name?
   You: John Smith
   Bot: Great! If you'd like reminders, send your WhatsApp number...
   You: /skip
   Bot: Welcome John Smith! You're now registered.
   ```

4. **Register 4-5 test players** (get friends to help or use multiple Telegram accounts)

### Step 4: Create a Test Round (Admin Dashboard)

1. **Open browser:** http://localhost:5000/admin
2. **Login** (check app.py for default password or set one)
3. **Create Round:**
   - Round Number: 1
   - PL Matchday: 15
   - Status: Active
   - Add 3-4 fixtures (Arsenal vs Chelsea, Liverpool vs Man City, etc.)

### Step 5: Test Token Generation

**Check if tokens were auto-generated:**
```bash
curl http://localhost:5000/api/automation/generate-tokens/1 \
  -X POST \
  -H "Content-Type: application/json"
```

**Players should receive in Telegram:**
```
⚽ Round 1 is open for picks!
Submit your pick: /pick ABC123XYZ...
Deadline: [date/time]
```

### Step 6: Test Pick Submission

**In Telegram:**
```
You: /pick ABC123XYZ
Bot: Choose your team:
     [Arsenal] [Chelsea] [Liverpool] [Man City]
You: Click "Arsenal"
Bot: ✅ Pick confirmed! You selected Arsenal for Round 1
```

### Step 7: Test Reminders

**Speed up testing by modifying scheduler.py temporarily:**
```python
# Change from hours to minutes for testing
trigger=IntervalTrigger(minutes=1),  # Was hours=1
```

**You should see:**
- Reminders being sent every minute
- Check Telegram for reminder messages

### Step 8: Test Match Results & Eliminations

**Simulate match completion:**
```bash
# In Python console or create test script:
cd lms_automation
python3 -c "
from lms_automation.app import app
from lms_automation.extensions import db
from lms_automation.models import Fixture, Round

with app.app_context():
    # Complete fixtures
    fixtures = Fixture.query.all()
    for f in fixtures:
        f.status = 'completed'
        f.home_score = 2
        f.away_score = 1
    db.session.commit()

    # Trigger elimination processing
    import requests
    r = requests.post('http://localhost:5000/api/automation/process-round/1')
    print(r.json())
"
```

### Step 9: Test ROLLOVER Scenario

**Simulate all players eliminated:**
```python
# Make all players pick losing teams
from lms_automation.app import app
from lms_automation.extensions import db
from lms_automation.models import Player, Pick

with app.app_context():
    # Set all picks to losing team
    picks = Pick.query.all()
    for p in picks:
        p.team_picked = "Chelsea"  # Assuming Chelsea lost
        p.is_winner = False
        p.is_eliminated = True
        p.player.status = 'eliminated'
    db.session.commit()

    # Check active players
    active = Player.query.filter_by(status='active').count()
    print(f"Active players: {active}")

    if active == 0:
        print("🔄 ROLLOVER TRIGGERED!")
        # System should auto-reset all players
```

**All players should receive:**
```
🔄 ROLLOVER - ALL PLAYERS ELIMINATED!
Good news: Everyone is back in the game!
Starting fresh with Cycle 2.
```

### Step 10: Verify Full Automation

**Check these are happening automatically:**
- [ ] Tokens generated when round activated
- [ ] Reminders sent at scheduled times
- [ ] Fixtures updating from Football API
- [ ] Eliminations processed after matches
- [ ] Rollover triggered when all eliminated
- [ ] Winner detected when 1 player left

## 🧪 Quick Test Script

Save as `quick_test.py`:
```python
#!/usr/bin/env python3
import requests
import time

BASE_URL = "http://localhost:5000"

def test_system():
    print("1. Testing API endpoints...")

    # Test token generation
    r = requests.post(f"{BASE_URL}/api/automation/generate-tokens/1")
    print(f"   Token generation: {r.status_code}")

    # Test elimination processing
    r = requests.post(f"{BASE_URL}/api/automation/process-round/1")
    print(f"   Elimination processing: {r.status_code}")

    print("\n2. Check Telegram bot...")
    print("   Open Telegram and try /start")

    print("\n3. System is running!" if all else "Issues detected")

if __name__ == "__main__":
    test_system()
```

## 🔍 Monitoring & Logs

**Watch the logs in each terminal:**

1. **Flask App:** Shows API requests
2. **Scheduler:** Shows job executions
3. **Bot:** Shows Telegram interactions

**Check database:**
```sql
sqlite3 lms_automation/lms.db
.tables
SELECT * FROM players;
SELECT * FROM picks;
SELECT * FROM rounds;
.quit
```

## ⚠️ Common Issues & Fixes

### Bot not responding
- Check TELEGRAM_BOT_TOKEN is correct
- Ensure bot terminal is running
- Try `/start` command again

### Tokens not generating
- Check round status is 'active'
- Verify players are registered
- Run manual generation endpoint

### Reminders not sending
- Check telegram_id is saved for players
- Verify scheduler is running
- Check reminder_schedules table

### Database errors
- Drop and recreate: `rm lms.db` then recreate
- Check all terminals are using same database

## 🎯 Success Criteria

You know it's working when:
1. ✅ Players can register via Telegram
2. ✅ Tokens auto-generate for rounds
3. ✅ Players can pick teams via bot
4. ✅ Reminders sent automatically
5. ✅ Eliminations process automatically
6. ✅ Rollover resets all players when all eliminated
7. ✅ Winner detected when 1 player remains

## 📱 Testing with Real Players

1. Share bot username with friends
2. Have them register via `/start`
3. Create a real round with actual PL fixtures
4. Let system run automatically
5. Watch the magic happen!

## 🛑 Stopping the System

Press `Ctrl+C` in each terminal to stop:
1. Stop bot first
2. Stop scheduler
3. Stop Flask app

## 📊 Checking Results

Visit: http://localhost:5000/picks-grid to see all picks and results!
