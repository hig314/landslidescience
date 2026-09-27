"""Floor under the solver surface: min of real returns (noise excluded; snow kept, it is a physical
upper bound on ground) over a 5x5 cell window. How often does the solver dip > TOL below it, and
does Grewingk (offset-corrected) ever do that too?"""
import sys, os; os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model')
import numpy as np, sitekit as K
from scipy import ndimage
def floor(s, win=5):
    S = K.site_info(s); x0, x1, y0, y1 = S['bounds']; NX, NY = int(x1-x0), int(y1-y0)
    P = np.load(f'site/{s}/pts.npz'); m = ~np.isin(P['cls'], (7, 18))
    cid = np.clip((y1-P['y'][m]).astype(int), 0, NY-1)*NX + np.clip((P['x'][m]-x0).astype(int), 0, NX-1)
    F = np.full(NX*NY, np.inf); np.minimum.at(F, cid, P['z'][m])
    F = ndimage.minimum_filter(F.reshape(NY, NX), win, mode='nearest'); F[np.isinf(F)] = np.nan
    return F
if __name__ == '__main__':
    for s in sys.argv[1:]:
        F = K.return_floor(s); Z, _ = K.rd(f'site/{s}/dtm_s_e.tif'); Fi, _ = K.rd(f'site/{s}/dtm_final.tif')
        out = [f'{s}: solver below floor-0.5: {np.nanmean(Z < F-0.5)*100:.2f}%  final below floor-0.5: {np.nanmean(Fi < F-0.5)*100:.2f}%  max dip {np.nanmax(F-Z):.1f} m']
        if not os.path.exists(f'site/{s}/NO_GREWINGK_grewingk_dtm_is_a_template_copy_of_vendor'):
            G, _ = K.rd(f'site/{s}/grewingk_dtm.tif'); O, _ = K.rd(f'site/{s}/offset_field.tif'); G = G + O
            out.append(f'   Grewingk below floor-0.5: {np.nanmean(G < F-0.5)*100:.2f}%  below floor-0.3: {np.nanmean(G < F-0.3)*100:.2f}%')
        print('\n'.join(out), flush=True)
