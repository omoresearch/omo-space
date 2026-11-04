#!/usr/bin/env python3
"""Local file API for Kino on Omo walls (127.0.0.1:8865).

The in-game Chromium (MCEF) dies (SIGTRAP, whole game) when it is handed a real directory handle (folder drop or
showDirectoryPicker). So the wall never gets real file-system handles: shim.js gives Kino emulated
FileSystemDirectoryHandle / FileSystemFileHandle objects whose every operation is a request to this server.

Access: every request needs the per-run token (header X-Omo-Kino or ?t=). Only paths under a grant are reachable;
a grant is made when Kino asks the desktop bridge for a path (dispatchFileDrop / authorizeLocalMedia), exactly the
paths the desktop app would have opened. CORS only for https://kino.ai.
"""
import json, os, secrets, shutil, threading, time, urllib.parse, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HOST, PORT = '127.0.0.1', int(os.environ.get('KINO_FS_PORT', '8865'))
TOKEN = secrets.token_hex(16)
ORIGIN = 'https://kino.ai'
GRANTS = set()
WRITERS = {}          # wid -> {'tmp', 'dst', 'f'}
LOCK = threading.Lock()


def log(*a):
    print(time.strftime('%H:%M:%S'), 'fs:', *a, flush=True)


def documents():
    try:
        import subprocess
        d = subprocess.run(['xdg-user-dir', 'DOCUMENTS'], capture_output=True, text=True, timeout=3).stdout.strip()
        if d:
            return d
    except Exception:
        pass
    return os.path.join(os.path.expanduser('~'), 'Documents')


DEFAULT_ROOT = os.path.join(documents(), 'KinoProjects')


class FsError(Exception):
    def __init__(self, name, msg, status=400):
        super().__init__(msg)
        self.name, self.status = name, status


def absolute(p):
    if not p:
        raise FsError('TypeError', 'path required')
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(os.path.expanduser('~'), p)
    return os.path.realpath(p)


def granted(p):
    with LOCK:
        return any(p == g or p.startswith(g.rstrip('/') + '/') for g in GRANTS)


def checked(p):
    a = absolute(p)
    if not granted(a):
        raise FsError('NotAllowedError', f'not granted: {a}', 403)
    return a


def grant(path):
    if not path:
        os.makedirs(DEFAULT_ROOT, exist_ok=True)
        path = DEFAULT_ROOT
    a = absolute(path)
    if not os.path.exists(a):
        raise FsError('NotFoundError', f'File or directory not found: {a}', 404)
    with LOCK:
        GRANTS.add(a)
        warm(a)
    kind = 'directory' if os.path.isdir(a) else 'file'
    return {'abs': a, 'name': os.path.basename(a.rstrip('/')) or a, 'kind': kind}


def stat(a):
    try:
        st = os.stat(a)
    except FileNotFoundError:
        raise FsError('NotFoundError', f'not found: {a}', 404)
    return {'kind': 'directory' if os.path.isdir(a) else 'file', 'size': st.st_size, 'mtime': int(st.st_mtime * 1000)}


# -- codec proxies -----------------------------------------------------------------------------------------
# The wall Chromium is built without proprietary codecs: no H.264/HEVC video and no AAC audio (MSE, <video>
# and WebCodecs all say no; VP9 and Opus work). Kino demuxes MP4 itself and asks WebCodecs for the track's
# codec, so the wall is served the SAME container with VP9 + Opus tracks (same size, fps, duration, name).
# Transcoded once (niced, cpus 10-15), cached by path+size+mtime; the original file is never touched.
PROXY_DIR = os.path.expanduser(os.environ.get('KINO_PROXY_DIR', '~/.cache/omo-kino-proxy'))
PROXY_EXT = ('.mp4', '.mov', '.m4v', '.mkv', '.m4a', '.aac', '.mp3', '.wav', '.flac', '.ogg', '.webm')
BAD_CODECS = {'h264', 'hevc', 'mpeg4', 'aac', 'mjpeg', 'prores'}
PROBES, PROXY_LOCKS = {}, {}
PROXY_SEM = threading.Semaphore(int(os.environ.get('KINO_PROXY_JOBS', '2')))


