#!/usr/bin/env python3
"""REStud workflow management CLI tool."""

import os
import sys
import subprocess
from subprocess import CalledProcessError
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
import toml
from tqdm import tqdm
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import prompt
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.key_binding import KeyBindings

from restud.render_jinja2 import ReportRenderer

# GitHub organization for replication packages
GITHUB_ORG = 'restud-replication-packages'


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
        # Load report.yaml with template anchors
        from yamlcore import CoreLoader
        tags_file = get_template_path('template-answers.yaml')
        with open('report.yaml', 'r', encoding='utf-8') as f_report, open(tags_file, 'r', encoding='utf-8') as f_tags:
            combined = f_tags.read() + '\n' + f_report.read()
        content = yaml.load(combined, Loader=CoreLoader)

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
    """REStud workflow management CLI tool.

    Commands:
      pull              Pull a replication package from GitHub
      new               Create a new replication package
      download          Download and import package files from Zenodo
      download-withid   Download from Zenodo draft using record ID
      revise            Generate revision response and push changes
      report            Generate and commit report with optional branch
      accept            Generate acceptance message with optional commit
      shell             Start interactive REStud shell with command history

    Use 'restud COMMAND --help' for more information on a command."""
    ctx.ensure_object(dict)




@cli.command()
@click.argument('package_name')
@click.pass_context
def pull(ctx, package_name):
    """Pull a replication package.

    Clones the repository if it doesn't exist locally, or pulls latest changes.
    Automatically switches to the latest version branch.

    Args:
        PACKAGE_NAME: Name of the replication package to pull
    """
    if not os.path.exists(package_name):
        subprocess.run(['git', 'clone', f'git@github.com:{GITHUB_ORG}/{package_name}.git'], check=True)
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
@click.option('--no-commit', is_flag=True, help='Generate revision response without committing and pushing')
@click.pass_context
def revise(ctx, no_commit):
    """Generate revision response.

    Creates a revision response based on the current version branch and report.yaml.
    Automatically selects the appropriate template, commits changes, and copies to clipboard.
    Works on version1 or version2+ branches with different templates.

    Options:
        --no-commit    Generate response without committing and pushing
    """
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

    # Commit changes (unless --no-commit flag is set)
    if not no_commit:
        subprocess.run(['git', 'add', 'report.yaml', 'response.txt'], check=True)
        subprocess.run(['git', 'commit', '-m', 'edit report'], check=True)
        subprocess.run(['git', 'push'], check=True)
    else:
        console = Console()
        console.print("[yellow]Revision response generated without committing. Files ready for review.[/yellow]")


@cli.command()
@click.option('--no-commit', is_flag=True, help='Generate acceptance message without committing and pushing')
@click.pass_context
def accept(ctx, no_commit):
    """Generate acceptance message.

    Creates an acceptance message based on the current version branch and report.toml.
    Automatically selects the appropriate Jinja2 template, commits changes, tags as 'accepted',
    and copies to clipboard (unless --no-commit is used).

    Options:
        --no-commit    Generate message without committing, pushing, or tagging
    """
    # Get current branch
    result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
    branch_name = result.stdout.strip()

    # Initialize renderer
    templates_dir = get_template_path('.')
    renderer = ReportRenderer(templates_dir)

    # Validate report.toml
    is_valid, msg = renderer.validate_toml('report.toml')
    if not is_valid:
        click.echo(f"[ERROR] {msg}", err=True)
        return

    # Select template based on version
    if branch_name == "version1":
        template_name = 'accept1.jinja2'
    else:
        template_name = 'accept2.jinja2'

    # Generate acceptance message
    acceptance = renderer.generate_report('report.toml', template_name)

    # Write acceptance to file
    with open('accept.txt', 'w') as f:
        f.write(acceptance)

    # Copy to clipboard (macOS)
    subprocess.run(['pbcopy'], input=acceptance.encode(), check=True)

    # Commit and tag (unless --no-commit flag is set)
    if not no_commit:
        subprocess.run(['git', 'add', 'accept.txt'], check=True)

        # Check if there are staged changes to commit
        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        if status_result.stdout.strip():
            # There are changes to commit
            subprocess.run(['git', 'commit', '-m', 'acceptance message'], check=True)
            subprocess.run(['git', 'tag', 'accepted'], check=True)
            subprocess.run(['git', 'push'], check=True)
            subprocess.run(['git', 'push', '--tags'], check=True)
        else:
            console = Console()
            console.print("[yellow]No changes to commit. Accept.txt is already up to date.[/yellow]")
            subprocess.run(['git', 'tag', 'accepted'], check=True)
            subprocess.run(['git', 'push', '--tags'], check=True)

        # Check community status
        _check_community(ctx)
    else:
        console = Console()
        console.print("[yellow]Acceptance message generated without committing. Files ready for review.[/yellow]")



