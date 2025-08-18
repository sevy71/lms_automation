#!/usr/bin/env python3
"""
Test script for phone number validation
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import is_valid_phone_number, to_e164_digits

def test_phone_numbers():
    """Test various phone number formats"""
    
    test_cases = [
        # Valid UK numbers
        ("447545851594", True, "+447545851594"),  # Your number
        ("07545851594", True, "+447545851594"),   # UK local format
        ("+447545851594", True, "+447545851594"),  # Already international
        ("44 7545 851594", True, "+447545851594"), # With spaces
        
        # Valid international numbers  
        ("1234567890", True, "+1234567890"),      # 10 digits
        ("+33123456789", True, "+33123456789"),   # French
        
        # Invalid numbers
        ("", False, ""),                          # Empty
        ("123", False, ""),                       # Too short
        ("12345678901234567890", False, ""),      # Too long
        ("07545", False, ""),                     # UK format too short
        ("abc123def", False, ""),                 # Letters (too short after extraction)
    ]
    
    print("🧪 Testing phone number validation...")
    
    all_passed = True
    for phone, expected_valid, expected_e164 in test_cases:
        is_valid = is_valid_phone_number(phone)
        
        if expected_valid:
            e164_result = to_e164_digits(phone)
        else:
            e164_result = ""
        
        # Test validation
        if is_valid != expected_valid:
            print(f"❌ Validation FAILED for '{phone}': expected {expected_valid}, got {is_valid}")
            all_passed = False
        else:
            print(f"✅ Validation OK for '{phone}': {is_valid}")
        
        # Test E164 formatting (only for valid numbers)
        if expected_valid and e164_result != expected_e164:
            print(f"❌ E164 format FAILED for '{phone}': expected '{expected_e164}', got '{e164_result}'")
            all_passed = False
        elif expected_valid:
            print(f"✅ E164 format OK for '{phone}': '{e164_result}'")
    
    print("\n" + "="*50)
    if all_passed:
        print("🎉 All phone number tests PASSED!")
        return True
    else:
        print("❌ Some phone number tests FAILED!")
        return False

if __name__ == "__main__":
    success = test_phone_numbers()
    if not success:
        exit(1)