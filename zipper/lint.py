"""zipper.lint

Frontmatter validation. The authority on what a note may contain.
"""
import re

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


def cmd_lint(a):
    problems = []
    for p in iter_notes():
        pairs = read_note(p)[0]
        d = fm_dict(pairs)
        name = rel(p)
        if not pairs:
            problems.append((name, 'no frontmatter at all'))
            continue
        required = ('type',) if d.get('type') == 'log' else ('type', 'status')
        for req in required:
            if req not in d:
                problems.append((name, 'missing required field: %s' % req))
        if d.get('type') == 'view':
            if 'view_kind' not in d:
                problems.append((name, 'view with no view_kind (query|generated)'))
            elif d['view_kind'] == 'generated':
                for req in ('source', 'generated'):
                    if req not in d:
                        problems.append((name, 'generated view missing %s' % req))
        if d.get('type') != 'view' and d.get('view_kind'):
            problems.append((name, 'view_kind on a non-view note'))
        if d.get('type') == 'event':
            if d.get('status') not in ('scheduled', 'debriefed', 'cancelled'):
                problems.append((name, 'event status should be scheduled/debriefed/cancelled'))
            for req in ('event_uid', 'event_start'):
                if not d.get(req):
                    problems.append((name, 'event note missing %s' % req))
            if d.get('event_start') and not EVT_DT_RE.match(d['event_start']):
                problems.append((name, 'event_start="%s" should be YYYY-MM-DD or '
                                       'YYYY-MM-DDTHH:MM' % d['event_start']))
            if d.get('status') == 'debriefed' and not d.get('debriefed'):
                problems.append((name, 'debriefed event with no debriefed date'))
        elif d.get('event_uid') or d.get('event_start'):
            problems.append((name, 'event_uid/event_start on a non-event note'))
        if d.get('type') == 'decision':
            if d.get('status') not in ('open', 'settled', 'superseded', 'reversed'):
                problems.append((name, 'decision status should be open/settled/superseded/reversed'))
            if 'review' not in d:
                problems.append((name, 'decision with no review date'))
        for k, allowed in ENUMS.items():
            if k in d and d[k] not in allowed:
                problems.append((name, '%s="%s" not in %s' % (k, d[k], '/'.join(allowed))))
        for k in DATE_FIELDS:
            if k in d and d[k] and not DATE_RE.match(d[k]):
                problems.append((name, '%s="%s" should be YYYY-MM-DD' % (k, d[k])))
        for k in MONTH_FIELDS:
            if k in d and d[k] and not MONTH_RE.match(d[k]):
                problems.append((name, '%s="%s" should be YYYY-MM or YYYY' % (k, d[k])))
        for k in NUM_FIELDS:
            if k in d and d[k] and not re.match(r'^-?\d+(\.\d+)?$', d[k]):
                problems.append((name, '%s="%s" should be a number' % (k, d[k])))
        for k in BOOL_FIELDS:
            if k in d and d[k] not in ('true', 'false'):
                problems.append((name, '%s="%s" should be true/false' % (k, d[k])))
        if d.get('type') == 'project':
            if d.get('revenue_intent') == 'true' and 'revenue_to_date' not in d:
                problems.append((name, 'revenue_intent=true but no revenue_to_date'))
            if d.get('status') == 'active' and not d.get('next_action'):
                problems.append((name, 'active project with no next_action'))
        if d.get('next_action') and len(d['next_action'].split()) < 3:
            problems.append((name, 'next_action looks too vague: "%s"' % d['next_action']))
    if not problems:
        print('lint: clean (%d notes)' % len(list(iter_notes())))
        return 0
    for name, msg in problems:
        print('%-45s %s' % (name, msg))
    print('\n%d problem(s)' % len(problems))
    return 1
