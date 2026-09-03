"""zipper.cli

The command line. Every subcommand is `fn=<module>.cmd_*`, so this file is the
only place that knows the whole surface -- and the only place to look when you
want to know what zipper can do.
"""
import argparse

from . import (canvas, chat, decisions, events, gh, ics, lint, metrics,
               runqueue, status, sync, views)


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

    s = sub.add_parser('ingest-budget'); s.add_argument('csvfile')
    s.set_defaults(fn=metrics.cmd_ingest_budget)

    s = sub.add_parser('agenda'); s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=ics.cmd_agenda)

    s = sub.add_parser('github'); s.add_argument('--since-days', type=int, default=30)
    s.add_argument('--full', action='store_true'); s.set_defaults(fn=gh.cmd_github)

    s = sub.add_parser('queue');  s.set_defaults(fn=runqueue.cmd_queue)
    s = sub.add_parser('views');  s.set_defaults(fn=views.cmd_views)
    s = sub.add_parser('discord', help='talk to the always-on Discord bot')
    s.add_argument('action', choices=['send', 'read', 'status'])
    s.add_argument('text', nargs='?', default='')
    s.add_argument('--file', help='path to attach')
    s.add_argument('--thread', help='thread id; default is the main channel')
    s.add_argument('--limit', type=int, default=5)
    s.set_defaults(fn=chat.cmd_discord)
    s = sub.add_parser('canvas'); s.add_argument('--file'); s.add_argument('--days', type=int, default=21)
    s.set_defaults(fn=canvas.cmd_canvas)
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

    s = sub.add_parser('catchup'); s.add_argument('--days', type=int, default=14)
    s.add_argument('--apply', action='store_true', default=True)
    s.set_defaults(fn=runqueue.cmd_catchup)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help(); return 0
    return a.fn(a)
