"""Taught ground filter over the Homer 2019 footprint.
Checkerboard split by 1 km block: (bx + by) even -> TRAIN, odd -> EVAL. Labels against the 2019 archive
(asymmetric rule, slope-dependent tolerance), site-level exclusions (vetoes.json from the review page,
built-up cap), balanced subsample per training tile, HistGradientBoosting, then every EVAL tile is
predicted in full, gridded to a DTM (same recipe as the vendor grid) and scored at the crests.
Usage: train_full.py [--built-cap 0.1] [--vetoes vetoes.json] [--tag run1]
"""
import json, os, sys, time, glob, subprocess, argparse, numpy as np, pandas as pd, rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_origin
from scipy import ndimage as ndi
from sklearn.ensemble import HistGradientBoostingClassifier
ap=argparse.ArgumentParser(); ap.add_argument("--built-cap",type=float,default=0.10); ap.add_argument("--vetoes",default="vetoes.json"); ap.add_argument("--tag",default="run1")
ap.add_argument("--per-tile",type=int,default=120_000); ap.add_argument("--tol0",type=float,default=0.15); ap.add_argument("--resid",type=float,default=0.15)
A=ap.parse_args(); t0=time.time()
REF="/Volumes/Nunatak/lidar_build/cog/homer_2019.tif"; CELL=1.2192
blocks=json.load(open("blocks.json")); sites=json.load(open("/Volumes/Nunatak/lidar_build/kenai_teacher/map/sites_map.geojson"))["features"]
site_props={f["properties"]["sid"]:f["properties"] for f in sites}
vetoed=set(); promoted=set()
if os.path.exists(A.vetoes):
    for r in json.load(open(A.vetoes)): (vetoed if r.get("state")=="veto" else promoted if r.get("state")=="promote" else set()).add(r["sid"])
print(f"{len(sites)} sites, {len(vetoed)} vetoed, {len(promoted)} promoted")
# site lookup grid: sid = "{i}_{j}" with x = x0 + i*100, y = y1 - j*100 (from make_sites)
xs=[p["x"] for p in site_props.values()]; ys=[p["y"] for p in site_props.values()]
i0=min(int(p["sid"].split("_")[0]) for p in site_props.values()); x0=min(xs)-(min(int(p["sid"].split("_")[0]) for p in site_props.values()))*100
j0=min(int(p["sid"].split("_")[1]) for p in site_props.values()); y1=max(ys)+j0*100
def sid_at(x,y): return f"{int((x-x0)//100)}_{int((y1-y)//100)}"
# built-up cap: keep a random 'cap' share of built sites for training
rng=np.random.default_rng(5); built=[s for s,p in site_props.items() if p["built"]]; keep_built=set(rng.choice(built,int(len(built)*A.built_cap),replace=False)) if built else set()
def site_ok(sid):
    p=site_props.get(sid)
    if p is None or sid in vetoed: return False
    if sid in promoted: return True
    if not p["teacher_ok"]: return False
    if p["built"] and sid not in keep_built: return False
    return True
# --- reference (whole footprint at 1 m would be 2 GB; read per tile instead)
ref_ds=rasterio.open(REF)
def ref_tile(x,y):
    win=rasterio.windows.from_bounds(x-70,y-70,x+1070,y+1070,ref_ds.transform).round_offsets().round_lengths()
    z=ref_ds.read(1,window=win).astype(np.float32); tr=ref_ds.window_transform(win); z[z==ref_ds.nodata]=np.nan; return z,tr
def label_tile(df,x,y):
    z,tr=ref_tile(x,y); res=tr.a
    sm=ndi.gaussian_filter(np.nan_to_num(z,nan=np.nanmedian(z)),1.0); gy,gx=np.gradient(sm,res); slope=np.degrees(np.arctan(np.hypot(gx,gy)))
    col=(df.X.values-tr.c)/res-0.5; row=(tr.f-df.Y.values)/res-0.5
    zt=ndi.map_coordinates(z,[row,col],order=1,mode='constant',cval=np.nan); st=ndi.map_coordinates(slope,[row,col],order=1,mode='nearest')
    dz=df.Z.values-zt; tol=A.tol0+A.resid*np.tan(np.radians(np.clip(st,0,60)))
    lab=np.full(len(df),-1,np.int8); lab[np.abs(dz)<=tol]=1; lab[dz>0.5+tol]=0; lab[~np.isfinite(dz)]=-1
    return lab,dz.astype(np.float32),st.astype(np.float32)