def probe(a, st):
    key = (a, st.st_size, st.st_mtime)
    if key not in PROBES:
        import subprocess
        try:
            out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name',
                                  '-of', 'json', a], capture_output=True, text=True, timeout=30).stdout
            PROBES[key] = [(s.get('codec_type'), s.get('codec_name')) for s in json.loads(out or '{}').get('streams', [])]
        except Exception:
            PROBES[key] = []
    return PROBES[key]


def proxied(a, edit=False, nowait=False):
    """Path to serve for a: a VP9/Opus copy when a holds codecs the wall can't decode, else a itself.
    edit=True: Kino's own editing proxy instead (<=720p, what Kino keeps in OPFS as proxy-<id>.mp4/.m4a);
    always made for audio/video so the wall never has to encode (it has no H.264/AAC encoder either)."""
    if not a.lower().endswith(PROXY_EXT):
        return a
    st = os.stat(a)
    import hashlib, subprocess
    h = hashlib.sha1(f'{a}|{st.st_size}|{st.st_mtime}|{"edit" if edit else "src"}'.encode()).hexdigest()[:20]
    out = os.path.join(PROXY_DIR, h + '.mp4')
    if os.path.exists(out):
        return out
    if nowait:
        warm(a)
        raise FsError('NotReadableError', 'proxy not ready yet', 503)
    streams = probe(a, st)
    if not edit and not any(c in BAD_CODECS for _, c in streams):
        return a
    with LOCK:
        lk = PROXY_LOCKS.setdefault(out, threading.Lock())
    with lk:
        if os.path.exists(out):
            return out
        os.makedirs(PROXY_DIR, exist_ok=True)
        tmp = f'{out}.{os.getpid()}.{threading.get_ident()}.part.mp4'
        has_v = any(t == 'video' for t, _ in streams)
        has_a = any(t == 'audio' for t, _ in streams)
        cmd = ['taskset', '-c', '10-15', 'nice', '-n', '19', 'ffmpeg', '-v', 'error', '-y', '-i', a, '-map', '0:v?',
               '-map', '0:a?', '-map_metadata', '0']
        if has_v and edit:
            cmd += ['-vf', "scale=-2:'min(720,ih)'", '-r', '30']
        if has_v:
            cmd += ['-c:v', 'libvpx-vp9', '-g', '60', '-b:v', '0', '-crf', '31', '-deadline', 'realtime', '-cpu-used', '8',
                    '-row-mt', '1', '-threads', '4', '-pix_fmt', 'yuv420p']
        if has_a:
            cmd += ['-c:a', 'libopus', '-b:a', '160k']
        cmd += ['-movflags', '+faststart']
        cmd += [tmp]
        t0 = time.time()
        with PROXY_SEM:
            r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(tmp):
            log('proxy FAILED', a, r.stderr[-300:])
            try:
                os.remove(tmp)
            except FileNotFoundError:
                pass
            return a
        os.replace(tmp, out)
        log(f'proxy{" (edit)" if edit else ""} {os.path.basename(a)} -> VP9/Opus in {time.time() - t0:.1f}s ({os.path.getsize(out) >> 20} MB)')
        return out


