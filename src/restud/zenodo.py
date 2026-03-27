"""Zenodo download and version-branch helpers for restud CLI."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import date

import click
import requests
import yaml
from tqdm import tqdm
from rich.console import Console
from rich.prompt import Prompt

from importlib.resources import files

from restud.tracking import _get_replicator, _today, _track_assign, track_event


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


def _get_zenodo_key():
    """Get Zenodo API key from config file."""
    key_file = os.path.expanduser('~/.config/.zenodo_api_key')
    with open(key_file, 'r') as f:
        return f.read().strip()


def _get_cookie() -> str:
    """Return Zenodo session cookie from ~/.config/restud/restud-cookie.json."""
    cookie_file = os.path.expanduser('~/.config/restud/restud-cookie.json')
    if not os.path.exists(cookie_file):
        raise FileNotFoundError('Cookie file not found: ~/.config/restud/restud-cookie.json')

    with open(cookie_file, 'r') as f:
        data = json.load(f)

    value = str(data.get('value', '')).strip()
    exp_date = str(data.get('exp_date', '')).strip()
    if not value:
        raise ValueError('Cookie value is missing in restud-cookie.json')

    if exp_date:
        try:
            if date.fromisoformat(exp_date) < date.today():
                raise ValueError('Saved cookie has expired. Please create a new cookie.')
        except ValueError as exc:
            raise ValueError(f'Invalid cookie expiration date: {exp_date}') from exc

    return value


def _try_get_zenodo_key():
    """Get Zenodo API key from config file, if present."""
    key_file = os.path.expanduser('~/.config/.zenodo_api_key')
    if not os.path.exists(key_file):
        return None
    with open(key_file, 'r') as f:
        key = f.read().strip()
    return key or None


def _fetch_zenodo_record(record_id: str, console: Console) -> tuple[dict, dict]:
    """Fetch Zenodo metadata for either a draft or a published record."""
    draft_url = f"https://zenodo.org/api/records/{record_id}/draft"
    published_url = f"https://zenodo.org/api/records/{record_id}"
    zenodo_key = _try_get_zenodo_key()

    draft_response = None
    if zenodo_key:
        draft_response = requests.get(draft_url, params={'access_token': zenodo_key})
        if draft_response.status_code == 200:
            return draft_response.json(), {
                'kind': 'draft',
                'metadata_url': draft_url,
                'zenodo_key': zenodo_key,
            }

    published_response = requests.get(published_url)
    if published_response.status_code == 200:
        return published_response.json(), {
            'kind': 'published',
            'metadata_url': f'https://zenodo.org/records/{record_id}',
            'zenodo_key': None,
        }

    console.print(f"[red]Error: Could not access draft or published record {record_id}[/red]")
    if draft_response is not None:
        console.print(f"[red]Draft status code: {draft_response.status_code}[/red]")
        console.print(f"[red]Draft response: {draft_response.text}[/red]")
    else:
        console.print("[red]Draft lookup skipped: no Zenodo API key found in ~/.config/.zenodo_api_key[/red]")
    console.print(f"[red]Published status code: {published_response.status_code}[/red]")
    console.print(f"[red]Published response: {published_response.text}[/red]")
    sys.exit(1)


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


def _download_record_by_id(record_id, ctx=None, assign=None):
    """Download package from a Zenodo draft or published record by record ID."""

    console = Console()
    console.print(f"[blue]Fetching file list for record {record_id}...[/blue]")
    data, record_info = _fetch_zenodo_record(record_id, console)

    # Get files from the response
    if 'files' not in data or not data['files']:
        console.print(f"[red]Error: No files found in this {record_info['kind']} record[/red]")
        sys.exit(1)

    files = data['files']

    # Download all files
    console.print(f"[yellow]Found {len(files)} file(s):[/yellow]")
    for idx, file_info in enumerate(files, 1):
        size_mb = file_info.get('size', 0) / (1024 * 1024)
        console.print(f"  {idx}. {file_info['key']} ({size_mb:.2f} MB)")

    _download_multiple_files(record_id, files, record_info, ctx=ctx, assign=assign)


def _download_multiple_files(record_id, files, record_info, ctx=None, assign=None):
    """Download multiple files from a Zenodo draft or published record.

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
        links = file_info.get('links', {})
        download_url = (
            links.get('content') or
            links.get('download') or
            links.get('self') or
            f"https://zenodo.org/api/records/{record_id}{'/draft' if record_info['kind'] == 'draft' else ''}/files/{filename}/content"
        )

        console.print(f"[blue]Downloading {filename}...[/blue]")

        request_kwargs = {'stream': True}
        if record_info['kind'] == 'draft' and record_info['zenodo_key']:
            request_kwargs['params'] = {'access_token': record_info['zenodo_key']}
        response = requests.get(download_url, **request_kwargs)

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
    _save_zenodo_metadata(record_info['metadata_url'])
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
                subprocess.run(['chmod', '-R', 'u+w', '.'], check=False)
                subprocess.run(['git', 'checkout', f'version{latest_version}'], check=False)
                subprocess.run(['chmod', '-R', 'u+w', '.'], check=False)
                subprocess.run(['git', 'clean', '-fd'], check=False)
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


def _empty_folder():
    """Remove directories from current folder."""

    def _make_writable_recursive(path):
        for root, dirnames, filenames in os.walk(path, topdown=False):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                try:
                    os.chmod(full_path, stat.S_IWRITE | stat.S_IREAD)
                except Exception:
                    pass
            for dirname in dirnames:
                full_path = os.path.join(root, dirname)
                try:
                    os.chmod(full_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                except Exception:
                    pass
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        except Exception:
            pass

    def _on_rm_error(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            func(path)
            return
        except Exception:
            pass
        _make_writable_recursive(path)
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            func(path)
            return
        except Exception:
            pass
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
    match = re.search(r'/(\d+)(?:/|$|\?)', url)
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
