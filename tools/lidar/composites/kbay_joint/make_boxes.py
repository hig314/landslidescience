#!/opt/anaconda3/bin/python3
"""Cut the bay into boxes and write one recipe per box from template.json (make_template.py first).

Extent x 540-635, y 6540-6636 km (Hig, 2026-10-29: west and south far enough to take the tip of the Kenai Peninsula -- Cape Elizabeth x 560 y 6558, the Chugach Islands to y 6548 -- a logical map boundary that nests in coarse model grids; the v7 layout plus one column west and two rows south,
Nanwalek and Port Graham). Column and row edges as v4-v7 (between the mapped lakes), each box BUILT with a 2 km
margin and cropped back on the mosaic. Per box: grid bounds, the fuse's out dir (Powder, BOX_JOINT), the composite
work dir (BOX_WORK), the vintage seeds (BOX_SEEDS), and the joint units window = the build bounds (km-aligned).
Writes boxes/<id>/recipe.json and boxes.json.
"""
import json, pathlib
HERE = pathlib.Path(__file__).resolve().parent
MARGIN_M = 2000
EX = [540000, 557000, 572500, 587500, 603500, 619000, 635000]
EY = [6540000, 6549000, 6558000, 6566000, 6579500, 6591500, 6604000, 6620000, 6636000]
JOINT_ROOT = '/Volumes/Powder/lidar_build/kbay_joint/boxes'
WORK_ROOT = '/Volumes/Nunatak/lidar_build/composite'
SEED_ROOT = '/Volumes/Nunatak/lidar_build/kbay_joint/seeds'
tpl = open(HERE / 'template.json').read()
out = []
for r, (y, y1) in enumerate(zip(EY[:-1], EY[1:])):
    for c, (x, x1) in enumerate(zip(EX[:-1], EX[1:])):
        bid = f'kj_r{r}c{c}'
        s = (tpl.replace('KBAY_BOX_ID', bid).replace('BOX_JOINT', f'{JOINT_ROOT}/{bid}')
                .replace('BOX_WORK', f'{WORK_ROOT}/{bid}').replace('BOX_SEEDS', f'{SEED_ROOT}/{bid}'))
        rec = json.loads(s)
        build = [x - MARGIN_M, y - MARGIN_M, x1 + MARGIN_M, y1 + MARGIN_M]
        rec['grid']['bounds'] = build
        rec['joint']['units']['win'] = [build[0] // 1000 * 1000, build[1] // 1000 * 1000, -(-build[2] // 1000) * 1000, -(-build[3] // 1000) * 1000]
        d = HERE / 'boxes' / bid; d.mkdir(parents=True, exist_ok=True)
        json.dump(rec, open(d / 'recipe.json', 'w'), indent=1); open(d / 'recipe.json', 'a').write('\n')
        out.append(dict(id=bid, crop=[x, y, x1, y1], build=build, units_win=rec['joint']['units']['win']))
json.dump(out, open(HERE / 'boxes.json', 'w'), indent=1)
print(f'{len(out)} boxes:', ', '.join(f"{b['id']} {b['crop']}" for b in out))
