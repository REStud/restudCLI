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

try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version('restud')
except Exception:
    __version__ = 'unknown'

import re
import click
import requests
import yaml
import toml
from tqdm import tqdm
from rich.console import Console
from rich.prompt import Prompt

from restud.render_jinja2 import ReportRenderer
from restud.render_aml import AMLReportRenderer

# GitHub organization for replication packages
GITHUB_ORG = 'restud-replication-packages'

# Admin repo for package tracking (accessed via GitHub API only — no local clone)
ADMIN_ORG = 'REStud'
ADMIN_REPO = 'packages-admin'
ADMIN_FILE = 'packages.toml'


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


# ---------------------------------------------------------------------------
# Admin repo / package tracking helpers
# ---------------------------------------------------------------------------

def _today():
    from datetime import date
    return date.today().isoformat()


def _get_local_config() -> dict:
    """Read ~/.config/restud/config.toml. Returns empty dict if not found."""
    config_file = os.path.expanduser('~/.config/restud/config.toml')
    if not os.path.exists(config_file):
        return {}
    try:
        with open(config_file, 'r') as f:
            return toml.load(f)
    except Exception:
        return {}


def _save_local_config(cfg: dict):
    """Write ~/.config/restud/config.toml."""
    config_file = os.path.expanduser('~/.config/restud/config.toml')
    os.makedirs(os.path.dirname(config_file), exist_ok=True)
    with open(config_file, 'w') as f:
        toml.dump(cfg, f)


def _get_replicator():
    """Return replicator name from local config, falling back to GitHub username or $USER."""
    name = _get_local_config().get('name', '')
    if name:
        return name
    try:
        r = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True)
        gh_name = r.stdout.strip()
        return gh_name if gh_name else os.environ.get('USER', 'unknown')
    except Exception:
        return os.environ.get('USER', 'unknown')


def _get_known_replicators() -> list:
    """Return sorted list of unique replicator names found in packages.toml."""
    try:
        packages, _ = _gh_api_get_packages()
        names = set()
        for pkg in packages.values():
            for v in pkg.get('versions', []):
                name = v.get('replicator', '').strip()
                if name:
                    names.add(name)
        return sorted(names)
    except Exception:
        return []


def _resolve_assign(assign) -> str:
    """Resolve --assign value: if empty string (flag given without value), prompt from known replicators."""
    if assign is None:
        return None
    if assign != '':
        return assign
    # Flag used without a value — list known replicators and let user pick
    console = Console()
    names = _get_known_replicators()
    if names:
        console.print("[bold]Known replicators:[/bold]")
        for i, name in enumerate(names, 1):
            console.print(f"  {i}. {name}")
        choice = Prompt.ask("Enter number or type a name", console=console).strip()
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return names[int(choice) - 1]
        return choice or None
    else:
        return Prompt.ask("Replicator name", console=console).strip() or None


