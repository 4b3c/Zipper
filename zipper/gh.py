"""zipper.gh

GitHub: push dates, commit counts, and the repo <-> note mapping.
"""
import os, json, datetime, subprocess
import urllib.request

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core



GH_USER = os.environ.get('ZIPPER_GH_USER', '')
# Orgs whose repos also count as his work. Team repos live here (Luminosity Lab),
# so without this Orbitscape and Mini Charlotte look dead in the vault when they aren't.
GH_ORGS = [o for o in os.environ.get('ZIPPER_GH_ORGS', '').split(',') if o.strip()]

def _token():
    t = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if t:
        return t.strip()
    try:
        r = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None

def _api(path, token, params=''):
    url = 'https://api.github.com' + path + params
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'zipper'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def note_repo_map():
    """{repo_name: (note_path, is_primary)} from explicit `repos:` frontmatter."""
    out = {}
    for p in iter_notes():
        d = fm_dict(read_note(p)[0])
        if not d.get('repos'):
            continue
        for k, name in enumerate(as_list(d['repos'])):
            out[name] = (p, k == 0)
    return out

def cmd_github(a):
    os.makedirs(INBOX, exist_ok=True)
    token = _token()
    print('auth: %s' % ('token found (private repos included)' if token
                        else 'none (public repos only - set GITHUB_TOKEN or run gh auth login)'))
    try:
        repos = _api('/user/repos', token, '?per_page=100&sort=pushed&affiliation=owner') \
                if token else _api('/users/%s/repos' % GH_USER, token,
                                   '?per_page=100&sort=pushed')
    except Exception as e:
        print('github fetch failed: %s' % e)
        return 1
    # Org repos are under NDA. He can see all of them because he's an owner, but the
    # vault must never hold any repo he hasn't explicitly claimed. Only repos already
    # named in a note's `repos:` field survive this filter -- names, descriptions and
    # commits of everything else are dropped before they can be written anywhere.
    _allow = set(note_repo_map())
    for org in GH_ORGS:
        try:
            more = _api('/orgs/%s/repos' % org, token, '?per_page=100&sort=pushed')
            keep = [r for r in more
                    if '%s/%s' % (org, r['name']) in _allow or r['name'] in _allow]
            repos += keep
            print('  + %d of %d repo(s) from org %s (NDA: only mapped repos kept)'
                  % (len(keep), len(more), org))
        except Exception as e:
            print('  ! org %s fetch failed: %s' % (org, e))
    repos = [{'name': r['name'], 'url': r['html_url'],
              'description': r.get('description') or '',
              'language': (r.get('language') or ''),
              'private': r.get('private', False),
              'owner': (r.get('owner') or {}).get('login') or GH_USER,
              'pushed_at': _utc_local(r.get('pushed_at'))} for r in repos]
    for r in repos:
        # his own repos keep bare names (back-compat with existing `repos:` fields);
        # org repos are addressed as org/name so they can never collide with his
        r['key'] = r['name'] if r['owner'] == GH_USER else '%s/%s' % (r['owner'], r['name'])
        r['is_org'] = r['owner'] != GH_USER
    repos.sort(key=lambda r: r['pushed_at'], reverse=True)
    print('%d repo(s) (%d private)' % (len(repos), sum(1 for r in repos if r['private'])))

    prev = {}
    if os.path.exists(GH_JSON):
        prev = dict((r.get('key') or r['name'], r) for r in
                    json.load(open(GH_JSON, encoding='utf-8')).get('repos', []))

    mapping = note_repo_map()
    since = (datetime.datetime.now() -
             datetime.timedelta(days=a.since_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    owner = GH_USER
    fetched = 0
    for r in repos:
        r['note'] = title_of(mapping[r['key']][0]) if r['key'] in mapping else None
        r['commits'] = []
        if r['key'] not in mapping:
            continue
        cached = prev.get(r['key'], {})
        if (cached.get('pushed_at') == r['pushed_at'] and cached.get('note')
                and not a.full):
            r['commits'] = cached.get('commits', [])
            continue
        try:
            cs = _api('/repos/%s/%s/commits' % (r['owner'], r['name']), token,
                      '?since=%s&per_page=100' % since)
            r['commits'] = [{'sha': c['sha'][:7],
                             'date': _utc_local(c['commit']['author']['date'])[:10],
                             'message': c['commit']['message'].split('\n')[0][:100],
                             'mine': ((c.get('author') or {}).get('login') or '').lower()
                                     == GH_USER.lower()}
                            for c in cs]
            fetched += 1
        except Exception as e:
            r['commit_error'] = str(e)
    with open(GH_JSON, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'user': owner, 'since': since, 'repos': repos}, fh, indent=1)
    print('commits fetched for %d changed repo(s), window %d days' % (fetched, a.since_days))

    # write activity back onto the notes
    by_note = {}
    for r in repos:
        if r['note']:
            by_note.setdefault(r['note'], []).append(r)
    idx = dict((title_of(p), p) for p in iter_notes())
    bumped = 0
    for note, rs in by_note.items():
        p = idx[note]
        pairs, body = read_note(p)
        d = fm_dict(pairs)
        newest = max(r['pushed_at'] for r in rs)
        ncommits = sum(len(r['commits']) for r in rs)
        mine = [c for r in rs for c in r['commits'] if c.get('mine')]
        has_org = any(r.get('is_org') for r in rs)
        changed = False
        if newest[:10] != d.get('last_push'):
            set_field(pairs, 'last_push', newest[:10]); changed = True
        if str(ncommits) != d.get('commits_recent'):
            set_field(pairs, 'commits_recent', str(ncommits)); changed = True
        if has_org:
            # On a team repo a push is the team's, not necessarily his. `last_touched`
            # feeds the drift flags, which are about *his* attention — so only his own
            # commits may move it. `commits_mine` keeps the distinction visible.
            if str(len(mine)) != d.get('commits_mine'):
                set_field(pairs, 'commits_mine', str(len(mine))); changed = True
            when = max((c['date'] for c in mine), default='')
            if when and when[:7] > (d.get('last_touched') or ''):
                set_field(pairs, 'last_touched', when[:7]); changed = True
        elif newest[:7] > (d.get('last_touched') or ''):
            set_field(pairs, 'last_touched', newest[:7]); changed = True
        if changed:
            write_note(p, pairs, body); bumped += 1
            print('  %-30s last_push %s  %d commit(s) in window%s'
                  % (note, newest[:10], ncommits,
                     '  (%d his)' % len(mine) if has_org else ''))
    print('updated %d note(s)' % bumped)

    # generated index
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: zipper github',
         'generated: ' + core.TODAY.isoformat(), '---', '', '# Repos', '',
         '*Generated by `zipper github`. The source of truth is the `repos:` field on each',
         'note — edit that, not this file.*', '',
         '%d repos, %d mapped to a note, %d unassigned.' %
         (len(repos), sum(1 for r in repos if r['note']),
          sum(1 for r in repos if not r['note'])), '',
         '## Mapped', '', '| Repo | Note | Language | Last push | Commits |', '|---|---|---|---|---|']
    for r in repos:
        if r['note']:
            nmine = sum(1 for c in r['commits'] if c.get('mine'))
            L.append('| [%s](%s)%s | [[%s]] | %s | %s | %s |' %
                     (r['key'], r['url'], ' 🔒' if r['private'] else '', r['note'],
                      r['language'] or '—', r['pushed_at'][:10],
                      ('%d (%d his)' % (len(r['commits']), nmine)) if r.get('is_org')
                      else str(len(r['commits']))))
    L += ['', '## Unassigned', '',
          'Add the repo name to a note\'s `repos:` field to map it.', '',
          '| Repo | Language | Last push | Description |', '|---|---|---|---|']
    for r in repos:
        if not r['note']:
            L.append('| [%s](%s)%s | %s | %s | %s |' %
                     (r['key'], r['url'], ' 🔒' if r['private'] else '',
                      r['language'] or '—', r['pushed_at'][:10], r['description'][:60]))
    L += ['', 'Related: [[Queue]] · [[Status]] · [[Workflow]] · [[Home]]', '']
    open(os.path.join(METADIR, 'Repos.md'), 'w', encoding='utf-8').write('\n'.join(L))
    print('-> Meta/Repos.md')
    return 0

