#!/usr/bin/env python3
"""Omo Space setup core: the game runtime, sign-in, host services, repair.

This is the engine behind the Omo Space window (and usable on its own):

  omo_setup.py status     what is installed, signed in and running (JSON)
  omo_setup.py setup      download and install what is missing (resumable)
  omo_setup.py repair     re-verify every managed file by hash and reinstall
                          what is bad (Java and tmux file by file against their
                          pinned archives), re-apply the CC:T loopback rule, the
                          unit files and the bridge
  omo_setup.py signin     Microsoft sign-in (device code) with Omo's client ID
  omo_setup.py signout    forget the stored sign-in
  omo_setup.py play       check the bridge, then start Minecraft
  omo_setup.py services   (re)install and start omo-tmux + omo-bridge

Every command exits non-zero, with the reason on its last line, unless the
result is actually ready to play.

Design rules:
  * Omo owns its runtime (~/.local/share/omo/runtime): Minecraft, Fabric and
    the mods are pinned in manifest.json and verified by hash (Mojang's
    version file is pinned by SHA-1 and pins everything it lists). Partial
    downloads resume; a bad file is fetched again. Files a Prism Launcher on
    this machine already has are reused after the same hash check.
  * With Omo's own Microsoft client ID, Omo signs in and starts Java itself:
    no other launcher is involved. Without one (Mojang has to approve it),
    Omo runs its own private Prism Launcher (its own data dir, so a Prism the
    player has open never takes the launch over): once visibly for the
    Microsoft sign-in, then headless.
  * The access token reaches Java through an inherited pipe (a `@argfile`),
    never the command line, a log or a file on disk.
  * The player's own Prism instances, worlds and accounts are never changed.
    Worlds from the "OmoResearch" instance the previous launcher made are
    copied across once, and never while that game is running.
  * Files are replaced atomically; game files are left alone while the game
    runs. Repair never deletes saves, the sign-in, the bridge token,
    workspace metadata or Hermes config, and never restarts the session
    server (that would end running shells).
  * Hermes is detected, never modified or installed.
"""
import concurrent.futures
import contextlib
import filecmp
import gzip
import hashlib
import http.client
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import omo_auth


def detect_platform():
    """'<os>-<arch>' for this machine: linux-x86_64, macos-arm64, macos-x86_64
    or windows-x86_64 are supported. OMO_PLATFORM forces one (tests)."""
    if os.environ.get("OMO_PLATFORM"):
        return os.environ["OMO_PLATFORM"]
    system = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    return f"{system}-{ {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine) }"


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = json.loads((HERE / "manifest.json").read_text())
HOME = Path.home()
ARCH = detect_platform()
OS = ARCH.split("-")[0]                 # what Omo does: linux | macos | windows
WIN = sys.platform == "win32"           # how processes and locks work on THIS machine
# Windows has no tmux: the game runs natively and the session server, bridge
# and Hermes run inside WSL2, reached on 127.0.0.1 through WSL's localhost forwarding.


def default_data_dir():
    """Where Omo keeps its runtime and the bridge token. The mod looks in the
    same place (BridgeLink.dataDir), so the two must agree."""
    if OS == "macos":
        return HOME / "Library/Application Support/OmoResearch"
    if OS == "windows":
        return Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData/Local") / "OmoResearch"
    return HOME / ".local/share/omo"


def default_user_prism():
    if OS == "macos":
        return HOME / "Library/Application Support/PrismLauncher"
    if OS == "windows":
        return Path(os.environ.get("APPDATA") or HOME / "AppData/Roaming") / "PrismLauncher"
    return HOME / ".local/share/PrismLauncher"


OMO = Path(os.environ.get("OMO_DATA") or default_data_dir())
DL = OMO / "downloads"
RUNTIME = OMO / "runtime"
MCDIR = RUNTIME / "minecraft"           # libraries, assets, versions, natives
GAME = RUNTIME / "game"                 # the game directory: mods, config, saves
ACCOUNT = RUNTIME / "account.json"      # Omo's own sign-in (0600)
PRISM_DIR = RUNTIME / "prism"           # the private Prism data dir (no Omo client ID yet)
INSTANCE_ID = MANIFEST["instance_id"]
INSTANCE = PRISM_DIR / "instances" / INSTANCE_ID
JAVA_DIR = OMO / "java" / f"temurin-{MANIFEST['java']['major']}"
PRISM_HOME = OMO / "bin/prism"           # macOS/Windows: the unpacked Prism archive
OWN_PRISM = {"macos": PRISM_HOME / "Prism Launcher.app/Contents/MacOS/prismlauncher",
             "windows": PRISM_HOME / "prismlauncher.exe"}.get(OS, OMO / "bin/PrismLauncher.AppImage")
# The player's own Prism: read only, for the one-time world copy and reusable downloads.
USER_PRISM = Path(os.environ.get("OMO_PRISM_DATA") or default_user_prism())
OLD_GAME = USER_PRISM / "instances" / INSTANCE_ID / ".minecraft"
SYSTEMD_USER = HOME / ".config/systemd/user"
LAUNCH_AGENTS = HOME / "Library/LaunchAgents"
TMUX_CONF = (OMO / "tmux.conf") if OS == "macos" else HOME / ".config/omo/tmux.conf"
UNITS = ("omo-tmux.service", "omo-bridge.service")
LABELS = {"omo-tmux.service": "space.omo.research.tmux", "omo-bridge.service": "space.omo.research.bridge"}
PORT = int(os.environ.get("OMO_BRIDGE_PORT", "8780"))   # the bridge; the only loopback port CC:T may reach
WSL_DISTRO = os.environ.get("OMO_WSL_DISTRO", "")       # empty: the default WSL distribution
ARGFILE_GRACE = float(os.environ.get("OMO_ARGFILE_GRACE", "10"))   # Windows: seconds before the launch argfile goes
MOD_JARS = (HERE, REPO / "mod/build")    # where our own mod ships: beside this script, or the build output
APP_ID = "space.omo.research.launcher"
MIGRATE = ("saves", "options.txt", "config", "resourcepacks", "shaderpacks", "screenshots", "servers.dat")
WINDOW_UP = "Backend library:"          # the game log line printed once its window exists


def say(msg):
    print(msg, flush=True)


def client_id():
    """Omo's Azure app ID, when it has one approved; None means the Prism path."""
    return os.environ.get("OMO_MSA_CLIENT_ID") or MANIFEST["microsoft"]["client_id"] or None


