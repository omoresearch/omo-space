package space.omo.life.client;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import com.mojang.blaze3d.textures.GpuTextureView;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.renderer.texture.AbstractTexture;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;

/**
 * The shared browser runtime behind every web monitor.
 *
 * <p>One Chromium (MCEF) instance for the whole game; one <em>page</em> per
 * monitor wall, keyed by the wall's origin panel and capped. A page is
 * created the first time its wall is rendered or opened, kept while the wall
 * stays web-bound and loaded, and closed when the wall is broken, rebound to
 * a terminal (from any client or command), its chunk unloads, or the world
 * is left. Reopening the GUI or re-rendering never creates a second page.
 *
 * <p>Walls change shape: placing or breaking a panel re-forms the wall, often
 * around a new origin. A page whose wall is gone waits {@link #ORPHAN_MS}
 * for a wall with the same address to claim it, and moves there as it is,
 * resized but not reloaded (logins, scroll position and scripts intact).
 *
 * <p>Lightweight by construction:
 * <ul>
 *   <li>render resolution is capped ({@link #MAX_PIXELS}) and scaled to the
 *       monitor's physical size, not the player's screen;</li>
 *   <li>pages nobody looked at for a moment drop to {@link #FPS_HIDDEN} through
 *       CEF's windowless frame-rate control instead of being destroyed, so
 *       their scripts and connections keep running;</li>
 *   <li>above {@link #MAX_PAGES} a new wall shows why it is dark instead of
 *       evicting a page that is showing something.</li>
 * </ul>
 * Without MCEF installed nothing here touches Chromium; walls show a message.
 *
 * <p>Never runaway (see {@link BrowserGuard} for the rules and the 27 Oct
 * crash they answer): every browser is born in {@link #newView} and closed
 * in {@link #closeNow} via {@link #retire}; creation is rate-limited and
 * capped; a browser is closed only once it is {@link #MIN_LIFE_MS} old (one
 * closed mid-creation is not reliably cleaned up by CEF: the crash left
 * ~2000 helper processes behind); and {@link HelperWatchdog} pauses or
 * closes the pages when Chromium's helpers grow past what a dozen walls use.
 */
public final class WebPages {

    public static final int MAX_PAGES = 12;
    /** Tabs per wall; beyond this a new-tab link opens in the current tab. */
    public static final int MAX_TABS = 6;
    /** Wall audio is at full level within this many blocks... */
    public static final double AUDIO_NEAR = 6;
    /** ...and silent from this far. */
    public static final double AUDIO_FAR = 40;
    /** A browser younger than this is closed later, not now (see the class comment). */
    static final long MIN_LIFE_MS = 3000;
    /**
     * A page this far from the player is closed (its wall keeps the pinned
     * URL; the renderer reopens it within its 64-block view distance), so the
     * cap counts the pages around you, not every wall in the world.
     */
    private static final double FAR_BLOCKS = 96;
    public static final int MAX_PIXELS = 1280 * 720;
    /** Pixels per monitor block edge at zoom 100%. */
    public static final int PIXELS_PER_BLOCK = 256;
    public static final int FPS_VISIBLE = 30;
    public static final int FPS_HIDDEN = 2;
    /** How long a page outlives its wall, waiting for the re-formed wall to claim it. */
    public static final long ORPHAN_MS = 3000;

    private static final boolean MCEF = FabricLoader.getInstance().isModLoaded("mcef-modern");

    public static final class Page {
        /** The origin panel of the wall showing this page (it moves when the wall re-forms). */
        BlockPos pos;
        /** The wall's tabs; the wall shows {@link #active}. Empty until Chromium is up. */
        final List<WebView> tabs = new ArrayList<>();
        int active;
        /** The wall's pinned address, as last given to {@link WebPages#open}. */
        String url;
        String status = "Starting browser...";
        int width;
        int height;
        /** Last frame a wall painted this page's texture (0 until it has one). */
        long lastVisibleFrame;
        final long createdAt = System.currentTimeMillis();
        /**
         * Last time a wall drew or opened this page, loaded or not: what "in
         * view" means for throttling and eviction. Not {@link #lastVisibleFrame}:
         * a page still loading has never painted yet is in use (counting it as
         * unseen is what let walls evict each other's pages every frame).
         */
        long lastWanted = createdAt;
        /** When its wall stopped standing, 0 while it stands. */
        long orphanSince;
        boolean throttled;
        /** True while the monitor screen shows this page full-window (it owns the size then). */
        boolean interactive;
        boolean closed;
        /** The wall's own volume, 0..100, and mute; remembered per wall in {@link AudioPrefs}. */
        int volume = 100;
        boolean muted;
        /** The level last sent to the tabs, -1 = never. */
        double sentLevel = -1;

