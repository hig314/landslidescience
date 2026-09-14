/**
 * lidar-tiles -- serve single tiles out of the PMTiles archives in R2, cached per
 * tile at the Cloudflare edge.
 *
 *   GET /t/<archive>/<z>/<x>/<y>.<ext>[?v=<version>]   one tile
 *   GET /t/<archive>.json                              header summary (zooms, bounds, type)
 *
 * <archive> is the file name under pmtiles/ in the bucket without the extension
 * (`kbay_2023`, `kbay_2023_slope`, `ctx_3dep`). `v` is the archive's R2 ETag,
 * which the catalog puts in each URL, so a re-uploaded archive gets fresh URLs
 * and every cached tile can be immutable. A request whose `v` does not match the
 * archive actually in the bucket is answered but not cached, so a catalog that
 * runs ahead of (or behind) an upload can never pin wrong tiles in the cache.
 *
 * WHY (2026-09-13): the browser used to read the archives directly with HTTP
 * range requests. Those bypass Cloudflare's cache (objects over 512 MB cannot be
 * cached, and a range is not a cacheable unit anyway), so every tile of every
 * view cost a round trip to R2: ~0.17 s from anywhere, 0.4-1 s from a slow
 * Alaskan link on a new connection, plus the directory lookups the client had
 * to make first. Here the directory walk happens next to R2, and the finished
 * tile is stored in the edge cache of whichever colo served it, so the second
 * view of an area -- by anyone -- is answered by the edge alone.
 *
 * Missing tiles answer 204 (cached like a tile): the demshade client treats
 * 204/404 as "no data here". Gated surveys are never uploaded to the bucket, so
 * there is nothing to protect here beyond the CORS allowlist, which mirrors the
 * bucket's own policy.
 */
import { EtagMismatch, PMTiles, TileType, type RangeResponse, type Source } from 'pmtiles';

export interface Env {
  /** R2 binding to the lidar bucket (production). */
  BUCKET?: R2Bucket;
  /** Dev/test fallback when there is no binding: public base URL of the bucket. */
  ORIGIN?: string;
  /** Comma-separated origins allowed to read tiles cross-origin. `*` allows all. */
  ALLOWED_ORIGINS?: string;
}

/** The edge cache as the handler uses it; `caches.default` in production. */
export interface TileCache {
  match(key: Request): Promise<Response | undefined>;
  put(key: Request, response: Response): Promise<void>;
}

const NAME_RE = /^[a-z0-9][a-z0-9_]{0,63}$/;
const TILE_RE = /^\/t\/([a-z0-9][a-z0-9_]{0,63})\/(\d{1,2})\/(\d{1,8})\/(\d{1,8})\.(png|webp|jpg|pbf|mvt)$/;
const JSON_RE = /^\/t\/([a-z0-9][a-z0-9_]{0,63})\.json$/;
const IMMUTABLE = 'public, max-age=31536000, immutable';
const SHORT = 'public, max-age=3600';

/** Range reads straight from the bucket binding: no HTTP, no public-domain hop. */
export class R2Source implements Source {
  constructor(private readonly bucket: R2Bucket, private readonly key: string) {}
  getKey(): string {
    return this.key;
  }
  async getBytes(offset: number, length: number, _signal?: AbortSignal, etag?: string): Promise<RangeResponse> {
    const obj = await this.bucket.get(this.key, {
      range: { offset, length },
      onlyIf: etag ? { etagMatches: etag } : undefined,
    });
    if (!obj) throw new ArchiveMissing(this.key);
    // A failed precondition returns the object's metadata without a body:
    // the archive was replaced under us, so pmtiles drops its cached
    // directories and retries once.
    if (!('body' in obj) || !obj.body) throw new EtagMismatch();
    return { data: await (obj as R2ObjectBody).arrayBuffer(), etag: obj.etag };
  }
}

/** Dev fallback: plain HTTP range requests against the public bucket URL. */
class HttpSource implements Source {
  constructor(private readonly url: string) {}
  getKey(): string {
    return this.url;
  }
  async getBytes(offset: number, length: number): Promise<RangeResponse> {
    const r = await fetch(this.url, { headers: { Range: `bytes=${offset}-${offset + length - 1}` } });
    if (r.status === 404) throw new ArchiveMissing(this.url);
    if (r.status !== 206 && r.status !== 200) throw new Error(`origin ${r.status}`);
    return { data: await r.arrayBuffer(), etag: r.headers.get('etag') ?? undefined };
  }
}

export class ArchiveMissing extends Error {}

