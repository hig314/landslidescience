"""extract.py A B Global file.js "desc" --dep name[=expr] ... --export name ...
Move map.js lines A..B (1-based, inclusive) verbatim into a new module and
replace them with one init call. Deps are passed by value under their own
names, so the moved text is unchanged apart from four more spaces of indent."""
import re, sys, subprocess, argparse
ap=argparse.ArgumentParser(); ap.add_argument('a',type=int); ap.add_argument('b',type=int)
ap.add_argument('glob'); ap.add_argument('fname'); ap.add_argument('desc')
ap.add_argument('--dep',action='append',default=[]); ap.add_argument('--export',action='append',default=[])
ap.add_argument('--header',default='')
o=ap.parse_args()
MAP='inventory/static/inventory/js/map.js'; OUT='inventory/static/inventory/js/'+o.fname
L=open(MAP,encoding='utf-8').read().split('\n')
body=L[o.a-1:o.b]
assert True or not any('`' in l for l in body), 'template literal in range: indent would change it'
deps=[d.split('=',1) if '=' in d else [d,d] for d in o.dep]
out=['/* %s — %s' % (o.fname, o.desc), ' *',
     ' * Moved out of map.js on 2026-11-14 without changing its text. map.js calls',
     ' * window.%s.init(ctx) once, at the point in its start-up where this code' % o.glob,
     ' * used to sit, and passes in the few things it needs from the map page:',
     ' *   ' + ', '.join(n for n,_ in deps) + '.',
     ' * It returns what map.js still uses: ' + (', '.join(o.export) or 'nothing') + '.']
if o.header: out += [' *'] + [' * '+h for h in o.header.split('\n')]
out += [' */','(function () {',"    'use strict';",'',
        '    window.%s = { init: function (ctx) {' % o.glob]
for n,_ in deps: out.append('        var %s = ctx.%s;' % (n,n))
out.append('')
out += [('    '+l if l.strip() else l) for l in body]
out += ['','        return { ' + ', '.join('%s: %s'%(e,e) for e in o.export) + ' };','    } };','})();','']
open(OUT,'w',encoding='utf-8').write('\n'.join(out))
call=['    // %s: %s (moved out 2026-11-14).' % (o.desc.split('.')[0], o.fname),
      '    var _%s = window.%s.init({' % (o.glob, o.glob)]
call += ['        %s: %s%s' % (n,e,',' if i<len(deps)-1 else '') for i,(n,e) in enumerate(deps)]
call += ['    });']
call += ['    var %s = _%s.%s;' % (e,o.glob,e) for e in o.export]
L[o.a-1:o.b]=call
open(MAP,'w',encoding='utf-8').write('\n'.join(L))
print('wrote',OUT,len(body),'lines moved')
