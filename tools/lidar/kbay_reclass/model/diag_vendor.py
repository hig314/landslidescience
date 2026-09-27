import numpy as np
d = np.load('model/pts.npz')
dz, cls, sl, cu = d['dz'], d['cls'], d['slope'], d['curv']
x, y = d['x'], d['y']
inner = (x > 603046) & (x < 604486) & (y > 6604794) & (y < 6606234)      # drop 30 m edge
tol = 0.20 + 0.25*np.tan(np.radians(np.minimum(sl, 70)))                   # 0.2 m + 0.2-ish m horizontal slack
at = np.abs(dz) <= tol; above = dz > 0.5 + tol
g = (cls == 2) & inner; ng = (cls != 2) & (cls != 7) & inner
print('vendor ground points: %d; FALSE ground (>0.5 m above teacher, slope-aware): %.1f%%; at teacher surface: %.1f%%' % (g.sum(), 100*above[g].mean(), 100*at[g].mean()))
print('vendor NON-ground points at the teacher surface (missed ground): %d = %.2f%% of non-ground; vs %d true-ground points labelled ground' % ((ng&at).sum(), 100*(ng&at).mean()/1, (g&at).sum()))
print('\n%-9s %9s %9s %9s' % ('slope', 'falseG%', 'missedG', 'caughtG'))
for a, b in [(0,15),(15,30),(30,45),(45,60),(60,90)]:
    s = (sl >= a) & (sl < b)
    print('%2d-%2d     %8.1f  %8d  %8d' % (a, b, 100*above[g&s].mean(), (ng&at&s).sum(), (g&at&s).sum()))
print('\nby landform (Grewingk surface minus 15 m mean):')
for a, b, lab in [(-9,-1,'channel <-1 m'),(-1,-0.3,'concave'),(-0.3,0.3,'planar'),(0.3,1,'convex'),(1,9,'crest >+1 m')]:
    s = (cu >= a) & (cu < b)
    print('%-15s falseG %5.1f%%   missed-ground pts %8d   caught %8d   missed share of true ground %5.1f%%' % (lab, 100*above[g&s].mean(), (ng&at&s).sum(), (g&at&s).sum(), 100*(ng&at&s).sum()/max(1,((ng|g)&at&s).sum())))