def _gh_api_get_packages():
    """Fetch packages.toml content from GitHub API. Returns (packages_dict, sha). No data written to disk."""
    import base64
    result = subprocess.run(
        ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/{ADMIN_FILE}'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not fetch {ADMIN_FILE}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    content = base64.b64decode(data['content']).decode('utf-8')
    packages = toml.loads(content).get('packages', {})
    return packages, data['sha']


def _gh_api_put_packages(packages: dict, sha: str, message: str):
    """Push updated packages.toml to GitHub API. No data written to disk."""
    import base64
    content_str = toml.dumps({'packages': packages})
    content_b64 = base64.b64encode(content_str.encode('utf-8')).decode('ascii')
    result = subprocess.run(
        ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/{ADMIN_FILE}',
         '-X', 'PUT',
         '-f', f'message={message}',
         '-f', f'content={content_b64}',
         '-f', f'sha={sha}'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not push {ADMIN_FILE}: {result.stderr.strip()}")


def _pkg_record(packages: dict, pkg_id: str) -> dict:
    """Return existing record or a fresh one."""
    if pkg_id not in packages:
        packages[pkg_id] = {
            'manuscript_id': pkg_id,
            'date_received': '',
            'date_accepted': '',
            'status': 'new',
            'versions': [],
        }
    return packages[pkg_id]


def _version_record(pkg: dict, version_num: int) -> dict:
    """Return existing version sub-record or create it."""
    versions = pkg.setdefault('versions', [])
    for v in versions:
        if v.get('version') == version_num:
            return v
    new_v = {
        'version': version_num,
        'replicator': _get_replicator(),
        'zenodo_id': '',
        'zenodo_url': '',
        'date_downloaded': '',
        'date_report_sent': '',
        'date_decision_sent': '',
        'hours': 0.0,
        'recommendation': '',
        'de_decision': '',
        'software': [],
        'data_availability': '',
        'comments': '',
    }
    versions.append(new_v)
    return new_v


def _current_version_number() -> int:
    """Return integer from current git branch (e.g. version2 → 2), or 1."""
    branch = get_git_branch() or 'version1'
    try:
        return int(branch.replace('version', ''))
    except ValueError:
        return 1


def track_event(pkg_id: str, event: str, value: str = '', ctx=None, date: str = ''):
    """
    Update packages.toml with a tracking event. Silent on errors —
    tracking should never break the main workflow.
    Pass click ctx to respect --notrack flag.

    Events:
        received          → date_received = today, status = new
        downloaded        → versions[N].date_downloaded = today, status = assigned (first) / revision
        report_sent       → versions[N].date_report_sent = today, status = recommendation
        accepted          → date_accepted = today, status = accepted
        decision_sent     → versions[N].date_decision_sent = today
        hours             → versions[N].hours += float(value)
        status            → status = value
        zenodo_id         → versions[N].zenodo_id = value
        replicator        → versions[N].replicator = value
        recommendation    → versions[N].recommendation = value
        de_decision       → versions[N].de_decision = value
        software          → versions[N].software = value (comma-separated string → list)
        data_availability → versions[N].data_availability = value
        comments          → versions[N].comments = value
    """
    console = Console()
    if ctx is not None and ctx.obj and ctx.obj.get('notrack'):
        return
    try:
        packages, sha = _gh_api_get_packages()
        pkg = _pkg_record(packages, pkg_id)
        ver_num = _current_version_number()
        today = date if date else _today()

        # Package-level events — no version record needed
        if event == 'received':
            if pkg.get('status') == 'withdrawn':
                # Re-submission after withdrawal: reset version history and dates for a clean slate
                pkg['versions'] = []
                pkg['date_received'] = today
                pkg['date_accepted'] = ''
            else:
                pkg['date_received'] = pkg['date_received'] or today
            pkg['status'] = 'new'
        elif event == 'accepted':
            pkg['date_accepted'] = pkg['date_accepted'] or today
            pkg['status'] = 'accepted'
        elif event == 'status':
            pkg['status'] = value
        else:
            # Version-level events — create/fetch version record only when needed
            ver = _version_record(pkg, ver_num)
            if event == 'downloaded':
                ver['date_downloaded'] = ver['date_downloaded'] or today
                pkg['status'] = 'assigned' if ver_num == 1 else 'resubmitted'
            elif event == 'report_sent':
                ver['date_report_sent'] = ver['date_report_sent'] or today
                pkg['status'] = 'with-authors'
            elif event == 'decision_sent':
                ver['date_decision_sent'] = ver['date_decision_sent'] or today
            elif event == 'hours':
                ver['hours'] = round(ver['hours'] + float(value), 2)
            elif event == 'zenodo_id':
                ver['zenodo_id'] = value
            elif event == 'zenodo_url':
                ver['zenodo_url'] = value
            elif event == 'replicator':
                ver['replicator'] = value
            elif event == 'recommendation':
                ver['recommendation'] = value
            elif event == 'de_decision':
                ver['de_decision'] = value
            elif event == 'software':
                ver['software'] = [s.strip() for s in value.split(',') if s.strip()]
            elif event == 'data_availability':
                ver['data_availability'] = value
            elif event == 'comments':
                ver['comments'] = value

        _gh_api_put_packages(packages, sha, f"track {pkg_id}: {event}")
    except Exception as e:
        console.print(f"[yellow]Warning: tracking update failed ({event}): {e}[/yellow]")


@click.group()
@click.version_option(__version__, prog_name='restud')
@click.option('--notrack', is_flag=True, default=False,
              help='Disable all automatic tracking hooks for this invocation.')
@click.pass_context
def cli(ctx, notrack):
    """REStud workflow management CLI tool."""
    ctx.ensure_object(dict)
    ctx.obj['notrack'] = notrack

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
@click.option('--no-commit', is_flag=True, help='Generate acceptance message without committing and pushing')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def accept(ctx, no_commit, notrack):
    if notrack:
        ctx.ensure_object(dict)
        ctx.obj['notrack'] = True
    """Generate acceptance message.

    Creates an acceptance message based on the current version branch and report.aml.
    Automatically selects the appropriate Jinja2 template, commits changes, tags as 'accepted',
    and copies to clipboard (unless --no-commit is used).

    Options:
        --no-commit    Generate message without committing, pushing, or tagging
    """
    # Get current branch
    result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
    branch_name = result.stdout.strip()

    templates_dir = get_template_path('.')

    # Prefer report.aml if present, fall back to report.toml
    if os.path.exists('report.aml'):
        renderer = AMLReportRenderer(templates_dir)
        is_valid, msg = renderer.validate_aml('report.aml')
        if not is_valid:
            click.echo(f"[ERROR] {msg}", err=True)
            return
        acceptance = renderer.generate_report('report.aml', 'response-accept.jinja2',
                                               extra_context={'branch_name': branch_name})
    else:
        renderer = ReportRenderer(templates_dir)
        is_valid, msg = renderer.validate_toml('report.toml')
        if not is_valid:
            click.echo(f"[ERROR] {msg}", err=True)
            return
        acceptance = renderer.generate_report('report.toml', 'response-accept.jinja2',
                                               extra_context={'branch_name': branch_name})

    # Write acceptance to file
    with open('accept.txt', 'w') as f:
        f.write(acceptance)

    print(acceptance)

    # Commit and tag (unless --no-commit flag is set)
    if not no_commit:
        subprocess.run(['git', 'add', 'accept.txt'], check=True)

        # Check if there are staged changes to commit
        staged_result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True)
        if staged_result.returncode != 0:
            # There are changes to commit
            subprocess.run(['git', 'commit', '-m', 'acceptance message'], check=True)
            subprocess.run(['git', 'push'], check=True)

        # Tag only if not already tagged
        if not get_git_accepted_tag():
            subprocess.run(['git', 'tag', 'accepted'], check=True)
            subprocess.run(['git', 'push', '--tags'], check=True)
        else:
            console = Console()
            console.print("[yellow]Tag 'accepted' already exists, skipping.[/yellow]")

        # Check community status
        _check_community(ctx)
        track_event(get_current_folder(), 'accepted', ctx=ctx)
    else:
        console = Console()
        console.print("[yellow]Acceptance message generated without committing. Files ready for review.[/yellow]")



def _parse_date_flag(value: str) -> str:
    """Parse --date flag: integer offset (e.g. -1) or MM-DD. Returns ISO date string."""
    from datetime import date, timedelta, datetime
    today = date.today()
    try:
        offset = int(value)
        return (today + timedelta(days=offset)).isoformat()
    except ValueError:
        pass
    for fmt in ('%m-%d', '%m/%d'):
        try:
            parsed = datetime.strptime(value, fmt)
            return today.replace(month=parsed.month, day=parsed.day).isoformat()
        except ValueError:
            continue
    raise click.BadParameter(f"Cannot parse date '{value}'. Use an integer offset (e.g. -1) or MM-DD format.")


@cli.command()
@click.argument('package_name')
@click.option('--date', '-d', 'received_date', default=None,
              help='Set date_received: integer offset from today (e.g. -1 for yesterday) or MM-DD.')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def new(ctx, package_name, received_date, notrack):
    if notrack:
        ctx.ensure_object(dict)
        ctx.obj['notrack'] = True
    """Create new replication package.

    Initializes a new local repository with report.aml template, creates a remote GitHub
    repository in the restud-replication-packages organization, and sets up the 'author' branch.
    Saves a minimal .gitignore with common macOS entries and a report.aml template and commits them.
    Pushes the initial commit to the remote.

    Args:
        PACKAGE_NAME: Name for the new replication package
    """
    os.makedirs(package_name, exist_ok=True)
    os.chdir(package_name)
    subprocess.run(['git', 'init'], check=True)

    # Check if remote repo already exists; if so, abort and suggest pull
    check = subprocess.run(['gh', 'repo', 'view', f'{GITHUB_ORG}/{package_name}'], capture_output=True, text=True)
    if check.returncode == 0:
        click.echo(f"[ERROR] Remote repository {GITHUB_ORG}/{package_name} already exists.", err=True)
        click.echo(f"Use 'restud pull {package_name}' to clone and set it up locally.", err=True)
        return
    else:
        result = subprocess.run(['gh', 'repo', 'create', f'{GITHUB_ORG}/{package_name}', '--private', '--team', 'Replicators'], capture_output=True, text=True)
        if result.returncode != 0:
            click.echo(f"Error creating repository: {result.stderr}", err=True)

    # Add or update the remote
    remote_url = f'git@github.com:{GITHUB_ORG}/{package_name}.git'
    result = subprocess.run(['git', 'remote', 'add', 'origin', remote_url], capture_output=True, text=True)
    if result.returncode != 0:
        # Remote already exists, update it
        subprocess.run(['git', 'remote', 'set-url', 'origin', remote_url], check=True)

    subprocess.run(['git', 'checkout', '-b', 'author'], check=True)

    # Ensure .gitignore with common macOS entries exists and is committed on initial author branch.
    gitignore_path = os.path.join(os.getcwd(), '.gitignore')
    if not os.path.exists(gitignore_path):
        try:
            with open(gitignore_path, 'w', encoding='utf-8') as f:
                f.write('_MACOSX\n.DS_Store\n')
            subprocess.run(['git', 'add', '.gitignore'], check=True)
            try:
                subprocess.run(['git', 'commit', '-m', 'Add .gitignore'], check=True)
            except CalledProcessError:
                # Commit may fail if git user.name/email are not set or other local config issues; continue.
                pass
        except Exception:
            # If creating or committing .gitignore fails for any reason, continue without stopping repo creation.
            pass

    # Try to push, if it fails because remote has content, pull first then push
    result = subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], capture_output=True, text=True)
    if result.returncode != 0 and 'already exists' not in result.stderr:
        if 'rejected' in result.stderr or 'fetch first' in result.stderr:
            # Remote exists with content, fetch and merge
            click.echo("Repository already exists remotely. Pulling existing content...")
            subprocess.run(['git', 'fetch', 'origin', 'author'], check=True)
            subprocess.run(['git', 'merge', 'origin/author', '--allow-unrelated-histories'], check=True)
            subprocess.run(['git', 'push', 'origin', 'author'], check=True)
        else:
            raise CalledProcessError(result.returncode, result.args)

    # Create version1 branch with report.aml template
    subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
    subprocess.run(['git', 'push', '-u', 'origin', 'version1'], check=True)
    shutil.copy(get_template_path('report.aml'), 'report.aml')
    _add_manuscript_id_to_report()
    subprocess.run(['git', 'add', 'report.aml'], check=True)
    subprocess.run(['git', 'commit', '-m', 'initial report template'], check=True)
    subprocess.run(['git', 'push'], check=True)
    click.echo(f"Created version1 with report.aml for {package_name}")
    parsed_date = _parse_date_flag(received_date) if received_date else ''
    track_event(package_name, 'received', ctx=ctx, date=parsed_date)


