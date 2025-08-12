#!/usr/bin/env python3
"""Test the enhanced shell prompt functionality."""

import os
import subprocess
from src.restud.cli import get_current_folder, get_git_branch, get_git_accepted_tag, get_report_status, create_shell_prompt

def test_prompt_functions():
    print(f"Current folder: {get_current_folder()}")
    print(f"Git branch: {get_git_branch()}")
    print(f"Git accepted tag: {get_git_accepted_tag()}")
    print(f"Report status: {get_report_status()}")
    print(f"Shell prompt: {create_shell_prompt()}")

if __name__ == "__main__":
    test_prompt_functions()