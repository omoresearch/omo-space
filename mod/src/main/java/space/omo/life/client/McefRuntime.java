package space.omo.life.client;

import java.lang.reflect.Method;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Consumer;

import com.mojang.blaze3d.textures.GpuTextureView;

import net.dimaskama.mcef.api.MCEFApi;
import net.dimaskama.mcef.api.MCEFBrowser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import org.cef.browser.CefBrowser;

import space.omo.life.OmoLife;

/**
 * The only class that references MCEF. {@link WebPages} calls into it only
 * after checking that the {@code mcef-modern} mod is loaded, so without MCEF
 * this class is never loaded and nothing throws NoClassDefFoundError.
 */
final class McefRuntime {

    private static boolean initRequested;
    /** Published only by the completion callback on the client thread. */
    private static MCEFApi api;
    /** JCEF's own key entry point, to send keys MCEF translates wrongly on Linux (see {@link PageKeys}). */
    private static final Method SEND_KEY = sendKeyMethod();

    private McefRuntime() {
    }

    static boolean ready() {
        // MCEF's getInstance() starts initialization and joins its future.
        // A readiness probe must never download/wait on the render thread.
        return api != null;
    }

    /**
     * Start Chromium once. {@code status} receives a progress line now and
     * "" (or an error) later; {@code onReady} runs on the client thread.
     */
    static void initialize(Consumer<String> status, Runnable onReady) {
        if (initRequested) {
            return;
        }
        initRequested = true;
        MCEFApi.Initialization init = MCEFApi.initialize();
        status.accept("Starting Chromium: " + init.getStage());
        init.getFuture().whenComplete((readyApi, err) -> Minecraft.getInstance().execute(() -> {
            if (err != null || readyApi == null) {
                status.accept("Chromium failed to start: " + (err == null ? "unknown error" : err.getMessage()));
                return;
            }
            api = readyApi;
            status.accept("");
            CefHooks.started();
            onReady.run();
        }));
    }

    /**
     * A new page; requires {@link #ready()}. Opaque, like a real browser: a
     * page without a background of its own paints on white, never on
     * nothing (a transparent page let the world show through the glass).
     */
    static WebView create(String url) {
        return create(url, null);
    }

    /**
     * A new page whose new-tab requests go to {@code events} (may be null:
     * ignored). {@link CefHooks} also gives every frame of it, on Linux,
     * {@link PageKeys#CODE_SHIM} as it starts loading.
     */
    static WebView create(String url, WebView.Events events) {
        View view = new View(api.createBrowser(url, false));
        if (view.browser != null) {
            CefHooks.attach(view.browser.getCefBrowser(), title -> view.title = title == null ? "" : title,
                target -> {
                    if (events != null) {
                        Minecraft.getInstance().execute(() -> events.newTab(target));
                    }
                });
        }
        return view;
    }

    private static Method sendKeyMethod() {
        if (!PageKeys.LINUX) {
            return null;
        }
        try {
            // Package-private class, protected method: reached by reflection (JCEF is in no named module).
            Class<?> impl = Class.forName("org.cef.browser.CefBrowser_N", false, CefBrowser.class.getClassLoader());
            Method m = impl.getDeclaredMethod("sendKeyEvent", java.awt.event.KeyEvent.class);
            m.setAccessible(true);
            return m;
        } catch (ReflectiveOperationException | RuntimeException e) {
            OmoLife.LOG.warn("web pages: JCEF key entry point not found, using MCEF's key mapping ({})", e.toString());
            return null;
        }
    }

    /**
     * One page. Keys go through {@link PageKeys} (MCEF's own mapping only
     * when the JCEF entry point is missing or off Linux), Enter also types
     * its character, and keys held down are remembered so a page that loses
     * the keyboard (Esc, the address bar, letting go of a wall) gets their
     * key-up: otherwise a web game kept walking with W no longer pressed.
     */
    private static final class View implements WebView {
        private final MCEFBrowser browser;
        /** GLFW key -> its modifiers when pressed, for keys the page saw go down and not up. */
        private final Map<Integer, Integer> held = new LinkedHashMap<>();
        /** Written by CEF's UI thread (title changes), read by the client thread. */
        private volatile String title = "";

