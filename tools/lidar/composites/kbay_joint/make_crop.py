#!/opt/anaconda3/bin/python3
"""A crop recipe from a box's recipe: the same sources, seeds and settings over a small window, its own id / out dirs.
    make_crop.py <box-id> <crop-id> x0 y0 x1 y1        -> composites/kbay_joint/crops/<crop-id>/recipe.json
Run it with run_box.sh (which looks in boxes/ then crops/)."""
import json, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
box, cid = sys.argv[1], sys.argv[2]; x0, y0, x1, y1 = (int(float(v)) for v in sys.argv[3:7])
s = open(HERE / 'boxes' / box / 'recipe.json').read()
s = s.replace(f'/Volumes/Powder/lidar_build/kbay_joint/boxes/{box}', f'/Volumes/Powder/lidar_build/kbay_joint/crops/{cid}')
s = s.replace(f'/Volumes/Nunatak/lidar_build/composite/{box}/', f'/Volumes/Nunatak/lidar_build/composite/{cid}/')
r = json.loads(s)
r['id'] = cid; r['dataset_id'] = f'kbay_joint_{cid}'; r['title'] = f'Kachemak Bay point-first topobathy, crop {cid} (of {box})'
r['grid']['bounds'] = [x0, y0, x1, y1]
r['joint']['units']['win'] = [x0 // 1000 * 1000, y0 // 1000 * 1000, -(-x1 // 1000) * 1000, -(-y1 // 1000) * 1000]
d = HERE / 'crops' / cid; d.mkdir(parents=True, exist_ok=True)
json.dump(r, open(d / 'recipe.json', 'w'), indent=1); open(d / 'recipe.json', 'a').write('\n')
print(d / 'recipe.json', r['grid']['bounds'], 'units', r['joint']['units']['win'])