@cli.command()
@click.argument('zenodo_url')
@click.option('--assign', '-a', default=None, is_flag=False, flag_value='',
              help='Override replicator name (superuser only). Omit value to pick from list.')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def download_withurl(ctx, zenodo_url, assign, notrack):
    if notrack:
        ctx.ensure_object(dict)
        ctx.obj['notrack'] = True
    """Download package from Zenodo via URL.

    Downloads and imports replication package files from Zenodo (published or preview records).
    Extracts files, removes large files from git tracking, and creates version branches.
    Requires Zenodo API key in ~/.config/.zenodo_api_key for published records.

    Args:
        ZENODO_URL: URL to the Zenodo record or preview record
    """
    if assign is not None and not _get_local_config().get('superuser', False):
        click.echo('[ERROR] --assign requires superuser = true in ~/.config/restud/config.toml', err=True)
        sys.exit(1)
    assign = _resolve_assign(assign)

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

    pkg_id = get_current_folder()
    if not branches:
        click.echo('No other branch than author exists')
        subprocess.run(['git', 'commit', '-m', f'initial commit from zenodo {zenodo_url}'], check=True)
        subprocess.run(['git', 'push', 'origin', 'author', '--set-upstream'], check=True)
        subprocess.run(['git', 'checkout', '-b', 'version1'], check=True)
        track_event(pkg_id, 'downloaded', ctx=ctx)
    else:
        click.echo('Other branches exist')
        subprocess.run(['git', 'commit', '-m', f'update to zenodo version {zenodo_url}'], check=True)
        subprocess.run(['git', 'push'], check=True)

        latest_version = _get_latest_version()

        if _version_is_empty(latest_version):
            # version branch was created by restud new but never downloaded into
            click.echo(f'First download: merging into existing version{latest_version}')
            subprocess.run(['git', 'checkout', f'version{latest_version}'], check=True)
            subprocess.run(['git', 'merge', 'author', '--no-edit'], check=True)
            subprocess.run(['git', 'push'], check=True)
        else:
            new_version = latest_version + 1

            # Create new version branch
            subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)

            # Copy report.yaml from previous version branch and commit it
            _copy_report_from_previous_version(latest_version)

            # Push the new version branch
            subprocess.run(['git', 'push', '-u', 'origin', f'version{new_version}'], check=True)
        track_event(pkg_id, 'downloaded', ctx=ctx)

    _save_zenodo_metadata(zenodo_url)
    m = re.search(r'/(\d+)', zenodo_url)
    if m:
        track_event(pkg_id, 'zenodo_id', m.group(1), ctx=ctx)
    track_event(pkg_id, 'zenodo_url', zenodo_url, ctx=ctx)
    if assign:
        track_event(pkg_id, 'replicator', assign, ctx=ctx)


