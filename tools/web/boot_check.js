// Run the page's inline script against minimal stubs. This cannot render a
// map, but it executes the whole top-level body -- which is exactly where an
// ordering fault like "var VIS used before its initialiser" lives, and where
// a syntax check sees nothing wrong.
var ObjC; // (unused; JXA global)
function El() {
  this.style = {}; this.dataset = {}; this.checked = false; this.value = '0';
  this.textContent = ''; this.innerHTML = ''; this.hidden = false;
  this.children = []; this.options = []; this.files = [];
  this.selectedIndex = 0; this.disabled = false; this.classList = { add: function(){}, remove: function(){}, toggle: function(){}, contains: function(){return false;} };
}
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.addEventListener = function () {};
El.prototype.removeEventListener = function () {};
El.prototype.querySelectorAll = function () { return []; };
El.prototype.querySelector = function () { return new El(); };
El.prototype.setAttributeNS = function () {};
El.prototype.getAttribute = function () { return null; };
El.prototype.hasAttribute = function () { return false; };
El.prototype.removeAttribute = function () {};
El.prototype.dispatchEvent = function () { return true; };
El.prototype.scrollIntoView = function () {};
El.prototype.replaceChildren = function () {};
El.prototype.setAttribute = function () {};
El.prototype.getContext = function () { return null; };
El.prototype.getBoundingClientRect = function () {
  return { top:0, left:0, right:100, bottom:100, width:100, height:100, x:0, y:0 }; };
El.prototype.insertBefore = function (c) { this.children.push(c); return c; };
El.prototype.removeChild = function (c) { return c; };
El.prototype.contains = function () { return false; };
El.prototype.closest = function () { return null; };
El.prototype.focus = function () {};
El.prototype.click = function () {};
El.prototype.remove = function () {};

var made = {};
var document = {
  getElementById: function (id) { return made[id] || (made[id] = new El()); },
  createElement: function () { return new El(); },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
  body: new El(), head: new El(),
  documentElement: new El(),
  createElementNS: function () { return new El(); },
  querySelector: function () { return null; },
  getElementsByTagName: function () { return []; },
  createTextNode: function (t) { return { textContent: t }; },
  readyState: 'complete',
};
var location = { search: '', hash: '', href: 'http://x/inventory/',
                 pathname: '/inventory/', origin: 'http://x', host: 'x',
                 protocol: 'http:', reload: function(){}, assign: function(){} };
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
  this.on = function(){}; this.once = function(){}; this.off = function(){};
  this.getStyle = function(){ return { layers: [], sources: {} }; };
  this.isStyleLoaded = function(){ return true; };
  this.loaded = function(){ return true; };
  this.getContainer = function(){ return new El(); };
  this.getCanvas = function(){ return new El(); };
  this.getCanvasContainer = function(){ return new El(); };
  this.queryRenderedFeatures = function(){ return []; };
  this.querySourceFeatures = function(){ return []; };
  this.setFilter = function(){}; this.getFilter = function(){ return null; };
  this.setLayoutProperty = function(){};
  this.getLayoutProperty = function(){ return null; };
  this.getPaintProperty = function(){ return null; };
  this.flyTo=function(){}; this.jumpTo=function(){}; this.panTo=function(){};
  this.project=function(){return {x:0,y:0};};
  this.unproject=function(){return {lng:0,lat:0};};
  this.remove=function(){}; this.triggerRepaint=function(){};
  this.setMaxBounds=function(){}; this.setStyle=function(){};
  this.hasImage=function(){return false;}; this.addImage=function(){};
  this.loadImage=function(){}; this.listImages=function(){return [];};
  this.setFog=function(){}; this.setLight=function(){};
  this.getLayersOrder=function(){return [];};
  this.scrollZoom={enable:function(){},disable:function(){}};
  this.boxZoom={enable:function(){},disable:function(){}};
  this.doubleClickZoom={enable:function(){},disable:function(){}}; this.addSource=function(){}; this.addLayer=function(){};
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
               removeEventListener: function(){},
               matchMedia: function(){ return { matches:false, addListener:function(){},
                                                addEventListener:function(){} }; },
               location: location, history: history, navigator: navigator,
               localStorage: { getItem:function(){return null;}, setItem:function(){},
                               removeItem:function(){} },
               sessionStorage: { getItem:function(){return null;}, setItem:function(){},
                                 removeItem:function(){} },
               devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
               requestAnimationFrame: function(){ return 0; },
               getComputedStyle: function(){ return { getPropertyValue:function(){return '';} }; },
               setTimeout: setTimeout, clearTimeout: clearTimeout,
               open: function(){ return null; }, print: function(){} };
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
