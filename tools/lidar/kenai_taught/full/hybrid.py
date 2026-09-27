"""Hybrid variants on the run1 held-out tiles, using the saved out-of-fold probabilities:
  A  prob > 0.3                                   (looser threshold)
  B  taught OR (near the SMRF-steep surface AND prob > 0.15)   (SMRF-steep crest retention, classifier vetoes its leakage)
  C  near the SMRF-steep surface AND prob > 0.3   (SMRF-steep filtered by the classifier)
Scored with the same crest strata as train_full.py."""
import json, os, numpy as np, pandas as pd, rasterio, subprocess, time
exec(open("train_full.py").read().split("# --- training sample")[0].split("ap=argparse")[0])   # imports + constants
import argparse
A=argparse.Namespace(tag="run1")
blocks=json.load(open("blocks.json")); CELL=1.2192; REF="/Volumes/Nunatak/lidar_build/cog/homer_2019.tif"; ref_ds=rasterio.open(REF)
from scipy import ndimage as ndi
from rasterio.warp import reproject, Resampling
def ref_tile(x,y):
    win=rasterio.windows.from_bounds(x-70,y-70,x+1070,y+1070,ref_ds.transform).round_offsets().round_lengths()
    z=ref_ds.read(1,window=win).astype(np.float32); tr=ref_ds.window_transform(win); z[z==ref_ds.nodata]=np.nan; return z,tr
def crest_stats(dtm_path,x,y,acc):
    z,tr=ref_tile(x,y); res=tr.a; t=np.full(z.shape,np.nan,np.float32)
    with rasterio.open(dtm_path) as s: reproject(rasterio.band(s,1),t,dst_transform=tr,dst_crs=ref_ds.crs,resampling=Resampling.bilinear,dst_nodata=np.nan,src_nodata=s.nodata)
    sm=ndi.gaussian_filter(np.nan_to_num(z,nan=np.nanmedian(z)),2.0); gy,gx=np.gradient(sm,res); slope=np.degrees(np.arctan(np.hypot(gx,gy))); lap=ndi.laplace(sm)/res**2
    dz=t-z; dz-=np.nanmedian(dz)
    for name,mk in (("sharp_crest",(lap<-0.15)&(slope>20)),("crest",(lap<-0.05)&(slope>15)),("steep_noncrest",(np.abs(lap)<0.05)&(slope>25)),("all",np.isfinite(z))):
        v=dz[mk&np.isfinite(dz)]; acc.setdefault(name,[]).append(v[np.isfinite(v)])
def summarize(acc):
    out={}
    for k,vs in acc.items():
        v=np.concatenate(vs); out[k]={"median":float(np.median(v)),"mad":float(np.median(np.abs(v-np.median(v)))),"lt_-1":float(np.mean(v<-1)*100),"lt_-0.5":float(np.mean(v<-0.5)*100),"gt_0.5":float(np.mean(v>0.5)*100),"abs_gt_1":float(np.mean(np.abs(v)>1)*100)}
    return out
variants={"A_p30":lambda p,h: p>0.3, "B_union":lambda p,h: (p>0.5)|((np.abs(h)<0.15)&(p>0.15)), "C_steep_p30":lambda p,h: (np.abs(h)<0.15)&(p>0.3)}
eval_ids=[b for b in blocks if (b["x"]//1000+b["y"]//1000)%2==1 and os.path.exists(f"eval_run1/{b['id']}_prob.npy")]
acc={k:{} for k in variants}; t0=time.time(); os.makedirs("eval_hybrid",exist_ok=True)
for b in eval_ids:
    df=pd.read_csv(f"feat/{b['id']}.csv",usecols=["X","Y","Z","hag_steep"],dtype=np.float32); p=np.load(f"eval_run1/{b['id']}_prob.npy").astype(np.float32)
    B=f"([{b['x']-60},{b['x']+1060}],[{b['y']-60},{b['y']+1060}])"
    grid={"type":"writers.gdal","resolution":CELL,"bounds":B,"output_type":"idw","radius":1.75,"window_size":4,"gdaldriver":"GTiff","data_type":"float","nodata":-9999,"gdalopts":"COMPRESS=ZSTD,PREDICTOR=3,TILED=YES"}
    for k,f in variants.items():
        m=f(p,df.hag_steep.values); csv=f"eval_hybrid/{b['id']}_{k}.csv"; tif=f"eval_hybrid/{b['id']}_{k}.tif"
        pd.DataFrame({"X":df.X.values[m],"Y":df.Y.values[m],"Z":df.Z.values[m]}).to_csv(csv,index=False)
        subprocess.run(["pdal","pipeline","--stdin"],input=json.dumps({"pipeline":[{"type":"readers.text","filename":csv},dict(grid,filename=tif)]}),text=True,check=True,capture_output=True); os.remove(csv)
        crest_stats(tif,b["x"],b["y"],acc[k])
    print(f"  {b['id']} ({time.time()-t0:.0f}s)",flush=True)
res={k:summarize(v) for k,v in acc.items()}; json.dump(res,open("results_hybrid.json","w"),indent=1)
print(f"\n{'stratum':16s}{'variant':14s}{'median':>8s}{'MAD':>7s}{'<-1':>6s}{'<-0.5':>7s}{'>+0.5':>7s}{'|dz|>1':>7s}")
for s in ("sharp_crest","crest","steep_noncrest","all"):
    for k in variants:
        r=res[k][s]; print(f"{s:16s}{k:14s}{r['median']:8.3f}{r['mad']:7.3f}{r['lt_-1']:5.1f}%{r['lt_-0.5']:6.1f}%{r['gt_0.5']:6.1f}%{r['abs_gt_1']:6.1f}%")
print(f"done ({time.time()-t0:.0f}s)")