@cli.command()
@click.argument('record_id')
@click.option('--assign', '-a', default=None, is_flag=False, flag_value='',
              help='Override replicator name (superuser only). Omit value to pick from list.')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def download(ctx, record_id, assign, notrack):
    if notrack:
        ctx.ensure_object(dict)
        ctx.obj['notrack'] = True
    """Download package from Zenodo draft using record ID.

    Downloads from a Zenodo draft record by ID. Downloads all available files.
    Requires Zenodo API key in ~/.config/.zenodo_api_key.

    Args:
        RECORD_ID: Zenodo draft record ID (numeric)
    """
    if assign is not None and not _get_local_config().get('superuser', False):
        click.echo('[ERROR] --assign requires superuser = true in ~/.config/restud/config.toml', err=True)
        sys.exit(1)
    assign = _resolve_assign(assign)

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

    _download_multiple_files(record_id, files, zenodo_key, ctx=ctx, assign=assign)


@cli.command()
@click.argument('branch_name', required=False)
@click.option('--no-commit', is_flag=True, help='Generate report without committing and pushing')
@click.option('--needspackage', is_flag=True, help='Use needs-replication-package template')
@click.option('--track', 'do_track', is_flag=True, default=False,
              help='Record report_sent event in tracking database (off by default).')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def revise(ctx, branch_name, no_commit, needspackage, do_track, notrack):
    if notrack:
        ctx.ensure_object(dict)
        ctx.obj['notrack'] = True
    """Generate revision report message.

    Generates response.txt from report.aml using the appropriate Jinja2 template based on
    the branch version. Commits and pushes changes (unless --no-commit).

    Args:
        BRANCH_NAME: Optional branch name (defaults to current branch)

    """
    # Get current branch if not specified
    if not branch_name:
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'], capture_output=True, text=True, check=True)
        branch_name = result.stdout.strip()

    templates_dir = get_template_path('.')
    template_name = 'response-needRP.jinja2' if needspackage else 'response-revise.jinja2'

    # Prefer report.aml if present, fall back to report.toml
    if os.path.exists('report.aml'):
        renderer = AMLReportRenderer(templates_dir)
        is_valid, msg = renderer.validate_aml('report.aml')
        if not is_valid:
            click.echo(f"[ERROR] {msg}", err=True)
            return
        response = renderer.generate_report('report.aml', template_name,
                                             extra_context={'branch_name': branch_name})
        report_file = 'report.aml'
    else:
        renderer = ReportRenderer(templates_dir)
        is_valid, msg = renderer.validate_toml('report.toml')
        if not is_valid:
            click.echo(f"[ERROR] {msg}", err=True)
            return
        response = renderer.generate_report('report.toml', template_name,
                                             extra_context={'branch_name': branch_name})
        report_file = 'report.toml'

    with open('response.txt', 'w') as f:
        f.write(response)

    # Commit changes (unless --no-commit flag is set)
    if not no_commit:
        subprocess.run(['git', 'add', report_file, 'response.txt'], check=True)
        subprocess.run(['git', 'commit', '-m', 'update report'], check=True)
        subprocess.run(['git', 'push', 'origin', branch_name], check=True)
        if do_track:
            track_event(get_current_folder(), 'report_sent', ctx=ctx)
    else:
        console = Console()
        console.print("[yellow]Report generated without committing. Files ready for review.[/yellow]")


