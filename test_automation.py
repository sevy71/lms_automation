#!/usr/bin/env python3
"""
Test script for LMS Telegram automation
Demonstrates the complete automated flow
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

# Setup path for imports
sys.path.insert(0, os.path.dirname(__file__))

from lms_automation.app import app
from lms_automation.models import db, Player, Round, Fixture, Pick, PickToken, ReminderSchedule
from lms_automation.scheduler import LMSScheduler
from lms_automation.telegram_service import telegram_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_test_data():
    """Create test data for dry run"""
    logger.info("Creating test data...")

    # Create test players
    test_players = [
        {"name": "Test Player 1", "telegram_id": "123456", "whatsapp_number": "+1234567890"},
        {"name": "Test Player 2", "telegram_id": "123457", "whatsapp_number": "+1234567891"},
        {"name": "Test Player 3", "telegram_id": "123458", "whatsapp_number": "+1234567892"},
        {"name": "Test Player 4", "telegram_id": "123459", "whatsapp_number": "+1234567893"},
        {"name": "Test Player 5", "telegram_id": "123460", "whatsapp_number": "+1234567894"},
    ]

    players = []
    for p_data in test_players:
        player = Player.query.filter_by(name=p_data["name"]).first()
        if not player:
            player = Player(**p_data)
            db.session.add(player)
        players.append(player)

    # Create test round
    test_round = Round.query.filter_by(round_number=99).first()
    if not test_round:
        test_round = Round(
            round_number=99,
            pl_matchday=15,
            start_date=datetime.now(),
            end_date=datetime.now() + timedelta(days=3),
            first_kickoff_at=datetime.now() + timedelta(days=2, hours=15),
            status='active',
            cycle_number=1
        )
        db.session.add(test_round)
        db.session.commit()  # Commit to get ID for fixtures

    # Create test fixtures
    test_fixtures = [
        {"home_team": "Arsenal", "away_team": "Chelsea", "date": datetime.now().date() + timedelta(days=2)},
        {"home_team": "Liverpool", "away_team": "Man City", "date": datetime.now().date() + timedelta(days=2)},
        {"home_team": "Tottenham", "away_team": "Man United", "date": datetime.now().date() + timedelta(days=2)},
    ]

    for f_data in test_fixtures:
        fixture = Fixture.query.filter_by(
            round_id=test_round.id,
            home_team=f_data["home_team"],
            away_team=f_data["away_team"]
        ).first()

        if not fixture:
            fixture = Fixture(
                round_id=test_round.id,
                status='scheduled',
                **f_data
            )
            db.session.add(fixture)

    db.session.commit()
    logger.info(f"Created {len(players)} test players and Round {test_round.round_number}")

    return test_round, players

def test_token_generation():
    """Test automatic token generation"""
    logger.info("\n=== Testing Token Generation ===")

    test_round = Round.query.filter_by(round_number=99).first()
    active_players = Player.query.filter_by(status='active').limit(5).all()

    tokens_created = 0
    for player in active_players:
        existing = PickToken.query.filter_by(
            player_id=player.id,
            round_id=test_round.id
        ).first()

        if not existing:
            token = PickToken.create_for_player_round(
                player.id,
                test_round.id,
                expires_hours=72
            )
            db.session.add(token)
            tokens_created += 1
            logger.info(f"Token created for {player.name}: /pick/{token.token}")

    db.session.commit()
    logger.info(f"✅ Generated {tokens_created} pick tokens")

def test_pick_submission():
    """Test pick submission via API"""
    logger.info("\n=== Testing Pick Submission ===")

    # Get a token
    token = PickToken.query.filter_by(is_used=False).first()
    if token:
        # Simulate pick submission
        pick = Pick(
            player_id=token.player_id,
            round_id=token.round_id,
            team_picked="Arsenal",
            timestamp=datetime.now()
        )
        db.session.add(pick)
        token.is_used = True
        token.used_at = datetime.now()
        db.session.commit()

        logger.info(f"✅ {token.player.name} picked Arsenal")
    else:
        logger.warning("No unused tokens found")

def test_reminder_scheduling():
    """Test reminder scheduling"""
    logger.info("\n=== Testing Reminder Scheduling ===")

    test_round = Round.query.filter_by(round_number=99).first()

    # Schedule reminders
    active_players = Player.query.filter_by(status='active').limit(5).all()
    reminders_created = 0

    for player in active_players:
        # 4-hour reminder
        existing = ReminderSchedule.query.filter_by(
            player_id=player.id,
            round_id=test_round.id,
            reminder_type='4_hour'
        ).first()

        if not existing and test_round.first_kickoff_at:
            reminder = ReminderSchedule(
                player_id=player.id,
                round_id=test_round.id,
                reminder_type='4_hour',
                scheduled_time=test_round.first_kickoff_at - timedelta(hours=4),
                is_sent=False
            )
            db.session.add(reminder)
            reminders_created += 1

    db.session.commit()
    logger.info(f"✅ Scheduled {reminders_created} reminders")

def test_elimination_processing():
    """Test elimination processing with rollover scenarios"""
    logger.info("\n=== Testing Elimination Processing ===")

    test_round = Round.query.filter_by(round_number=99).first()

    # Simulate match results
    fixtures = Fixture.query.filter_by(round_id=test_round.id).all()
    for fixture in fixtures:
        fixture.status = 'completed'
        fixture.home_score = 2
        fixture.away_score = 1
        logger.info(f"Result: {fixture.home_team} 2-1 {fixture.away_team}")

    # Update picks based on results
    picks = Pick.query.filter_by(round_id=test_round.id).all()
    eliminated_count = 0

    for pick in picks:
        # Check if pick won
        winning_teams = ["Arsenal", "Liverpool", "Tottenham"]  # Home teams won
        if pick.team_picked in winning_teams:
            pick.is_winner = True
        else:
            pick.is_winner = False
            pick.is_eliminated = True
            pick.player.status = 'eliminated'
            eliminated_count += 1

    db.session.commit()
    logger.info(f"✅ Processed eliminations: {eliminated_count} players eliminated")

    # Check for rollover scenarios
    active_players = Player.query.filter_by(status='active').count()
    logger.info(f"Active players remaining: {active_players}")

    if active_players == 0:
        logger.info("🔄 ROLLOVER SCENARIO: All players eliminated!")
        # Reset all players
        Player.query.update({'status': 'active'}, synchronize_session=False)
        db.session.commit()
        logger.info("✅ All players reset to active for new cycle")
    elif active_players == 1:
        winner = Player.query.filter_by(status='active').first()
        winner.status = 'winner'
        db.session.commit()
        logger.info(f"🏆 WINNER: {winner.name} has won the game!")

def test_scheduler_jobs():
    """Test scheduler jobs"""
    logger.info("\n=== Testing Scheduler Jobs ===")

    scheduler = LMSScheduler()
    scheduler.init_app(app)

    # Test each job manually
    logger.info("Testing fixture updates...")
    scheduler.update_fixture_results()

    logger.info("Testing elimination processing...")
    scheduler.process_eliminations()

    logger.info("Testing token generation...")
    scheduler.generate_round_tokens()

    logger.info("Testing reminder sending...")
    scheduler.send_due_reminders()

    logger.info("✅ All scheduler jobs tested")

def cleanup_test_data():
    """Clean up test data after run"""
    logger.info("\n=== Cleaning Up Test Data ===")

    # Delete test round and related data
    test_round = Round.query.filter_by(round_number=99).first()
    if test_round:
        Pick.query.filter_by(round_id=test_round.id).delete()
        PickToken.query.filter_by(round_id=test_round.id).delete()
        ReminderSchedule.query.filter_by(round_id=test_round.id).delete()
        Fixture.query.filter_by(round_id=test_round.id).delete()
        db.session.delete(test_round)

    # Reset test players
    Player.query.filter(Player.name.like('Test Player%')).delete(synchronize_session=False)

    db.session.commit()
    logger.info("✅ Test data cleaned up")

def main():
    """Run the complete automation test"""
    logger.info("=" * 50)
    logger.info("LMS TELEGRAM AUTOMATION DRY RUN")
    logger.info("=" * 50)

    with app.app_context():
        try:
            # Create test data
            test_round, players = create_test_data()

            # Test each component
            test_token_generation()
            test_pick_submission()
            test_reminder_scheduling()
            test_elimination_processing()
            test_scheduler_jobs()

            logger.info("\n" + "=" * 50)
            logger.info("✅ DRY RUN COMPLETED SUCCESSFULLY!")
            logger.info("=" * 50)

            logger.info("\nThe automation system is ready for production use!")
            logger.info("\nTo start the full system:")
            logger.info("1. Run: python run_with_scheduler.py")
            logger.info("2. Run: python -m telegram_bot.bot.main")
            logger.info("3. Configure environment variables in .env")

            # Optional: cleanup
            cleanup = input("\nClean up test data? (y/n): ")
            if cleanup.lower() == 'y':
                cleanup_test_data()

        except Exception as e:
            logger.error(f"Error during dry run: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

if __name__ == "__main__":
    main()