"""zipper.decisions

Decision notes, scaffolded with a review date and a mind-changing test.
"""
import os, re, datetime

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


DEC_TEMPLATE = """---
type: decision
status: open
decided: {date}
review: {review}
confidence: medium
reversible: true
---
# {title}

## The call


## Why


## What I'm giving up


## What would change my mind
*Be specific. This is the field that makes the decision worth re-reading.*
- 

## Revisit
Set for {review}.

Related: [[Home]]
"""

def cmd_decide(a):
    os.makedirs(DECDIR, exist_ok=True)
    day = a.date or core.TODAY.isoformat()
    slug = re.sub(r'[^a-z0-9]+', '-', a.title.lower()).strip('-')[:60]
    p = os.path.join(DECDIR, '%s-%s.md' % (day, slug))
    review = (datetime.date(*[int(x) for x in day.split('-')]) +
              datetime.timedelta(days=a.review_days)).isoformat()
    if os.path.exists(p):
        print('exists %s' % rel(p)); return 0
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(DEC_TEMPLATE.format(date=day, review=review, title=a.title))
    print('created %s' % rel(p))
    return 0
