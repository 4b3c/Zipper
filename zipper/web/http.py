"""The HTTP handler and the entry point.

Routing, the SSE endpoint, the JSON API, and `main()`. Every branch here
delegates: nothing in this module decides anything about the vault.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, canvas, chat, conversations, events, gh, ics, metrics, usage
from .conv import (PASTE_DIR, TTYD, _prune_pastes, _queue_prompt, conversation_rows,
                   current_conversation, new_conversation, newest_buffer,
                   open_conversation, start_session)
from .data import content_sig, toggle_done
from .feed import (SUBS, SUBS_LOCK, do_refresh, emit_diff, feed_load, feed_mark,
                   feed_mark_all, feed_rows, feed_watch, notes_watch, publish,
                   snapshot_data)
from .render import _list_page, _views_page, panels_html, render, views_blob


# ---------------------------------------------------------------- http

SRV = {'server': None, 'clients': 0, 'quit_timer': None, 'daemon': False}

def _maybe_quit():
    """Last tab closed -> stop. A reload also drops the SSE stream, so wait a
    beat before believing it: a grace window tells a reload from a real close."""
    if SRV['clients'] > 0:
        return
    if SRV.get('daemon'):
        return          # always-on: the browser is a viewer, not the owner
    print('no clients left - shutting down')
    threading.Thread(target=SRV['server'].shutdown, daemon=True).start()

def client_gone():
    SRV['clients'] -= 1
    if SRV['quit_timer']:
        SRV['quit_timer'].cancel()
    SRV['quit_timer'] = threading.Timer(4.0, _maybe_quit)
    SRV['quit_timer'].daemon = True
    SRV['quit_timer'].start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        core.TODAY = datetime.date.today()
        if self.path == '/':
            self._send(200, render())
        elif self.path == '/events':
            self._events()
        elif self.path.split('?')[0] == '/api/tmuxbuffer':
            # Claude Code does its own mouse selection -- that is why xterm sees
            # none -- and copies what you highlight into a *tmux* buffer, saying
            # "copied N chars to tmux buffer". That text lives on the box. This
            # hands the newest one to the page so it can put it on the operator's
            # actual clipboard, which is the thing he asked for.
            q = urllib.parse.parse_qs(self.path.partition('?')[2])
            seen = (q.get('seen') or [''])[0]
            self._send(200, json.dumps(newest_buffer(seen)), 'application/json')
        elif self.path.split('?')[0] == '/api/usage':
            q = urllib.parse.parse_qs(self.path.partition('?')[2])
            self._send(200, json.dumps(usage.read(force=bool(q.get('force')))),
                       'application/json')
        elif self.path == '/api/conversations':
            self._send(200, json.dumps({'conversations': conversation_rows()}),
                       'application/json')
        elif self.path.split('?')[0] == '/api/panels':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            day = (q.get('day') or [''])[0]
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', day or ''):
                day = None
            ep = {}
            for k, v in freshness().items():
                try:
                    ep[k] = datetime.datetime.fromisoformat(v).timestamp() if v else None
                except Exception:
                    ep[k] = None
            self._send(200, json.dumps({'epochs': ep, 'html': panels_html(day),
                                        'queue_ready': bool(_queue_prompt()),
                                        'session': bool(current_conversation()),
                                        'termup': False,
                                        'termport': 0}), 'application/json')
        elif self.path == '/api/state':
            with LOCK:
                st = dict(STATE)
            st['ages'] = {k: ago(v) for k, v in freshness().items()}
            st['sig'] = content_sig()
            st['clients'] = SRV['clients']
            self._send(200, json.dumps(st), 'application/json')
        elif self.path == '/views' or self.path.startswith('/views/'):
            key = self.path[7:].strip('/') or (views_blob().get('pages') or [{'key': ''}])[0]['key']
            page = _views_page(key)
            if page is None:
                self._send(404, '<p>no such view page</p>')
            else:
                self._send(200, page)
        elif self.path in ('/tasks', '/canvas'):
            self._send(200, _list_page('task' if self.path == '/tasks' else 'canvas'))
        elif self.path == '/bookmarklet':
            base = 'http://%s' % self.headers.get('Host', 'localhost')
            code = BOOKMARKLET % ((core.TODAY - datetime.timedelta(days=14)).isoformat(),
                                  (core.TODAY + datetime.timedelta(days=120)).isoformat(), base)
            self._send(200, '<!doctype html><meta charset=utf-8><title>Bookmarklet</title>'
                            '<p>Drag to your bookmarks bar, then click it on your Canvas host:</p>'
                            '<p><a href="%s">Send Canvas &rarr; Zipper</a></p>' % html.escape(code))
        else:
            self._send(404, 'not found', 'text/plain; charset=utf-8')

    def _events(self):
        q = []
        with SUBS_LOCK:
            SUBS.append(q)
        SRV['clients'] += 1
        if SRV['quit_timer']:
            SRV['quit_timer'].cancel()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        try:
            while True:
                if q:
                    ev = q.pop(0)
                    self.wfile.write(('event: %s\ndata: %s\n\n'
                                      % (ev['kind'], json.dumps(ev))).encode())
                else:
                    self.wfile.write(b': ping\n\n')     # detects a closed tab
                    time.sleep(0.5)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with SUBS_LOCK:
                if q in SUBS:
                    SUBS.remove(q)
            client_gone()

    def do_POST(self):
        core.TODAY = datetime.date.today()
        if self.path == '/api/refresh':
            threading.Thread(target=do_refresh, daemon=True).start()
            self._send(202, json.dumps({'ok': True}), 'application/json')
        elif self.path == '/api/session':
            n = int(self.headers.get('Content-Length', 0))
            mode = 'blank'
            if n:
                try:
                    mode = json.loads(self.rfile.read(n).decode('utf-8')).get('mode', 'blank')
                except Exception:
                    pass
            self._send(200, json.dumps(start_session(mode) or {'ok': False}), 'application/json')
        elif self.path == '/api/clipdebug':
            # The clipboard is the one thing here that cannot be tested from
            # this box: whether it works depends on the browser, and on whether
            # the page is a secure context. So the page says what happened and
            # it lands in the journal, where it can be read instead of guessed.
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            print('[clip] %s' % json.dumps(d, sort_keys=True)[:400], flush=True)
            self._send(200, json.dumps({'ok': True}), 'application/json')
        elif self.path == '/api/copybuffer':
            # A selection should land in the tmux paste buffer as well as the
            # browser's clipboard. They are different clipboards: the browser's
            # is the operator's own machine, tmux's is inside the box, and
            # pasting from one pane into another wants the second.
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            text = d.get('text') or ''
            if not text:
                self._send(400, json.dumps({'error': 'text required'}), 'application/json')
                return
            tid = str(d.get('thread_id') or '') or current_conversation()
            if not tid:
                self._send(400, json.dumps({'error': 'no conversation'}), 'application/json')
                return
            # Always `conversations.target()`, never a hand-built session name:
            # it is the one place that knows how to name a pane exactly, and
            # tmux would otherwise resolve `-t` by prefix onto a neighbour.
            try:
                tmux = shutil.which('tmux')
                subprocess.run([tmux, 'load-buffer', '-b', 'zipper-copy', '-'],
                               input=text.encode('utf-8'), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([tmux, 'display-message', '-t', conversations.target(tid),
                                'copied %d chars to tmux buffer' % len(text)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._send(200, json.dumps({'ok': True, 'chars': len(text)}),
                           'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/api/pasteimage':
            # An image on the clipboard is not text and cannot be typed into a
            # terminal. The browser can read it, so it posts the bytes here and
            # gets back a path -- which *is* text, and which Claude Code opens.
            n = int(self.headers.get('Content-Length', 0))
            ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
            if n <= 0 or n > 16 * 1024 * 1024:
                self._send(400, json.dumps({'error': 'empty or too large (16MB max)'}),
                           'application/json')
                return
            ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
                   'image/webp': '.webp'}.get(ctype)
            if not ext:
                self._send(400, json.dumps({'error': 'unsupported type %s' % ctype}),
                           'application/json')
                return
            try:
                os.makedirs(PASTE_DIR, exist_ok=True)
                name = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + ext
                path = os.path.join(PASTE_DIR, name)
                with open(path, 'wb') as fh:
                    fh.write(self.rfile.read(n))
                _prune_pastes()
                self._send(200, json.dumps({'ok': True, 'path': path}), 'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/api/newconversation':
            self._send(200, json.dumps(new_conversation()), 'application/json')
        elif self.path == '/api/conversation':
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            self._send(200, json.dumps(open_conversation(str(d.get('thread_id') or ''))),
                       'application/json')
        elif self.path == '/api/eventnote':
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                summary = (d.get('summary') or '').strip()
                day = (d.get('date') or '').strip()
                if not summary or not re.match(r'^\d{4}-\d{2}-\d{2}$', day):
                    raise ValueError('need summary and date')

                class _A:                      # events.cmd_event's argparse shape
                    match = summary
                    date = day
                    about = None
                    why = None
                buf = io.StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    rc = events.cmd_event(_A())
                finally:
                    sys.stdout = old
                out = buf.getvalue().strip()
                if rc != 0:
                    raise ValueError(out.split('\n')[0] or 'no matching event')
                # the client re-fetches the panel itself; no SSE kind fits this
                self._send(200, json.dumps({'ok': True, 'msg': out}), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)[:80]}), 'application/json')
        elif self.path == '/api/newsession':
            # Was: kill the single fixed tmux session so the next attach started
            # a fresh conversation. There is no single session now, and starting
            # one closes none, so this is /api/newconversation by another name.
            self._send(200, json.dumps(new_conversation()), 'application/json')
        elif self.path == '/api/done':
            n = int(self.headers.get('Content-Length', 0))
            try:
                key = json.loads(self.rfile.read(n).decode('utf-8'))['key']
                self._send(200, json.dumps(toggle_done(key)), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
        # No /api/queuedone. Crossing a queue row off from the browser is gone;
        # the endpoint went with the button rather than being left as a live
        # route with no caller, which is how a "removed" feature comes back.
        # `feed_mark` itself stays -- --mark and `zipper commit` both need it.
        elif self.path == '/discord':
            # The bot posts every message it sees here. It is loopback-only and
            # unauthenticated, exactly like the rest of this server.
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                self._send(400, json.dumps({'error': 'bad json'}), 'application/json')
                return
            text = (body.get('content') or body.get('prompt') or '').strip()
            if not text:
                self._send(400, json.dumps({'error': 'content required'}),
                           'application/json')
                return
            tid = body.get('discord_thread_id')
            if not tid:
                # Every conversation is keyed on a thread -- that is what the
                # reply forwarding posts to -- so a message with no thread has
                # nowhere to be answered. The bot opens one before posting here.
                self._send(400, json.dumps({'error': 'no thread'}),
                           'application/json')
                return

            # A thread is a conversation. Its own instance gets the message,
            # started or resumed as needed, and Discord shows the typing
            # indicator until that instance answers -- chat.discord_send
            # clears it, so every reply path ends the indicator exactly once.
            known = bool(conversations.load().get(str(tid)))
            if not known and not body.get('opening'):
                # A thread Zipper has never seen, and this message did not
                # create it: the conversation it belonged to is gone. Refuse,
                # and let the bot say so in the thread. Starting a fresh session
                # here would answer underneath a visible history it has not read
                # -- continuous to look at, amnesiac in fact.
                self._send(404, json.dumps({'ok': False,
                                            'error': 'no conversation'}),
                           'application/json')
                return

            chat.discord_typing(True, tid)
            if not (conversations.load().get(str(tid)) or {}).get('title'):
                # The thread's name in Discord is the first line of the
                # message that opened it; the chat list should read the same
                # rather than showing an id nobody recognises.
                conversations.touch(str(tid), title=' '.join(text.split())[:60])
            res = conversations.deliver(str(tid), text,
                                        body.get('source', 'discord'))
            if res.get('ok'):
                publish('status', 'terminal    %s -> thread %s (%s)'
                        % (body.get('source', 'discord'), tid, res.get('state')))
            else:
                chat.discord_typing(False, tid)
            self._send(200 if res.get('ok') else 503, json.dumps(res),
                       'application/json')
        elif self.path == '/api/canvas':
            n = int(self.headers.get('Content-Length', 0))
            try:
                before = snapshot_data()
                body = json.loads(self.rfile.read(n).decode('utf-8'))
                # Two shapes, on purpose. The bookmarklet has always posted a
                # bare array and there is no reason to break a working tool to
                # add a second caller, so the envelope is optional and the
                # source is whatever the sender says it is -- which is the only
                # way `canvas.json` can later admit *how* it was read.
                if isinstance(body, dict):
                    items = body.get('items') or []
                    assignments = body.get('assignments')
                    source = str(body.get('source') or 'unknown')[:32]
                else:
                    items, assignments, source = body, None, 'bookmarklet'
                # `assignments` carries the per-course bodies, and only the
                # extension sends them. Without it an assignment whose work
                # lives on PrairieLearn keeps reading as outstanding, because
                # the only evidence it is hosted elsewhere is in its text.
                rows, skipped, described = canvas.ingest(items, assignments, source)
                emit_diff(before, snapshot_data())
                publish('source', 'canvas')
                self._send(200, json.dumps({'ok': True, 'kept': len(rows),
                                            'described': described}),
                           'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
        else:
            self._send(404, 'not found', 'text/plain; charset=utf-8')


BOOKMARKLET = (
    "javascript:(async()=>{let u='/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100',a=[];"
    "while(u){const r=await fetch(u,{credentials:'same-origin'});let t=await r.text();"
    "if(t.startsWith('while(1);'))t=t.slice(9);a.push(...JSON.parse(t));"
    "const m=(r.headers.get('Link')||'').match(/<([^>]+)>;\\s*rel=\"next\"/);u=m?m[1]:null;}"
    "await fetch('%s/api/canvas',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify(a)});alert('sent '+a.length+' items to Zipper');})()")


def conversation_reaper():
    """Close conversations once their prompt cache has gone cold.

    The message is the point, not the kill: an idle instance costs nothing, but
    the next message to a cold one is re-read from scratch at full price. The
    operator asked to know that before he types, not after.
    """
    def notify(tid, text):
        chat.discord_send(text, thread_id=tid)
    while True:
        time.sleep(120)
        try:
            for tid in conversations.reap(notify=notify):
                # Status, not an event. A conversation going idle is the
                # machinery talking about itself -- it happened to Zipper, not
                # to Abram's work, and it has no system, no action and nothing
                # to bookkeep against. It reached the queue as a transient row
                # for a while, which meant the one channel that is supposed to
                # be "things needing a decision" carried housekeeping too.
                publish('status', 'conversation %s closed (idle)' % tid)
        except Exception as e:
            print('[reaper] %s' % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8800)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--open', action='store_true', help='open a browser and exit when it closes')
    ap.add_argument('--no-terminal', action='store_true', help='skip the embedded Claude session')
    # Defaults come from the environment so a systemd EnvironmentFile can set
    # them; the flags still win when both are given.
    ap.add_argument('--term-host', default=os.environ.get('ZIPPER_TERM_HOST', '127.0.0.1'),
                    help='ttyd -W is a live shell; leave on loopback unless you mean it')
    ap.add_argument('--term-cred', default=os.environ.get('ZIPPER_TERM_CRED', ''),
                    help='user:password for the terminal; REQUIRED to bind it off loopback')
    ap.add_argument('--mark', metavar='KEY',
                    help='cross a queue item off by key (or unique prefix) and exit')
    ap.add_argument('--mark-all', action='store_true', dest='mark_all',
                    help='cross off every run-queue item still open and exit')
    ap.add_argument('--queue', action='store_true', help='print the run queue and exit')
    ap.add_argument('--daemon', action='store_true',
                    help='stay up when the last tab closes (for systemd)')
    a = ap.parse_args()
    if a.mark or a.queue or a.mark_all:
        feed_load()
        if a.queue:
            for r in feed_rows():
                print('%s %s %s' % (r['key'], '[x]' if r['done'] else '[ ]', r['text']))
            return 0
        if a.mark_all:
            n = feed_mark_all()['marked']
            print('crossed off  %d item(s)' % n if n else 'nothing open')
            return 0
        res = feed_mark(a.mark)
        print(res.get('error') or ('crossed off  %s' % res['text'] if res['done']
                                   else 'restored     %s' % res['text']))
        return 0 if res['ok'] else 1
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    TTYD['enabled'] = not a.no_terminal
    TTYD['host'] = a.term_host
    TTYD['cred'] = a.term_cred
    SRV['server'] = srv
    SRV['daemon'] = a.daemon
    feed_load()
    threading.Thread(target=feed_watch, daemon=True).start()
    threading.Thread(target=notes_watch, daemon=True).start()
    threading.Thread(target=conversation_reaper, daemon=True).start()
    url = 'http://%s:%d/' % (a.host, a.port)
    print('zipper dashboard on %s%s' % (url, '  (daemon)' if a.daemon else ''))
    if a.daemon:
        print('inbound: POST %sdiscord   {"content": "..."}' % url)
    # No fetch at launch. Inputs are pulled on the hour by zipper-fetch.timer
    # and at the start of every bookkeeping pass -- the two moments that mean
    # something. Fetching here tied freshness to when a browser happened to
    # open, which made the morning page current and a tab left open all day
    # silently stale. The `refresh` button still forces one on demand.
    if a.open:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    print('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
