#!/usr/bin/env python3
"""
Database initialization script for LMS application
This script initializes the database and creates all necessary tables
"""

import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

def init_db():
    """Initialize the database and create all tables"""
    try:
        from app import app, db
        
        with app.app_context():
            print("🔄 Initializing database...")
            db.create_all()
            print("✅ Database tables created successfully")
            
            # Check if we have any existing data
            from models import Player, Round
            
            player_count = Player.query.count()
            round_count = Round.query.count()
            
            print(f"📊 Current data: {player_count} players, {round_count} rounds")
            
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        sys.exit(1)

if __name__ == "__main__":
    init_db()
    print("🎉 Database initialization complete!")