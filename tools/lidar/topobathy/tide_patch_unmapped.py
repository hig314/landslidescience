"""Patch the as-built tide products with the closing-edge rule only (no full rebuild: a rebuild drifts elsewhere).
   OLD = water as built (repo), NEW = a dev full build with the rule; writes OUT/tide_low_water.geojson and OUT/tide_lines.geojson + tide_vector_lines.geojson."""
import json, sys, shapely, numpy as np
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
OLD, NEW, OUT = sys.argv[1:4]
J = lambda d, f: json.load(open(f"{d}/{f}.geojson"))
fc = lambda F: dict(type="FeatureCollection", crs=dict(type="name", properties=dict(name="EPSG:6334")), features=F)
lo = shape(J(OLD, "tide_low_water")["features"][0]["geometry"]); ln = shape(J(NEW, "tide_low_water")["features"][0]["geometry"])
hi = shape(J(OLD, "tide_high_water")["features"][0]["geometry"])
add = ln.difference(lo); add = unary_union([g for g in getattr(add, "geoms", [add]) if g.geom_type == "Polygon" and g.area >= 100])
lp = lo.union(add)
# the seam: the source low water within 2 m of the high polygon's closing edges (tide_vectors.build's inset left a strip there)
sys.path.insert(0, "/Volumes/Nunatak/lidar_build/lidar_dev/topobathy"); import tide_vectors as tv
Gl, Tl = tv._polys(tv.LOW); low0 = unary_union([g for g, t in zip(Gl, Tl) if t == "Shore"]).difference(unary_union([g for g, t in zip(Gl, Tl) if t == "Island"]))
pools = [g for g, t in zip(Gl, Tl) if t == "Pool"]; low0 = tv._poly(low0.union(unary_union(pools)) if pools else low0)
le_ = unary_union([shapely.LineString(V[i:i + 2]) for r, _, _ in tv._rings(hi) for V in [shapely.get_coordinates(r)] for i in np.flatnonzero(np.hypot(*np.diff(V, axis=0).T) > tv.LONG_EDGE_M)])
seam = low0.intersection(le_.buffer(2.0 + tv.LOW_OFF_HIGH_M + 1.0, quad_segs=1)).difference(lp)
lp = tv._poly(shapely.make_valid(lp.union(seam)))
lp = unary_union([g for g in getattr(lp, "geoms", [lp]) if g.geom_type == "Polygon"])
print(f"low water +{add.area/1e4:.1f} ha past closing edges, +{seam.area/1e4:.2f} ha of seam along {le_.length/1e3:.0f} km of closing edges")
json.dump(fc([dict(type="Feature", properties={}, geometry=mapping(shapely.simplify(lp, 0.5)))]), open(f"{OUT}/tide_low_water.geojson", "w"))
# new low lines: the dev build's vector_low lines lying past the high polygon (which the old build dropped as a crossing)
past = lp.difference(hi.buffer(10.0, quad_segs=2)).buffer(10.0, quad_segs=2); shapely.prepare(past)
Vo = J(OLD, "tide_vector_lines")["features"]; Vn = J(NEW, "tide_vector_lines")["features"]
old_low = unary_union([shape(f["geometry"]) for f in Vo if f["properties"]["source"] == "vector_low"]).buffer(5.0, quad_segs=1)
newL = []
for f in Vn:
    if f["properties"]["source"] != "vector_low": continue
    g = shape(f["geometry"]); part = g.intersection(past).difference(old_low)
    for p in getattr(part, "geoms", [part]):
        if p.geom_type == "LineString" and p.length >= 40.0:
            newL.append(dict(type="Feature", properties=dict(f["properties"], patch="past the high polygon's closing edge (2026-11-09)"), geometry=dict(type="LineString", coordinates=np.round(shapely.get_coordinates(p), 2).tolist())))
print(f"new low lines: {len(newL)}, {sum(shape(f['geometry']).length for f in newL)/1e3:.1f} km, levels p10/50/90", np.percentile([f['properties']['level_m'] for f in newL], [10,50,90]).round(2) if newL else None)
def put(name, F):
    hv = [f for f in F if f["properties"]["source"] == "vector_high"]; rest = [f for f in F if f["properties"]["source"] != "vector_high"]
    json.dump(fc(rest + newL + hv), open(f"{OUT}/{name}.geojson", "w"))
put("tide_vector_lines", Vo); put("tide_lines", J(OLD, "tide_lines")["features"])
json.dump(fc(newL), open(f"{OUT}/tide_patch_unmapped_lines.geojson", "w"))
