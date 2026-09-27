import numpy as np, pyarrow as pa, pyarrow.feather as f
P = np.load('model/pts.npz'); prob = np.load('model/prob_cv.npy'); vend = np.load('model/feat.npz')['vendor_ground'] == 1
sets = {'m70': prob >= 0.70, 'm85': prob >= 0.85,
        'hyb': (vend & (prob >= 0.5)) | (prob >= 0.85)}     # keep vendor ground unless the model doubts it; add confident extras
for n, s in sets.items():
    t = pa.table({'X': P['x'][s], 'Y': P['y'][s], 'Z': P['z'][s], 'Classification': np.full(s.sum(), 2, np.uint8)})
    f.write_feather(t, f'model/ground_{n}.feather'); print(n, s.sum())
