package space.omo.life.client;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;


import org.cef.CefClient;
import org.cef.CefSettings;
import org.cef.browser.CefBrowser;
import org.cef.browser.CefFrame;
import org.cef.handler.CefDisplayHandler;
import org.cef.handler.CefLoadHandler;
import org.cef.network.CefRequest;


/**
 * JCEF handlers MCEF Modern does not install.
 * Like {@link McefRuntime}, only loaded when MCEF is present.
 *
 * <p><b>New tabs.</b> JCEF's native OnBeforePopup cancels every popup of an
 * off-screen browser before Java hears of it, so target=_blank links,
 * window.open and middle-/Ctrl+clicks did nothing. A small script injected
 * into every frame (on load start and end) turns those into a console
 * message with a private marker; the display handler swallows it and hands
 * the URL to the page's owner, which opens it as a tab. window.open returns
 * null, as when a popup blocker is active.
 */
final class CefHooks {


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
            if (h != null && !url.isEmpty() && !url.startsWith("about:") && newTabAllowed(browser)) {
                h.popup().accept(url);
            }
            return true;                        // ours: not printed
        }

        /**
         * Any frame on a page (ads and cross-origin iframes included) can print
         * the new-tab marker, so at most {@link #NEW_TAB_BURST} tabs per
         * {@link #NEW_TAB_WINDOW_MS} come from one browser.
         */
        private static boolean newTabAllowed(CefBrowser browser) {
            long now = System.currentTimeMillis();
            synchronized (NEW_TAB_TIMES) {
                long[] times = NEW_TAB_TIMES.computeIfAbsent(browser, b -> new long[NEW_TAB_BURST]);
                int oldest = 0;
                for (int i = 1; i < times.length; i++) {
                    if (times[i] < times[oldest]) {
                        oldest = i;
                    }
                }
                if (times[oldest] != 0 && now - times[oldest] < NEW_TAB_WINDOW_MS) {
                    return false;
                }
                times[oldest] = now;
                return true;
            }
        }

        @Override
        public boolean onCursorChange(CefBrowser browser, int cursorType) {
            var render = browser.getRenderHandler();
            return render != null && render.onCursorChange(browser, cursorType);
        }
    }

    /** Console marker of a new-tab request from {@link #NEW_TAB_JS}. */
    private static final String NEW_TAB = "\u0001omo-newtab ";
    private static final int NEW_TAB_BURST = 3;
    private static final long NEW_TAB_WINDOW_MS = 10_000;
    private static final java.util.Map<CefBrowser, long[]> NEW_TAB_TIMES = new java.util.WeakHashMap<>();

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
}
