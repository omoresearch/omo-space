#!/usr/bin/env python3
"""Native Kino on an Omo wall.

The wall's own Chromium (MCEF) loads https://kino.ai directly (crisp, native rendering, real input). What the plain
web page lacks is the desktop app's `window.electron` bridge (local files, embedded Claude/Codex agents, MCP install,
YouTube download, CapCut import...). This relay gives it back:

  MCEF page  --(shim.js + CDP binding __omoKinoOut)-->  kino_native.py  --(CDP)-->  real Kino desktop app window
             <--(Runtime.evaluate __omoKinoIn)-------                  <--(binding __kinoRelay)--

Every `window.electron.*` call in the wall runs on the real app's own `window.electron` (same main process, same
IPC handlers), subscriptions forward their events back. The wall also gets the desktop app's user agent and, once,
its kino.ai login cookies, so it opens logged in.

Env: KINO_CDP (9337, the Kino desktop app's --remote-debugging-port), WALL_CDP (9338, MCEF's), KINO_COOKIES (1).
"""
import asyncio, itertools, json, os, sys, time, urllib.request
from pathlib import Path

import websockets

import kino_fs

FS_BASE, FS_TOKEN = None, None

KINO_CDP = int(os.environ.get('KINO_CDP', '9347'))
WALL_CDP = int(os.environ.get('WALL_CDP', '9338'))
WALL_DROPS = os.environ.get('KINO_WALL_DROPS', '0') == '1'   # CDP drag-drop: crashes MCEF, test browsers only
COPY_COOKIES = os.environ.get('KINO_COOKIES', '1') == '1'
SHIM = (Path(__file__).resolve().parent / 'shim.js').read_text()


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def targets(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as r:
        return json.load(r)


class Cdp:
    """One CDP websocket session with request/response + event callbacks."""

    def __init__(self, url, name):
        self.url, self.name = url, name
        self.ids = itertools.count(1)
        self.pending = {}
        self.handlers = {}
        self.ws = None
        self.closed = asyncio.Event()

    async def open(self):
        self.ws = await websockets.connect(self.url, max_size=1 << 27, ping_interval=None)
        asyncio.create_task(self._reader())
        return self

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if 'id' in m:
                    f = self.pending.pop(m['id'], None)
                    if f and not f.done():
                        f.set_result(m)
                elif m.get('method') in self.handlers:
                    # never await a handler here: a handler that waits on another session (or this one) would
                    # stall this reader and deadlock the two CDP connections
                    asyncio.create_task(self._handle(m['method'], m.get('params', {})))
        except websockets.ConnectionClosed:
            pass
        finally:
            self.closed.set()
            for f in self.pending.values():
                if not f.done():
                    f.set_result({'error': {'message': 'cdp closed'}})

    async def _handle(self, method, params):
        try:
            await self.handlers[method](params)
        except Exception as e:
            log(self.name, 'handler error', method, repr(e))

    async def send(self, method, params=None, timeout=120):
        i = next(self.ids)
        f = asyncio.get_running_loop().create_future()
        self.pending[i] = f
        await self.ws.send(json.dumps({'id': i, 'method': method, 'params': params or {}}))
        return await asyncio.wait_for(f, timeout)

    def on(self, method, fn):
        self.handlers[method] = fn


RESOLVE = "(p=>p.split('.').reduce((o,k)=>o[k],window.electron))"
SCHEMA_JS = """(()=>{const w=o=>{const r={};for(const k of Object.keys(o)){const v=o[k];
r[k]=typeof v==='function'?'__fn__':(v&&typeof v==='object'&&!Array.isArray(v))?w(v):v}return r};
return JSON.stringify({schema:w(window.electron),ua:navigator.userAgent})})()"""


IDB_READ = """(async()=>{const db=await new Promise((res,rej)=>{const q=indexedDB.open('kino-local-file-paths',1);
q.onupgradeneeded=()=>q.result.createObjectStore('paths');q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error)});
const s=db.transaction('paths').objectStore('paths');
const ks=await new Promise(r=>{const q=s.getAllKeys();q.onsuccess=()=>r(q.result)});
const vs=await new Promise(r=>{const q=s.getAll();q.onsuccess=()=>r(q.result)});db.close();
return JSON.stringify(ks.map((k,i)=>[k,vs[i]]).filter(e=>typeof e[1]==='string'))})()"""

IDB_WRITE = """(async(rows)=>{const db=await new Promise((res,rej)=>{const q=indexedDB.open('kino-local-file-paths',1);
q.onupgradeneeded=()=>q.result.createObjectStore('paths');q.onsuccess=()=>res(q.result);q.onerror=()=>rej(q.error)});
const tx=db.transaction('paths','readwrite'),s=tx.objectStore('paths');let n=0;
for(const [k,v] of rows){const have=await new Promise(r=>{const q=s.get(k);q.onsuccess=()=>r(q.result)});if(have===undefined){s.put(v,k);n++}}
await new Promise(r=>{tx.oncomplete=r;tx.onerror=r});db.close();return n})"""


def resume_stopped_app():
    """SIGCONT every stopped /opt/Kino process (something paused the hidden app while the game ran)."""
    import signal
    out = []
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            cmd = open(f'/proc/{d}/cmdline', 'rb').read().split(b'\0')[0]
            if not cmd.startswith(b'/opt/Kino/'):
                continue
            st = next(l for l in open(f'/proc/{d}/status') if l.startswith('State:'))
            if '\tT' in st or ' T ' in st or st.split()[1] == 'T':
                os.kill(int(d), signal.SIGCONT)
                out.append(int(d))
        except Exception:
            pass
    return out


class Kino:
    """The real Kino desktop app window (its renderer holds the real window.electron)."""

    def __init__(self):
        self.cdp = None
        self.schema = None
        self.ua = None
        self.subs = {}          # gsid -> (wall, local sid, path, args)
        self.ready = asyncio.Event()

    async def run(self):
        while True:
            try:
                t = next(x for x in targets(KINO_CDP) if x['type'] == 'page' and 'kino.ai' in x['url'])
                self.cdp = await Cdp(t['webSocketDebuggerUrl'], 'kino').open()
                self.cdp.on('Runtime.bindingCalled', self._binding)
                self.cdp.on('Runtime.executionContextCreated', self._ctx)
                await self.cdp.send('Runtime.enable')
                await self.cdp.send('Runtime.addBinding', {'name': '__kinoRelay'})
                await self._load_schema()
                self.ready.set()
                log('kino desktop app connected:', len(json.dumps(self.schema)), 'bytes of bridge schema;', self.ua)
                await self.watch()
                log('kino desktop app connection lost')
            except StopIteration:
                pass
            except Exception as e:
                log('kino connect failed:', repr(e))
            self.ready.clear()
            await asyncio.sleep(2)

    async def watch(self):
        """Until the connection drops: every 20 s check the app answers. A stopped (SIGSTOP) app is resumed;
        3 missed checks in a row drop the connection so run() reconnects."""
        misses = 0
        while not self.cdp.closed.is_set():
            try:
                await asyncio.wait_for(self.cdp.closed.wait(), 20)
                break
            except asyncio.TimeoutError:
                pass
            resumed = resume_stopped_app()
            if resumed:
                log('kino desktop app was stopped (SIGSTOP): resumed', resumed)
            try:
                await asyncio.wait_for(self.cdp.send('Runtime.evaluate', {'expression': '1'}), 8)
                misses = 0
            except Exception:
                misses += 1
                log('kino desktop app not answering', misses)
                if misses >= 3:
                    await self.cdp.ws.close()
                    break

    async def _load_schema(self):
        r = await self.evaluate(SCHEMA_JS)
        d = json.loads(r['value'])
        self.schema, self.ua = d['schema'], d['ua']

    async def _ctx(self, p):
        ctx = p.get('context', {})
        if ctx.get('auxData', {}).get('isDefault') and self.subs:
            await asyncio.sleep(0.5)          # app window reloaded: re-create every live subscription
            for gsid, (_, _, path, args) in list(self.subs.items()):
                await self._subscribe(gsid, path, args)
            log('re-subscribed', len(self.subs), 'after app reload')

    async def evaluate(self, expr):
        r = await self.cdp.send('Runtime.evaluate', {'expression': expr, 'awaitPromise': True, 'returnByValue': True})
        if 'error' in r:
            raise RuntimeError(r['error'].get('message'))
        res = r['result']
        if 'exceptionDetails' in res:
            ex = res['exceptionDetails'].get('exception', {})
            raise CallError(ex.get('description') or res['exceptionDetails'].get('text', 'error'), ex.get('className'))
        return res.get('result', {})

    async def call(self, path, args):
        expr = (f"(async()=>{{const r=await {RESOLVE}({json.dumps(path)})(...{json.dumps(args)});"
                f"return r===undefined?{{u:1}}:{{v:r}}}})()")
        return (await self.evaluate(expr)).get('value', {'u': 1})

    async def _subscribe(self, gsid, path, args):
        expr = (f"(()=>{{const K=window.__kr||(window.__kr={{subs:{{}}}});const g={json.dumps(gsid)};"
                f"const safe=x=>{{try{{return JSON.parse(JSON.stringify(x===undefined?null:x))}}catch(e){{return null}}}};"
                f"const a={json.dumps(args)}.map(x=>x&&x.__cb?((...v)=>__kinoRelay(JSON.stringify({{g,args:v.map(safe)}}))):x);"
                f"if(K.subs[g])try{{K.subs[g]()}}catch(e){{}};K.subs[g]={RESOLVE}({json.dumps(path)})(...a);return 1}})()")
        await self.evaluate(expr)

    async def subscribe(self, wall, sid, path, args):
        gsid = f'{wall.tid}:{sid}'
        self.subs[gsid] = (wall, sid, path, args)
        await self._subscribe(gsid, path, args)

    async def unsubscribe(self, gsid):
        if self.subs.pop(gsid, None) and self.cdp:
            await self.evaluate(f"(()=>{{const K=window.__kr;const f=K&&K.subs[{json.dumps(gsid)}];"
                                f"if(typeof f==='function')f();if(K)delete K.subs[{json.dumps(gsid)}];return 1}})()")

    async def drop_wall(self, wall):
        for gsid in [g for g, s in self.subs.items() if s[0] is wall]:
            await self.unsubscribe(gsid)

    async def _binding(self, p):
        if p.get('name') != '__kinoRelay':
            return
        m = json.loads(p['payload'])
        s = self.subs.get(m['g'])
        if s:
            await s[0].push({'t': 'ev', 'sid': s[1], 'args': m['args']})

    async def local_paths(self):
        """The desktop app's media-id -> absolute local path table (IndexedDB kino-local-file-paths/paths)."""
        r = await self.evaluate(IDB_READ)
        return json.loads(r['value'])

    async def cookies(self):
        r = await self.cdp.send('Network.getAllCookies')
        return [c for c in r.get('result', {}).get('cookies', []) if c['domain'].lstrip('.').endswith('kino.ai')]


class CallError(Exception):
    def __init__(self, msg, name=None):
        super().__init__(msg)
        self.name = name or 'Error'


class Wall:
    """One page in the wall browser (every MCEF page gets the shim; it only wakes up on kino.ai)."""

    def __init__(self, t, kino):
        self.t, self.tid, self.kino = t, t['id'], kino
        self.cdp = None

    async def attach(self):
        await self.kino.ready.wait()
        self.cdp = await Cdp(self.t['webSocketDebuggerUrl'], 'wall ' + self.tid[:6]).open()
        self.cdp.on('Runtime.bindingCalled', self._binding)
        self.cdp.on('Runtime.executionContextsCleared', self._cleared)
        self.cdp.on('Page.frameRequestedNavigation', self._nav)
        self.cdp.on('Page.frameNavigated', self._navigated)
        await self.cdp.send('Runtime.enable')
        await self.cdp.send('Page.enable')
        await self.cdp.send('Runtime.addBinding', {'name': '__omoKinoOut'})
        await self.cdp.send('Network.enable')
        await self.cdp.send('Network.setUserAgentOverride', {'userAgent': self.kino.ua}) if self.on_kino() else None
        shim = (SHIM.replace('__SCHEMA__', json.dumps(self.kino.schema))
                .replace('__FSBASE__', json.dumps(FS_BASE)).replace('__FSTOKEN__', json.dumps(FS_TOKEN)))
        await self.cdp.send('Page.addScriptToEvaluateOnNewDocument', {'source': shim})
        if self.on_kino():
            await self.login_and_reload()
        log('attached', self.tid[:6], self.t['url'][:80])

    def on_kino(self, url=None):
        u = url or self.t.get('url', '')
        return u.startswith('https://kino.ai')

    async def _nav(self, p):
        if self.on_kino(p.get('url', '')):
            await self.cdp.send('Network.setUserAgentOverride', {'userAgent': self.kino.ua})

    @staticmethod
    def desktop_target(url):
        """kino.ai sends plain browsers to /open-desktop?desktop_redirect=<path>: with the bridge we ARE the desktop."""
        from urllib.parse import urlsplit, parse_qs
        u = urlsplit(url)
        if u.hostname == 'kino.ai' and u.path.startswith('/open-desktop'):
            r = parse_qs(u.query).get('desktop_redirect', ['/'])[0]
            return 'https://kino.ai' + (r if r.startswith('/') else '/')
        return None

    async def _navigated(self, p):
        f = p.get('frame', {})
        if f.get('parentId'):
            return
        self.t['url'] = f.get('url', self.t.get('url', ''))
        dest = self.desktop_target(self.t['url'])
        if dest:
            log('wall', self.tid[:6], 'open-desktop page -> back to', dest[:80])
            await self.cdp.send('Network.setUserAgentOverride', {'userAgent': self.kino.ua})
            await self.cdp.send('Page.navigate', {'url': dest})

    async def allow_local(self):
        """Let kino.ai reach 127.0.0.1 (the file API) without a Local Network Access prompt: CEF ignores
        permission prompts, so without this grant the fetches fail once --disable-web-security is gone."""
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{WALL_CDP}/json/version', timeout=3) as r:
                url = json.load(r)['webSocketDebuggerUrl']
            b = await Cdp(url, 'wall-browser').open()
            try:
                for perm in (['localNetworkAccess'], ['localNetwork'], ['loopbackNetwork']):
                    r = await asyncio.wait_for(b.send('Browser.grantPermissions',
                                                      {'permissions': perm, 'origin': 'https://kino.ai'}), 5)
                    log('grant', perm[0], 'to https://kino.ai:', 'ok' if 'error' not in r else r['error'].get('message'))
            finally:
                await b.ws.close()
        except Exception as e:
            log('local network grant failed', repr(e))

    async def login_and_reload(self):
        await self.allow_local()
        if COPY_COOKIES:
            have = {(c['name'], c['domain']) for c in
                    (await self.cdp.send('Network.getCookies', {'urls': ['https://kino.ai/']})).get('result', {}).get('cookies', [])}
            mine = await asyncio.wait_for(self.kino.cookies(), 10)
            new = [c for c in mine if (c['name'], c['domain']) not in have]
            if new:
                keep = ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'sameSite', 'expires')
                await self.cdp.send('Network.setCookies', {'cookies': [{k: c[k] for k in keep if k in c and c[k] is not None}
                                                                      for c in new]})
                log('wall', self.tid[:6], 'got', len(new), 'kino.ai cookies from the desktop app')
        try:
            rows = await asyncio.wait_for(self.kino.local_paths(), 10)
            r = await self.cdp.send('Runtime.evaluate', {'expression': f'{IDB_WRITE}({json.dumps(rows)})',
                                                         'awaitPromise': True, 'returnByValue': True})
            log('wall', self.tid[:6], 'local media paths: desktop app has', len(rows), '- added',
                r.get('result', {}).get('result', {}).get('value'))
        except Exception as e:
            log('local path copy failed', repr(e))
        dest = self.desktop_target(self.t.get('url', ''))
        if dest:
            await self.cdp.send('Page.navigate', {'url': dest})
        else:
            await self.cdp.send('Page.reload', {'ignoreCache': False})

    async def _cleared(self, _):
        await self.kino.drop_wall(self)

    async def push(self, m):
        js = f'window.__omoKinoIn&&window.__omoKinoIn({json.dumps(m)})'
        await self.cdp.send('Runtime.evaluate', {'expression': js})

    async def _binding(self, p):
        if p.get('name') != '__omoKinoOut':
            return
        m = json.loads(p['payload'])
        t = m.get('t')
        if t == 'call':
            asyncio.create_task(self._call(m))
        elif t == 'sub':
            try:
                await self.kino.subscribe(self, m['sid'], m['path'], m['args'])
            except Exception as e:
                log('sub failed', m['path'], repr(e))
        elif t == 'unsub':
            await self.kino.unsubscribe(f"{self.tid}:{m['sid']}")
        elif t == 'bye':
            await self.kino.drop_wall(self)

    async def file_drop(self, selector, path, drop_id):
        """What the desktop app's 'electron:dispatch-file-drop' does to its own window, done to this wall page:
        a real OS file drop (CDP Input.dispatchDragEvent with the path) on Kino's synthetic drop target, which
        hands the page a real file/directory handle for its local project folder."""
        if not selector:
            raise CallError('A drop target selector is required.')
        if not path:
            # no path = the default project root, like the desktop app: <Documents>/KinoProjects
            docs = os.path.join(os.path.expanduser('~'), 'Documents')
            try:
                import subprocess
                docs = subprocess.run(['xdg-user-dir', 'DOCUMENTS'], capture_output=True, text=True,
                                      timeout=3).stdout.strip() or docs
            except Exception:
                pass
            path = os.path.join(docs, 'KinoProjects')
            os.makedirs(path, exist_ok=True)
        if path and not os.path.isabs(path):
            # the web app names project folders relative to home ('Documents/KinoProjects', '~/...')
            path = os.path.join(os.path.expanduser('~'), path[2:] if path.startswith('~/') else path)
        if not path or not os.path.isabs(path):
            raise CallError('The file or directory path must be absolute.')
        if not drop_id:
            raise CallError('A drop ID is required.')
        if not os.path.exists(path):
            raise CallError('File or directory not found: ' + path)
        js = ("(()=>{const e=document.querySelector(%s);if(!(e instanceof HTMLElement))return null;"
              "const b=e.getBoundingClientRect(),x=b.left+b.width/2,y=b.top+b.height/2;"
              "if(!document.elementFromPoint(x,y)?.closest('[data-synthetic-drop-target]'))return{blocked:true};"
              "return{x,y}})()") % json.dumps(selector)
        r = (await self.cdp.send('Runtime.evaluate', {'expression': js, 'returnByValue': True}))['result']['result']
        pt = r.get('value')
        if not pt:
            raise CallError('No element matched selector: ' + selector)
        if pt.get('blocked'):
            raise CallError('Local file access is covered by a dialog or fullscreen view. Close it and retry.')
        data = {'items': [{'mimeType': 'application/x-kino-drop-id', 'data': drop_id}], 'files': [path],
                'dragOperationsMask': 1 | 2 | 16}
        for kind in ('dragEnter', 'dragOver', 'drop'):
            res = await self.cdp.send('Input.dispatchDragEvent', {'type': kind, 'x': pt['x'], 'y': pt['y'], 'data': data})
            if 'error' in res:
                raise CallError(f"{kind} failed: {res['error'].get('message')}")
        log('file drop', path, '->', self.tid[:6])
        return {'v': {'absolutePath': path}}

    def keep_prompt(self, m):
        """Every prompt sent from a wall goes to prompts.jsonl first, so a crash can't eat a long prompt."""
        try:
            rec = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'wall': self.tid[:6], 'path': m['path'],
                   'payload': m['args'][0] if m['args'] else None}
            with open(Path(__file__).resolve().parent / 'prompts.jsonl', 'a') as f:
                f.write(json.dumps(rec, ensure_ascii=False)[:200000] + '\n')
        except Exception as e:
            log('prompt log failed', repr(e))

    async def _call(self, m):
        if m['path'] in ('embeddedAgent.send', 'embeddedAgent.forkSession'):
            self.keep_prompt(m)
        try:
            if m['path'] == 'dispatchFileDrop' and not WALL_DROPS:
                # CDP Input.dispatchDragEvent kills the in-game Chromium (and with it the whole game, 2026-11-04
                # 13:49:18 and 13:50:31): never synthesize drops on a wall page
                raise CallError('Local project folders are not available on this wall yet.')
            if m['path'] == 'dispatchFileDrop':
                r = await self.file_drop(*(m['args'] + [None, None, None])[:3])
            else:
                r = await self.kino.call(m['path'], m['args'])
                if os.environ.get('KINO_TRACE'):
                    log('call', m['path'], json.dumps(m['args'])[:200], '->', json.dumps(r)[:200])
            reply = {'t': 'res', 'id': m['id'], 'val': r.get('v'), 'undef': bool(r.get('u'))}
        except CallError as e:
            reply = {'t': 'res', 'id': m['id'], 'err': {'message': str(e), 'name': e.name}}
        except Exception as e:
            reply = {'t': 'res', 'id': m['id'], 'err': {'message': f'Kino desktop app unavailable: {e}'}}
        if m['id']:
            await self.push(reply)


async def walls(kino):
    seen = {}
    while True:
        try:
            for t in targets(WALL_CDP):
                if t['type'] == 'page' and t['id'] not in seen:
                    w = Wall(t, kino)
                    seen[t['id']] = w
                    asyncio.create_task(attach(w, seen))
        except Exception as e:
            if not getattr(walls, 'warned', False):
                log(f'wall browser CDP on {WALL_CDP} not reachable yet ({e.__class__.__name__}); waiting')
                walls.warned = True
        await asyncio.sleep(2)


async def attach(w, seen):
    try:
        await w.attach()
        await w.cdp.closed.wait()
    except Exception as e:
        log('attach failed', w.tid[:6], repr(e))
    await w.kino.drop_wall(w)
    seen.pop(w.tid, None)


async def main():
    global FS_BASE, FS_TOKEN
    FS_BASE, FS_TOKEN = kino_fs.start()
    kino = Kino()
    log(f'kino-native relay: desktop app CDP {KINO_CDP} -> wall browser CDP {WALL_CDP}')
    await asyncio.gather(kino.run(), walls(kino))


asyncio.run(main())
