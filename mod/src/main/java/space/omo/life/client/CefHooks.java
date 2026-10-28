package space.omo.life.client;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Date;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import net.minecraft.client.Minecraft;
import org.cef.CefClient;
import org.cef.CefSettings;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.handler.CefDisplayHandler;
import org.cef.handler.CefLoadHandler;
import org.cef.network.CefRequest;
import org.cef.network.CefCookie;
import org.cef.network.CefCookieManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;


/**
 * JCEF handlers MCEF Modern does not install, and the Firefox cookie import.
 * Like {@link McefRuntime}, only loaded when MCEF is present.
 *
 * <p><b>New tabs.</b> JCEF's native OnBeforePopup cancels every popup of an
 * off-screen browser before Java hears of it, so target=_blank links,
 * window.open and middle-/Ctrl+clicks did nothing. A small script injected
 * into every frame (on load start and end) turns those into a console
 * message with a private marker; the display handler swallows it and hands
 * the URL to the page's owner, which opens it as a tab. window.open returns
 * null, as when a popup blocker is active.
 *
 * <p><b>Cookies (opt-in).</b> With {@code -Domo.firefoxCookies=true} or
 * {@code OMO_FIREFOX_COOKIES=1}: on Chromium start, and whenever Firefox's cookie file
 * changes afterwards (checked once a minute; only cookies Firefox changed are
 * re-set), the user's Firefox cookies are copied in, so sites are
 * logged in like in Firefox. One way only (Firefox is the source); nothing is
 * written back to Firefox, and a cookie Firefox later deletes stays here until
 * it expires. Container and partitioned (third-party) cookies are skipped.
 * Off by default: any page a wall shows (including one another player pinned)
 * would browse with those logins. A specific profile directory with
 * {@code OMO_FIREFOX_PROFILE}.
 */
final class CefHooks {

    /** Same logger name as OmoLife.LOG, without loading the mod's block registry (testable standalone). */
    private static final Logger LOG = LoggerFactory.getLogger("Omo Space");

    private static final class Hooked {
        final Consumer<String> title;
        final Consumer<String> popup;
        /** Page audio level 0..1, applied by {@link #AUDIO_JS} in every frame. */
        volatile double volume = 1;

        Hooked(Consumer<String> title, Consumer<String> popup) {
            this.title = title;
            this.popup = popup;
        }

        Consumer<String> title() {
            return title;
        }

        Consumer<String> popup() {
            return popup;
        }
    }

    /** Our browsers by identity (JCEF hands back the same Java object). */
    private static final Map<CefBrowser, Hooked> BROWSERS = new ConcurrentHashMap<>();
    private static CefClient installedOn;

    private CefHooks() {
    }

    /** Route this browser's title and new-window requests; installs the client handlers once. */
    static void attach(CefBrowser browser, Consumer<String> title, Consumer<String> popup) {
        if (browser == null) {
            return;
        }
        install(browser.getClient());
        BROWSERS.put(browser, new Hooked(title, popup));
    }

    /**
     * Set the page's audio level (0 = silent, 1 = as the page wants) in every
     * frame now; frames that load later pick it up from {@link Load}.
     */
    static void setVolume(CefBrowser browser, double volume) {
        Hooked h = browser == null ? null : BROWSERS.get(browser);
        if (h == null) {
            return;
        }
        h.volume = Math.max(0, Math.min(1, volume));
        String js = audioJs(h.volume);
        try {
            for (String id : browser.getFrameIdentifiers()) {
                CefFrame f = browser.getFrameByIdentifier(id);
                if (f != null) {
                    f.executeJavaScript(js, "omo://audio", 0);
                }
            }
        } catch (RuntimeException ignored) {
            // mid-teardown
        }
    }

    static void detach(CefBrowser browser) {
        if (browser != null) {
            BROWSERS.remove(browser);
        }
    }

