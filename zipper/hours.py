"""zipper.hours

The Luminosity timesheet, as a ledger rather than a spreadsheet.

The spreadsheet stays the system of record -- it is what he copies into Workday
on Thursday nights, and an hour that never reaches it is an hour he is not paid
for. So nothing here replaces it. This module holds what has been *captured*
(told to Zipper, usually over Discord) and tracks how far each entry has got:

    pending    captured here, not yet in the sheet
    in_sheet   the extension has seen it in the sheet
    submitted  the sheet's Submitted column carries a date

`pending` is the only state that means someone still owes an action, which is
why it is the only one that raises a flag. Everything else is reporting.

The sheet is one tab per semester, so row positions never shift under an older
term, and the writer only ever has to understand the current one.
"""
import json
import os
import re
import datetime as dt

from .core import INBOX

LEDGER = os.path.join(INBOX, 'hours.json')

# Which Google Sheet is the timesheet. A document id is not a credential, but it
# points straight at a private file, so it lives in .env with everything else
# personal rather than in the extension -- whose manifest is published. The
# panel matches spreadsheets in general and asks the server which one is his,
# which also means moving to a new sheet is one line here and no rebuild there.
SHEET_ID = os.environ.get('ZIPPER_SHEET_ID', '').strip()

# A week is Monday-based, matching the sheet's own `Week N` ranges.
def _monday(d):
    return d - dt.timedelta(days=d.weekday())


def _load():
    try:
        with open(LEDGER) as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.setdefault('version', 1)
    d.setdefault('entries', [])
    d.setdefault('sheet', {})
    return d


def _save(d):
    os.makedirs(INBOX, exist_ok=True)
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, indent=1, sort_keys=False)
    os.replace(tmp, LEDGER)


# ---------------------------------------------------------------- time format
#
# His rule, verbatim: 24-hour for any period that crosses noon, 12-hour
# otherwise. It exists because the Total column is a plain subtraction -- a
# 10:00 to 2:00 span would come out negative -- and not because the sheet has
# any opinion about clocks. So it is a *rendering* rule: the ledger always
# stores 24-hour, and the shape is chosen only at the moment a row is written.

def _hm(s):
    m = re.match(r'^\s*(\d{1,2}):(\d{2})\s*$', str(s or ''))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi


def sheet_times(start, end):
    """(start, end) in 24h -> the pair as this sheet would write them."""
    a, b = _hm(start), _hm(end)
    if a is None or b is None:
        return '', ''
    crosses = a < 12 * 60 <= b
    def f(mins):
        h, mi = divmod(mins, 60)
        if not crosses and h > 12:
            h -= 12
        return f'{h}:{mi:02d}'
    return f(a), f(b)


def duration(start, end):
    a, b = _hm(start), _hm(end)
    if a is None or b is None:
        return None
    return round((b - a) / 60.0, 4)


def hhmmss(hours):
    total = int(round(float(hours) * 3600))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}'


# ------------------------------------------------------------------- identity
#
# An entry has to be recognisable in the sheet after a round trip, and the only
# things that survive are the date, the times and the note -- he may reword a
# note, so the note is not part of the key. Date plus start plus end is unique
# in every term of the existing sheet; where a row has no times (carried-over
# hours) the duration stands in for them.

def key_of(date, start, end, hours):
    st, en = (start or ''), (end or '')
    if st and en:
        return f'{date}|{st}|{en}'
    return f'{date}|~{hhmmss(hours or 0)}'


