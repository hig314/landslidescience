# Kenai 2008 taught ground filter — repository copy

A copy of the code and first trained model for the taught ground filter on
the USGS Kenai 2008 point cloud (plan: `tools/lidar/KENAI_2008_PLAN.md`).
This is the experimental follow-on. The published `kenai_2008` surface comes
from the progressive-TIN reclassification driven by `tools/lidar/kenai_ptd_run.py`,
whose classified point cloud lives at
`/Volumes/Powder/lidar_build/kenai_2008/classified/`.
Brought into the repo 2026-09-27 because it existed only on one external drive.

- **Live working directory:** `/Volumes/Nunatak/lidar_build/kenai_taught/`
  (`full/` for the full-footprint run). Scripts use paths relative to it.
- **`full/model_run1.joblib`** is the first trained model. The 54 GB feature
  cache it was trained on is not here; `features.sh` / `full/features_tile.sh`
  rebuild it from the EPT mirror.

Re-sync after changing anything in the working directory:

    cp -p /Volumes/Nunatak/lidar_build/kenai_taught/{taught.py,features.sh} tools/lidar/kenai_taught/
    cp -p /Volumes/Nunatak/lidar_build/kenai_taught/full/{*.py,*.sh,*.joblib} tools/lidar/kenai_taught/full/