/** ETags arrive as `abc`, `"abc"` or `W/"abc"` depending on the path; compare the core. */
export function normEtag(t?: string | null): string {
  return (t ?? '').trim().replace(/^W\//, '').replace(/^"|"$/g, '');
}

// Readers survive across requests within an isolate: header and directories
// are then fetched once per isolate, not once per tile.
const readers = new Map<string, PMTiles>();
const MAX_READERS = 64;

function reader(env: Env, name: string): PMTiles {
  let pm = readers.get(name);
  if (pm) return pm;
  const key = `pmtiles/${name}.pmtiles`;
  let source: Source;
  if (env.BUCKET) source = new R2Source(env.BUCKET, key);
  else if (env.ORIGIN) source = new HttpSource(`${env.ORIGIN.replace(/\/+$/, '')}/${key}`);
  else throw new Error('lidar-tiles: neither BUCKET nor ORIGIN configured');
  pm = new PMTiles(source);
  if (readers.size >= MAX_READERS) readers.delete(readers.keys().next().value as string);
  readers.set(name, pm);
  return pm;
}

const CONTENT_TYPE: Record<number, string> = {
  [TileType.Png]: 'image/png',
  [TileType.Webp]: 'image/webp',
  [TileType.Jpeg]: 'image/jpeg',
  [TileType.Mvt]: 'application/x-protobuf',
  [TileType.Avif]: 'image/avif',
};

function corsHeaders(env: Env, request: Request): Record<string, string> {
  const origin = request.headers.get('Origin');
  const allowed = (env.ALLOWED_ORIGINS ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  const h: Record<string, string> = { Vary: 'Origin' };
  if (allowed.includes('*')) h['Access-Control-Allow-Origin'] = '*';
  else if (origin && allowed.includes(origin)) h['Access-Control-Allow-Origin'] = origin;
  return h;
}

function withCors(resp: Response, env: Env, request: Request, extra: Record<string, string> = {}): Response {
  const out = new Response(request.method === 'HEAD' ? null : resp.body, resp);
  for (const [k, v] of Object.entries({ ...corsHeaders(env, request), ...extra })) out.headers.set(k, v);
  return out;
}

export async function handle(request: Request, env: Env, cache: TileCache | null, waitUntil: (p: Promise<unknown>) => void): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: {
        ...corsHeaders(env, request),
        'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
        'Access-Control-Max-Age': '86400',
      },
    });
  }
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response('method not allowed', { status: 405 });
  }

  const tm = TILE_RE.exec(url.pathname);
  const jm = tm ? null : JSON_RE.exec(url.pathname);
  if (!tm && !jm) return withCors(new Response('not found', { status: 404 }), env, request);
  const name = (tm ?? jm)![1];
  if (!NAME_RE.test(name)) return withCors(new Response('bad archive name', { status: 400 }), env, request);

  // Cache key: path plus the version token only, so no other query noise or
  // header splits the cache. CORS is added per request, never stored.
  const v = url.searchParams.get('v');
  const keyUrl = `${url.origin}${url.pathname}${v ? `?v=${encodeURIComponent(v)}` : ''}`;
  const cacheKey = new Request(keyUrl, { method: 'GET' });
  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      // Empty tiles are stored as a 200 marker (the Cache API will not keep a
      // 204) and turned back into 204 on the way out.
      if (hit.headers.get('X-Tile-Empty') === '1') {
        return withCors(new Response(null, { status: 204, headers: { 'Cache-Control': hit.headers.get('Cache-Control') ?? SHORT } }),
                        env, request, { 'X-Tile-Cache': 'HIT' });
      }
      return withCors(hit, env, request, { 'X-Tile-Cache': 'HIT' });
    }
  }

  let resp: Response;
  let stale = false;
  try {
    const pm = reader(env, name);
    if (jm) {
      const h = await pm.getHeader();
      resp = Response.json(
        {
          minzoom: h.minZoom, maxzoom: h.maxZoom,
          bounds: [h.minLon, h.minLat, h.maxLon, h.maxLat],
          center: [h.centerLon, h.centerLat, h.centerZoom],
          tileType: h.tileType, tiles: h.numAddressedTiles,
        },
        { headers: { 'Cache-Control': SHORT } },
      );
    } else {
      const z = Number(tm![2]), x = Number(tm![3]), y = Number(tm![4]);
      if (z > 30 || x >= 2 ** z || y >= 2 ** z) {
        return withCors(new Response('tile out of range', { status: 400 }), env, request);
      }
      const [h, t] = await Promise.all([pm.getHeader(), pm.getZxy(z, x, y)]);
      const current = normEtag(h.etag);
      stale = !!v && !!current && normEtag(v) !== current;
      const cc = v && !stale ? IMMUTABLE : stale ? 'no-store' : SHORT;
      resp = t
        ? new Response(t.data, { headers: { 'Content-Type': CONTENT_TYPE[h.tileType] ?? 'application/octet-stream', 'Cache-Control': cc } })
        : new Response(null, { status: 204, headers: { 'Cache-Control': cc } });
    }
  } catch (e) {
    if (e instanceof ArchiveMissing) {
      readers.delete(name);
      return withCors(new Response('no such archive', { status: 404, headers: { 'Cache-Control': 'public, max-age=60' } }), env, request);
    }
    readers.delete(name);
    return withCors(new Response(`upstream error: ${(e as Error).message}`, { status: 502 }), env, request);
  }

  if (cache && request.method === 'GET' && !stale) {
    const stored = resp.status === 204
      ? new Response('', { status: 200, headers: { 'Cache-Control': resp.headers.get('Cache-Control') ?? SHORT, 'X-Tile-Empty': '1' } })
      : resp.clone();
    waitUntil(cache.put(cacheKey, stored));
  }
  return withCors(resp, env, request, { 'X-Tile-Cache': 'MISS' });
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return handle(request, env, caches.default as unknown as TileCache, (p) => ctx.waitUntil(p));
  },
};
