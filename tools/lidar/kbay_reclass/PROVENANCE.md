# KBay 2023/24 reclassification — repository copy

A copy of the code, running log and trained models for the reclassification
of the NOAA 2023/24 Kachemak Bay point cloud, calibrated on Grewingk 2021.
Brought into the repo 2026-09-27 because it existed only on one external drive.

- **Live working directory:** `/Volumes/Nunatak/lidar_build/kbay_reclass_test/`.
  Scripts use paths relative to it (`full/`, `site/`, `model/saved/`,
  `grewingk_2021/`) and read the COPC tiles from
  `/Volumes/Powder/lidar_src/kbay_2023_laz/tiles/`. Run them from there, not
  from here.
- **`README.md`** is the running log of the method, newest sections last.
  **`report.txt`** is the scoring output behind it.
- **`model/saved/*.joblib`** are the trained ground, zone and blend models
  the full run applies (`model/apply.py`, `model/fullrun.py`). Retraining
  needs the training caches in the working directory, which are not here.
- Data, point clouds and outputs are **not** in the repo.

Re-sync after changing anything in the working directory:

    rsync -a --include='*/' --include='*.py' --include='*.joblib' --exclude='*' \
      /Volumes/Nunatak/lidar_build/kbay_reclass_test/model/ tools/lidar/kbay_reclass/model/
    cp -p /Volumes/Nunatak/lidar_build/kbay_reclass_test/{README.md,report.txt,score.py} tools/lidar/kbay_reclass/
