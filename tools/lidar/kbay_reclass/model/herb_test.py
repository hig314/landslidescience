"""Among points the stage-1 model accepts (p>=0.85), can KBay-only features tell true
ground (|dz|<=tol) from a herb layer (dz in 0.25..1.5 m above)? Spatial 3-fold CV AUC."""
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
P=np.load('model/pts.npz'); F=dict(np.load('model/feat.npz')); prob=np.load('model/prob_cv.npy')
x,y,dz,sl=P['x'],P['y'],P['dz'],P['slope']
tol=0.20+0.25*np.tan(np.radians(np.minimum(sl,70)))
acc=prob>=0.85
lab=np.where(np.abs(dz)<=tol,0,np.where((dz>0.25+tol*0.5)&(dz<1.5),1,-1))
names=list(F); X=np.column_stack([F[k] for k in names])
fold=(np.clip(((x-603016)//500).astype(int),0,2)+np.clip(((6606264-y)//500).astype(int),0,2))%3
print('accepted points: %d; herb-layer share among labelled: %.1f%%'%(acc.sum(),100*np.mean(lab[acc&(lab>=0)]==1)))
rng=np.random.default_rng(1); aucs=[]
for k in range(3):
    tr=np.flatnonzero(acc&(lab>=0)&(fold!=k)); tr=rng.choice(tr,min(2_000_000,len(tr)),replace=False)
    te=np.flatnonzero(acc&(lab>=0)&(fold==k))
    m=HistGradientBoostingClassifier(max_iter=300,max_leaf_nodes=63,l2_regularization=1.0).fit(X[tr],lab[tr])
    p=m.predict_proba(X[te])[:,1]; aucs.append(roc_auc_score(lab[te],p))
    # precision if we drop the top-scored 20% as herb
    thr=np.quantile(p,0.8); dr=p>=thr
    print('fold %d AUC %.3f; dropping top 20%%: %.0f%% of dropped are herb, removes %.0f%% of herb points'%(k,aucs[-1],100*lab[te][dr].mean(),100*lab[te][dr].sum()/lab[te].sum()))
from sklearn.inspection import permutation_importance
imp=permutation_importance(m,X[te][:200000],lab[te][:200000],n_repeats=3,random_state=0,scoring='roc_auc')
for i in np.argsort(-imp.importances_mean)[:6]: print('  %-14s %.3f'%(names[i],imp.importances_mean[i]))
