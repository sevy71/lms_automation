#!/usr/bin/env python3
"""
Advanced Stress Testing Scenarios for LMS Project
Tests system behavior under extreme conditions
"""

import os
import sys
import time
import threading
import multiprocessing
import requests
import random
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta

# Add current directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lms_automation'))

def stress_test_concurrent_picks():
    """Simulate many users making picks simultaneously"""
    print("🏃‍♂️ Stress Testing: Concurrent Pick Submissions")
    
    try:
        from lms_automation.app import app, db, make_pick_token
        from lms_automation.models import Player, Round, Pick, Fixture
        
        with app.app_context():
            # Clean up any existing test data
            Player.query.filter(Player.name.like('stress_test_%')).delete()
            db.session.commit()
            
            # Create test players
            test_players = []
            for i in range(50):
                player = Player(name=f"stress_test_player_{i}", whatsapp_number=f"+123456789{i:02d}")
                test_players.append(player)
                db.session.add(player)
            
            # Create a test round
            test_round = Round(
                round_number=999,
                start_date=datetime.now().date(),
                end_date=(datetime.now() + timedelta(days=1)).date(),
                status='open'
            )
            db.session.add(test_round)
            
            # Create test fixtures
            teams = ['Arsenal', 'Chelsea', 'Liverpool', 'Manchester City', 'Tottenham']
            for i in range(5):
                fixture = Fixture(
                    event_id=f"stress_test_{i}",
                    home_team=teams[i],
                    away_team=teams[(i+1) % len(teams)],
                    date=datetime.now() + timedelta(hours=2),
                    time="15:00",
                    status="scheduled"
                )
                db.session.add(fixture)
            
            db.session.commit()
            
            # Function to simulate user making a pick
            def make_concurrent_pick(player_id):
                try:
                    with app.test_client() as client:
                        token = make_pick_token(player_id, test_round.id)
                        team = random.choice(teams)
                        
                        response = client.post(f'/l/{token}', data={
                            'team_picked': team
                        }, follow_redirects=True)
                        
                        return response.status_code < 400
                except Exception as e:
                    print(f"Pick error for player {player_id}: {e}")
                    return False
            
            # Execute concurrent picks
            start_time = time.time()
            with ThreadPoolExecutor(max_workers=20) as executor:
                player_ids = [p.id for p in test_players]
                futures = [executor.submit(make_concurrent_pick, pid) for pid in player_ids]
                results = [future.result() for future in as_completed(futures)]
            
            execution_time = time.time() - start_time
            success_count = sum(results)
            
            # Check database consistency
            pick_count = Pick.query.filter_by(round_id=test_round.id).count()
            
            print(f"  ✅ Concurrent picks completed in {execution_time:.2f} seconds")
            print(f"  📊 Successful submissions: {success_count}/{len(test_players)}")
            print(f"  🗄️  Picks in database: {pick_count}")
            
            # Clean up test data
            Pick.query.filter_by(round_id=test_round.id).delete()
            Player.query.filter(Player.name.like('stress_test_%')).delete()
            db.session.delete(test_round)
            Fixture.query.filter(Fixture.event_id.like('stress_test_%')).delete()
            db.session.commit()
            
            return success_count >= len(test_players) * 0.8  # 80% success rate acceptable
            
    except Exception as e:
        print(f"  ❌ Concurrent picks stress test failed: {e}")
        return False

