"""zipper.digest

The evening message: what is due tomorrow, what is on the calendar, and what
is coming after that. Posted to Discord by `zipper-digest.timer`.

**It reads through `zipper.web.data`, not through the JSON.** The ranking, the
cross-offs and the class links are the dashboard's, so a digest and the *What
to work on* card cannot disagree about what is outstanding -- two components
answering the same question differently is the bug class this system keeps
rediscovering, and a nightly message is the worst place to find it, because
there is nothing on screen next to it to contradict.

It concludes nothing and writes nothing to the vault. It is a reminder.
"""
import datetime, json, os

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core, canvas, chat


# One digest per date. The timer is `Persistent=true` so a box that was asleep
# at 19:00 still sends when it wakes -- which means a catch-up run and the
# ordinary run can both fire for the same day, and a reminder delivered twice
# reads as a bug in the thing reminding him.
STAMP = lambda: os.path.join(core.INBOX, 'digest-sent.json')

# Discord's message ceiling is 2000. The margin is for the code fence the
# sections are wrapped in, and for a truncation line that must always fit.
LIMIT = 1900

# How many rows a section prints before it starts counting instead. A digest
# that lists eighty outstanding assignments is a wall, and a wall is the thing
# he already scrolls past on Canvas.
MAX_ROWS = 10


def _sent_today():
    try:
        return json.load(open(STAMP(), encoding='utf-8')).get('date') == core.TODAY.isoformat()
    except Exception:
        return False


def _mark_sent():
    # Written after the send, never before: a digest that failed to reach
    # Discord must not count as delivered, or the retry is suppressed by the
    # record of the attempt that did not work.
    try:
        json.dump({'date': core.TODAY.isoformat(),
                   'at': datetime.datetime.now().isoformat(timespec='seconds')},
                  open(STAMP(), 'w', encoding='utf-8'))
    except Exception:
        pass


