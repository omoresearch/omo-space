// Injected into kino.ai pages in the Omo wall browser (MCEF) before any page script runs.
// 1) Recreates the Kino desktop app's `window.electron` bridge; calls/subscriptions are relayed through
//    kino_native.py to the real Kino desktop app (Electron main process, via its own window).
// 2) Local files WITHOUT real file-system handles: MCEF kills the whole game when it is handed a directory
//    handle, so Kino gets emulated FileSystem*Handle objects backed by kino_fs.py (http://127.0.0.1:8865).
(() => {
  if (window.top !== window || location.hostname !== 'kino.ai' || window.electron) return;
  const SCHEMA = __SCHEMA__;
  const FS = __FSBASE__, TOKEN = __FSTOKEN__;
  const out = m => { try { window.__omoKinoOut(JSON.stringify(m)); } catch (e) { console.warn('[omo-kino] bridge down', e); } };
  let seq = 0;
  const pend = new Map(), subs = new Map();
  window.__omoKinoIn = m => {
    if (m.t === 'res') {
      const p = pend.get(m.id);
      if (!p) return;
      pend.delete(m.id);
      if (m.err) p.rej(Object.assign(new Error(m.err.message || 'Kino desktop call failed'), { name: m.err.name || 'Error', code: m.err.code }));
      else p.res(m.val === null && m.undef ? undefined : m.val);
    } else if (m.t === 'ev') {
      const cb = subs.get(m.sid);
      if (cb) try { cb(...m.args); } catch (e) { console.error(e); }
    }
  };

  // ---------------------------------------------------------------- emulated local file system (kino_fs.py)
  const filePaths = new WeakMap();                       // File objects we made -> absolute path
  const fsErr = (name, msg) => new DOMException(msg || name, name);
  const qs = o => Object.entries(o).filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
  async function fsx(op, params = {}, { method = 'GET', body, raw = false } = {}) {
    let r;
    try {
      r = await fetch(`${FS}/fs/${op}?${qs(params)}`, { method, body, headers: { 'X-Omo-Kino': TOKEN }, cache: 'no-store' });
    } catch (e) { throw fsErr('NotReadableError', 'Kino local file bridge unreachable: ' + e.message); }
    if (!r.ok) {
      let j = {};
      try { j = await r.json(); } catch (e) {}
      throw fsErr(j.error === 'TypeError' ? 'TypeMismatchError' : (j.error || 'UnknownError'), j.message || ('HTTP ' + r.status));
    }
    return raw ? r : r.json();
  }
  const H = window.FileSystemHandle, DH = window.FileSystemDirectoryHandle, FH = window.FileSystemFileHandle;
  const WS = window.FileSystemWritableFileStream;
  class OmoHandle {
    constructor(abs, name, kind) {
      Object.defineProperty(this, '__omoPath', { value: abs, configurable: true });
      Object.defineProperty(this, '__omoKind', { value: kind });
      Object.defineProperty(this, '__omoName', { value: name, configurable: true });
    }
    get kind() { return this.__omoKind; }
    get name() { return this.__omoName; }
    async isSameEntry(o) { return !!o && o.__omoPath === this.__omoPath; }
    async queryPermission() { return 'granted'; }
    async requestPermission() { return 'granted'; }
    async remove(opts = {}) {
      const dir = this.__omoPath.replace(/\/[^/]+\/?$/, '') || '/';
      await fsx('remove', { p: dir, name: this.name, recursive: opts.recursive ? 1 : 0 }, { method: 'POST' });
    }
    async move(a, b) {
      let dir, name;
      if (typeof a === 'string') name = a; else { dir = a && a.__omoPath; name = b; }
      const r = await fsx('move', { p: this.__omoPath, dir, name }, { method: 'POST' });
      Object.defineProperty(this, '__omoPath', { value: r.abs, configurable: true });
      Object.defineProperty(this, '__omoName', { value: r.name, configurable: true });
    }
  }
  class OmoDir extends OmoHandle {
    constructor(abs, name) { super(abs, name, 'directory'); }
    async getDirectoryHandle(name, o = {}) {
      const r = await fsx('child', { p: this.__omoPath, name, kind: 'directory', create: o.create ? 1 : 0 }, { method: 'POST' });
      return new OmoDir(r.abs, r.name);
    }
    async getFileHandle(name, o = {}) {
      const r = await fsx('child', { p: this.__omoPath, name, kind: 'file', create: o.create ? 1 : 0 }, { method: 'POST' });
      return new OmoFile(r.abs, r.name);
    }
    async removeEntry(name, o = {}) {
      await fsx('remove', { p: this.__omoPath, name, recursive: o.recursive ? 1 : 0 }, { method: 'POST' });
    }
    async resolve(h) {
      if (!h || !h.__omoPath) return null;
      if (h.__omoPath === this.__omoPath) return [];
      const base = this.__omoPath.replace(/\/$/, '') + '/';
      return h.__omoPath.startsWith(base) ? h.__omoPath.slice(base.length).split('/') : null;
    }
    async *entries() {
      const list = await fsx('list', { p: this.__omoPath });
      const base = this.__omoPath.replace(/\/$/, '');
      for (const [n, k] of list) yield [n, k === 'directory' ? new OmoDir(base + '/' + n, n) : new OmoFile(base + '/' + n, n)];
    }
    async *keys() { for await (const [n] of this.entries()) yield n; }
    async *values() { for await (const [, h] of this.entries()) yield h; }
    [Symbol.asyncIterator]() { return this.entries(); }
  }
  class OmoFile extends OmoHandle {
    constructor(abs, name) { super(abs, name, 'file'); }
    async getFile() {
      const r = await fsx('read', { p: this.__omoPath }, { raw: true });
      const blob = await r.blob();
      const f = new File([blob], this.name, { type: blob.type === 'application/octet-stream' ? '' : blob.type,
                                              lastModified: Number(r.headers.get('X-Mtime')) || Date.now() });
      filePaths.set(f, this.__omoPath);
      return f;
    }
    async createWritable(o = {}) {
      const { wid, size } = await fsx('wopen', { p: this.__omoPath, keep: o.keepExistingData ? 1 : 0 }, { method: 'POST' });
      let pos = 0, len = size, open = true;
      const put = async (data, at) => {
        let body;
        if (typeof data === 'string') body = new TextEncoder().encode(data);
        else if (data instanceof Blob || ArrayBuffer.isView(data)) body = data;
        else if (data instanceof ArrayBuffer) body = new Uint8Array(data);
        else throw new TypeError('Unsupported data for write()');
        const n = body instanceof Blob ? body.size : body.byteLength;
        const p = at ?? pos;
        await fsx('wwrite', { wid, pos: p }, { method: 'PUT', body });
        pos = p + n; len = Math.max(len, pos);
      };
      const command = async c => {
        if (!open) throw fsErr('InvalidStateError', 'stream is closed');
        if (c && typeof c === 'object' && !(c instanceof Blob) && !ArrayBuffer.isView(c) && !(c instanceof ArrayBuffer) && 'type' in c) {
          if (c.type === 'write') return put(c.data, c.position ?? undefined);
          if (c.type === 'seek') { pos = c.position; return; }
          if (c.type === 'truncate') { await fsx('wtrunc', { wid, size: c.size }, { method: 'POST' }); len = c.size; if (pos > len) pos = len; return; }
          throw new TypeError('bad write command');
        }
        return put(c);
      };
      const finish = async () => { if (open) { open = false; await fsx('wclose', { wid }, { method: 'POST' }); } };
      const abort = async () => { if (open) { open = false; await fsx('wabort', { wid }, { method: 'POST' }).catch(() => {}); } };
      const stream = new WritableStream({ write: command, close: finish, abort });
      const viaWriter = f => async (...a) => { const w = stream.getWriter(); try { return await f(w, ...a); } finally { w.releaseLock(); } };
      Object.defineProperties(stream, {
        write: { value: viaWriter((w, d) => w.write(d)) },
        seek: { value: viaWriter((w, p) => w.write({ type: 'seek', position: p })) },
        truncate: { value: viaWriter((w, s) => w.write({ type: 'truncate', size: s })) },
        close: { value: viaWriter(w => w.close()) },
        abort: { value: async r => { if (stream.locked) return abort(); const w = stream.getWriter(); try { await w.abort(r); } finally { w.releaseLock(); } } },
      });
      return stream;
    }
    async createSyncAccessHandle() { throw fsErr('NotSupportedError', 'sync access handles are not available on the wall'); }
  }
  if (H) Object.setPrototypeOf(OmoHandle.prototype, H.prototype);   // instanceof FileSystemHandle
  for (const [Real, Omo] of [[DH, OmoDir], [FH, OmoFile], [WS, null]]) {  // instanceof FileSystem{Directory,File}Handle
    if (!Real || !Omo) continue;
    const orig = Function.prototype[Symbol.hasInstance];
    Object.defineProperty(Real, Symbol.hasInstance, { value: v => v instanceof Omo || orig.call(Real, v), configurable: true });
  }
  const isOmo = v => v instanceof OmoHandle;
  // Handles in IndexedDB (Kino remembers media files): store a marker, revive it on read.
  const MARK = '__omoKinoHandle';
  const plain = v => v && typeof v === 'object' && Object.getPrototypeOf(v) === Object.prototype;
  const pack = v => {
    if (isOmo(v)) return { [MARK]: 1, kind: v.kind, path: v.__omoPath, name: v.name };
    if (Array.isArray(v)) { let c = false; const a = v.map(x => { const y = pack(x); if (y !== x) c = true; return y; }); return c ? a : v; }
    if (plain(v)) { let c = false; const o = {}; for (const k of Object.keys(v)) { const y = pack(v[k]); if (y !== v[k]) c = true; o[k] = y; } return c ? o : v; }
    return v;
  };
  const unpack = v => {
    if (!v || typeof v !== 'object') return v;
    if (v[MARK] === 1 && v.path) return v.kind === 'directory' ? new OmoDir(v.path, v.name) : new OmoFile(v.path, v.name);
    if (Array.isArray(v)) { let c = false; const a = v.map(x => { const y = unpack(x); if (y !== x) c = true; return y; }); return c ? a : v; }
    if (plain(v)) { let c = false; const o = {}; for (const k of Object.keys(v)) { const y = unpack(v[k]); if (y !== v[k]) c = true; o[k] = y; } return c ? o : v; }
    return v;
  };
  for (const m of ['put', 'add']) {
    const orig = IDBObjectStore.prototype[m];
    IDBObjectStore.prototype[m] = function (value, ...rest) { return orig.call(this, pack(value), ...rest); };
  }
  for (const [proto, prop] of [[IDBRequest.prototype, 'result'], [IDBCursorWithValue.prototype, 'value']]) {
    const d = Object.getOwnPropertyDescriptor(proto, prop);
    if (d && d.get) Object.defineProperty(proto, prop, { ...d, get() { return unpack(d.get.call(this)); } });
  }
  // FileSystemObserver can't watch an emulated handle: accept it and stay quiet (changes Kino makes itself are
  // known to it; edits made outside Kino show after a reload / "Retry filesystem sync").
  if (window.FileSystemObserver) {
    const P = window.FileSystemObserver.prototype, obs = P.observe, unobs = P.unobserve;
    P.observe = function (h, ...r) { return isOmo(h) ? Promise.resolve() : obs.call(this, h, ...r); };
    if (unobs) P.unobserve = function (h, ...r) { return isOmo(h) ? undefined : unobs.call(this, h, ...r); };
  }
  // Never let the page open a real directory picker: on MCEF that kills the game.
  window.showDirectoryPicker = async () => { throw fsErr('AbortError', 'Folder pickers are not available on the wall.'); };

  // What the desktop app's dispatchFileDrop does (an OS drop carrying a real handle), with an emulated handle.
  async function fileDrop(selector, path, dropId) {
    const g = await fsx('grant', {}, { method: 'POST', body: JSON.stringify({ path }) });
    const handle = g.kind === 'directory' ? new OmoDir(g.abs, g.name) : new OmoFile(g.abs, g.name);
    const target = (selector && document.querySelector(selector)) || document.body;
    const item = { kind: 'file', type: '', getAsFileSystemHandle: async () => handle, getAsFile: () => null, getAsString: () => {} };
    const dt = { types: ['Files', 'application/x-kino-drop-id'], files: [], items: [item], dropEffect: 'copy', effectAllowed: 'all',
                 getData: t => (t === 'application/x-kino-drop-id' ? dropId : ''), setData() {}, clearData() {} };
    const ev = new Event('drop', { bubbles: true, cancelable: true, composed: true });
    Object.defineProperty(ev, 'dataTransfer', { value: dt });
    setTimeout(() => target.dispatchEvent(ev), 0);
    return { absolutePath: g.abs };
  }
  async function authorizeLocalMedia(abs) {
    const g = await fsx('grant', {}, { method: 'POST', body: JSON.stringify({ path: abs }) });
    return `${FS}/fs/read?${qs({ p: g.abs, t: TOKEN })}`;
  }
  window.__omoKinoFs = { OmoDir, OmoFile, fileDrop };

  // ---------------------------------------------------------------- editing proxies (no H.264/AAC here)
  // The wall Chromium can't decode OR encode H.264/AAC, so Kino can't build its own editing proxies
  // (OPFS proxy-<mediaId>.mp4 / .m4a, normally H.264+AAC). Before Kino looks for them we drop in VP9+Opus
  // proxies made by kino_fs (ffmpeg, cached); Kino's reuse check (demux + duration) accepts those.
  const AUD = new Set(['m4a', 'aac', 'mp3', 'wav', 'flac', 'ogg']);
  const VID = new Set(['mp4', 'mov', 'm4v', 'mkv', 'webm']);
  const realGetDir = navigator.storage && navigator.storage.getDirectory && navigator.storage.getDirectory.bind(navigator.storage);
  const idbRows = () => new Promise(res => {
    const q = indexedDB.open('kino-local-file-paths', 1);
    q.onupgradeneeded = () => q.result.createObjectStore('paths');
    q.onerror = () => res([]);
    q.onsuccess = () => {
      const db = q.result, s = db.transaction('paths').objectStore('paths');
      const k = s.getAllKeys(), v = s.getAll();
      v.onsuccess = () => { db.close(); res(k.result.map((id, i) => [id, v.result[i]]).filter(r => typeof r[1] === 'string')); };
      v.onerror = () => res([]);
    };
  });
  const prefill = (async () => {
    if (!realGetDir) return;
    const t0 = performance.now();
    let added = 0, pending = 0;
    try {
      const root = await realGetDir();
      const todo = [];
      for (const [id, p] of await idbRows()) {
        const ext = (p.split('.').pop() || '').toLowerCase();
        const kind = AUD.has(ext) ? 'm4a' : VID.has(ext) ? 'mp4' : null;
        if (!kind) continue;
        const name = `proxy-${id}.${kind}`;
        try { if ((await (await root.getFileHandle(name)).getFile()).size > 0) continue; } catch (e) {}
        todo.push([name, p]);
      }
      // grants these media paths (the ones the desktop app already knows) and queues their conversion
      if (todo.length) await fsx('warm', {}, { method: 'POST', body: JSON.stringify(todo.map(t => t[1])) });
      for (const [name, p] of todo) {
        const r = await fetch(`${FS}/fs/read?${qs({ p, t: TOKEN, edit: 1, nowait: 1 })}`).catch(() => null);
        if (!r || !r.ok) { if (r && r.status === 503) pending++; continue; }
        const blob = await r.blob();
        const w = await (await root.getFileHandle(name, { create: true })).createWritable();
        await w.write(blob); await w.close(); added++;
      }
    } catch (e) { console.warn('[omo-kino] proxy prefill failed', e); }
    console.info(`[omo-kino] editing proxies: ${added} added, ${pending} still converting (${Math.round(performance.now() - t0)} ms)`);
  })();
  if (realGetDir) {
    // Kino's proxy lookup starts with getDirectory(): let it wait until the prefill is in
    // one shared gate: open once the prefill is done or 15 s after page start, whichever comes first
    const gate = Promise.race([prefill, new Promise(r => setTimeout(r, 15000))]);
    navigator.storage.getDirectory = () => gate.then(realGetDir);
  }

  // ---------------------------------------------------------------- window.electron relay
  const clean = a => {
    if (a instanceof File || a instanceof Blob) return null;
    if (Array.isArray(a)) return a.map(clean);
    return a;
  };
  const LOCAL = {
    dispatchFileDrop: fileDrop,
    'fileSystem.authorizeLocalMedia': authorizeLocalMedia,
    getPathForFile: f => filePaths.get(f) || '',
    getPathsForFiles: files => Array.from(files || [], f => filePaths.get(f) || ''),
  };
  const fn = path => {
    if (LOCAL[path]) return LOCAL[path];
    if (path === 'performHaptic' || path === 'menuBar.publish') return (...args) => out({ t: 'call', id: 0, path, args: args.map(clean) });
    return (...args) => {
      const cbIdx = args.findIndex(a => typeof a === 'function');
      if (cbIdx >= 0) {
        const sid = 's' + (++seq);
        subs.set(sid, args[cbIdx]);
        const rest = args.map((a, i) => (i === cbIdx ? { __cb: sid } : clean(a)));
        out({ t: 'sub', sid, path, args: rest });
        return () => { if (subs.delete(sid)) out({ t: 'unsub', sid }); };
      }
      const id = ++seq;
      return new Promise((res, rej) => { pend.set(id, { res, rej }); out({ t: 'call', id, path, args: args.map(clean) }); });
    };
  };
  const build = (node, prefix) => {
    const o = {};
    for (const [k, v] of Object.entries(node)) {
      const p = prefix ? prefix + '.' + k : k;
      if (v === '__fn__') o[k] = fn(p);
      else if (v && typeof v === 'object' && !Array.isArray(v)) o[k] = build(v, p);
      else o[k] = v;
    }
    return Object.freeze(o);
  };
  Object.defineProperty(window, 'electron', { value: build(SCHEMA, ''), configurable: false, enumerable: true, writable: false });
  window.addEventListener('pagehide', () => out({ t: 'bye' }));
  console.info('[omo-kino] desktop bridge + local file bridge ready');
})();