def add(date, start=None, end=None, hours=None, note='', source='discord'):
    """Capture one entry. Times optional; hours required if they are absent."""
    d = _load()
    date = str(date)
    dt.date.fromisoformat(date)              # raises on a malformed date
    if start and end:
        hours = duration(start, end)
        if hours is None:
            raise ValueError('start/end must be HH:MM in 24-hour time')
        if hours <= 0:
            raise ValueError(f'{start}-{end} is not a positive span in 24h time')
    if hours is None:
        raise ValueError('need either --start and --end, or --hours')
    k = key_of(date, start, end, hours)
    for e in d['entries']:
        if e['key'] == k:
            raise ValueError(f'already captured: {k} ({e["state"]})')
    e = {'key': k, 'date': date, 'start': start or '', 'end': end or '',
         'hours': round(float(hours), 4), 'note': note.strip(),
         'state': 'pending', 'submitted': None, 'source': source,
         'captured': dt.datetime.now().isoformat(timespec='seconds')}
    d['entries'].append(e)
    d['entries'].sort(key=lambda x: (x['date'], x['start'] or '~'))
    _save(d)
    return e


def remove(key):
    d = _load()
    before = len(d['entries'])
    d['entries'] = [e for e in d['entries'] if e['key'] != key]
    _save(d)
    return before - len(d['entries'])


# ------------------------------------------------------------- reconciliation

class PartialSnapshot(Exception):
    """A read that cannot be trusted to mean rows were deleted."""


def reconcile(rows, tab=None, complete=False, force=False):
    """Take a snapshot of the sheet and make the ledger agree with it.

    The sheet wins on everything it knows. A captured row found there becomes
    `in_sheet`; a row he typed straight into the sheet is adopted, because the
    ledger claiming to be complete while the sheet has more is the one failure
    that would quietly under-report his hours.

    Deletion is the dangerous direction and is treated as such. A browser can
    read a half-rendered page, and a snapshot that is merely *short* looks
    exactly like a tab he emptied -- so rows are only removed when the caller
    says it read the whole tab (`complete`), and even then not if that would
    discard a quarter of what is on file. Getting this wrong costs real money,
    since a row that leaves the ledger stops being chased into the sheet.

    `rows` are dicts: date, start, end, hours, note, submitted.
    """
    d = _load()
    by_key = {e['key']: e for e in d['entries']}
    seen, adopted, confirmed = set(), 0, 0
    for r in rows:
        date = (r.get('date') or '').strip()
        if not date:
            continue
        hours = r.get('hours')
        if hours is None:
            hours = duration(r.get('start'), r.get('end'))
        if hours is None:
            continue
        k = key_of(date, r.get('start'), r.get('end'), hours)
        seen.add(k)
        sub = (r.get('submitted') or '').strip() or None
        e = by_key.get(k)
        if e is None:
            e = {'key': k, 'date': date, 'start': r.get('start') or '',
                 'end': r.get('end') or '', 'hours': round(float(hours), 4),
                 'note': (r.get('note') or '').strip(),
                 'state': 'submitted' if sub else 'in_sheet',
                 'submitted': sub, 'source': 'sheet',
                 'captured': dt.datetime.now().isoformat(timespec='seconds')}
            d['entries'].append(e)
            by_key[k] = e
            adopted += 1
        else:
            if e['state'] == 'pending':
                confirmed += 1
            # The note is the sheet's to own once the row exists there -- he
            # edits wording in place and the ledger should not fight him.
            e['note'] = (r.get('note') or e['note']).strip()
            e['submitted'] = sub
            e['state'] = 'submitted' if sub else 'in_sheet'

    # A row that was in the sheet and is now gone was deleted there on purpose
    # -- but only if we actually saw the whole sheet. Otherwise leave it be.
    known = [e for e in d['entries'] if e['state'] in ('in_sheet', 'submitted')]
    dropped = [e for e in known if e['key'] not in seen]
    if dropped and not complete:
        dropped = []
    elif dropped and not force and len(dropped) > max(2, 0.25 * len(known)):
        raise PartialSnapshot(
            f'{len(dropped)} of {len(known)} rows missing from the snapshot; '
            f'refusing to drop them. Re-read the sheet, or pass force.')
    for e in dropped:
        d['entries'].remove(e)

    d['entries'].sort(key=lambda x: (x['date'], x['start'] or '~'))
    if tab:
        d['sheet']['tab'] = tab
    d['sheet']['fetched'] = dt.datetime.now().isoformat(timespec='seconds')
    d['sheet']['rows'] = len(rows)
    _save(d)
    return {'confirmed': confirmed, 'adopted': adopted,
            'dropped': len(dropped), 'pending': len(pending())}


