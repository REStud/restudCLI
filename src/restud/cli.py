#!/usr/bin/env python3
"""REStud workflow management CLI tool."""

import os
import sys
import subprocess
from subprocess import CalledProcessError
import tempfile
import shutil
import json
import stat
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
        template_files = files('restud.templates')
        template_file = template_files / filename
        return str(template_file)
    except Exception:
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
    except Exception:
        return None


def get_git_accepted_tag():
    """Check if 'accepted' tag exists in the repo."""
    try:
        result = subprocess.run(['git', 'tag', '-l', 'accepted'],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip() == 'accepted'
    except Exception:
        return False


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


ASSIGNMENT_TASKS = ('replication', 'decision')
ASSIGNMENT_RESOLUTIONS = {
    'accept': 'Accept',
    'r&r': 'R&R',
    'rr': 'R&R',
    'minor': 'Minor',
    'question me': 'Question ME',
    'question de': 'Question DE',
    'question authors': 'Question Authors',
}


def _is_superuser() -> bool:
    return _get_local_config().get('superuser', False)


def _normalise_resolution(value: str) -> str:
    key = (value or '').strip().lower()
    if key not in ASSIGNMENT_RESOLUTIONS:
        valid = ', '.join(['Accept', 'R&R', 'Minor', 'Question ME', 'Question DE', 'Question Authors'])
        raise ValueError(f"Unknown resolution '{value}'. Choose one of: {valid}")
    return ASSIGNMENT_RESOLUTIONS[key]


def _resolve_followup_assignee(resolution: str) -> str:
    if resolution == 'Question ME':
        return 'me'
    if resolution == 'Question DE':
        return 'de'
    return 'authors'


def _assign_task_for_assignee(assignee: str) -> str:
    return 'decision' if assignee.lower() == 'de' else 'replication'


def _version_assignments(version_record: dict) -> list:
    return version_record.setdefault('assignments', [])


def _next_assignment_id(assignments: list) -> int:
    ids = [int(a.get('id', 0)) for a in assignments if str(a.get('id', '')).isdigit()]
    return (max(ids) + 1) if ids else 1


def _append_assignment(version_record: dict, assignee: str, task: str, assigned_by: str, assigned_date: str) -> dict:
    assignments = _version_assignments(version_record)
    if assignments:
        last = assignments[-1]
        if (not last.get('resolved_date') and
                last.get('assignee', '').lower() == assignee.lower() and
                last.get('task', '') == task):
            return last

    assignment = {
        'id': _next_assignment_id(assignments),
        'task': task,
        'assignee': assignee,
        'assigned_by': assigned_by,
        'assigned_date': assigned_date,
        'resolved_date': '',
        'resolved_by': '',
        'resolution': '',
    }
    assignments.append(assignment)
    return assignment


def _open_assignment(version_record: dict) -> Optional[dict]:
    assignments = _version_assignments(version_record)
    for assignment in reversed(assignments):
        if not assignment.get('resolved_date'):
            return assignment
    return None


def _track_assign(pkg_id: str, assignee: str, task: str, ctx=None,
                  assigned_date: str = '', assigned_by: Optional[str] = None,
                  message: str = '') -> dict:
    console = Console()
    if ctx is not None and ctx.obj and ctx.obj.get('notrack'):
        return {}

    task_clean = (task or '').strip().lower()
    if task_clean not in ASSIGNMENT_TASKS:
        raise ValueError(f"Unknown task '{task}'. Use one of: {', '.join(ASSIGNMENT_TASKS)}")

    who_assigned = assigned_by or _get_replicator()
    when_assigned = assigned_date or _today()

    packages, sha = _gh_api_get_packages()
    pkg = _pkg_record(packages, pkg_id)
    ver = _version_record(pkg, _current_version_number())
    assignment = _append_assignment(ver, assignee=assignee, task=task_clean,
                                    assigned_by=who_assigned, assigned_date=when_assigned)

    if assignee.lower() == 'authors':
        pkg['status'] = 'with-authors'
    elif assignee.lower() == 'de':
        pkg['status'] = 'with-de'
    elif assignee.lower() == 'me':
        pkg['status'] = 'with-me'
    else:
        pkg['status'] = 'assigned'

    commit_msg = message or f"track {pkg_id}: assign v{ver.get('version')} #{assignment['id']}"
    _gh_api_put_packages(packages, sha, commit_msg)
    console.print(f"[green]Assignment #{assignment['id']} set: {task_clean} → {assignee} ({when_assigned})[/green]")
    return assignment


def _track_resolve(pkg_id: str, resolution: str, ctx=None,
                   resolved_date: str = '', followup_assignee: Optional[str] = None,
                   resolved_by: Optional[str] = None) -> dict:
    console = Console()
    if ctx is not None and ctx.obj and ctx.obj.get('notrack'):
        return {}

    canonical_resolution = _normalise_resolution(resolution)
    who_resolved = resolved_by or _get_replicator()
    when_resolved = resolved_date or _today()

    packages, sha = _gh_api_get_packages()
    pkg = _pkg_record(packages, pkg_id)
    ver = _version_record(pkg, _current_version_number())
    active = _open_assignment(ver)
    if not active:
        raise ValueError('No open assignment found for this version. Use track assign first.')

    active['resolved_date'] = when_resolved
    active['resolved_by'] = who_resolved
    active['resolution'] = canonical_resolution

    next_assignee = (followup_assignee or _resolve_followup_assignee(canonical_resolution)).strip()
    next_task = _assign_task_for_assignee(next_assignee)
    next_assignment = _append_assignment(
        ver,
        assignee=next_assignee,
        task=next_task,
        assigned_by=who_resolved,
        assigned_date=when_resolved,
    )

    if next_assignee.lower() == 'authors':
        pkg['status'] = 'with-authors'
    elif next_assignee.lower() == 'de':
        pkg['status'] = 'with-de'
    elif next_assignee.lower() == 'me':
        pkg['status'] = 'with-me'
    else:
        pkg['status'] = 'assigned'

    _gh_api_put_packages(packages, sha,
                         f"track {pkg_id}: resolve v{ver.get('version')} #{active.get('id')} ({canonical_resolution})")
    console.print(
        f"[green]Resolved assignment #{active.get('id')} as {canonical_resolution}; "
        f"next assignment #{next_assignment.get('id')} → {next_assignee}[/green]"
    )
    return {'resolved': active, 'next': next_assignment}


def track_event(pkg_id: str, event: str, value: str = '', ctx=None, date: str = ''):
    """
    Update packages.toml with a tracking event. Silent on errors —
    tracking should never break the main workflow.
    Pass click ctx to respect --notrack flag.
    """
    console = Console()
    if ctx is not None and ctx.obj and ctx.obj.get('notrack'):
        return
    try:
        packages, sha = _gh_api_get_packages()
        pkg = _pkg_record(packages, pkg_id)
        ver_num = _current_version_number()
        today = date if date else _today()

        if event == 'received':
            if pkg.get('status') == 'withdrawn':
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
        subprocess.run(['git', 'fetch', 'origin'], check=True)

    # Get latest version and switch to it
    result = subprocess.run(['git', 'branch', '-r'], capture_output=True, text=True, check=True)
    versions = [line.strip() for line in result.stdout.split('\n') if 'version' in line]
    if versions:
        latest_version = max([int(v.split('version')[-1]) for v in versions if 'version' in v])
        subprocess.run(['git', 'switch', f'version{latest_version}'], check=True)


@cli.command()
@click.option('--no-commit', is_flag=True, help='Generate acceptance message without committing and pushing')
@click.option('--preview', is_flag=True,
              help='Preview acceptance message without commit/tag/community actions or tracking updates.')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def accept(ctx, no_commit, preview, notrack):
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
    dry_run = no_commit or preview

    if not _is_superuser() and not dry_run:
        click.echo('[ERROR] accept is restricted to superusers.', err=True)
        click.echo('Use --preview (or --no-commit) to render the acceptance message without modifying tracking.', err=True)
        sys.exit(1)

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

    # Commit and tag (unless --no-commit/--preview flag is set)
    if not dry_run:
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
        console.print("[yellow]Acceptance message generated in preview mode. No tracking or remote changes were made.[/yellow]")



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

    match = re.search(r'/records?/(\d+)', zenodo_url)
    if not match:
        click.echo('[ERROR] Could not parse Zenodo record ID from URL. Expected .../record/<id> or .../records/<id>.', err=True)
        sys.exit(1)

    record_id = match.group(1)
    _download_record_by_id(record_id, ctx=ctx, assign=assign)


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

    _download_record_by_id(record_id, ctx=ctx, assign=assign)


def _download_record_by_id(record_id, ctx=None, assign=None):
    """Download package from Zenodo draft by record ID and process it via the shared flow."""

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
@click.option('--preview', is_flag=True,
              help='Preview revision response without commit/push actions or tracking updates.')
@click.option('--needspackage', is_flag=True, help='Use needs-replication-package template')
@click.option('--track', 'track_resolution', default=None, flag_value='report',
              help='Track workflow update; optionally provide resolution (Accept, R&R, Minor, Question ME, Question DE, Question Authors).')
@click.option('--notrack', is_flag=True, default=False, help='Disable tracking for this invocation.')
@click.pass_context
def revise(ctx, branch_name, no_commit, preview, needspackage, track_resolution, notrack):
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

    dry_run = no_commit or preview

    # Commit changes (unless --no-commit/--preview flag is set)
    if not dry_run:
        subprocess.run(['git', 'add', report_file, 'response.txt'], check=True)
        subprocess.run(['git', 'commit', '-m', 'update report'], check=True)
        subprocess.run(['git', 'push', 'origin', branch_name], check=True)
        if track_resolution and not _is_superuser():
            click.echo('[ERROR] --track requires superuser = true in ~/.config/restud/config.toml', err=True)
            sys.exit(1)
        if track_resolution:
            track_event(get_current_folder(), 'report_sent', ctx=ctx)
            if track_resolution != 'report':
                _track_resolve(get_current_folder(), track_resolution, ctx=ctx)
    else:
        console = Console()
        console.print("[yellow]Revision response generated in preview mode. No tracking or remote changes were made.[/yellow]")


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


@track.command('assign')
@click.argument('assignee')
@click.argument('task', type=click.Choice(['replication', 'decision'], case_sensitive=False))
@click.option('--date', '-d', 'assigned_date', default=None,
              help='Set assigned_date: integer offset from today (e.g. -1 for yesterday) or MM-DD.')
@click.pass_context
def track_assign(ctx, assignee, task, assigned_date):
    """Assign current version to ASSIGNEE for TASK (replication|decision)."""
    pkg_id = ctx.obj['pkg_id']
    parsed_date = _parse_date_flag(assigned_date) if assigned_date else _today()
    _track_assign(pkg_id, assignee=assignee, task=task.lower(), ctx=ctx, assigned_date=parsed_date)


@track.command('resolve')
@click.argument('resolution')
@click.option('--assignee', '-a', default=None,
              help='Optional explicit assignee for the follow-up assignment.')
@click.option('--date', '-d', 'resolved_date', default=None,
              help='Set resolved_date: integer offset from today (e.g. -1 for yesterday) or MM-DD.')
@click.pass_context
def track_resolve(ctx, resolution, assignee, resolved_date):
    """Resolve the active assignment and create the next assignment."""
    pkg_id = ctx.obj['pkg_id']
    parsed_date = _parse_date_flag(resolved_date) if resolved_date else _today()
    _track_resolve(pkg_id, resolution=resolution, ctx=ctx,
                   resolved_date=parsed_date, followup_assignee=assignee)


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
        assignments = v.get('assignments', [])
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
        if assignments:
            open_assignment = next((a for a in reversed(assignments) if not a.get('resolved_date')), None)
            if open_assignment:
                console.print(
                    f"    Open assignment: #{open_assignment.get('id')} "
                    f"{open_assignment.get('task')} → {open_assignment.get('assignee')} "
                    f"(since {open_assignment.get('assigned_date')})"
                )


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
@click.option('--static', 'use_static', is_flag=True,
              help='Open published dashboard.html from admin repo instead of live rendering from packages.toml.')
@click.pass_context
def dashboard(ctx, use_static):
    """Open the package tracking dashboard in a browser."""
    import base64 as _b64
    import tempfile
    import webbrowser
    from html import escape

    def _open_html(html_text: str):
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
            f.write(html_text)
            tmp_path = f.name
        webbrowser.open(f'file://{tmp_path}')

    def _live_dashboard_html(packages: dict) -> str:
        status_counts = {}
        package_rows = []
        status_values = set()
        replicator_values = set()

        for pkg_id, pkg in sorted(packages.items()):
            status = pkg.get('status', '')
            status_counts[status] = status_counts.get(status, 0) + 1
            status_values.add(status or '')

            versions = pkg.get('versions', [])
            latest_version = max(versions, key=lambda item: int(item.get('version', 0) or 0)) if versions else {}
            replicator = latest_version.get('replicator', '') or ''
            if replicator:
                replicator_values.add(replicator)

            assignments = latest_version.get('assignments', [])
            open_assignment = next((a for a in reversed(assignments) if not a.get('resolved_date')), None)

            versions_rows = []
            for version in sorted(versions, key=lambda item: int(item.get('version', 0) or 0), reverse=True):
                assignment_items = version.get('assignments', [])
                if assignment_items:
                    assignment_lines = []
                    for assignment in assignment_items:
                        assignment_lines.append(
                            "<li>"
                            f"#{escape(str(assignment.get('id', '')))} "
                            f"{escape(str(assignment.get('task', '')))} → "
                            f"{escape(str(assignment.get('assignee', '')))} "
                            f"(assigned {escape(str(assignment.get('assigned_date', '')))}) "
                            f"{escape(str(assignment.get('resolution', '')))} "
                            f"{escape(str(assignment.get('resolved_date', '')))}"
                            "</li>"
                        )
                    assignments_html = f"<ul>{''.join(assignment_lines)}</ul>"
                else:
                    assignments_html = ""

                versions_rows.append(
                    "<tr>"
                    f"<td>{escape(str(version.get('version', '')))}</td>"
                    f"<td>{escape(str(version.get('replicator', '')))}</td>"
                    f"<td>{escape(str(version.get('date_downloaded', '')))}</td>"
                    f"<td>{escape(str(version.get('date_report_sent', '')))}</td>"
                    f"<td>{escape(str(version.get('date_decision_sent', '')))}</td>"
                    f"<td>{escape(str(version.get('recommendation', '')))}</td>"
                    f"<td>{escape(str(version.get('de_decision', '')))}</td>"
                    f"<td>{escape(str(version.get('hours', 0.0)))}</td>"
                    f"<td>{assignments_html}</td>"
                    "</tr>"
                )

            details_html = (
                "<table class='details-table'>"
                "<thead><tr>"
                "<th>Version</th><th>Replicator</th><th>Downloaded</th><th>Completed</th><th>Decision date</th>"
                "<th>Recommendation</th><th>Decision</th><th>Hours</th><th>Assignments</th>"
                "</tr></thead>"
                f"<tbody>{''.join(versions_rows)}</tbody>"
                "</table>"
            )

            package_rows.append({
                'pkg_id': pkg_id,
                'status': status,
                'version': latest_version.get('version', ''),
                'replicator': replicator,
                'open_task': (open_assignment or {}).get('task', ''),
                'open_assignee': (open_assignment or {}).get('assignee', ''),
                'open_since': (open_assignment or {}).get('assigned_date', ''),
                'hours': latest_version.get('hours', 0.0),
                'details_html': details_html,
            })

        counts_html = ''.join(
            f"<span class='pill'><b>{escape(k or 'unknown')}</b>: {v}</span>"
            for k, v in sorted(status_counts.items(), key=lambda item: item[0])
        )

        status_options = ''.join(
            f"<option value=\"{escape(s)}\">{escape(s or '(blank)')}</option>"
            for s in sorted(status_values)
        )
        replicator_options = ''.join(
            f"<option value=\"{escape(r)}\">{escape(r)}</option>"
            for r in sorted(replicator_values)
        )

        table_rows = []
        for row in package_rows:
            pkg_id = escape(str(row['pkg_id']))
            table_rows.append(
                f"<tr class='pkg-row' data-package='{pkg_id}' data-status='{escape(str(row['status']))}' "
                f"data-replicator='{escape(str(row['replicator']))}' data-version='{escape(str(row['version']))}' "
                f"data-hours='{escape(str(row['hours']))}'>"
                f"<td><button class='toggle' data-pkg='{pkg_id}'>+</button> {pkg_id}</td>"
                f"<td>{escape(str(row['status']))}</td>"
                f"<td>{escape(str(row['version']))}</td>"
                f"<td>{escape(str(row['replicator']))}</td>"
                f"<td>{escape(str(row['open_task']))}</td>"
                f"<td>{escape(str(row['open_assignee']))}</td>"
                f"<td>{escape(str(row['open_since']))}</td>"
                f"<td>{escape(str(row['hours']))}</td>"
                "</tr>"
                f"<tr class='details-row hidden' data-detail='{pkg_id}'>"
                f"<td colspan='8'>{row['details_html']}</td>"
                "</tr>"
            )

        table_rows_html = ''.join(table_rows)
        return f"""
<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>REStud Tracking Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif; margin: 24px; }}
        h1 {{ margin: 0 0 12px 0; }}
        .meta {{ color: #666; margin-bottom: 12px; }}
        .pill {{ display: inline-block; border: 1px solid #ccc; border-radius: 999px; padding: 4px 10px; margin: 4px 6px 4px 0; font-size: 12px; }}
        .controls {{ margin: 10px 0 14px 0; display: flex; gap: 12px; align-items: center; }}
        .controls label {{ font-size: 13px; color: #333; }}
        .controls select {{ margin-left: 6px; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 14px; font-size: 13px; }}
        th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
        th {{ background: #f4f4f4; position: sticky; top: 0; cursor: pointer; }}
        tr:nth-child(even) {{ background: #fafafa; }}
        .hidden {{ display: none; }}
        .toggle {{ width: 24px; height: 24px; }}
        .details-row td {{ background: #fcfcfc; }}
        .details-table {{ margin: 8px 0 4px 0; font-size: 12px; }}
        .details-table th {{ position: static; cursor: default; }}
        .details-table ul {{ margin: 4px 0; padding-left: 18px; }}
    </style>
</head>
<body>
    <h1>REStud Tracking Dashboard (Live)</h1>
    <div class=\"meta\">Source: {escape(ADMIN_ORG)}/{escape(ADMIN_REPO)}/{escape(ADMIN_FILE)}</div>
    <div>{counts_html}</div>
    <div class="controls">
        <label>Status
            <select id="statusFilter">
                <option value="">All</option>
                {status_options}
            </select>
        </label>
        <label>Replicator
            <select id="replicatorFilter">
                <option value="">All</option>
                {replicator_options}
            </select>
        </label>
    </div>
    <table>
        <thead>
            <tr>
                <th data-key="package">Package</th><th data-key="status">Status</th><th data-key="version">Version</th><th data-key="replicator">Replicator</th>
                <th data-key="open_task">Open task</th><th data-key="open_assignee">Open assignee</th><th data-key="open_since">Open since</th>
                <th data-key="hours">Hours</th>
            </tr>
        </thead>
        <tbody id="packagesBody">
            {table_rows_html}
        </tbody>
    </table>
    <script>
        const sortState = {{ key: 'package', asc: true }};

        function getPkgRows() {{
            return Array.from(document.querySelectorAll('tr.pkg-row'));
        }}

        function applyFilters() {{
            const status = document.getElementById('statusFilter').value;
            const replicator = document.getElementById('replicatorFilter').value;
            for (const row of getPkgRows()) {{
                const okStatus = !status || row.dataset.status === status;
                const okRep = !replicator || row.dataset.replicator === replicator;
                const show = okStatus && okRep;
                row.style.display = show ? '' : 'none';
                const detail = document.querySelector(`tr.details-row[data-detail="${{row.dataset.package}}"]`);
                if (detail) {{
                    detail.style.display = show && !detail.classList.contains('hidden') ? '' : 'none';
                }}
            }}
        }}

        function sortBy(key) {{
            const body = document.getElementById('packagesBody');
            const rows = getPkgRows();
            sortState.asc = sortState.key === key ? !sortState.asc : true;
            sortState.key = key;

            rows.sort((a, b) => {{
                let av = '';
                let bv = '';
                if (key === 'package') {{
                    av = a.dataset.package || '';
                    bv = b.dataset.package || '';
                }} else if (key === 'status') {{
                    av = a.dataset.status || '';
                    bv = b.dataset.status || '';
                }} else if (key === 'version') {{
                    av = Number(a.dataset.version || 0);
                    bv = Number(b.dataset.version || 0);
                }} else if (key === 'replicator') {{
                    av = a.dataset.replicator || '';
                    bv = b.dataset.replicator || '';
                }} else if (key === 'hours') {{
                    av = Number(a.dataset.hours || 0);
                    bv = Number(b.dataset.hours || 0);
                }} else {{
                    av = (a.children[0]?.textContent || '').trim();
                    bv = (b.children[0]?.textContent || '').trim();
                }}

                let cmp = 0;
                if (typeof av === 'number' && typeof bv === 'number') {{
                    cmp = av - bv;
                }} else {{
                    cmp = String(av).localeCompare(String(bv));
                }}
                return sortState.asc ? cmp : -cmp;
            }});

            for (const row of rows) {{
                const detail = document.querySelector(`tr.details-row[data-detail="${{row.dataset.package}}"]`);
                body.appendChild(row);
                if (detail) body.appendChild(detail);
            }}
            applyFilters();
        }}

        document.querySelectorAll('th[data-key]').forEach((th) => {{
            th.addEventListener('click', () => sortBy(th.dataset.key));
        }});

        document.querySelectorAll('button.toggle').forEach((btn) => {{
            btn.addEventListener('click', () => {{
                const pkg = btn.dataset.pkg;
                const detail = document.querySelector(`tr.details-row[data-detail="${{pkg}}"]`);
                if (!detail) return;
                const nowHidden = detail.classList.toggle('hidden');
                btn.textContent = nowHidden ? '+' : '−';
                applyFilters();
            }});
        }});

        document.getElementById('statusFilter').addEventListener('change', applyFilters);
        document.getElementById('replicatorFilter').addEventListener('change', applyFilters);
        sortBy('package');
    </script>
</body>
</html>
"""

    console = Console()
    if not use_static:
        try:
            packages, _ = _gh_api_get_packages()
            _open_html(_live_dashboard_html(packages))
            console.print("[green]Live dashboard opened in browser.[/green]")
            return
        except Exception as e:
            console.print(f"[yellow]Live dashboard failed ({e}); falling back to static dashboard.html.[/yellow]")

    result = subprocess.run(
        ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/dashboard.html'],
        capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]dashboard.html not found in {ADMIN_ORG}/{ADMIN_REPO}.[/red]")
        console.print("[yellow]Trigger the workflow on GitHub (Actions → Generate Dashboard → Run workflow).[/yellow]")
        return
    data = json.loads(result.stdout)
    html = _b64.b64decode(data['content']).decode('utf-8')
    _open_html(html)
    console.print("[green]Static dashboard opened in browser.[/green]")


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
        assignee = assign or _get_replicator()
        track_event(pkg_id, 'replicator', assignee, ctx=ctx)
        _track_assign(pkg_id, assignee=assignee, task='replication', ctx=ctx,
                      assigned_by=_get_replicator(), assigned_date=_today(),
                      message=f"track {pkg_id}: downloaded/assigned (zenodo {record_id})")
        console.print(f"[green]Tracking updated for {pkg_id}: downloaded and assigned to {assignee} (zenodo {record_id})[/green]")
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
        assignee = assign or _get_replicator()
        track_event(pkg_id, 'replicator', assignee, ctx=ctx)
        _track_assign(pkg_id, assignee=assignee, task='replication', ctx=ctx,
                      assigned_by=_get_replicator(), assigned_date=_today(),
                      message=f"track {pkg_id}: downloaded/assigned (zenodo {record_id})")
        console.print(f"[green]Tracking updated for {pkg_id}: downloaded and assigned to {assignee} (zenodo {record_id})[/green]")

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
    def _make_writable_recursive(path):
        for root, dirnames, filenames in os.walk(path, topdown=False):
            for filename in filenames:
                try:
                    os.chmod(os.path.join(root, filename), stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
            for dirname in dirnames:
                try:
                    os.chmod(os.path.join(root, dirname), stat.S_IRWXU)
                except OSError:
                    pass
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass

    def _on_rm_error(func, path, exc_info):
        if isinstance(exc_info[1], PermissionError):
            parent = os.path.dirname(path) or '.'
            try:
                os.chmod(parent, stat.S_IRWXU)
            except OSError:
                pass
            try:
                os.chmod(path, stat.S_IRWXU)
            except OSError:
                pass
            func(path)
            return
        raise exc_info[1]

    dirs = [d for d in os.listdir('.') if os.path.isdir(d)]
    if dirs:
        click.echo("Removing previous directories!")
        for d in dirs:
            if d not in ['.git']:
                _make_writable_recursive(d)
                shutil.rmtree(d, onerror=_on_rm_error)
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
