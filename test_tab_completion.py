#!/usr/bin/env python3
"""Test tab completion functionality."""

from prompt_toolkit import prompt
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.formatted_text import HTML

def test_tab_completion():
    print("Testing tab completion - type a partial filename and press Tab")
    print("Type 'exit' to quit")
    
    completer = PathCompleter()
    
    while True:
        try:
            command = prompt(
                HTML('<ansiblue><b>test</b></ansiblue> > '),
                completer=completer,
                complete_style='column'
            ).strip()
            
            if command == 'exit':
                break
                
            print(f"You entered: {command}")
            
        except (EOFError, KeyboardInterrupt):
            break
    
    print("Exiting tab completion test")

if __name__ == "__main__":
    test_tab_completion()