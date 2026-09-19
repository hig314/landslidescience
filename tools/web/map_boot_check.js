// Boot the inventory map's map.js against stubs, with its real sibling
// modules loaded first. Usage:
//   curl -s https://landslidescience.org/inventory/ | grep -oE '/static/.*map\.[a-f0-9]*\.js'
//   curl -s "https://landslidescience.org<that>" > /tmp/prod_map.js
//   tools/web/fetch_map_deps.sh            # writes /tmp/map_deps.js
//   osascript -l JavaScript tools/web/map_boot_check.js
//
// Why: boot_check.sh covers the /lidar/ page's inline script. map.js is a
// separate file with its own dependencies, and it is the page most of the
// editing happens on -- an ordering fault there is the one that hurts.
var MAP_JS = '/tmp/prod_map.js';
// The inventory map's own script, booted against stubs WITH its real sibling
// modules loaded first -- basemaps, colours, overlays, export, demshade bridge.
var stub = $.NSString.stringWithContentsOfFileEncodingError(
  '/Users/Hig/Claude_projects/landslidescience/tools/web/boot_check.js', 4, null).js;
// reuse the stub definitions but not its final eval
stub = stub.replace(/var src = [\s\S]*$/, '');
eval(stub);
var deps = $.NSString.stringWithContentsOfFileEncodingError('/tmp/map_deps.js', 4, null).js;
try { eval(deps); } catch (e) { throw new Error('DEPS FAILED: ' + e.message); }
// The modules publish themselves on `window`, which in a browser IS the global
// scope. Here it is an ordinary object, so map.js's bare references to
// LSBasemaps, LSColors and friends would not resolve. Bridge them across.
for (var _k in window) { try { this[_k] = window[_k]; } catch (e) {} }
var src = $.NSString.stringWithContentsOfFileEncodingError(MAP_JS, 4, null).js;
try { eval(src); 'BOOT OK — map.js ran to completion'; }
catch (e) { 'BOOT FAILED: ' + e.message; }
