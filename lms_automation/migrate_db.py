#!/usr/bin/env python3
"""
Database migration script for LMS application
This script handles migrating from the old schema to the new simplified schema
"""

import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

def migrate_database():
    """Migrate database schema safely"""
    try:
        from app import app, db
        from sqlalchemy import text, inspect
        
        with app.app_context():
            print("🔄 Starting database migration...")
            
            # Get database inspector
            inspector = inspect(db.engine)
            
            # Check if Round table exists
            if 'round' not in inspector.get_table_names():
                print("ℹ️  Round table doesn't exist, creating fresh schema")
                db.create_all()
                print("✅ Fresh database schema created")
                return
            
            # Get existing columns in Round table
            columns = [col['name'] for col in inspector.get_columns('round')]
            print(f"📋 Existing Round table columns: {columns}")
            
            # Check if migration is needed
            if 'round_number' in columns:
                print("✅ Database schema is already up to date")
                # Still create any missing tables
                db.create_all()
                return
            
            # Migration needed - backup and recreate approach
            print("🔄 Migration needed - using backup and recreate approach...")
            
            with db.engine.connect() as conn:
                # Start transaction
                trans = conn.begin()
                
                try:
                    # Create backup of existing data
                    if 'game_round_number' in columns:
                        print("💾 Backing up Round table data...")
                        backup_query = text("""
                            CREATE TEMPORARY TABLE round_backup AS 
                            SELECT id, game_round_number as round_number, start_date, end_date, status 
                            FROM round
                        """)
                        conn.execute(backup_query)
                        
                        # Drop existing table
                        conn.execute(text("DROP TABLE round"))
                        print("🗑️  Dropped old Round table")
                    
                    # Commit the transaction
                    trans.commit()
                    
                except Exception as e:
                    trans.rollback()
                    raise e
            
            # Create new schema
            print("🏗️  Creating new database schema...")
            db.create_all()
            
            # Restore data if we had a backup
            if 'game_round_number' in columns:
                with db.engine.connect() as conn:
                    trans = conn.begin()
                    try:
                        restore_query = text("""
                            INSERT INTO round (id, round_number, start_date, end_date, status)
                            SELECT id, round_number, start_date, end_date, status 
                            FROM round_backup
                        """)
                        result = conn.execute(restore_query)
                        trans.commit()
                        print(f"📦 Restored {result.rowcount} Round records")
                    except Exception as e:
                        trans.rollback()
                        print(f"⚠️  Could not restore Round data: {e}")
                        print("💡 You may need to recreate your rounds manually")
            
            print("✅ Database migration completed successfully!")
            
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to create fresh schema as fallback
        try:
            print("🔄 Attempting fresh schema creation as fallback...")
            from app import app, db
            with app.app_context():
                db.drop_all()
                db.create_all()
            print("✅ Fresh schema created successfully")
        except Exception as e2:
            print(f"❌ Fallback also failed: {e2}")
            sys.exit(1)

if __name__ == "__main__":
    migrate_database()
    print("🎉 Migration process complete!")