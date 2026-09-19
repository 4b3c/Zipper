"""zipper.reddit

Watching Reddit for threads worth replying to, and asking Claude which ones are.

Three steps, and only the two ends are mechanical:

    search   -> recent threads matching the watch terms, minus the ones already seen
    judge    -> one `claude -p` call per batch: is this a thread worth commenting in?
    deliver  -> the survivors, as links, to Discord

The middle step is the whole point and it is not something the engine can do.
A thread matching "macro tracker" is a fact; that it is a person asking for a
recommendation rather than a screenshot of somebody's lunch is a judgement, and
judgement is Claude's job here exactly as it is in a bookkeeping pass.

**What to watch is not in this repository.** The queries, the subreddits and the
description of what a good thread looks like live in a vault note, because they
name a product and a market and this code must not. See `watch()`.

Reddit's anonymous JSON endpoints are blocked for datacenter IPs, so this reads
the OAuth API with a script app's credentials -- REDDIT_CLIENT_ID and
REDDIT_CLIENT_SECRET, from the environment.
"""
import datetime, json, os, re, shutil, subprocess, time
import urllib.error, urllib.parse, urllib.request

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core, chat


UA = 'zipper/1.0 (personal thread watcher)'

# Where the watch configuration lives inside the vault. A note, not a config
# file: what to look for is a conclusion about a market, it changes when the
# positioning changes, and it belongs next to the project it serves.
WATCH_NOTE = 'Meta/Reddit Watch.md'

SEEN = lambda: os.path.join(core.INBOX, 'reddit-seen.json')

# Ids of threads already searched, so a hit is offered once and never again.
# Trimmed to this many, oldest first: the file is a dedupe memory, not a record,
# and a thread that has aged out of every search window cannot come back.
SEEN_MAX = 4000

# How many candidates one judging call may carry. A batch that is too big gets a
# shallower read of each thread, which is the one thing this step exists to give.
BATCH = 12


# --- the watch note ---------------------------------------------------------

def _terms(v):
    """Frontmatter values, as a list of non-empty strings.

    `parse_fm` reads a minimal YAML subset: an inline `[a, b, c]` only, since a
    `- item` block line carries no colon and is skipped. So the note must use
    the inline form -- and a query may not contain a comma. A list is accepted
    here too, harmlessly, in case the parser ever learns the block form.
    """
    if v is None:
        return []
    items = v if isinstance(v, list) else as_list(v)
    return [str(x).strip() for x in items if str(x).strip()]


def watch():
    """The watch configuration, read from the vault.

    Frontmatter:
        reddit_queries    - search strings, each run as its own query
        reddit_subreddits - optional; restricts every query to these
        reddit_window     - optional; hours back to look, default 2

    The note's **body** is handed to the judge verbatim as the description of
    what a thread worth answering looks like. Prose is the right shape for that:
    it is a standard, not a filter, and anything expressible as a filter should
    have been a query.
    """
    p = os.path.join(core.VAULT, WATCH_NOTE)
    if not os.path.exists(p):
        return None
    pairs, body = read_note(p)
    d = fm_dict(pairs)
    qs = _terms(d.get('reddit_queries'))
    if not qs:
        return None
    try:
        window = int(str(d.get('reddit_window') or 2))
    except ValueError:
        window = 2
    return {'queries': qs,
            'subreddits': _terms(d.get('reddit_subreddits')),
            'window': max(1, window),
            'standard': body.strip()}


# --- reddit -----------------------------------------------------------------

def _creds():
    cid = os.environ.get('REDDIT_CLIENT_ID', '').strip()
    secret = os.environ.get('REDDIT_CLIENT_SECRET', '').strip()
    return (cid, secret) if cid and secret else (None, None)


