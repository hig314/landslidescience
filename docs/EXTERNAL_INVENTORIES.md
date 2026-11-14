# External landslide inventories

Mirrored third-party inventories and one-at-a-time promotion.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## External landslide inventories — mirrored, not merged

Other people's landslide inventories, shown on the map as their own overlays
and mirrored verbatim into their own tables. **Live on production since
2026-09-24** (77,142 features). Server code is self-contained in
`inventory/external.py` (registry, fetch, API, promotion); the fetcher is
`python manage.py fetch_external_inventory`; client code is the `_ext*`
block in `map.js`.

| Source id | What | Licence / attribution |
|---|---|---|
| `ak_dggs_dds23` | Alaska DGGS DDS 23 (Nicolazzo & Larsen 2025, doi:10.14509/31697) — 5 layers: points, deposits, flanks, scarps, toes | DGGS: name the source, describe modifications |
| `canada_pcld` | Preliminary Canadian Landslide Database (Brideau and others 2026, doi:10.5281/zenodo.20371365) — points, from a CSV | CC BY 4.0, credit required |

**One table per source layer, columns from the publisher** (`ext_<sid>__<layer>`).
Hig's framing, and the reason the design is what it is: *"view these as distinct
datasets from ours … as an overlay it's just some data that you can click on to
see whatever fields they populated it with."* DGGS classifies by material
crossed with movement category, Canada by type/material/size class, we classify
by slow-versus-catastrophic plus creep behaviour and a resolvable age. There is
no honest way to push one into another without deciding, per record, things the
source never said. So nothing is translated on the way in, and adding a third
inventory is a registry entry plus a fetch rather than a schema negotiation.

- **Columns are discovered, never declared.** `ensure_table` builds the table
  from whatever the service or CSV header declares at fetch time and adds
  columns it has not seen before, so a publisher's v15 column widens the
  mirror instead of being silently dropped by hand-written DDL. Publisher
  field labels (the ArcGIS *alias*, e.g. `movement_category` for `mvmt_categ`)
  are stored as **column comments** and are what the popup shows.
- **A refetch is a REPLACE, inside one transaction** — not an upsert by their
  id. These publishers revise, retire and renumber between releases (Canada
  ships a `Modified_in_version` column for exactly that), so an upsert would
  accumulate rows the current release no longer contains and the mirror would
  stop being a mirror. The ArcGIS path also asks the service for its own
  record count first and **refuses to write a layer whose fetched total does
  not match**, because a mirror that is quietly two-thirds complete is worse
  than none: nothing downstream can tell.
- **CSV columns are all text, on purpose.** A service declares its types so we
  use them; a CSV declares nothing, and sniffing is where faithfulness dies
  (a volume of `">1000"` becomes a parse error or a silent null). Text keeps
  what the publisher wrote; interpretation belongs in promotion.
- **ArcGIS Dates arrive as epoch milliseconds** despite being declared Date,
  so they are converted on insert (`to_timestamp(%s/1000.0)`) rather than
  stored as integers nobody will think to decode.
- **Endpoints** (`inventory/urls.py`): `api/external/` (registry + field
  labels + counts), `api/external/<sid>/<layer>/` (**bbox required**, limited),
  `.../record/<ext_id>/`, `.../promote/<ext_id>/` (editor-only, 403 otherwise).
  The map fetches per viewport on `moveend`; active layers persist per-browser
  and ride the URL hash as `ext=<sid>/<layer>`.

### Promotion — one record at a time, deliberately

Hig: *"the promotion … should be singular — some way of selecting a single
item, and promoting it to the main dataset … a natural flow that allows
creation of a polygon, then manipulation of the fields."* So there is **no
bulk adopt**. Clicking a mirrored feature opens its popup; an editor sees a
**Promote into the inventory** button, which creates one of our records seeded
from that point, stamps provenance, and drops the editor into the draw flow to
trace geometry and then the review form. It lands in the **pending queue**
like any other induction, never straight onto the public map.

- **Provenance** is two nullable columns on `landslides` (schema:
  `migrate_external_promotion`, run once per environment): `external_source`
  (registry id) + `external_id` (**the publisher's own id, not our mirror row
  id** — the mirror is replaced on every refetch, so our row ids do not
  survive it). Indexed, **not unique**: two of our records may legitimately
  derive from one of theirs, and forbidding the more careful reading would be
  backwards.
- **Per-source promotion rules** live in the registry's `promote` dict
  (`promote_values()`, fields in `PROMOTE_FIELDS`). Two rules today:
  `ongoing_work` always records where the observation came from ("Noted in the
  Alaska Landslide Inventory Database…"), and `noted_by` is **cleared, not
  filled with the editor who promoted it** — everywhere else a blank
  `noted_by` auto-fills with the editor, which here would credit them with
  noticing something somebody else noticed. `canada_pcld` overrides it to
  `Marc-Andre Brideau`, who built that inventory. Add a source-specific rule
  in the registry, not at the call site.
- **The "Promoted from" card** (`_promoted_from.html`, included by both the
  edit and review forms) shows the source record's fields verbatim beside our
  own, read from the form's own `external_source`/`external_id` inputs so the
  card cannot disagree with what is about to be saved. Nothing in it is an
  input: copying a value is a deliberate act of typing it, which is the whole
  point of promotion being separate from mirroring.

### Refetching, and what a new source costs

```bash
python manage.py fetch_external_inventory --list          # the registry
python manage.py fetch_external_inventory ak_dggs_dds23   # one source
python manage.py fetch_external_inventory --all --dry-run # fetch, report, write nothing
```

Runs **once per environment** like every other PostGIS command (dev and prod
are separate databases). A new source is: one `SOURCES` entry (title, short,
citation, url, licence, colour, fetch spec, `id_field`, `primary_layer`,
optional `promote` rules), then the fetch. No schema work, no client change —
the overlay list, the popup field order and the legend all come from the
registry.
