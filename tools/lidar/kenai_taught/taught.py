"""Taught ground filter, Woodard prototype.
1. Label every 2008 return against the co-registered Homer 2019 surface:
   ground if |dz| <= tol(slope); non-ground if dz > 0.5 + tol; below -tol -> unlabelled.
2. HistGradientBoosting on PDAL features, 5-fold CV by 200 m blocks -> out-of-fold predictions for every point.
3. Ground points (OOF) -> IDW DTM on the test-patch grid -> crest metric via crest2/3 (run separately).
"""
import numpy as np, pandas as pd, rasterio, json, subprocess, sys, time
from scipy import ndimage as ndi
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import precision_recall_fscore_support
t0=time.time()
W="/Volumes/Nunatak/lidar_build/kenai_test_woodard"; REF="/Volumes/Nunatak/lidar_build/cog/homer_2019.tif"
df=pd.read_csv("features.csv", dtype=np.float32); print(f"{len(df):,} points, {df.shape[1]} columns, {time.time()-t0:.0f}s")
# --- teacher surface and slope at each point
with rasterio.open(REF) as r:
    win=rasterio.windows.from_bounds(df.X.min()-10,df.Y.min()-10,df.X.max()+10,df.Y.max()+10,r.transform).round_offsets().round_lengths()
    ref=r.read(1,window=win).astype(np.float32); tr=r.window_transform(win); ref[ref==r.nodata]=np.nan; res=tr.a
sm=ndi.gaussian_filter(np.nan_to_num(ref,nan=np.nanmedian(ref)),1.0); gy,gx=np.gradient(sm,res); slope=np.degrees(np.arctan(np.hypot(gx,gy)))
col=(df.X.values-tr.c)/res-0.5; row=(tr.f-df.Y.values)/res-0.5
zt=ndi.map_coordinates(ref,[row,col],order=1,mode='constant',cval=np.nan); st=ndi.map_coordinates(slope,[row,col],order=1,mode='nearest')
dz=df.Z.values-zt
RESID=0.15   # residual horizontal uncertainty after co-registration, m
tol=0.15+RESID*np.tan(np.radians(np.clip(st,0,60)))
y=np.full(len(df),-1,np.int8); y[np.abs(dz)<=tol]=1; y[dz>0.5+tol]=0
y[~np.isfinite(dz)]=-1
print(f"labels: ground {np.mean(y==1)*100:.1f}%  non-ground {np.mean(y==0)*100:.1f}%  unlabelled {np.mean(y==-1)*100:.1f}% (below -tol {np.mean(dz<-tol)*100:.1f}%)")
vend=df.Classification.values==2
print(f"vendor class 2 vs labels (labelled pts only): precision {np.mean(y[vend&(y>=0)]==1)*100:.1f}%  recall {np.mean(vend[y==1])*100:.1f}%")
# --- features
drop=["X","Y","Z","Classification"]; feats=[c for c in df.columns if c not in drop]
X=df[feats].values; X[~np.isfinite(X)]=np.nan
# --- 5-fold spatial CV by 200 m blocks
blk=(np.floor(df.X.values/200).astype(int)*100003+np.floor(df.Y.values/200).astype(int)); ub=np.unique(blk); rng=np.random.default_rng(3); rng.shuffle(ub)
fold=np.zeros(len(df),int); 
for k,b in enumerate(np.array_split(ub,5)): fold[np.isin(blk,b)]=k
lab=y>=0; p=np.full(len(df),np.nan,np.float32); imp=None
for k in range(5):
    tr_=lab&(fold!=k); te=fold==k
    # subsample training to <=1.5 M for speed, balanced
    idx=np.flatnonzero(tr_); 
    if len(idx)>1_500_000: idx=rng.choice(idx,1_500_000,replace=False)
    w=np.where(y[idx]==1,1.0,np.mean(y[idx]==1)/max(np.mean(y[idx]==0),1e-6))   # balance classes
    clf=HistGradientBoostingClassifier(max_iter=300,learning_rate=0.1,max_leaf_nodes=63,min_samples_leaf=50,l2_regularization=1.0,random_state=0)
    clf.fit(X[idx],y[idx],sample_weight=w); p[te]=clf.predict_proba(X[te])[:,1]
    pr,rc,f1,_=precision_recall_fscore_support(y[te&lab],(p[te&lab]>0.5).astype(int),average=None,labels=[0,1])
    print(f"fold {k}: train {len(idx):,} test {te.sum():,}  ground P/R {pr[1]*100:.1f}/{rc[1]*100:.1f}  non-ground P/R {pr[0]*100:.1f}/{rc[0]*100:.1f}  ({time.time()-t0:.0f}s)")
pred=p>0.5
print(f"OOF: ground P/R {np.mean(y[pred&lab]==1)*100:.1f}/{np.mean(pred[y==1])*100:.1f};  predicted ground {pred.mean()*100:.1f}% of points (vendor {vend.mean()*100:.1f}%)")
# feature importance by permutation on a small OOF sample (last model as proxy)
from sklearn.inspection import permutation_importance
sub=rng.choice(np.flatnonzero(lab&(fold==4)),100_000,replace=False)
pi=permutation_importance(clf,X[sub],y[sub],n_repeats=3,random_state=0,n_jobs=4)
order=np.argsort(-pi.importances_mean)[:12]; print("top features:",", ".join(f"{feats[i]} {pi.importances_mean[i]:.3f}" for i in order))
np.save("oof_prob.npy",p); np.save("labels.npy",y); np.save("dz.npy",dz.astype(np.float32))
# --- ground points -> DTM on the test grid (same recipe as the other four)
pd.DataFrame({"X":df.X.values[pred],"Y":df.Y.values[pred],"Z":df.Z.values[pred]}).to_csv("ground_taught.csv",index=False)
pipe={"pipeline":[{"type":"readers.text","filename":"ground_taught.csv"},
      {"type":"writers.gdal","resolution":1.2192,"bounds":"([580409.9892,582458.2452],[6613064.2052,6615108.8036])","output_type":"idw","radius":1.75,"window_size":4,"gdaldriver":"GTiff","data_type":"float","nodata":-9999,"gdalopts":"COMPRESS=ZSTD,PREDICTOR=3,TILED=YES","filename":f"{W}/dtm_taught.tif"}]}
subprocess.run(["pdal","pipeline","--stdin"],input=json.dumps(pipe),text=True,check=True)
print(f"wrote {W}/dtm_taught.tif ({time.time()-t0:.0f}s)")
