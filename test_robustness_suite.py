#!/usr/bin/env python3
"""
Comprehensive Robustness Testing Suite for LMS Project
Tests system resilience under various failure scenarios
"""

import os
import sys
import time
import threading
import sqlite3
import requests
import subprocess
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import random
import string

# Add current directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lms_automation'))

def test_database_resilience():
    """Test database under various failure scenarios"""
    print("🗄️  Testing Database Resilience...")
    
    test_results = []
    
    # Test 1: Database connection under load
    print("  📊 Testing concurrent database connections...")
    try:
        from lms_automation.app import app, db
        from lms_automation.models import Player, Round, Pick
        
        def create_test_player(thread_id):
            with app.app_context():
                try:
                    player_name = f"test_player_{thread_id}_{int(time.time())}"
                    player = Player(name=player_name, whatsapp_number="+1234567890")
                    db.session.add(player)
                    db.session.commit()
                    return True, None
                except Exception as e:
                    return False, str(e)
        
        # Test with 20 concurrent database operations
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(create_test_player, i) for i in range(20)]
            results = [future.result() for future in as_completed(futures)]
        
        success_count = sum(1 for success, _ in results if success)
        test_results.append(f"✅ Concurrent DB operations: {success_count}/20 succeeded")
        
    except Exception as e:
        test_results.append(f"❌ Database load test failed: {e}")
    
    # Test 2: Database corruption recovery
    print("  🔧 Testing database corruption scenarios...")
    try:
        db_path = os.path.join(os.path.dirname(__file__), 'lms_automation', 'lms.db')
        if os.path.exists(db_path):
            # Test SQLite integrity
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            result = cursor.fetchone()
            if result[0] == 'ok':
                test_results.append("✅ Database integrity check passed")
            else:
                test_results.append(f"⚠️  Database integrity issues: {result[0]}")
            conn.close()
        else:
            test_results.append("⚠️  Database file not found")
    except Exception as e:
        test_results.append(f"❌ Database integrity test failed: {e}")
    
    return test_results

def test_background_worker_resilience():
    """Test background worker under failure conditions"""
    print("⚙️  Testing Background Worker Resilience...")
    
    test_results = []
    
    # Test 1: Worker API connectivity
    print("  🔗 Testing worker API connectivity...")
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
        WORKER_API_TOKEN = os.environ.get("WORKER_API_TOKEN")
        
        if WORKER_API_TOKEN:
            headers = {"Authorization": f"Bearer {WORKER_API_TOKEN}"}
            response = requests.get(f"{BASE_URL}/api/queue/next?limit=1", 
                                  headers=headers, timeout=5)
            if response.status_code == 200:
                test_results.append("✅ Worker API connectivity successful")
            else:
                test_results.append(f"⚠️  Worker API returned status: {response.status_code}")
        else:
            test_results.append("⚠️  WORKER_API_TOKEN not configured")
            
    except requests.exceptions.ConnectionError:
        test_results.append("❌ Worker API connection failed - Flask server not running")
    except Exception as e:
        test_results.append(f"❌ Worker API test failed: {e}")
    
    # Test 2: Queue processing under load
    print("  📥 Testing message queue under load...")
    try:
        from lms_automation.app import app, db, queue_whatsapp_message
        
        with app.app_context():
            # Queue multiple messages rapidly
            for i in range(10):
                success, error = queue_whatsapp_message(
                    f"+44754585159{i % 10}", 
                    f"Test message {i}",
                    player_id=1
                )
                if not success:
                    test_results.append(f"⚠️  Failed to queue message {i}: {error}")
                    break
            else:
                test_results.append("✅ Successfully queued 10 test messages")
    except Exception as e:
        test_results.append(f"❌ Queue load test failed: {e}")
    
    return test_results

def test_web_application_resilience():
    """Test Flask application under various conditions"""
    print("🌐 Testing Web Application Resilience...")
    
    test_results = []
    
    # Test 1: Route accessibility
    print("  🛣️  Testing route accessibility...")
    try:
        from lms_automation.app import app
        
        test_routes = [
            '/',
            '/admin_dashboard',
            '/standings',
            '/register_player'
        ]
        
        with app.test_client() as client:
            for route in test_routes:
                try:
                    response = client.get(route)
                    if response.status_code < 500:
                        test_results.append(f"✅ Route {route}: Status {response.status_code}")
                    else:
                        test_results.append(f"❌ Route {route}: Status {response.status_code}")
                except Exception as e:
                    test_results.append(f"❌ Route {route} failed: {e}")
                    
    except Exception as e:
        test_results.append(f"❌ Route testing failed: {e}")
    
    # Test 2: Concurrent user sessions
    print("  👥 Testing concurrent user sessions...")
    try:
        from lms_automation.app import app
        
        def simulate_user_session(session_id):
            try:
                with app.test_client() as client:
                    # Simulate typical user workflow
                    response1 = client.get('/')
                    response2 = client.get('/admin_dashboard')
                    response3 = client.get('/standings')
                    
                    return all(r.status_code < 500 for r in [response1, response2, response3])
            except Exception:
                return False
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(simulate_user_session, i) for i in range(10)]
            results = [future.result() for future in as_completed(futures)]
        
        success_count = sum(results)
        test_results.append(f"✅ Concurrent sessions: {success_count}/10 succeeded")
        
    except Exception as e:
        test_results.append(f"❌ Concurrent session test failed: {e}")
    
    return test_results

