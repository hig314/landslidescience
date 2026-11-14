// node deps.js map.js A B : what lines A..B need from the rest of the closure, and what the rest needs from them
const fs = require('fs'), acorn = require('acorn'), escope = require('eslint-scope');
const [file, A, B] = [process.argv[2], +process.argv[3], +process.argv[4]];
const src = fs.readFileSync(file, 'utf8');
const ast = acorn.parse(src, { ecmaVersion: 2022, sourceType: 'script', locations: true, ranges: true });
const sm = escope.analyze(ast, { ecmaVersion: 2022, sourceType: 'script' });
const closure = sm.globalScope.childScopes[0];           // the file's IIFE
const inR = l => l >= A && l <= B;
const uses = [], exps = [];
for (const v of closure.variables) {
  if (!v.defs.length) continue;
  const dl = v.defs[0].name.loc.start.line, fn = v.defs[0].type === 'FunctionName';
  let rin = 0, rout = 0, win = 0, wout = 0, firstOut = 1e9;
  for (const r of v.references) {
    const l = r.identifier.loc.start.line, w = r.isWrite() && !r.init;
    if (inR(l)) { rin++; if (w) win++; } else { rout++; if (w) wout++; firstOut = Math.min(firstOut, l); }
  }
  const tag = fn ? 'fn' : (win + wout ? 'VAR-MUTATED(in ' + win + ', out ' + wout + ')' : 'var');
  if (!inR(dl) && rin) uses.push(`${v.name} [${tag}, decl ${dl}]`);
  if (inR(dl) && rout) exps.push(`${v.name} [${tag}, first outside use ${firstOut}, ${rout} uses]`);
}
console.log(`lines ${A}-${B} (${B - A + 1})\n NEEDS (${uses.length}):\n   ` + uses.join('\n   ') + `\n GIVES (${exps.length}):\n   ` + exps.join('\n   '));
