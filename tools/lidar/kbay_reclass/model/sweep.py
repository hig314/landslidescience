"""Sweep surface.py weightings; composite each onto the true-returns TIN (dtm_final_tin);
report whole-site and inside-zone scores on every held-out site.

    python model/sweep.py name:zone_p:dilate:tau:cand_w:lam_in [...]
"""
import json, subprocess, sys
import numpy as np, rasterio
from scipy import ndimage
sys.path.insert(0, 'model'); import sitekit as K, loso as L
FEATHER = 3


def composite(s, v):
    d = f'site/{s}'
    T, _ = K.rd(f'{d}/dtm_final_tin.tif'); Z, _ = K.rd(f'{d}/dtm_s_{v}.tif')
    with rasterio.open(f'{d}/zone_s_{v}.tif') as src: zone = src.read(1).astype(bool)
    wgt = np.clip(1 - ndimage.distance_transform_edt(~zone)/FEATHER, 0, 1)
    C = np.where(np.isfinite(Z), wgt*Z + (1-wgt)*T, T)
    with rasterio.open(f'{d}/dtm_final_tin.tif') as src: prof = src.profile
    with rasterio.open(f'{d}/dtm_comp_{v}.tif', 'w', **prof) as dst:
        dst.write(np.where(np.isnan(C), prof['nodata'], C).astype('float32'), 1)
    return zone


def inzone(s, v, zone):
    d = f'site/{s}'; off = json.load(open('site/offsets_strict.json'))[s]['median']
    G, _ = K.rd(f'{d}/grewingk_dtm.tif'); T, _ = K.rd(f'{d}/dtm_final_tin.tif'); C, _ = K.rd(f'{d}/dtm_comp_{v}.tif')
    e = np.zeros_like(zone); e[30:-30, 30:-30] = True
    m = zone & e & np.isfinite(G) & np.isfinite(T) & np.isfinite(C) & ~K.flat_mask(G)
    Fo = K.offset_grid(d); a, b = T-Fo-G, C-Fo-G
    if m.sum() < 50:
        return dict(zone_pct=round(100*float(zone[e].mean()), 2))
    return dict(zone_pct=round(100*float(zone[e].mean()), 2), hi_before=round(100*float(np.mean(a[m] > 0.5)), 1),
                hi_after=round(100*float(np.mean(b[m] > 0.5)), 1), lo_before=round(100*float(np.mean(a[m] < -0.5)), 1),
                lo_after=round(100*float(np.mean(b[m] < -0.5)), 1), med_before=round(float(np.median(a[m])), 3),
                med_after=round(float(np.median(b[m])), 3))


if __name__ == '__main__':
    res = json.load(open('site/loso_results.json'))
    for spec in sys.argv[1:]:
        v, zp, dil, tau, cw, lam = spec.split(':')
        subprocess.run([sys.executable, 'model/surface.py', '--zone-p', zp, '--dilate', dil, '--tau', tau,
                        '--cand-w', cw, '--lam-in', lam, '--tag', f's_{v}'], check=True, capture_output=True)
        res = json.load(open('site/loso_results.json'))
        for s in L.SITES:
            zone = composite(s, v)
            off = json.load(open('site/offsets_strict.json'))[s]['median']
            res[s]['dtm'][f'comp_{v}'] = L.score(f'site/{s}', f'comp_{v}', off)
            res[s].setdefault('inzone', {})[v] = inzone(s, v, zone)
            r = res[s]['dtm']; iz = res[s]['inzone'][v]
            print('%-8s %-13s site %4.1f/%4.1f (tin %4.1f/%4.1f)  zone %5.2f%%  in-zone hi %s->%s lo %s->%s med %s->%s' % (
                v, s, r[f'comp_{v}']['ALL']['high'], r[f'comp_{v}']['ALL']['low'], r['final_tin']['ALL']['high'], r['final_tin']['ALL']['low'],
                iz['zone_pct'], iz.get('hi_before'), iz.get('hi_after'), iz.get('lo_before'), iz.get('lo_after'), iz.get('med_before'), iz.get('med_after')), flush=True)
        json.dump(res, open('site/loso_results.json', 'w'), indent=1)