@cli.command()
@click.argument('package_name')
@click.pass_context
def new(ctx, package_name):
    """Create new replication package.

    Initializes a new local repository with report.toml template, creates a remote GitHub
    repository in the restud-replication-packages organization, and sets up the 'author' branch.

    Args:
        PACKAGE_NAME: Name for the new replication package
    """
    os.makedirs(package_name, exist_ok=True)
    os.chdir(package_name)
    subprocess.run(['git', 'init'], check=True)

    # Try to create the repo, but continue if it already exists
    result = subprocess.run(['gh', 'repo', 'create', f'{GITHUB_ORG}/{package_name}', '--private', '--team', 'Replicators'], capture_output=True, text=True)
    if result.returncode != 0 and 'already exists' not in result.stderr:
        click.echo(f"Error creating repository: {result.stderr}", err=True)

    # Add or update the remote
    remote_url = f'git@github.com:{GITHUB_ORG}/{package_name}.git'
    result = subprocess.run(['git', 'remote', 'add', 'origin', remote_url], capture_output=True, text=True)
    if result.returncode != 0:
        # Remote already exists, update it
        subprocess.run(['git', 'remote', 'set-url', 'origin', remote_url], check=True)

    subprocess.run(['git', 'checkout', '-b', 'author'], check=True)

    # Try to push, if it fails because remote has content, pull first then push
    result = subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], capture_output=True, text=True)
    if result.returncode != 0:
        if 'rejected' in result.stderr or 'fetch first' in result.stderr:
            # Remote exists with content, fetch and merge
            click.echo("Repository already exists remotely. Pulling existing content...")
            subprocess.run(['git', 'fetch', 'origin', 'author'], check=True)
            subprocess.run(['git', 'merge', 'origin/author', '--allow-unrelated-histories'], check=True)
            subprocess.run(['git', 'push', 'origin', 'author'], check=True)
        else:
            raise CalledProcessError(result.returncode, result.args)


@cli.command()
@click.argument('zenodo_url')
@click.pass_context
def download_withurl(ctx, zenodo_url):
    """Download package from Zenodo via URL.

    Downloads and imports replication package files from Zenodo (published or preview records).
    Extracts files, removes large files from git tracking, and creates version branches.
    Requires Zenodo API key in ~/.config/.zenodo_api_key for published records.

    Args:
        ZENODO_URL: URL to the Zenodo record or preview record
    """
    branch = get_git_branch()
    if branch != 'author':
        click.echo('You must be on the author branch to download from Zenodo. Changing to author branch now.')
        # Check for uncommitted changes to tracked files (ignore untracked files)
        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        # Filter out untracked files (lines starting with ??)
        committed_changes = [line for line in status_result.stdout.split('\n') if line and not line.startswith('??')]
        if committed_changes:
            click.echo('[ERROR] You have uncommitted changes. Please commit or discard them before downloading.', err=True)
            sys.exit(1)
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

    # Check if there are staged changes to commit
    status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
    has_changes = status_result.stdout.strip() != ""

    if not has_changes:
        click.echo('No changes detected. Files are already up to date.')
        # Still checkout the latest version branch even if no changes
        result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
        branches = [line.strip() for line in result.stdout.split('\n') if line.strip() and 'author' not in line and 'version' in line]
        if branches:
            latest_version = _get_latest_version()
            if latest_version > 0:
                subprocess.run(['git', 'checkout', f'version{latest_version}'], check=True)
                click.echo(f'Switched to version{latest_version}')
        _save_zenodo_metadata(zenodo_url)
        return

    # Check if other branches exist (only needed if there are changes to commit)
    result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
    # Filter for local branches only (exclude remote branches and author)
    branches = [line.strip() for line in result.stdout.split('\n') if line.strip() and 'author' not in line and not line.strip().startswith('remotes/')]

    if not branches:
        click.echo('No other branch than author exists')
        subprocess.run(['git', 'commit', '-m', f'initial commit from zenodo {zenodo_url}'], check=True)
        subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], check=True)
        subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
    else:
        click.echo('Other branches exist')
        subprocess.run(['git', 'commit', '-m', f'update to zenodo version {zenodo_url}'], check=True)
        subprocess.run(['git', 'push'], check=True)

        # Get latest version number
        latest_version = _get_latest_version()
        new_version = latest_version + 1

        # Create new version branch
        subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)

        # Copy report.yaml from previous version branch and commit it
        _copy_report_from_previous_version(latest_version)

        # Push the new version branch
        subprocess.run(['git', 'push', '-u', 'origin', f'version{new_version}'], check=True)

    _save_zenodo_metadata(zenodo_url)


