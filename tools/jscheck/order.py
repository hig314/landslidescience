"""order.py — init-order check for the split-out modules (run from the repo root).

Each `window.LSx.init({ name: name, ... })` call in map.js passes its deps BY
VALUE at the moment the call runs. A dep that is a top-level `var` assigned
further down the file is still undefined then (a `function` declaration is
hoisted and is fine). verify.sh cannot see that; this does. Exit 1 on a find.
"""
import re, sys
MAP = 'inventory/static/inventory/js/map.js'
L = open(MAP, encoding='utf-8').read().split('\n')
fn = {}; var = {}
for i, l in enumerate(L, 1):
    m = re.match(r'    function ([A-Za-z_$][\w$]*)\s*\(', l)
    if m: fn.setdefault(m.group(1), i)
    m = re.match(r'    var (.+)', l)
    if m:
        for part in re.split(r',\s*(?=[A-Za-z_$][\w$]*\s*(?:=|,|;|$))', m.group(1)):
            n = re.match(r'([A-Za-z_$][\w$]*)\s*(=)?', part)
            if n and n.group(2): var.setdefault(n.group(1), i)
bad = 0
i = 0
while i < len(L):
    m = re.match(r'    var _(LS\w+) = window\.LS\w+\.init\(\{', L[i])
    if m:
        at = i + 1; j = i + 1
        while not L[j].startswith('    });'):
            d = re.match(r'        (\w+): ([A-Za-z_$][\w$]*),?$', L[j])
            if d and d.group(2) not in fn:
                q = var.get(d.group(2))
                if q is None:
                    print('%s (line %d): dep %s has no top-level definition found' % (m.group(1), at, d.group(2))); bad = 1
                elif q > at:
                    print('%s (line %d): dep %s is assigned LATER, at line %d' % (m.group(1), at, d.group(2), q)); bad = 1
            j += 1
        i = j
    i += 1
print('init order: ' + ('PROBLEMS' if bad else 'ok'))
sys.exit(bad)
