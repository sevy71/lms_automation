#!/usr/bin/env python3
"""
Fix local setup by copying messages to the correct database location
"""

import sys
import os
import sqlite3
import shutil

# Add the lms_automation directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lms_automation'))

def fix_database_setup():
    print("🔧 Fixing local database setup...")
    
    # Paths
    source_db = "lms_automation/lms.db"  # Where messages were queued
    target_db = "lms.db"  # Where Flask app expects them
    
    # Check if source database exists and has messages
    if os.path.exists(source_db):
        conn_source = sqlite3.connect(source_db)
        cursor_source = conn_source.cursor()
        
        try:
            cursor_source.execute("SELECT COUNT(*) FROM send_queue WHERE status='pending'")
            pending_count = cursor_source.fetchone()[0]
            print(f"📥 Found {pending_count} pending messages in source database")
            
            if pending_count > 0:
                # Copy the database
                print(f"📋 Copying database from {source_db} to {target_db}")
                shutil.copy2(source_db, target_db)
                print("✅ Database copied successfully")
            else:
                print("ℹ️  No pending messages to copy")
                
        except sqlite3.OperationalError as e:
            print(f"⚠️  Source database issue: {e}")
        finally:
            conn_source.close()
    else:
        print(f"❌ Source database {source_db} not found")
    
    # Now ensure the target database has all tables
    print("🔧 Ensuring target database has all tables...")
    
    try:
        from app import app, db
        
        # Configure the app to use the target database
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.abspath(target_db)}'
        
        with app.app_context():
            db.create_all()
            print("✅ All tables created in target database")
            
            # Verify we have pending messages
            from models import SendQueue
            pending = SendQueue.query.filter_by(status='pending').count()
            print(f"📥 Confirmed {pending} pending messages in target database")
            
    except Exception as e:
        print(f"❌ Error setting up target database: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = fix_database_setup()
    if success:
        print("\n🎉 Local setup fixed!")
        print("📋 Next steps:")
        print("1. Start Flask: cd lms_automation && python3 app.py")
        print("2. Restart worker: launchctl unload ~/Library/LaunchAgents/com.lms.senderworker.plist && launchctl load ~/Library/LaunchAgents/com.lms.senderworker.plist")
        print("3. Test connection: python3 test_connection.py")
    else:
        print("❌ Failed to fix setup")
        sys.exit(1)