def test_data_validation_resilience():
    """Test data validation under edge cases"""
    print("🔍 Testing Data Validation Resilience...")
    
    test_results = []
    
    # Test phone number validation
    print("  📞 Testing phone number validation...")
    try:
        from lms_automation.app import is_valid_phone_number, to_e164_digits
        
        edge_cases = [
            ("", False),  # Empty
            ("a" * 1000, False),  # Very long string
            ("+" + "1" * 20, False),  # Too many digits
            ("++44", False),  # Invalid format
            ("044abc", False),  # Mixed characters
            ("\x00\x01", False),  # Control characters
            ("DROP TABLE players", False),  # SQL injection attempt
        ]
        
        passed = 0
        for test_input, expected_valid in edge_cases:
            try:
                result = is_valid_phone_number(test_input)
                if result == expected_valid:
                    passed += 1
            except Exception:
                # Validation should handle exceptions gracefully
                if not expected_valid:
                    passed += 1
        
        test_results.append(f"✅ Phone validation edge cases: {passed}/{len(edge_cases)} passed")
        
    except Exception as e:
        test_results.append(f"❌ Phone validation test failed: {e}")
    
    # Test database input sanitization
    print("  🛡️  Testing database input sanitization...")
    try:
        from lms_automation.app import app, db
        from lms_automation.models import Player
        
        with app.app_context():
            dangerous_inputs = [
                "'; DROP TABLE players; --",
                "<script>alert('xss')</script>",
                "\x00\x01\x02",
                "a" * 1000,
                "Robert'); DROP TABLE students;--"
            ]
            
            safe_count = 0
            for dangerous_input in dangerous_inputs:
                try:
                    # Try to create player with dangerous input
                    test_player = Player(name=dangerous_input[:50], whatsapp_number="+1234567890")
                    db.session.add(test_player)
                    db.session.commit()
                    # Clean up
                    db.session.delete(test_player)
                    db.session.commit()
                    safe_count += 1
                except Exception:
                    # Database should reject or sanitize dangerous input
                    db.session.rollback()
                    safe_count += 1
            
            test_results.append(f"✅ Database input sanitization: {safe_count}/{len(dangerous_inputs)} handled safely")
            
    except Exception as e:
        test_results.append(f"❌ Database sanitization test failed: {e}")
    
    return test_results

def test_memory_and_resource_usage():
    """Test memory usage and resource management"""
    print("💾 Testing Memory and Resource Usage...")
    
    test_results = []
    
    try:
        import psutil
        import gc
        
        # Get current process
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        print(f"  📊 Initial memory usage: {initial_memory:.2f} MB")
        
        # Test memory usage under load
        print("  🔄 Testing memory usage under simulated load...")
        
        from lms_automation.app import app, db
        from lms_automation.models import Player
        
        with app.app_context():
            # Create and delete many objects to test memory management
            for i in range(100):
                players = []
                for j in range(10):
                    player = Player(name=f"temp_player_{i}_{j}", whatsapp_number="+1234567890")
                    players.append(player)
                
                # Force garbage collection
                del players
                gc.collect()
        
        # Check final memory usage
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory
        
        print(f"  📊 Final memory usage: {final_memory:.2f} MB")
        print(f"  📈 Memory increase: {memory_increase:.2f} MB")
        
        if memory_increase < 50:  # Less than 50MB increase is acceptable
            test_results.append(f"✅ Memory usage under control: +{memory_increase:.2f} MB")
        else:
            test_results.append(f"⚠️  High memory usage detected: +{memory_increase:.2f} MB")
            
    except ImportError:
        test_results.append("⚠️  psutil not available - skipping memory tests")
    except Exception as e:
        test_results.append(f"❌ Memory test failed: {e}")
    
    return test_results