@cli.command()
@click.argument('record_id')
@click.pass_context
def download(ctx, record_id):
    """Download package from Zenodo draft using record ID.

    Downloads from a Zenodo draft record by ID. Lists available files and prompts for
    selection (or auto-selects if only one file). Requires valid Zenodo session cookie.

    Args:
        RECORD_ID: Zenodo draft record ID (numeric)
    """
    # Get Zenodo API key
    zenodo_key = _get_zenodo_key()

    # Query the draft API to get available files
    console = Console()
    console.print(f"[blue]Fetching file list for record {record_id}...[/blue]")

    api_url = f"https://zenodo.org/api/records/{record_id}/draft"
    response = requests.get(f"{api_url}?access_token={zenodo_key}")

    if response.status_code != 200:
        console.print(f"[red]Error: Could not access draft record {record_id}[/red]")
        console.print(f"[red]Status code: {response.status_code}[/red]")
        console.print(f"[red]Response: {response.text}[/red]")
        sys.exit(1)

    data = response.json()

    # Get files from the response
    if 'files' not in data or not data['files']:
        console.print("[red]Error: No files found in this draft[/red]")
        sys.exit(1)

    files = data['files']

    # Download all files
    console.print(f"[yellow]Found {len(files)} file(s):[/yellow]")
    for idx, file_info in enumerate(files, 1):
        size_mb = file_info.get('size', 0) / (1024 * 1024)
        console.print(f"  {idx}. {file_info['key']} ({size_mb:.2f} MB)")

    # If only one file, process directly
    if len(files) == 1:
        filename = files[0]['key']
        download_url = f"https://zenodo.org/api/records/{record_id}/draft/files/{filename}/content"
        console.print(f"[blue]Downloading {filename}...[/blue]")

        branch = get_git_branch()
        if branch != 'author':
            click.echo('You must be on the author branch to download from Zenodo. Changing to author branch now.')
            status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
            committed_changes = [line for line in status_result.stdout.split('\n') if line and not line.startswith('??')]
            if committed_changes:
                click.echo('[ERROR] You have uncommitted changes. Please commit or discard them before downloading.', err=True)
                sys.exit(1)
            subprocess.run(['git', 'switch', 'author'], check=True)

        _empty_folder()

        if "preview" in download_url:
            _download_zenodo_preview(download_url)
        else:
            _download_zenodo(download_url, zenodo_key)

        _commit_changes()
        _check_for_files()

        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        has_changes = status_result.stdout.strip() != ""

        result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
        branches = [line.strip() for line in result.stdout.split('\n') if line.strip() and 'author' not in line and not line.strip().startswith('remotes/')]

        if not branches:
            # First download: no version branches exist
            console.print('First download: creating version1')
            subprocess.run(['git', 'commit', '-m', f'initial commit from zenodo'], check=True)
            subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], check=True)
            subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
            subprocess.run(['git', 'push', '-u', 'origin', 'version1'], check=True)
            # Create initial report.toml template on version1
            shutil.copy(get_template_path('report-sample.toml'), 'report.toml')
            _add_manuscript_id_to_report()
            subprocess.run(['git', 'add', 'report.toml'], check=True)
            subprocess.run(['git', 'commit', '-m', 'initial report template'], check=True)
            subprocess.run(['git', 'push'], check=True)
            console.print('Switched to version1 and pushed to remote')
        else:
            # Subsequent downloads: version branches already exist
            if not has_changes:
                console.print('No changes detected. Files are already up to date.')
                latest_version = _get_latest_version()
                if latest_version > 0:
                    subprocess.run(['git', 'checkout', f'version{latest_version}'], check=True)
                    console.print(f'Switched to version{latest_version}')
            else:
                # There are changes: create new version
                console.print('Changes detected. Creating new version.')
                subprocess.run(['git', 'commit', '-m', f'update from zenodo'], check=True)
                subprocess.run(['git', 'push'], check=True)

                latest_version = _get_latest_version()
                new_version = latest_version + 1

                subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)
                _copy_report_from_previous_version(latest_version)
                subprocess.run(['git', 'push', '-u', 'origin', f'version{new_version}'], check=True)
                console.print(f'Created version{new_version} and pushed to remote')

        _save_zenodo_metadata(download_url)
    else:
        # Multiple files: download all, then process
        _download_multiple_files(record_id, files, zenodo_key)


