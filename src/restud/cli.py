#!/usr/bin/env python3
"""REStud workflow management CLI tool."""

import os
import sys
import subprocess
import tempfile
import shutil
import json
from pathlib import Path
from typing import Optional

try:
    from importlib.resources import files
except ImportError:
    # Fallback for Python < 3.9
    from importlib_resources import files

import click
import requests
import yaml
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.key_binding import KeyBindings

from .render import generate_report, ReportTemplate


def get_template_path(filename):
    """Get path to template file from package resources."""
    try:
        # Use importlib.resources for modern Python
        template_files = files('restud.templates')
        template_file = template_files / filename
        return str(template_file)
    except:
        # Fallback for development
        return os.path.join(os.path.dirname(__file__), 'templates', filename)


def get_current_folder():
    """Get the current folder name (last component)."""
    return os.path.basename(os.getcwd())


def get_git_branch():
    """Get current git branch, if any."""
    try:
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], 
                               capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except:
        return None


def get_git_accepted_tag():
    """Check if 'accepted' tag exists in the repo."""
    try:
        result = subprocess.run(['git', 'tag', '-l', 'accepted'], 
                               capture_output=True, text=True, check=True)
        return result.stdout.strip() == 'accepted'
    except:
        return False


def get_report_status():
    """Check report.yaml status if it exists."""
    if not os.path.exists('report.yaml'):
        return None
    
    try:
        # Load report.yaml
        with open('report.yaml', 'r', encoding='utf-8') as f:
            content = yaml.safe_load(f)
        
        if not content or content.get('version', 1) < 2:
            return "report"  # Old format, can't determine status
            
        # Check DCAS_rules
        dcas_rules = content.get('DCAS_rules', [])
        if not dcas_rules:
            return "report"
            
        # Check if any rules have "no" answers (issues)
        has_issues = False
        for rule in dcas_rules:
            answer = rule.get('answer', '').lower()
            if answer == 'no':
                has_issues = True
                break
                
        return "issues" if has_issues else "good"
        
    except Exception:
        return "report"  # Error reading, just show basic status


def create_shell_prompt():
    """Create a rich shell prompt with status indicators."""
    prompt_parts = []
    
    # Add folder name
    folder = get_current_folder()
    prompt_parts.append(f"[bold blue]{folder}[/bold blue]")
    
    # Add git branch if available
    branch = get_git_branch()
    if branch:
        prompt_parts.append(f"[yellow]({branch})[/yellow]")
    
    # Add report status if report.yaml exists
    report_status = get_report_status()
    if report_status == "good":
        prompt_parts.append("[green]report[/green]")
    elif report_status == "issues":
        prompt_parts.append("[red]report[/red]")
    elif report_status == "report":
        prompt_parts.append("[dim]report[/dim]")
    
    # Add accepted tag if it exists
    if get_git_accepted_tag():
        prompt_parts.append("[bold green]accepted[/bold green]")
    
    # Join with spaces and add the prompt symbol
    return " ".join(prompt_parts) + " [bold]>[/bold] "


def rich_to_html_prompt(rich_markup):
    """Convert rich markup to HTML for prompt-toolkit."""
    # Simple conversion for basic rich markup to HTML
    html = rich_markup
    html = html.replace("[bold blue]", '<ansiblue><b>')
    html = html.replace("[/bold blue]", '</b></ansiblue>')
    html = html.replace("[yellow]", '<ansiyellow>')
    html = html.replace("[/yellow]", '</ansiyellow>')
    html = html.replace("[green]", '<ansigreen>')
    html = html.replace("[/green]", '</ansigreen>')
    html = html.replace("[red]", '<ansired>')
    html = html.replace("[/red]", '</ansired>')
    html = html.replace("[bold green]", '<ansigreen><b>')
    html = html.replace("[/bold green]", '</b></ansigreen>')
    html = html.replace("[dim]", '<ansiblack>')
    html = html.replace("[/dim]", '</ansiblack>')
    html = html.replace("[bold]", '<b>')
    html = html.replace("[/bold]", '</b>')
    return html


@click.group()
@click.pass_context
def cli(ctx):
    """REStud workflow management CLI tool."""
    ctx.ensure_object(dict)