def stress_test_api_endpoints():
    """Test API endpoints under high load"""
    print("🌐 Stress Testing: API Endpoints Under Load")
    
    try:
        from lms_automation.app import app
        
        endpoints = [
            '/',
            '/standings', 
            '/register_player',
            '/admin_dashboard'  # This might fail due to missing tables, but we test resilience
        ]
        
        def hit_endpoint(endpoint):
            try:
                with app.test_client() as client:
                    start = time.time()
                    response = client.get(endpoint)
                    duration = time.time() - start
                    return {
                        'endpoint': endpoint,
                        'status': response.status_code,
                        'duration': duration,
                        'success': response.status_code < 500
                    }
            except Exception as e:
                return {
                    'endpoint': endpoint,
                    'status': 500,
                    'duration': 0,
                    'success': False,
                    'error': str(e)
                }
        
        # Generate load: 100 requests across endpoints
        requests_to_make = []
        for _ in range(100):
            requests_to_make.append(random.choice(endpoints))
        
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(hit_endpoint, endpoint) for endpoint in requests_to_make]
            results = [future.result() for future in as_completed(futures)]
        
        total_time = time.time() - start_time
        
        # Analyze results
        successful = sum(1 for r in results if r['success'])
        total_requests = len(results)
        avg_response_time = sum(r['duration'] for r in results) / total_requests
        requests_per_second = total_requests / total_time
        
        print(f"  ⚡ Processed {total_requests} requests in {total_time:.2f} seconds")
        print(f"  📈 Requests per second: {requests_per_second:.1f}")
        print(f"  ✅ Success rate: {successful}/{total_requests} ({successful/total_requests*100:.1f}%)")
        print(f"  ⏱️  Average response time: {avg_response_time*1000:.1f}ms")
        
        # Group by endpoint
        endpoint_stats = {}
        for result in results:
            ep = result['endpoint']
            if ep not in endpoint_stats:
                endpoint_stats[ep] = {'success': 0, 'total': 0, 'total_time': 0}
            endpoint_stats[ep]['total'] += 1
            endpoint_stats[ep]['total_time'] += result['duration']
            if result['success']:
                endpoint_stats[ep]['success'] += 1
        
        print("  📋 Per-endpoint statistics:")
        for endpoint, stats in endpoint_stats.items():
            success_rate = stats['success'] / stats['total'] * 100
            avg_time = stats['total_time'] / stats['total'] * 1000
            print(f"     {endpoint}: {success_rate:.1f}% success, {avg_time:.1f}ms avg")
        
        return successful >= total_requests * 0.7  # 70% success rate acceptable under load
        
    except Exception as e:
        print(f"  ❌ API stress test failed: {e}")
        return False

def stress_test_database_operations():
    """Test database under heavy concurrent operations"""
    print("🗄️  Stress Testing: Database Under Heavy Load")
    
    try:
        from lms_automation.app import app, db
        from lms_automation.models import Player
        
        def database_operation_worker(worker_id):
            operations_completed = 0
            errors = 0
            
            with app.app_context():
                for i in range(10):  # Each worker does 10 operations
                    try:
                        # Create player
                        player_name = f"stress_db_worker_{worker_id}_{i}_{int(time.time()*1000000) % 1000000}"
                        player = Player(name=player_name, whatsapp_number=f"+1555{worker_id:03d}{i:03d}")
                        db.session.add(player)
                        db.session.commit()
                        
                        # Read players
                        players = Player.query.filter(Player.name.like('stress_db_%')).limit(10).all()
                        
                        # Update player
                        if players:
                            players[0].whatsapp_number = f"+1999{worker_id:03d}{i:03d}"
                            db.session.commit()
                        
                        operations_completed += 1
                        
                        # Small delay to prevent overwhelming
                        time.sleep(0.01)
                        
                    except Exception as e:
                        db.session.rollback()
                        errors += 1
                        print(f"    DB operation error (worker {worker_id}): {e}")
            
            return operations_completed, errors
        
        # Launch multiple workers
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(database_operation_worker, i) for i in range(5)]
            results = [future.result() for future in as_completed(futures)]
        
        execution_time = time.time() - start_time
        
        total_operations = sum(ops for ops, _ in results)
        total_errors = sum(errors for _, errors in results)
        
        print(f"  ⚡ Completed {total_operations} database operations in {execution_time:.2f} seconds")
        print(f"  📊 Operations per second: {total_operations/execution_time:.1f}")
        print(f"  ❌ Errors: {total_errors}")
        print(f"  ✅ Success rate: {(total_operations/(total_operations+total_errors))*100:.1f}%")
        
        # Clean up test data
        with app.app_context():
            Player.query.filter(Player.name.like('stress_db_%')).delete()
            db.session.commit()
        
        return total_errors < total_operations * 0.1  # Less than 10% error rate acceptable
        
    except Exception as e:
        print(f"  ❌ Database stress test failed: {e}")
        return False

