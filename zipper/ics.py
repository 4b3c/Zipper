"""zipper.ics

ICS parsing, recurrence expansion, and the rendered agenda.
"""
import os, re, json, datetime, glob
import urllib.request

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core
from .canvas import canvas_status_map
from .events import event_note_map, resolve_events


def _unfold(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\n ', '').replace('\n\t', '')

def _ics_dt(params, val):
    """An ICS date-time -> ('YYYY-MM-DD HH:MM' | 'YYYY-MM-DD', all_day).

    Three forms appear in the wild and they are not interchangeable:
      DTSTART;VALUE=DATE:20260909              -> a date, no time, no zone
      DTSTART:20260909T160000Z                 -> UTC, must be converted
      DTSTART;TZID=America/Phoenix:20260909T090000 -> already local to that zone
    The vault dates everything in local time, so anything zoned is converted.
    Slicing the digits instead is how an 09:00 meeting became 16:00.
    """
    val = val.strip()
    m = re.search(r'(\d{8})(?:T(\d{6}))?(Z)?', val)
    if not m:
        return None, False
    d, t, zulu = m.group(1), m.group(2), m.group(3)
    y, mo, dy = int(d[0:4]), int(d[4:6]), int(d[6:8])
    if not t or 'VALUE=DATE' in params.upper():
        return '%04d-%02d-%02d' % (y, mo, dy), True
    dt = datetime.datetime(y, mo, dy, int(t[0:2]), int(t[2:4]), int(t[4:6]))
    tzm = re.search(r'TZID=([^;:]+)', params)
    tz = None
    if zulu:
        tz = datetime.timezone.utc
    elif tzm:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tzm.group(1).strip())
        except Exception:
            tz = None
    if tz is not None:
        dt = dt.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M'), False

_WD = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}

def _expand(start, all_day, rrule, exdates, back=180, ahead=400):
    """Expand an RRULE into concrete start strings inside a bounded window.

    Google emits one VEVENT per recurring series, not one per occurrence. Without
    this a weekly class contributes exactly one dated event -- its first -- and the
    agenda silently loses the entire semester.
    """
    fmt = '%Y-%m-%d' if all_day else '%Y-%m-%d %H:%M'
    try:
        base = datetime.datetime.strptime(start, fmt)
    except ValueError:
        return [start]
    if not rrule:
        return [start]
    r = {}
    for part in rrule.split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            r[k.strip().upper()] = v.strip()
    freq = r.get('FREQ', '').upper()
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):
        return [start]
    interval = int(r.get('INTERVAL', 1) or 1)
    count = int(r['COUNT']) if r.get('COUNT', '').isdigit() else None
    until = None
    if r.get('UNTIL'):
        u, _ = _ics_dt('', r['UNTIL'])
        if u:   # parse UNTIL by its own shape, not the event's
            until = datetime.datetime.strptime(
                u, '%Y-%m-%d %H:%M' if len(u) > 10 else '%Y-%m-%d')
    lo = datetime.datetime.combine(core.TODAY - datetime.timedelta(days=back), datetime.time())
    hi = datetime.datetime.combine(core.TODAY + datetime.timedelta(days=ahead), datetime.time())
    days = [_WD[x[-2:]] for x in r.get('BYDAY', '').split(',') if x[-2:] in _WD]
    out, cur, n = [], base, 0
    step = {'DAILY': datetime.timedelta(days=interval),
            'WEEKLY': datetime.timedelta(weeks=interval)}.get(freq)
    while cur <= hi and n < 2000:
        n += 1
        occs = [cur]
        if freq == 'WEEKLY' and days:
            wk = cur - datetime.timedelta(days=cur.weekday())
            occs = [wk + datetime.timedelta(days=d) for d in days]
        for o in occs:
            if o < base or o > hi:
                continue
            if until and o > until:
                continue
            s = o.strftime(fmt)
            if s in exdates:
                continue
            if o >= lo:
                out.append(s)
            if count and len(out) >= count:
                return out
        if step:
            cur = cur + step
        elif freq == 'MONTHLY':
            y, mth = cur.year, cur.month + interval
            y, mth = y + (mth - 1) // 12, (mth - 1) % 12 + 1
            try:
                cur = cur.replace(year=y, month=mth)
            except ValueError:
                cur = cur.replace(year=y, month=mth, day=28)
        else:
            try:
                cur = cur.replace(year=cur.year + interval)
            except ValueError:
                cur = cur.replace(year=cur.year + interval, day=28)
    return sorted(set(out))