        Page(BlockPos pos, String url) {
            this.pos = pos.immutable();
            this.url = url;
            int[] saved = AudioPrefs.get(this.pos);
            if (saved != null) {
                volume = saved[0];
                muted = saved[1] != 0;
            }
        }

        public int volume() {
            return volume;
        }

        public boolean muted() {
            return muted;
        }

        public void setVolume(int percent) {
            volume = Math.max(0, Math.min(100, percent));
            if (volume > 0) {
                muted = false;
            }
            AudioPrefs.put(pos, volume, muted);
            updateAudio();
        }

        public void toggleMute() {
            muted = !muted;
            AudioPrefs.put(pos, volume, muted);
            updateAudio();
        }

        /**
         * What the tabs play at: the wall's volume, times Minecraft's
         * Master x Jukebox/Note Blocks sliders, times distance (full within
         * {@link #AUDIO_NEAR} blocks, silent from {@link #AUDIO_FAR}) unless
         * you are using the page full-window.
         */
        double level() {
            if (muted || volume == 0) {
                return 0;
            }
            Minecraft mc = Minecraft.getInstance();
            double game = mc.options == null ? 1 : mc.options.getFinalSoundSourceVolume(SoundSource.RECORDS);
            double near = 1;
            if (!interactive && mc.player != null) {
                double d = Math.sqrt(mc.player.position().distanceToSqr(Vec3.atCenterOf(pos)));
                near = d <= AUDIO_NEAR ? 1 : d >= AUDIO_FAR ? 0 : 1 - (d - AUDIO_NEAR) / (AUDIO_FAR - AUDIO_NEAR);
                near *= near;                   // falls off like a sound, not linearly
            }
            return volume / 100.0 * game * near;
        }

        /** Send the level to every tab when it moved noticeably (or a tab is new). */
        void updateAudio() {
            double lv = level();
            if (sentLevel >= 0 && Math.abs(lv - sentLevel) < 0.01 && !(lv == 0 && sentLevel != 0)) {
                return;
            }
            sentLevel = lv;
            for (WebView v : tabs) {
                try {
                    v.volume(lv);
                } catch (RuntimeException ignored) {
                    // mid-teardown
                }
            }
        }

        public boolean ready() {
            WebView v = view();
            return v != null && v.texture() != null;
        }

        /** The active tab, or null before Chromium is up. */
        public WebView view() {
            return tabs.isEmpty() ? null : tabs.get(Math.min(active, tabs.size() - 1));
        }

        public int tabCount() {
            return tabs.size();
        }

        public int activeTab() {
            return active;
        }

        /** A tab's label: its title, else its site. */
        public String tabLabel(int i) {
            WebView v = tabs.get(i);
            String t = v.title();
            if (t != null && !t.isBlank()) {
                return t;
            }
            String u = v.url();
            return u == null || u.isBlank() || u.startsWith("about:") ? "New tab" : OmoMonitorBlockEntity.host(u);
        }

        /**
         * A new tab showing {@code target} becomes the active one. At
         * {@link #MAX_TABS} the active tab goes there instead, so a link
         * never silently does nothing.
         */
        public void openTab(String target) {
            if (closed || tabs.isEmpty()) {
                return;
            }
            if (tabs.size() >= MAX_TABS || !GUARD.deny(null, live()).isEmpty()) {
                view().load(target);
                return;
            }
            WebView v;
            try {
                v = newView(target, this::requestedTab);
            } catch (RuntimeException e) {
                status = "Browser error: " + e.getMessage();
                return;
            }
            GUARD.created(null);
            tabs.add(v);
            sentLevel = -1;
            OmoLife.LOG.info("web tab opened on monitor {} ({} tabs)", pos.toShortString(), tabs.size());
            selectTab(tabs.size() - 1);
        }

