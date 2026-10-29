#!/opt/anaconda3/bin/python3
"""3DEP's hydro-flattened lakes as mapped water polygons (a PRIOR for the water stage, not a trusted edge).

    make_3dep_lakes.py /Volumes/Nunatak/lidar_build/kbay_joint/obs/water_3dep_bay10.tif [--min-m2 2000] [--out mapped_3dep_lakes.geojson]

flat_water.py's code 1 patches (constant to the centimetre, above 0.5 m) over the whole extent, vectorised, with the
3DEP level and area. Listed in sources.json BEFORE hig_lakes (which replaces them where Hig mapped the same water),
with no `edge`, so the water stage runs its detector pass inside them and takes the kind as a prior while the shore
stage gives them no shoreline cost (2026-10-29, Hig: the 3DEP lakes are a useful prior where only the Chugachmiut
DTM, which is not hydro-flattened, covers the ground).
"""
import argparse, json
from pathlib import Path
import numpy as np, rasterio
from rasterio import features
from rasterio.warp import transform_geom
from shapely.geometry import shape, mapping
HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('raster'); ap.add_argument('--min-m2', type=float, default=2000.0)
    ap.add_argument('--out', default=str(HERE / 'mapped_3dep_lakes.geojson'))
    a = ap.parse_args()
    with rasterio.open(a.raster) as s:
        m = s.read(1); T = s.transform; crs = s.crs; res = s.res[0]
        side = Path(a.raster).with_suffix('.json')
    lev = {}
    if side.exists():
        for r in json.load(open(side)).get('lakes', []): lev[(r['x'], r['y'])] = r['level_m']
    lake = (m == 1).astype(np.uint8)
    feats = []
    for geom, v in features.shapes(lake, mask=lake > 0, transform=T, connectivity=8):
        g = shape(geom); area = g.area
        if area < a.min_m2: continue
        c = g.representative_point()
        # the level: the sidecar's nearest patch centre, else none
        best = min(lev.items(), key=lambda kv: (kv[0][0] - c.x) ** 2 + (kv[0][1] - c.y) ** 2, default=(None, None))
        level = best[1] if best[0] and ((best[0][0] - c.x) ** 2 + (best[0][1] - c.y) ** 2) ** 0.5 < max(200.0, area ** 0.5) else None
        feats.append(dict(type='Feature', properties=dict(source='3DEP flat water', Type='Lake', area_m2=int(area), level_m=level, res_m=res),
                          geometry=transform_geom(crs, 'EPSG:4326', mapping(g.simplify(res / 2)))))
    fc = dict(type='FeatureCollection', name='mapped_3dep_lakes', features=feats)
    Path(a.out).write_text(json.dumps(fc, separators=(',', ':')))
    print(f'{len(feats)} 3DEP lake polygons >= {a.min_m2:.0f} m2 -> {a.out}')


if __name__ == '__main__':
    main()
