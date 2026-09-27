#!/bin/bash
# Per-tile provisional grounds + per-point features for one 1 km block (id x y). Grid pinned to the block (+60 m buffer).
set -u; id=$1; x=$2; y=$3; cd /Volumes/Nunatak/lidar_build/kenai_taught/full; mkdir -p feat dtm
P=/opt/homebrew/bin/pdal; CELL=1.2192
B="([$((x-60)),$((x+1060))],[$((y-60)),$((y+1060))])"
GRID="\"resolution\":$CELL,\"bounds\":\"$B\",\"gdaldriver\":\"GTiff\",\"data_type\":\"float\",\"nodata\":-9999,\"gdalopts\":\"COMPRESS=ZSTD,PREDICTOR=3,TILED=YES\""
for v in steep std; do
  if [ $v = steep ]; then S=0.8; W=12.0; else S=0.2; W=18.0; fi
  nice -n 10 $P pipeline --stdin > feat/$id.smrf_$v.log 2>&1 <<JSON
{"pipeline":["tiles/$id.laz",{"type":"filters.assign","assignment":"Classification[:]=0"},
 {"type":"filters.smrf","cell":1.2,"slope":$S,"window":$W,"threshold":0.5,"scalar":1.25},
 {"type":"filters.range","limits":"Classification[2:2]"},
 {"type":"writers.gdal",$GRID,"output_type":"idw","radius":1.75,"window_size":4,"filename":"dtm/$id.smrf_$v.tif"}]}
JSON
done
nice -n 10 $P pipeline --stdin > feat/$id.min5.log 2>&1 <<JSON
{"pipeline":["tiles/$id.laz",{"type":"writers.gdal","resolution":5,"bounds":"$B","output_type":"min","radius":3.6,"window_size":6,"gdaldriver":"GTiff","data_type":"float","nodata":-9999,"filename":"dtm/$id.min5.tif"}]}
JSON
nice -n 10 $P pipeline --stdin > feat/$id.features.log 2>&1 <<JSON
{"pipeline":["tiles/$id.laz",
 {"type":"filters.hag_dem","raster":"dtm/$id.smrf_steep.tif","zero_ground":false},{"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_steep"},
 {"type":"filters.hag_dem","raster":"dtm/$id.smrf_std.tif","zero_ground":false},{"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_smrf"},
 {"type":"filters.hag_dem","raster":"dtm/$id.min5.tif","zero_ground":false},{"type":"filters.ferry","dimensions":"HeightAboveGround=>hag_min5"},
 {"type":"filters.covariancefeatures","knn":12,"threads":4,"feature_set":"Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality"},
 {"type":"filters.ferry","dimensions":"Linearity=>lin12,Planarity=>pla12,Scattering=>sca12,Verticality=>ver12,Omnivariance=>omn12,Anisotropy=>ani12,Eigenentropy=>eig12,EigenvalueSum=>esum12,SurfaceVariation=>sv12,DemantkeVerticality=>dver12"},
 {"type":"filters.covariancefeatures","knn":48,"threads":4,"feature_set":"Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality"},
 {"type":"filters.radialdensity","radius":2.0},
 {"type":"writers.text","filename":"feat/$id.csv","order":"X,Y,Z,Intensity,ReturnNumber,NumberOfReturns,Classification,hag_steep,hag_smrf,hag_min5,lin12,pla12,sca12,ver12,omn12,ani12,eig12,esum12,sv12,dver12,Linearity,Planarity,Scattering,Verticality,Omnivariance,Anisotropy,Eigenentropy,EigenvalueSum,SurfaceVariation,DemantkeVerticality,RadialDensity","keep_unspecified":false,"precision":3}]}
JSON
[ -s feat/$id.csv ] && echo "$id ok $(du -h feat/$id.csv | cut -f1)" || echo "$id FAILED"
