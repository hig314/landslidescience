# jscheck — moving code out of map.js without changing it

`map.js` is one closure; a section moved to its own file can silently leave
a name behind (the page then fails only when that code path runs). These
tools make that a static check. One-time setup: `cd tools/jscheck && npm install`
(`node_modules/` is gitignored). Run everything from the repo root.

- `node tools/jscheck/deps.js inventory/static/inventory/js/map.js A B` — what
  lines A..B **need** from the rest of the closure and what the rest **uses**
  from them, each marked `fn`, `var`, or `VAR-MUTATED`. A section whose
  crossings are all `fn` / never-reassigned `var` can move verbatim. A
  `VAR-MUTATED` crossing cannot be passed by value: it needs a getter/setter
  or the state has to move to one side first.
- `python3 tools/jscheck/extract.py A B LSName file.js "description" --dep x --export y`
  — moves the lines into `window.LSName.init(ctx)` (text unchanged, four
  spaces more indent) and leaves the init call where they were. Add the
  `<script>` tag to `home.html` before `map.js`.
- `tools/jscheck/verify.sh file.js …` — no script may use a name that is
  neither defined in it nor in `globals_baseline.txt`. Add a line to the
  baseline when a new module global (`window.LSSomething`) is introduced.

Before extracting, also check by eye: every export's first outside use comes
after the section (a `function` used to be hoisted; the `var` that replaces
it is not), and every dep is assigned before the section's position.