    private static synchronized void install(CefClient client) {
        if (client == null || client == installedOn) {
            return;
        }
        installedOn = client;
        client.addDisplayHandler(new Display());
        client.addLoadHandler(new Load());
    }

    /**
     * CefClient asks a set display handler for cursor changes <em>instead
     * of</em> the browser's render handler, so cursors are passed through.
     */
    private static final class Display implements CefDisplayHandler {
        @Override
        public void onAddressChange(CefBrowser browser, CefFrame frame, String url) {
        }

        @Override
        public void onTitleChange(CefBrowser browser, String title) {
            Hooked h = BROWSERS.get(browser);
            if (h != null) {
                h.title().accept(title);
            }
        }

        @Override
        public void onFullscreenModeChange(CefBrowser browser, boolean fullscreen) {
        }

        @Override
        public boolean onTooltip(CefBrowser browser, String text) {
            return false;
        }

        @Override
        public void onStatusMessage(CefBrowser browser, String value) {
        }

        @Override
        public boolean onConsoleMessage(CefBrowser browser, CefSettings.LogSeverity level, String message,
                                        String source, int line) {
            if (message == null || !message.startsWith(NEW_TAB)) {
                return false;
            }
            Hooked h = BROWSERS.get(browser);
            String url = message.substring(NEW_TAB.length()).trim();
            if (h != null && !url.isEmpty() && !url.startsWith("about:")) {
                h.popup().accept(url);
            }
            return true;                        // ours: not printed
        }

        @Override
        public boolean onCursorChange(CefBrowser browser, int cursorType) {
            var render = browser.getRenderHandler();
            return render != null && render.onCursorChange(browser, cursorType);
        }
    }

    /** Console marker of a new-tab request from {@link #NEW_TAB_JS}. */
    private static final String NEW_TAB = "\u0001omo-newtab ";

    /**
     * Idempotent per document. Links that would open elsewhere (any target
     * but _self/_top/_parent, middle button, Ctrl/Shift/Meta) are taken over
     * unless the page already handled the click itself.
     */
    static final String NEW_TAB_JS = String.join("",
        "(function(){if(window.__omoTabs)return;window.__omoTabs=1;",
        "var send=function(u){try{u=new URL(String(u),location.href).href}catch(e){return}",
        "if(/^https?:/i.test(u))console.log('\u0001omo-newtab '+u)};",
        "window.open=function(u){if(u&&String(u)!=='about:blank')send(u);return null};",
        "var pick=function(e){if(e.defaultPrevented)return;",
        "if(e.type==='auxclick'&&e.button!==1)return;",
        "var t=e.target,a=t&&t.closest?t.closest('a[href]'):null;if(!a)return;",
        "var g=(a.getAttribute('target')||'').toLowerCase();",
        "var away=(g&&g!=='_self'&&g!=='_top'&&g!=='_parent')||e.button===1||e.ctrlKey||e.metaKey||e.shiftKey;",
        "if(!away||!/^https?:/i.test(a.href))return;",
        "e.preventDefault();send(a.href)};",
        "window.addEventListener('click',pick);window.addEventListener('auxclick',pick);",
        "})();");