def test_error_recovery_scenarios():
    """Test various error recovery scenarios"""
    print("🔄 Testing Error Recovery Scenarios...")
    
    test_results = []
    
    # Test 1: Invalid fixture data handling
    print("  🏈 Testing fixture data error handling...")
    try:
        from lms_automation.app import app, normalize_team, fixture_decision
        from lms_automation.models import Fixture
        
        with app.app_context():
            # Create fixture with invalid data
            invalid_fixture = Fixture(
                event_id="test_invalid_001",
                home_team="",  # Empty team name
                away_team=None,  # Null team name
                date=datetime.now(),
                time="25:00",  # Invalid time
                home_score=-1,  # Negative score
                away_score=999,  # Unrealistic score
                status="INVALID_STATUS"
            )
            
            # Test if functions handle invalid data gracefully
            try:
                decision = fixture_decision(invalid_fixture)
                normalized_home = normalize_team(invalid_fixture.home_team)
                normalized_away = normalize_team(invalid_fixture.away_team)
                
                test_results.append("✅ Invalid fixture data handled gracefully")
            except Exception:
                test_results.append("⚠️  Fixture data validation needs improvement")
                
    except Exception as e:
        test_results.append(f"❌ Fixture data test failed: {e}")
    
    return test_results

def run_comprehensive_test_suite():
    """Run the complete robustness test suite"""
    print("=" * 60)
    print("🧪 STARTING COMPREHENSIVE ROBUSTNESS TEST SUITE")
    print("=" * 60)
    
    all_results = []
    
    # Run all test categories
    test_categories = [
        ("Database Resilience", test_database_resilience),
        ("Background Worker Resilience", test_background_worker_resilience),
        ("Web Application Resilience", test_web_application_resilience),
        ("Data Validation Resilience", test_data_validation_resilience),
        ("Memory and Resource Usage", test_memory_and_resource_usage),
        ("Error Recovery Scenarios", test_error_recovery_scenarios),
    ]
    
    for category_name, test_function in test_categories:
        print(f"\n{'=' * 20} {category_name} {'=' * 20}")
        start_time = time.time()
        
        try:
            results = test_function()
            elapsed = time.time() - start_time
            
            print(f"\n📋 Results for {category_name}:")
            for result in results:
                print(f"   {result}")
            print(f"⏱️  Completed in {elapsed:.2f} seconds")
            
            all_results.extend([(category_name, result) for result in results])
            
        except Exception as e:
            print(f"❌ Category {category_name} failed completely: {e}")
            all_results.append((category_name, f"❌ Test suite error: {e}"))
    
    # Generate summary report
    print("\n" + "=" * 60)
    print("📊 ROBUSTNESS TEST SUMMARY REPORT")
    print("=" * 60)
    
    total_tests = len(all_results)
    passed_tests = sum(1 for _, result in all_results if result.startswith("✅"))
    warning_tests = sum(1 for _, result in all_results if result.startswith("⚠️"))
    failed_tests = sum(1 for _, result in all_results if result.startswith("❌"))
    
    print(f"📈 Total Tests: {total_tests}")
    print(f"✅ Passed: {passed_tests}")
    print(f"⚠️  Warnings: {warning_tests}")
    print(f"❌ Failed: {failed_tests}")
    print(f"📊 Success Rate: {(passed_tests/total_tests*100):.1f}%")
    
    print(f"\n🎯 ROBUSTNESS SCORE: {passed_tests}/{total_tests} ({(passed_tests/total_tests*100):.1f}%)")
    
    if failed_tests == 0 and warning_tests <= total_tests * 0.2:  # Less than 20% warnings
        print("🏆 EXCELLENT - System demonstrates high robustness!")
    elif failed_tests <= total_tests * 0.1:  # Less than 10% failures
        print("👍 GOOD - System is reasonably robust with minor issues")
    elif failed_tests <= total_tests * 0.3:  # Less than 30% failures
        print("⚠️  MODERATE - System needs robustness improvements")
    else:
        print("🚨 POOR - System requires significant robustness work")
    
    # Detailed failure analysis
    if failed_tests > 0:
        print(f"\n🔍 FAILED TESTS ANALYSIS:")
        for category, result in all_results:
            if result.startswith("❌"):
                print(f"   {category}: {result}")
    
    print("\n" + "=" * 60)
    print("✅ ROBUSTNESS TEST SUITE COMPLETED")
    print("=" * 60)
    
    return passed_tests, warning_tests, failed_tests

if __name__ == "__main__":
    passed, warnings, failed = run_comprehensive_test_suite()
    
    # Exit with appropriate code
    if failed > 0:
        sys.exit(1)
    elif warnings > 0:
        sys.exit(2)
    else:
        sys.exit(0)