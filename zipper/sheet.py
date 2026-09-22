"""zipper.sheet

Reading and writing the Luminosity timesheet through the Sheets API.

The tab is not a table. It is week blocks -- a `Week N` header carrying the
week's range and a SUM over the rows beneath it, then one row per session --
and a writer that ignores that shape would produce rows the totals never count.
Two details of his sheet decide everything here:

  * The Total column is a formula, `=C{row}-B{row}`, not a value. A row written
    with "5:30:00" in D would look right and stop tracking its own times.
  * A week header's total has *headroom*: Week 4 is `=SUM(D27:D35)` with rows
    only to 30. So the natural place for a new row is the next free row inside
    that range, where the total picks it up with nothing else touched.

The headroom is the whole reason this can be an append rather than a row
insert. Inserting shifts every row below and rewrites other weeks' formulas;
writing into a blank row inside an existing SUM range touches one row and can
be checked afterwards. When a week runs out of headroom this refuses rather
than guessing -- see `plan`.

Times go in as text ("1:30"), which Sheets parses into the same day-fraction it
already stores. The 24-hour-across-noon rule lives in `zipper.hours`, because it
is a fact about his sheet rather than about this transport.
"""
import datetime as dt
import re

from . import google, hours

EPOCH = dt.date(1899, 12, 30)       # what a Sheets date serial counts from


def _d(serial):
    try:
        return EPOCH + dt.timedelta(days=int(float(serial)))
    except (TypeError, ValueError):
        return None


def _pad(row, n=6):
    return (list(row) + [''] * n)[:n]


class Tab:
    """One semester tab, parsed into week blocks."""

    def __init__(self, sheet_id, name):
        self.id, self.name = sheet_id, name
        rng = f"'{name}'!A1:F200"
        self.shown = [_pad(r) for r in google.read(sheet_id, rng)]
        self.formula = [_pad(r) for r in google.read_formula(sheet_id, rng)]
        self.weeks = []
        self._parse()

    def _parse(self):
        cur = None
        for i, row in enumerate(self.shown, 1):
            a = str(row[0]).strip()
            if re.match(r'^Week\s+\d+', a):
                m = re.match(r'^=SUM\(D(\d+):D(\d+)\)$',
                             str(self.formula[i - 1][3]).strip())
                cur = {'header': i, 'label': a, 'range': str(row[4]).strip(),
                       'submitted': str(row[5]).strip(),
                       'sum_from': int(m.group(1)) if m else None,
                       'sum_to': int(m.group(2)) if m else None,
                       'details': []}
                self.weeks.append(cur)
            elif cur is not None and a:
                cur['details'].append(i)

    # -- what the ledger needs ------------------------------------------------

    def entries(self):
        """Every session row, in the shape `hours.reconcile` takes.

        Read from the *displayed* values, not the underlying serials, and kept
        that way. His 12-hour cells are ambiguous about which half of the day
        they mean, and nothing here needs to know -- the duration is the
        subtraction of the two cells as written, and the key is the pair as
        written. Guessing the real hour was a bug, not a missing feature.
        """
        out = []
        for i, row in enumerate(self.shown, 1):
            a = str(row[0]).strip()
            if not a or a.lower() == 'date' or re.match(r'^Week\s+\d+', a):
                continue
            if re.match(r'^(Fall|Spring|Summer)\s+\d{4}$', a):
                continue
            try:
                date = dt.datetime.strptime(a, '%m/%d/%Y').date()
            except ValueError:
                continue
            # H:MM as well as H:MM:SS. A freshly written cell inherits the
            # column's default format rather than its neighbour's, so it can
            # read back a digit group short -- and a total this failed to parse
            # was a row the ledger could not see, which would be written again
            # on the next push, and again after that.
            m = re.match(r'^(\d+):(\d+)(?::(\d+))?$', str(row[3]).strip())
            if not m:
                continue
            h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            st, en = str(row[1]).strip(), str(row[2]).strip()
            sub = str(row[5]).strip()
            try:
                sub = dt.datetime.strptime(sub, '%m/%d/%Y').date().isoformat()
            except ValueError:
                sub = None
            out.append({'date': date.isoformat(), 'start': st, 'end': en,
                        'hours': round(h + mi / 60 + se / 3600, 4),
                        'note': str(row[4]).strip(), 'submitted': sub})
        return out

    # -- where a new row goes -------------------------------------------------

    def week_for(self, date):
        """The block whose stated range contains this date."""
        for w in self.weeks:
            m = re.match(r'^(\d\d/\d\d/\d{4})\s+to\s+(\d\d/\d\d/\d{4})$', w['range'])
            if not m:
                continue
            a = dt.datetime.strptime(m.group(1), '%m/%d/%Y').date()
            b = dt.datetime.strptime(m.group(2), '%m/%d/%Y').date()
            if a <= date <= b:
                return w
        return None

    def free_row(self, w, taken):
        """The next blank row inside the week's SUM range, or None."""
        if not w['sum_from']:
            return None
        last = max(w['details']) if w['details'] else w['header']
        for r in range(last + 1, w['sum_to'] + 1):
            if r in taken:
                continue
            if any(str(x).strip() for x in self.shown[r - 1]) if r <= len(self.shown) else False:
                continue
            return r
        return None


