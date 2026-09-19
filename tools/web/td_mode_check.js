// Drive the REAL terra-draw library (not a stub) to confirm features whose
// properties.mode is 'polygon' are accepted by the mode set revise.js builds.
var window = {}, document = { addEventListener: function(){} };
var src = $.NSString.stringWithContentsOfFileEncodingError('/tmp/td.js', 4, null).js;
eval(src);
var TD = window.terraDraw || this.terraDraw;
if (!TD) { 'FAILED: terra-draw did not expose a global'; } else {
  var out = [];
  // a no-op adapter: enough for the store/validation path
  function Adapter() {
    this.register=function(){}; this.unregister=function(){};
    this.render=function(){}; this.getMapEventElement=function(){
      return { addEventListener:function(){}, removeEventListener:function(){} }; };
    this.setDraggability=function(){}; this.setCursor=function(){};
    this.setDoubleClickToZoom=function(){}; this.project=function(){return {x:0,y:0};};
    this.unproject=function(){return {lng:0,lat:0};};
    this.getLngLatFromEvent=function(){return {lng:0,lat:0};};
    this.getCoordinatePrecision=function(){ return 9; };
    this.setMapDraggability=function(){};
  }
  var poly = { type:'Feature',
    geometry:{ type:'Polygon', coordinates:[[[0,0],[1,0],[1,1],[0,1],[0,0]]] },
    properties:{ mode:'polygon' } };

  function tryModes(label, modes) {
    try {
      var d = new TD.TerraDraw({ adapter: new Adapter(), modes: modes });
      d.start(); d.setMode('select');
      var r = d.addFeatures([JSON.parse(JSON.stringify(poly))]);
      out.push(label + ': ' + JSON.stringify(r));
    } catch (e) { out.push(label + ': threw ' + e.message); }
  }
  var withHole = { type:'Feature', properties:{ mode:'polygon' },
    geometry:{ type:'Polygon', coordinates:[
      [[0,0],[4,0],[4,4],[0,4],[0,0]],
      [[1,1],[2,1],[2,2],[1,2],[1,1]] ] } };
  var multi = { type:'Feature', properties:{ mode:'polygon' },
    geometry:{ type:'MultiPolygon', coordinates:[[[[0,0],[1,0],[1,1],[0,1],[0,0]]]] } };
  function tryFeature(label, f) {
    try {
      var d = new TD.TerraDraw({ adapter:new Adapter(),
        modes:[new TD.TerraDrawPolygonMode(), new TD.TerraDrawSelectMode({})] });
      d.start(); d.setMode('select');
      out.push(label + ': ' + JSON.stringify(d.addFeatures([f])));
    } catch (e) { out.push(label + ': threw ' + e.message); }
  }
  // what revise.js now sends: the single part, unwrapped
  var unwrapped = { type:'Feature', properties:{ mode:'polygon' },
    geometry:{ type:'Polygon', coordinates: multi.geometry.coordinates[0] } };
  tryFeature('MultiPolygon (1 part)', multi);
  tryFeature('...after unwrapping  ', unwrapped);
  tryFeature('Polygon with a hole  ', withHole);
  tryModes('select only          ', [new TD.TerraDrawSelectMode({})]);
  tryModes('polygon + select     ', [new TD.TerraDrawPolygonMode(),
                                     new TD.TerraDrawSelectMode({
    flags:{ polygon:{ feature:{ draggable:false, rotateable:false, scaleable:false,
      coordinates:{ midpoints:true, draggable:true, deletable:true } } } } })]);
  out.join('\n');
}
