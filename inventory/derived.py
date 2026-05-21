"""Derived/computed values for landslide records — authoritative implementations.

Each function in `RULES` is a pure transform from a row dict (one landslide
record, keyed by snake_case column name) to a computed value. The source code
of these functions is rendered verbatim at `/inventory/manage/rules/` so the
displayed rule and the executed rule cannot drift apart.

To add a rule:
  1. Write a function `compute_<column>(row) -> value`.
  2. Set `.target_column`, `.inputs`, and optionally `.summary` attributes.
  3. Register it in `RULES` below.

Workflow per rule (from `/inventory/manage/rules/<name>/`):
  - Preview: see every row where stored != computed, side by side.
  - Apply: UPDATE the column across all disagreeing rows in one transaction,
    invalidate caches, write an audit log entry per touched row.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Post-2012 catastrophic events get classified by their precursory-creep
# evidence (if any). Pre-2012 catastrophic events fall into Modern vs Holocene.
POST_2012_THRESHOLD_YEAR = 2012

# Rough end of the Little Ice Age in Alaska — divides "Modern" from "Holocene"
# for catastrophic events without finer dating.
LIA_END_YEAR = 1850


# ---------------------------------------------------------------------------
# Rule: insar_creep
# ---------------------------------------------------------------------------

def compute_insar_creep(row):
    """Tri-state OR-merge of the per-study InSAR creep flags.

    Preserves the distinction between "no study has flagged this" and
    "no study has assessed this":
      - True   if any of (insar_schaefer, insar_kim, insar_opera, insar_other)
               is explicitly True.
      - False  if at least one is explicitly False AND none is True
               (i.e., at least one study assessed it and saw no creep).
      - None   if all four are NULL (no study has assessed this site).
    """
    vals = [row.get(c) for c in
            ('insar_schaefer', 'insar_kim', 'insar_opera', 'insar_other')]
    if any(v is True for v in vals):
        return True
    if any(v is False for v in vals):
        return False
    return None

compute_insar_creep.target_column = 'insar_creep'
compute_insar_creep.inputs        = ('insar_schaefer', 'insar_kim',
                                     'insar_opera', 'insar_other')
compute_insar_creep.summary       = ('Tri-state OR-merge of the per-study '
                                     'InSAR creep flags (preserves NULL='
                                     '"not yet assessed").')


# ---------------------------------------------------------------------------
# Helpers for landslide_class
# ---------------------------------------------------------------------------

def _resolve_event_era(row):
    """Resolve the temporal era of this landslide, in priority order.

    Returns one of:
      - int  (a 4-digit year)         → caller bucket s into 2012+/Modern/Holocene
      - 'Holocene' or 'Modern'        → explicit era token from year_text
      - None                          → can't determine; caller should leave as-is

    Priority (most authoritative first):
      1. seismic_datetime              → most precise (timestamp of failure)
      2. year_text == 4-digit year     → human-recorded numeric year
      3. year_text == 'Holocene'/'Modern' (case-insensitive substring)
                                       → explicit era for prehistoric / pre-record events
      4. date_min                      → fallback; may be a *recording* date
                                         rather than an event date, so it ranks
                                         below explicit era tokens.
    """
    sd = row.get('seismic_datetime')
    if sd is not None:
        return sd.year if hasattr(sd, 'year') else int(str(sd)[:4])
    yt = (row.get('year_text') or '').strip()
    if yt.isdigit() and len(yt) == 4:
        return int(yt)
    yt_l = yt.lower()
    if 'holocene' in yt_l:
        return 'Holocene'
    if 'modern' in yt_l:
        return 'Modern'
    dm = row.get('date_min')
    if dm is not None:
        return dm.year if hasattr(dm, 'year') else int(str(dm)[:4])
    return None


# ---------------------------------------------------------------------------
# Rule: landslide_class
# ---------------------------------------------------------------------------

def compute_landslide_class(row):
    """Categorical class label for this landslide.

    Decision tree (mirrors the inventory legend / map color scheme):

    For SLOW landslides (`landslide_type='slow'`):
      - If `size_inclusion` is False → "Small slow landslide"
      - Else → "Slow " + creep_behavior, e.g. "Slow Obvious creep"
      - If creep_behavior is null → None (needs review)

    For CATASTROPHIC landslides (`landslide_type='catastrophic'`):
      - If `size_inclusion` is False → "Small catastrophic landslide"
      - Else look at year:
        · 2012+ → "Catastrophic " + creep_behavior if any precursor creep is
          recorded, else just "Catastrophic"
        · 1850-2011 → "Catastrophic Modern"
        · before 1850 → "Catastrophic Holocene"
      - If no numeric year but year_text contains "Holocene" or "Modern",
        use that as the era hint.
      - If neither year nor era hint is resolvable → None (needs review)
    """
    t = (row.get('landslide_type') or '').strip().lower()

    if t == 'slow':
        if not row.get('size_inclusion'):
            return 'Small slow landslide'
        cb = (row.get('creep_behavior') or '').strip()
        return f'Slow {cb}' if cb else None

    if t == 'catastrophic':
        if not row.get('size_inclusion'):
            return 'Small catastrophic landslide'
        era = _resolve_event_era(row)
        if era is None:
            return None
        if era == 'Holocene': return 'Catastrophic Holocene'
        if era == 'Modern':   return 'Catastrophic Modern'
        # era is a numeric year
        if era >= POST_2012_THRESHOLD_YEAR:
            cb = (row.get('creep_behavior') or '').strip()
            return f'Catastrophic {cb}' if cb else 'Catastrophic'
        if era >= LIA_END_YEAR:
            return 'Catastrophic Modern'
        return 'Catastrophic Holocene'

    return None

compute_landslide_class.target_column = 'landslide_class'
compute_landslide_class.inputs        = ('landslide_type', 'size_inclusion',
                                         'creep_behavior', 'seismic_datetime',
                                         'year_text', 'date_min')
compute_landslide_class.summary       = ('Categorical class derived from type, '
                                         'size, creep evidence, and event year.')


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RULES = {
    'insar_creep':     compute_insar_creep,
    'landslide_class': compute_landslide_class,
}


# ---------------------------------------------------------------------------
# Comparison helper used by the manage_rules views
# ---------------------------------------------------------------------------

def _normalize(v):
    """Treat empty string as None so '' vs NULL doesn't register as a change."""
    return None if v == '' else v


def diff_against_db(cur, rule_name):
    """Run the rule against all rows; return a dict summarizing differences.

    Returns:
        {
          'agreements': int,
          'changes':    [{'id': N, 'unique_name': str, 'old': v, 'new': v}, ...],
        }
    """
    fn = RULES[rule_name]
    cols = sorted(set(fn.inputs) | {fn.target_column, 'id', 'unique_name'})
    cur.execute(f"SELECT {', '.join(cols)} FROM landslides ORDER BY id")
    rows = cur.fetchall()
    agreements = 0
    changes = []
    for raw in rows:
        row = dict(zip(cols, raw))
        old = _normalize(row[fn.target_column])
        new = _normalize(fn(row))
        if old == new:
            agreements += 1
        else:
            changes.append({
                'id': row['id'],
                'unique_name': row.get('unique_name') or '',
                'old': old,
                'new': new,
            })
    return {'agreements': agreements, 'changes': changes}