    /**
     * Page audio without a Chromium API for it (JCEF has no per-browser mute
     * or volume): every media element plays at what the page asked for times
     * our level, and every Web Audio context's destination is a gain node at
     * our level. The page still sees and sets its own volume as usual.
     * Idempotent per document; a context created before injection is missed.
     */
    private static final String AUDIO_JS = String.join("",
        "(function(v){var w=window,A=w.__omoAudio;if(!A){A=w.__omoAudio={v:1,g:[]};",
        "var P=HTMLMediaElement.prototype,dv=Object.getOwnPropertyDescriptor(P,'volume'),want=new WeakMap();",
        "var fix=function(e){if(!(e instanceof HTMLMediaElement))return;if(!want.has(e))want.set(e,dv.get.call(e));",
        "dv.set.call(e,want.get(e)*A.v)};",
        "Object.defineProperty(P,'volume',{configurable:true,enumerable:true,",
        "get:function(){return want.has(this)?want.get(this):dv.get.call(this)},",
        "set:function(x){dv.set.call(this,x);want.set(this,dv.get.call(this));dv.set.call(this,want.get(this)*A.v)}});",
        "var op=P.play;P.play=function(){fix(this);return op.apply(this,arguments)};",
        "['play','loadstart','playing'].forEach(function(t){document.addEventListener(t,function(e){fix(e.target)},true)});",
        "var B=w.BaseAudioContext||w.AudioContext,dd=B&&Object.getOwnPropertyDescriptor(B.prototype,'destination');",
        "if(dd&&dd.get)Object.defineProperty(B.prototype,'destination',{configurable:true,enumerable:true,get:function(){",
        "var real=dd.get.call(this);if(w.OfflineAudioContext&&this instanceof w.OfflineAudioContext)return real;",
        "if(!this.__omoG){var g=this.createGain();g.gain.value=A.v;g.connect(real);this.__omoG=g;A.g.push(new WeakRef(g))}",
        "return this.__omoG}});",
        "A.set=function(x){A.v=x;document.querySelectorAll('video,audio').forEach(fix);",
        "A.g.forEach(function(r){var g=r.deref();if(g)g.gain.value=x})};}",
        "A.set(v)})(");

    private static String audioJs(double volume) {
        return AUDIO_JS + String.format(java.util.Locale.ROOT, "%.3f", volume) + ");";
    }

    /**
     * One load handler for everything we inject: JCEF keeps only the first
     * load handler a client is given, so the key-code fix lives here too.
     */
    private static final class Load implements CefLoadHandler {
        private static void inject(CefBrowser browser, CefFrame frame, boolean start) {
            Hooked h = BROWSERS.get(browser);
            if (frame != null && h != null) {
                try {
                    if (start && PageKeys.LINUX) {
                        frame.executeJavaScript(PageKeys.CODE_SHIM, frame.getURL(), 0);
                    }
                    frame.executeJavaScript(NEW_TAB_JS, "omo://tabs", 0);
                    frame.executeJavaScript(audioJs(h.volume), "omo://audio", 0);
                } catch (RuntimeException ignored) {
                    // frame went away
                }
            }
        }

        @Override
        public void onLoadingStateChange(CefBrowser browser, boolean loading, boolean back, boolean forward) {
        }

        @Override
        public void onLoadStart(CefBrowser browser, CefFrame frame, CefRequest.TransitionType type) {
            inject(browser, frame, true);       // early, before most page scripts keep a window.open
        }

        @Override
        public void onLoadEnd(CefBrowser browser, CefFrame frame, int status) {
            inject(browser, frame, false);      // and once the document surely exists
        }

        @Override
        public void onLoadError(CefBrowser browser, CefFrame frame, ErrorCode code, String text, String url) {
        }
    }

    // -- Firefox cookies --------------------------------------------------------------------

    private static final long RESYNC_CHECK_MS = 60_000;
    /**
     * What Firefox had for each cookie at the last import. A re-import only
     * sets cookies Firefox changed since then, so a cookie a site has since
     * rotated inside the game is not overwritten with Firefox's older copy.
     */
    private static final Map<String, String> LAST = new ConcurrentHashMap<>();
    private static final AtomicBoolean IMPORTING = new AtomicBoolean();
    private static volatile Path profile;
    private static volatile long importedStamp;
    private static long lastCheck;