@cli.command()
@click.argument('branch_name', required=False)
@click.option('--no-commit', is_flag=True, help='Generate report without committing and pushing')
@click.pass_context
def report(ctx, branch_name, no_commit):
    """Generate and commit report.

    Generates response.txt from report.toml using the appropriate Jinja2 template based on
    the branch version. Commits and pushes changes (unless --no-commit).

    Args:
        BRANCH_NAME: Optional branch name (defaults to current branch)

    Options:
        --no-commit    Generate report without committing and pushing
    """
    # Get current branch if not specified
    if not branch_name:
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
        branch_name = result.stdout.strip()

    # Initialize renderer
    templates_dir = get_template_path('.')
    renderer = ReportRenderer(templates_dir)

    # Validate report.toml
    is_valid, msg = renderer.validate_toml('report.toml')
    if not is_valid:
        click.echo(f"[ERROR] {msg}", err=True)
        return

    # Select template based on branch version
    if branch_name == "version1":
        template_name = 'response1.jinja2'
        click.echo(f"[DEBUG] Selected template: response1.jinja2 (version1)", err=True)
    else:
        template_name = 'response2.jinja2'
        click.echo(f"[DEBUG] Selected template: response2.jinja2 (default)", err=True)

    # Generate response
    response = renderer.generate_report('report.toml', template_name)

    with open('response.txt', 'w') as f:
        f.write(response)

    # Commit changes (unless --no-commit flag is set)
    if not no_commit:
        subprocess.run(['git', 'add', 'report.toml', 'response.txt'], check=True)
        subprocess.run(['git', 'commit', '-m', 'update report'], check=True)
        subprocess.run(['git', 'push', 'origin', branch_name], check=True)
    else:
        console = Console()
        console.print("[yellow]Report generated without committing. Files ready for review.[/yellow]")