# --------------------------------------------------------------------------- files
def digest(fileobj, algo):
    h = hashlib.new(algo)
    for chunk in iter(lambda: fileobj.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()


def verify(path, spec):
    """True when `path` matches the manifest entry (size and hash when known)."""
    path = Path(path)
    if not path.is_file():
        return False
    if "size" in spec and path.stat().st_size != spec["size"]:
        return False
    for algo in ("sha512", "sha256", "sha1"):
        if algo in spec:
            with open(path, "rb") as fh:
                return digest(fh, algo) == spec[algo]
    return path.stat().st_size > 0


def present(path, spec, thorough):
    """The quick check for setup (size), the full one for repair (hash)."""
    if thorough:
        return verify(path, spec)
    path = Path(path)
    return path.is_file() and path.stat().st_size == spec.get("size", path.stat().st_size)


def write_if_changed(dest, data, mode=None):
    """Atomically replace `dest` with `data` unless it already holds exactly
    that. Returns True when the file changed."""
    dest = Path(dest)
    if dest.is_file() and dest.read_bytes() == data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.omo-tmp")
    tmp.write_bytes(data)
    if mode is not None:
        tmp.chmod(mode)
    os.replace(tmp, dest)
    return True


def install_file(src, dest):
    """Copy `src` over `dest` atomically; skip identical files. True if copied."""
    dest = Path(dest)
    if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
        return False
    tmp = dest.with_name(f".{dest.name}.omo-tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return True


def copy_new(src, dest):
    """Copy a file or tree to `dest` unless something is already there; via a
    temporary name, so an interrupted copy never looks finished."""
    if dest.exists():
        return False
    tmp = dest.with_name(f".{dest.name}.omo-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.unlink(missing_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns("session.lock"))
    else:
        shutil.copy2(src, tmp)
    tmp.rename(dest)
    return True


def link(path, target):
    """`path` -> `target`, unless something real is already at `path`.
    Windows: a directory junction, which needs no admin or Developer Mode."""
    if WIN:
        import _winapi
        if path.exists() and os.path.realpath(path) == os.path.realpath(target):
            return
        if path.is_junction() or path.is_symlink():
            os.rmdir(path) if path.is_junction() else path.unlink()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            Path(target).mkdir(parents=True, exist_ok=True)
            _winapi.CreateJunction(str(target), str(path))
        return
    if path.is_symlink() and os.readlink(path) == str(target):
        return
    if path.is_symlink():
        path.unlink()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


def runs(*argv):
    try:
        return subprocess.run([str(a) for a in argv], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# --------------------------------------------------------------------------- downloads
def download(url, dest, spec, label, seeds=(), progress=None):
    """Resumable, hash-verified download. Returns dest.

    A local copy in `seeds` that verifies is used instead of the network.
    An interrupted run leaves `<dest>.part`. A partial one is resumed with a
    Range request; a full-size one is verified and moved into place (asking
    the server for more would only get 416); a bad one is discarded.
    `progress(n)` is told about bytes as they arrive; without it, a
    percentage line is printed now and then."""
    dest = Path(dest)
    if verify(dest, spec):
        if not progress:
            say(f"  ok        {label}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for seed in seeds:
        if verify(seed, spec):
            shutil.copyfile(seed, part)
            part.replace(dest)
            if progress:
                progress(dest.stat().st_size)
            else:
                say(f"  reused    {label} ({seed})")
            return dest
    size = spec.get("size")
    for attempt in range(1, 4):
        have = part.stat().st_size if part.exists() else 0
        if have and size and have >= size:
            if _finish(part, dest, spec, label, progress):
                return dest
            have = 0
        try:
            _fetch(url, part, have, size, label, progress)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 416 and have:        # nothing left to send: the .part is complete or stale
                if _finish(part, dest, spec, label, progress):
                    return dest
                continue
            say(f"  retry     {label}: HTTP {exc.code}")
            time.sleep(2 * attempt)
            continue
        except (OSError, http.client.HTTPException) as exc:
            say(f"  retry     {label}: {exc}")
            time.sleep(2 * attempt)
            continue
        if _finish(part, dest, spec, label, progress):
            return dest
    raise SystemExit(f"could not download {label} from {url}")


def _finish(part, dest, spec, label, progress):
    """Move a complete .part into place if it verifies; otherwise discard it."""
    if verify(part, spec):
        part.replace(dest)
        if not progress:
            say(f"  verified  {label}")
        return True
    say(f"  BAD HASH  {label}; discarding it and downloading again")
    part.unlink(missing_ok=True)
    return False


def _fetch(url, part, have, total, label, progress):
    req = urllib.request.Request(url, headers={"User-Agent": "omo-setup/1.1"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        if have and resp.status != 206:
            have = 0                                    # server ignored the range: start over
        elif have and progress:
            progress(have)                              # resumed bytes count as done
        done, last = have, time.monotonic()
        with open(part, "ab" if have else "wb") as fh:
            while chunk := resp.read(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(len(chunk))
                elif time.monotonic() - last > 1.0:
                    say(f"  {100 * done / total:5.1f}%  {label}" if total else f"  {done >> 20} MB  {label}")
                    last = time.monotonic()


def download_all(items, label, thorough=False):
    """Many files, eight at a time, with one overall percentage. `items` are
    (url, dest, spec, seeds) with sizes in every spec."""
    todo = [it for it in items if not present(it[1], it[2], thorough)]
    if not todo:
        say(f"  ok        {label}")
        return
    total = sum(it[2]["size"] for it in todo)
    state = {"done": 0, "said": 0.0}
    lock = threading.Lock()

    def progress(n):
        with lock:
            state["done"] += n
            if time.monotonic() - state["said"] > 0.5:
                state["said"] = time.monotonic()
                say(f"  {min(99.9, 100 * state['done'] / total):5.1f}%  {label} "
                    f"({state['done'] >> 20} of {total >> 20} MB)")

    def one(item):
        url, dest, spec, seeds = item
        Path(dest).unlink(missing_ok=True)              # present but wrong: fetch it again
        download(url, dest, spec, Path(dest).name, seeds, progress)

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        for future in [pool.submit(one, it) for it in todo]:
            future.result()
    say(f"  verified  {label} ({len(todo)} files)")


# --------------------------------------------------------------------------- minecraft
MOJANG_OS = {"linux": "linux", "macos": "osx", "windows": "windows"}


def allowed(rules):
    """Mojang's rule lists for this machine: its OS, 64-bit, no optional
    features. Per-arch natives (macos-arm64, windows-x86) share one OS rule;
    LWJGL loads the right one at runtime, as the vanilla launcher does."""
    here = MOJANG_OS.get(OS, OS)
    ok = not rules
    for rule in rules or ():
        where = rule.get("os", {})
        if "features" in rule or where.get("name", here) != here or "arch" in where:
            continue                                    # a rule for somewhere else does not apply
        ok = rule["action"] == "allow"
    return ok


def maven_path(name):
    group, artifact, version, *classifier = name.split(":")
    tail = f"-{classifier[0]}" if classifier else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{tail}.jar"


def version_file():
    v = MANIFEST["minecraft"]["version"]
    return MCDIR / "versions" / v / f"{v}.json"


def version_json():
    return json.loads(version_file().read_text())


def game_files(v):
    """(url, dest, spec, seeds) for Fabric, the libraries this machine needs
    and the client jar, in classpath order."""
    old_libs = USER_PRISM / "libraries"
    files = []
    for lib in MANIFEST["fabric"]["libraries"]:
        path = maven_path(lib["name"])
        files.append((lib["url"] + path, MCDIR / "libraries" / path,
                      {"sha1": lib["sha1"], "size": lib["size"]}, [old_libs / path]))
    for lib in v["libraries"]:
        if allowed(lib.get("rules")):
            a = lib["downloads"]["artifact"]
            files.append((a["url"], MCDIR / "libraries" / a["path"],
                          {"sha1": a["sha1"], "size": a["size"]}, [old_libs / a["path"]]))
    c, ver = v["downloads"]["client"], v["id"]
    files.append((c["url"], MCDIR / "versions" / ver / f"{ver}.jar", {"sha1": c["sha1"], "size": c["size"]},
                  [old_libs / f"com/mojang/minecraft/{ver}/minecraft-{ver}-client.jar"]))
    return files


def ensure_minecraft(thorough=False):
    mc = MANIFEST["minecraft"]
    download(mc["url"], version_file(), mc, f"Minecraft {mc['version']} version file")
    v = version_json()
    idx = v["assetIndex"]
    index = MCDIR / "assets/indexes" / f"{idx['id']}.json"
    download(idx["url"], index, idx, f"asset index {idx['id']}", [USER_PRISM / "assets/indexes" / f"{idx['id']}.json"])
    objects = {o["hash"]: o["size"] for o in json.loads(index.read_text())["objects"].values()}
    base = mc["resources"]
    assets = [(f"{base}{h[:2]}/{h}", MCDIR / "assets/objects" / h[:2] / h, {"sha1": h, "size": size},
               [USER_PRISM / "assets/objects" / h[:2] / h]) for h, size in objects.items()]
    download_all(game_files(v) + assets, f"Minecraft {mc['version']}, Fabric and game assets", thorough)
    write_if_changed(MCDIR / ".installed", runtime_stamp().encode())


def runtime_stamp():
    """What a complete runtime was built from; status compares against it."""
    return f"{MANIFEST['minecraft']['sha1']} fabric {MANIFEST['fabric']['loader']}\n"


def extra_jvm_args():
    """OMO_JVM_ARGS: extra game JVM options, e.g. -Domo.port=... to point the
    mod at an isolated test bridge."""
    return os.environ.get("OMO_JVM_ARGS", "").split()


def argfile_line(arg):
    """One argument for a Java @argfile: quoted, with \\ and \" escaped."""
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launch_args(v, account, java):
    """(JVM arguments, main class and game arguments). Only the second part
    carries the token; play hands it to Java through a pipe."""
    ver = v["id"]
    cp = [str(f[1]) for f in game_files(v)]
    natives = MCDIR / "natives" / ver
    subs = {
        "natives_directory": str(natives), "launcher_name": "omo-research",
        "launcher_version": MANIFEST["version"], "classpath": os.pathsep.join(cp),
        "classpath_separator": os.pathsep, "library_directory": str(MCDIR / "libraries"),
        "version_name": f"fabric-loader-{MANIFEST['fabric']['loader']}-{ver}", "game_directory": str(GAME),
        "assets_root": str(MCDIR / "assets"), "assets_index_name": v["assetIndex"]["id"],
        "auth_player_name": account["profile"]["name"], "auth_uuid": account["profile"]["id"],
        "auth_access_token": account["minecraft"]["access_token"], "clientid": account.get("client_id", ""),
        "auth_xuid": "0", "user_type": "msa", "version_type": v.get("type", "release"),
    }

    def expand(args):
        out = []
        for a in args:
            if isinstance(a, dict):
                if not allowed(a.get("rules")):
                    continue
                a = a["value"]
            for piece in [a] if isinstance(a, str) else a:
                out.append(re.sub(r"\$\{(\w+)\}", lambda m: subs.get(m.group(1), m.group(0)), piece))
        return out

    mem = MANIFEST["memory_mb"]
    # omo.game marks the process as Omo's (instance_pids reads it on macOS/Windows);
    # omo.data tells the mod where the bridge token lives.
    jvm = [f"-Xms{mem['min']}m", f"-Xmx{mem['max']}m", "-Dfile.encoding=UTF-8",
           f"-Domo.game={GAME}", f"-Domo.data={OMO}", *extra_jvm_args(),
           *expand(v["arguments"]["jvm"]), *MANIFEST["fabric"]["jvm"]]
    for sub in ("java", "jna", "lwjgl", "netty"):
        (natives / sub).mkdir(parents=True, exist_ok=True)
    return [str(java), *jvm], [MANIFEST["fabric"]["main_class"], *expand(v["arguments"]["game"])]


# --------------------------------------------------------------------------- pieces
SUPPORTED = ("linux-x86_64", "macos-arm64", "macos-x86_64", "windows-x86_64")


def require_supported():
    if ARCH not in SUPPORTED:
        raise SystemExit(f"Omo Space does not support {ARCH} yet; it runs on {', '.join(SUPPORTED)}")


def archive_files(path):
    """(name, size, open(), mode, link_target) for the regular files and
    symlinks of a .tar.gz or .zip, in archive order. Unix modes and symlinks
    in zips (Prism's macOS app has both) come from the zip's external attrs."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                mode = info.external_attr >> 16
                if info.is_dir():
                    continue
                if (mode & 0o170000) == 0o120000:
                    yield info.filename, info.file_size, None, mode, zf.read(info).decode()
                else:
                    yield info.filename, info.file_size, (lambda i=info: zf.open(i)), mode, None
        return
    with tarfile.open(path) as tf:
        for m in tf:
            if m.issym():
                yield m.name, 0, None, m.mode, m.linkname
            elif m.isfile():
                yield m.name, m.size, (lambda m=m: tf.extractfile(m)), m.mode, None


def safe_join(root, name):
    """`root/name`, refusing absolute names and any `..` escape."""
    parts = Path(name.replace("\\", "/")).parts
    if not parts or Path(name).is_absolute() or ".." in parts or name.startswith("/"):
        raise SystemExit(f"refusing an unsafe path in an archive: {name!r}")
    return root.joinpath(*parts)


def extract(archive, dest, strip=0, skip=("__MACOSX/",)):
    """Unpack a .tar.gz or .zip into `dest`, dropping `strip` leading path
    parts. Keeps exec bits and (where the OS allows) symlinks."""
    for name, _size, opener, mode, target in archive_files(archive):
        if name.startswith(skip):
            continue
        parts = Path(name).parts[strip:]
        if not parts:
            continue
        out = safe_join(dest, "/".join(parts))
        out.parent.mkdir(parents=True, exist_ok=True)
        if target is not None:
            if WIN:
                continue                    # Windows archives here carry none; symlinks need admin
            if os.path.isabs(target) or not os.path.realpath(out.parent / target).startswith(
                    os.path.realpath(dest) + os.sep):
                raise SystemExit(f"refusing a symlink that leaves the archive: {name} -> {target}")
            out.unlink(missing_ok=True)
            out.symlink_to(target)
            continue
        assert opener is not None
        with opener() as src, open(out, "wb") as fh:
            shutil.copyfileobj(src, fh, 1 << 20)
        if mode & 0o111 and not WIN:
            out.chmod(0o755)


def tree_matches(archive, root, strip=1):
    """Every regular file of the pinned archive is present in `root`, byte for byte."""
    for name, size, opener, _mode, target in archive_files(archive):
        if target is not None or name.startswith("__MACOSX/"):
            continue
        parts = Path(name).parts[strip:]
        if not parts:
            continue
        have = root.joinpath(*parts)
        if not have.is_file() or have.stat().st_size != size:
            return False
        assert opener is not None
        with opener() as src, open(have, "rb") as fh:
            if digest(src, "sha256") != digest(fh, "sha256"):
                return False
    return True


def install_tree(archive, dest, strip=0):
    """Replace the directory `dest` with the archive's contents, atomically
    enough: unpack beside it, then swap names."""
    tmp, old = dest.with_name(dest.name + ".extract"), dest.with_name(dest.name + ".old")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    tmp.mkdir(parents=True)
    extract(archive, tmp, strip)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def archive_name(spec, stem):
    return f"{stem}.zip" if spec["url"].endswith(".zip") else f"{stem}.tar.gz"


def ensure_prism():
    """Omo's own pinned Prism (the one the player may have is never used).
    Linux: one AppImage. macOS and Windows: the official archive, unpacked."""
    spec = MANIFEST["prism"][ARCH]
    label = f"Prism Launcher {MANIFEST['prism']['version']}"
    if OS == "linux":
        seeds = [HOME / "Applications/minecraft/PrismLauncher.AppImage"]
        download(spec["url"], OWN_PRISM, spec, label, seeds)
        OWN_PRISM.chmod(0o755)
        return OWN_PRISM
    archive = download(spec["url"], DL / archive_name(spec, f"prism-{MANIFEST['prism']['version']}"), spec, label)
    stamp = PRISM_HOME / ".omo-source"
    if OWN_PRISM.exists() and stamp.is_file() and stamp.read_text() == spec["sha256"]:
        return OWN_PRISM
    install_tree(archive, PRISM_HOME)
    stamp.write_text(spec["sha256"])
    if not OWN_PRISM.exists():
        raise SystemExit(f"{label}: {OWN_PRISM.relative_to(PRISM_HOME)} is not in {spec['url']}")
    say(f"Prism: installed {PRISM_HOME}")
    return OWN_PRISM


def java_tree_matches(tgz, root):
    """Every regular file of the pinned Java archive is present in `root`, byte for byte."""
    return tree_matches(tgz, root, strip=1)


def java_exe(home):
    """The java a Temurin tree runs the game with: bin/java on Linux, the
    bundle's Contents/Home on macOS, javaw.exe (no console window) on Windows."""
    if OS == "macos":
        return home / "Contents/Home/bin/java"
    if OS == "windows":
        return home / "bin/javaw.exe"
    return home / "bin/java"


def users_java():
    """A matching Temurin the user already installed by hand (Linux), if any."""
    if OS != "linux":
        return None
    java = HOME / f".local/share/Java/temurin-{MANIFEST['java']['major']}/bin/java"
    return java if java.exists() else None


def java_path():
    return java_exe(JAVA_DIR) if JAVA_DIR.exists() else users_java()


def ensure_java(verify_all=False):
    java = java_exe(JAVA_DIR)
    major = MANIFEST["java"]["major"]
    if not JAVA_DIR.exists() and users_java():
        say(f"Java: {users_java()} (yours, used as is)")
        return users_java()
    if JAVA_DIR.exists() and not verify_all and runs(java, "-version"):
        say(f"Java: {java}")
        return java
    spec = MANIFEST["java"][ARCH]
    tgz = download(spec["url"], DL / archive_name(spec, f"temurin{major}-jre"), spec, f"Temurin {major} JRE")
    if JAVA_DIR.exists() and java_tree_matches(tgz, JAVA_DIR):
        say(f"  verified  Java runtime files ({JAVA_DIR})")
        return java
    install_tree(tgz, JAVA_DIR, strip=1)
    say(f"Java: installed {java}")
    return java


def find_tmux_macos():
    """macOS has no static tmux to pin; use the player's (Homebrew or MacPorts)."""
    for cand in (os.environ.get("OMO_TMUX"), "/opt/homebrew/bin/tmux", "/usr/local/bin/tmux",
                 "/opt/local/bin/tmux", shutil.which("tmux")):
        if cand and Path(cand).is_file() and runs(cand, "-V"):
            return Path(cand)
    return None


def ensure_tmux(verify_all=False):
    if OS == "windows":
        say("tmux: runs inside WSL (set up with the Omo services)")
        return None
    if OS == "macos":
        tmux = find_tmux_macos()
        if not tmux:
            raise SystemExit("tmux is required on macOS: install it with `brew install tmux` "
                             "(https://brew.sh), then run setup again")
        say(f"tmux: {tmux}")
        return tmux
    dest = OMO / "bin/tmux"
    if not verify_all and runs(dest, "-V"):
        say(f"tmux: {dest}")
        return dest
    spec = MANIFEST["tmux"][ARCH]
    gz = download(spec["url"], DL / "tmux.linux-amd64.stripped.gz", spec, "tmux (static)")
    # Replacing the binary under a running session server is safe: os.replace
    # swaps the name, the running server keeps its own inode.
    changed = write_if_changed(dest, gzip.decompress(gz.read_bytes()), 0o755)
    say(f"tmux: {'installed' if changed else 'verified'} {dest}")
    return dest


def ensure_mods():
    mods = GAME / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    for m in MANIFEST["mods"]:
        download(m["url"], mods / m["file"], m, m["file"], [OLD_GAME / "mods" / m["file"]])
    own = MANIFEST["omolife"]["file"]
    src = next((d / own for d in MOD_JARS if (d / own).exists()), None)
    if not src:
        raise SystemExit(f"{own} not found next to the launcher or in mod/build")
    say(f"  {'installed' if install_file(src, mods / own) else 'ok       '} {own}")
    # Older omolife jars must go, or two versions load together.
    for old in mods.glob("omolife-*.jar"):
        if old.name != own:
            old.unlink()


CCT_RULE = ('\t[[http.rules]]\n'
            '\t\t#Allow CC:T computers to reach the local Omo bridge (loopback, bridge port only).\n'
            '\t\thost = "127.0.0.1"\n'
            '\t\tport = {port}\n'
            '\t\taction = "allow"\n')
LOOPBACK_HOSTS = {"127.0.0.1", "127.0.0.0/8", "localhost", "$private", "*"}


def cct_rule_state(text, port):
    """How CC:T treats 127.0.0.1:<port>: 'ok' (our port-scoped allow decides),
    'open' (a 127.0.0.1 allow without a port decides) or 'missing'."""
    for rule in tomllib.loads(text).get("http", {}).get("rules", []):
        if rule.get("host") not in LOOPBACK_HOSTS or rule.get("port", port) != port or "action" not in rule:
            continue
        if rule["host"] == "127.0.0.1" and rule["action"] == "allow":
            return "ok" if "port" in rule else "open"
        return "missing"
    return "missing"


def ensure_cct_rule(path, port=None, source=REPO / "config/computercraft-server.toml"):
    """CC:T denies private addresses by default; the station client needs
    127.0.0.1:<port> (default: the bridge's) and nothing else on loopback.
    Re-applied by every setup."""
    path, port = Path(path), port or PORT
    try:
        text = path.read_text()
        state = cct_rule_state(text, port)
    except FileNotFoundError:
        text, state = None, "absent"
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        text, state = None, "unreadable"
    if state == "ok":
        return "ok"
    if text is None:
        new = source.read_text().replace("port = 8780", f"port = {port}")
    elif state == "open":       # tighten the existing rule to the bridge port
        new = re.sub(r'^([ \t]*)host\s*=\s*"127\.0\.0\.1"[ \t]*\n',
                     lambda m: f'{m.group(0)}{m.group(1)}port = {port}\n', text, count=1, flags=re.M)
    else:                       # rules are evaluated in order: ours goes first
        rule = CCT_RULE.format(port=port)
        head = re.search(r"^[ \t]*\[\[http\.rules\]\]", text, flags=re.M)
        new = text[:head.start()] + rule + text[head.start():] if head else text + "\n" + rule
    try:
        applied = cct_rule_state(new, port) == "ok"
    except tomllib.TOMLDecodeError:
        applied = False
    if not applied:
        raise SystemExit(f"could not add the CC:T loopback rule to {path}; "
                         f"allow host 127.0.0.1 port {port} there by hand")
    write_if_changed(path, new.encode())
    return state


def old_worlds():
    """Worlds in the previous launcher's Prism instance that are not here yet."""
    saves = OLD_GAME / "saves"
    if not saves.is_dir():
        return []
    return sorted(w.name for w in saves.iterdir() if w.is_dir() and not (GAME / "saves" / w.name).exists())


def migrate():
    """Bring worlds and settings across from the "OmoResearch" Prism instance
    the previous launcher made: copied (the source is never changed), only
    what is not here yet, never while that game runs, and only once."""
    marker = RUNTIME / ".migrated"
    if marker.exists() or not OLD_GAME.is_dir():
        return
    if instance_pids(OLD_GAME.parent):
        say("worlds: Minecraft is running from Prism; your worlds come across once it has quit")
        return
    copied = []
    for name in MIGRATE:
        src = OLD_GAME / name
        if name == "saves" and src.is_dir():
            copied += [f"saves/{w.name}" for w in sorted(src.iterdir())
                       if w.is_dir() and copy_new(w, GAME / "saves" / w.name)]
        elif src.exists() and copy_new(src, GAME / name):
            copied.append(name)
    write_if_changed(marker, (f"copied from {OLD_GAME}:\n" + "".join(f"  {c}\n" for c in copied)).encode())
    say(f"worlds: brought across from Prism: {', '.join(copied) or 'nothing new'}")


def ensure_game_dir():
    (GAME / "config").mkdir(parents=True, exist_ok=True)
    state = ensure_cct_rule(GAME / "config/computercraft-server.toml")
    if state != "ok":
        say(f"  CC:T loopback rule: {'tightened to port ' + str(PORT) if state == 'open' else 'applied'}")
    opts = GAME / "options.txt"
    if not opts.exists():
        opts.write_text("tutorialStep:none\nskipMultiplayerWarning:true\nonboardAccessibility:false\n"
                        "joinedFirstServer:true\npauseOnLostFocus:false\n")
    ensure_starter_world()
    say(f"game: {GAME}")


def ensure_starter_world(src=None):
    name = MANIFEST["world_name"]
    src = Path(src) if src else REPO / "worlds" / name
    if (GAME / "saves" / name).exists():
        say(f"world: {name} (kept)")
    elif not src.exists():
        say("world: no starter world bundled; create any world and place a Hermes Computer")
    elif copy_new(src, GAME / "saves" / name):
        say(f"world: installed starter '{name}'")


def ini_set(path, section, values):
    """Set keys in one section of a Qt-style .cfg, keeping everything else."""
    lines = path.read_text().splitlines() if path.exists() else []
    if f"[{section}]" not in lines:
        lines.append(f"[{section}]")
    start = lines.index(f"[{section}]") + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
    todo = dict(values)
    for i in range(start, end):
        key = lines[i].partition("=")[0]
        if key in todo:
            lines[i] = f"{key}={todo.pop(key)}"
    lines[end:end] = [f"{k}={v}" for k, v in todo.items()]
    write_if_changed(path, ("\n".join(lines) + "\n").encode())


def ensure_prism_runtime(java):
    """The private Prism: its own data dir, sharing Omo's libraries, assets
    and game directory. Its setup wizard only asks for the Microsoft account."""
    ensure_prism()
    link(PRISM_DIR / "libraries", MCDIR / "libraries")
    link(PRISM_DIR / "assets", MCDIR / "assets")
    ini_set(PRISM_DIR / "prismlauncher.cfg", "General", {      # answered here, so its wizard asks nothing else
        "ConfigVersion": "1.3", "Language": "en_US", "ApplicationTheme": "system", "IconTheme": "pe_colored",
        "AutoUpdate": "false", "JavaPath": Path(java).as_posix(), "AutomaticJavaDownload": "false",
        "AutomaticJavaSwitch": "false",
        "UserAskedAboutAutomaticJavaDownload": "true", "ShowConsole": "false", "ShowConsoleOnError": "false",
        "CloseAfterLaunch": "false", "QuitAfterGameStop": "true", "AutoCloseConsole": "true"})
    mem = MANIFEST["memory_mb"]
    ini_set(INSTANCE / "instance.cfg", "General", {
        "ConfigVersion": "1.3", "InstanceType": "OneSix", "name": MANIFEST["name"], "iconKey": "default",
        "JavaPath": Path(java).as_posix(), "OverrideJavaLocation": "true", "OverrideMemory": "true",
        "MinMemAlloc": mem["min"], "MaxMemAlloc": mem["max"], "OverrideJavaArgs": "true",
        "JvmArgs": '"' + " ".join(["-Dfile.encoding=UTF-8", *extra_jvm_args()]) + '"',
        "notes": "Managed by Omo Space."})
    ver = MANIFEST["minecraft"]["version"]
    pack = {"formatVersion": 1, "components": [
        {"important": True, "uid": "net.minecraft", "version": ver},
        {"uid": "net.fabricmc.fabric-loader", "version": MANIFEST["fabric"]["loader"]},
        {"uid": "org.lwjgl3", "version": MANIFEST["lwjgl"]}]}
    write_if_changed(INSTANCE / "mmc-pack.json", json.dumps(pack, indent=2).encode())
    link(INSTANCE / ".minecraft", GAME)
    say(f"Prism: private data dir {PRISM_DIR}")


def prism_account():
    """The name on the private Prism's Microsoft account, if it has one."""
    try:
        accounts = json.loads((PRISM_DIR / "accounts.json").read_text()).get("accounts", [])
    except (OSError, ValueError, AttributeError):
        return None
    names = [a.get("profile", {}).get("name") for a in accounts if a.get("type") == "MSA"]
    return next((n for n in names if n), None)


def account_status():
    if client_id():
        account = omo_auth.load(ACCOUNT)
        ok = bool(account) and account.get("client_id") == client_id()
        return {"mode": "omo", "signed_in": ok, "name": account["profile"]["name"] if ok else None}
    name = prism_account()
    return {"mode": "prism", "signed_in": bool(name), "name": name}


def pick_python():
    """The Python the services run. systemd and launchd run without the
    user's shell, so pyenv/conda shims break there; the bridge needs 3.11+
    (macOS's /usr/bin/python3 is 3.9)."""
    cands = ["/usr/bin/python3"] if OS == "linux" else []
    cands += ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"] if OS == "macos" else []
    cands += [shutil.which("python3"), sys.executable]
    for cand in cands:
        if cand and Path(cand).exists() and (cand == sys.executable or runs(
                cand, "-c", "import sys; sys.exit(sys.version_info < (3, 11))")):
            return cand
    return sys.executable


def desktop_exec_arg(value):
    """Quote one Exec= argument (Desktop Entry spec: quoting, then string escapes)."""
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", str(value).replace("%", "%%")) + '"'
    return quoted.replace("\\", "\\\\")


def write_desktop_entry():
    """The app-menu entry points at wherever this checkout lives. Named after
    the GTK application id so shells match the window to it; the icon goes
    into the user's icon theme under the same name. Linux only: the macOS and
    Windows installers make their own shortcut."""
    if OS != "linux":
        return
    share = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
    write_if_changed(share / f"icons/hicolor/scalable/apps/{APP_ID}.svg", (HERE / "assets/omo-icon.svg").read_bytes())
    entry = ("[Desktop Entry]\nType=Application\nName=Omo Space\n"
             "Comment=Hermes and your terminals, inside Minecraft\n"
             f"Exec={desktop_exec_arg(pick_python())} {desktop_exec_arg(HERE / 'omo-launcher.py')}\n"
             f"Icon={APP_ID}\nTerminal=false\nCategories=Game;Development;\n"
             f"StartupWMClass={APP_ID}\nStartupNotify=true\nSingleMainWindow=true\n")
    write_if_changed(share / f"applications/{APP_ID}.desktop", entry.encode())


# --------------------------------------------------------------------------- services
def unit_value(value):
    """A value for a quoted unit-file argument: % is a specifier, so double it.
    Quotes, backslashes, $ and newlines have different meanings in ExecStart=
    and Environment=, so refuse them rather than guess."""
    value = str(value)
    if any(c in value for c in '"\\$\n'):
        raise SystemExit(f"cannot write a systemd unit for a path containing quotes, backslashes, $ "
                         f"or newlines: {value!r}")
    return value.replace("%", "%%")


def render_unit(template, subs):
    for key, value in subs.items():
        template = template.replace(f"@{key}@", unit_value(value))
    left = sorted(set(re.findall(r"@[A-Z_]+@", template)))
    if left:
        raise SystemExit(f"unit template has unfilled placeholders: {', '.join(left)}")
    return template


def systemctl(*args):
    exe = shutil.which("systemctl")
    if not exe:
        raise SystemExit("systemd is required for the Omo services (systemctl was not found). "
                         "Without it, run the session server and bridge by hand: see README.md.")
    p = subprocess.run([exe, "--user", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"systemctl --user {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
    return p


def bridge_get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3) as r:
            h = json.loads(r.read().decode())
        return h if isinstance(h, dict) else None
    except urllib.error.HTTPError as exc:
        exc.close()
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def bridge_health():
    return bridge_get("/health")


def port_holder(port):
    """Who listens on 127.0.0.1:<port>, e.g. 'python3 (pid 42)', or None."""
    try:
        out = subprocess.run(["ss", "-Hltnp", "sport", "=", f":{port}"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if out is None:
        with socket.socket() as s:
            s.settimeout(1)
            return "another program" if s.connect_ex(("127.0.0.1", port)) == 0 else None
    if not out.strip():
        return None
    m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', out)
    return f"{m.group(1)}" if m else "another user's program"


def bridge_problem(*health):
    """None when the bridge answers AND its session server is up; else why not.
    Pass an already fetched /health result (even None) to skip asking again."""
    h = health[0] if health else bridge_health()
    if h and h.get("ok") and h.get("tmux"):
        return None
    where = {"macos": ("`launchctl print gui/$(id -u)/space.omo.research.tmux`",
                       f"{OMO / 'bridge.log'}"),
             "windows": ("`wsl systemctl --user status omo-tmux`", "`wsl journalctl --user -u omo-bridge`")}
    tmux_hint, bridge_hint = where.get(OS, ("`systemctl --user status omo-tmux`", "`journalctl --user -u omo-bridge`"))
    if h and h.get("ok"):
        return f"the session server (omo-tmux) is not running: see {tmux_hint}"
    holder = port_holder(PORT) if OS == "linux" else None
    if holder:
        return f"port {PORT} is held by {holder}, which does not answer as the Omo bridge"
    return f"the Omo bridge is not running on 127.0.0.1:{PORT}: see {bridge_hint}"


def wait_ready(timeout):
    end = time.monotonic() + timeout
    while (problem := bridge_problem()) and time.monotonic() < end:
        time.sleep(0.5)
    return problem


# macOS: launchd user agents instead of systemd units. Same two services,
# same split: the tmux server owns the shells, the bridge only attaches.
def launch_agent(label, argv, env, log):
    return plistlib.dumps({
        "Label": label, "ProgramArguments": [str(a) for a in argv],
        "EnvironmentVariables": {k: str(v) for k, v in env.items()},
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 2,
        "ProcessType": "Interactive", "StandardOutPath": str(log), "StandardErrorPath": str(log),
        "WorkingDirectory": str(HOME)})


def launch_agents(tmux):
    path = f"{Path(tmux).parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    hermes = find_hermes()
    return {
        "omo-tmux.service": launch_agent(LABELS["omo-tmux.service"], [tmux, "-D", "-L", "omo", "-f", TMUX_CONF],
                                         {"OMO_SESSION_SERVER": "1", "PATH": path, "LANG": "en_US.UTF-8"},
                                         OMO / "tmux.log"),
        "omo-bridge.service": launch_agent(LABELS["omo-bridge.service"], [pick_python(), REPO / "bridge/omobridge.py"],
                                           {"OMO_DATA": OMO, "OMO_TMUX": tmux, "OMO_HERMES_BIN": hermes or "",
                                            "PATH": path, "OMO_OPEN": "open", "LANG": "en_US.UTF-8"},
                                           OMO / "bridge.log"),
    }


def launchctl(*args, check=True):
    p = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"launchctl {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
    return p


def ensure_services_macos(tmux):
    domain = f"gui/{os.getuid()}"
    changed = set()
    if write_if_changed(TMUX_CONF, (REPO / "config/omo-tmux.conf").read_bytes()):
        changed.add("tmux.conf")
    for unit, plist in launch_agents(tmux).items():
        dest = LAUNCH_AGENTS / f"{LABELS[unit]}.plist"
        if write_if_changed(dest, plist):
            changed.add(unit)
        loaded = launchctl("print", f"{domain}/{LABELS[unit]}", check=False).returncode == 0
        if loaded and unit == "omo-bridge.service" and unit in changed:
            launchctl("bootout", f"{domain}/{LABELS[unit]}", check=False)
            loaded = False
        if not loaded:              # a changed tmux agent waits: reloading it would end running shells
            launchctl("bootstrap", domain, str(dest))
    if changed & {"omo-tmux.service", "tmux.conf"}:
        say("services: session server settings changed; they apply the next time omo-tmux starts "
            "(it is not restarted: that would end running shells)")
    problem = wait_ready(8)
    if problem:
        say(f"services: {problem}; restarting the bridge")
        launchctl("kickstart", "-k", f"{domain}/{LABELS['omo-bridge.service']}")
        problem = wait_ready(10)
    if problem:
        raise SystemExit(f"services: {problem}")
    say("services: bridge and session server ready")


# Windows: the session server, bridge and Hermes live in WSL2, where tmux,
# bash and systemd exist; this same script runs there in its Linux role.
# WSL forwards 127.0.0.1 ports to Windows, so the game reaches the bridge as
# on Linux. The bridge token is copied out so the mod can read it.
def wsl(*args, **kw):
    exe = shutil.which("wsl") or shutil.which("wsl.exe")
    if not exe:
        raise SystemExit("Windows needs WSL2 for the Omo session server. Install it once from an "
                         "admin PowerShell with `wsl --install`, restart, then run setup again.")
    argv = [exe, *(["-d", WSL_DISTRO] if WSL_DISTRO else []), *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=kw.pop("timeout", 900), **kw)


def wsl_path(p):
    """This Windows path as WSL sees it (C:\\x -> /mnt/c/x)."""
    p = str(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
    if not m:
        raise SystemExit(f"cannot reach {p} from WSL (only drive-letter paths are supported)")
    return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")


def ensure_services_windows():
    probe = wsl("-e", "sh", "-c", "uname -s; command -v python3; command -v systemctl; "
                                  "cat /proc/sys/fs/binfmt_misc/WSLInterop >/dev/null 2>&1 && echo interop",
                timeout=60)
    out = probe.stdout.split()
    if probe.returncode != 0 or not out or out[0] != "Linux":
        raise SystemExit("WSL is installed but has no Linux distribution yet: run `wsl --install -d Ubuntu`, "
                         f"finish its first start, then run setup again. ({(probe.stderr or '').strip()[:200]})")
    if not any(o.endswith("python3") for o in out):
        raise SystemExit("WSL has no python3: run `wsl sudo apt install -y python3`, then setup again")
    if not any(o.endswith("systemctl") for o in out):
        raise SystemExit("WSL needs systemd for the Omo services: add `[boot]` / `systemd=true` to "
                         "/etc/wsl.conf inside WSL, run `wsl --shutdown`, then setup again")
    # The Linux setup inside WSL: tmux, units, bridge (the game runs natively
    # on Windows). It finds Hermes in WSL by itself. Linger keeps the user's
    # systemd, and so the shells, running with no WSL terminal open.
    wsl("-e", "sh", "-c", 'loginctl enable-linger "$(id -un)"', timeout=30)
    setup_py = wsl_path(HERE / "omo_setup.py")
    p = wsl("-e", "env", "OMO_PLATFORM=linux-x86_64", f"OMO_BRIDGE_PORT={PORT}",
            "python3", setup_py, "services")
    for line in (p.stdout or "").splitlines():
        say(f"  wsl: {line}")
    if p.returncode != 0:
        last = ((p.stderr or p.stdout).strip().splitlines() or ["(no output)"])[-1]
        raise SystemExit(f"services in WSL failed: {last}")
    tok = wsl("-e", "sh", "-c", 'cat "${OMO_DATA:-$HOME/.local/share/omo}/token"', timeout=30)
    token = tok.stdout.strip()
    if tok.returncode != 0 or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise SystemExit("services: the bridge in WSL has no token yet; see `wsl journalctl --user -u omo-bridge`")
    write_if_changed(OMO / "token", token.encode())
    problem = wait_ready(10)
    if problem:
        raise SystemExit(f"services: {problem}. WSL must forward localhost (the default); check "
                         "`localhostForwarding` in %UserProfile%\\.wslconfig")
    say("services: bridge and session server ready (in WSL)")


def ensure_services(tmux):
    if os.environ.get("OMO_NO_SERVICES"):
        say("services: skipped (OMO_NO_SERVICES set)")
        return
    if OS == "macos":
        return ensure_services_macos(tmux)
    if OS == "windows":
        return ensure_services_windows()
    systemctl("--version")
    changed = set()
    if write_if_changed(TMUX_CONF, (REPO / "config/omo-tmux.conf").read_bytes()):
        changed.add("tmux.conf")
    subs = {"TMUX": tmux, "TMUX_CONF": TMUX_CONF, "PYTHON": pick_python(),
            "BRIDGE": REPO / "bridge/omobridge.py", "OMO_DATA": OMO, "HERMES": find_hermes() or ""}
    for unit in UNITS:
        if write_if_changed(SYSTEMD_USER / unit, render_unit((REPO / "scripts" / unit).read_text(), subs).encode()):
            changed.add(unit)
    if changed:
        systemctl("daemon-reload")
    systemctl("enable", "--now", *UNITS)          # starts what is stopped, restarts nothing
    if changed & {"omo-tmux.service", "tmux.conf"}:
        say("services: session server settings changed; they apply the next time omo-tmux starts "
            "(it is not restarted: that would end running shells)")
    restarted = "omo-bridge.service" in changed
    if restarted:
        systemctl("restart", "omo-bridge.service")
    problem = wait_ready(8)
    if problem and not restarted:
        say(f"services: {problem}; restarting the bridge")
        systemctl("restart", "omo-bridge.service")
        problem = wait_ready(10)
    if problem:
        raise SystemExit(f"services: {problem}")
    say("services: bridge and session server ready")


# --------------------------------------------------------------------------- hermes
def find_hermes():
    for cand in (shutil.which("hermes"), HOME / ".local/bin/hermes", HOME / ".hermes/hermes-agent/hermes"):
        if cand and Path(cand).exists():
            return Path(cand)
    return None


def hermes_status():
    h = find_hermes()
    if not h:
        return {"installed": False}
    model = provider = None
    try:
        lines = (HOME / ".hermes/config.yaml").read_text().splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        if line.strip() == "model:":
            for sub in lines[i + 1:i + 6]:
                key, _, value = sub.strip().partition(":")
                if key == "default":
                    model = value.strip()
                elif key == "provider":
                    provider = value.strip()
            break
    return {"installed": True, "path": str(h), "model": model, "provider": provider}


def report_hermes():
    h = hermes_status()
    if h["installed"]:
        say(f"Hermes: {h['path']}  model={h.get('model')} provider={h.get('provider')}")
    else:
        say("Hermes: NOT found. Install it from https://hermes-agent.nousresearch.com then run setup again.")
        say("        Terminal blocks work without Hermes; Hermes Computers will show a shell instead.")


# --------------------------------------------------------------------------- guards
@contextlib.contextmanager
def setup_lock():
    """One setup/repair/play at a time: two would race on downloads and units."""
    OMO.mkdir(parents=True, exist_ok=True)
    with open(OMO / ".setup.lock", "w") as fh:
        try:
            if WIN:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:                       # BlockingIOError / PermissionError (Windows)
            raise SystemExit("another Omo setup, repair or play is already running; "
                             "wait for it to finish") from None
        yield


def java_processes():
    """[(pid, argv text)] for this user's Java processes, macOS and Windows.
    (Linux reads /proc directly in instance_pids.)"""
    try:
        if WIN:
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe' or Name='javaw.exe'\" | "
                  "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            rows = [line.split("\t", 1) for line in out.splitlines() if "\t" in line]
        else:
            out = subprocess.run(["ps", "-ww", "-U", str(os.getuid()), "-o", "pid=,args="],
                                 capture_output=True, text=True, timeout=10).stdout
            rows = [line.strip().split(None, 1) for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for row in rows:
        if len(row) == 2 and row[0].strip().isdigit():
            exe = re.split(r"\s", row[1].strip().strip('"'), maxsplit=1)[0]
            if re.search(r"(^|[\\/])javaw?(\.exe)?\"?$", exe, re.I) or "/bin/java " in row[1] + " ":
                found.append((int(row[0]), row[1]))
    return found


def mac_cwds(pids):
    """{pid: working directory} on macOS, from lsof."""
    if not pids:
        return {}
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(map(str, pids))],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    cwds, pid = {}, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def instance_pids(root=None):
    """PIDs of this user's Java processes running the game in `root` (default:
    Omo's game dir): their working directory is in it, or an argument names it.
    Omo's own launches also carry -Domo.game=<dir> so the argument check finds them."""
    root = Path(root or GAME)
    real = str(root.resolve())
    if OS != "linux":
        procs = java_processes()
        cwds = mac_cwds([p for p, _ in procs]) if OS == "macos" else {}
        names = {str(root), real}
        if root == GAME:        # Prism starts Java from its instance and passes the game dir on stdin
            names |= {str(INSTANCE), str(INSTANCE.resolve())}
        hits = []
        for pid, args in procs:
            norm = args.replace("\\", "/")
            cwd = cwds.get(pid, "")
            if (cwd == real or cwd.startswith(real + os.sep)
                    or any(re.search(re.escape(n.replace("\\", "/")) + r"(?=[/\"\s]|$)", norm, re.I if WIN else 0)
                           for n in names)):
                hits.append(pid)
        return hits
    needle = str(root).encode() + b"/"
    pids = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if d.stat().st_uid != os.getuid():
                continue
            argv = (d / "cmdline").read_bytes().split(b"\0")
            if os.path.basename(argv[0]) != b"java":
                continue
            cwd = os.readlink(d / "cwd")
        except OSError:
            continue
        if cwd == real or cwd.startswith(real + "/") or any(needle in a for a in argv[1:]):
            pids.append(int(d.name))
    return pids


def fuse_headroom():
    """AppImages need a FUSE mount. The kernel default allows 1000; stale
    mounts from other AppImages (seen: hundreds left by a crashed app) make
    every AppImage fail with 'too many FUSE filesystems mounted'. Say so
    plainly instead of letting Prism print a cryptic error."""
    try:
        with open("/proc/mounts") as fh:
            count = sum(1 for line in fh if line.split()[2].startswith("fuse"))
    except OSError:
        return True
    if count >= 990:
        say(f"WARNING: {count} FUSE mounts are active; AppImages cannot start above 1000.")
        say("  Stale mounts can be removed with:  for m in /tmp/.mount_*; do fusermount3 -u \"$m\"; done")
        return False
    return True


# --------------------------------------------------------------------------- commands
def installed():
    """The game runtime is complete for the current manifest (cheap checks)."""
    stamp = MCDIR / ".installed"
    return (stamp.is_file() and stamp.read_text() == runtime_stamp() and bool(java_path())
            and all(present(GAME / "mods" / m["file"], m, False) for m in MANIFEST["mods"])
            and (GAME / "mods" / MANIFEST["omolife"]["file"]).exists()
            and (client_id() is not None or (OWN_PRISM.exists() if OS != "linux" else
                                             present(OWN_PRISM, MANIFEST["prism"][ARCH], False))
                 and (INSTANCE / "mmc-pack.json").exists()))


def status():
    health = bridge_health()
    s = {
        "installed": installed(),
        "running": bool(instance_pids()),
        "account": account_status(),
        "mods": {m["file"]: present(GAME / "mods" / m["file"], m, False) for m in MANIFEST["mods"]},
        "omolife": (GAME / "mods" / MANIFEST["omolife"]["file"]).exists(),
        "tmux": OS == "windows" or (find_tmux_macos() is not None if OS == "macos" else (OMO / "bin/tmux").exists()),
        "platform": ARCH,
        "bridge": health,
        "bridge_problem": bridge_problem(health),
        "hermes": hermes_status(),
        "token": (OMO / "token").exists(),
        "worlds_to_copy": [] if (RUNTIME / ".migrated").exists() else old_worlds(),
        "download_mb": MANIFEST["download_mb"],
        "game_dir": str(GAME),
    }
    print(json.dumps(s, indent=1))
    return s


def setup(verify_all=False):
    require_supported()
    with setup_lock():
        say("== Omo Space " + ("repair (saves, sign-in, token, workspaces and Hermes config are never touched)"
                                  if verify_all else "setup"))
        running = instance_pids()
        titles = ["Session server", *([] if running else ["Java", "Minecraft", "Mods", "Worlds"]),
                  *([] if running or client_id() else ["Game engine"]), "Omo services"]
        steps = enumerate(titles, 1)
        say(f"== plan: {' | '.join(titles)}")

        def step():         # the window shows "step 3 of 7: Minecraft" from these lines
            i, title = next(steps)
            say(f"== step {i}/{len(titles)}: {title}")

        step()
        tmux = ensure_tmux(verify_all)
        if not running:
            step()
            java = ensure_java(verify_all)
            step()
            ensure_minecraft(verify_all)
            step()
            ensure_mods()
            step()
            migrate()
            ensure_game_dir()
            if not client_id():
                step()
                ensure_prism_runtime(java)
        step()
        write_desktop_entry()
        report_hermes()
        ensure_services(tmux)
        if running:
            raise SystemExit("Game files were not checked because Minecraft is running. "
                             "Quit the game, then run this again.")
        say("== done. Ready to play.")


def repair():
    setup(verify_all=True)


def services():
    with setup_lock():
        ensure_services(ensure_tmux())


def signin():
    cid = client_id()
    if not cid:
        raise SystemExit("Omo has no Microsoft client ID yet: press Play, and sign in once in the "
                         "Microsoft window that opens.")

    def show(code):
        say(f"signin: code {code['user_code']} {code['verification_uri']} {code.get('expires_in', 900)}")
    try:
        account = omo_auth.sign_in(cid, ACCOUNT, show)
    except omo_auth.AuthError as exc:
        raise SystemExit(f"sign-in failed: {exc}") from None
    say(f"signin: signed in as {account['profile']['name']}")


def signout():
    omo_auth.sign_out(ACCOUNT)
    (PRISM_DIR / "accounts.json").unlink(missing_ok=True)
    say("signed out")


def game_env():
    """GLFW is happier on XWayland than native Wayland for this stack. Without
    WAYLAND_DISPLAY, Qt and GLFW would still find the default Wayland socket
    through XDG_SESSION_TYPE, so point both at DISPLAY explicitly. (Linux only.)"""
    env = dict(os.environ)
    if OS == "linux" and env.get("DISPLAY"):
        env.pop("WAYLAND_DISPLAY", None)
        env.update(XDG_SESSION_TYPE="x11", QT_QPA_PLATFORM="xcb")
    return env


def write_argfile(game):
    """Windows has no /dev/fd: the game arguments (with the access token) go
    to a 0600-equivalent file in the user's own data dir, which a helper
    deletes as soon as Java has read it. Never on the command line."""
    path = RUNTIME / f".launch-{os.getpid()}.args"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(argfile_line(a) for a in game) + "\n")
    return path


def launch_windows(cmd, env, argfile=None):
    """Windows has no exec: run the game (or Prism) as a child with no console
    and stay alive until it ends, as exec would on Linux, so the window's
    watch on this process still means "the game is up". The argfile with the
    access token is deleted once Java has had time to read it."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, cwd=GAME, env=env, creationflags=flags,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if argfile:
            try:
                proc.wait(ARGFILE_GRACE)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if argfile:
            argfile.unlink(missing_ok=True)
    sys.exit(proc.wait())


def play(show_prism=False):
    require_supported()
    argfile = None
    with setup_lock():
        if not installed():
            raise SystemExit("Omo Space is not set up yet: run setup first")
        if instance_pids():
            say(f"{MANIFEST['name']} is already running.")
            return
        problem = bridge_problem()
        if problem and os.environ.get("OMO_NO_SERVICES"):
            raise SystemExit(f"cannot play: {problem}")
        if problem:
            say(f"bridge: {problem}; starting services")
            ensure_services(ensure_tmux())                # raises with the reason if still not ready
        migrate()                                         # worlds that were busy at setup time
        env = game_env()
        if client_id():
            try:
                account = omo_auth.fresh(client_id(), ACCOUNT)
            except omo_auth.AuthError as exc:
                raise SystemExit(f"cannot play: {exc}") from None
            jvm, game = launch_args(version_json(), account, java_path())
            if WIN:
                argfile = write_argfile(game)
                cmd = [*jvm, f"@{argfile}"]
            else:
                r, w = os.pipe()
                os.write(w, "\n".join(argfile_line(a) for a in game).encode() + b"\n")
                os.close(w)
                os.set_inheritable(r, True)
                cmd = [*jvm, f"@/dev/fd/{r}"]
            say(f"launching: Minecraft {MANIFEST['minecraft']['version']} as {account['profile']['name']}")
        else:
            if OS == "linux" and not fuse_headroom():
                raise SystemExit("cannot start the AppImage until FUSE mounts are freed (see above)")
            # Launched with -l, Prism opens no window of its own (only the game's);
            # it stays on the real display because Qt's offscreen platform stops
            # Prism from ever starting the game (verified with a real account).
            headless = prism_account() and not show_prism
            cmd = [str(OWN_PRISM), "--dir", str(PRISM_DIR), "-l", INSTANCE_ID]
            say("launching: Minecraft " + ("(headless Prism)" if headless else "(Prism sign-in window)"))
    if WIN:
        launch_windows(cmd, env, argfile)
    os.chdir(GAME)
    os.execve(cmd[0], cmd, env)


COMMANDS = {"status": status, "setup": setup, "repair": repair, "play": play, "services": services,
            "signin": signin, "signout": signout}

if __name__ == "__main__":
    op = sys.argv[1] if len(sys.argv) > 1 else "status"
    if op not in COMMANDS:
        sys.exit(f"usage: {Path(sys.argv[0]).name} {{{'|'.join(COMMANDS)}}} [--show]")
    COMMANDS[op](*([True] if op == "play" and "--show" in sys.argv else []))