    /** Prints {"profile": dir, "cookies": [[name, value, host, path, expiresMs, secure, httpOnly, createdMs, accessedMs]]}. */
    private static final String EXPORT = String.join("\n",
        "import configparser, json, os, shutil, sqlite3, sys, tempfile, time",
        "home = os.path.expanduser('~')",
        "roots = [home + '/snap/firefox/common/.mozilla/firefox', home + '/.mozilla/firefox',",
        "         home + '/.var/app/org.mozilla.firefox/.mozilla/firefox', home + '/.config/mozilla/firefox']",
        "want = os.environ.get('OMO_FIREFOX_PROFILE', '')",
        "cands = [want] if want else []",
        "for r in roots:",
        "    ini = os.path.join(r, 'profiles.ini')",
        "    if not os.path.isfile(ini): continue",
        "    cp = configparser.RawConfigParser(); cp.read(ini)",
        "    for s in cp.sections():",
        "        if s.startswith('Install'):",
        "            p = cp.get(s, 'Default', fallback=None); rel = p is not None and not os.path.isabs(p)",
        "        elif s.startswith('Profile'):",
        "            p = cp.get(s, 'Path', fallback=None); rel = cp.get(s, 'IsRelative', fallback='1') == '1'",
        "        else: continue",
        "        if p: cands.append(os.path.join(r, p) if rel else p)",
        "def stamp(d):",
        "    fs = [os.path.join(d, f) for f in ('cookies.sqlite', 'cookies.sqlite-wal')]",
        "    return max([os.path.getmtime(f) for f in fs if os.path.exists(f)] or [0])",
        "cands = [c for c in dict.fromkeys(cands) if os.path.isfile(os.path.join(c, 'cookies.sqlite'))]",
        "if not cands:",
        "    print(json.dumps({'error': 'no Firefox profile with cookies found'})); sys.exit(0)",
        "prof = want if want in cands else max(cands, key=stamp)",
        "tmp = tempfile.mkdtemp(prefix='omo-ffcookies-')",
        "try:",
        "    os.chmod(tmp, 0o700)",
        "    for f in ('cookies.sqlite', 'cookies.sqlite-wal'):",
        "        if os.path.exists(os.path.join(prof, f)): shutil.copyfile(os.path.join(prof, f), os.path.join(tmp, f))",
        "    db = sqlite3.connect(os.path.join(tmp, 'cookies.sqlite'))",
        "    now = time.time(); out = []",
        "    for n, v, h, p, e, s, ho, c, a in db.execute(\"select name, value, host, path, expiry, isSecure, isHttpOnly,\"",
        "            \" creationTime, lastAccessed from moz_cookies where originAttributes = ''\"):",
        "        e = e / 1000.0 if e > 1e11 else float(e)",
        "        if not h or e < now: continue",
        "        out.append([n, v, h, p or '/', int(e * 1000), bool(s), bool(ho), int(c // 1000), int(a // 1000)])",
        "    db.close()",
        "finally:",
        "    shutil.rmtree(tmp, ignore_errors=True)",
        "print(json.dumps({'profile': prof, 'cookies': out}))");

    /** Opt-in: {@code -Domo.firefoxCookies=true} or {@code OMO_FIREFOX_COOKIES=1}. */
    private static boolean cookiesWanted() {
        String env = System.getenv("OMO_FIREFOX_COOKIES");
        return Boolean.parseBoolean(System.getProperty("omo.firefoxCookies",
            env == null ? "false" : String.valueOf("1".equals(env) || "true".equalsIgnoreCase(env))));
    }

    /** Chromium is up: import once, then reload the pages that opened without the cookies. */
    static void started() {
        if (cookiesWanted()) {
            importAsync(true);
        }
    }

    /** From the client tick: re-import when Firefox's cookie file changed since the last import. */
    static void maybeResync() {
        long now = System.currentTimeMillis();
        if (now - lastCheck < RESYNC_CHECK_MS || profile == null || IMPORTING.get()) {
            return;
        }
        lastCheck = now;
        if (stamp(profile) > importedStamp) {
            importAsync(false);
        }
    }

