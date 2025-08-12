#!/usr/bin/env python3
"""Show examples of the enhanced shell prompt in different states."""

import os
from rich.console import Console

# Mock different scenarios for demonstration
def show_prompt_examples():
    console = Console()
    
    console.print("\n[bold]REStud Shell Prompt Examples:[/bold]\n")
    
    # Example 1: Basic folder + git branch
    console.print("1. Basic (folder + git branch):")
    console.print("   [bold blue]workflow[/bold blue] [yellow](experiment)[/yellow] [bold]>[/bold] ")
    
    # Example 2: With report issues (red)
    console.print("\n2. With report issues:")
    console.print("   [bold blue]28552[/bold blue] [yellow](version1)[/yellow] [red]report[/red] [bold]>[/bold] ")
    
    # Example 3: With good report (green)
    console.print("\n3. With good report:")
    console.print("   [bold blue]28552[/bold blue] [yellow](version1)[/yellow] [green]report[/green] [bold]>[/bold] ")
    
    # Example 4: With accepted tag
    console.print("\n4. With accepted tag:")
    console.print("   [bold blue]28552[/bold blue] [yellow](version1)[/yellow] [green]report[/green] [bold green]accepted[/bold green] [bold]>[/bold] ")
    
    # Example 5: No git repo
    console.print("\n5. No git repository:")
    console.print("   [bold blue]myproject[/bold blue] [bold]>[/bold] ")
    
    # Example 6: Old report format
    console.print("\n6. Old report format (can't determine status):")
    console.print("   [bold blue]old-package[/bold blue] [yellow](main)[/yellow] [dim]report[/dim] [bold]>[/bold] ")
    
    console.print("\n[dim]Colors: folder=blue, branch=yellow, good report=green, issues=red, accepted=bold green[/dim]")

if __name__ == "__main__":
    show_prompt_examples()