def pending():
    return [e for e in _load()['entries'] if e['state'] == 'pending']


def to_write():
    """Pending entries as the sheet wants them: 6 cells, his time convention."""
    out = []
    for e in sorted(pending(), key=lambda x: (x['date'], x['start'] or '~')):
        st, en = sheet_times(e['start'], e['end'])
        d = dt.date.fromisoformat(e['date'])
        out.append({'key': e['key'],
                    'week': _monday(d).isoformat(),
                    'cells': [d.strftime('%m/%d/%Y'), st, en,
                              hhmmss(e['hours']), e['note'], '']})
    return out


# ------------------------------------------------------------------ reporting

CAP = 20.0     # hours submittable per week; the rest is banked


def weeks():
    d = _load()
    acc = {}
    for e in d['entries']:
        w = _monday(dt.date.fromisoformat(e['date'])).isoformat()
        a = acc.setdefault(w, {'week': w, 'worked': 0.0, 'submitted': 0.0,
                               'pending': 0.0})
        a['worked'] += e['hours']
        if e['state'] == 'submitted':
            a['submitted'] += e['hours']
        elif e['state'] == 'pending':
            a['pending'] += e['hours']
    for a in acc.values():
        a['banked'] = round(a['worked'] - a['submitted'], 2)
        for k in ('worked', 'submitted', 'pending'):
            a[k] = round(a[k], 2)
    return [acc[k] for k in sorted(acc)]


def flags():
    """Conditions for the brief. Pending is the only one that owes an action."""
    out = []
    p = pending()
    if p:
        total = round(sum(e['hours'] for e in p), 2)
        oldest = min(e['date'] for e in p)
        age = (dt.date.today() - dt.date.fromisoformat(oldest)).days
        out.append(f'{len(p)} hour entr{"y" if len(p)==1 else "ies"} '
                   f'({total}h) not yet in the sheet, oldest {oldest} ({age}d)')
    banked = round(sum(w['banked'] for w in weeks()), 2)
    if banked >= CAP:
        out.append(f'{banked}h worked but not submitted — over a full week banked')
    return out


def pull():
    """Read the sheet and make the ledger agree with it.

    `complete=True` is honest here in a way it never was from the browser: an
    API read returns the whole tab, so a row missing from it really is a row he
    deleted. This is the path the delete guard was written for.
    """
    from . import google, sheet
    sid = google._cfg('ZIPPER_SHEET_ID')
    tab = _load().get('sheet', {}).get('tab') or current_tab()
    t = sheet.Tab(sid, tab)
    return reconcile(t.entries(), tab=tab, complete=True)


def push(dry=False):
    """Put every pending entry into the sheet, or say why it cannot."""
    from . import google, sheet
    sid = google._cfg('ZIPPER_SHEET_ID')
    tab = _load().get('sheet', {}).get('tab') or current_tab()
    ents = sorted(pending(), key=lambda x: (x['date'], x['start'] or '~'))
    t, writes, refused = sheet.plan(sid, tab, ents)
    if writes and not dry:
        google.write(sid, [(f"'{tab}'!A{r}:F{r}", [v]) for r, v in writes])
        # Formatting first: the read-back below trusts what the sheet displays.
        sheet.match_format(sid, tab, writes)
        # Read back rather than assume. A write that lands in the wrong row is
        # the failure worth catching, and the sheet is the only witness.
        pull()
    return writes, refused


def current_tab():
    """The tab for today, by name. One per semester, so this is a guess only
    at the turn of a term -- and a wrong guess refuses rather than writes."""
    today = dt.date.today()
    term = 'Spring' if today.month <= 6 else 'Fall'
    return f'{term} {today.year}'


