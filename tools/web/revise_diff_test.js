var window = {}; var document = { addEventListener: function(){} };
// Synchronous thenable so the whole chain runs inline under JXA, which has no
// event loop to resolve real promises against.
function SP(v){
  // Adopt a returned thenable, the way a real promise does -- without this the
  // next .then() receives the wrapper instead of the value.
  if (v && typeof v.then === 'function') return v;
  return { then:function(f){ try { return SP(f(v)); } catch(e){ return SF(e); } },
           catch:function(){ return this; } };
}
function SF(e){ return { then:function(){ return this; },
                         catch:function(f){ return SP(f(e)); } }; }
var Promise = { resolve: SP, reject: SF };
var fetch = null;
var src = $.NSString.stringWithContentsOfFileEncodingError(
  '/Users/Hig/Claude_projects/landslidescience/inventory/static/inventory/js/revise.js', 4, null).js;
eval(src);
var R = window.LSRevise;

var poly = function (x) {
  return { type:'Polygon', coordinates:[[[x,0],[x+1,0],[x+1,1],[x,1],[x,0]]] }; };
// what the API actually returns: a single-part MultiPolygon
var mpoly = function (x) {
  return { type:'MultiPolygon', coordinates:[poly(x).coordinates] }; };
var snapshot = [];
var TD = { TerraDraw: function () {
    this.start=function(){}; this.stop=function(){}; this.setMode=function(){};
    this.addFeatures=function(){ return []; }; this.getSnapshot=function(){ return snapshot; }; },
  TerraDrawSelectMode: function(){}, TerraDrawPolygonMode: function(){} };
var ADAPT = { TerraDrawMapLibreGLAdapter: function(){} };
var fakeMap = { doubleClickZoom:{ disable:function(){}, enable:function(){} } };
fetch = function () { return SP({ status:200, redirected:false,
    headers:{ get:function(){ return 'application/json'; } },
    json:function(){ return SP({
    ok:true, unique_name:'Test', polygons:{ features:[
      { geometry:mpoly(0), properties:{ db_id:11, role:'source' } },
      { geometry:mpoly(5), properties:{ db_id:12, role:'deposit' } }]}}); }}); };

var out = [];
R.start({ map:fakeMap, csrf:function(){return 'x';}, terraDraw:TD, adapter:ADAPT, id:1 })
 .then(function (info) { out.push('loaded ' + info.count + ' polygons for ' + info.name); })
 .catch(function (e) { out.push('start FAILED: ' + e.message); });

snapshot = [{ geometry:poly(0), properties:{ ls_db_id:11 } },
            { geometry:poly(5), properties:{ ls_db_id:12 } }];
var d = R.diff();
out.push('untouched        -> updates ' + d.updates.length + ', deletes ' + d.deletes.length);
snapshot[0].geometry.coordinates[0][0][0] = 0.0000000001;
d = R.diff();
out.push('float noise only -> updates ' + d.updates.length);
snapshot[0].geometry = poly(2);
d = R.diff();
out.push('one vertex moved -> updates ' + d.updates.length + ' (db_id ' +
         (d.updates[0] && d.updates[0].db_id) + ')');
R.markDeleted(12, true);
d = R.diff();
out.push('one marked gone  -> updates ' + d.updates.length + ', deletes ' + JSON.stringify(d.deletes));
out.join('\n');
