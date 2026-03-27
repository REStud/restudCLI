"""Tracking and assignment helpers for restud CLI."""

import json
import os
import subprocess
from typing import Optional

import click
import toml
from rich.console import Console
from rich.prompt import Prompt

# Admin repo for package tracking (accessed via GitHub API only — no local clone)
ADMIN_ORG = 'REStud'
ADMIN_REPO = 'packages-admin'
ADMIN_FILE = 'packages.toml'

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


def _resolve_track_assign_args(args) -> tuple[str, str]:
    """Allow `track assign ASSIGNEE TASK` or `track assign TASK` with assignee picker."""
    if len(args) == 2:
        assignee, task = args
    elif len(args) == 1:
        assignee, task = _resolve_assign(''), args[0]
    else:
        raise click.UsageError("Usage: restud track assign [ASSIGNEE] TASK")

    task_clean = task.lower().strip()
    if task_clean not in ASSIGNMENT_TASKS:
        raise click.UsageError(f"TASK must be one of: {', '.join(ASSIGNMENT_TASKS)}")
    if not assignee:
        raise click.UsageError('No assignee selected.')
    return assignee, task_clean


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


def _get_git_branch() -> Optional[str]:
    try:
        result = subprocess.run(['git', 'symbolic-ref', '--short', 'HEAD'],
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return None


def _current_version_number() -> int:
    """Return integer from current git branch (e.g. version2 → 2), or 1."""
    branch = _get_git_branch() or 'version1'
    try:
        return int(branch.replace('version', ''))
    except ValueError:
        return 1


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