        /** A page asked for a new window: only web addresses become tabs. */
        private void requestedTab(String target) {
            String safe = OmoMonitorBlockEntity.safeUrl(target);
            if (safe != null && safe.equals(target.trim())) {
                openTab(safe);
            }
        }

        public void selectTab(int i) {
            if (i < 0 || i >= tabs.size()) {
                return;
            }
            WebView old = view();
            active = i;
            WebView now = view();
            if (old != now && old != null) {
                old.focus(false);
            }
            if (width > 0 && height > 0) {
                now.resize(width, height);
            }
            applyRates();
        }

        /** Close a tab; the last one stays (the wall always shows a page). */
        public void closeTab(int i) {
            if (i < 0 || i >= tabs.size() || tabs.size() <= 1) {
                return;
            }
            retire(tabs.remove(i));
            if (active > i || active >= tabs.size()) {
                active = Math.max(0, active - 1);
            }
            selectTab(active);
        }

        public WebView tab(int i) {
            return tabs.get(i);
        }

        public String status() {
            return status;
        }

        public int width() {
            return width;
        }

        public int height() {
            return height;
        }

        public boolean throttled() {
            return throttled;
        }

        public long lastVisibleFrame() {
            return lastVisibleFrame;
        }

        void markVisible() {
            lastVisibleFrame = lastWanted = System.currentTimeMillis();
        }

        void setInteractive(boolean on) {
            interactive = on;
            updateAudio();
            if (!on) {
                width = height = 0;             // the block renderer resizes it back next frame
            }
        }

        private void setVisible(boolean visible) {
            if (tabs.isEmpty() || visible != throttled) {
                return;
            }
            throttled = !visible;
            applyRates();
        }

        /** The shown tab paints at full rate while seen; background tabs keep running slowly. */
        private void applyRates() {
            for (int i = 0; i < tabs.size(); i++) {
                try {
                    tabs.get(i).frameRate(i == active && !throttled ? FPS_VISIBLE : FPS_HIDDEN);
                } catch (RuntimeException ignored) {
                    // a page mid-teardown; the next tick decides again
                }
            }
        }

        /** Resize to the block's size; ignored while the monitor screen owns the page. */
        void resizeForBlock(int w, int h) {
            if (!interactive) {
                resize(w, h);
            }
        }

        void resize(int w, int h) {
            WebView v = view();
            if (v == null || (w == width && h == height) || w < 16 || h < 16) {
                return;
            }
            width = w;
            height = h;
            v.resize(w, h);
        }

        /** Load in the active tab; the wall's pin ({@link #url}) is unchanged. */
        void navigate(String target) {
            WebView v = view();
            if (v != null) {
                v.load(target);
            }
        }

        void reloadTabs() {
            for (WebView v : tabs) {
                try {
                    v.reload();
                } catch (RuntimeException ignored) {
                    // mid-teardown
                }
            }
        }

        public String currentUrl() {
            try {
                WebView v = view();
                String u = v == null ? null : v.url();
                return u == null || u.isBlank() ? url : u;
            } catch (RuntimeException e) {
                return url;
            }
        }

        private void close() {
            closed = true;
            tabs.forEach(WebPages::retire);
            tabs.clear();
            active = 0;
        }
    }

    private static final Map<BlockPos, Page> PAGES = new HashMap<>();
    private static String runtimeStatus = MCEF ? "" : "Web pages need MCEF Modern, which is not installed.";
    private static final BrowserGuard GUARD = new BrowserGuard(System::currentTimeMillis);
    /** Live browsers and when each was created. */
    private static final Map<WebView, Long> BORN = new IdentityHashMap<>();
    /** Browsers waiting out {@link #MIN_LIFE_MS} before they are closed. */
    private static final ClosingQueue<WebView> CLOSING = new ClosingQueue<>();
    /** What {@link BrowserGuard#deny} said to the last wall that needed a new page. */
    private static String guardRefusal = "";
    /** The watchdog's last verdict. */
    private static BrowserGuard.Level memoryLevel = BrowserGuard.Level.OK;
    /** The watchdog's last sample (null before the first). */
    private static BrowserGuard.Sample lastSample;
    /** Since when no browser has been alive or closing; 0 while one is. */
    private static long idleSince;