@cli.command()
@click.argument('package_name')
@click.pass_context
def pull(ctx, package_name):
    """Pull a replication package."""
    if not os.path.exists(package_name):
        subprocess.run(['git', 'clone', f'git@github.com:restud-replication-packages/{package_name}.git'], check=True)
        os.chdir(package_name)
    else:
        os.chdir(package_name)
        subprocess.run(['git', 'pull'], check=True)
    
    # Get latest version and switch to it  
    result = subprocess.run(['git', 'branch', '-r'], capture_output=True, text=True, check=True)
    versions = [line.strip() for line in result.stdout.split('\n') if 'version' in line]
    if versions:
        latest_version = max([int(v.split('version')[-1]) for v in versions if 'version' in v])
        subprocess.run(['git', 'switch', f'version{latest_version}'], check=True)


@cli.command()
@click.pass_context
def revise(ctx):
    """Generate revision response."""
    # Get current branch
    result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
    branch_name = result.stdout.strip()
    
    # Select email template based on version
    if branch_name == "version1":
        email_template = get_template_path('response1.txt')
    else:
        email_template = get_template_path('response2.txt')
    
    # Generate response
    tags_file = get_template_path('template-answers.yaml')
    response = generate_report(email_template, 'report.yaml', tags_file)
    
    # Write response to file
    with open('response.txt', 'w') as f:
        f.write(response)
    
    # Copy to clipboard (macOS)
    subprocess.run(['pbcopy'], input=response.encode(), check=True)
    
    # Commit changes
    subprocess.run(['git', 'add', 'report.yaml', 'response.txt'], check=True)
    subprocess.run(['git', 'commit', '-m', 'edit report'], check=True)
    subprocess.run(['git', 'push'], check=True)


@cli.command()
@click.pass_context
def accept(ctx):
    """Generate acceptance message."""
    # Get current branch
    result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
    branch_name = result.stdout.strip()
    
    # Select email template based on version
    if branch_name == "version1":
        email_template = get_template_path('accept1.txt') 
    else:
        email_template = get_template_path('accept2.txt')
    
    # Generate acceptance message
    tags_file = get_template_path('template-answers.yaml')
    acceptance = generate_report(email_template, 'report.yaml', tags_file)
    
    # Write acceptance to file
    with open('accept.txt', 'w') as f:
        f.write(acceptance)
    
    # Copy to clipboard (macOS)
    subprocess.run(['pbcopy'], input=acceptance.encode(), check=True)
    
    # Commit and tag
    subprocess.run(['git', 'add', 'accept.txt'], check=True)
    subprocess.run(['git', 'commit', '-m', 'acceptance message'], check=True)
    subprocess.run(['git', 'tag', 'accepted'], check=True)
    subprocess.run(['git', 'push'], check=True)
    subprocess.run(['git', 'push', '--tags'], check=True)
    
    # Check community status
    _check_community(ctx)



@cli.command()
@click.argument('package_name')
@click.pass_context
def new(ctx, package_name):
    """Create new replication package."""
    os.makedirs(package_name, exist_ok=True)
    os.chdir(package_name)
    subprocess.run(['git', 'init'], check=True)
    subprocess.run(['gh', 'repo', 'create', f'restud-replication-packages/{package_name}', '--private', '--team', 'Replicators'], check=True)
    subprocess.run(['git', 'remote', 'add', 'origin', f'git@github.com:restud-replication-packages/{package_name}.git'], check=True)
    subprocess.run(['git', 'checkout', '-b', 'author'], check=True)


@cli.command()
@click.argument('zenodo_url')
@click.pass_context
def download(ctx, zenodo_url):
    """Download package from Zenodo."""
    branch = get_git_branch()
    if branch != 'author':
        click.echo('You must be on the author branch to download from Zenodo. Changing to author branch now.')
        subprocess.run(['git', 'switch', 'author'], check=True)
    
    _empty_folder()
    
    # Get Zenodo API key
    zenodo_key = _get_zenodo_key()
    
    # Download from Zenodo
    if "preview" in zenodo_url:
        _download_zenodo_preview(zenodo_url)
    else:
        _download_zenodo(zenodo_url, zenodo_key)
    
    # Commit changes
    _commit_changes()
    _check_for_files()
    
    # Check if other branches exist
    result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
    branches = [line.strip() for line in result.stdout.split('\n') if line.strip() and 'author' not in line]
    
    if not branches:
        click.echo('No other branch than author exists')
        subprocess.run(['git', 'commit', '-m', f'initial commit from zenodo {zenodo_url}'], check=True)
        subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], check=True)
        subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
        shutil.copy(get_template_path('report-template.yaml'), 'report.yaml')
    else:
        click.echo('Other branches exist')
        subprocess.run(['git', 'commit', '-m', f'update to zenodo version {zenodo_url}'], check=True)
        subprocess.run(['git', 'push'], check=True)
        
        # Get latest version number
        latest_version = _get_latest_version()
        new_version = latest_version + 1
        subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)
        shutil.copy(get_template_path('report-template.yaml'), 'report.yaml')
    
    _save_zenodo_id(zenodo_url)