def cmd_inspect(a):
    """Dump README + commit history for repos so Claude can write notes for them."""
    if not os.path.exists(GH_JSON):
        print('run `zipper github` first'); return 1
    blob = json.load(open(GH_JSON, encoding='utf-8'))
    repos = blob['repos']
    owner = blob.get('user', GH_USER)
    token = _token()
    if a.repos:
        want = [r for r in repos if r['name'] in a.repos]
        missing = set(a.repos) - set(r['name'] for r in want)
        if missing:
            print('not found: %s' % ', '.join(sorted(missing)))
    else:
        want = [r for r in repos if not r.get('note')]
        want.sort(key=lambda r: r['pushed_at'], reverse=True)
        want = want[:a.limit]
    # Never dump org repo contents. They are under NDA -- he can read them because he
    # owns the org, the vault may not hold them. Mapped-ness is not consent.
    blocked = [r['key'] for r in want if r.get('is_org')]
    if blocked:
        print('refusing %d org repo(s) (NDA, contents stay out of the vault): %s'
              % (len(blocked), ', '.join(sorted(blocked))))
        want = [r for r in want if not r.get('is_org')]
    print('inspecting %d repo(s)%s' % (len(want), '' if a.repos else
          ' (most recently pushed unmapped; --limit to change)'))
    out = []
    for r in want:
        rec = {'name': r['name'], 'private': r['private'], 'url': r['url'],
               'language': r['language'], 'description': r['description'],
               'pushed_at': r['pushed_at'], 'readme': None, 'commits': []}
        try:
            req = urllib.request.Request(
                'https://api.github.com/repos/%s/%s/readme' % (owner, r['name']),
                headers={'Accept': 'application/vnd.github.raw', 'User-Agent': 'zipper'})
            if token:
                req.add_header('Authorization', 'Bearer ' + token)
            with urllib.request.urlopen(req, timeout=30) as fh:
                rec['readme'] = fh.read().decode('utf-8', 'replace')[:a.readme_chars]
        except Exception as e:
            rec['readme_error'] = str(e)
        try:
            cs = _api('/repos/%s/%s/commits' % (owner, r['name']), token, '?per_page=40')
            rec['commits'] = [{'date': c['commit']['author']['date'][:10],
                               'message': c['commit']['message'].split(chr(10))[0][:110]}
                              for c in cs]
        except Exception as e:
            rec['commits_error'] = str(e)
        out.append(rec)
        print('  %-32s readme:%-5s commits:%d' %
              (r['name'], 'yes' if rec['readme'] else 'no', len(rec['commits'])))
    dest = os.path.join(INBOX, 'repo-details.json')
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                   'repos': out}, fh, indent=1)
    print('-> %s' % rel(dest))
    print('Now message Claude: "read Inbox/repo-details.json and write notes for these."')
    return 0