@cli.command(name='snippet')
@click.argument('tag', required=False, default=None)
@click.pass_context
def snippet_cmd(ctx, tag):
    """Print the text of a snippet from base-snippets.toml.

    Without arguments, lists all available snippet names grouped by category.

    Args:
        TAG: Snippet tag name, with or without the leading * (e.g. DAS or *DAS)
    """
    snippets_path = get_template_path('base-snippets.toml')
    with open(snippets_path, 'r', encoding='utf-8') as f:
        data = toml.load(f)
    groups = data.get('snippets', {})

    # Build flat lookup dict
    flat = {}
    for group_tags in groups.values():
        if isinstance(group_tags, dict):
            flat.update(group_tags)

    if tag is None:
        for group_name, group_tags in groups.items():
            if isinstance(group_tags, dict):
                click.echo(f"{group_name}:")
                click.echo('  ' + ', '.join(k.lstrip('*') for k in group_tags))
                click.echo()
        return

    # Normalise: ensure tag starts with *
    key = tag if tag.startswith('*') else f'*{tag}'

    if key not in flat:
        available = ', '.join(k.lstrip('*') for k in sorted(flat))
        click.echo(f"Unknown snippet '{key}'. Available: {available}", err=True)
        return

    click.echo(flat[key])


# ---------------------------------------------------------------------------
# Track commands
# ---------------------------------------------------------------------------

@cli.group()
@click.option('-p', '--package', 'pkg_override', default=None,
              help='Package number (defaults to current folder name)')
@click.pass_context
def track(ctx, pkg_override):
    """Update tracking data for a package."""
    ctx.ensure_object(dict)
    ctx.obj['pkg_id'] = pkg_override or get_current_folder()


@track.command('hours')
@click.argument('hours', type=float)
@click.pass_context
def track_hours(ctx, hours):
    """Add hours spent on current package/version."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'hours', str(hours), ctx=ctx)
    click.echo(f"Logged {hours}h for {pkg_id}.")


@track.command('status')
@click.argument('status', type=click.Choice(
    ['new', 'assigned', 'resubmitted', 'with-authors', 'with-de', 'with-me', 'q-authors', 'accepted', 'withdrawn']))
@click.pass_context
def track_status(ctx, status):
    """Manually set package status."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'status', status, ctx=ctx)
    click.echo(f"Status of {pkg_id} set to '{status}'.")


@track.command('decision-sent')
@click.pass_context
def track_decision_sent(ctx):
    """Record today as the date the decision was sent to the author."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'decision_sent', ctx=ctx)
    click.echo(f"Decision-sent date recorded for {pkg_id}.")


@track.command('replicator')
@click.argument('name')
@click.pass_context
def track_replicator(ctx, name):
    """Set the replicator name for the current version of a package."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'replicator', name, ctx=ctx)
    click.echo(f"Replicator for {pkg_id} set to '{name}'.")


@track.command('recommendation')
@click.argument('value')
@click.pass_context
def track_recommendation(ctx, value):
    """Set the replicator's recommendation for the current version."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'recommendation', value, ctx=ctx)
    click.echo(f"Recommendation for {pkg_id} set to '{value}'.")


@track.command('de-decision')
@click.argument('value')
@click.pass_context
def track_de_decision(ctx, value):
    """Set the data editor's decision for the current version."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'de_decision', value, ctx=ctx)
    click.echo(f"DE decision for {pkg_id} set to '{value}'.")


@track.command('software')
@click.argument('value')
@click.pass_context
def track_software(ctx, value):
    """Set software list for the current version (comma-separated, e.g. 'Stata,R')."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'software', value, ctx=ctx)
    click.echo(f"Software for {pkg_id} set to '{value}'.")


@track.command('data-availability')
@click.argument('value')
@click.pass_context
def track_data_availability(ctx, value):
    """Set data availability note for the current version."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'data_availability', value, ctx=ctx)
    click.echo(f"Data availability for {pkg_id} set to '{value}'.")


@track.command('comment')
@click.argument('value')
@click.pass_context
def track_comment(ctx, value):
    """Set comments for the current version."""
    pkg_id = ctx.obj['pkg_id']
    track_event(pkg_id, 'comments', value, ctx=ctx)
    click.echo(f"Comments for {pkg_id} updated.")


@track.command('show')
@click.option('-p', '--package', 'pkg_override', default=None,
              help='Package number (overrides group -p and current folder name)')
@click.pass_context
def track_show(ctx, pkg_override):
    """Show tracking record for the current (or -p) package."""
    pkg_id = pkg_override or ctx.obj['pkg_id']
    try:
        packages, _ = _gh_api_get_packages()
    except Exception as e:
        click.echo(f"Error fetching tracking data: {e}", err=True)
        return
    if pkg_id not in packages:
        click.echo(f"No tracking record found for {pkg_id}.")
        return
    pkg = packages[pkg_id]
    console = Console()
    console.print(f"\n[bold]Package {pkg_id}[/bold]")
    console.print(f"  Status      : {pkg.get('status', '')}")
    console.print(f"  Received    : {pkg.get('date_received', '')}")
    console.print(f"  Accepted    : {pkg.get('date_accepted', '')}")
    for v in pkg.get('versions', []):
        n = v['version']
        software_str = ', '.join(v.get('software', [])) or ''
        console.print(f"  [bold]Version {n}[/bold]")
        console.print(f"    Replicator   : {v.get('replicator','')}")
        console.print(f"    Zenodo ID    : {v.get('zenodo_id','')}")
        console.print(f"    Downloaded   : {v.get('date_downloaded','')}")
        console.print(f"    Report sent  : {v.get('date_report_sent','')}")
        console.print(f"    Decision sent: {v.get('date_decision_sent','')}")
        console.print(f"    Hours        : {v.get('hours', 0.0)}")
        console.print(f"    Recommendation: {v.get('recommendation','')}")
        console.print(f"    DE decision  : {v.get('de_decision','')}")
        console.print(f"    Software     : {software_str}")
        if v.get('data_availability'):
            console.print(f"    Data avail.  : {v.get('data_availability','')}")
        if v.get('comments'):
            console.print(f"    Comments     : {v.get('comments','')}")