def stress_test_memory_usage():
    """Test for memory leaks under sustained load"""
    print("💾 Stress Testing: Memory Leak Detection")
    
    try:
        import psutil
        import gc
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        from lms_automation.app import app
        
        # Simulate sustained application usage
        memory_samples = []
        
        with app.test_client() as client:
            for i in range(100):
                # Simulate user activity
                client.get('/')
                client.get('/standings')
                client.get('/register_player')
                
                # Sample memory every 10 iterations
                if i % 10 == 0:
                    current_memory = process.memory_info().rss / 1024 / 1024
                    memory_samples.append(current_memory)
                    
                # Force garbage collection periodically
                if i % 50 == 0:
                    gc.collect()
        
        final_memory = process.memory_info().rss / 1024 / 1024
        memory_growth = final_memory - initial_memory
        
        # Analyze memory trend
        memory_trend = []
        for i in range(1, len(memory_samples)):
            trend = memory_samples[i] - memory_samples[i-1]
            memory_trend.append(trend)
        
        avg_growth_per_sample = sum(memory_trend) / len(memory_trend) if memory_trend else 0
        
        print(f"  📊 Initial memory: {initial_memory:.2f} MB")
        print(f"  📊 Final memory: {final_memory:.2f} MB")
        print(f"  📈 Total growth: {memory_growth:.2f} MB")
        print(f"  📈 Average growth per sample: {avg_growth_per_sample:.3f} MB")
        
        # Check for significant memory leaks
        if memory_growth < 10:  # Less than 10MB growth acceptable
            print(f"  ✅ Memory usage stable")
            return True
        elif memory_growth < 25:  # 10-25MB might be acceptable
            print(f"  ⚠️  Moderate memory growth detected")
            return True
        else:
            print(f"  ❌ Significant memory leak detected")
            return False
            
    except ImportError:
        print("  ⚠️  psutil not available - skipping memory stress test")
        return True
    except Exception as e:
        print(f"  ❌ Memory stress test failed: {e}")
        return False

def run_advanced_stress_tests():
    """Run all advanced stress test scenarios"""
    print("=" * 60)
    print("🔥 STARTING ADVANCED STRESS TEST SCENARIOS")
    print("=" * 60)
    
    test_scenarios = [
        ("Concurrent Pick Submissions", stress_test_concurrent_picks),
        ("API Endpoints Under Load", stress_test_api_endpoints),
        ("Database Heavy Operations", stress_test_database_operations),
        ("Memory Leak Detection", stress_test_memory_usage),
    ]
    
    results = []
    
    for scenario_name, test_function in test_scenarios:
        print(f"\n{'=' * 20} {scenario_name} {'=' * 20}")
        start_time = time.time()
        
        try:
            success = test_function()
            elapsed = time.time() - start_time
            
            result_status = "✅ PASSED" if success else "❌ FAILED"
            print(f"  {result_status} in {elapsed:.2f} seconds")
            results.append((scenario_name, success))
            
        except Exception as e:
            print(f"  ❌ FAILED with exception: {e}")
            results.append((scenario_name, False))
    
    # Generate stress test report
    print("\n" + "=" * 60)
    print("🔥 STRESS TEST SUMMARY REPORT")
    print("=" * 60)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    print(f"📊 Stress Tests Passed: {passed}/{total} ({passed/total*100:.1f}%)")
    
    print("\n📋 Detailed Results:")
    for scenario, success in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"   {scenario}: {status}")
    
    if passed == total:
        print("\n🏆 OUTSTANDING - System handles extreme stress excellently!")
    elif passed >= total * 0.8:
        print("\n👍 EXCELLENT - System demonstrates high stress tolerance")
    elif passed >= total * 0.6:
        print("\n⚠️  GOOD - System handles moderate stress well")
    else:
        print("\n🚨 NEEDS IMPROVEMENT - System struggles under stress")
    
    print("\n💡 RECOMMENDATIONS:")
    if passed < total:
        failed_tests = [name for name, success in results if not success]
        print("   • Focus on improving:", ", ".join(failed_tests))
        print("   • Consider implementing connection pooling")
        print("   • Add request rate limiting")
        print("   • Implement better error handling and recovery")
        print("   • Consider scaling database solutions")
    else:
        print("   • System is performing well under stress")
        print("   • Consider load balancing for production")
        print("   • Monitor memory usage in production")
        print("   • Implement proactive health checks")
    
    print("\n" + "=" * 60)
    print("✅ STRESS TEST SCENARIOS COMPLETED")
    print("=" * 60)
    
    return passed, total

if __name__ == "__main__":
    passed, total = run_advanced_stress_tests()
    
    # Exit with appropriate code
    if passed == total:
        sys.exit(0)  # All passed
    elif passed >= total * 0.5:
        sys.exit(1)  # Some failures but not critical
    else:
        sys.exit(2)  # Critical failures