def parse_ics(text):
    events, overrides = [], set()
    blocks = re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', _unfold(text), re.S)
    for block in blocks:
        m = re.search(r'^RECURRENCE-ID([^:\n]*):(.+)$', block, re.M)
        u = re.search(r'^UID[^:\n]*:(.+)$', block, re.M)
        if m and u:
            s, _ad = _ics_dt(m.group(1), m.group(2))
            overrides.add((u.group(1).strip(), s))
    for block in blocks:
        ev, rrule, exdates, recur_id, uid = {}, None, set(), None, None
        for line in block.split('\n'):
            if ':' not in line:
                continue
            name, val = line.split(':', 1)
            key = name.split(';')[0].strip().upper()
            clean = val.strip().replace('\\,', ',').replace('\\n', ' ')
            if key == 'SUMMARY':
                ev['summary'] = clean
            elif key == 'DTSTART':
                ev['start'], ev['all_day'] = _ics_dt(name, val)
            elif key == 'DTEND':
                ev['end'], _ = _ics_dt(name, val)
            elif key == 'LOCATION':
                ev['location'] = clean
            elif key == 'UID':
                uid = clean; ev['uid'] = clean
            elif key == 'URL':
                ev['url'] = clean
            elif key == 'RRULE':
                rrule = val.strip()
            elif key == 'EXDATE':
                for piece in val.split(','):
                    s, _ = _ics_dt(name, piece)
                    if s:
                        exdates.add(s)
            elif key == 'RECURRENCE-ID':
                recur_id, _ = _ics_dt(name, val)
            elif key == 'STATUS' and clean.upper() == 'CANCELLED':
                ev['_cancelled'] = True
        if not ev.get('summary') or not ev.get('start') or ev.get('_cancelled'):
            continue
        if rrule and not recur_id:
            dur = None
            if not ev.get('all_day'):
                s0, e0 = _dt(ev.get('start')), _dt(ev.get('end'))
                if s0 and e0 and e0 > s0:
                    dur = e0 - s0
            for s in _expand(ev['start'], ev.get('all_day', False), rrule, exdates):
                if (uid, s) in overrides:
                    continue          # a moved instance; its own VEVENT carries it
                e = dict(ev); e['start'] = s; e.pop('end', None)
                if dur is not None:
                    st = _dt(s)
                    if st is not None:
                        e['end'] = (st + dur).strftime('%Y-%m-%d %H:%M')
                events.append(e)
        else:
            events.append(ev)
    return events

def _known_codes():
    """Course codes from Classes/ — e.g. {'CSE423': 'CSE 423'}."""
    out = {}
    for q in iter_notes():
        d = fm_dict(read_note(q)[0])
        if d.get('type') == 'class' and d.get('code'):
            out[d['code'].replace(' ', '').upper()] = d['code']
    return out

def _clean_canvas(summary, codes):
    """Canvas appends a course tag: 'HW 01 [2026FallC-T-CSE485-CSE423-60367-...]'.
    Turn that into 'HW 01 · CSE 423', or drop it when it's an org calendar."""
    m = re.search(r'\s*\[([^\]]+)\]\s*$', summary)
    if not m:
        return summary.strip()
    tag, title = m.group(1).upper(), summary[:m.start()].strip()
    hits = [codes[c] for c in codes if c in tag.replace('-', '')]
    if hits:
        return '%s · %s' % (title, '/'.join(sorted(set(hits))))
    return '%s · org' % title if tag.startswith('ORG-') else title

def _ics_text(src):
    if src.startswith('http'):
        with urllib.request.urlopen(src.replace('webcal://', 'https://'), timeout=30) as r:
            return r.read().decode('utf-8', 'replace')
    return open(os.path.expanduser(src), encoding='utf-8', errors='replace').read()

def cmd_ingest_ics(a):
    os.makedirs(INBOX, exist_ok=True)
    src = a.source
    text = getattr(a, 'text', None)     # cmd_calendars pre-fetches in parallel
    if text is None:
        try:
            text = _ics_text(src)
        except Exception as e:
            print('fetch failed: %s' % e); return 1
    events = parse_ics(text)
    if a.label == 'canvas':
        codes = _known_codes()
        for e in events:
            e['summary'] = _clean_canvas(e.get('summary', ''), codes)
    total = len(events)
    match = getattr(a, 'match', None)
    if match:
        rx = re.compile(match, re.I)
        events = [e for e in events if rx.search(e.get('summary', ''))]
    out = os.path.join(INBOX, 'calendar-%s.json' % a.label)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({'label': a.label, 'fetched': core.TODAY.isoformat(),
                   'match': match, 'kept': len(events), 'seen': total,
                   'events': events}, fh, indent=1)
    # A shared calendar changes constantly, so remember the URL and refetch on catchup.
    if a.source.startswith('http'):
        cfg = {}
        if os.path.exists(CAL_CFG):
            cfg = json.load(open(CAL_CFG, encoding='utf-8'))
        cfg[a.label] = {'url': a.source, 'match': match}
        with open(CAL_CFG, 'w', encoding='utf-8') as fh:
            json.dump(cfg, fh, indent=1)
        print('  remembered in %s — catchup will refetch it' % rel(CAL_CFG))
    if match:
        print('  filter %r kept %d of %d event(s)' % (match, len(events), total))
    upcoming = [e for e in events if e['start'][:10] >= core.TODAY.isoformat()]
    print('%s: %d event(s), %d upcoming -> %s' %
          (a.label, len(events), len(upcoming), rel(out)))
    return 0

