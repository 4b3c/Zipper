"""Conversations, as the dashboard sees them.

Starting one, resuming one, listing them, and the paste/copy plumbing behind
the terminal card. The conversations themselves belong to
`zipper.conversations`; this is only what the page needs of them.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, chat, conversations
from .feed import feed_rows, publish


# ---------------------------------------------------------------- terminal
#
# **Every conversation is keyed on a Discord thread**, which is what the reply
# forwarding posts to -- a conversation without one is a conversation whose
# answers cannot get back out. There is exactly one kind, named
# `zipper-<thread>`, and the dashboard shows *a conversation*: by default the
# most recently active live one, and any other by picking it from the list.
#
# The single naming scheme is load-bearing, not tidiness. tmux resolves `-t` by
# prefix, so any session name that is a prefix of another's matches it silently.
# See HISTORY.md, 2026-09-06.

def _queue_prompt():
    """The queue as an opening instruction — only what is still outstanding."""
    real = [r for r in feed_rows()
            if not r['done'] and not r['text'].startswith('error')]
    if not real:
        return ''
    return ("Zipper just refreshed the vault. Open in the queue:\n\n"
            + '\n'.join('  [%s] %s' % (r['key'], r['text']) for r in real)
            + "\n\nRead Meta/Queue.md — it has these rows with their targets, the "
              "uncommitted note diff, and the flags. For each row work out what it "
              "affected and update that note; read the diff to see what another "
              "session already changed. Flag anything contradictory rather than "
              "guessing. Then tell me what you changed.\n\n"
              "Cross a single row off with its key:\n"
              "  python3 -m zipper.serve --mark <key>\n"
              "Or end the whole pass — ticks every row and commits the notes:\n"
              "  python3 -m zipper commit \"<message>\"\n"
              "An open dashboard picks either up within a second.")



TTYD = {'enabled': True, 'host': '127.0.0.1', 'cred': ''}
# How every ttyd is bound. `ttyd -W` hands out a live shell, so binding one off
# loopback without a credential is refused in `conversations.ensure_ttyd`. Ports
# are not here -- each conversation is allocated its own (see `ttyd.py`).


def current_conversation():
    """The conversation the terminal card shows by default.

    The most recently active live one. `listing()` is ordered by last message
    (from the transcript, so a message typed straight into a pane counts), which
    means this follows the conversation actually being used rather than whichever
    was started first.
    """
    for r in conversations.listing():
        if r.get('alive'):
            return r['thread_id']
    return ''


def new_conversation(prompt=None):
    """Start another conversation, closing none.

    It gets a Discord thread of its own straight away, so a conversation begun
    at the keyboard can be picked up from a phone without being adopted after
    the fact -- which is the awkward path that binding exists to patch.
    """
    title = 'Dashboard \u00b7 %s' % datetime.datetime.now().strftime('%a %H:%M')
    try:
        r = chat._bot('/thread', {'name': title,
                                  'message': 'New conversation started from the dashboard.'})
        tid = str(r.get('thread_id') or '')
    except Exception as e:
        # No Discord, no thread -- but the conversation should still start. A
        # local id keeps it addressable in the list; it just cannot be reached
        # from a phone, and the reply hook skips it for exactly that reason.
        publish('status', 'terminal    no Discord thread for this conversation: %s' % e)
        tid = 'local-%d' % int(time.time())
    if not tid:
        return {'ok': False, 'error': 'could not open a Discord thread'}
    conversations.touch(tid, title=title, auto_named=True, discord_name=title)
    r = conversations.start(tid, prompt=prompt or None)
    if not r.get('ok'):
        return r
    res = conversations.ensure_ttyd(tid, host=TTYD['host'], cred=TTYD['cred'])
    return dict(res, thread_id=tid, title=title,
                primed=bool(prompt), resumed=False)


def resume_conversation(prompt=None):
    """Bring the current conversation back onto the page, optionally handing it
    the queue. Resumes rather than replaces: the transcript is the conversation.
    """
    tid = current_conversation()
    if not tid:
        return {'ok': False, 'error': 'no conversation to resume'}
    r = open_conversation(tid)
    if not r.get('ok'):
        return r
    primed = False
    if prompt:
        primed = conversations.paste(tid, prompt).get('ok', False)
    return dict(r, thread_id=tid, resumed=True, primed=primed)


def start_session(mode='blank'):
    """The terminal card's start buttons.

    `blank`/`queue` open a new conversation; `resume`/`catchup` return to the
    current one. The only difference within each pair is whether the run queue
    is handed over.
    """
    prompt = _queue_prompt() if mode in ('queue', 'catchup') else None
    if mode in ('resume', 'catchup'):
        return resume_conversation(prompt)
    return new_conversation(prompt)


# ---------------------------------------------------------------- inbound
#
# A message that arrives from outside the dashboard -- today that means Discord,
# tomorrow a cron trigger or a webhook. It is addressed to one conversation, and
# these are the three states that conversation can be in:
#
#   live      ttyd is serving and tmux holds a conversation  -> paste into it
#   detached  tmux still holds the conversation, ttyd is not serving
#             (the tab was closed, or the server restarted)  -> bring ttyd back,
#                                                                then paste
#   cold      no tmux session at all                         -> start one, primed
#                                                                with the message
#
# The cold path deliberately does NOT paste. claude-session.sh reads the
# ready-file before exec'ing claude, so the message becomes the conversation's
# opening prompt -- no race against a TUI that has not drawn yet.

# A message is delivered **verbatim** -- no provenance tag, nothing prepended.
# Routing is per-turn and is never inferred from the prompt: the reply is
# forwarded by the Stop hook (`hooks/forward_reply.py`), and provenance is
# recorded at delivery (`conversations.note_delivery`) and read back from the
# registry, where the bot can look it up rather than it sitting in the context
# window forever. Putting it in the prompt instead is what HISTORY.md,
# 2026-09-06 is about; don't.
#
# A message with no thread is refused rather than delivered somewhere -- the bot
# opens a thread before it posts, and `/discord` requires one.


TITLE_TTL = 900          # seconds before a Discord thread name is looked up again


def _row_title(thread_id, row):
    """What to call a conversation in the list.

    Claude's own `ai-title` first: it names the *conversation* rather than its
    delivery mechanism, it updates as the subject moves, and it exists for
    sessions started at the terminal that have no thread at all. One scheme for
    every row, rather than one per way a row can be created.

    A Discord thread name is the fallback, for a conversation too young to have
    been titled yet; an id is the last resort.
    """
    own = conversations.title(thread_id)
    if own:
        _sync_thread_name(thread_id, row, own)
        return own
    if str(thread_id).startswith('local-'):
        return row.get('title') or 'new conversation'
    fetched = row.get('title_at') or 0
    if row.get('title_src') == 'discord' and (time.time() - fetched) < TITLE_TTL:
        return row.get('title')
    try:
        name = (chat._bot('/threadinfo', {'thread_id': thread_id}, timeout=6).get('name') or '').strip()
    except Exception:
        name = ''
    if name:
        conversations.touch(thread_id, title=name, title_src='discord',
                            title_at=int(time.time()))
        return name
    return row.get('title') or 'conversation %s' % str(thread_id)[-6:]


RENAME_EVERY = 600       # Discord rate-limits thread renames; twice per 10 min


def _sync_thread_name(thread_id, row, name):
    """Give the Discord thread the name Claude gave the conversation.

    A conversation started from the dashboard opens its thread before anyone
    knows what it is about, so it is born as "Dashboard · Sun 14:26". Leaving it
    that way means the phone shows a list of timestamps -- the whole point of
    opening the thread up front is being able to find the conversation later
    without having planned to.

    Renaming is rate-limited by Discord and the title moves as the subject does,
    so this only fires when the name actually changed and at most once every ten
    minutes per thread.
    """
    if str(thread_id).startswith('local-') or not name:
        return
    # Only ever rename a thread whose name we wrote. A thread opened from a
    # message in the channel is named by Discord from what he typed, and a
    # thread he renames himself is a deliberate act -- overwriting either with
    # a generated title would be taking something away, and the titles are not
    # always better than the words a person chose.
    if not row.get('auto_named'):
        return
    if row.get('discord_name') == name:
        return
    if time.time() - (row.get('renamed_at') or 0) < RENAME_EVERY:
        return
    try:
        r = chat._bot('/threadrename', {'thread_id': thread_id, 'name': name}, timeout=8)
    except Exception:
        return
    if r.get('ok'):
        conversations.touch(thread_id, discord_name=name, renamed_at=int(time.time()))


PASTE_DIR = os.path.join(tempfile.gettempdir(), 'zipper-pastes')
PASTE_KEEP = 40


def _prune_pastes():
    """Keep the last few pastes and no more.

    They are screenshots dropped into a conversation, not vault content -- they
    live in tmp, and the only reason to keep any is that a conversation may
    refer back to one it was shown a few minutes ago.
    """
    try:
        files = sorted((os.path.getmtime(os.path.join(PASTE_DIR, f)), f)
                       for f in os.listdir(PASTE_DIR))
    except OSError:
        return
    for _, f in files[:-PASTE_KEEP]:
        try:
            os.remove(os.path.join(PASTE_DIR, f))
        except OSError:
            pass


BUFFER_MAX = 200000


def newest_buffer(seen=''):
    """The most recent tmux paste buffer, if it is not the one already seen.

    Buffers are named bufferNN and numbered upwards, so the highest is newest.
    The name and size together are the identity -- a re-copy of the same text
    makes a new buffer, and the operator expects that to reach the clipboard
    again.
    """
    tmux = shutil.which('tmux')
    if not tmux:
        return {'ok': False, 'error': 'tmux not installed'}
    try:
        r = subprocess.run([tmux, 'list-buffers', '-F', '#{buffer_name}\t#{buffer_size}'],
                           capture_output=True, text=True, timeout=5)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    best, best_n, size = None, -1, 0
    for line in r.stdout.splitlines():
        name, _, sz = line.partition('\t')
        if not name.startswith('buffer'):
            continue          # zipper-copy is ours; it is not a new selection
        try:
            n = int(name[6:])
        except ValueError:
            continue
        if n > best_n:
            best, best_n, size = name, n, int(sz or 0)
    if not best:
        return {'ok': True, 'id': ''}
    ident = '%s:%d' % (best, size)
    if ident == seen or size > BUFFER_MAX:
        return {'ok': True, 'id': ident, 'unchanged': True}
    try:
        text = subprocess.run([tmux, 'show-buffer', '-b', best],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True, 'id': ident, 'text': text}


def conversation_rows():
    """The chat list: every conversation, with the state the page has to show."""
    conversations.sweep()
    rows = []
    for r in conversations.listing():
        rows.append({
            'thread_id': r['thread_id'],
            'title': _row_title(r['thread_id'], r),
            'alive': r['alive'],
            'bound': bool(r.get('bound')),
            'serving': r.get('serving'),
            'port': r.get('port'),
            'state': r.get('state'),
        })
    return rows


def open_conversation(thread_id):
    """Show a conversation in the dashboard: revive it if closed, then serve it.

    A conversation closed by the reaper is resumed rather than replaced -- the
    transcript is the conversation, and picking one out of the list must never
    mean starting a stranger with the same name.
    """
    if not thread_id:
        return {'ok': False, 'error': 'thread_id required'}
    if not conversations.alive(thread_id):
        r = conversations.start(thread_id)
        if not r.get('ok'):
            return r
    # Every conversation is served the same way: no special cases, no fixed
    # ports. `ensure_ttyd` is idempotent, so this is also the resume path.
    return conversations.ensure_ttyd(thread_id, host=TTYD['host'], cred=TTYD['cred'])
