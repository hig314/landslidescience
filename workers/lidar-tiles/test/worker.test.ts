import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, statSync } from 'node:fs';
import { PMTiles, type Source, type RangeResponse } from 'pmtiles';
import { handle, type Env, type TileCache } from '../src/index';

// A real archive from the local build, served through a fake R2 binding.
const ARCHIVE = '/Users/Hig/Claude_projects/landslidescience/data/lidar/pmtiles/juneau_thane_2019.pmtiles';
const buf = readFileSync(ARCHIVE);

class FileSource implements Source {
  getKey() { return 'f'; }
  async getBytes(o: number, l: number): Promise<RangeResponse> {
    return { data: buf.buffer.slice(buf.byteOffset + o, buf.byteOffset + o + l) as ArrayBuffer };
  }
}

let gets = 0;
const bucket = {
  async get(key: string, opts: { range: { offset: number; length: number } }) {
    gets++;
    if (key !== 'pmtiles/juneau_thane_2019.pmtiles') return null;
    const { offset, length } = opts.range;
    const data = buf.buffer.slice(buf.byteOffset + offset, buf.byteOffset + offset + length);
    return { etag: 'e1-3', body: {}, arrayBuffer: async () => data };
  },
} as unknown as R2Bucket;

const env: Env = { BUCKET: bucket, ALLOWED_ORIGINS: 'https://landslidescience.org,http://localhost:8001' };

class MemCache implements TileCache {
  store = new Map<string, Response>();
  async match(k: Request) { const r = this.store.get(k.url); return r ? r.clone() : undefined; }
  async put(k: Request, r: Response) { this.store.set(k.url, r); }
}

let tile: { z: number; x: number; y: number };
let expected: ArrayBuffer;
beforeAll(async () => {
  const pm = new PMTiles(new FileSource());
  const h = await pm.getHeader();
  // first tile that exists at a mid zoom, scanning the header bounds
  const z = h.maxZoom - 3, n = 2 ** z;
  const tx = (lon: number) => Math.floor(((lon + 180) / 360) * n);
  const ty = (la: number) => { const r = (la * Math.PI) / 180; return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n); };
  // the archives carry world-default header bounds, so take the footprint from the catalog
  const cat = JSON.parse(readFileSync('/Users/Hig/Claude_projects/landslidescience/data/lidar/catalog.geojson', 'utf8'));
  const f = cat.features.find((q: { properties: { id: string } }) => q.properties.id === 'juneau_thane_2019');
  const pts: number[][] = f.geometry.coordinates.flat(2);
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  outer: for (let x = tx(Math.min(...lons)); x <= tx(Math.max(...lons)); x++) {
    for (let y = ty(Math.max(...lats)); y <= ty(Math.min(...lats)); y++) {
      const t = await pm.getZxy(z, x, y);
      if (t) { tile = { z, x, y }; expected = t.data; break outer; }
    }
  }
});

const run = (path: string, cache: TileCache | null, origin?: string, method = 'GET') => {
  const pending: Promise<unknown>[] = [];
  const req = new Request(`https://tiles.test${path}`, { method, headers: origin ? { Origin: origin } : {} });
  return handle(req, env, cache, (p) => pending.push(p)).then(async (r) => { await Promise.all(pending); return r; });
};

describe('lidar-tiles', () => {
  it('serves the same bytes pmtiles reads from the archive, with CORS for allowed origins', async () => {
    const r = await run(`/t/juneau_thane_2019/${tile.z}/${tile.x}/${tile.y}.png?v=e1-3`, null, 'http://localhost:8001');
    expect(r.status).toBe(200);
    expect(r.headers.get('content-type')).toBe('image/png');
    expect(r.headers.get('access-control-allow-origin')).toBe('http://localhost:8001');
    expect(r.headers.get('cache-control')).toContain('immutable');
    expect(Buffer.from(await r.arrayBuffer()).equals(Buffer.from(expected))).toBe(true);
  });

  it('answers 204 for a tile the archive does not have, from the cache too', async () => {
    const cache = new MemCache();
    const r = await run(`/t/juneau_thane_2019/${tile.z}/0/0.png?v=e1-3`, cache);
    expect(r.status).toBe(204);
    const again = await run(`/t/juneau_thane_2019/${tile.z}/0/0.png?v=e1-3`, cache);
    expect(again.status).toBe(204);
    expect(again.headers.get('x-tile-cache')).toBe('HIT');
  });

  it('caches per tile: the second request never touches the bucket', async () => {
    const cache = new MemCache();
    const path = `/t/juneau_thane_2019/${tile.z}/${tile.x}/${tile.y}.png?v=e1-3`;
    const a = await run(path, cache);
    expect(a.headers.get('x-tile-cache')).toBe('MISS');
    const before = gets;
    const b = await run(`${path}&junk=1`, cache, 'https://landslidescience.org');
    expect(b.headers.get('x-tile-cache')).toBe('HIT');
    expect(gets).toBe(before);
    expect(b.headers.get('access-control-allow-origin')).toBe('https://landslidescience.org');
  });

  it('refuses unlisted origins, bad names and unknown archives', async () => {
    const r = await run(`/t/juneau_thane_2019/${tile.z}/${tile.x}/${tile.y}.png`, null, 'https://evil.example');
    expect(r.headers.get('access-control-allow-origin')).toBeNull();
    expect((await run('/t/../x/1/0/0.png', null)).status).toBe(404);
    expect((await run('/t/Nope/1/0/0.png', null)).status).toBe(404);
    expect((await run('/t/nope/1/0/0.png', null)).status).toBe(404);
    expect((await run('/t/juneau_thane_2019/3/9/0.png', null)).status).toBe(400);
  });

  it('caches only when v matches the archive in the bucket', async () => {
    const cache = new MemCache();
    const good = await run(`/t/juneau_thane_2019/${tile.z}/${tile.x}/${tile.y}.png?v=e1-3`, cache);
    expect(good.headers.get('cache-control')).toContain('immutable');
    expect(cache.store.size).toBe(1);
    const wrong = await run(`/t/juneau_thane_2019/${tile.z}/${tile.x}/${tile.y}.png?v=old-9`, cache);
    expect(wrong.status).toBe(200);
    expect(wrong.headers.get('cache-control')).toBe('no-store');
    expect(cache.store.size).toBe(1);
  });

  it('describes an archive at /t/<name>.json', async () => {
    const r = await run('/t/juneau_thane_2019.json', null);
    const j = (await r.json()) as { maxzoom: number; bounds: number[] };
    expect(j.maxzoom).toBeGreaterThan(10);
    expect(j.bounds[0]).toBeLessThan(-134);
  });

  it('answers CORS preflight', async () => {
    const r = await run('/t/juneau_thane_2019/1/0/0.png', null, 'http://localhost:8001', 'OPTIONS');
    expect(r.status).toBe(204);
    expect(r.headers.get('access-control-allow-methods')).toContain('GET');
  });
});