FEATS=None
def load(id_):
    df=pd.read_csv(f"feat/{id_}.csv",dtype=np.float32); return df
train_ids=[b for b in blocks if (b["x"]//1000+b["y"]//1000)%2==0 and os.path.exists(f"feat/{b['id']}.csv")]
eval_ids=[b for b in blocks if (b["x"]//1000+b["y"]//1000)%2==1 and os.path.exists(f"feat/{b['id']}.csv")]
print(f"train tiles {len(train_ids)}, eval tiles {len(eval_ids)}")
# --- training sample
Xs=[];ys=[]; nsite_used=set()
for b in train_ids:
    df=load(b["id"]); lab,dz,st=label_tile(df,b["x"],b["y"])
    sids=np.array([sid_at(x,y) for x,y in zip(df.X.values,df.Y.values)])
    okm=np.array([site_ok(s) for s in sids]); nsite_used|=set(sids[okm])
    m=(lab>=0)&okm; idx=np.flatnonzero(m)
    if len(idx)==0: continue
    # balanced: half ground, half non-ground where possible
    g=idx[lab[idx]==1]; n=idx[lab[idx]==0]; k=A.per_tile//2
    pick=np.concatenate([rng.choice(g,min(k,len(g)),replace=False),rng.choice(n,min(k,len(n)),replace=False)])
    if FEATS is None: FEATS=[c for c in df.columns if c not in ("X","Y","Z","Classification")]
    Xs.append(df[FEATS].values[pick]); ys.append(lab[pick]); print(f"  {b['id']}: {len(pick):,} sampled (ground {np.mean(lab[pick]==1)*100:.0f}%), sites used {okm.mean()*100:.0f}% of points",flush=True)
X=np.vstack(Xs); y=np.concatenate(ys); X[~np.isfinite(X)]=np.nan
print(f"training set {len(y):,} points from {len(nsite_used)} sites; ground {np.mean(y==1)*100:.1f}%  ({time.time()-t0:.0f}s)")
clf=HistGradientBoostingClassifier(max_iter=400,learning_rate=0.08,max_leaf_nodes=63,min_samples_leaf=100,l2_regularization=1.0,random_state=0)
clf.fit(X,y); print(f"trained ({time.time()-t0:.0f}s)")
import joblib; joblib.dump({"clf":clf,"feats":FEATS},f"model_{A.tag}.joblib")
# --- evaluate: predict every eval tile, DTM per tile, crest metrics pooled
os.makedirs(f"eval_{A.tag}",exist_ok=True)
def crest_stats(dtm_path,x,y,acc):
    z,tr=ref_tile(x,y); res=tr.a
    # resample the 1.2 m DTM onto the 0.5 m reference window (bilinear), then metrics on the reference's crest mask
    t=np.full(z.shape,np.nan,np.float32)
    with rasterio.open(dtm_path) as s: reproject(rasterio.band(s,1),t,dst_transform=tr,dst_crs=ref_ds.crs,resampling=Resampling.bilinear,dst_nodata=np.nan,src_nodata=s.nodata)
    sm=ndi.gaussian_filter(np.nan_to_num(z,nan=np.nanmedian(z)),2.0); gy,gx=np.gradient(sm,res); slope=np.degrees(np.arctan(np.hypot(gx,gy))); lap=ndi.laplace(sm)/res**2
    # restrict to the tile proper and to sites that are not vetoed (real change) 
    dz=t-z; dz-=np.nanmedian(dz)
    for name,mk in (("sharp_crest",(lap<-0.15)&(slope>20)),("crest",(lap<-0.05)&(slope>15)),("steep_noncrest",(np.abs(lap)<0.05)&(slope>25)),("all",np.isfinite(z))):
        v=dz[mk&np.isfinite(dz)]; acc.setdefault(name,[]).append(v[np.isfinite(v)])
def summarize(acc):
    out={}
    for k,vs in acc.items():
        v=np.concatenate(vs); out[k]={"n":int(len(v)),"median":float(np.median(v)),"mad":float(np.median(np.abs(v-np.median(v)))),"lt_-1":float(np.mean(v<-1)*100),"lt_-0.5":float(np.mean(v<-0.5)*100),"gt_0.5":float(np.mean(v>0.5)*100),"abs_gt_1":float(np.mean(np.abs(v)>1)*100)}
    return out
acc_t={};acc_v={};acc_s={}
for b in eval_ids:
    df=load(b["id"]); Xe=df[FEATS].values; Xe[~np.isfinite(Xe)]=np.nan
    p=clf.predict_proba(Xe)[:,1]; pred=p>0.5
    np.save(f"eval_{A.tag}/{b['id']}_prob.npy",p.astype(np.float16))
    B=f"([{b['x']-60},{b['x']+1060}],[{b['y']-60},{b['y']+1060}])"
    grid={"type":"writers.gdal","resolution":CELL,"bounds":B,"output_type":"idw","radius":1.75,"window_size":4,"gdaldriver":"GTiff","data_type":"float","nodata":-9999,"gdalopts":"COMPRESS=ZSTD,PREDICTOR=3,TILED=YES"}
    pd.DataFrame({"X":df.X.values[pred],"Y":df.Y.values[pred],"Z":df.Z.values[pred]}).to_csv(f"eval_{A.tag}/{b['id']}_ground.csv",index=False)
    subprocess.run(["pdal","pipeline","--stdin"],input=json.dumps({"pipeline":[{"type":"readers.text","filename":f"eval_{A.tag}/{b['id']}_ground.csv"},dict(grid,filename=f"eval_{A.tag}/{b['id']}_taught.tif")]}),text=True,check=True,capture_output=True)
    vend=df.Classification.values==2
    pd.DataFrame({"X":df.X.values[vend],"Y":df.Y.values[vend],"Z":df.Z.values[vend]}).to_csv(f"eval_{A.tag}/{b['id']}_vendor.csv",index=False)
    subprocess.run(["pdal","pipeline","--stdin"],input=json.dumps({"pipeline":[{"type":"readers.text","filename":f"eval_{A.tag}/{b['id']}_vendor.csv"},dict(grid,filename=f"eval_{A.tag}/{b['id']}_vendor.tif")]}),text=True,check=True,capture_output=True)
    os.remove(f"eval_{A.tag}/{b['id']}_ground.csv"); os.remove(f"eval_{A.tag}/{b['id']}_vendor.csv")
    crest_stats(f"eval_{A.tag}/{b['id']}_taught.tif",b["x"],b["y"],acc_t); crest_stats(f"eval_{A.tag}/{b['id']}_vendor.tif",b["x"],b["y"],acc_v)
    if os.path.exists(f"dtm/{b['id']}.smrf_steep.tif"): crest_stats(f"dtm/{b['id']}.smrf_steep.tif",b["x"],b["y"],acc_s)
    print(f"  eval {b['id']}: predicted ground {pred.mean()*100:.0f}% (vendor {vend.mean()*100:.0f}%)  ({time.time()-t0:.0f}s)",flush=True)
res={"taught":summarize(acc_t),"vendor":summarize(acc_v),"smrf_steep":summarize(acc_s),"train_tiles":len(train_ids),"eval_tiles":len(eval_ids),"train_points":int(len(y)),"args":vars(A)}
json.dump(res,open(f"results_{A.tag}.json","w"),indent=1)
print(f"\n{'stratum':16s}{'surface':12s}{'median':>8s}{'MAD':>7s}{'<-1':>6s}{'<-0.5':>7s}{'>+0.5':>7s}{'|dz|>1':>7s}")
for k in ("sharp_crest","crest","steep_noncrest","all"):
    for s in ("vendor","smrf_steep","taught"):
        r=res[s].get(k); 
        if r: print(f"{k:16s}{s:12s}{r['median']:8.3f}{r['mad']:7.3f}{r['lt_-1']:5.1f}%{r['lt_-0.5']:6.1f}%{r['gt_0.5']:6.1f}%{r['abs_gt_1']:6.1f}%")
print(f"done ({time.time()-t0:.0f}s)")
