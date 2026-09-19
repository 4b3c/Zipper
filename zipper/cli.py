"""zipper.cli

The command line. Every subcommand is `fn=<module>.cmd_*`, so this file is the
only place that knows the whole surface -- and the only place to look when you
want to know what zipper can do.
"""
import argparse

from . import (canvas, chat, conversations, decisions, events, ext, gh, ghapp, google,
               hours, ics, lint, metrics, runqueue, status, sync, views)


def main():
    ap = argparse.ArgumentParser(prog='zipper', description='vault command line')
    sub = ap.add_subparsers(dest='cmd')

    s = sub.add_parser('today');   s.add_argument('--date'); s.set_defaults(fn=sync.cmd_today)
    s = sub.add_parser('lint');    s.set_defaults(fn=lint.cmd_lint)
    s = sub.add_parser('status');  s.set_defaults(fn=status.cmd_status)
    s = sub.add_parser('sync');    s.set_defaults(fn=sync.cmd_sync)
    s = sub.add_parser('metrics'); s.set_defaults(fn=metrics.cmd_metrics)

    s = sub.add_parser('touch'); s.add_argument('note'); s.add_argument('--date')
    s.set_defaults(fn=sync.cmd_touch)

    s = sub.add_parser('metric'); s.add_argument('key'); s.add_argument('value')
    s.add_argument('--date'); s.add_argument('--note'); s.set_defaults(fn=metrics.cmd_metric)

    s = sub.add_parser('decide'); s.add_argument('title'); s.add_argument('--date')
    s.add_argument('--review-days', type=int, default=90); s.set_defaults(fn=decisions.cmd_decide)

    s = sub.add_parser('ingest-ics'); s.add_argument('source')
    s.add_argument('--match', default=None,
                   help='regex; keep only events whose summary matches')
    s.add_argument('--label', default='calendar'); s.set_defaults(fn=ics.cmd_ingest_ics)
    sub.add_parser('calendars').set_defaults(fn=ics.cmd_calendars)

    # The timesheet. `add` captures; the sheet stays the system of record and
    # the extension reconciles the two, so nothing here submits anything.
    s = sub.add_parser('google', help='the Google account behind the timesheet')
    s.add_argument('--auth', action='store_true', help='print the consent link')
    s.set_defaults(fn=google.cmd_google)

    s = sub.add_parser('hours', help='the Luminosity timesheet ledger')
    hs = s.add_subparsers(dest='action')
    s.set_defaults(fn=hours.cmd_hours)
    a1 = hs.add_parser('add'); a1.add_argument('--date', required=True)
    a1.add_argument('--start'); a1.add_argument('--end')
    a1.add_argument('--hours', type=float); a1.add_argument('--note', default='')
    a1.add_argument('--source', default='cli'); a1.set_defaults(fn=hours.cmd_hours)
    a2 = hs.add_parser('rm'); a2.add_argument('key'); a2.set_defaults(fn=hours.cmd_hours)
    a3 = hs.add_parser('import'); a3.add_argument('csvfile'); a3.set_defaults(fn=hours.cmd_hours)
    a4 = hs.add_parser('pull', help='read the sheet; it wins')
    a4.set_defaults(fn=hours.cmd_hours)
    a5 = hs.add_parser('push', help='write pending entries into the sheet')
    a5.add_argument('--dry-run', action='store_true')
    a5.set_defaults(fn=hours.cmd_hours)

    s = sub.add_parser('ingest-budget'); s.add_argument('csvfile')
    s.set_defaults(fn=metrics.cmd_ingest_budget)

    s = sub.add_parser('agenda'); s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=ics.cmd_agenda)

    s = sub.add_parser('github'); s.add_argument('--since-days', type=int, default=30)
    s.add_argument('--full', action='store_true'); s.set_defaults(fn=gh.cmd_github)

    # A bookkeeping pass is fetch -> reasoning -> commit. Only the ends are
    # commands; the middle is an agent reading the brief against the vault, so
    # there is deliberately no `bookkeep` subcommand to imply otherwise.
    s = sub.add_parser('fetch', help='start a pass: pull every input, write the brief')
    s.add_argument('--days', type=int, default=14); s.set_defaults(fn=runqueue.cmd_fetch)
    s = sub.add_parser('brief', help='rewrite the brief without pulling anything')
    s.add_argument('--days', type=int, default=14); s.set_defaults(fn=runqueue.cmd_brief)
    s = sub.add_parser('commit', help='close a pass: tick every event, commit the notes')
    s.add_argument('message')
    s.add_argument('--force', action='store_true',
                   help='commit even while another conversation is live')
    s.set_defaults(fn=runqueue.cmd_commit)
    s = sub.add_parser('queue', help='deprecated spelling of fetch')
    s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=runqueue.cmd_queue)
    s = sub.add_parser('views');  s.set_defaults(fn=views.cmd_views)
    s = sub.add_parser('discord', help='talk to the always-on Discord bot')
    s.add_argument('action', choices=['send', 'read', 'status'])
    s.add_argument('text', nargs='?', default='')
    s.add_argument('--file', help='path to attach')
    s.add_argument('--thread', help='thread id; default is the main channel')
    s.add_argument('--limit', type=int, default=5)
    s.set_defaults(fn=chat.cmd_discord)
    # No --days and no fetch: the browser extension takes the reading, this
    # reports it. --file still ingests a saved planner dump.
    s = sub.add_parser('canvas'); s.add_argument('--file')
    s.set_defaults(fn=canvas.cmd_canvas)
    s = sub.add_parser('conversations'); s.add_argument('--close', metavar='THREAD')
    s.add_argument('--force', action='store_true',
                   help='close even a bound conversation -- kills a live terminal')
    s.set_defaults(fn=conversations.cmd_conversations)
    s = sub.add_parser('score'); s.add_argument('--window', type=int, default=30)
    s.add_argument('--force', action='store_true'); s.set_defaults(fn=metrics.cmd_score)

    s = sub.add_parser('event'); s.add_argument('match')
    s.add_argument('--date'); s.add_argument('--about'); s.add_argument('--why')
    s.set_defaults(fn=events.cmd_event)
    s = sub.add_parser('events'); s.add_argument('--pending', action='store_true')
    s.set_defaults(fn=events.cmd_events)

    s = sub.add_parser('inspect'); s.add_argument('repos', nargs='*')
    s.add_argument('--limit', type=int, default=12)
    s.add_argument('--readme-chars', type=int, default=6000)
    s.set_defaults(fn=gh.cmd_inspect)

    s = sub.add_parser('ghapp', help='the bot identity: show it, mint a token, push as it')
    s.add_argument('--push', action='store_true', help='push a repo as the App')
    s.add_argument('--repo', help='which repo to push (default: /opt/zipper)')
    s.add_argument('--token', dest='print_token', action='store_true',
                   help='print a raw installation token')
    s.set_defaults(fn=ghapp.cmd_ghapp)

    s = sub.add_parser('ext', help='build, sign and publish the browser extension')
    s.add_argument('--build', action='store_true', help='sign a new version')
    s.add_argument('--bump', choices=['major', 'minor', 'patch'], default='patch')
    s.add_argument('--set-version', dest='set_version', metavar='X.Y.Z')
    s.add_argument('--clean', action='store_true',
                   help='put the update_url placeholder back after a failed sign')
    s.set_defaults(fn=ext.cmd_ext)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help(); return 0
    return a.fn(a)
