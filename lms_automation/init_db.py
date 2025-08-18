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
        from sqlalchemy import inspect, text
        
        with app.app_context():
            print("🔄 Initializing database...")
            
            # Check current state
            inspector = inspect(db.engine)
            
            if 'round' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('round')]
                print(f"📋 Existing Round table columns: {columns}")
                
                # If old schema detected, drop and recreate
                if 'game_round_number' in columns and 'round_number' not in columns:
                    print("⚠️  Old schema detected, dropping Round table for recreation")
                    with db.engine.connect() as conn:
                        conn.execute(text("DROP TABLE round"))
                        conn.commit()
                    print("🗑️  Old Round table dropped")
            
            # Create all tables
            db.create_all()
            print("✅ Database tables created/updated successfully")
            
            # Check if we have any existing data
            from models import Player, Round
            
            try:
                player_count = Player.query.count()
                round_count = Round.query.count()
                print(f"📊 Current data: {player_count} players, {round_count} rounds")
            except Exception as e:
                print(f"ℹ️  Could not count existing data: {e}")
            
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        
        # Try creating fresh schema as fallback
        try:
            print("🔄 Trying fresh schema creation...")
            from app import app, db
            with app.app_context():
                db.drop_all()
                db.create_all()
            print("✅ Fresh schema created")
        except Exception as e2:
            print(f"❌ Fresh schema creation also failed: {e2}")
            sys.exit(1)

if __name__ == "__main__":
    init_db()
    print("🎉 Database initialization complete!")