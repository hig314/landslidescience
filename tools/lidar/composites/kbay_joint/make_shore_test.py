#!/opt/anaconda3/bin/python3
"""Test crops for the contour shore model (shoreface.py, 2026-11-03): make_crop.py's recipe + ArcticDEM v2, the calibrated NOS casts,
shore_model contour.  make_shore_test.py <box> <crop> x0 y0 x1 y1"""
import json, sys, subprocess, pathlib
HERE = pathlib.Path(__file__).resolve().parent
box, cid = sys.argv[1:3]
subprocess.run([sys.executable, str(HERE / "make_crop.py"), box, cid, *sys.argv[3:7]], check=True)
p = HERE / "crops" / cid / "recipe.json"; s = p.read_text()
s = s.replace("obs/arcticdem_ground_2m.tif", "obs/arcticdem_ground_2m_v2.tif").replace("nos_1977_1984_20261101.csv", "nos_1977_1984_20261103.csv").replace("nos_1915_1931_20261101.csv", "nos_1915_1931_20261103.csv")
r = json.loads(s); f = r["fill"]
f.update(shore_model="contour", ground_water=f"/Volumes/Nunatak/lidar_build/composite/{cid}/obs/water_3dep.tif", shore_base_m=f["low_tide_m"] - 2.0,
         tide_lines=str(HERE.parent.parent / "water" / "tide_lines.geojson"))
p.write_text(json.dumps(r, indent=1)); print(cid, r["grid"]["bounds"], "ok")