@cli.command()
@click.option('-andrea', 'branch', flag_value='andrea', help='Reinstall from remote andrea branch')
@click.option('--pip', 'use_pip', is_flag=True, help='Use pip instead of uv for installation')
@click.option('--ssh', 'use_ssh', is_flag=True, help='Use SSH URL instead of HTTPS')
@click.pass_context
def reinstall(ctx, branch, use_pip, use_ssh):
    """Reinstall REStud from remote branch.

    Reinstalls REStud from the specified remote branch (default: andrea).

    Options:
        -andrea       Reinstall from remote origin/andrea branch
        --pip         Use pip instead of uv for installation
        --ssh         Use SSH URL instead of HTTPS
    """
    if not branch:
        branch = 'andrea'

    console = Console()
    console.print(f"[blue]Reinstalling REStud from origin/{branch}...[/blue]")

    try:
        if use_ssh:
            git_url = f'git+ssh://git@github.com/REStud/workflow.git@{branch}#subdirectory=.'
        else:
            git_url = f'git+https://github.com/REStud/workflow.git@{branch}#subdirectory=.'

        if use_pip:
            console.print("[dim]Using pip for installation...[/dim]")
            subprocess.run(
                ['pip', 'install', '--force-reinstall', git_url],
                check=True
            )
        else:
            console.print("[dim]Using uv for installation...[/dim]")
            subprocess.run(
                ['uv', 'tool', 'install', '--force', git_url],
                check=True
            )

        console.print(f"[green]REStud successfully reinstalled from origin/{branch}[/green]")

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error reinstalling REStud: {e}[/red]")
        installer = "pip" if use_pip else "uv"
        console.print(f"[yellow]Make sure you have the correct remote branch and {installer} is installed.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@cli.command()
@click.pass_context
def shell(ctx):
    """Start interactive REStud shell.

    Launches an interactive shell with REStud command support, command history,
    tab completion, and status indicators (branch, report status, accepted tag).

    Built-in commands:
        cd DIRECTORY   Change directory
        help           Show available commands
        exit           Quit the shell

    REStud commands are available directly in the shell.
    Non-REStud commands are passed to the system shell.
    """
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
    response = requests.get(f"{url}?access_token={api_key}", stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    with open('repo.zip', 'wb') as f, tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading') as pbar:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            pbar.update(len(chunk))

    subprocess.run(['unzip', 'repo.zip'], check=True)
    os.remove('repo.zip')


def _download_zenodo_preview(url):
    """Download Zenodo preview with cookie."""
    cookie_value = _get_cookie()
    headers = {'Cookie': f'session={cookie_value}'}

    response = requests.get(url, headers=headers, stream=True)
    response.raise_for_status()

    total_size = int(response.headers.get('content-length', 0))
    with open('repo.zip', 'wb') as f, tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading') as pbar:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
            pbar.update(len(chunk))

    subprocess.run(['unzip', 'repo.zip'], check=True)
    os.remove('repo.zip')


def _download_multiple_files(record_id, files, zenodo_key):
    """Download multiple files from Zenodo draft.

    Downloads all files, unzips only .zip files, and keeps other files as-is.
    Handles branch management and commits all changes together.
    """
    branch = get_git_branch()
    if branch != 'author':
        click.echo('You must be on the author branch to download from Zenodo. Changing to author branch now.')
        status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
        committed_changes = [line for line in status_result.stdout.split('\n') if line and not line.startswith('??')]
        if committed_changes:
            click.echo('[ERROR] You have uncommitted changes. Please commit or discard them before downloading.', err=True)
            sys.exit(1)
        subprocess.run(['git', 'switch', 'author'], check=True)

    _empty_folder()

    console = Console()

    # Download all files
    zip_files = []
    for file_info in files:
        filename = file_info['key']
        download_url = f"https://zenodo.org/api/records/{record_id}/draft/files/{filename}/content"

        console.print(f"[blue]Downloading {filename}...[/blue]")

        if "preview" in download_url:
            cookie_value = _get_cookie()
            headers = {'Cookie': f'session={cookie_value}'}
            response = requests.get(download_url, headers=headers, stream=True)
        else:
            response = requests.get(f"{download_url}?access_token={zenodo_key}", stream=True)

        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        with open(filename, 'wb') as f, tqdm(total=total_size, unit='B', unit_scale=True, desc=filename) as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

        # Track zip files for extraction
        if filename.endswith('.zip'):
            zip_files.append(filename)

    # Unzip only .zip files
    for zip_file in zip_files:
        console.print(f"[yellow]Extracting {zip_file}...[/yellow]")
        subprocess.run(['unzip', zip_file], check=True)
        os.remove(zip_file)

    # Commit changes
    _commit_changes()
    _check_for_files()

    # Check if there are staged changes to commit
    status_result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, check=True)
    has_changes = status_result.stdout.strip() != ""

    result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
    branches = [line.strip() for line in result.stdout.split('\n') if line.strip() and 'author' not in line and not line.strip().startswith('remotes/')]

    if not branches:
        # First download: no version branches exist
        console.print('First download: creating version1')
        subprocess.run(['git', 'commit', '-m', f'initial commit from zenodo'], check=True)
        subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], check=True)
        subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
        subprocess.run(['git', 'push', '-u', 'origin', 'version1'], check=True)
        console.print('Switched to version1 and pushed to remote')
    else:
        # Subsequent downloads: version branches already exist
        if not has_changes:
            console.print('No changes detected. Files are already up to date.')
            latest_version = _get_latest_version()
            if latest_version > 0:
                subprocess.run(['git', 'checkout', f'version{latest_version}'], check=True)
                console.print(f'Switched to version{latest_version}')
        else:
            # There are changes: create new version
            console.print('Changes detected. Creating new version.')
            subprocess.run(['git', 'commit', '-m', f'update from zenodo'], check=True)
            subprocess.run(['git', 'push'], check=True)

            latest_version = _get_latest_version()
            new_version = latest_version + 1

            subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)
            _copy_report_from_previous_version(latest_version)
            subprocess.run(['git', 'push', '-u', 'origin', f'version{new_version}'], check=True)
            console.print(f'Created version{new_version} and pushed to remote')

    _save_zenodo_metadata(f"https://zenodo.org/api/records/{record_id}/draft")


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
    console = Console()
    size_limit = 20 * 1024 * 1024  # 20MB

    # Read existing .gitignore if it exists
    existing_gitignore = ""
    if os.path.exists('.gitignore'):
        with open('.gitignore', 'r') as f:
            existing_gitignore = f.read()

    # Find large files and add to gitignore
    large_files = []
    large_files_info = []

    for root, dirs, files in os.walk('.'):
        for file in files:
            filepath = os.path.join(root, file)
            try:
                file_size = os.path.getsize(filepath)
                if file_size > size_limit:
                    relative_path = filepath[2:]  # Remove './' prefix
                    large_files.append(relative_path)

                    # Store detailed info
                    size_mb = file_size / (1024 * 1024)
                    was_previously_ignored = relative_path in existing_gitignore
                    large_files_info.append({
                        'path': relative_path,
                        'size_bytes': file_size,
                        'size_mb': size_mb,
                        'previously_ignored': was_previously_ignored,
                        'now_ignored': True  # Will be ignored after this run
                    })
            except (OSError, IOError):
                pass

    # Always create/update .gitignore with common ignore patterns
    gitignore_entries = [
        '.zenodo',
        '_MACOSX',
        '.DS_Store'
    ]

    if large_files:
        gitignore_entries = large_files + gitignore_entries

    with open('.gitignore', 'w') as f:
        f.write('\n'.join(gitignore_entries))

    if large_files:
        # Save report to file
        report_file = 'LARGE_FILES.txt'
        with open(report_file, 'w') as f:
            f.write(f"Large Files Report (size limit: {size_limit / (1024 * 1024):.0f}MB)\n")
            f.write("=" * 70 + "\n\n")

            for file_info in large_files_info:
                prev_status = "Yes" if file_info['previously_ignored'] else "No"
                now_status = "Yes" if file_info['now_ignored'] else "No"
                f.write(f"File: {file_info['path']}\n")
                f.write(f"  Size: {file_info['size_mb']:.2f} MB ({file_info['size_bytes']:,} bytes)\n")
                f.write(f"  Previously in .gitignore: {prev_status}\n")
                f.write(f"  Now in .gitignore: {now_status}\n\n")

            f.write(f"\nTotal large files: {len(large_files_info)}\n")
            total_size_mb = sum(f['size_mb'] for f in large_files_info)
            f.write(f"Total size: {total_size_mb:.2f} MB\n")

        # Display summary to user
        console.print(f"\n[yellow]Found {len(large_files)} files exceeding {size_limit / (1024 * 1024):.0f}MB limit[/yellow]")
        console.print(f"[blue]Detailed report saved to: {report_file}[/blue]\n")

        for file_info in large_files_info:
            if file_info['previously_ignored']:
                status = "[green]✓[/green]"
                label = "was already ignored"
            else:
                status = "[yellow]➕[/yellow]"
                label = "newly ignored"
            console.print(f"  {status} {file_info['path']} ({file_info['size_mb']:.2f} MB) - {label}")

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
    """Get the latest version number from git branches (local or remote)."""
    result = subprocess.run(['git', 'branch', '-a'], capture_output=True, text=True, check=True)
    versions = []
    for line in result.stdout.split('\n'):
        if 'version' in line:
            try:
                version_num = int(line.split('version')[-1].strip())
                versions.append(version_num)
            except ValueError:
                continue
    return max(versions) if versions else 0