# ---------------------------------------------------------------------------
# List command
# ---------------------------------------------------------------------------

VALID_STATUSES = ['new', 'assigned', 'resubmitted', 'with-authors', 'with-de', 'with-me', 'q-authors', 'accepted', 'withdrawn']


@cli.command(name='list')
@click.option('--status', '-s', default=None,
              type=click.Choice(VALID_STATUSES + ['all', 'active']),
              help='Filter by status (default: active)')
@click.option('--replicator', '-r', default=None, help='Filter by replicator name')
@click.pass_context
def list_packages(ctx, status, replicator):
    """List packages from the admin tracking database."""
    try:
        packages, _ = _gh_api_get_packages()
    except Exception as e:
        click.echo(f"Error fetching tracking data: {e}", err=True)
        return
    if not packages:
        click.echo("No packages in tracking database.")
        return

    console = Console()
    rows = []
    for pkg_id, pkg in sorted(packages.items()):
        s = pkg.get('status', '')
        # replicator: take from the latest version
        versions = pkg.get('versions', [])
        r = versions[-1].get('replicator', '') if versions else ''
        if status == 'all':
            pass  # show everything
        elif status == 'active' or status is None:
            if s in ('accepted', 'withdrawn'):
                continue
        elif s != status:
            continue
        if replicator and r.lower() != replicator.lower():
            continue
        total_hours = sum(v.get('hours', 0.0) for v in pkg.get('versions', []))
        n_versions = len(pkg.get('versions', []))
        rows.append((pkg_id, s, r, pkg.get('date_received', ''), n_versions, total_hours))

    if not rows:
        click.echo("No packages match the filter.")
        return

    header = f"{'ID':<10} {'Status':<16} {'Replicator':<20} {'Received':<12} {'Versions':>8} {'Hours':>6}"
    console.print(f"\n[bold]{header}[/bold]")
    console.print("-" * len(header))
    for pkg_id, s, r, received, n_v, hrs in rows:
        color = {'accepted': 'green', 'with-authors': 'yellow', 'with-de': 'yellow', 'with-me': 'yellow',
                 'resubmitted': 'cyan', 'assigned': 'blue', 'q-authors': 'magenta', 'new': 'white',
                 'withdrawn': 'red'}.get(s, 'white')
        console.print(f"{pkg_id:<10} [{color}]{s:<16}[/{color}] {r:<20} {received:<12} {n_v:>8} {hrs:>6.1f}")
    console.print()


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def stats(ctx):
    """Show aggregate statistics from the tracking database."""
    from datetime import date

    try:
        packages, _ = _gh_api_get_packages()
    except Exception as e:
        click.echo(f"Error fetching tracking data: {e}", err=True)
        return
    if not packages:
        click.echo("No packages in tracking database.")
        return

    console = Console()

    total = len(packages)
    by_status = {}
    for pkg in packages.values():
        s = pkg.get('status', 'unknown')
        by_status[s] = by_status.get(s, 0) + 1

    # Days: receipt to acceptance
    receipt_to_accept_days = []
    total_hours_all = []
    replicator_hours = {}

    for pkg in packages.values():
        versions = pkg.get('versions', [])
        r = versions[-1].get('replicator', 'unknown') if versions else 'unknown'
        hrs = sum(v.get('hours', 0.0) for v in versions)
        total_hours_all.append(hrs)
        replicator_hours[r] = replicator_hours.get(r, 0.0) + hrs

        if pkg.get('date_received') and pkg.get('date_accepted'):
            try:
                d0 = date.fromisoformat(pkg['date_received'])
                d1 = date.fromisoformat(pkg['date_accepted'])
                receipt_to_accept_days.append((d1 - d0).days)
            except ValueError:
                pass

    console.print("\n[bold]Package counts by status:[/bold]")
    for s in VALID_STATUSES:
        n = by_status.get(s, 0)
        if n:
            console.print(f"  {s:<20} {n}")
    console.print(f"  {'Total':<20} {total}")

    if receipt_to_accept_days:
        avg_days = sum(receipt_to_accept_days) / len(receipt_to_accept_days)
        console.print(f"\n[bold]Days receipt → acceptance:[/bold] {avg_days:.1f} avg over {len(receipt_to_accept_days)} packages")

    accepted_hrs = [sum(v.get('hours', 0.0) for v in packages[pid].get('versions', []))
                    for pid, pkg in packages.items() if pkg.get('status') == 'accepted'
                    for pid in [pid] if packages[pid].get('hours_counted', True)]
    # simpler:
    accepted_hrs = [sum(v.get('hours', 0.0) for v in pkg.get('versions', []))
                    for pkg in packages.values() if pkg.get('status') == 'accepted']
    if accepted_hrs:
        console.print(f"[bold]Avg hours (accepted):[/bold] {sum(accepted_hrs)/len(accepted_hrs):.2f}")

    console.print("\n[bold]Hours by replicator:[/bold]")
    for r, hrs in sorted(replicator_hours.items(), key=lambda x: -x[1]):
        console.print(f"  {r:<25} {hrs:.1f}h")
    console.print()


