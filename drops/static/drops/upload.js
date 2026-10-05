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

  uppy.use(AwsS3, {
    shouldUseMultipart: (file) => file.size > cfg.multipartThreshold,
    limit: 6,
    getUploadParameters: (file) =>
      api('sign/', { path: file.meta.relpath, type: file.type, size: file.size }),
    createMultipartUpload: (file) =>
      api('multipart/create/', { path: file.meta.relpath, type: file.type, size: file.size }),
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
  uppy.on('upload-success', (file) => {
    if (!file || file.size > cfg.multipartThreshold) return;
    api('record/', { path: file.meta.relpath, size: file.size, type: file.type })
      .then((t) => { existing[file.meta.relpath] = file.size; showTotals(t); })
      .catch((e) => {
        errEl.textContent = 'Stored but not recorded: ' + file.meta.relpath + ' (' + e.message + ') — drop the folder again later and it will be picked up.';
      });
  });

  uppy.on('complete', () => {
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
