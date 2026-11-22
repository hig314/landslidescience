"""Check a match by eye: the video's median frame warped onto the reference
grid, beside / blended with the radar simulation and a hillshade, with the
RGI outlines would-be next. usage: overlay.py STACK.npz REF.npz MATCH.json KEY OUT.jpg"""
import sys, json, math
import numpy as np, cv2
st, ref, m, key, out = np.load(sys.argv[1]), np.load(sys.argv[2]), json.load(open(sys.argv[3])), sys.argv[4], sys.argv[5]
b = m[key]['bounds_3857']; T = ref['transform']; orbit = key.split('_')[1]
px = float(T[0])
x0 = (b[0] - T[2]) / px; x1 = (b[2] - T[2]) / px; y0 = (T[5] - b[3]) / px; y1 = (T[5] - b[1]) / px
pad = 0.25 * (x1 - x0)
X0, Y0, X1, Y1 = int(max(0, x0 - pad)), int(max(0, y0 - pad)), int(x1 + pad), int(y1 + pad)
sim = ref[orbit][Y0:Y1, X0:X1]
dem = np.nan_to_num(ref['dem'][Y0:Y1, X0:X1])
gy, gx = np.gradient(dem, float(ref['res']))
hs = np.clip(( -gx * math.sin(math.radians(315)) * 0.7 + gy * math.cos(math.radians(315)) * 0.7 + 1) / np.sqrt(gx**2 + gy**2 + 1), 0, 1)
v = st['med_rgb'][..., ::-1]
H, W = sim.shape
M = np.float32([[(x1 - x0) / v.shape[1], 0, x0 - X0], [0, (y1 - y0) / v.shape[0], y0 - Y0]])
vw = cv2.warpAffine(v, M, (W, H), flags=cv2.INTER_AREA, borderValue=(0, 0, 0))
inside = cv2.warpAffine(np.ones(v.shape[:2], np.uint8), M, (W, H)) > 0
s3 = cv2.cvtColor((sim * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
blend = s3.copy(); blend[inside] = (0.55 * vw[inside] + 0.45 * s3[inside]).astype(np.uint8)
# edges of the simulation drawn on the video
e = cv2.Canny(cv2.GaussianBlur((sim * 255).astype(np.uint8), (0, 0), 1.5), 40, 90) > 0
ed = vw.copy(); ed[e & inside] = (0, 255, 255)
cv2.rectangle(blend, (int(x0 - X0), int(y0 - Y0)), (int(x1 - X0), int(y1 - Y0)), (0, 255, 255), 2)
row = np.hstack([cv2.cvtColor((hs * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR), blend, ed])
sc = min(1.0, 2400 / row.shape[1])
cv2.imwrite(out, cv2.resize(row, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 88])
R = 6378137.0
lon = lambda x: math.degrees(x / R); lat = lambda y: math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
print('corners W %.4f S %.4f E %.4f N %.4f' % (lon(b[0]), lat(b[1]), lon(b[2]), lat(b[3])))