@cli.command()
@click.option('--branch', default='main', help='Remote branch to install from (default: main)')
@click.option('--pip', 'use_pip', is_flag=True, help='Use pip instead of uv for installation')
@click.option('--ssh', 'use_ssh', is_flag=True, help='Use SSH URL instead of HTTPS')
@click.option('--accre', 'accre', is_flag=True, help='Use options amenable to ACCRE: --pip --ssh')
@click.pass_context
def reinstall(ctx, branch, use_pip, use_ssh, accre):
    """Reinstall restud cli from remote branch.
    """

    console = Console()
    console.print(f"[blue]Reinstalling REStud from origin/{branch}...[/blue]")

    try:
        if accre:
            use_pip = True
            use_ssh = True

        repo_url = 'git@github.com:REStud/restudCLI.git' if use_ssh else 'https://github.com/REStud/restudCLI.git'

        # Ensure we target a branch head explicitly (avoid ambiguity with tags/other refs).
        branch_ref = f'refs/heads/{branch}'

        branch_check = subprocess.run(
            ['git', 'ls-remote', '--heads', repo_url, branch_ref],
            capture_output=True,
            text=True,
            check=True,
        )
        branch_check_output = branch_check.stdout.strip()
        if not branch_check_output:
            raise ValueError(f"Remote branch '{branch}' was not found on origin")

        resolved_commit = branch_check_output.split()[0]
        console.print(f"[dim]Resolved origin/{branch} to {resolved_commit[:12]}[/dim]")

        if use_ssh:
            git_url = f'git+ssh://git@github.com/REStud/restudCLI.git@{resolved_commit}'
        else:
            git_url = f'git+https://github.com/REStud/restudCLI.git@{resolved_commit}'

        if use_pip:
            console.print("[dim]Using pip for installation...[/dim]")
            env = os.environ.copy()
            env['TMPDIR'] = '/tmp'
            subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', '--force-reinstall', '--no-cache-dir', '--user', git_url],
                check=True,
                env=env
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
        if not use_pip or not use_ssh:
            console.print("[yellow]If you got a permission error, try: restud reinstall --pip --ssh[/yellow]")


@cli.command()
@click.pass_context
def dashboard(ctx):
    """Open the package tracking dashboard in a browser."""
    import base64 as _b64, tempfile, webbrowser
    console = Console()
    result = subprocess.run(
        ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/dashboard.html'],
        capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]dashboard.html not found in {ADMIN_ORG}/{ADMIN_REPO}.[/red]")
        console.print("[yellow]Trigger the workflow on GitHub (Actions → Generate Dashboard → Run workflow).[/yellow]")
        return
    data = json.loads(result.stdout)
    html = _b64.b64decode(data['content']).decode('utf-8')
    with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
        f.write(html)
        tmp_path = f.name
    webbrowser.open(f'file://{tmp_path}')
    console.print(f"[green]Dashboard opened in browser.[/green]")


@cli.command(name='config')
@click.option('--name', 'set_name', default=None, help='Set your replicator display name.')
@click.option('--superuser', 'superuser_true', is_flag=True, default=False, help='Grant superuser privileges (enables --assign).')
@click.option('--no-superuser', 'superuser_false', is_flag=True, default=False, help='Revoke superuser privileges.')
@click.pass_context
def config_cmd(ctx, set_name, superuser_true, superuser_false):
    """Show or update local restud config (~/.config/restud/config.toml)."""
    cfg = _get_local_config()
    is_superuser = cfg.get('superuser', False)
    changed = False
    if set_name is not None:
        cfg['name'] = set_name
        changed = True
    if superuser_true or superuser_false:
        if not is_superuser:
            click.echo('[ERROR] Only an existing superuser can change superuser status.', err=True)
            sys.exit(1)
        cfg['superuser'] = bool(superuser_true)
        changed = True
    if changed:
        _save_local_config(cfg)
        click.echo('Config updated.')
    console = Console()
    console.print(f"\n[bold]Local config[/bold] (~/.config/restud/config.toml)")
    console.print(f"  name      : {cfg.get('name', '(not set — using GitHub username)')}")
    console.print(f"  superuser : {cfg.get('superuser', False)}")
    console.print()


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