@cli.command()
@click.argument('branch_name', required=False)
@click.pass_context
def report(ctx, branch_name):
    """Generate and commit report."""
    # Get current branch if not specified
    if not branch_name:
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
        branch_name = result.stdout.strip()
    
    # Select email template based on version
    if branch_name == "version1":
        email_template = get_template_path('response1.txt')
    else:  
        email_template = get_template_path('response2.txt')
        
    # Generate response
    tags_file = get_template_path('template-answers.yaml')
    response = generate_report(email_template, 'report.yaml', tags_file)
    
    with open('response.txt', 'w') as f:
        f.write(response)
    
    # Commit changes
    subprocess.run(['git', 'add', 'report.yaml', 'response.txt'], check=True)
    subprocess.run(['git', 'commit', '-m', 'update report'], check=True)
    subprocess.run(['git', 'push', 'origin', branch_name], check=True)


@cli.command()
@click.pass_context
def shell(ctx):
    """Start interactive REStud shell."""
    console = Console()
    user_shell = os.environ.get('SHELL', '/bin/bash')
    
    # Welcome message
    welcome_text = Text()
    welcome_text.append("REStud Interactive Shell", style="bold blue")
    welcome_text.append("\nType 'exit' to quit, 'help' for available commands", style="dim")
    welcome_text.append("\nArrow keys, command history, and tab completion are supported", style="dim")
    
    console.print(Panel(welcome_text, border_style="blue"))
    
    # Available commands for completion
    restud_commands = [cmd.name for cmd in cli.commands.values() if cmd.name != 'shell']
    
    # Create command history and tab completion
    history = InMemoryHistory()
    completer = PathCompleter()
    
    # Create key bindings for tab completion
    bindings = KeyBindings()
    
    while True:
        try:
            # Create dynamic prompt with status indicators
            prompt_text = create_shell_prompt()
            html_prompt = rich_to_html_prompt(prompt_text)
            
            # Use prompt-toolkit for readline support with tab completion
            command = prompt(
                HTML(html_prompt),
                history=history,
                completer=completer,
                key_bindings=bindings,
                complete_style='column'
            ).strip()
            
            if not command:
                continue
                
            if command == 'exit':
                break
            
            if command == 'help':
                console.print(f"[bold]Available REStud commands:[/bold] {', '.join(restud_commands)}")
                console.print("[bold]Built-in commands:[/bold] cd")
                console.print("[dim]Other commands are passed to your shell[/dim]")
                continue
                
            # Split command into parts
            parts = command.split()
            command_name = parts[0]
            
            # Check if it's a REStud command
            if command_name in restud_commands:
                try:
                    # Execute REStud command - need to parse arguments properly
                    cmd = cli.commands[command_name]
                    # Create a new context for the subcommand
                    sub_ctx = click.Context(cmd, parent=ctx)
                    
                    # Parse arguments based on command signature
                    if command_name == 'pull' and len(parts) > 1:
                        ctx.invoke(cmd, package_name=parts[1])
                    elif command_name == 'new' and len(parts) > 1:
                        ctx.invoke(cmd, package_name=parts[1])
                    elif command_name == 'download' and len(parts) > 1:
                        ctx.invoke(cmd, zenodo_url=parts[1])
                    elif command_name == 'report' and len(parts) > 1:
                        ctx.invoke(cmd, branch_name=parts[1])
                    elif command_name in ['revise', 'accept']:
                        ctx.invoke(cmd)
                    else:
                        console.print(f"[yellow]Usage: {command_name} [arguments][/yellow]")
                        
                except Exception as e:
                    console.print(f"[red]Error executing REStud command:[/red] {e}")
            elif command_name == 'cd':
                # Special case for cd command - implement in Python
                try:
                    if len(parts) == 1:
                        # cd with no arguments goes to home directory
                        target_dir = os.path.expanduser('~')
                    else:
                        # cd with path argument
                        target_dir = os.path.expanduser(parts[1])
                    
                    # Change directory
                    os.chdir(target_dir)
                    console.print(f"[dim]Changed to: {os.getcwd()}[/dim]")
                    
                except FileNotFoundError:
                    console.print(f"[red]cd: no such file or directory: {parts[1] if len(parts) > 1 else '~'}[/red]")
                except PermissionError:
                    console.print(f"[red]cd: permission denied: {parts[1] if len(parts) > 1 else '~'}[/red]")
                except Exception as e:
                    console.print(f"[red]cd: {e}[/red]")
            else:
                # Pass to user's shell
                try:
                    result = subprocess.run(command, shell=True, check=False)
                    if result.returncode != 0 and result.returncode != 130:  # 130 is Ctrl+C
                        console.print(f"[yellow]Command exited with code {result.returncode}[/yellow]")
                except KeyboardInterrupt:
                    console.print("[yellow]Interrupted[/yellow]")
                    continue
                    
        except (EOFError, KeyboardInterrupt):
            break
    
    console.print("[blue]Exiting REStud shell[/blue]")