def cmd_calendars(a):
    """Refetch every calendar remembered by `ingest-ics <url>`. Shared calendars change
    constantly, so a one-time import is stale the day after it's taken."""
    if not os.path.exists(CAL_CFG):
        print('no remembered calendars — run: zipper ingest-ics <url> --label X')
        return 0
    cfg = json.load(open(CAL_CFG, encoding='utf-8'))
    # The fetches are pure network wait and dominate the refresh, so overlap them.
    # Parsing and writing stay serial: cmd_ingest_ics rewrites calendars.json, and
    # concurrent writers there would race over the file holding the secret URLs.
    import concurrent.futures as _cf
    items = sorted(cfg.items())
    texts = {}
    with _cf.ThreadPoolExecutor(max_workers=min(8, max(1, len(items)))) as ex:
        futs = {ex.submit(_ics_text, c['url']): label for label, c in items}
        for f in _cf.as_completed(futs):
            label = futs[f]
            try:
                texts[label] = f.result()
            except Exception as e:
                print('%s: fetch failed: %s' % (label, e))
    for label, c in items:
        if label not in texts:
            continue
        class A:
            source = c['url']; label = None; match = c.get('match'); text = None
        A.label = label
        A.text = texts[label]
        try:
            cmd_ingest_ics(A())
        except Exception as e:
            print('%s: refetch failed: %s' % (label, e))
    return 0

def _prep(uid, start, enotes):
    """The 📝 tail for a row that has an event note behind it."""
    r = enotes.get((uid, _fmt_dt(start)))
    if not r:
        return ''
    tail = ' · 📝 [[%s]]' % r['title']
    if r['state'] == 'due':
        tail += ' ← **debrief**'
    return tail

