"""Independent check of a match: RGI 7.0 outlines (a different source from
the DEM the match used) drawn on the video's median frame at the matched
placement. usage: rgi_check.py STACK.npz MATCH.json KEY RGI.json OUT.jpg"""
import sys, json
import numpy as np, cv2
st, m, key, rgi, out = np.load(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3], json.load(open(sys.argv[4])), sys.argv[5]
b = m[key]['bounds_3857']
v = np.ascontiguousarray(st['med_rgb'][..., ::-1])
h, w = v.shape[:2]
sx = w / (b[2] - b[0]); sy = h / (b[3] - b[1])
def ring(r):
    a = np.array(r)
    return np.stack([(a[:, 0] - b[0]) * sx, (b[3] - a[:, 1]) * sy], 1).round().astype(np.int32)
for gid, name, g in rgi:
    polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
    for p in polys:
        for r in p:
            cv2.polylines(v, [ring(r)], True, (0, 255, 255), 2, cv2.LINE_AA)
cv2.imwrite(out, cv2.resize(v, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])
