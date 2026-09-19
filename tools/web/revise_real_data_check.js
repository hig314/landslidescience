// Feed the REAL API response through revise.js's own loader logic, then hand
// the result to the REAL terra-draw. This is the end-to-end path the browser
// takes, minus the map.
var window = {}, document = { addEventListener:function(){} };
function SP(v){ if (v && typeof v.then==='function') return v;
  return { then:function(f){ try { return SP(f(v)); } catch(e){ return SF(e); } },
           catch:function(){ return this; } }; }
function SF(e){ return { then:function(){return this;},
                         catch:function(f){ return SP(f(e)); } }; }
var Promise = { resolve:SP, reject:SF };
var tdsrc = $.NSString.stringWithContentsOfFileEncodingError('/tmp/td.js',4,null).js;
eval(tdsrc);
var TD = window.terraDraw || this.terraDraw;
var real = JSON.parse($.NSString.stringWithContentsOfFileEncodingError('/tmp/real_polys.json',4,null).js);

function Adapter(){ this.register=function(){}; this.unregister=function(){};
  this.render=function(){}; this.getMapEventElement=function(){
    return { addEventListener:function(){}, removeEventListener:function(){} }; };
  this.setDraggability=function(){}; this.setCursor=function(){};
  this.setDoubleClickToZoom=function(){}; this.project=function(){return{x:0,y:0};};
  this.unproject=function(){return{lng:0,lat:0};};
  this.getLngLatFromEvent=function(){return{lng:0,lat:0};};
  this.getCoordinatePrecision=function(){return 9;}; this.setMapDraggability=function(){}; }

var fetch = function(){ return SP({ status:200, redirected:false,
  headers:{get:function(){return 'application/json';}},
  json:function(){ return SP({ ok:true, unique_name:'Real', polygons: real }); } }); };
var rv = $.NSString.stringWithContentsOfFileEncodingError(
  '/Users/Hig/Claude_projects/landslidescience/inventory/static/inventory/js/revise.js',4,null).js;
eval(rv);
var R = window.LSRevise;
var out = [];
R.start({ id: 1256, map:{ doubleClickZoom:{disable:function(){},enable:function(){}} },
          csrf:function(){return 'x';}, terraDraw:TD, adapter:{ TerraDrawMapLibreGLAdapter: Adapter } })
 .then(function(i){ out.push('OK: loaded ' + i.count + ' polygons' +
        (i.skipped && i.skipped.length ? ', skipped ' + i.skipped.join('; ') : ', none skipped')); })
 .catch(function(e){ out.push('FAILED: ' + e.message); });
out.join('\n');
