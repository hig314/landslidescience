// Drop uploader: browser → R2 directly, Django only signs and records.
//
// Uppy 4 (v5+ dropped these callbacks for a server protocol of its own) with
// the AwsS3 plugin driven by our own small API under /drops/<slug>/api/.
// Small files take one presigned PUT; files over the threshold go multipart
// so a dropped connection costs one part, not the file.
//
// "Resume" is deliberately simple: before anything is added we fetch the
// {path: size} map of what the server already has and skip matches. So the
// instruction to an interrupted uploader is just "drop the folder again".
// A big file caught half-way resumes too: before it starts, we ask the
// server whether this very file (path, size, lastModified) has an
// unfinished multipart upload, and if so hand Uppy that uploadId; Uppy then
// lists the parts already stored and sends only the rest.
(async function () {
  const cfg = JSON.parse(document.getElementById('drop-config').textContent);
  const csrf = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '';
  const $ = (id) => document.getElementById(id);
  const errEl = $('drop-err');

  const { Uppy, Dashboard, AwsS3 } = await import(
    `https://releases.transloadit.com/uppy/v${cfg.uppy}/uppy.min.mjs`);

  async function api(path, body) {
    const r = await fetch(cfg.api + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
    if (!r.ok) {
      let msg = r.status + ' ' + r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) { /* not json */ }
      throw new Error(msg);
    }
    return r.json();
  }

  let existing = {};
  try { existing = await api('existing/'); } catch (e) { errEl.textContent = 'Could not reach the server: ' + e.message; return; }

  // The path we store: the folder-relative path the browser gives for a
  // dropped/browsed folder, else the bare filename.
  function relPath(file) {
    const p = file.meta.relativePath || file.name || '';
    return String(p).replace(/^\/+/, '');
  }

  let skipped = 0;
  function showSkipped() {
    const el = $('drop-skipped');
    el.hidden = skipped === 0;
    el.textContent = skipped + ' already received, skipped';
  }

  function fmtBytes(n) {
    if (n < 1024) return n + ' bytes';
    const u = ['KB', 'MB', 'GB', 'TB']; let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n < 10 ? 1 : 0) + ' ' + u[i];
  }
  function showTotals(t) { $('drop-n').textContent = t.n; $('drop-bytes').textContent = fmtBytes(t.bytes); }

  const uppy = new Uppy({
    autoProceed: false,
    allowMultipleUploadBatches: true,
    onBeforeFileAdded(file) {
      const p = relPath(file);
      if (existing[p] === file.size) { skipped++; showSkipped(); return false; }
      file.meta.relpath = p;
      return true;
    },
  });

  uppy.use(Dashboard, {
    inline: true,
    target: '#uppy',
    height: 480,
    width: '100%',
    fileManagerSelectionType: 'both',
    showProgressDetails: true,
    proudlyDisplayPoweredByUppy: false,
    hideProgressAfterFinish: false,
    note: 'Drop a folder here. Nothing is sent until you press Upload.',
    locale: { strings: { dropPasteBoth: 'Drop a folder here, %{browseFiles} or %{browseFolders}' } },
  });

  const chunkSize = (size) => Math.max(cfg.partSize, Math.ceil(size / 10000));
  function mtimeOf(file) {
    const m = file.data && file.data.lastModified;
    return Number.isFinite(m) ? m : null;
  }

  // "Safe so far": bytes of big files whose parts have fully arrived, i.e.
  // what an interruption would not lose. Uppy's own bar counts partial
  // parts too, which is what made an early close look like a resume failure.
  const safe = {};
  function showSafe() {
    const b = Object.values(safe).reduce((a, x) => a + x, 0);
    const el = $('drop-safe');
    el.hidden = b === 0;
    el.textContent = 'Large files: ' + fmtBytes(b) + ' safely stored';
  }
  uppy.on('s3-multipart:part-uploaded', (file, part) => {
    if (!file) return;
    safe[file.id] = Math.min(file.size, (safe[file.id] || 0) + chunkSize(file.size));
    showSafe();
  });
  // Before a batch starts: pick up any unfinished upload of the same file.
  // Setting s3Multipart is what Uppy's own restore path reads (the
  // MultipartUploader treats uploadId + key as "restoring").
  uppy.addPreProcessor(async (fileIDs) => {
    let n = 0, bytes = 0;
    await Promise.all(fileIDs.map(async (id) => {
      const file = uppy.getFile(id);
      if (!file || file.size <= cfg.multipartThreshold || file.s3Multipart?.uploadId) return;
      const mtime = mtimeOf(file);
      if (mtime == null) return;
      try {
        const r = await api('multipart/resume/', { path: file.meta.relpath, size: file.size, mtime });
        if (r.resume) {
          uppy.setFileState(id, { s3Multipart: { uploadId: r.uploadId, key: r.key } });
          if (r.parts) { n++; bytes += r.bytes; safe[id] = r.bytes; }
        }
      } catch (e) { /* no resume: it starts from the beginning */ }
    }));
    if (n) {
      const el = $('drop-resumed');
      el.hidden = false;
      el.textContent = 'Resuming ' + n + ' interrupted file' + (n === 1 ? '' : 's') + ' (' + fmtBytes(bytes) + ' already stored)';
    }
  });

  uppy.use(AwsS3, {
    shouldUseMultipart: (file) => file.size > cfg.multipartThreshold,
    // Deterministic, so a resumed upload is cut into the same parts.
    getChunkSize: (data) => chunkSize(data.size),
    // Requests in flight. Parts share the uplink and count only when whole,
    // so fewer at once means something is safe sooner (see views.py).
    limit: 3,
    getUploadParameters: (file) =>
      api('sign/', { path: file.meta.relpath, type: file.type, size: file.size }),
    createMultipartUpload: (file) =>
      api('multipart/create/', { path: file.meta.relpath, type: file.type, size: file.size, mtime: mtimeOf(file) }),
    signPart: (file, { uploadId, key, partNumber }) =>
      api('multipart/sign/', { uploadId, key, partNumber }),
    listParts: (file, { uploadId, key }) =>
      api('multipart/list/', { uploadId, key }),
    completeMultipartUpload: (file, { uploadId, key, parts }) =>
      api('multipart/complete/', { uploadId, key, parts }),
    abortMultipartUpload: (file, { uploadId, key }) =>
      api('multipart/abort/', { uploadId, key }),
  });

  // The multipart path records server-side on completion; the single-PUT
  // path is confirmed here (the server HEADs the object before trusting it).
  //
  // That confirmation is a second request after the bytes are already safe,
  // and on a flaky connection it is the one that gets lost: the Portage
  // game-cam drop arrived with 37 of 5,183 files stored but never recorded.
  // So it is retried with backoff, and when the batch ends the server lists
  // the bucket itself (`reconcile/`) and adds rows for anything still missing.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let unrecorded = 0;
  async function record(file) {
    const body = { path: file.meta.relpath, size: file.size, type: file.type };
    const delays = [0, 1500, 4000, 10000, 20000];
    for (let i = 0; i < delays.length; i++) {
      if (delays[i]) await sleep(delays[i]);
      try {
        const t = await api('record/', body);
        existing[file.meta.relpath] = file.size;
        showTotals(t);
        return;
      } catch (e) {
        if (i === delays.length - 1) {
          unrecorded++;
          errEl.textContent = 'Stored but not yet recorded: ' + file.meta.relpath + ' (' + e.message + ') — will be picked up when the batch finishes.';
        }
      }
    }
  }
  uppy.on('upload-success', (file) => {
    if (!file || file.size > cfg.multipartThreshold) return;
    record(file);
  });

  uppy.on('complete', async () => {
    // Settle the server's view against storage, whatever happened above.
    for (let i = 0; i < 3; i++) {
      try {
        const t = await api('reconcile/', {});
        showTotals(t);
        if (t.added) errEl.textContent = t.added + ' file' + (t.added === 1 ? '' : 's') + ' recorded from storage.';
        else if (unrecorded) errEl.textContent = '';
        unrecorded = 0;
        return;
      } catch (e) {
        if (i === 2) errEl.textContent = 'Could not confirm the count with the server (' + e.message + ') — reload this page to check.';
        await sleep(3000 * (i + 1));
      }
    }
    api('status/').then(showTotals).catch(() => {});
  });

  uppy.on('upload-error', (file, error) => {
    errEl.textContent = 'Upload failed for ' + (file && file.meta.relpath) + ': ' + (error && error.message || error) + ' — press Retry.';
  });

  // Leaving mid-upload loses the in-flight files, nothing else. Say so.
  window.addEventListener('beforeunload', (e) => {
    const s = uppy.getState();
    if (s.currentUploads && Object.keys(s.currentUploads).length) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
})();