def cmd_hours(a):
    action = getattr(a, 'action', None) or 'show'
    if action == 'add':
        e = add(a.date, a.start, a.end, a.hours, a.note or '', a.source or 'cli')
        print(f'{e["date"]}  {e["hours"]}h  {e["state"]}  {e["note"][:50]}')
        return
    if action == 'rm':
        print(f'removed {remove(a.key)}')
        return
    if action == 'import':
        rows = _read_csv(a.csvfile)
        print(reconcile(rows, tab=os.path.basename(a.csvfile)))
        return
    if action == 'pull':
        print(pull())
        return
    if action == 'push':
        writes, refused = push(dry=getattr(a, 'dry_run', False))
        for r, v in writes:
            print(f'  row {r:>4}  {v[0]}  {v[1]}-{v[2]:<6} {v[4][:48]}')
        for e, why in refused:
            print(f'  REFUSED  {e["date"]}  {e["note"][:34]:36} {why}')
        if not writes and not refused:
            print('nothing pending')
        elif getattr(a, 'dry_run', False):
            print('\n(dry run — nothing was written)')
        return

    ws = weeks()
    if not ws:
        print('hours: nothing captured yet')
        return
    print(f'{"week":12} {"worked":>7} {"submitted":>10} {"banked":>7} {"pending":>8}')
    for w in ws[-12:]:
        print(f'{w["week"]:12} {w["worked"]:7.2f} {w["submitted"]:10.2f} '
              f'{w["banked"]:7.2f} {w["pending"]:8.2f}')
    p = pending()
    if p:
        print(f'\npending — not in the sheet ({len(p)}):')
        for e in p:
            st, en = sheet_times(e['start'], e['end'])
            span = f'{st}-{en}' if st else ''
            print(f'  {e["date"]} {span:12} {e["hours"]:5.2f}h  {e["note"][:56]}')
    for f in flags():
        print(f'\nflag: {f}')


def _read_csv(path):
    """Parse an exported semester tab. Week rows and headers are not entries."""
    import csv
    rows = []
    with open(path, newline='') as f:
        for r in csv.reader(f):
            r = (list(r) + [''] * 6)[:6]
            a = r[0].strip()
            if not a or a.lower() == 'date' or a.startswith('Week'):
                continue
            if re.match(r'^(Fall|Spring|Summer)\s+\d{4}$', a):
                continue
            try:
                d = dt.datetime.strptime(a, '%m/%d/%Y').date()
            except ValueError:
                continue
            m = re.match(r'^(\d+):(\d+)(?::(\d+))?$', r[3].strip())
            if not m:
                continue
            h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            sub = r[5].strip()
            if sub.lower() in ('yes', 'no'):
                sub = ''
            rows.append({'date': d.isoformat(), 'start': _to24(r[1], r[2])[0],
                         'end': _to24(r[1], r[2])[1],
                         'hours': round(h + mi / 60 + se / 3600, 4),
                         'note': r[4].strip(), 'submitted': _iso(sub)})
    return rows


def _to24(start, end):
    """Undo the sheet's rendering rule: a pair that does not cross noon is 12h."""
    a, b = _hm(start), _hm(end)
    if a is None or b is None:
        return '', ''
    if a < 12 * 60 <= b:                     # already 24-hour, by his rule
        pass
    else:
        # Both ends share a half of the day. Afternoon unless the span makes
        # sense in the morning -- the sheet writes 1:30-7:30 for the afternoon.
        if a < 12 * 60 and b < 12 * 60 and a >= 8 * 60:
            pass                             # 9:00-11:30 really is the morning
        else:
            if a < 12 * 60:
                a += 12 * 60
            if b < 12 * 60:
                b += 12 * 60
    return f'{a//60}:{a%60:02d}', f'{b//60}:{b%60:02d}'


def _iso(s):
    s = (s or '').strip()
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, '%m/%d/%Y').date().isoformat()
    except ValueError:
        return None
