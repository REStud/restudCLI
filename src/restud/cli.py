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
from rich.console import Console
from rich.prompt import Prompt

from restud.render_jinja2 import ReportRenderer
from restud.render_aml import AMLReportRenderer
from restud.tracking import (
    _get_local_config,
    _get_replicator,
    _gh_api_get_packages,
    _is_superuser,
    _resolve_assign,
    _resolve_track_assign_args,
    _save_local_config,
    _today,
    _track_assign,
    _track_resolve,
    track_event,
)
from restud.zenodo import (
    _add_manuscript_id_to_report,
    _download_record_by_id,
    _get_zenodo_key,
)

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
@click.argument('args', nargs=-1)
@click.option('--date', '-d', 'assigned_date', default=None,
              help='Set assigned_date: integer offset from today (e.g. -1 for yesterday) or MM-DD.')
@click.pass_context
def track_assign(ctx, args, assigned_date):
    """Assign current version using `track assign ASSIGNEE TASK` or `track assign TASK`."""
    pkg_id = ctx.obj['pkg_id']
    assignee, task = _resolve_track_assign_args(args)
    parsed_date = _parse_date_flag(assigned_date) if assigned_date else _today()
    _track_assign(pkg_id, assignee=assignee, task=task, ctx=ctx, assigned_date=parsed_date)


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
            pip_cmd = [sys.executable, '-m', 'pip', 'install', '--upgrade', '--no-cache-dir', '--user']
            # ACCRE/PanFS can fail with Errno 16 during force-reinstall cleanup.
            if accre:
                pip_cmd.append('--ignore-installed')
            else:
                pip_cmd.append('--force-reinstall')
            pip_cmd.append(git_url)

            env = os.environ.copy()
            tmp_parent = os.path.expanduser('~/.tmp')
            tmp_root = os.path.join(tmp_parent, 'restud')
            os.makedirs(tmp_parent, exist_ok=True)
            os.makedirs(tmp_root, exist_ok=True)

            def _run_pip_once() -> None:
                with tempfile.TemporaryDirectory(prefix='restud-pip-', dir=tmp_root) as tmp_dir:
                    pip_env = env.copy()
                    pip_env['TMPDIR'] = tmp_dir
                    pip_env['TMP'] = tmp_dir
                    pip_env['TEMP'] = tmp_dir
                    subprocess.run(pip_cmd, check=True, env=pip_env)

            try:
                _run_pip_once()
            except subprocess.CalledProcessError as e:
                if accre and ('Errno 16' in str(e) or 'build-tracker' in str(e) or 'Errno 2' in str(e)):
                    console.print("[yellow]Retrying pip install after temporary filesystem/build-tracker error...[/yellow]")
                    _run_pip_once()
                else:
                    raise
            finally:
                # Clean up only ~/.tmp/restud and keep ~/.tmp intact.
                shutil.rmtree(tmp_root, ignore_errors=True)
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