def warm(a):
    """Start proxies for a granted file (or the media files in a granted folder's top level) in the background."""
    def run(paths):
        for p in paths:
            try:
                proxied(p)
                proxied(p, edit=True)
            except Exception as e:
                log('proxy warm failed', p, repr(e))
    paths = ([a] if os.path.isfile(a) else []) if isinstance(a, str) else [p for p in a if os.path.isfile(p)]
    if paths:
        threading.Thread(target=run, args=(paths,), daemon=True).start()


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Omo-Kino, Content-Type, Range')
        self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range, X-Size, X-Mtime')
        self.send_header('Access-Control-Allow-Private-Network', 'true')

    def reply(self, code, obj=None, body=None, ctype='application/json', extra=None):
        data = body if body is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.cors()
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def q(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path, {k: v[-1] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(n) if n else b''

    def auth(self, q):
        if (self.headers.get('X-Omo-Kino') or q.get('t')) != TOKEN:
            raise FsError('NotAllowedError', 'bad token', 403)

    def handle_any(self):
        path, q = self.q()
        try:
            if path == '/fs/health':
                return self.reply(200, {'ok': True, 'grants': len(GRANTS), 'writers': len(WRITERS)})
            self.auth(q)
            op = path[len('/fs/'):] if path.startswith('/fs/') else ''
            fn = getattr(self, 'op_' + op, None)
            if not fn:
                raise FsError('NotFoundError', 'no such op', 404)
            return fn(q)
        except FsError as e:
            return self.reply(e.status, {'error': e.name, 'message': str(e)})
        except FileNotFoundError as e:
            return self.reply(404, {'error': 'NotFoundError', 'message': str(e)})
        except (IsADirectoryError, NotADirectoryError) as e:
            return self.reply(400, {'error': 'TypeMismatchError', 'message': str(e)})
        except PermissionError as e:
            return self.reply(403, {'error': 'NotAllowedError', 'message': str(e)})
        except OSError as e:
            if e.errno == 39:
                return self.reply(400, {'error': 'InvalidModificationError', 'message': str(e)})
            return self.reply(500, {'error': 'UnknownError', 'message': str(e)})
        except Exception as e:
            return self.reply(500, {'error': 'UnknownError', 'message': repr(e)})

    do_GET = do_POST = do_PUT = do_HEAD = handle_any

    # -- ops ------------------------------------------------------------------------------------------------
    def op_grant(self, q):
        g = grant(json.loads(self.body() or b'{}').get('path'))
        log('grant', g['kind'], g['abs'])
        self.reply(200, g)

    def op_warm(self, q):
        paths = [absolute(p) for p in json.loads(self.body() or b'[]') if p]
        ok = [p for p in paths if os.path.isfile(p)]
        with LOCK:
            GRANTS.update(ok)
        warm(ok)
        self.reply(200, {'queued': len(ok)})

    def op_stat(self, q):
        self.reply(200, stat(checked(q.get('p'))))

    def op_list(self, q):
        a = checked(q.get('p'))
        if not os.path.isdir(a):
            raise FsError('TypeMismatchError', 'not a directory')
        out = []
        with os.scandir(a) as it:
            for e in it:
                if e.name.endswith('.crswap'):
                    continue
                try:
                    out.append([e.name, 'directory' if e.is_dir() else 'file'])
                except OSError:
                    pass
        self.reply(200, out)

    def op_child(self, q):
        """getDirectoryHandle / getFileHandle: kind = directory|file, create = 0|1."""
        parent = checked(q.get('p'))
        name, kind, create = q.get('name', ''), q.get('kind'), q.get('create') == '1'
        if not name or '/' in name or name in ('.', '..'):
            raise FsError('TypeError', f'invalid name {name!r}')
        a = os.path.join(parent, name)
        if os.path.exists(a):
            if os.path.isdir(a) != (kind == 'directory'):
                raise FsError('TypeMismatchError', f'{a} is not a {kind}')
        elif not create:
            raise FsError('NotFoundError', f'not found: {a}', 404)
        elif kind == 'directory':
            os.makedirs(a, exist_ok=True)
        else:
            open(a, 'ab').close()
        self.reply(200, {'abs': a, 'name': name, 'kind': kind})

    def op_read(self, q):
        a = checked(q.get('p'))
        if os.path.isdir(a):
            raise FsError('TypeMismatchError', 'is a directory')
        st = stat(a)
        if q.get('edit') == '1':
            a = proxied(a, edit=True, nowait=q.get('nowait') == '1')
        elif q.get('raw') != '1':
            a = proxied(a)  # wall can't decode H.264/AAC: serve the VP9/Opus copy (made once, cached)
        size = os.path.getsize(a)
        rng = self.headers.get('Range')
        start, end = 0, size - 1
        code = 200
        if rng and rng.startswith('bytes='):
            s, _, e = rng[6:].partition('-')
            start = int(s) if s else max(0, size - int(e))
            end = int(e) if (s and e) else size - 1
            end = min(end, size - 1)
            code = 206
        n = max(0, end - start + 1)
        self.send_response(code)
        self.cors()
        ext = os.path.splitext(a)[1].lower()
        ctype = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm', '.mp3': 'audio/mpeg',
                 '.wav': 'audio/wav', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                 '.json': 'application/json'}.get(ext, 'application/octet-stream')
        self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(n))
        self.send_header('X-Size', str(size))
        self.send_header('X-Mtime', str(st['mtime']))
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(a, 'rb') as f:
            f.seek(start)
            left = n
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def op_remove(self, q):
        parent = checked(q.get('p'))
        a = os.path.join(parent, q.get('name', ''))
        if not os.path.lexists(a):
            raise FsError('NotFoundError', f'not found: {a}', 404)
        if os.path.isdir(a) and not os.path.islink(a):
            if q.get('recursive') == '1':
                shutil.rmtree(a)
            else:
                os.rmdir(a)
        else:
            os.remove(a)
        log('remove', a)
        self.reply(200, {'ok': True})

    def op_move(self, q):
        src = checked(q.get('p'))
        dst_dir = checked(q.get('dir')) if q.get('dir') else os.path.dirname(src)
        dst = os.path.join(dst_dir, q.get('name') or os.path.basename(src))
        os.replace(src, dst)
        self.reply(200, {'abs': dst, 'name': os.path.basename(dst)})

    def op_wopen(self, q):
        a = checked(q.get('p'))
        if os.path.isdir(a):
            raise FsError('TypeMismatchError', 'is a directory')
        wid = uuid.uuid4().hex
        tmp = os.path.join(os.path.dirname(a), f'.{os.path.basename(a)}.{wid[:8]}.crswap')
        if q.get('keep') == '1' and os.path.exists(a):
            shutil.copyfile(a, tmp)
        else:
            open(tmp, 'wb').close()
        with LOCK:
            WRITERS[wid] = {'tmp': tmp, 'dst': a, 'f': open(tmp, 'r+b')}
        self.reply(200, {'wid': wid, 'size': os.path.getsize(tmp)})

    def writer(self, q):
        with LOCK:
            w = WRITERS.get(q.get('wid'))
        if not w:
            raise FsError('InvalidStateError', 'writer closed')
        return w

    def op_wwrite(self, q):
        w = self.writer(q)
        data = self.body()
        f = w['f']
        f.seek(int(q.get('pos', '0')))
        f.write(data)
        f.flush()
        self.reply(200, {'pos': f.tell()})

    def op_wtrunc(self, q):
        w = self.writer(q)
        w['f'].truncate(int(q.get('size', '0')))
        self.reply(200, {'ok': True})

    def op_wclose(self, q):
        w = self.writer(q)
        with LOCK:
            WRITERS.pop(q.get('wid'), None)
        w['f'].close()
        os.replace(w['tmp'], w['dst'])
        self.reply(200, {'ok': True, 'size': os.path.getsize(w['dst'])})

    def op_wabort(self, q):
        with LOCK:
            w = WRITERS.pop(q.get('wid'), None)
        if w:
            w['f'].close()
            try:
                os.remove(w['tmp'])
            except FileNotFoundError:
                pass
        self.reply(200, {'ok': True})


def start():
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name='kino-fs', daemon=True).start()
    log(f'local file API on http://{HOST}:{PORT}/fs/ (default root {DEFAULT_ROOT})')
    return f'http://{HOST}:{PORT}', TOKEN
