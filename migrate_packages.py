"""
One-time migration: read Package information(Packages).csv and push an assignment-aware
packages.toml to GitHub.

Run with: .venv/bin/python migrate_packages.py
"""
import base64
import csv
import json
import subprocess
from datetime import datetime

import toml

CSV_PATH = 'Package information(Packages).csv'
ADMIN_ORG  = 'REStud'
ADMIN_REPO = 'packages-admin'
ADMIN_FILE = 'packages.toml'


DE_ASSIGNEE = 'Andrea'
DE_ID = 'de'


def parse_date(s):
    if not s or not s.strip():
        return ''
    raw = s.strip()
    for fmt in ('%d-%b-%Y', '%d-%b-%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


def parse_hours(s):
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return 0.0


def normalise_resolution(value):
    key = (value or '').strip().lower()
    mapping = {
        'accepted': 'Accept',
        'accept': 'Accept',
        'minor': 'Minor',
        'r&r': 'R&R',
        'rr': 'R&R',
        'question me': 'Question ME',
        'question de': 'Question DE',
        'question authors': 'Question Authors',
    }
    if key in mapping:
        return mapping[key]
    return (value or '').strip()


def version_assignments(row_data):
    """Infer assignment flow from CSV dates.

    Flow:
      1) DE (downloader) assignment at date_received
      2) Replicator assignment at date_assigned
      3) DE assignment on date_completed (replicator recommendation handed back)
      4) DE decision on decision_date
    """
    assignments = []

    date_received = parse_date(row_data['date_received'])
    date_assigned = parse_date(row_data['date_assigned'])
    date_completed = parse_date(row_data['date_completed'])
    decision_date = parse_date(row_data['decision_date'])
    replicator = row_data['replicator'] or 'unknown'
    recommendation = normalise_resolution(row_data['recommendation'])
    decision = normalise_resolution(row_data['decision'])

    assignment_id = 1

    if date_received:
        assignments.append({
            'id': assignment_id,
            'task': 'replication',
            'assignee': DE_ASSIGNEE,
            'assigned_by': DE_ASSIGNEE,
            'assigned_date': date_received,
            'resolved_date': date_assigned or date_received,
            'resolved_by': DE_ASSIGNEE,
            'resolution': 'Downloaded',
        })
        assignment_id += 1

    if date_assigned or replicator:
        assignments.append({
            'id': assignment_id,
            'task': 'replication',
            'assignee': replicator,
            'assigned_by': DE_ASSIGNEE,
            'assigned_date': date_assigned or date_received,
            'resolved_date': date_completed,
            'resolved_by': replicator if date_completed else '',
            'resolution': recommendation if date_completed else '',
        })
        assignment_id += 1

    if date_completed:
        assignments.append({
            'id': assignment_id,
            'task': 'decision',
            'assignee': DE_ID,
            'assigned_by': replicator,
            'assigned_date': date_completed,
            'resolved_date': decision_date,
            'resolved_by': DE_ASSIGNEE if decision_date else '',
            'resolution': decision if decision_date else '',
        })

    return assignments


def map_status(s):
    s = s.strip().lower()
    if '1. accepted' in s or s == 'accepted':
        return 'accepted'
    elif '2. resubmitted' in s or 'resubmitted' in s:
        return 'resubmitted'
    elif '3. with authors' in s or s == 'with authors':
        return 'with-authors'
    elif s == 'assigned':
        return 'assigned'
    elif 'with de' in s:
        return 'with-de'
    elif 'with me' in s:
        return 'with-me'
    elif 'q authors' in s:
        return 'q-authors'
    elif s == '':
        return 'new'
    else:
        return s


# ---- read CSV ---------------------------------------------------------------
with open(CSV_PATH) as f:
    rows = list(csv.reader(f))

# Row 0: group headers, Row 1: column headers — skip both
data_rows = rows[2:]

# Collect all rows per manuscript, grouped by version
packages_raw = {}
for row in data_rows:
    if len(row) < 3 or not row[2].strip():
        continue
    ms = row[2].strip()
    try:
        version = int(row[1].strip())
    except (ValueError, IndexError):
        version = 1
    packages_raw.setdefault(ms, []).append({
        'status':            row[0].strip()  if len(row) > 0  else '',
        'version':           version,
        'zenodo':            row[3].strip()  if len(row) > 3  else '',
        'date_received':     row[5].strip()  if len(row) > 5  else '',
        'replicator':        row[6].strip()  if len(row) > 6  else '',
        'date_assigned':     row[7].strip()  if len(row) > 7  else '',
        'decision_date':     row[8].strip()  if len(row) > 8  else '',
        'decision':          row[9].strip()  if len(row) > 9  else '',
        'date_completed':    row[10].strip() if len(row) > 10 else '',
        'recommendation':    row[11].strip() if len(row) > 11 else '',
        'hours':             row[12].strip() if len(row) > 12 else '',
        'data_availability': row[13].strip() if len(row) > 13 else '',
        'software':          row[15].strip() if len(row) > 15 else '',
        'comments':          row[16].strip() if len(row) > 16 else '',
    })

# ---- build toml structure ---------------------------------------------------
toml_packages = {}
for ms, ms_rows in packages_raw.items():
    ms_rows_sorted = sorted(ms_rows, key=lambda r: r['version'])
    latest = ms_rows_sorted[-1]
    first  = ms_rows_sorted[0]

    # date_accepted: first row with decision == 'Accepted'
    date_accepted = ''
    for r in ms_rows_sorted:
        if r['decision'].strip().lower() == 'accepted':
            date_accepted = parse_date(r['decision_date']) or parse_date(r['date_received'])
            break

    pkg = {
        'manuscript_id': ms,
        'date_received': parse_date(first['date_received']),
        'date_accepted': date_accepted,
        'status':        map_status(latest['status']),
        'versions':      [],
    }

    for r in ms_rows_sorted:
        software_list = [s.strip() for s in r['software'].split(',') if s.strip()]
        zenodo_id = r['zenodo']
        zenodo_url = f"https://zenodo.org/records/{zenodo_id}" if zenodo_id else ''

        pkg['versions'].append({
            'version':            r['version'],
            'replicator':         r['replicator'],
            'zenodo_id':          zenodo_id,
            'zenodo_url':         zenodo_url,
            'date_downloaded':    parse_date(r['date_received']),
            'date_report_sent':   parse_date(r['date_completed']),
            'date_decision_sent': parse_date(r['decision_date']),
            'hours':              parse_hours(r['hours']),
            'recommendation':     normalise_resolution(r['recommendation']),
            'de_decision':        normalise_resolution(r['decision']),
            'software':           software_list,
            'data_availability':  r['data_availability'],
            'comments':           r['comments'],
            'assignments':        version_assignments(r),
        })

    toml_packages[ms] = pkg

# ---- push to GitHub ---------------------------------------------------------
result = subprocess.run(
    ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/{ADMIN_FILE}'],
    capture_output=True, text=True)
if result.returncode != 0:
    print('Error fetching file:', result.stderr)
    exit(1)

sha = json.loads(result.stdout)['sha']
content_b64 = base64.b64encode(
    toml.dumps({'packages': toml_packages}).encode()
).decode('ascii')

r = subprocess.run(
    ['gh', 'api', f'repos/{ADMIN_ORG}/{ADMIN_REPO}/contents/{ADMIN_FILE}',
     '-X', 'PUT',
     '-f', 'message=Import packages from CSV',
     '-f', f'content={content_b64}',
     '-f', f'sha={sha}'],
    capture_output=True, text=True)

if r.returncode == 0:
    print(f'Done! Imported {len(toml_packages)} packages with assignment timelines.')
else:
    print('Error:', r.stderr)
    print(r.stdout)