def _day_grid(rows, enotes, slot=30, now_min=None):
    """One day as a time sheet: blocks whose height is their real length.

    A list of start times says when things begin and hides how much of the day
    they eat. The grid draws each event across the rows it actually occupies,
    and gives overlapping events their own lane — so a double-booking shows up
    as two bars on one row instead of two innocent lines in a list.
    """
    timed = [r for r in rows if not r['all_day'] and len(r['start']) > 10]
    allday = [r for r in rows if r not in timed]
    out = []
    for r in allday:
        out.append('- `all day` **%s** · %s%s'
                   % (r['summary'], r['label'], _prep(r['uid'], r['start'], enotes)))
    if allday and timed:
        out.append('')
    if not timed:
        if not allday:
            out.append('*Nothing scheduled.*')
        return out

    blocks = []
    for r in timed:
        st, en = _dt(r['start']), _dt(r.get('end') or '')
        if st is None:
            continue
        s0 = st.hour * 60 + st.minute
        if en is None or en <= st:
            e0 = s0 + slot                      # no DTEND in the feed
        elif en.date() != st.date():
            e0 = 24 * 60                        # runs past midnight; clamp to the day
        else:
            e0 = en.hour * 60 + en.minute
        blocks.append({'s': s0, 'e': max(e0, s0 + 5), 'r': r})
    blocks.sort(key=lambda b: (b['s'], b['e']))

    lanes = []                                   # lane -> minute it frees up
    for b in blocks:
        for i, free in enumerate(lanes):
            if b['s'] >= free:
                lanes[i] = b['e']; b['lane'] = i; break
        else:
            b['lane'] = len(lanes); lanes.append(b['e'])
    width = max(1, len(lanes)) * 2 - 1

    lo = min(b['s'] for b in blocks) // slot * slot
    hi = max(-(-b['e'] // slot) * slot for b in blocks)
    out.append('```text')
    for t in range(lo, hi, slot):
        cells = [' '] * len(lanes)
        labels = []
        for b in blocks:
            if b['s'] < t + slot and b['e'] > t:
                cells[b['lane']] = '█'
            if b['s'] - (b['s'] % slot) == t:
                r = b['r']
                bits = ['%s–%s, %s' % (_hhmm(b['s']), _hhmm(b['e']), _dur(b['e'] - b['s']))]
                if _place(r['loc']):
                    bits.append(_place(r['loc']))
                # a wiki link inside a code block is dead text, so the grid
                # only marks that a note exists; the linked reasons go below it
                er = enotes.get((r['uid'], _fmt_dt(r['start'])))
                tag = ' 📝' if er else ''
                if er and er['state'] == 'due':
                    tag = ' 📝 debrief'
                labels.append('%s%s  (%s)' % (r['summary'], tag,
                                              ' · '.join(b for b in bits if b)))
        mark = ' ←now' if now_min is not None and t <= now_min < t + slot else ''
        bar = ' '.join(cells).ljust(width)
        out.append((' %s │ %s %s%s'
                    % (_hhmm(t), bar, '  '.join(labels), mark)).rstrip())
    out.append('```')

    # the reason each noted event is on the calendar, under the grid
    whys = []
    for b in blocks:
        r = enotes.get((b['r']['uid'], _fmt_dt(b['r']['start'])))
        if r and r.get('why'):
            whys.append('- 📝 [[%s|%s]] — %s'
                        % (r['title'], r['summary'] or r['title'], _snip(r['why'], 150)))
    if whys:
        out += [''] + whys
    return out

def cmd_agenda(a):
    os.makedirs(METADIR, exist_ok=True)
    horizon = (core.TODAY + datetime.timedelta(days=a.days)).isoformat()
    rows = []
    for f in sorted(glob.glob(os.path.join(INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            if core.TODAY.isoformat() <= e['start'][:10] <= horizon:
                rows.append({'start': e['start'], 'end': e.get('end', ''),
                             'label': blob['label'], 'summary': e['summary'],
                             'loc': e.get('location', ''), 'uid': e.get('uid', ''),
                             'all_day': bool(e.get('all_day')) or len(e['start']) <= 10})
    rows.sort(key=lambda r: (r['start'], r['summary']))
    cstat = canvas_status_map()
    enotes = event_note_map()
    lines = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
             'status: living', 'source: zipper agenda',
             'generated: ' + core.TODAY.isoformat(), '---', '',
             '# Agenda', '',
             '*Generated by `zipper agenda`. Do not hand-edit — rerun instead.*', '',
             '📝 marks an event with a note in `Events/` — read it before you go.', '',
             'Next %d days, from %d ingested calendar(s).%s' %
             (a.days, len(glob.glob(os.path.join(INBOX, 'calendar-*.json'))),
              '  Canvas items struck through are submitted.' if cstat else
              '  Canvas submission status not loaded - run `zipper canvas`.'), '']
    if not rows:
        lines.append('Nothing ingested yet. Run `zipper ingest-ics <url> --label canvas`.')

    # Today, as a time sheet. The heading stays literally "Today" so that
    # ![[Agenda#Today]] keeps resolving from Now and Dashboard tomorrow.
    now = datetime.datetime.now()
    lines += ['## Today', '',
              '**%s**' % core.TODAY.strftime('%A %-d %B %Y'), '']
    lines += _day_grid([r for r in rows if r['start'][:10] == core.TODAY.isoformat()],
                       enotes, now_min=now.hour * 60 + now.minute)
    lines += ['', '## Next %d days' % a.days]

    day = None
    for r in rows:
        start, label, summary = r['start'], r['label'], r['summary']
        loc, uid = r['loc'], r['uid']
        d = start[:10]
        if d != day:
            day = d
            dt = datetime.date(*[int(x) for x in d.split('-')])
            lines.append('')
            lines.append('### %s (%s)' % (d, dt.strftime('%a')))
        t = start[11:] or 'all day'
        mark = ''
        if label == 'canvas':
            done = cstat.get((d, _norm_title(summary)))
            mark = {True: '~~', False: ''}.get(done, '')
        body = '%s**%s**%s' % (mark, summary, mark)
        # An event with a note is one he scheduled *for* a reason. Show the
        # link here so the reason is in front of him before he walks in, not
        # only afterwards when the queue asks how it went.
        er = enotes.get((uid, _fmt_dt(start)))
        prep = ''
        if er:
            prep = ' · 📝 [[%s]]' % er['title']
            if er['state'] == 'due':
                prep += ' ← **debrief**'
        lines.append('- `%s` %s · %s%s%s%s' % (t, body, label,
                     ' · ' + loc if loc else '', '  ✓ submitted' if mark else '',
                     prep))
    lines += ['', 'Related: [[Status]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Agenda.md'), 'w', encoding='utf-8').write('\n'.join(lines))
    print('agenda: %d event(s) in the next %d days -> Meta/Agenda.md' % (len(rows), a.days))
    return 0
