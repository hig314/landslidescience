"""Schema for bathymetric soundings: sparse depths and digitised depth contours.

    python manage.py migrate_soundings            # apply
    python manage.py migrate_soundings --dry-run  # print the SQL

WHAT THIS IS FOR (Hig, 2026-10-12)
----------------------------------
Lake and nearshore depths turn up as scattered field measurements -- a
handheld sounder and a GPS from a packraft, a lead line, an old map -- that
are too sparse to be a raster survey and too valuable to lose in a folder.
They feed the topobathy composites (tools/lidar/topobathy/fill.py conditions
a lake basin on them) and show on /lidar/ and /inventory/ as reference points.
Dense data (multibeam, BAG) stays a raster survey in tools/lidar/datasets.json;
this is the place for the sparse rest.

THE MODEL
---------
  sounding_surveys    one per delivery: who, when, how, units, licence,
                      public or gated, the default uncertainty, and where the
                      untouched original files are kept.
  sounding_levels     a water surface: one water body's level on a date, with
                      its source and uncertainty. A depth is measured from the
                      water surface; the elevation of the bed is level - depth.
  soundings           the points. DEPTH AS MEASURED (metres, plus the original
                      value and unit), the level it hangs from, sigma, and a
                      quality flag with a reason. Never deleted for being
                      wrong -- flagged 'rejected' so the record of what was
                      measured survives the judgement about it.
  sounding_contours   digitised depth contours (e.g. ADF&G lake maps from old
                      surveys whose sounding positions are unknown): a line, its
                      depth, the level it hangs from, a sigma that is larger
                      than a sounding's because the line is an interpretation.

DEPTH BOUNDS (2026-10-13, Hig): a sounding can be a LOWER BOUND -- a lead line
that ran out (">120"): depth_bound = 'min' says the bed is at least depth_m
down. It sits on the colour scale at that depth, and it must survive as a
limit for synthetic bathymetry, never as a depth: export_soundings writes the
bound column and topobathy/fill.py keeps bounds out of its exact points.
'' = an ordinary depth.

Elevation is DERIVED, never stored: z = level_m - depth_m (soundings_z view).
A lake's level is often not known for the day of the survey (Nick Riordan's
2018 depths hang from the 2023 lidar water surface), and a better level later
should correct every point in the lake without touching what was measured.
Uncertainty adds the same way: sigma_z = sqrt(sigma_depth^2 + sigma_level^2).

Users are Django usernames as text, not foreign keys: the auth tables are in
SQLite, not PostGIS (as fault_scarps).

Idempotent: CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR
REPLACE VIEW, so re-running is safe and extending it is an edit here.
"""
from django.core.management.base import BaseCommand

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sounding_surveys (
    id              serial PRIMARY KEY,
    slug            text NOT NULL UNIQUE,          -- e.g. riordan_2018_kbsp_lakes
    title           text NOT NULL,
    collector       text NOT NULL DEFAULT '',      -- who measured
    collected_from  date,
    collected_to    date,
    -- how: sounder | lead_line | contour_map | estimate | other (free text
    -- with these as the vocabulary; the default sigma depends on it)
    method          text NOT NULL DEFAULT 'sounder',
    instrument      text NOT NULL DEFAULT '',
    depth_unit      text NOT NULL DEFAULT 'm',     -- the unit as delivered
    sigma_m         real NOT NULL DEFAULT 1.0,     -- default per-point depth sigma (incl. position)
    horizontal_m    real,                          -- position uncertainty, as stated or estimated
    licence         text NOT NULL DEFAULT '',
    citation        text NOT NULL DEFAULT '',
    public          boolean NOT NULL DEFAULT false,-- false: signed-in data users only
    raw_path        text NOT NULL DEFAULT '',      -- where the untouched originals are kept
    notes           text NOT NULL DEFAULT '',
    ingest          jsonb,                         -- what the ingest did: unit changes, drops, counts
    created_by      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sounding_levels (
    id              serial PRIMARY KEY,
    survey_id       integer NOT NULL REFERENCES sounding_surveys(id) ON DELETE CASCADE,
    key             text NOT NULL,                 -- short id within the survey (china_poot)
    water_body      text NOT NULL DEFAULT '',      -- display name
    level_m         real,                          -- NULL: unknown, the points have no elevation yet
    datum           text NOT NULL DEFAULT 'NAVD88',
    sigma_m         real NOT NULL DEFAULT 0.5,
    observed_on     date,                          -- the date the LEVEL refers to (may differ from the survey)
    source          text NOT NULL DEFAULT '',      -- how it was determined
    geom            geometry(Point, 4326),         -- a label point for the water body
    UNIQUE (survey_id, key)
);

CREATE TABLE IF NOT EXISTS soundings (
    id              serial PRIMARY KEY,
    survey_id       integer NOT NULL REFERENCES sounding_surveys(id) ON DELETE CASCADE,
    level_id        integer REFERENCES sounding_levels(id) ON DELETE SET NULL,
    geom            geometry(Point, 4326) NOT NULL,
    observed_at     timestamptz,
    depth_m         real NOT NULL,                 -- positive down from the water surface
    depth_orig      real,                          -- as delivered
    depth_unit      text NOT NULL DEFAULT 'm',
    sigma_m         real,                          -- NULL: the survey's default
    flag            text NOT NULL DEFAULT '',      -- '' ok | suspect | rejected
    flag_reason     text NOT NULL DEFAULT '',
    name            text NOT NULL DEFAULT '',      -- the field label (waypoint name)
    notes           text NOT NULL DEFAULT '',
    created_by      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE soundings ADD COLUMN IF NOT EXISTS depth_bound text NOT NULL DEFAULT '';  -- '' depth | 'min' at least this deep
CREATE INDEX IF NOT EXISTS soundings_geom_idx ON soundings USING GIST (geom);
CREATE INDEX IF NOT EXISTS soundings_survey_idx ON soundings (survey_id);

CREATE TABLE IF NOT EXISTS sounding_contours (
    id              serial PRIMARY KEY,
    survey_id       integer NOT NULL REFERENCES sounding_surveys(id) ON DELETE CASCADE,
    level_id        integer REFERENCES sounding_levels(id) ON DELETE SET NULL,
    geom            geometry(MultiLineString, 4326) NOT NULL,
    depth_m         real NOT NULL,
    depth_orig      real,
    depth_unit      text NOT NULL DEFAULT 'm',
    sigma_m         real,                          -- NULL: the survey's default
    flag            text NOT NULL DEFAULT '',
    flag_reason     text NOT NULL DEFAULT '',
    notes           text NOT NULL DEFAULT '',
    created_by      text NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sounding_contours_geom_idx ON sounding_contours USING GIST (geom);

-- Elevation and its uncertainty, derived. sigma_z adds the depth's and the
-- level's in quadrature; z is NULL where the level is unknown. Dropped and
-- re-created rather than replaced: s.* grows when soundings gains a column, and
-- CREATE OR REPLACE VIEW cannot insert one ahead of the view's own columns.
DROP VIEW IF EXISTS soundings_z;
CREATE VIEW soundings_z AS
SELECT s.*, l.level_m, l.key AS level_key, l.water_body,
       l.level_m - s.depth_m AS z,
       sqrt(power(COALESCE(s.sigma_m, v.sigma_m), 2) + power(COALESCE(l.sigma_m, 0), 2)) AS sigma_z,
       COALESCE(s.sigma_m, v.sigma_m) AS sigma_depth
  FROM soundings s
  JOIN sounding_surveys v ON v.id = s.survey_id
  LEFT JOIN sounding_levels l ON l.id = s.level_id;
"""


class Command(BaseCommand):
    help = 'Create / extend the soundings tables (idempotent)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn
        if opts['dry_run']:
            self.stdout.write(SCHEMA_SQL)
            return
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            conn.commit()
            out = []
            for t in ('sounding_surveys', 'sounding_levels', 'soundings', 'sounding_contours'):
                cur.execute('SELECT count(*) FROM ' + t)
                out.append('%s %d' % (t, cur.fetchone()[0]))
        finally:
            _put_conn(conn)
        self.stdout.write(self.style.SUCCESS('soundings ready: ' + ', '.join(out)))
