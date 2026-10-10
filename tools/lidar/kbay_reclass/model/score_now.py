"""Score the CURRENT dtm_blend / dtm_final of the six Grewingk sites (v2.score: % cells too high /
too low vs offset-corrected Grewingk). Usage: score_now.py <label>  -> site/score_<label>.json"""
import sys, os, json; os.chdir('/Volumes/Nunatak/lidar_build/kbay_reclass_test'); sys.path.insert(0, 'model')
import v2, sitekit as K
out = {}
for s in v2.SITES:
    out[s] = {k: v2.score(s, K.rd(f'site/{s}/{f}')[0])['ALL'] for k, f in [('vendor', 'vendor_dtm.tif'), ('blend', 'dtm_blend.tif'), ('final', 'dtm_final.tif')]}
    print(s, ' '.join(f"{k} {v['high']}/{v['low']}" for k, v in out[s].items()), flush=True)
json.dump(out, open(f'site/score_{sys.argv[1]}.json', 'w'), indent=1)