def _canvas_age():
    """How old the *submitted* reading is, in his words rather than a timestamp.

    Freshness here is a fact about his browsing, not a fault: due dates come
    from the ICS feed and are current either way, but whether something is
    already submitted is only as new as the last time he had Canvas open. The
    digest says so, because a list that quietly includes work he finished this
    afternoon is how he got handed back homework he had already done.
    """
    try:
        blob = json.load(open(canvas.CANVAS_JSON, encoding='utf-8'))
        stamp = blob['fetched']
        secs = (datetime.datetime.now() - datetime.datetime.fromisoformat(stamp)).total_seconds()
    except Exception:
        return 'Canvas: never read.'
    if secs < 3600:
        return 'Canvas read %dm ago.' % (secs // 60)
    if secs < 86400:
        return 'Canvas read %dh ago.' % (secs // 3600)
    return ('Canvas read %dd ago -- anything submitted since then still shows here.'
            % (secs // 86400))


def _row(it):
    """One work item, as a line. Course first: what class it is for is the thing
    he sorts by in his head, and the title is often twenty words of assignment
    name that says nothing until the course is known."""
    tag = it.get('tag') or ('task' if it['source'] == 'task' else '')
    title = it['title']
    if len(title) > 68:
        title = title[:67] + '…'
    line = '  - %s%s' % ((tag + ' · ') if tag else '', title)
    if it.get('elsewhere'):
        # Canvas will never mark these submitted, so they sit outstanding until
        # he crosses them off. Saying where the work actually lives is the
        # difference between a stale-looking row and an accurate one.
        line += '  (%s)' % it['elsewhere']
    return line


def _section(head, rows, extra=''):
    if not rows:
        return []
    out = ['%s%s' % (head, extra)]
    for it in rows[:MAX_ROWS]:
        out.append(_row(it))
    if len(rows) > MAX_ROWS:
        out.append('  … and %d more' % (len(rows) - MAX_ROWS))
    return out + ['']


def compose(days=7):
    """The message. Pure -- builds a string and touches nothing."""
    from .web import data          # imported here: the engine must not need the
                                   # dashboard's module graph to run `lint`.

    today = core.TODAY
    tomorrow = today + datetime.timedelta(days=1)
    tstr = tomorrow.isoformat()

    _, items = data.ranked(limit=10 ** 6)
    live = [i for i in items if not i.get('done')]

    overdue = [i for i in live if i.get('overdue')]
    due_tmw = [i for i in live if i['due'] == tstr]
    horizon = (tomorrow + datetime.timedelta(days=days)).isoformat()
    later = [i for i in live
             if i['due'] and tstr < i['due'] <= horizon]

    lines = ['**%s — due tomorrow**' % tomorrow.strftime('%a %b %-d')]
    lines.append('')

    # **Coursework and self-set tasks are counted separately, never merged.**
    # They are due the same day but they are not the same kind of obligation:
    # a task's due date is one he chose and can move, and an assignment's is
    # not. Ranked together, eight tasks he wrote himself push the homework
    # below the truncation line -- which is the exact failure this digest was
    # built to answer.
    hw = [i for i in due_tmw if i['source'] == 'canvas']
    tasks = [i for i in due_tmw if i['source'] != 'canvas']
    if hw:
        lines += _section('Homework — %d due tomorrow:' % len(hw), hw)
    else:
        lines += ['No coursework due tomorrow.', '']
    lines += _section('Your own tasks (%d):' % len(tasks), tasks)

    lines += _section('Overdue (%d):' % len(overdue), overdue)

    # Tomorrow's timed events, so the reminder knows how much of the day is
    # already spoken for. Due-tomorrow Canvas rows arrive as all-day calendar
    # entries too, and listing them twice would double the apparent load.
    _, timed = data.today_split(tstr)
    if timed:
        lines.append("Tomorrow's schedule:")
        for e in timed[:MAX_ROWS]:
            lines.append('  %s  %s' % (e['time'], e['summary'][:60]))
        if len(timed) > MAX_ROWS:
            lines.append('  … and %d more' % (len(timed) - MAX_ROWS))
        lines.append('')

    if later:
        by_day = {}
        for i in later:
            by_day.setdefault(i['due'], []).append(i)
        lines.append('After that:')
        for d in sorted(by_day)[:6]:
            when = datetime.date(*map(int, d.split('-')))
            lines.append('  %s  %d · %s'
                         % (when.strftime('%a %b %-d'), len(by_day[d]),
                            ', '.join(sorted({str(x['tag'] or 'task') for x in by_day[d]}))))
        lines.append('')

    # Flags are conditions, not work, so they come last and are never counted in
    # with the assignments -- but they are the part of the vault most worth
    # reading, and a digest that dropped them would be a Canvas mirror.
    fl = data.flags()
    if fl:
        lines.append('Flags:')
        for f in fl[:5]:
            lines.append('  - %s' % str(f)[:90])
        lines.append('')

    lines.append(_canvas_age())

    msg = '\n'.join(lines)
    if len(msg) > LIMIT:
        msg = msg[:LIMIT].rsplit('\n', 1)[0] + '\n… truncated. Full list on the dashboard.'
    return msg


def cmd_digest(a):
    """Send tonight's digest, or print what it would say."""
    msg = compose(a.days)

    if a.dry_run:
        print(msg)
        return 0

    if _sent_today() and not a.force:
        print('digest: already sent today (%s). --force to send anyway.' % core.TODAY)
        return 0

    # No thread. A timer is not a conversation, so it has none to answer into.
    # It goes to the notifications channel with everything else that arrives
    # unasked-for; the main channel is the door *he* opens, and a message that
    # posts itself at 19:00 every day does not belong in front of his own.
    r = chat.discord_send(msg, thread_id=a.thread or chat.notify_channel())
    if r.get('error'):
        print('digest: %s' % r['error'])
        return 1
    _mark_sent()
    print('digest: sent (id %s, %d chars)' % (r.get('message_id', '?'), len(msg)))
    return 0