# Helper functions
def _get_zenodo_key():
    """Get Zenodo API key from config file."""
    key_file = os.path.expanduser('~/.config/.zenodo_api_key')
    with open(key_file, 'r') as f:
        return f.read().strip()


def _download_zenodo(url, api_key):
    """Download from Zenodo with API key."""
    response = requests.get(f"{url}?access_token={api_key}")
    response.raise_for_status()
    
    with open('repo.zip', 'wb') as f:
        f.write(response.content)
    
    with open('.zenodo', 'w') as f:
        f.write(url)
    
    subprocess.run(['unzip', 'repo.zip'], check=True)
    os.remove('repo.zip')


def _download_zenodo_preview(url):
    """Download Zenodo preview with cookie."""
    cookie_value = _get_cookie()
    headers = {'Cookie': f'session={cookie_value}'}
    
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    with open('repo.zip', 'wb') as f:
        f.write(response.content)
    
    with open('.zenodo', 'w') as f:
        f.write(url)
        
    subprocess.run(['unzip', 'repo.zip'], check=True) 
    os.remove('repo.zip')


def _get_cookie():
    """Get or create Zenodo session cookie."""
    cookie_file = os.path.expanduser('~/.config/restud/restud-cookie.json')
    
    if not os.path.exists(cookie_file):
        _create_cookie()
        
    with open(cookie_file, 'r') as f:
        cookie_data = json.load(f)
    
    # Check if cookie is expired
    from datetime import datetime
    exp_date = datetime.strptime(cookie_data['exp_date'], '%Y-%m-%d')
    if datetime.now() > exp_date:
        _create_cookie()
        with open(cookie_file, 'r') as f:
            cookie_data = json.load(f)
    
    return cookie_data['value']


def _create_cookie():
    """Create new Zenodo session cookie."""
    console = Console()
    
    console.print("\n[yellow]Your REStud cookie either does not exist or expired.[/yellow]")
    console.print("To download preview records, you need to create a new one!")
    
    confirm = Prompt.ask("Create new cookie in ~/.config/restud/restud-cookie.json?", 
                        choices=["y", "n"], default="n", console=console)
    if confirm.lower() != 'y':
        return
    
    # Instructions panel
    instructions = Text()
    instructions.append("To create a new cookie you need:\n", style="bold")
    instructions.append("1. Zenodo session cookie value\n")
    instructions.append("2. Expiration date\n\n")
    instructions.append("Steps to get these:\n", style="bold")
    instructions.append("1. Open zenodo.org and log in\n")
    instructions.append("2. Open developer tools (F12)\n")
    instructions.append("3. Go to Application > Storage > Cookies\n")
    instructions.append("4. Find the 'session' cookie")
    
    console.print(Panel(instructions, title="Cookie Setup Instructions", border_style="green"))
    
    value = Prompt.ask("[bold]Cookie value[/bold]", console=console)
    exp_date = Prompt.ask("[bold]Expiration date (YYYY-MM-DD)[/bold]", console=console)
    
    cookie_data = {
        "name": "session",
        "value": value,
        "exp_date": exp_date
    }
    
    cookie_file = os.path.expanduser('~/.config/restud/restud-cookie.json')
    os.makedirs(os.path.dirname(cookie_file), exist_ok=True)
    
    with open(cookie_file, 'w') as f:
        json.dump(cookie_data, f)
    
    console.print("[green]Cookie saved successfully![/green]")