def plan(sheet_id, tab_name, entries):
    """Decide a row for each pending entry, refusing anything unclear.

    Returns (writes, refused). A write is (row, values) where values already
    carry the formula for D and his rendering for B and C.
    """
    tab = Tab(sheet_id, tab_name)
    writes, refused, taken = [], [], set()
    for e in entries:
        date = dt.date.fromisoformat(e['date'])
        w = tab.week_for(date)
        if w is None:
            refused.append((e, f'no week block covers {e["date"]}'))
            continue
        if w['submitted'].lower() == 'yes':
            # The week has been billed. Adding to it silently would change a
            # total he has already copied into Workday.
            refused.append((e, f'{w["label"]} is already submitted'))
            continue
        row = tab.free_row(w, taken)
        if row is None:
            refused.append((e, f'{w["label"]} has no free row inside '
                               f'{w["sum_from"]}:{w["sum_to"]}'))
            continue
        st, en = hours.sheet_times(e['start'], e['end'])
        if not st or not en:
            # Column D is `=C-B`, so a row without a span bills zero hours and
            # reads back with a key the entry does not have -- invisible to
            # `pull`, still pending, and written again on the next push.
            refused.append((e, 'no start/end; the sheet totals by subtraction'))
            continue
        taken.add(row)
        writes.append((row, [date.strftime('%m/%d/%Y'), st, en,
                             f'=C{row}-B{row}', e['note'], '']))
    return tab, writes, refused


def match_format(sheet_id, tab, writes):
    """Copy each written row's formatting from the row above it.

    Values alone land in the column's default format, so a duration comes back
    as 0:30 where every neighbour reads 0:30:00. That is cosmetic in the sheet
    and load-bearing in the ledger, which reads what the sheet displays.
    """
    gid = None
    for t in google.tabs(sheet_id):
        if t['title'] == tab:
            gid = t['sheetId']
    if gid is None or not writes:
        return
    # The nearest *session* row above, not literally the row above. The first
    # row of a freshly opened week has the `Week N` header above it, whose A-C
    # cells carry no date or time format -- a row that inherited those read back
    # as bare serials, which `entries` cannot parse, so it stayed pending and
    # would have been written again on the next push.
    t = Tab(sheet_id, tab)
    sessions = sorted({r for w in t.weeks for r in w['details']})
    reqs = []
    for row, _ in writes:
        src = max((r for r in sessions if r < row), default=row - 1)
        reqs.append({'copyPaste': {
            'source': {'sheetId': gid, 'startRowIndex': src - 1,
                       'endRowIndex': src, 'startColumnIndex': 0,
                       'endColumnIndex': 6},
            'destination': {'sheetId': gid, 'startRowIndex': row - 1,
                            'endRowIndex': row, 'startColumnIndex': 0,
                            'endColumnIndex': 6},
            'pasteType': 'PASTE_FORMAT'}})
    google.batch(sheet_id, reqs)


