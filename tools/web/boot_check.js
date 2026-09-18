// Run the page's inline script against minimal stubs. This cannot render a
// map, but it executes the whole top-level body -- which is exactly where an
// ordering fault like "var VIS used before its initialiser" lives, and where
// a syntax check sees nothing wrong.
var ObjC; // (unused; JXA global)
function El() {
  this.style = {}; this.dataset = {}; this.checked = false; this.value = '0';
  this.textContent = ''; this.innerHTML = ''; this.hidden = false;
  this.children = []; this.classList = { add: function(){}, remove: function(){}, toggle: function(){}, contains: function(){return false;} };
}
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.addEventListener = function () {};
El.prototype.removeEventListener = function () {};
El.prototype.querySelectorAll = function () { return []; };
El.prototype.setAttribute = function () {};
El.prototype.getContext = function () { return null; };
El.prototype.remove = function () {};

var made = {};
var document = {
  getElementById: function (id) { return made[id] || (made[id] = new El()); },
  createElement: function () { return new El(); },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  body: new El(), head: new El(),
};
var location = { search: '', href: 'http://x/lidar/', pathname: '/lidar/' };
var history = { replaceState: function () {} };
var navigator = { userAgent: 'node', clipboard: { writeText: function(){} } };
function URLSearchParams(s) { this.get = function () { return null; };
  this.set = function () {}; this.toString = function () { return ''; }; }
function fetch() { return { then: function () { return this; },
                            catch: function () { return this; } }; }
var console = { log: function(){}, warn: function(){}, error: function(){} };
function setTimeout(f) { return 0; }
function setInterval() { return 0; }
function clearTimeout() {}
var Map_ = function () {
  this.on = function(){}; this.addSource=function(){}; this.addLayer=function(){};
  this.getLayer=function(){return null;}; this.getSource=function(){return null;};
  this.removeLayer=function(){}; this.removeSource=function(){};
  this.moveLayer=function(){}; this.setPaintProperty=function(){};
  this.getTerrain=function(){return null;}; this.setTerrain=function(){};
  this.setSky=function(){}; this.getZoom=function(){return 10;};
  this.getCenter=function(){return {lat:60,lng:-150};};
  this.getBounds=function(){return {getWest:function(){return -151;},
    getEast:function(){return -150;},getSouth:function(){return 59;},
    getNorth:function(){return 61;}};};
  this.getPitch=function(){return 0;}; this.getBearing=function(){return 0;};
  this.easeTo=function(){}; this.addControl=function(){}; this.fitBounds=function(){};
  this.dragRotate={enable:function(){},disable:function(){}};
  this.touchZoomRotate={enable:function(){},disable:function(){},enableRotation:function(){},disableRotation:function(){}};
  this.keyboard={enable:function(){},disable:function(){}};
  this.setBearing=function(){}; this.setPitch=function(){}; this.resize=function(){};
};
var maplibregl = { Map: Map_, addProtocol: function(){}, NavigationControl: function(){},
                   ScaleControl: function(){}, Marker: function(){ this.setLngLat=function(){return this;};
                   this.addTo=function(){return this;}; } };
var pmtiles = { Protocol: function () { this.tile = function(){}; this.add = function(){}; },
                PMTiles: function () { this.getHeader = function(){ return {then:function(){return this;},
                  catch:function(){return this;}}; }; } };
var window = { DemShade: null, MapLibreGlDemShade: null, addEventListener: function(){},
               matchMedia: function(){ return { matches:false, addListener:function(){} }; } };
var DemShade = { register:function(){}, addDataset:function(){}, addImageServer:function(){},
                 url:function(){return '';}, demUrl:function(){return '';},
                 diffUrl:function(){return '';},
                 catalogOpts:function(p,e){return {};}, stats:function(){return {};} };
window.DemShade = DemShade;

var src = $.NSString.stringWithContentsOfFileEncodingError('/tmp/boot_check_inline.js', 4, null).js;
try {
  eval(src);
  'BOOT OK — top-level body ran to completion';
} catch (e) {
  'BOOT FAILED: ' + e.message;
}