def _copy_report_from_previous_version(latest_version):
    """Copy report.toml from the previous version branch and commit it."""
    previous_branch = f'version{latest_version}'

    try:
        # Check if report.toml exists on the previous version branch
        result = subprocess.run(
            ['git', 'show', f'{previous_branch}:report.toml'],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode == 0:
            # Write the report.toml from previous branch
            with open('report.toml', 'w') as f:
                f.write(result.stdout)

            # Stage and commit the report.toml
            subprocess.run(['git', 'add', 'report.toml'], check=True)
            subprocess.run(['git', 'commit', '-m', f'copy report.toml from {previous_branch}'], check=True)
            click.echo(f'Copied report.toml from {previous_branch}')
        else:
            click.echo(f'Warning: report.toml not found on {previous_branch}')
    except Exception as e:
        click.echo(f'Warning: Could not copy report.toml from previous version: {e}')


def _save_zenodo_metadata(url):
    """Save Zenodo URL and ID to .zenodo file in YAML format."""
    import re
    match = re.search(r'/(\d+)/', url)
    if match:
        zenodo_id = match.group(1)
        zenodo_data = {
            'url': url,
            'id': zenodo_id
        }
        with open('.zenodo', 'w') as f:
            yaml.dump(zenodo_data, f, default_flow_style=False)


def _add_manuscript_id_to_report():
    """Add manuscript_id to report.toml from the GitHub repo name."""
    # Get the current folder name (which is the repo name)
    repo_name = get_current_folder()

    # Read the report.toml file
    with open('report.toml', 'r', encoding='utf-8') as f:
        data = toml.load(f)

    # Update the manuscript_id in the metadata section
    if 'metadata' not in data:
        data['metadata'] = {}
    data['metadata']['manuscript_id'] = repo_name

    # Write back to report.toml
    with open('report.toml', 'w', encoding='utf-8') as f:
        toml.dump(data, f)


def _get_dcas_rule_answer(dcas_reference):
    """Get the answer to a specific DCAS rule by its reference."""
    try:
        from yamlcore import CoreLoader
        tags_file = get_template_path('template-answers.yaml')
        with open('report.yaml', 'r', encoding='utf-8') as f_report, open(tags_file, 'r', encoding='utf-8') as f_tags:
            combined = f_tags.read() + '\n' + f_report.read()
        content = yaml.load(combined, Loader=CoreLoader)

        if content and 'DCAS_rules' in content:
            for rule in content['DCAS_rules']:
                ref = rule.get('dcas_reference', '')
                answer = rule.get('answer', '')
                # Handle both string and boolean/other types
                answer_str = str(answer).lower() if answer is not None else ''
                if ref == dcas_reference:
                    return answer_str
        click.echo(f"[DEBUG] No rule found for {dcas_reference}", err=True)
    except Exception as e:
        click.echo(f"[DEBUG] Error reading report.yaml: {e}", err=True)
        return None
    return None


def _check_community(ctx):
    """Check and manage community membership."""
    console = Console()

    # Get Zenodo ID from .zenodo file
    try:
        with open('.zenodo', 'r') as f:
            zenodo_data = yaml.safe_load(f)
        zenodo_id = zenodo_data.get('id')
    except Exception as e:
        console.print(f"[red]Could not read .zenodo file: {e}[/red]")
        return

    if not zenodo_id:
        console.print("[red]Could not extract Zenodo ID[/red]")
        return

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