    private WebPages() {
    }

    public static boolean available() {
        return MCEF;
    }

    public static int count() {
        return PAGES.size();
    }

    /** Why pages are not showing, or "" when the browser runtime is fine. */
    public static String runtimeStatus() {
        return runtimeStatus;
    }

    public static Page get(BlockPos origin) {
        return PAGES.get(origin);
    }

    /**
     * Get or create the page for a monitor wall. Returns null, with a reason
     * in {@link #runtimeStatus} or {@link #refusal}, when MCEF is missing or
     * the cap is reached. Asking for the URL the page already shows (for
     * example after "Pin this") never reloads it.
     */
    public static Page open(BlockPos origin, String url) {
        Page page = PAGES.get(origin);
        if (page == null) {
            page = adopt(origin, url);
        }
        if (page != null) {
            page.lastWanted = System.currentTimeMillis();
            // Only a changed pin moves the page: browsing, tabs and typed
            // addresses never make the wall drag it back.
            if (!url.equals(page.url)) {
                page.url = url;
                if (!url.equals(page.currentUrl())) {
                    page.navigate(url);
                }
            }
            return page;
        }
        if (!MCEF) {
            return null;
        }
        guardRefusal = GUARD.deny(origin, live(),
            BrowserGuard.nearExempt(lastSample, blocksTo(origin), nearOpen()));
        if (!guardRefusal.isEmpty() || PAGES.size() >= MAX_PAGES && !evictUnseen()) {
            return null;
        }
        page = new Page(origin, url);
        PAGES.put(page.pos, page);
        if (GUARD.created(origin)) {
            OmoLife.LOG.warn("monitor {} reopened its page {} times in a minute; pausing it for {} s",
                origin.toShortString(), BrowserGuard.CHURN_OPENS, BrowserGuard.CHURN_COOLDOWN_MS / 1000);
        }
        start(page);
        OmoLife.LOG.info("web page opened for monitor {} -> {} ({} open)", origin.toShortString(), url, PAGES.size());
        return page;
    }

    /**
     * A page whose own wall no longer stands and that shows {@code url} moves
     * to the wall at {@code origin}: the same wall, re-formed.
     */
    private static Page adopt(BlockPos origin, String url) {
        ClientLevel level = Minecraft.getInstance().level;
        for (Page p : PAGES.values()) {
            if (level != null && !standing(level, p) && (url.equals(p.url) || url.equals(p.currentUrl()))) {
                PAGES.remove(p.pos);
                Textures.release(p.pos);
                OmoLife.LOG.info("web page moved from {} to {} with its wall", p.pos.toShortString(),
                    origin.toShortString());
                p.pos = origin.immutable();
                p.orphanSince = 0;
                PAGES.put(p.pos, p);
                return p;
            }
        }
        return null;
    }

    /** Its wall is there, web-bound, and still has this page's panel as its origin. */
    private static boolean standing(ClientLevel level, Page p) {
        return level.getBlockEntity(p.pos) instanceof OmoMonitorBlockEntity m && m.isWeb() && m.origin().equals(p.pos);
    }

    /**
     * At the cap, close the {@link BrowserGuard#evictable} page no wall has
     * asked for in the longest. Its wall
     * keeps the pinned URL and reopens it when it comes back into view.
     * Returns false when every page is in use, so the new wall stays dark.
     */
    private static boolean evictUnseen() {
        long now = System.currentTimeMillis();
        Page oldest = null;
        for (Page p : PAGES.values()) {
            if (BrowserGuard.evictable(now, p.lastWanted, p.createdAt, p.interactive)
                && (oldest == null || p.lastWanted < oldest.lastWanted)) {
                oldest = p;
            }
        }
        if (oldest == null) {
            return false;
        }
        OmoLife.LOG.info("page cap: closing unseen page {} -> {}", oldest.pos.toShortString(), oldest.url);
        close(oldest.pos);
        return true;
    }

    /** Why a wall without a page is dark. */
    public static String refusal() {
        if (!runtimeStatus.isEmpty()) {
            return runtimeStatus;
        }
        if (GUARD.paused()) {
            return GUARD.pauseReason();
        }
        if (!guardRefusal.isEmpty()) {
            return guardRefusal;
        }
        return PAGES.size() >= MAX_PAGES ? "Page limit reached (" + MAX_PAGES + ") — unpin a page" : "";
    }