def _empty_folder():
    """Remove directories from current folder.""" 
    dirs = [d for d in os.listdir('.') if os.path.isdir(d)]
    if dirs:
        click.echo("Removing previous directories!")
        for d in dirs:
            if d not in ['.git']:
                shutil.rmtree(d)
    else:
        click.echo("No directories in the folder!")


def _commit_changes():
    """Commit changes, ignoring large files."""
    # Find large files and add to gitignore
    large_files = []
    for root, dirs, files in os.walk('.'):
        for file in files:
            filepath = os.path.join(root, file)
            if os.path.getsize(filepath) > 20 * 1024 * 1024:  # 20MB
                large_files.append(filepath[2:])  # Remove './' prefix
    
    if large_files:
        with open('.gitignore', 'w') as f:
            f.write('\n'.join(large_files))
    
    subprocess.run(['git', 'add', '.'], check=True)


def _check_for_files():
    """Check for empty files and prompt user."""
    console = Console()
    empty_files = []
    for root, dirs, files in os.walk('.'):
        for file in files:
            filepath = os.path.join(root, file)
            if os.path.getsize(filepath) == 0:
                empty_files.append(filepath)
    
    if empty_files:
        console.print(f"[yellow]Total empty files: {len(empty_files)}[/yellow]")
        console.print(f"[dim]Empty files: {empty_files}[/dim]")
        interrupt = Prompt.ask("Interrupt?", choices=["y", "n"], default="n", console=console)
        if interrupt.lower() != 'n':
            return False
    else:
        console.print("[green]No empty files[/green]")
    return True


def _get_latest_version():
    """Get the latest version number from git branches."""
    result = subprocess.run(['git', 'branch', '-r'], capture_output=True, text=True, check=True)
    versions = []
    for line in result.stdout.split('\n'):
        if 'version' in line:
            try:
                version_num = int(line.split('version')[-1].strip())
                versions.append(version_num)
            except ValueError:
                continue
    return max(versions) if versions else 0


def _save_zenodo_id(url):
    """Save Zenodo ID for later use."""
    # Extract ID from URL
    import re
    match = re.search(r'/(\d+)/', url)
    if match:
        zenodo_id = match.group(1)
        with open('.zenodo_id', 'w') as f:
            f.write(zenodo_id)


def _check_community(ctx):
    """Check and manage community membership."""
    console = Console()
    
    # Get Zenodo ID
    with open('.zenodo', 'r') as f:
        url = f.read().strip()
    
    import re
    match = re.search(r'/(\d+)/', url)
    if not match:
        console.print("[red]Could not extract Zenodo ID[/red]")
        return
        
    zenodo_id = match.group(1)
    
    # Check community membership
    response = requests.get(f"https://zenodo.org/api/records/{zenodo_id}/communities")
    if 'restud-replication' not in response.text:
        console.print(f"\n[yellow]Replication package {zenodo_id} is not part of REStud community.[/yellow]")
        confirm = Prompt.ask("Accept into the community?", choices=["y", "n"], default="n", console=console)
        if confirm.lower() == 'y':
            _community_accept(zenodo_id)
    else:
        console.print("\n[green]Already part of REStud community![/green]")


def _community_accept(zenodo_id):
    """Accept package into REStud community."""
    api_key = _get_zenodo_key()

    cookie_value = _get_cookie()
    headers = {'Cookie': f'session={cookie_value}'}
    
    url = _get_accept_request(zenodo_id, api_key, headers)
    requests.post(f"{url}?access_token={api_key}", headers=headers)

def _get_accept_request(zenodo_id, api_key, headers):
    """Get the acceptance request URL for a Zenodo record."""
    url = f"https://zenodo.org/api/communities/451be469-757a-4121-8792-af8ffc4461fb/requests?size=50&is_open=true&access_token={api_key}"
    response = requests.get(url, headers=headers)
    requests_data = response.json()
    link = [item['links']['actions']['accept'] for item in requests_data['hits']['hits'] if item['topic']['record'] == zenodo_id]
    return link[0]

def main():
    """Main entry point."""
    cli()


if __name__ == '__main__':
    main()
