// Usage: node free.js file.js  -> prints the file's unresolved identifiers (its "free" names), one per line
const fs = require('fs'), acorn = require('acorn'), escope = require('eslint-scope');
const src = fs.readFileSync(process.argv[2], 'utf8');
const ast = acorn.parse(src, { ecmaVersion: 2022, sourceType: 'script', ranges: true, locations: true });
const sm = escope.analyze(ast, { ecmaVersion: 2022, sourceType: 'script' });
const names = new Map();
for (const ref of sm.globalScope.through) {
  const n = ref.identifier.name;
  if (!names.has(n)) names.set(n, { line: ref.identifier.loc.start.line, write: false });
  if (ref.isWrite()) names.get(n).write = true;
}
// implicit globals created by assignment are in globalScope.implicit too
for (const [n, v] of [...names].sort()) console.log(n + (v.write ? ' WRITE' : '') + ' @' + v.line);
