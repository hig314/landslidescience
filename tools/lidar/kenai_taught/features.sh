#!/bin/bash
# Per-point features for the Woodard patch (7.06 M pts), for the taught ground filter prototype.
set -u; cd /Volumes/Nunatak/lidar_build/kenai_taught
W=/Volumes/Nunatak/lidar_build/kenai_test_woodard
say() { echo "[$(date '+%T')] $*"; }
# co-registration of the 2008 points to the corrected 2019 archive (ptshift.py: 2008 sits E -0.12 N -0.05 of 2019 -> translate +0.12, +0.05)
DX=0.123; DY=0.048
say "5 m minimum surface"
pdal pipeline --stdin <<JSON
{"pipeline":["$W/patch_6334.laz",
 {"type":"filters.transformation","matrix":"1 0 0 $DX 0 1 0 $DY 0 0 1 0 0 0 0 1"},
 {"type":"writers.gdal","resolution":5,"output_type":"min","radius":3.6,"window_size":6,"gdaldriver":"GTiff","data_type":"float","nodata":-9999,"filename":"min5.tif"}]}
JSON
say "features"
pdal pipeline --stdin <<JSON
{"pipeline":["$W/patch_6334.laz",
 {"type":"filters.transformation","matrix":"1 0 0 $DX 0 1 0 $DY 0 0 1 0 0 0 0 1"},
 {"type":"filters.hag_dem","raster":"$W/dtm_smrf_steep.tif","zero_ground":false},
 {"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_steep"},
 {"type":"filters.hag_dem","raster":"$W/dtm_smrf.tif","zero_ground":false},
 {"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_smrf"},
 {"type":"filters.hag_dem","raster":"min5.tif","zero_ground":false},
 {"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_min5"},
 {"type":"filters.covariancefeatures","knn":12,"threads":8,"feature_set":"Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality"},
 {"type":"filters.ferry","dimensions":"Linearity=>lin12,Planarity=>pla12,Scattering=>sca12,Verticality=>ver12,Omnivariance=>omn12,Anisotropy=>ani12,Eigenentropy=>eig12,EigenvalueSum=>esum12,SurfaceVariation=>sv12,DemantkeVerticality=>dver12"},
 {"type":"filters.covariancefeatures","knn":48,"threads":8,"feature_set":"Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality"},
 {"type":"filters.radialdensity","radius":2.0},
 {"type":"writers.text","filename":"features.csv","order":"X,Y,Z,Intensity,ReturnNumber,NumberOfReturns,Classification,hag_steep,hag_smrf,hag_min5,lin12,pla12,sca12,ver12,omn12,ani12,eig12,esum12,sv12,dver12,Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality,RadialDensity","keep_unspecified":false,"precision":3}]}
JSON
say "exit $?; $(du -h features.csv | cut -f1), $(wc -l < features.csv) lines"
say "done"
