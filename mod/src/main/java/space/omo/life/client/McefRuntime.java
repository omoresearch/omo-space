package space.omo.life.client;

import java.util.function.Consumer;

import com.mojang.blaze3d.textures.GpuTextureView;

import net.dimaskama.mcef.api.MCEFApi;
import net.dimaskama.mcef.api.MCEFBrowser;
import net.minecraft.client.Minecraft;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;

/**
 * The only class that references MCEF. {@link WebPages} calls into it only
 * after checking that the {@code mcef-modern} mod is loaded, so without MCEF
 * this class is never loaded and nothing throws NoClassDefFoundError.
 */
final class McefRuntime {

    private static boolean initRequested;

    private McefRuntime() {
    }

    static boolean ready() {
        return MCEFApi.getInstance() != null;
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
        init.getFuture().whenComplete((api, err) -> Minecraft.getInstance().execute(() -> {
            if (err != null || api == null) {
                status.accept("Chromium failed to start: " + (err == null ? "unknown error" : err.getMessage()));
                return;
            }
            status.accept("");
            onReady.run();
        }));
    }

    /**
     * A new page; requires {@link #ready()}. Opaque, like a real browser: a
     * page without a background of its own paints on white, never on
     * nothing (a transparent page let the world show through the glass).
     */
    static WebView create(String url) {
        return new View(MCEFApi.getInstance().createBrowser(url, false));
    }

    private record View(MCEFBrowser browser) implements WebView {
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
        public void focus(boolean focused) {
            browser.setFocus(focused);
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
            browser.onKeyPressed(event);
        }

        @Override
        public void keyReleased(KeyEvent event) {
            browser.onKeyReleased(event);
        }

        @Override
        public void charTyped(CharacterEvent event) {
            browser.onCharTyped(event);
        }

        @Override
        public void close() {
            browser.close();
        }
    }
}