        View(MCEFBrowser browser) {
            this.browser = browser;
        }

        @Override
        public String title() {
            return title;
        }

        @Override
        public GpuTextureView texture() {
            return browser.getTextureView();
        }

        @Override
        public void resize(int width, int height) {
            browser.resize(width, height);
        }

        @Override
        public void load(String url) {
            browser.getCefBrowser().loadURL(url);
        }

        @Override
        public String url() {
            return browser.getCefBrowser().getURL();
        }

        @Override
        public void back() {
            browser.getCefBrowser().goBack();
        }

        @Override
        public void forward() {
            browser.getCefBrowser().goForward();
        }

        @Override
        public void reload() {
            browser.getCefBrowser().reload();
        }

        @Override
        public void frameRate(int fps) {
            browser.getCefBrowser().setWindowlessFrameRate(fps);
        }

        @Override
        public void volume(double level) {
            CefHooks.setVolume(browser.getCefBrowser(), level);
        }

        @Override
        public void focus(boolean focused) {
            if (!focused) {
                releaseKeys();
            }
            browser.setFocus(focused);
        }

        @Override
        public void releaseKeys() {
            Map<Integer, Integer> keys = new LinkedHashMap<>(held);
            held.clear();
            keys.forEach((key, mods) -> send(java.awt.event.KeyEvent.KEY_RELEASED, new KeyEvent(key, 0, mods)));
        }

        @Override
        public void mouseMoved(int x, int y) {
            browser.onMouseMoved(x, y);
        }

        @Override
        public void mouseClicked(MouseButtonEvent event, boolean doubleClick) {
            browser.onMouseClicked(event, doubleClick);
        }

        @Override
        public void mouseReleased(MouseButtonEvent event) {
            browser.onMouseReleased(event);
        }

        @Override
        public void mouseScrolled(int x, int y, double amount) {
            browser.onMouseScrolled(x, y, amount);
        }

        @Override
        public void keyPressed(KeyEvent event) {
            held.put(event.key(), event.modifiers());
            send(java.awt.event.KeyEvent.KEY_PRESSED, event);
            if (PageKeys.typesReturn(event.key())) {
                browser.onCharTyped(new CharacterEvent('\r'));
            }
        }

        @Override
        public void keyReleased(KeyEvent event) {
            held.remove(event.key());
            send(java.awt.event.KeyEvent.KEY_RELEASED, event);
        }

        private void send(int id, KeyEvent event) {
            boolean down = id == java.awt.event.KeyEvent.KEY_PRESSED;
            CefBrowser cef = browser.getCefBrowser();
            if (SEND_KEY != null && SEND_KEY.getDeclaringClass().isInstance(cef)) {
                int code = PageKeys.awtKeyCode(event.key());
                int keysym = PageKeys.linuxKeysym(event.key());
                if (code == 0 && keysym == 0) {
                    return;                                // no key a page knows
                }
                try {
                    SEND_KEY.invoke(cef, new java.awt.event.KeyEvent(cef.getUIComponent(), id,
                        System.currentTimeMillis(), PageKeys.awtModifiers(event.modifiers()), code, (char) keysym));
                    return;
                } catch (ReflectiveOperationException | RuntimeException e) {
                    OmoLife.LOG.debug("web pages: key via JCEF failed, using MCEF's", e);
                }
            }
            if (down) {
                browser.onKeyPressed(event);
            } else {
                browser.onKeyReleased(event);
            }
        }

        @Override
        public void charTyped(CharacterEvent event) {
            browser.onCharTyped(event);
        }

        @Override
        public void close() {
            held.clear();
            CefHooks.detach(browser.getCefBrowser());
            browser.close();
        }
    }
}
