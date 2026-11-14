/* charts.js — Seasonal histogram and timeline charts: compute, draw, canvas tooltips.
 *
 * Moved out of map.js on 2026-11-14 without changing its text. map.js calls
 * window.LSCharts.init(ctx) once, at the point in its start-up where this code
 * used to sit, and passes in the few things it needs from the map page:
 *   _getTimedEvents, _getTimelineEvents, CURRENT_YEAR, TL_HOL_UNITS, TL_MOD_UNITS, TL_ANN_UNITS, yearPosToMinNum, srcAreaDual, depAreaDual, volDual, yearDual, cbStream, cbSiteVolume, cbTimed, cbSeismic, _ftPass, cbLimitView, histDaysSlider, cbHistVol, cbHistLog, cbTimingVol, cbTimingLog, writeUrlState, makeViewportTest, histPanel, timingPanel.
 * It returns what map.js still uses: updateHistogram, onHistResize, updateTimeline, onTimingResize.
 *
 * The two floating panels themselves are made in map.js, just after this
 * file's init call. The filter controls (sliders, checkboxes) are passed in as
 * the elements map.js already holds.
 */
(function () {
    'use strict';

    window.LSCharts = { init: function (ctx) {
        var _getTimedEvents = ctx._getTimedEvents;
        var _getTimelineEvents = ctx._getTimelineEvents;
        var CURRENT_YEAR = ctx.CURRENT_YEAR;
        var TL_HOL_UNITS = ctx.TL_HOL_UNITS;
        var TL_MOD_UNITS = ctx.TL_MOD_UNITS;
        var TL_ANN_UNITS = ctx.TL_ANN_UNITS;
        var yearPosToMinNum = ctx.yearPosToMinNum;
        var srcAreaDual = ctx.srcAreaDual;
        var depAreaDual = ctx.depAreaDual;
        var volDual = ctx.volDual;
        var yearDual = ctx.yearDual;
        var cbStream = ctx.cbStream;
        var cbSiteVolume = ctx.cbSiteVolume;
        var cbTimed = ctx.cbTimed;
        var cbSeismic = ctx.cbSeismic;
        var _ftPass = ctx._ftPass;
        var cbLimitView = ctx.cbLimitView;
        var histDaysSlider = ctx.histDaysSlider;
        var cbHistVol = ctx.cbHistVol;
        var cbHistLog = ctx.cbHistLog;
        var cbTimingVol = ctx.cbTimingVol;
        var cbTimingLog = ctx.cbTimingLog;
        var writeUrlState = ctx.writeUrlState;
        var makeViewportTest = ctx.makeViewportTest;
        var histPanel = ctx.histPanel;
        var timingPanel = ctx.timingPanel;

        // ---------------------------------------------------------------------------
        // Chart tooltip
        // ---------------------------------------------------------------------------
        var _chartTooltip = (function() {
            var el = document.createElement('div');
            el.style.cssText = 'position:fixed;background:#fff;border:1px solid #ccc;border-radius:3px;' +
                'padding:3px 8px;font-size:11px;color:#333;pointer-events:none;z-index:200;' +
                'display:none;box-shadow:0 1px 4px rgba(0,0,0,0.18);white-space:nowrap;';
            document.body.appendChild(el);
            return el;
        }());
        var _tlHits   = null;
        var _histHits = null;

        function showChartTip(cx, cy, text) {
            _chartTooltip.textContent = text;
            _chartTooltip.style.display = 'block';
            _chartTooltip.style.left = (cx + 14) + 'px';
            _chartTooltip.style.top  = (cy - 24) + 'px';
        }
        function hideChartTip() { _chartTooltip.style.display = 'none'; }

        function fmtEvYr(v) {
            if (v <= 0)   return '0';
            if (v < 0.01) return v.toFixed(4);
            if (v < 0.1)  return v.toFixed(3);
            if (v < 10)   return v.toFixed(2);
            return v.toFixed(1);
        }

        // ===========================================================================
        // Seasonal timing histogram
        // ===========================================================================

        function isLeapYear(y) { return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0; }

        // Distribute weight across 37 bins for a day range [from, to] in a year of yd days.
        // Optional perBin callback receives (binIndex, fraction) for parallel volume tracking.
        function distributeRange(bins, from, to, span, yd, perBin) {
            for (var b = 0; b < 37; b++) {
                var bStart = b * 10 + 1;
                var bEnd   = b < 36 ? b * 10 + 10 : yd;
                var overlap = Math.max(0, Math.min(bEnd, to) - Math.max(bStart, from) + 1);
                if (overlap > 0) {
                    var frac = overlap / span;
                    bins[b] += frac;
                    if (perBin) perBin(b, frac);
                }
            }
        }

        function currentFilterState() {
            var types = [], classes = [];
            document.querySelectorAll('.filter-type').forEach(function (c) { if (c.checked) types.push(c.value); });
            document.querySelectorAll('.filter-class').forEach(function (c) { if (c.checked) classes.push(c.value); });
            function dualLo(dual, toValue) {
                if (!dual) return null;
                var v = parseFloat(dual.minEl.value);
                return v > dual.sliderMin ? toValue(v) : null;
            }
            function dualHi(dual, toValue) {
                if (!dual) return null;
                var v = parseFloat(dual.maxEl.value);
                return v < dual.sliderMax ? toValue(v) : null;
            }
            var areaToValue = function (p) { return Math.pow(10, p + 3); };
            var volToValue  = function (p) { return Math.pow(10, p + 4); };
            function dualYearLo() {
                if (!yearDual) return null;
                var v = parseInt(yearDual.minEl.value);
                return v > yearDual.sliderMin ? yearPosToMinNum(v) : null;
            }
            function dualYearHi() {
                if (!yearDual) return null;
                var v = parseInt(yearDual.maxEl.value);
                return v < yearDual.sliderMax ? yearPosToMinNum(v) : null;
            }
            return {
                types:    types,
                classes:  classes,
                minSrcArea: dualLo(srcAreaDual, areaToValue),
                maxSrcArea: dualHi(srcAreaDual, areaToValue),
                minDepArea: dualLo(depAreaDual, areaToValue),
                maxDepArea: dualHi(depAreaDual, areaToValue),
                minVol:     dualLo(volDual,     volToValue),
                maxVol:     dualHi(volDual,     volToValue),
                minYear:    dualYearLo(),
                maxYear:    dualYearHi(),
                stream:       cbStream       && cbStream.checked,
                siteVolume:   cbSiteVolume   && cbSiteVolume.checked,
                timed:        cbTimed        && cbTimed.checked,
                seismic:      cbSeismic      && cbSeismic.checked,
                // landslide features: tested with _ftPass against ev.features
            };
        }

        function computeHistogram() {
            var _timedEvents = _getTimedEvents();
            if (!_timedEvents) return null;
            var inView = makeViewportTest();
            var fs = currentFilterState();
            var maxDays = histDaysSlider ? parseInt(histDaysSlider.value) : 30;
            var bins      = new Array(37).fill(0);   // count per bin
            var volBins   = new Array(37).fill(0);   // cumulative volume per bin
            var volMaxEv  = new Array(37).fill(0);   // largest single-event contribution per bin
            var count     = 0;
            var unknownVolCount = 0;

            _timedEvents.forEach(function (ev) {
                // Map-view clip only when the pinned "Limit to map view" toggle is on.
                if (cbLimitView && cbLimitView.checked && !inView(ev.lon, ev.lat)) return;
                if (fs.types.indexOf(ev.ls_type) < 0) return;
                if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
                // Dual-handle range filters. Each side only applies when the
                // handle has moved off the no-filter position. NULL field
                // values fail the comparison and drop out the moment any
                // side activates.
                if (fs.minVol     !== null && !(ev.vol      >= fs.minVol))     return;
                if (fs.maxVol     !== null && !(ev.vol      <= fs.maxVol))     return;
                if (fs.minSrcArea !== null && !(ev.area_src >= fs.minSrcArea)) return;
                if (fs.maxSrcArea !== null && !(ev.area_src <= fs.maxSrcArea)) return;
                if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep >= fs.minDepArea)) return;
                if (fs.maxDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep <= fs.maxDepArea)) return;
                if (fs.minYear !== null || fs.maxYear !== null) {
                    var yn = ev.year_num;
                    if (yn === null) return;
                    if (fs.minYear !== null && yn < fs.minYear) return;
                    if (fs.maxYear !== null && yn > fs.maxYear) return;
                }
                if (fs.stream       && !ev.stream_dam)      return;
                if (fs.siteVolume   && !ev.has_site_volume) return;
                if (!_ftPass(ev.features))                  return;
                if (fs.seismic && ev.timing !== 'point')  return;
                if (fs.timed   && ev.timing === 'point')  return;

                if (ev.timing === 'range' && ev.span - 1 > maxDays) return;

                count++;
                var hasVol = ev.vol !== null && ev.vol > 0;
                if (!hasVol) unknownVolCount++;
                var v = hasVol ? ev.vol : 0;
                var yd = isLeapYear(ev.year) ? 366 : 365;

                // perBin tallies volume (frac * vol) and per-bin max contribution.
                function tallyVol(bi, frac) {
                    if (!hasVol) return;
                    var contrib = frac * v;
                    volBins[bi] += contrib;
                    if (contrib > volMaxEv[bi]) volMaxEv[bi] = contrib;
                }

                if (ev.timing === 'point') {
                    var pb = Math.min(36, Math.floor((ev.doy - 1) / 10));
                    bins[pb] += 1.0;
                    tallyVol(pb, 1.0);
                } else {
                    if (ev.doy <= ev.doy_end) {
                        distributeRange(bins, ev.doy, ev.doy_end, ev.span, yd, tallyVol);
                    } else {
                        distributeRange(bins, ev.doy,  yd, ev.span, yd, tallyVol);
                        distributeRange(bins, 1, ev.doy_end, ev.span, yd, tallyVol);
                    }
                }
            });

            return {
                bins: bins, count: count,
                volBins: volBins, volMaxEv: volMaxEv,
                unknownVolCount: unknownVolCount,
            };
        }

        // Nice y-axis scale: returns {max, step} giving 3-6 ticks
        function niceScale(maxVal) {
            if (maxVal <= 0) return { max: 1, step: 0.5 };
            var steps = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50];
            for (var i = 0; i < steps.length; i++) {
                var ticks = Math.ceil(maxVal / steps[i]);
                if (ticks >= 3 && ticks <= 7) return { max: ticks * steps[i], step: steps[i] };
            }
            return { max: Math.ceil(maxVal), step: 1 };
        }

        // Magnitude-aware version for volume axes (handles 10^3 to 10^12 m³).
        function niceScaleVol(maxVal) {
            if (maxVal <= 0) return { max: 1, step: 1 };
            var mag = Math.pow(10, Math.floor(Math.log10(maxVal)));
            var fracs = [1, 2, 2.5, 5, 10];
            for (var i = 0; i < fracs.length; i++) {
                var step = fracs[i] * mag;
                var ticks = Math.ceil(maxVal / step);
                if (ticks >= 3 && ticks <= 7) return { max: ticks * step, step: step };
            }
            return { max: Math.ceil(maxVal / mag) * mag, step: mag };
        }

        // Volume formatters
        function fmtVolBig(m3) {
            if (!m3) return '0';
            if (m3 >= 1e9)  return (m3 / 1e9 ).toFixed(m3 >= 1e10 ? 1 : 2) + ' km³';
            if (m3 >= 1e6)  return (m3 / 1e6 ).toFixed(m3 >= 1e7  ? 1 : 2) + ' M m³';
            if (m3 >= 1e3)  return Math.round(m3 / 1e3) + ' k m³';
            return Math.round(m3) + ' m³';
        }
        function fmtVolTick(m3) {
            if (m3 <= 0) return '0';
            if (m3 >= 1e9)  return (m3 / 1e9 ) + 'B';   // billions
            if (m3 >= 1e6)  return (m3 / 1e6 ) + 'M';
            if (m3 >= 1e3)  return (m3 / 1e3 ) + 'k';
            return String(Math.round(m3));
        }

        // Bar palette: regular (light) and dominant-event slice (darker).
        var BAR_COLOR       = '#7090c8';
        var BAR_COLOR_DARK  = '#3f67b1';

        var FINAL_BIN_DAYS = 5.25;
        var MONTHS     = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var MONTH_MID  = [16, 45, 75, 106, 136, 167, 197, 228, 259, 289, 320, 350];
        var MONTH_STRT = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335];

        function renderHistogram(result) {
            var canvas = document.getElementById('hist-canvas');
            var panel  = document.getElementById('hist-panel');
            if (!canvas || !panel) return;

            canvas.width  = panel.clientWidth;
            canvas.height = panel.clientHeight - panel.querySelector('.hist-header').offsetHeight;

            var ctx = canvas.getContext('2d');
            var W = canvas.width, H = canvas.height;
            var ml = 42, mr = 8, mt = 8, mb = 24;
            var cw = W - ml - mr, ch = H - mt - mb;

            var subtitle = document.getElementById('hist-subtitle');
            if (!result || result.count === 0) {
                ctx.clearRect(0, 0, W, H);
                ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
                var inView0 = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
                ctx.fillText(result ? ('No timed events' + (inView0 ? ' in current view' : '')) : 'Loading…', W/2, H/2);
                if (subtitle) subtitle.textContent = '0 events with precise timing' + inView0;
                return;
            }
            var maxDays = histDaysSlider ? parseInt(histDaysSlider.value) : 30;
            var useVol = cbHistVol && cbHistVol.checked;
            var useLog = cbHistLog && cbHistLog.checked;

            // Pick which series we're rendering (count or volume) and apply final-bin normalization.
            var normF = 10 / FINAL_BIN_DAYS;
            var bc, bcMax;
            if (useVol) {
                bc    = result.volBins.slice();
                bcMax = result.volMaxEv.slice();
                bc[36]    *= normF;
                bcMax[36] *= normF;
            } else {
                bc    = result.bins.slice();
                bcMax = null;
                bc[36] *= normF;
            }

            var inView = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
            var subtitleText = result.count + ' event' + (result.count === 1 ? '' : 's') +
                inView + ' \u2264\u202f' + maxDays + '\u202fd uncertainty';
            if (useVol && result.unknownVolCount) {
                subtitleText += ' \u00b7 ' + result.unknownVolCount + ' with unknown volume';
            }
            if (subtitle) subtitle.textContent = subtitleText;

            // Y-axis scale (linear or log)
            var maxCombined = 0;
            for (var b = 0; b < 37; b++) maxCombined = Math.max(maxCombined, bc[b]);
            var ymax, ystep, logMin, logMax;
            if (useLog && maxCombined > 0) {
                var minPos = Infinity;
                for (var b2 = 0; b2 < 37; b2++) if (bc[b2] > 0) minPos = Math.min(minPos, bc[b2]);
                logMin = Math.floor(Math.log10(minPos) - 0.01);
                logMax = Math.ceil(Math.log10(maxCombined) + 0.01);
                if (logMax <= logMin) logMax = logMin + 1;
            } else {
                var sc = useVol ? niceScaleVol(maxCombined) : niceScale(maxCombined);
                ymax = sc.max; ystep = sc.step;
            }

            function yPixel(val) {
                if (val <= 0) return mt + ch;
                if (useLog) {
                    var lv = Math.log10(val);
                    if (lv < logMin) return mt + ch;
                    return mt + ch - (lv - logMin) / (logMax - logMin) * ch;
                }
                return mt + ch - (val / ymax) * ch;
            }

            var totalUnits = 36 + FINAL_BIN_DAYS / 10;
            var barUnit = cw / totalUnits;

            ctx.clearRect(0, 0, W, H);

            // Grid lines
            ctx.strokeStyle = '#e8e8e8'; ctx.lineWidth = 0.5;
            if (useLog) {
                for (var exp = logMin + 1; exp <= logMax; exp++) {
                    var gyL = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                    ctx.beginPath(); ctx.moveTo(ml, gyL); ctx.lineTo(ml + cw, gyL); ctx.stroke();
                }
            } else {
                for (var v = ystep; v <= ymax + 0.001; v += ystep) {
                    var gy = mt + ch - (v / ymax) * ch;
                    ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
                }
            }

            // Month dividers
            ctx.strokeStyle = '#ddd'; ctx.lineWidth = 0.5;
            MONTH_STRT.forEach(function (doy) {
                var mx = ml + (doy - 1) / 10 * barUnit;
                ctx.beginPath(); ctx.moveTo(mx, mt); ctx.lineTo(mx, mt + ch); ctx.stroke();
            });

            // Bars: vol mode draws darker "largest single contributor" slice at the bottom of each bar,
            // with the rest of the bin's volume in the lighter color above.
            for (var b3 = 0; b3 < 37; b3++) {
                var bw = b3 < 36 ? barUnit : barUnit * FINAL_BIN_DAYS / 10;
                var bx = ml + b3 * barUnit;
                var gap = bw > 3 ? 0.8 : 0;
                var bxDraw = bx + gap/2, bwDraw = bw - gap;
                if (bc[b3] <= 0) continue;
                var yTop = yPixel(bc[b3]);
                var yBot = mt + ch;
                if (useVol && bcMax) {
                    var maxSeg = Math.min(bcMax[b3], bc[b3]);
                    var ySplit = yPixel(bc[b3] - maxSeg);
                    if (ySplit > yTop) {
                        ctx.fillStyle = BAR_COLOR;
                        ctx.fillRect(bxDraw, yTop, bwDraw, ySplit - yTop);
                    }
                    if (yBot > ySplit) {
                        ctx.fillStyle = BAR_COLOR_DARK;
                        ctx.fillRect(bxDraw, ySplit, bwDraw, yBot - ySplit);
                    }
                } else {
                    ctx.fillStyle = BAR_COLOR;
                    ctx.fillRect(bxDraw, yTop, bwDraw, yBot - yTop);
                }
            }

            // Save hit-test data for mouseover
            _histHits = [];
            for (var bh = 0; bh < 37; bh++) {
                var bhw = bh < 36 ? barUnit : barUnit * FINAL_BIN_DAYS / 10;
                _histHits.push({
                    b: bh,
                    x0: ml + bh * barUnit,
                    x1: ml + bh * barUnit + bhw,
                    val: bc[bh],
                    maxEv: useVol && bcMax ? bcMax[bh] : null,
                });
            }

            ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
            ctx.beginPath(); ctx.moveTo(ml, mt); ctx.lineTo(ml, mt + ch); ctx.lineTo(ml + cw, mt + ch); ctx.stroke();

            // Y-axis labels
            ctx.fillStyle = '#666'; ctx.font = '8px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
            if (useLog) {
                for (var exp2 = logMin; exp2 <= logMax; exp2++) {
                    var lvVal = Math.pow(10, exp2);
                    var tyL = mt + ch - (exp2 - logMin) / (logMax - logMin) * ch;
                    ctx.fillText(useVol ? fmtVolTick(lvVal) : (lvVal >= 1 ? String(lvVal) : lvVal.toFixed(-exp2)), ml - 3, tyL);
                }
            } else {
                for (var v2 = 0; v2 <= ymax + 0.001; v2 += ystep) {
                    var ty = mt + ch - (v2 / ymax) * ch;
                    ctx.fillText(useVol ? fmtVolTick(v2) : (v2 % 1 === 0 ? v2 : v2.toFixed(1)), ml - 3, ty);
                }
            }

            ctx.save();
            ctx.translate(8, mt + ch / 2); ctx.rotate(-Math.PI / 2);
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#888'; ctx.font = '8px sans-serif';
            ctx.fillText(useVol ? 'volume (m\u00b3) / 10 days' : 'events / 10 days', 0, 0);
            ctx.restore();

            ctx.fillStyle = '#777'; ctx.font = '8px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
            MONTHS.forEach(function (m, i) {
                var lx = ml + (MONTH_MID[i] - 1) / 10 * barUnit;
                ctx.fillText(m, lx, mt + ch + 5);
            });

            // Legend swatch (top-right corner of plot area)
            var legendX = ml + cw - 90, legendY = mt + 3;
            if (useVol) {
                ctx.fillStyle = BAR_COLOR_DARK; ctx.fillRect(legendX, legendY, 9, 8);
                ctx.fillStyle = '#555'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = '8px sans-serif';
                ctx.fillText('Largest event', legendX + 12, legendY + 4);
                ctx.fillStyle = BAR_COLOR; ctx.fillRect(legendX, legendY + 11, 9, 8);
                ctx.fillStyle = '#555';
                ctx.fillText('Other events', legendX + 12, legendY + 15);
            } else {
                ctx.fillStyle = BAR_COLOR; ctx.fillRect(legendX, legendY, 9, 8);
                ctx.fillStyle = '#555'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = '8px sans-serif';
                ctx.fillText('Catastrophic', legendX + 12, legendY + 4);
            }
        }

        function updateHistogram() {
            if (!histPanel || histPanel.classList.contains('hidden')) return;
            renderHistogram(computeHistogram());
        }

        function onHistResize() {
            if (histPanel && !histPanel.classList.contains('hidden')) renderHistogram(computeHistogram());
        }

        // ===========================================================================
        // Timeline histogram
        // ===========================================================================

        // Monthly binning helpers for timeline 2012+
        // Key format for monthly bins: 'YYYY-M' (M = 0-11)
        var TL_MONTHLY_START = 2012;

        function tlParseDate(str) {
            if (!str) return null;
            var p = str.split('-');
            return new Date(parseInt(p[0]), parseInt(p[1]) - 1, parseInt(p[2]));
        }

        // Distribute weight across monthly bins for a date range [d0str, d1str].
        // Optional perBin callback receives (key, fraction) for parallel volume tracking.
        function tlDistributeRange(bins, d0str, d1str, weight, perBin) {
            var d0 = tlParseDate(d0str), d1 = tlParseDate(d1str);
            if (!d0 || !d1 || d1 < d0) return;
            var totalMs = d1 - d0 + 864e5;
            var cur = new Date(d0.getFullYear(), d0.getMonth(), 1);
            while (cur <= d1) {
                var yr = cur.getFullYear(), mo = cur.getMonth();
                if (yr >= TL_MONTHLY_START && yr <= CURRENT_YEAR) {
                    var mStart = new Date(yr, mo, 1);
                    var mEnd   = new Date(yr, mo + 1, 0);  // last day of month
                    var oStart = d0 > mStart ? d0 : mStart;
                    var oEnd   = d1 < mEnd   ? d1 : mEnd;
                    if (oStart <= oEnd) {
                        var key = yr + '-' + mo;
                        if (bins[key] !== undefined) {
                            var frac = (oEnd - oStart + 864e5) / totalMs;
                            bins[key] += frac * weight;
                            if (perBin) perBin(key, frac);
                        }
                    }
                }
                cur = new Date(yr, mo + 1, 1);
            }
        }

        function computeTimeline() {
            var _timelineEvents = _getTimelineEvents();
            if (!_timelineEvents) return null;
            var inView = makeViewportTest();
            var fs = currentFilterState();

            // Pre-populate all expected bins with 0
            var bins = {}, volBins = {}, volMaxEv = {};
            function initKey(k) { bins[k] = 0; volBins[k] = 0; volMaxEv[k] = 0; }
            initKey('H');   // Holocene
            initKey('M');   // Modern pre-2000
            for (var y = 2000; y < TL_MONTHLY_START; y++) initKey(String(y));
            for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y++) {
                for (var mo = 0; mo < 12; mo++) initKey(y + '-' + mo);
            }

            var total = 0;
            var unknownVolCount = 0;
            _timelineEvents.forEach(function (ev) {
                // Map-view clip only when the pinned "Limit to map view" toggle is on.
                if (cbLimitView && cbLimitView.checked && !inView(ev.lon, ev.lat)) return;
                if (fs.types.indexOf(ev.ls_type) < 0) return;
                if (fs.classes.length && fs.classes.indexOf(ev.cls || '__unclassified__') < 0) return;
                // See histogram filter above — dual-handle ranges with NULL exclusion.
                if (fs.minVol     !== null && !(ev.vol      >= fs.minVol))     return;
                if (fs.maxVol     !== null && !(ev.vol      <= fs.maxVol))     return;
                if (fs.minSrcArea !== null && !(ev.area_src >= fs.minSrcArea)) return;
                if (fs.maxSrcArea !== null && !(ev.area_src <= fs.maxSrcArea)) return;
                if (fs.minDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep >= fs.minDepArea)) return;
                if (fs.maxDepArea !== null && ev.ls_type === 'catastrophic' && !(ev.area_dep <= fs.maxDepArea)) return;
                if (fs.minYear !== null || fs.maxYear !== null) {
                    var yn = ev.year_num;
                    if (yn === null) return;
                    if (fs.minYear !== null && yn < fs.minYear) return;
                    if (fs.maxYear !== null && yn > fs.maxYear) return;
                }
                if (fs.stream       && !ev.stream_dam)      return;
                if (fs.siteVolume   && !ev.has_site_volume) return;
                if (!_ftPass(ev.features))                  return;
                if (fs.seismic      && !ev.has_seismic)  return;

                total++;
                var hasVol = ev.vol !== null && ev.vol > 0;
                if (!hasVol) unknownVolCount++;
                var v = hasVol ? ev.vol : 0;

                // Tally volume contribution into bin `k` with fraction `frac`.
                function tally(k, frac) {
                    if (!hasVol || bins[k] === undefined) return;
                    var contrib = frac * v;
                    volBins[k] += contrib;
                    if (contrib > volMaxEv[k]) volMaxEv[k] = contrib;
                }

                var yn = ev.year_num;

                if (yn === -1)              { bins['H']++; tally('H', 1); return; }
                if (yn === 0 || yn < 2000)  { bins['M']++; tally('M', 1); return; }
                if (yn < TL_MONTHLY_START)  {
                    var ak = String(yn);
                    if (bins[ak] !== undefined) { bins[ak]++; tally(ak, 1); }
                    return;
                }

                // 2012+: use monthly bins with date spreading
                if (yn > CURRENT_YEAR) { total--; if (!hasVol) unknownVolCount--; return; }

                if (ev.tl_pt) {
                    // Point event (seismic): assign to one month
                    var d = tlParseDate(ev.tl_pt);
                    if (d) {
                        var k = d.getFullYear() + '-' + d.getMonth();
                        if (bins[k] !== undefined) { bins[k] += 1; tally(k, 1); }
                    }
                } else if (ev.tl_d0 && ev.tl_d1) {
                    // Date range: spread proportionally
                    tlDistributeRange(bins, ev.tl_d0, ev.tl_d1, 1, tally);
                } else {
                    // Year precision only: spread evenly across 12 months
                    for (var mo = 0; mo < 12; mo++) {
                        var k = yn + '-' + mo;
                        if (bins[k] !== undefined) { bins[k] += 1/12; tally(k, 1/12); }
                    }
                }
            });

            // Normalize raw counts AND volumes to per-year rate using the same scaling.
            function normalize(yearScale) {
                bins['H']    /= yearScale.H;    volBins['H']    /= yearScale.H;    volMaxEv['H']    /= yearScale.H;
                bins['M']    /= yearScale.M;    volBins['M']    /= yearScale.M;    volMaxEv['M']    /= yearScale.M;
                for (var yr2 = TL_MONTHLY_START; yr2 <= CURRENT_YEAR; yr2++) {
                    for (var mo2 = 0; mo2 < 12; mo2++) {
                        var kk = yr2 + '-' + mo2;
                        bins[kk]     *= 12;
                        volBins[kk]  *= 12;
                        volMaxEv[kk] *= 12;
                    }
                }
            }
            normalize({ H: 10000, M: 150 });

            // Express the rate per MONTH (the finest bin granularity) instead of per
            // year: divide every bin + volume by 12. The chart shape is unchanged —
            // only the y-axis units — but the monthly-resolution tail reads more
            // intuitively (one event in a month → 1 ev/month, not 12 ev/yr).
            Object.keys(bins).forEach(function (k) {
                bins[k]     /= 12;
                volBins[k]  /= 12;
                volMaxEv[k] /= 12;
            });

            return {
                bins: bins, total: total,
                volBins: volBins, volMaxEv: volMaxEv,
                unknownVolCount: unknownVolCount,
            };
        }

        function renderTimeline(result) {
            var canvas = document.getElementById('timing-canvas');
            var panel  = document.getElementById('timing-panel');
            if (!canvas || !panel) return;

            var header = panel.querySelector('.timing-header');
            canvas.width  = panel.clientWidth;
            canvas.height = panel.clientHeight - (header ? header.offsetHeight : 26);

            var ctx = canvas.getContext('2d');
            var W = canvas.width, H = canvas.height;
            var ml = 42, mr = 10, mt = 8, mb = 42;
            var cw = W - ml - mr, ch = H - mt - mb;

            var subtitle = document.getElementById('timing-subtitle');
            if (!result || result.total === 0) {
                ctx.clearRect(0, 0, W, H);
                ctx.fillStyle = '#aaa'; ctx.font = '11px sans-serif'; ctx.textAlign = 'center';
                var inViewTl0 = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
                ctx.fillText(result ? ('No events with temporal data' + (inViewTl0 ? ' in current view' : '')) : 'Loading\u2026', W/2, H/2);
                if (subtitle) subtitle.textContent = '0 events with temporal data' + inViewTl0;
                return;
            }
            var useVolTl = cbTimingVol && cbTimingVol.checked;
            var inViewTl = (cbLimitView && cbLimitView.checked) ? ' in view' : '';
            var subtitleTl = result.total + ' event' + (result.total === 1 ? '' : 's') + ' with temporal data' + inViewTl;
            if (useVolTl && result.unknownVolCount) {
                subtitleTl += ' \u00b7 ' + result.unknownVolCount + ' with unknown volume';
            }
            if (subtitle) subtitle.textContent = subtitleTl;

            // Pick which bin series to render
            var binsR    = useVolTl ? result.volBins  : result.bins;
            var binsRMax = useVolTl ? result.volMaxEv : null;

            // Layout:  Holocene (H_U) | Modern pre-2000 (M_U) | annual 2000-2011 (TL_ANN_UNITS each) | monthly 2012+ (1 per month)
            var H_U = TL_HOL_UNITS, M_U = TL_MOD_UNITS, A_U = TL_ANN_UNITS;
            var annualYears = TL_MONTHLY_START - 2000;           // 12
            var monthlyMonths = (CURRENT_YEAR - TL_MONTHLY_START + 1) * 12;
            var totalUnits = H_U + M_U + annualYears * A_U + monthlyMonths;
            var barUnit = cw / totalUnits;

            // Pixel x for the left edge of a bin key
            function xFor(key) {
                if (key === 'H') return ml;
                if (key === 'M') return ml + H_U * barUnit;
                var dash = key.indexOf('-');
                if (dash < 0) {
                    // annual bin 2000-2011
                    return ml + (H_U + M_U + (parseInt(key) - 2000) * A_U) * barUnit;
                }
                // monthly bin 'YYYY-M'
                var yr = parseInt(key.slice(0, dash)), mo = parseInt(key.slice(dash + 1));
                return ml + (H_U + M_U + annualYears * A_U + (yr - TL_MONTHLY_START) * 12 + mo) * barUnit;
            }
            function wFor(key) {
                if (key === 'H') return H_U * barUnit;
                if (key === 'M') return M_U * barUnit;
                if (key.indexOf('-') < 0) return A_U * barUnit;  // annual
                return barUnit;  // monthly
            }

            var useLog = cbTimingLog && cbTimingLog.checked;
            var maxVal = 0;
            Object.keys(binsR).forEach(function (k) { maxVal = Math.max(maxVal, binsR[k]); });

            // Y-scale setup
            var ymax, ystep, logMin, logMax;
            if (useLog && maxVal > 0) {
                var minPos = Infinity;
                Object.keys(binsR).forEach(function (k) { var v = binsR[k]; if (v > 0) minPos = Math.min(minPos, v); });
                logMin = Math.floor(Math.log10(minPos) - 0.01);
                logMax = Math.ceil(Math.log10(maxVal)  + 0.01);
                if (logMax <= logMin) logMax = logMin + 1;
                ymax = logMax; ystep = 1;  // unused in log mode but keep refs consistent
            } else {
                var sc = useVolTl ? niceScaleVol(maxVal) : niceScale(maxVal);
                ymax = sc.max; ystep = sc.step;
            }

            function yPixel(val) {
                if (val <= 0) return mt + ch;
                if (useLog) {
                    var lv = Math.log10(val);
                    if (lv < logMin) return mt + ch;
                    return mt + ch - Math.max(0, (lv - logMin) / (logMax - logMin)) * ch;
                }
                return mt + ch - (val / ymax) * ch;
            }

            ctx.clearRect(0, 0, W, H);

            // 2012+ highlight
            var x2012 = xFor(TL_MONTHLY_START + '-0');
            ctx.fillStyle = 'rgba(63,103,177,0.07)';
            ctx.fillRect(x2012, mt, ml + cw - x2012, ch);

            // Grid lines
            ctx.strokeStyle = '#e8e8e8'; ctx.lineWidth = 0.5;
            if (useLog) {
                for (var exp = logMin + 1; exp <= logMax; exp++) {
                    var gy = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                    ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
                }
            } else {
                for (var v = ystep; v <= ymax + 0.001; v += ystep) {
                    var gy = mt + ch - (v / ymax) * ch;
                    ctx.beginPath(); ctx.moveTo(ml, gy); ctx.lineTo(ml + cw, gy); ctx.stroke();
                }
            }

            // Month dividers (2012+) — very subtle
            ctx.strokeStyle = 'rgba(0,0,0,0.06)'; ctx.lineWidth = 0.4;
            for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y++) {
                for (var mo = 1; mo < 12; mo++) {
                    var mx = xFor(y + '-' + mo);
                    ctx.beginPath(); ctx.moveTo(mx, mt); ctx.lineTo(mx, mt + ch); ctx.stroke();
                }
            }

            // Year dividers (2012+) and section dividers
            var xModStart = xFor('M'), x2000 = xFor('2000');
            ctx.lineWidth = 0.7; ctx.strokeStyle = '#bbb';
            ctx.beginPath(); ctx.moveTo(xModStart, mt); ctx.lineTo(xModStart, mt + ch); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(x2000,     mt); ctx.lineTo(x2000,     mt + ch); ctx.stroke();

            ctx.strokeStyle = 'rgba(63,103,177,0.3)'; ctx.lineWidth = 0.8;
            ctx.beginPath(); ctx.moveTo(x2012, mt); ctx.lineTo(x2012, mt + ch); ctx.stroke();

            ctx.strokeStyle = 'rgba(0,0,0,0.12)'; ctx.lineWidth = 0.5;
            for (var y = TL_MONTHLY_START + 1; y <= CURRENT_YEAR; y++) {
                var yx = xFor(y + '-0');
                ctx.beginPath(); ctx.moveTo(yx, mt); ctx.lineTo(yx, mt + ch); ctx.stroke();
            }

            // Bars: vol mode draws a darker "largest single contributor" slice at the bottom.
            Object.keys(binsR).forEach(function (k) {
                var val = binsR[k];
                if (val <= 0) return;
                var bx = xFor(k), bw = wFor(k);
                var gap = bw > 8 ? 1.5 : bw > 2 ? 0.4 : 0;
                var bxDraw = bx + gap, bwDraw = bw - gap * 2;
                var yTop = yPixel(val);
                var yBot = mt + ch;
                if (useVolTl && binsRMax) {
                    var maxSeg = Math.min(binsRMax[k] || 0, val);
                    var ySplit = yPixel(val - maxSeg);
                    if (ySplit > yTop) {
                        ctx.fillStyle = BAR_COLOR;
                        ctx.fillRect(bxDraw, yTop, bwDraw, ySplit - yTop);
                    }
                    if (yBot > ySplit) {
                        ctx.fillStyle = BAR_COLOR_DARK;
                        ctx.fillRect(bxDraw, ySplit, bwDraw, yBot - ySplit);
                    }
                } else {
                    ctx.fillStyle = BAR_COLOR;
                    if (yBot > yTop) ctx.fillRect(bxDraw, yTop, bwDraw, yBot - yTop);
                }
            });

            // Save hit-test data for mouseover
            _tlHits = [];
            Object.keys(binsR).forEach(function(k) {
                _tlHits.push({
                    key: k,
                    x0: xFor(k), x1: xFor(k) + wFor(k),
                    val: binsR[k],
                    maxEv: useVolTl && binsRMax ? binsRMax[k] : null,
                });
            });

            // Annual average lines across each 2012+ year's monthly columns
            ctx.strokeStyle = 'rgba(30,80,160,0.55)';
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 2]);
            for (var ay = TL_MONTHLY_START; ay <= CURRENT_YEAR; ay++) {
                var sum = 0, cnt = 0;
                for (var am = 0; am < 12; am++) { sum += binsR[ay + '-' + am] || 0; cnt++; }
                var avg = sum / cnt;
                if (avg > 0) {
                    var lx0 = xFor(ay + '-0'), lx1 = xFor(ay + '-0') + 12 * barUnit;
                    var ly  = yPixel(avg);
                    ctx.beginPath(); ctx.moveTo(lx0, ly); ctx.lineTo(lx1, ly); ctx.stroke();
                }
            }
            ctx.setLineDash([]);

            // Axes
            ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
            ctx.beginPath(); ctx.moveTo(ml, mt); ctx.lineTo(ml, mt + ch); ctx.lineTo(ml + cw, mt + ch); ctx.stroke();

            // Y-axis labels
            ctx.fillStyle = '#666'; ctx.font = '8px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
            if (useLog) {
                for (var exp = logMin; exp <= logMax; exp++) {
                    var lvVal = Math.pow(10, exp);
                    var ty = mt + ch - (exp - logMin) / (logMax - logMin) * ch;
                    var lbl;
                    if (useVolTl) {
                        lbl = fmtVolTick(lvVal);
                    } else {
                        lbl = lvVal >= 1 ? (lvVal >= 100 ? String(Math.round(lvVal)) : String(lvVal)) : lvVal.toFixed(-exp);
                    }
                    ctx.fillText(lbl, ml - 3, ty);
                }
            } else {
                for (var v = 0; v <= ymax + 0.001; v += ystep) {
                    ctx.fillText(useVolTl ? fmtVolTick(v) : (v % 1 === 0 ? v : v.toFixed(1)), ml - 3, mt + ch - (v / ymax) * ch);
                }
            }

            // Y-axis title
            ctx.save();
            ctx.translate(8, mt + ch / 2); ctx.rotate(-Math.PI / 2);
            ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#888'; ctx.font = '8px sans-serif';
            ctx.fillText(useVolTl ? 'volume (m³) / month' : 'events / month', 0, 0);
            ctx.restore();

            // X-axis labels
            var yLbl = mt + ch + 5;
            ctx.textBaseline = 'top'; ctx.textAlign = 'center'; ctx.font = '8px sans-serif';

            ctx.fillStyle = '#555';
            ctx.fillText('Holocene', ml + H_U * barUnit / 2, yLbl);
            ctx.fillStyle = '#666'; ctx.font = '7.5px sans-serif';
            ctx.fillText('pre-2000', xModStart + M_U * barUnit / 2, yLbl);

            // Annual 2000-2011 milestones — center on each A_U-wide bar
            ctx.fillStyle = '#777'; ctx.font = '8px sans-serif';
            [2000, 2002, 2004, 2006, 2008, 2010].forEach(function (yr) {
                ctx.fillText(String(yr), xFor(String(yr)) + A_U * barUnit / 2, yLbl);
            });

            // Monthly-era year labels: centered on each year's 12 bins
            // Show every year if there's room (barUnit*12 wide), else every 2 years
            var yearPx = barUnit * 12;
            var yearStep = yearPx >= 22 ? 1 : yearPx >= 11 ? 2 : 3;
            for (var y = TL_MONTHLY_START; y <= CURRENT_YEAR; y += yearStep) {
                var yCx = xFor(y + '-0') + yearPx / 2;
                ctx.fillText(String(y), yCx, yLbl);
            }

            // "more complete" label
            ctx.fillStyle = 'rgba(63,103,177,0.55)'; ctx.font = '7.5px sans-serif'; ctx.textAlign = 'left';
            ctx.fillText('more complete \u25b8', x2012 + 3, mt + 2);

            // "Modern era" span annotation
            var ySpan = mt + ch + 26, tickH = 3.5, spanLabel = 'Modern era';
            ctx.font = '7.5px sans-serif';
            var lblW = ctx.measureText(spanLabel).width + 8;
            var spanMid = (xModStart + x2012) / 2;

            ctx.strokeStyle = '#999'; ctx.lineWidth = 0.8;
            ctx.beginPath(); ctx.moveTo(xModStart, ySpan); ctx.lineTo(spanMid - lblW/2, ySpan); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(spanMid + lblW/2, ySpan); ctx.lineTo(x2012, ySpan); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(xModStart, ySpan - tickH); ctx.lineTo(xModStart, ySpan + tickH); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(x2012,     ySpan - tickH); ctx.lineTo(x2012,     ySpan + tickH); ctx.stroke();
            ctx.strokeStyle = '#aaa';
            ctx.beginPath(); ctx.moveTo(x2000, ySpan - tickH - 2); ctx.lineTo(x2000, ySpan + tickH + 2); ctx.stroke();

            ctx.fillStyle = '#888'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
            ctx.fillText(spanLabel, spanMid, ySpan);
        }

        function updateTimeline() {
            if (!timingPanel || timingPanel.classList.contains('hidden')) return;
            renderTimeline(computeTimeline());
        }

        function onTimingResize() {
            if (timingPanel && !timingPanel.classList.contains('hidden')) renderTimeline(computeTimeline());
        }

        // Listeners for panel-mode checkboxes (refs declared near _filterColdApply).
        if (cbTimingLog) cbTimingLog.addEventListener('change', function () { updateTimeline(); writeUrlState(); });
        if (cbTimingVol) cbTimingVol.addEventListener('change', function () { updateTimeline(); writeUrlState(); });
        if (cbHistLog)   cbHistLog.addEventListener('change',   function () { updateHistogram(); writeUrlState(); });
        if (cbHistVol)   cbHistVol.addEventListener('change',   function () { updateHistogram(); writeUrlState(); });

        // ===========================================================================
        // Canvas mouseover tooltips
        // ===========================================================================
        function doyApprox(doy) {
            var m = 0;
            for (var i = 11; i >= 0; i--) { if (doy >= MONTH_STRT[i]) { m = i; break; } }
            return MONTHS[m] + '\u202f' + (doy - MONTH_STRT[m] + 1);
        }

        var timingCanvasEl = document.getElementById('timing-canvas');
        if (timingCanvasEl) {
            timingCanvasEl.addEventListener('mousemove', function(e) {
                if (!_tlHits) { hideChartTip(); return; }
                var ox = e.offsetX, hit = null;
                for (var i = 0; i < _tlHits.length; i++) {
                    if (ox >= _tlHits[i].x0 && ox < _tlHits[i].x1) { hit = _tlHits[i]; break; }
                }
                if (!hit) { hideChartTip(); return; }
                var k = hit.key, lbl;
                if      (k === 'H')           lbl = 'Holocene';
                else if (k === 'M')           lbl = 'Modern (pre-2000)';
                else if (k.indexOf('-') < 0)  lbl = k;
                else { var p = k.split('-'); lbl = MONTHS[parseInt(p[1])] + ' ' + p[0]; }
                var tip;
                if (cbTimingVol && cbTimingVol.checked) {
                    tip = lbl + ': ' + fmtVolBig(hit.val) + ' / month';
                    if (hit.maxEv > 0) tip += ' \u00b7 largest ' + fmtVolBig(hit.maxEv);
                } else {
                    tip = lbl + ': ' + fmtEvYr(hit.val) + ' ev/month';
                }
                showChartTip(e.clientX, e.clientY, tip);
            });
            timingCanvasEl.addEventListener('mouseleave', hideChartTip);
        }

        var histCanvasEl = document.getElementById('hist-canvas');
        if (histCanvasEl) {
            histCanvasEl.addEventListener('mousemove', function(e) {
                if (!_histHits) { hideChartTip(); return; }
                var ox = e.offsetX, hit = null;
                for (var i = 0; i < _histHits.length; i++) {
                    if (ox >= _histHits[i].x0 && ox < _histHits[i].x1) { hit = _histHits[i]; break; }
                }
                if (!hit) { hideChartTip(); return; }
                var b = hit.b, d0 = b * 10 + 1, d1 = b < 36 ? b * 10 + 10 : 365;
                var rangeLbl = doyApprox(d0) + '\u2013' + doyApprox(d1);
                var tip;
                if (cbHistVol && cbHistVol.checked) {
                    tip = rangeLbl + ': ' + fmtVolBig(hit.val) + ' / 10d';
                    if (hit.maxEv > 0) tip += ' \u00b7 largest ' + fmtVolBig(hit.maxEv);
                } else {
                    tip = rangeLbl + ': ' + (hit.val > 0 ? hit.val.toFixed(2) : '0') + ' ev/10d';
                }
                showChartTip(e.clientX, e.clientY, tip);
            });
            histCanvasEl.addEventListener('mouseleave', hideChartTip);
        }

        return { updateHistogram: updateHistogram, onHistResize: onHistResize, updateTimeline: updateTimeline, onTimingResize: onTimingResize };
    } };
})();