    private static void start(Page page) {
        if (McefRuntime.ready()) {
            create(page);
            return;
        }
        page.status = "Starting Chromium...";
        McefRuntime.initialize(s -> runtimeStatus = s, () -> {
            for (Page p : PAGES.values()) {
                if (p.tabs.isEmpty()) {
                    create(p);
                }
            }
        });
    }

    private static void create(Page page) {
        try {
            page.tabs.add(newView(page.url, page::requestedTab));
            page.active = 0;
            page.sentLevel = -1;
            page.updateAudio();
            page.status = "";
            page.applyRates();
        } catch (RuntimeException e) {
            page.status = "Browser error: " + e.getMessage();
        }
    }

    public static void close(BlockPos origin) {
        Page p = PAGES.remove(origin);
        if (p != null) {
            p.close();
            Textures.release(origin);
            OmoLife.LOG.info("web page closed for monitor {} ({} open)", origin.toShortString(), PAGES.size());
        }
    }

    /** A few times a second: follow the player's distance and the game's volume sliders. */
    public static void audioTick() {
        for (Page p : PAGES.values()) {
            if (!p.tabs.isEmpty()) {
                p.updateAudio();
            }
        }
    }

    /** Reload every tab (new cookies arrived after the pages opened). */
    static void reloadAll() {
        for (Page p : PAGES.values()) {
            p.reloadTabs();
        }
    }

    /** Blocks from the player to a wall (infinite with no player). */
    private static double blocksTo(BlockPos pos) {
        var player = Minecraft.getInstance().player;
        return player == null ? Double.POSITIVE_INFINITY : Math.sqrt(player.position().distanceToSqr(Vec3.atCenterOf(pos)));
    }

    /** Open pages within {@link BrowserGuard#NEAR_BLOCKS} of the player. */
    private static int nearOpen() {
        int n = 0;
        for (Page p : PAGES.values()) {
            if (blocksTo(p.pos) <= BrowserGuard.NEAR_BLOCKS) {
                n++;
            }
        }
        return n;
    }

    /**
     * Machine memory is short: close every page except the ones in use
     * full-window and the {@link BrowserGuard#NEAR_KEEP} nearest walls (when
     * {@link BrowserGuard#nearExempt} allows). Returns how many stay.
     */
    private static int closeAllButNear(BrowserGuard.Sample s) {
        List<Page> byDistance = new ArrayList<>(PAGES.values());
        byDistance.sort((a, b) -> Double.compare(blocksTo(a.pos), blocksTo(b.pos)));
        int kept = 0;
        List<BlockPos> drop = new ArrayList<>();
        for (Page p : byDistance) {
            if (p.interactive || BrowserGuard.nearExempt(s, blocksTo(p.pos), kept)) {
                kept++;
            } else {
                drop.add(p.pos);
            }
        }
        drop.forEach(WebPages::close);
        return kept;
    }

    /** World left or disconnected: every page and texture goes. */
    public static void closeAll() {
        for (Page p : PAGES.values()) {
            p.close();
        }
        PAGES.clear();
        Textures.releaseAll();
    }

    /** Game closing: everything goes now, young browsers included (the process ends anyway). */
    public static void shutdown() {
        closeAll();
        CLOSING.drainAll(WebPages::closeNow);
    }

    // -- browser lifecycle: every WebView is born and retired here ---------------

    /** Live browsers plus those waiting to close: what {@link BrowserGuard#MAX_BROWSERS} counts. */
    private static int live() {
        return BORN.size() + CLOSING.size();
    }

    private static WebView newView(String url, WebView.Events events) {
        WebView v = McefRuntime.create(url, events);
        BORN.put(v, System.currentTimeMillis());
        idleSince = 0;
        return v;
    }

    /** Close a browser, or queue it until it is {@link #MIN_LIFE_MS} old. */
    private static void retire(WebView v) {
        if (v == null || CLOSING.contains(v)) {
            return;
        }
        Long born = BORN.remove(v);
        long due = born == null ? 0 : born + MIN_LIFE_MS;
        if (System.currentTimeMillis() >= due) {
            closeNow(v);
        } else {
            CLOSING.add(v, due);
        }
    }

