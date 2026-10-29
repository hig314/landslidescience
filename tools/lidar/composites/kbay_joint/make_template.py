#!/opt/anaconda3/bin/python3
"""The whole-bay point-first template, derived from the Grewingk box's recipe (composites/joint_v3/recipe.json) so
the two cannot drift: the same joint, fill, water and shore settings, with the source list of BAY_SOURCES.md.

    make_template.py            -> composites/kbay_joint/template.json   (then make_boxes.py)

Placeholders make_boxes.py fills per box: KBAY_BOX_ID (the box id), and the per-box paths under BOX_JOINT (the
fuse's out dir), BOX_WORK (the composite work dir, lidar_build/composite/<id>), BOX_SEEDS (vintage seeds).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TL = HERE.parents[1]
src = json.load(open(TL / 'composites' / 'joint_v3' / 'recipe.json'))

UNITS = '/Volumes/Powder/lidar_build/kbay_joint/units'
OBS = '/Volumes/Nunatak/lidar_build/kbay_joint/obs'
NUN = '/Volumes/Nunatak/lidar_build'
POW = '/Volumes/Powder/lidar_build'
SEEDS = 'BOX_SEEDS'

t = dict(src)
t['id'] = 'KBAY_BOX_ID'; t['dataset_id'] = 'kbay_joint_KBAY_BOX_ID'
t['title'] = 'Kachemak Bay point-first topobathy, box KBAY_BOX_ID'
t['note'] = ('Whole-bay point-first build (2026-10-29, BAY_SOURCES.md): every lidar from its returns, the beds from the multibeam, '
             'BAGs and compilations, Chugachmiut as a cleaned DTM, 3DEP only where nothing saw the ground; the joint box recipe\'s '
             'water, shore and fill settings.')
t['grid'] = dict(src['grid'], bounds=[0, 0, 0, 0])

# ---------------------------------------------------------------- joint: units + sources
j = dict(src['joint'])
j['out'] = 'BOX_JOINT'
j['units'] = dict(out=UNITS, buf=30, workers=4, surveys=[
    dict(name='kbay2023', reader='copc', tiles='/Volumes/Powder/lidar_src/kbay_2023_laz/tiles', glob='*.laz',
         index=f'{NUN}/kbay_reclass_test/full/tileindex.json', drop=[6, 7, 17, 18],
         ground=dict(classes=[1, 2, 22], support=f'{NUN}/cog/kbay_2023_reclass_v2.tif', tol_m=0.25), water=[9], date='2023-08-01'),
    dict(name='gk2021', reader='las', tiles=f'{NUN}/kbay_reclass_test/grewingk_2021/laz', index=f'{NUN}/composite/grewingk/obs/nohf_full/index.csv',
         drop=[7, 18], ground=dict(classes=[2]), water=[9], shift_m=0.36, date='2021-10-12'),
    dict(name='gk2019', reader='las', tiles='/Volumes/Powder/lidar_src/grewingk_2019/dds4/grewingk_2019/laz', index='/Volumes/Powder/lidar_src/grewingk_2019/tiles.txt',
         drop=[7, 18], ground=dict(classes=[2]), water=[9], date='2019-06-03'),
    # NCMP topobathy lidar as OCM delivers it (metadata_ak2018_homer.xml, read 2026-10-29): 1 unclassified, 2 ground,
    # 23 submerged aquatic vegetation (kelp canopy: dropped, it is neither bed nor ground), 29 bathymetric point (the bed).
    # No water-surface class: open water is a no-return hole between the ground and the bed, for the hole rule.
    # Seldovia's COPC is in NAD83(2011) Alaska Albers (EPSG:6393); the Homer files are UTM 5N.
    dict(name='homer2018', reader='copc', tiles='/Volumes/Nunatak/lidar_src/homer_2018_ncmp', glob='*.copc.laz',
         drop=[7, 18, 23], ground=dict(classes=[2]), water=[9], bed=[29], date='2018-06-10'),
    dict(name='seldovia2019', reader='copc', tiles='/Volumes/Nunatak/lidar_src/seldovia_2019_ncmp', glob='*.copc.laz', srs='EPSG:6393',
         drop=[7, 18, 23], ground=dict(classes=[2]), water=[9], bed=[29], date='2019-07-17'),
    # Kenai 2008, our PTD reclassification in 2 km tiles named by their corner; the USGS classes carry no water
    dict(name='kenai2008', reader='las', tiles='/Volumes/Powder/lidar_build/kenai_2008/classified', glob='t_*.laz',
         index=dict(regex=r't_(\d+)_(\d+)', size_m=2000), drop=[7, 18], ground=dict(classes=[2]), water=[9], date='2008-06-01'),
    dict(name='homer2019', reader='las', tiles='/Volumes/Nunatak/lidar_build/homer_2019_pc/laz/dds4/homer_2019/laz', glob='pt*.laz',
         drop=[7, 18], ground=dict(classes=[2]), water=[9], date='2019-06-15'),
])
UDIR = f'{UNITS}/units'                                  # joint_pilot.py writes <out>/units/u_<x>_<y>/<name>.npz
lid = lambda n, **k: dict(name=n, kind='lidar', src=UDIR, unit_file=f'{n}.npz', **k)
j['sources'] = [
    lid('kbay2023', date='2023-08-01', sigma_m=0.05, reference=True),
    lid('gk2021', date='2021-10-12', sigma_m=0.08),
    lid('gk2019', date='2019-06-03', sigma_m=0.08),
    lid('homer2018', date='2018-06-10', sigma_m=0.08),
    dict(name='homer2018bed', kind='lidar', cls='bed', src=UDIR, unit_file='homer2018_bed.npz', date='2018-06-10', sigma_m=0.3),
    lid('seldovia2019', date='2019-07-17', sigma_m=0.08),
    dict(name='seldovia2019bed', kind='lidar', cls='bed', src=UDIR, unit_file='seldovia2019_bed.npz', date='2019-07-17', sigma_m=0.3),
    lid('kenai2008', date='2008-06-01', sigma_m=0.15),
    lid('homer2019', date='2019-06-15', sigma_m=0.08),
    # a DTM with no cloud: one observation per cell, calibrated to the measured lidar by the derived field, never rejecting one
    dict(name='chugachmiut', kind='grid', cls='ground', derived=True, src=f'{OBS}/chugachmiut_clean.tif', date='2018-07-01', sigma_m=0.3),
]
beds = {s['name']: s for s in src['joint']['sources'] if s.get('kind') == 'grid'}
for n in ('sonar2023', 'bathy2009', 'h12146', 'ocm2023'):
    j['sources'].append(dict(beds[n]))
j['sources'].append(dict(name='cudem2022', derived=True, kind='grid', cls='bed', src=f'{NUN}/bathy_src/outer_kachemak/cmp_cudem/cand_4m.tif',
                         date='2022-07-01', sigma_m=1.0, valid_below_m=-1.38))
for n in ('ncei2010', 'cgoa', 'sfm2023bare', 'dixon2023'):
    j['sources'].append(dict(beds[n]))
# vintage zones from the v7 bay product's seeds (make_seeds.py), each taking the newest lidar that saw the cell
j['vintage'] = [
    dict(name='floodplain', allow='newest', floodplain=dict(chan=f'{SEEDS}/chan.tif', eta=f'{SEEDS}/eta.tif', hand_m=3.0, veg_m=1.0, dist_m=600.0)),
    dict(name='beach', allow='newest', beach=dict(sea=f'{SEEDS}/sea.tif', dilate_m=20.0, zmax_m=8.0)),
]
j['notes'] = 'BAY_SOURCES.md 2026-10-29. Lidar from the shared units cache (joint_pilot.py units --recipe), grids as observations.'
t['joint'] = j

# ---------------------------------------------------------------- stack layers: 3DEP, the Homer 2019 DTM where its points are missing, joint, fill
def repath(o):
    s = json.dumps(o)
    s = s.replace('/Volumes/Powder/lidar_build/joint_v3/', 'BOX_JOINT/')
    s = s.replace('/Volumes/Nunatak/lidar_build/composite/joint_v3/obs/water_3dep.tif', 'BOX_WORK/obs/water_3dep.tif')
    return json.loads(s)

by = {l['name']: l for l in src['layers']}
old = json.load(open(TL / 'composites' / 'kbay_2023' / 'template.json'))
oldby = {l['name']: l for l in old['layers']}
homer19 = dict(oldby['homer2019'], notes=oldby['homer2019'].get('notes', '') + ' In the bay build its points are a joint source where the tiles are complete; the DTM stands only where they are not.')
t['layers'] = [repath(by['ctx3dep']), homer19, repath(by['joint']), by['fill']]
t['water'] = dict(src['water'], max_px=10100)
t['notes_boxes'] = 'make_boxes.py: grid bounds, BOX_JOINT, BOX_WORK, BOX_SEEDS per box.'
json.dump(t, open(HERE / 'template.json', 'w'), indent=1); open(HERE / 'template.json', 'a').write('\n')
print('template.json:', len(j['sources']), 'joint sources,', len(j['units']['surveys']), 'unit surveys,', len(t['layers']), 'layers')
