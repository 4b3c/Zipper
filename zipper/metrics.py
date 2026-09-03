"""zipper.metrics

The numbers: metric rows, budget totals, and the execution score.
"""
import os, re, csv, json, datetime, glob

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


MET_HEADER = ['date', 'key', 'value', 'note', 'source']

def _ensure_metrics():
    os.makedirs(METDIR, exist_ok=True)
    if not os.path.exists(METCSV):
        with open(METCSV, 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerow(MET_HEADER)

def add_metric(key, value, when=None, note='', source='manual'):
    _ensure_metrics()
    with open(METCSV, 'a', newline='', encoding='utf-8') as fh:
        csv.writer(fh).writerow([when or core.TODAY.isoformat(), key, value, note, source])

def cmd_metric(a):
    add_metric(a.key, a.value, a.date, a.note or '', 'manual')
    print('%s  %s = %s' % (a.date or core.TODAY.isoformat(), a.key, a.value))
    return 0

def cmd_metrics(a):
    if not os.path.exists(METCSV):
        print('no metrics yet'); return 0
    rows = list(csv.DictReader(open(METCSV, encoding='utf-8')))
    keys = sorted(set(r['key'] for r in rows))
    for k in keys:
        # Several score runs on one date are one measurement re-taken, not a
        # slope. Collapse to the last row per date before reading a trend.
        by_date = {}
        for r in rows:
            if r['key'] == k:
                by_date[r['date']] = r
        series = [by_date[dt] for dt in sorted(by_date)]
        trail = '  '.join('%s:%s' % (r['date'][:7], r['value']) for r in series[-6:])
        first, last = series[0], series[-1]
        delta = ''
        try:
            d = float(last['value']) - float(first['value'])
            if len(series) > 1:
                delta = '  (%+g since %s)' % (d, first['date'][:7])
        except ValueError:
            pass
        print('%-28s %s%s' % (k, trail, delta))
    return 0

def cmd_ingest_budget(a):
    path = os.path.expanduser(a.csvfile)
    rows = list(csv.DictReader(open(path, encoding='utf-8', errors='replace')))
    if not rows:
        print('empty csv'); return 1
    cols = rows[0].keys()
    def pick(cands):
        for c in cols:
            if c and c.strip().lower() in cands:
                return c
        for c in cols:
            for cand in cands:
                if c and cand in c.strip().lower():
                    return c
        return None
    dcol = pick({'date', 'transaction date', 'posted date'})
    acol = pick({'amount', 'debit', 'value'})
    if not dcol or not acol:
        print('could not find date/amount columns in: %s' % list(cols)); return 1
    months = {}
    for r in rows:
        try:
            amt = float(str(r[acol]).replace('$', '').replace(',', '').strip() or 0)
        except ValueError:
            continue
        raw = str(r[dcol]).strip()
        m = re.search(r'(\d{4})-(\d{2})', raw) or re.search(r'(\d{2})/(\d{2})/(\d{4})', raw)
        if not m:
            continue
        key = '%s-%s' % (m.group(1), m.group(2)) if '-' in raw else '%s-%s' % (m.group(3), m.group(1))
        b = months.setdefault(key, [0.0, 0.0])
        if amt < 0:
            b[0] += -amt
        else:
            b[1] += amt
    for month in sorted(months):
        out, inn = months[month]
        add_metric('spend_month', round(out, 2), month + '-01', '', 'budget-csv')
        add_metric('income_month', round(inn, 2), month + '-01', '', 'budget-csv')
        print('%s  out %.2f  in %.2f' % (month, out, inn))
    print('wrote %d month(s) to Metrics/metrics.csv' % len(months))
    return 0


def _ledger_update(tasks):
    """Track first_seen / done_on / dropped_on / renamed_on per task.

    Task text carries no id, so the ledger keys on the text itself and an edit
    looks exactly like a deletion plus an unrelated addition. Distinguishing them
    without fuzzy-matching text uses a structural signal instead: if the total
    number of tasks did not fall, nothing was actually abandoned, so the vanished
    key is marked `renamed_on` and kept out of the drop count. Only a key that
    disappears while the total shrinks is a real `dropped_on`.
    """
    led = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
    today = core.TODAY.isoformat()
    # dropped/renamed carry a full timestamp so cmd_queue can report only what
    # moved since the previous run; a bare date re-reported all day long.
    now = datetime.datetime.now().isoformat()      # microseconds: a queue run
    meta = led.setdefault('__meta__', {})           # finishes inside one second
    prev_count = meta.get('count')
    for t, done in tasks.items():
        fresh = t not in led
        e = led.setdefault(t, {'first_seen': today})
        if fresh and done:
            # already ticked the first time the ledger ever saw it: its age is
            # unknowable, so it must not count as a close in either direction
            e['preexisting'] = True
        if done and not e.get('done_on'):
            e['done_on'] = today
            e.pop('dropped_on', None)
        if not done and e.get('done_on'):
            e.pop('done_on', None)          # unticked again; it is open work
        e.pop('dropped_on', None)   # back on the list; no longer dropped
        e.pop('renamed_on', None)
    vanished = [t for t, e in led.items()
                if t != '__meta__' and t not in tasks
                and not e.get('done_on') and not e.get('dropped_on')
                and not e.get('renamed_on')]
    shrank = prev_count is not None and len(tasks) < prev_count
    for t in vanished:
        led[t]['renamed_on' if not shrank else 'dropped_on'] = now
    meta['count'] = len(tasks)
    with open(LEDGER, 'w', encoding='utf-8') as fh:
        json.dump(led, fh, indent=1, sort_keys=True)
    return led

def compute_score(window=30):
    led = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
    cut = (core.TODAY - datetime.timedelta(days=window)).isoformat()
    out, detail = {}, {}

    # --- the ungameable denominator: active projects, which shrink only by an
    # --- explicit, dated status change
    stalls = []
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        if d.get('type') != 'project' or d.get('status') != 'active':
            continue
        n = _months_ago(d.get('last_touched'))
        stalls.append((title_of(p), n if n is not None else 9999))
    out['projects_active'] = len(stalls)
    out['projects_drifting'] = sum(1 for _, n in stalls if n >= 30)
    out['stall_days_max'] = max([n for _, n in stalls] or [0])
    out['stall_days_median'] = int(_median([n for _, n in stalls]))
    detail['oldest'] = sorted(stalls, key=lambda x: -x[1])[:3]

    # --- anti-inflation: closes weighted by how long the thing sat
    hard = quick = 0
    for t, e in led.items():
        if t == '__meta__' or e.get('preexisting') or not e.get('done_on') or e['done_on'] < cut:
            continue
        age = (datetime.date(*map(int, e['done_on'].split('-'))) -
               datetime.date(*map(int, e['first_seen'].split('-')))).days
        if age >= 14:
            hard += 1
        elif age <= 1:
            quick += 1
    out['hard_closes'] = hard
    out['quick_closes'] = quick

    # --- anti-omission: work that left the list without being done
    out['tasks_dropped'] = sum(1 for t, e in led.items()
                               if t != '__meta__' and e.get('dropped_on', '') >= cut)

    # --- open commitments, and the ones already past a date he set himself
    open_tasks = overdue = 0
    for p in glob.glob(os.path.join(VAULT, 'Tasks', '*.md')):
        for line in open(p, encoding='utf-8'):
            m = TASK_RE.match(line)
            if not m or m.group(1).lower() == 'x':
                continue
            open_tasks += 1
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', m.group(2))
            if due and due.group(1) < core.TODAY.isoformat():
                overdue += 1
    out['tasks_open'] = open_tasks
    out['tasks_overdue'] = overdue
    return out, detail

def cmd_score(a):
    out, detail = compute_score(a.window)
    _ensure_metrics()
    rows = list(csv.DictReader(open(METCSV, encoding='utf-8')))
    last = {}
    for r in rows:
        if r['key'] in out and r['date'] > last.get(r['key'], ''):
            last[r['key']] = r['date']
    gap = (core.TODAY - datetime.timedelta(days=7)).isoformat()
    wrote = 0
    for k in sorted(out):
        if not a.force and last.get(k, '') > gap:
            continue
        add_metric(k, out[k], None, '', 'score')
        wrote += 1
    for k in sorted(out):
        print('  %-20s %s' % (k, out[k]))
    if detail.get('oldest'):
        print('\n  oldest untouched active project(s):')
        for t, n in detail['oldest']:
            print('    %-28s %s' % (t, '%d days' % n if n < 9999 else 'never touched'))
    print('\n%s' % ('wrote %d row(s) to Metrics/metrics.csv' % wrote if wrote
                    else 'no rows written - scored within the last 7 days (--force to override)'))
    return 0