    private static void closeNow(WebView v) {
        try {
            v.close();
        } catch (RuntimeException ignored) {
            // already gone
        }
    }

    /** Close the queued browsers that are old enough now. */
    private static void drainClosing(long now) {
        CLOSING.drain(now, WebPages::closeNow);
        if (live() == 0) {
            if (idleSince == 0) {
                idleSince = now;
            }
        } else {
            idleSince = 0;
        }
    }

    // -- memory guard ------------------------------------------------------------

    /** Leftover renderers were already reaped in the current idle stretch. */
    private static boolean reaped;

    /** From {@link HelperWatchdog}'s thread: act on the client thread. */
    private static void onSample(BrowserGuard.Sample s) {
        Minecraft.getInstance().execute(() -> housekeeping("memory guard", () -> applySample(s)));
    }

    private static void applySample(BrowserGuard.Sample s) {
        BrowserGuard.Level was = memoryLevel;
        memoryLevel = BrowserGuard.assess(s);
        lastSample = s;
        switch (memoryLevel) {
            case HARD -> {
                if (BrowserGuard.chromiumOver(s)) {
                    GUARD.pause(BrowserGuard.HARD_PAUSE_MS, BrowserGuard.MEMORY_PAUSE + ": Chromium was using too much memory");
                    if (was != BrowserGuard.Level.HARD) {
                        OmoLife.LOG.warn("browser memory guard: closing all {} pages ({})", PAGES.size(), s.describe());
                        tell("Web screens paused for 2 minutes: Chromium was using too much memory.");
                    }
                    closeAll();
                } else {
                    // The machine is short, not Chromium: the nearest screens stay up.
                    GUARD.pause(BrowserGuard.HARD_PAUSE_MS, BrowserGuard.MEMORY_PAUSE + ": the computer is low on memory");
                    int before = PAGES.size();
                    int kept = closeAllButNear(s);
                    if (was != BrowserGuard.Level.HARD) {
                        OmoLife.LOG.warn("browser memory guard: low memory, kept {} nearest of {} pages ({})", kept, before,
                            s.describe());
                        tell("Low memory: only the screens nearest you stay on for now.");
                    }
                }
            }
            case SOFT -> {
                GUARD.pause(BrowserGuard.SOFT_PAUSE_MS, BrowserGuard.MEMORY_PAUSE + ": memory is low");
                if (was == BrowserGuard.Level.OK) {
                    OmoLife.LOG.warn("browser memory guard: no new pages for now ({})", s.describe());
                }
            }
            case OK -> {
                if (was != BrowserGuard.Level.OK) {
                    OmoLife.LOG.info("browser memory guard: back to normal ({})", s.describe());
                }
            }
        }
        // No browser alive or closing for a while: any renderer left is a leak nothing will reap.
        if (idleSince == 0) {
            reaped = false;
        } else if (!reaped && s.renderers() > 0
            && System.currentTimeMillis() - idleSince >= BrowserGuard.ORPHAN_REAP_MS) {
            reaped = true;
            // Reaping runs on the watchdog thread; no new browser may start meanwhile.
            GUARD.pause(BrowserGuard.REAP_HOLD_MS, "Opening pages...");
            HelperWatchdog.reapOrphans(n -> OmoLife.LOG.warn(
                "browser memory guard: killed {} orphaned Chromium renderers ({})", n, s.describe()));
        }
    }

