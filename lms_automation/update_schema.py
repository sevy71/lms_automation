#!/usr/bin/env python3
"""
Update database schema for hybrid WhatsApp system
This script helps migrate from the old schema to the new simplified schema
"""

import os
import sys
from sqlalchemy import create_engine, text

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from database import db
from models import *
from app import app

def update_schema():
    """Update the database schema to match the new models"""
    
    with app.app_context():
        print("🔄 Updating database schema...")
        
        try:
            # Create all tables
            db.create_all()
            print("✅ Database tables created/updated")
            
            # Check if we need to migrate Round table data
            engine = db.get_engine()
            
            # Check if old columns exist
            try:
                result = engine.execute(text("SELECT game_round_number FROM round LIMIT 1"))
                # If we get here, old schema exists, need to migrate
                print("📦 Migrating Round table data...")
                
                # Migrate data from old columns to new
                engine.execute(text("""
                    UPDATE round 
                    SET round_number = game_round_number 
                    WHERE round_number IS NULL
                """))
                
                print("✅ Round table data migrated")
                
            except Exception as e:
                # Old columns don't exist, probably already migrated or new install
                print("ℹ️  No migration needed for Round table")
            
            print("🎉 Schema update completed successfully!")
            
        except Exception as e:
            print(f"❌ Error updating schema: {e}")
            return False
            
    return True

if __name__ == "__main__":
    if update_schema():
        print("\n✅ Database is ready for the hybrid WhatsApp system!")
    else:
        print("\n❌ Schema update failed. Please check the errors above.")
        sys.exit(1)