def _token():
    """An app-only OAuth token. Read scope is all this needs and all it asks for."""
    cid, secret = _creds()
    if not cid:
        return None
    body = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
    req = urllib.request.Request('https://www.reddit.com/api/v1/access_token',
                                 data=body, headers={'User-Agent': UA})
    import base64
    req.add_header('Authorization', 'Basic ' + base64.b64encode(
        ('%s:%s' % (cid, secret)).encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get('access_token')


def _search(token, query, subs, window):
    """One search, newest first. Reddit's own `t=` window, widened to what it offers.

    `t` takes hour/day/week and nothing between, so a 2-hour window asks for a
    day and filters on `created_utc` here. Asking for more and cutting locally is
    right: the cut is exact, and the extra rows are free.
    """
    t = 'hour' if window <= 1 else ('day' if window <= 24 else 'week')
    params = {'q': query, 'sort': 'new', 't': t, 'limit': '50', 'type': 'link',
              'raw_json': '1'}
    if subs:
        path = '/r/%s/search' % '+'.join(subs)
        params['restrict_sr'] = 'true'
    else:
        path = '/search'
    url = 'https://oauth.reddit.com%s?%s' % (path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={
        'User-Agent': UA, 'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    cutoff = time.time() - window * 3600
    out = []
    for child in data.get('data', {}).get('children', []):
        p = child.get('data', {})
        if p.get('created_utc', 0) < cutoff or p.get('over_18'):
            continue
        out.append({
            'id': p.get('id'),
            'subreddit': p.get('subreddit'),
            'title': p.get('title', ''),
            'body': _snip(p.get('selftext', '') or '', 1200),
            'url': 'https://www.reddit.com' + p.get('permalink', ''),
            'comments': p.get('num_comments', 0),
            # Reddit stamps in UTC and the vault dates everything local.
            'when': datetime.datetime.fromtimestamp(
                p['created_utc'], datetime.timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M'),
            'query': query,
        })
    return out


# --- the memory of what has been offered ------------------------------------

def _seen():
    try:
        return json.load(open(SEEN(), encoding='utf-8'))
    except Exception:
        return {'ids': []}


def _remember(state, ids):
    # Written after the judging, so a crash mid-batch re-offers a thread rather
    # than silently swallowing it: a duplicate link is visible and merely
    # annoying, a dropped one looks exactly like nothing having been posted.
    state['ids'] = (state.get('ids', []) + list(ids))[-SEEN_MAX:]
    tmp = SEEN() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh)
    os.replace(tmp, SEEN())


# --- judgement --------------------------------------------------------------

PROMPT = """You are screening Reddit threads on behalf of someone who is looking for \
places where it would be genuinely useful -- to the person posting -- for them to reply.

What they are watching for, in their own words:
---
%s
---

Below are %d threads found in the last few hours. For each one decide whether it is \
worth them writing a comment in.

Say yes only when there is a real person asking something they could actually answer, \
and the answer would stand on its own as a helpful comment. Say no to: memes, images, \
progress pictures, threads already full of the same answer, rants, anything where a \
reply would read as an advertisement, and anything where the poster is not asking for \
anything.

Reply with a JSON array and nothing else. One object per thread you say YES to:
[{"id": "<id>", "why": "<one sentence, under 20 words, on what they'd be answering>"}]
An empty array is a perfectly good answer and most hours it is the right one.

THREADS:
%s"""


def judge(cands, standard, model='sonnet', timeout=240):
    """Ask Claude which of these are worth a comment. Returns {id: why}.

    Run from /tmp with no tools: the judge should see the standard and the
    threads and nothing else. A run in the repo would load this project's
    CLAUDE.md, which is about the engine and has no bearing on whether a
    stranger's question deserves an answer.
    """
    exe = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
    if not os.path.exists(exe):
        return None
    rows = [{'id': c['id'], 'subreddit': c['subreddit'], 'title': c['title'],
             'body': c['body'], 'comments': c['comments']} for c in cands]
    prompt = PROMPT % (standard or '(nothing written down -- use your judgement)',
                       len(rows), json.dumps(rows, indent=1))
    try:
        # The prompt goes in on stdin, not as an argument: it carries the
        # whole batch and a thread title can start with a dash. `--allowed-tools=`
        # must keep the equals -- as two words it swallows the next argument.
        r = subprocess.run([exe, '-p', '--model', model, '--output-format', 'json',
                            '--allowed-tools='],
                           input=prompt, capture_output=True, text=True,
                           timeout=timeout, cwd='/tmp')
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    try:
        text = json.loads(r.stdout).get('result', '')
    except Exception:
        return None
    m = re.search(r'\[.*\]', text, re.S)
    if not m:
        return {}
    try:
        verdicts = json.loads(m.group(0))
    except Exception:
        return {}
    ids = {c['id'] for c in cands}
    return {v['id']: str(v.get('why', ''))[:160]
            for v in verdicts
            if isinstance(v, dict) and v.get('id') in ids}


# --- the command ------------------------------------------------------------

def cmd_reddit(a):
    w = watch()
    if not w:
        print('reddit: no watch terms. Add `reddit_queries:` to %s' % WATCH_NOTE)
        return 1
    window = a.hours or w['window']
    token = _token()
    if not token:
        print('reddit: no credentials. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET\n'
              '        (reddit.com/prefs/apps -> create app -> type "script").')
        return 1

    state = _seen()
    known = set(state.get('ids', []))
    found, by_id = [], {}
    for q in w['queries']:
        try:
            hits = _search(token, q, w['subreddits'], window)
        except urllib.error.HTTPError as e:
            print('  ! %s: HTTP %s' % (q, e.code))
            continue
        except Exception as e:
            print('  ! %s: %s' % (q, e))
            continue
        fresh = [h for h in hits if h['id'] not in known and h['id'] not in by_id]
        for h in fresh:
            by_id[h['id']] = h
        found.extend(fresh)
        print('  %-40s %d new of %d' % (_snip(q, 40), len(fresh), len(hits)))

    print('reddit: %d new thread(s) in the last %dh' % (len(found), window))
    if not found:
        return 0

    picks = {}
    for i in range(0, len(found), BATCH):
        batch = found[i:i + BATCH]
        verdict = judge(batch, w['standard'], model=a.model)
        if verdict is None:
            print('reddit: the judge did not answer -- leaving this batch unseen')
            continue
        picks.update(verdict)
        # Only a judged batch is remembered. An unjudged one stays unseen so the
        # next run gets another go at it.
        _remember(state, [c['id'] for c in batch])

    print('reddit: %d worth commenting in' % len(picks))
    for pid, why in picks.items():
        c = by_id[pid]
        line = '**r/%s** — %s\n%s\n%s' % (c['subreddit'], c['title'], why, c['url'])
        if a.dry_run:
            print('\n' + line)
        else:
            try:
                chat.discord_send(line)
            except Exception as e:
                print('  ! could not send %s: %s' % (c['url'], e))
    return 0
