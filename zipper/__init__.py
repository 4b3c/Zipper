"""zipper -- a queryable model of one person's work.

The package is split by concern; `core` holds the vault paths, the frontmatter
reader and the handful of helpers everything shares.

`core.TODAY` is deliberately read through the module rather than imported by
value: the server is long-running and re-reads it at midnight, and a `from core
import TODAY` would pin a stale date that only misbehaves after a rollover.
"""
from . import core
from .core import (VAULT, INBOX, METADIR, LOGDIR, METDIR, DECDIR, EVTDIR,
                   METCSV, ENUMS, iter_notes, read_note, write_note, parse_fm,
                   fm_dict, as_list, title_of, rel, set_field)

__all__ = ['core', 'VAULT', 'INBOX', 'METADIR', 'LOGDIR', 'METDIR', 'DECDIR',
           'EVTDIR', 'METCSV', 'ENUMS', 'iter_notes', 'read_note', 'write_note',
           'parse_fm', 'fm_dict', 'as_list', 'title_of', 'rel', 'set_field']
