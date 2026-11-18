"""Dates as people write them, kept to the precision they were written at.

One place for reading a typed date anywhere on the site. A date may be known
to a day, a month or a year, or only as a bracket between two of those, so a
reading is always a pair (earliest day, latest day):

    18 Aug 2003          -> 2003-08-18 .. 2003-08-18
    August 18, 2003      -> the same;  18-Aug-2003 and 2003-08-18 too
    Aug 2003, 2003-08    -> 2003-08-01 .. 2003-08-31
    2003                 -> 2003-01-01 .. 2003-12-31
    1894 to 1904         -> 1894-01-01 .. 1904-12-31   (also "..", an en dash, "/")
    18 Aug 2003 to 2 Sep 2003

python-dateutil reads the free-form ones. It fills whatever is missing from a
default date, which would turn "Aug 2003" into a made-up day, so each text is
read twice against two defaults that differ in every field: a field that
changes between the readings was not written.

Refused on purpose: a year not written with four digits ("Aug 04": 1904 or
2004?) and all-number dates other than ISO ("3/4/2005": March or April?).
"""
import datetime
import re

_ISO = re.compile(r'^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?$')
_SPLIT = re.compile(r'\s*(?:/|\.\.|–|—|\bto\b)\s*')
_MON = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
HINT = 'dates look like 18 Aug 2003, Aug 2003, 2003, or 1894 to 1904'


def _month_end(y, mo):
    nxt = datetime.date(y + (mo == 12), mo % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def _span(y, mo, d):
    if mo is None:
        return datetime.date(y, 1, 1), datetime.date(y, 12, 31)
    if d is None:
        return datetime.date(y, mo, 1), _month_end(y, mo)
    day = datetime.date(y, mo, d)
    return day, day


def parse_one(text):
    """One written date -> (earliest day, latest day). ValueError if unreadable."""
    t = (text or '').strip().rstrip('.')
    m = _ISO.match(t)
    if m:
        y, mo, d = m.groups()
        return _span(int(y), int(mo) if mo else None, int(d) if d else None)
    if not re.search(r'(?<!\d)\d{4}(?!\d)', t):
        raise ValueError('write the year with four digits; ' + HINT)
    if not re.search(r'[A-Za-z]', t):
        raise ValueError('write the month as a name or use YYYY-MM-DD; ' + HINT)
    from dateutil import parser as _p
    try:
        a = _p.parse(t, default=datetime.datetime(2000, 1, 1))
        b = _p.parse(t, default=datetime.datetime(2001, 2, 2))
    except (ValueError, OverflowError):
        raise ValueError('could not read "%s"; %s' % (t, HINT))
    has_mo, has_d = a.month == b.month, a.day == b.day
    if a.year != b.year or (has_d and not has_mo):
        raise ValueError('could not read "%s"; %s' % (t, HINT))
    return _span(a.year, a.month if has_mo else None, a.day if has_d else None)


def parse_span(text):
    """A written date or bracket -> (date_min, date_max). Empty -> (None, None)."""
    text = (text or '').strip()
    if not text:
        return None, None
    parts = _SPLIT.split(text)
    if len(parts) > 2:
        raise ValueError('a date bracket is one date or two; ' + HINT)
    a = parse_one(parts[0])[0]
    b = parse_one(parts[-1])[1]
    if b < a:
        raise ValueError('the bracket ends before it starts')
    return a, b


def parse_day(text):
    """A date that must be known to the day -> date. Empty -> None."""
    a, b = parse_span(text)
    if a != b:
        raise ValueError('a single day is needed, like 18 Aug 2003')
    return a


def _day(d):
    return '%d %s %d' % (d.day, _MON[d.month - 1], d.year)


def _half(d, end):
    """The shortest writing of one end of a bracket that parse_one reads back
    to the same day."""
    if (d.month, d.day) == ((12, 31) if end else (1, 1)):
        return str(d.year)
    if d == (_month_end(d.year, d.month) if end else d.replace(day=1)):
        return '%s %d' % (_MON[d.month - 1], d.year)
    return _day(d)


def span_text(a, b):
    """(date_min, date_max) -> the shortest text parse_span reads back to it."""
    if not a:
        return ''
    b = b or a
    if a == b:
        return _day(a)
    if a.year == b.year and (a.month, a.day, b.month, b.day) == (1, 1, 12, 31):
        return str(a.year)
    if (a.year, a.month) == (b.year, b.month) and a.day == 1 and b == _month_end(b.year, b.month):
        return '%s %d' % (_MON[a.month - 1], a.year)
    return '%s to %s' % (_half(a, False), _half(b, True))