    private static void tell(String text) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.player != null) {
            mc.player.sendOverlayMessage(Component.literal(text));
        }
    }

    /**
     * Once a second: close pages whose wall has been gone or no longer
     * web-bound (rebound from anywhere, broken, unloaded) for
     * {@link #ORPHAN_MS}, and throttle pages nobody rendered recently.
     * Throttled pages keep running; only paint slows.
     */
    public static void tick() {
        housekeeping("tick", WebPages::tickOnce);
    }

    private static int housekeepingErrors;

    /**
     * Background upkeep must never take the game down (a bug here crashed a
     * live stream on 28 Oct): an exception is logged, in full the first
     * time and then every 100th, and the next round runs as usual.
     */
    private static void housekeeping(String what, Runnable work) {
        try {
            work.run();
        } catch (RuntimeException e) {
            if (housekeepingErrors++ % 100 == 0) {
                OmoLife.LOG.error("web pages {} failed ({} so far); the game carries on", what, housekeepingErrors, e);
            }
        }
    }

    private static void tickOnce() {
        long now = System.currentTimeMillis();
        drainClosing(now);
        GUARD.prune();
        if (MCEF && McefRuntime.ready()) {
            CefHooks.maybeResync();
            BrowserGuard.MemLimits limits = HelperWatchdog.start(WebPages::onSample);
            if (limits != null) {
                OmoLife.LOG.info("browser memory guard: limits {}", limits.describe());
            }
        }
        ClientLevel level = Minecraft.getInstance().level;
        if (level == null) {
            if (!PAGES.isEmpty()) {
                closeAll();
            }
            return;
        }
        List<BlockPos> stale = new ArrayList<>();
        var player = Minecraft.getInstance().player;
        for (Page p : PAGES.values()) {
            if (player != null && !p.interactive
                && player.position().distanceToSqr(Vec3.atCenterOf(p.pos)) > FAR_BLOCKS * FAR_BLOCKS) {
                stale.add(p.pos);
            } else if (standing(level, p)) {
                p.orphanSince = 0;
                p.setVisible(p.interactive || now - p.lastWanted < 1500L);
            } else if (p.orphanSince == 0) {
                p.orphanSince = now;
                p.setVisible(false);
            } else if (now - p.orphanSince >= ORPHAN_MS) {
                stale.add(p.pos);
            }
        }
        stale.forEach(WebPages::close);
    }

    /** Pixel size for a monitor of the given block dimensions and zoom percent. */
    public static int[] pixelSize(int blocksW, int blocksH, int zoomPercent) {
        double z = Math.max(25, Math.min(400, zoomPercent)) / 100.0;
        // zoom > 100 = bigger text = fewer CSS pixels for the same glass
        int w = (int) Math.round(blocksW * PIXELS_PER_BLOCK / z);
        int h = (int) Math.round(blocksH * PIXELS_PER_BLOCK / z);
        long px = (long) w * h;
        if (px > MAX_PIXELS) {
            double s = Math.sqrt((double) MAX_PIXELS / px);
            w = (int) (w * s);
            h = (int) (h * s);
        }
        return new int[]{Math.max(64, w), Math.max(64, h)};
    }

    /** A texture-manager id that samples this page's live browser texture. */
    static Identifier texture(Page page) {
        return Textures.bind(page);
    }

    /**
     * Texture-manager entries that alias a page's live GPU texture, so a
     * normal texture-backed RenderType can sample Chromium's output.
     */
    private static final class Textures {
        private static final Map<BlockPos, Identifier> ACTIVE = new HashMap<>();
        private static final Map<Identifier, Aliased> BY_ID = new HashMap<>();

        static Identifier bind(Page page) {
            Identifier id = ACTIVE.computeIfAbsent(page.pos, p ->
                OmoLife.id("web/" + p.getX() + "_" + p.getY() + "_" + p.getZ()));
            Aliased tex = BY_ID.get(id);
            if (tex == null) {
                tex = new Aliased();
                BY_ID.put(id, tex);
                Minecraft.getInstance().getTextureManager().register(id, tex);
            }
            tex.point(page.view().texture());
            return id;
        }

        static void release(BlockPos pos) {
            Identifier id = ACTIVE.remove(pos);
            if (id != null && BY_ID.remove(id) != null) {
                Minecraft.getInstance().getTextureManager().release(id);
            }
        }

        static void releaseAll() {
            for (BlockPos pos : new ArrayList<>(ACTIVE.keySet())) {
                release(pos);
            }
        }
    }

    /** An AbstractTexture whose view is borrowed from the browser; never freed by us. */
    private static final class Aliased extends AbstractTexture {
        void point(GpuTextureView view) {
            this.texture = view == null ? null : view.texture();
            this.textureView = view;
            this.sampler = RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR);
        }

        @Override
        public void close() {
            releaseTextures();
        }

        @Override
        protected void releaseTextures() {
            // Borrowed: the browser owns and frees the GPU texture; just let go.
            this.texture = null;
            this.textureView = null;
        }
    }
}
