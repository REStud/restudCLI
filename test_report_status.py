#!/usr/bin/env python3
"""Test the report status logic fix."""

import os
import yaml
from src.restud.cli import get_report_status

def test_report_status():
    # Test 1: Report with "no" answer should show issues (red)
    report_with_no = {
        'version': 2,
        'DCAS_rules': [
            {'answer': 'yes', 'description': 'Good rule'},
            {'answer': 'no', 'description': 'Bad rule'},  # This should trigger red
            {'answer': 'na', 'description': 'Not applicable'}
        ]
    }
    
    with open('report.yaml', 'w') as f:
        yaml.dump(report_with_no, f)
    
    status = get_report_status()
    print(f"Report with 'no' answer: {status}")
    assert status == "issues", f"Expected 'issues', got '{status}'"
    
    # Test 2: Report with only "yes" and "na" should show good (green)
    report_all_good = {
        'version': 2,
        'DCAS_rules': [
            {'answer': 'yes', 'description': 'Good rule'},
            {'answer': 'na', 'description': 'Not applicable'},
            {'answer': 'yes', 'description': 'Another good rule'}
        ]
    }
    
    with open('report.yaml', 'w') as f:
        yaml.dump(report_all_good, f)
    
    status = get_report_status()
    print(f"Report with only 'yes'/'na': {status}")
    assert status == "good", f"Expected 'good', got '{status}'"
    
    # Test 3: Report with "maybe" should show good (not an issue)
    report_with_maybe = {
        'version': 2,
        'DCAS_rules': [
            {'answer': 'yes', 'description': 'Good rule'},
            {'answer': 'maybe', 'description': 'Uncertain rule'},
            {'answer': 'na', 'description': 'Not applicable'}
        ]
    }
    
    with open('report.yaml', 'w') as f:
        yaml.dump(report_with_maybe, f)
    
    status = get_report_status()
    print(f"Report with 'maybe' answer: {status}")
    assert status == "good", f"Expected 'good', got '{status}'"
    
    # Cleanup
    os.remove('report.yaml')
    print("All tests passed!")

if __name__ == "__main__":
    test_report_status()