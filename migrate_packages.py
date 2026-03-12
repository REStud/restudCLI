"""
One-time migration: read Package information(Packages).csv → push to packages.toml on GitHub.
Run with: .venv/bin/python migrate_packages.py
"""
import csv, json, base64, subprocess, toml
from datetime import datetime

CSV_PATH = 'Package information(Packages).csv'
ADMIN_ORG  = 'REStud'
ADMIN_REPO = 'packages-admin'
ADMIN_FILE = 'packages.toml'


def parse_date(s):
    if not s or not s.strip():
        return ''
    try:
        return datetime.strptime(s.strip(), '%d-%b-%Y').strftime('%Y-%m-%d')
    except ValueError:
        return ''


def parse_hours(s):
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return 0.0


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
        pkg['versions'].append({
            'version':            r['version'],
            'replicator':         r['replicator'],
            'zenodo_id':          r['zenodo'],
            'date_downloaded':    parse_date(r['date_assigned']),
            'date_report_sent':   parse_date(r['date_completed']),
            'date_decision_sent': parse_date(r['decision_date']),
            'hours':              parse_hours(r['hours']),
            'recommendation':     r['recommendation'],
            'de_decision':        r['decision'],
            'software':           software_list,
            'data_availability':  r['data_availability'],
            'comments':           r['comments'],
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
    print(f'Done! Imported {len(toml_packages)} packages.')
else:
    print('Error:', r.stderr)
    print(r.stdout)
