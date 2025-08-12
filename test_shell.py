#!/usr/bin/env python3
"""Test script for restud shell interaction."""

import subprocess
import time
import os

def test_shell():
    # Start the shell process
    proc = subprocess.Popen(
        ['uv', 'run', 'restud', 'shell'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.getcwd()
    )
    
    try:
        # Send commands
        commands = [
            "help\n",
            "pull 28552\n", 
            "exit\n"
        ]
        
        for cmd in commands:
            proc.stdin.write(cmd)
            proc.stdin.flush()
            time.sleep(2)
        
        # Get output
        stdout, stderr = proc.communicate(timeout=30)
        
        print("STDOUT:")
        print(stdout)
        if stderr:
            print("STDERR:")
            print(stderr)
            
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        print("Process timed out")
        print("STDOUT:", stdout)
        print("STDERR:", stderr)

if __name__ == "__main__":
    test_shell()