def _download_multiple_files(record_id, files, zenodo_key, ctx=None, assign=None):
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

    # Save zenodo metadata before checking for changes so it is included
    _save_zenodo_metadata(f"https://zenodo.org/api/records/{record_id}/draft")
    subprocess.run(['git', 'add', '.zenodo'], check=True)

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
        # Create initial report.aml template on version1
        shutil.copy(get_template_path('report.aml'), 'report.aml')
        _add_manuscript_id_to_report()
        subprocess.run(['git', 'add', 'report.aml'], check=True)
        subprocess.run(['git', 'commit', '-m', 'initial report template'], check=True)
        subprocess.run(['git', 'push'], check=True)
        console.print('Switched to version1 and pushed to remote')
        pkg_id = get_current_folder()
        track_event(pkg_id, 'zenodo_id', str(record_id), ctx=ctx)
        track_event(pkg_id, 'zenodo_url', f'https://zenodo.org/records/{record_id}', ctx=ctx)
        track_event(pkg_id, 'downloaded', ctx=ctx)
        if assign:
            track_event(pkg_id, 'replicator', assign, ctx=ctx)
        console.print(f"[green]Tracking updated for {pkg_id}: downloaded (zenodo {record_id}{', replicator: ' + assign if assign else ''})[/green]")
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

            if _version_is_empty(latest_version):
                console.print(f'First download: merging into existing version{latest_version}')
                subprocess.run(['git', 'checkout', f'version{latest_version}'], check=True)
                subprocess.run(['git', 'merge', 'author', '--no-edit'], check=True)
                subprocess.run(['git', 'push'], check=True)
            else:
                new_version = latest_version + 1

                subprocess.run(['git', 'checkout', '-b', f'version{new_version}'], check=True)
                _copy_report_from_previous_version(latest_version)
                subprocess.run(['git', 'push', '-u', 'origin', f'version{new_version}'], check=True)
                console.print(f'Created version{new_version} and pushed to remote')
        pkg_id = get_current_folder()
        track_event(pkg_id, 'zenodo_id', str(record_id), ctx=ctx)
        track_event(pkg_id, 'zenodo_url', f'https://zenodo.org/records/{record_id}', ctx=ctx)
        track_event(pkg_id, 'downloaded', ctx=ctx)
        if assign:
            track_event(pkg_id, 'replicator', assign, ctx=ctx)
        console.print(f"[green]Tracking updated for {pkg_id}: downloaded (zenodo {record_id}{', replicator: ' + assign if assign else ''})[/green]")

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
            for file_info in large_files_info:
                prev_status = "NOT previously in gitignore" if not file_info['previously_ignored'] else "previously in gitignore"
                f.write(f"{file_info['path']}, {file_info['size_mb']:.2f} MB, {prev_status}\n")

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


def _version_is_empty(version_num: int) -> bool:
    """Return True if versionN has never had content downloaded into it (no .zenodo file committed)."""
    result = subprocess.run(
        ['git', 'show', f'version{version_num}:.zenodo'],
        capture_output=True
    )
    return result.returncode != 0


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


def _comment_out_sections(content: str) -> str:
    """Comment out all lines inside [requests] and [recommendations] sections."""
    lines = content.split('\n')
    result = []
    in_section = False
    section_re = re.compile(r'^\[(requests|recommendations)\]\s*$', re.IGNORECASE)
    next_section_re = re.compile(r'^\[')

    for line in lines:
        if section_re.match(line):
            in_section = True
            result.append(line)
        elif in_section and next_section_re.match(line) and not section_re.match(line):
            in_section = False
            result.append(line)
        elif in_section and line.strip() and not line.startswith('#'):
            result.append('# ' + line)
        else:
            result.append(line)

    return '\n'.join(result)


def _copy_report_from_previous_version(latest_version):
    """Copy report.aml (or report.toml) from the previous version branch and commit it.

    Recommendations and requests from the previous version are kept but commented out.
    """
    previous_branch = f'version{latest_version}'

    try:
        # Try report.aml first, then fall back to report.toml
        for report_file in ('report.aml', 'report.toml'):
            result = subprocess.run(
                ['git', 'show', f'{previous_branch}:{report_file}'],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                content = _comment_out_sections(result.stdout)
                with open(report_file, 'w') as f:
                    f.write(content)
                subprocess.run(['git', 'add', report_file], check=True)
                subprocess.run(['git', 'commit', '-m', f'copy {report_file} from {previous_branch}'], check=True)
                click.echo(f'Copied {report_file} from {previous_branch}')
                return

        click.echo(f'Warning: no report file found on {previous_branch}')
    except Exception as e:
        click.echo(f'Warning: Could not copy report from previous version: {e}')


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
    """Add manuscript_id from the current folder name into report.aml or report.toml."""
    repo_name = get_current_folder()
    import re

    # Prefer report.aml
    report_file = 'report.aml' if os.path.exists('report.aml') else 'report.toml'

    with open(report_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace the manuscript_id value in-place to preserve comments
    content = re.sub(
        r'^(manuscript_id\s*=\s*).*$',
        lambda m: f'{m.group(1)}"{repo_name}"',
        content,
        flags=re.MULTILINE
    )

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(content)


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
    console = Console()
    try:
        api_key = _get_zenodo_key()
        url = _get_accept_request(zenodo_id, api_key)
        response = requests.post(f"{url}?access_token={api_key}")
        if response.status_code in (200, 201, 202, 204):
            console.print("[green]Successfully accepted into REStud community.[/green]")
        else:
            console.print(f"[red]Community accept request failed (HTTP {response.status_code}):[/red] {response.text}")
    except IndexError:
        console.print(f"[red]Could not find a pending community request for record {zenodo_id}.[/red]")
    except Exception as e:
        console.print(f"[red]Failed to accept into community: {e}[/red]")

def _get_accept_request(zenodo_id, api_key):
    """Get the acceptance request URL for a Zenodo record."""
    url = f"https://zenodo.org/api/communities/451be469-757a-4121-8792-af8ffc4461fb/requests?size=50&is_open=true&access_token={api_key}"
    response = requests.get(url)
    requests_data = response.json()
    link = [item['links']['actions']['accept'] for item in requests_data['hits']['hits'] if item['topic']['record'] == zenodo_id]
    return link[0]

def main():
    """Main entry point."""
    cli()


if __name__ == '__main__':
    main()