# -- opening the next week ----------------------------------------------------

HEADROOM = 9            # session rows a new block's SUM covers, as Week 4 has


def new_week(sheet_id, tab_name, date, dry=False):
    """Append a `Week N` block covering the week `date` falls in.

    An append, never an insert, for the same reason `plan` appends: inserting
    shifts every row below and rewrites the formulas of weeks already billed.

    The header cannot go in the first blank row of the previous block: those
    rows are its SUM's headroom, and a header written among them would put the
    new week's total inside the old week's sum, so the old week would bill the
    new week's hours on top of its own.

    So the previous block's SUM is **trimmed to the rows it actually used**
    first, and the header goes immediately after. The total does not move --
    every row dropped is empty -- which is what makes this safe on a week
    already submitted. What the old week loses is its headroom, so an entry
    backfilled into it after the next week is open has nowhere to go and `push`
    refuses. That is the accepted trade: he logs in order, and never a Monday
    before the Sunday before it (his words, 2026-09-22).

    Returns (row, values, trim), where `trim` is the previous block's new SUM
    formula or None. Raises ValueError with the reason it refused.
    """
    tab = Tab(sheet_id, tab_name)
    monday = date - dt.timedelta(days=date.weekday())
    sunday = monday + dt.timedelta(days=6)

    existing = tab.week_for(date)
    if existing is not None:
        raise ValueError(f'{existing["label"]} already covers {date}')

    last = tab.weeks[-1] if tab.weeks else None
    if last is None:
        raise ValueError(f"'{tab_name}' has no week blocks to append after")
    if last['sum_to'] is None:
        raise ValueError(f'{last["label"]} has no SUM range to append after')

    # Trim the previous block to its used rows, so the new header sits directly
    # beneath it. `sum_from` is the floor: a week with no sessions keeps one row
    # rather than collapsing into a backwards range.
    last_used = max(last['details']) if last['details'] else 0
    keep_to = max(last_used, last['sum_from'])
    trim = (f"=SUM(D{last['sum_from']}:D{keep_to})"
            if keep_to < last['sum_to'] else None)
    row = keep_to + 1

    n = 0
    for w in tab.weeks:
        m = re.match(r'^Week\s+(\d+)', w['label'])
        if m:
            n = max(n, int(m.group(1)))
    values = [f'Week {n + 1}', '', '', f'=SUM(D{row + 1}:D{row + HEADROOM})',
              f'{monday:%m/%d/%Y} to {sunday:%m/%d/%Y}', 'No']
    if dry:
        return row, values, trim

    writes = [(f"'{tab_name}'!A{row}:F{row}", [values])]
    if trim:
        writes.insert(0, (f"'{tab_name}'!D{last['header']}", [[trim]]))
    google.write(sheet_id, writes)
    # Formatting comes from the previous *header*, not the row above -- the row
    # above is a session row, and a header that inherits it reads as one.
    gid = next((t['sheetId'] for t in google.tabs(sheet_id)
                if t['title'] == tab_name), None)
    if gid is not None:
        google.batch(sheet_id, [{'copyPaste': {
            'source': {'sheetId': gid, 'startRowIndex': last['header'] - 1,
                       'endRowIndex': last['header'], 'startColumnIndex': 0,
                       'endColumnIndex': 6},
            'destination': {'sheetId': gid, 'startRowIndex': row - 1,
                            'endRowIndex': row, 'startColumnIndex': 0,
                            'endColumnIndex': 6},
            'pasteType': 'PASTE_FORMAT'}}])
    return row, values, trim