    private static long stamp(Path dir) {
        long t = 0;
        for (String f : new String[]{"cookies.sqlite", "cookies.sqlite-wal"}) {
            try {
                t = Math.max(t, Files.getLastModifiedTime(dir.resolve(f)).toMillis());
            } catch (IOException ignored) {
                // no WAL right now
            }
        }
        return t;
    }

    private static void importAsync(boolean reloadAfter) {
        if (!IMPORTING.compareAndSet(false, true)) {
            return;
        }
        Thread t = new Thread(() -> {
            try {
                int n = importNow();
                if (reloadAfter && n > 0) {
                    Minecraft.getInstance().execute(WebPages::reloadAll);
                }
            } catch (Exception e) {
                LOG.warn("Firefox cookie import failed: {}", e.toString());
            } finally {
                IMPORTING.set(false);
            }
        }, "omo-firefox-cookies");
        t.setDaemon(true);
        t.start();
    }

    /** Runs the exporter and hands each cookie to Chromium. Never logs cookie names or values. */
    private static int importNow() throws IOException, InterruptedException {
        String python = Files.isExecutable(Path.of("/usr/bin/python3")) ? "/usr/bin/python3" : "python3";
        Process proc = new ProcessBuilder(python, "-c", EXPORT)
            .redirectError(ProcessBuilder.Redirect.DISCARD)
            .start();
        String out;
        try (InputStream in = proc.getInputStream()) {
            out = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
        if (!proc.waitFor(30, TimeUnit.SECONDS)) {
            proc.destroyForcibly();
            throw new IOException("exporter timed out");
        }
        if (proc.exitValue() != 0 || out.isBlank()) {
            throw new IOException("exporter exited " + proc.exitValue());
        }
        JsonObject result = JsonParser.parseString(out.trim()).getAsJsonObject();
        if (result.has("error")) {
            LOG.info("Firefox cookies not imported: {}", result.get("error").getAsString());
            return 0;
        }
        Path prof = Path.of(result.get("profile").getAsString());
        long stampBefore = stamp(prof);
        CefCookieManager manager = CefCookieManager.getGlobalManager();
        if (manager == null) {
            throw new IOException("no Chromium cookie manager");
        }
        JsonArray cookies = result.getAsJsonArray("cookies");
        boolean first = LAST.isEmpty();
        int ok = 0;
        int changed = 0;
        for (JsonElement el : cookies) {
            JsonArray c = el.getAsJsonArray();
            String key = c.get(0).getAsString() + '\0' + c.get(2).getAsString() + '\0' + c.get(3).getAsString();
            String seen = c.get(1).getAsString() + '\0' + c.get(4).getAsLong();
            if (seen.equals(LAST.put(key, seen))) {
                continue;                       // unchanged in Firefox since the last import
            }
            changed++;
            String host = c.get(2).getAsString();
            boolean domainCookie = host.startsWith(".");
            String bare = domainCookie ? host.substring(1) : host;
            String path = c.get(3).getAsString();
            boolean secure = c.get(5).getAsBoolean();
            CefCookie cookie = new CefCookie(c.get(0).getAsString(), c.get(1).getAsString(),
                domainCookie ? host : "", path, secure, c.get(6).getAsBoolean(),
                new Date(c.get(7).getAsLong()), new Date(c.get(8).getAsLong()),
                true, new Date(c.get(4).getAsLong()));
            String url = (secure || !bare.equals("localhost") ? "https://" : "http://") + bare
                + (path.startsWith("/") ? path : "/" + path);
            try {
                if (manager.setCookie(url, cookie)) {
                    ok++;
                }
            } catch (RuntimeException ignored) {
                // a cookie Chromium will not take; the rest still go in
            }
        }
        profile = prof;
        importedStamp = stampBefore;
        if (first) {
            LOG.info("Firefox cookies: {} of {} imported from {}", ok, cookies.size(), prof);
        } else if (changed > 0) {
            LOG.info("Firefox cookies: {} changed in Firefox, {} updated", changed, ok);
        }
        return ok;
    }
}
