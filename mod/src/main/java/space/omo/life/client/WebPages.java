package space.omo.life.client;

import java.util.ArrayList;
import java.util.HashMap;
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
import net.minecraft.resources.Identifier;
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
 */
public final class WebPages {

    public static final int MAX_PAGES = 6;
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
        WebView view;
        String url;
        String status = "Starting browser...";
        int width;
        int height;
        long lastVisibleFrame;
        /** When its wall stopped standing, 0 while it stands. */
        long orphanSince;
        boolean throttled;
        /** True while the monitor screen shows this page full-window (it owns the size then). */
        boolean interactive;

        Page(BlockPos pos, String url) {
            this.pos = pos.immutable();
            this.url = url;
        }

        public boolean ready() {
            return view != null && view.texture() != null;
        }

        public WebView view() {
            return view;
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
            lastVisibleFrame = System.currentTimeMillis();
        }

        void setInteractive(boolean on) {
            interactive = on;
            if (!on) {
                width = height = 0;             // the block renderer resizes it back next frame
            }
        }

        private void setVisible(boolean visible) {
            if (view == null || visible != throttled) {
                return;
            }
            throttled = !visible;
            try {
                view.frameRate(visible ? FPS_VISIBLE : FPS_HIDDEN);
            } catch (RuntimeException ignored) {
                // a page mid-teardown; the next tick decides again
            }
        }

        /** Resize to the block's size; ignored while the monitor screen owns the page. */
        void resizeForBlock(int w, int h) {
            if (!interactive) {
                resize(w, h);
            }
        }

        void resize(int w, int h) {
            if (view == null || (w == width && h == height) || w < 16 || h < 16) {
                return;
            }
            width = w;
            height = h;
            view.resize(w, h);
        }

        void navigate(String target) {
            url = target;
            if (view != null) {
                view.load(target);
            }
        }

        public String currentUrl() {
            try {
                String u = view == null ? null : view.url();
                return u == null || u.isBlank() ? url : u;
            } catch (RuntimeException e) {
                return url;
            }
        }

        private void close() {
            if (view != null) {
                try {
                    view.close();
                } catch (RuntimeException ignored) {
                    // already gone
                }
                view = null;
            }
        }
    }

    private static final Map<BlockPos, Page> PAGES = new HashMap<>();
    private static String runtimeStatus = MCEF ? "" : "Web pages need MCEF Modern, which is not installed.";

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
            if (!url.equals(page.url)) {
                if (url.equals(page.currentUrl())) {
                    page.url = url;
                } else {
                    page.navigate(url);
                }
            }
            return page;
        }
        if (!MCEF || PAGES.size() >= MAX_PAGES) {
            return null;
        }
        page = new Page(origin, url);
        PAGES.put(page.pos, page);
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

    /** Why a wall without a page is dark. */
    public static String refusal() {
        if (!runtimeStatus.isEmpty()) {
            return runtimeStatus;
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
                if (p.view == null) {
                    create(p);
                }
            }
        });
    }

    private static void create(Page page) {
        try {
            page.view = McefRuntime.create(page.url);
            page.status = "";
            page.view.frameRate(FPS_VISIBLE);
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

    /** World left, disconnected or game closing: every page and texture goes. */
    public static void closeAll() {
        for (Page p : PAGES.values()) {
            p.close();
        }
        PAGES.clear();
        Textures.releaseAll();
    }

    /**
     * Once a second: close pages whose wall has been gone or no longer
     * web-bound (rebound from anywhere, broken, unloaded) for
     * {@link #ORPHAN_MS}, and throttle pages nobody rendered recently.
     * Throttled pages keep running; only paint slows.
     */
    public static void tick() {
        ClientLevel level = Minecraft.getInstance().level;
        if (level == null) {
            if (!PAGES.isEmpty()) {
                closeAll();
            }
            return;
        }
        List<BlockPos> stale = new ArrayList<>();
        long now = System.currentTimeMillis();
        var player = Minecraft.getInstance().player;
        for (Page p : PAGES.values()) {
            if (player != null && !p.interactive
                && player.position().distanceToSqr(Vec3.atCenterOf(p.pos)) > FAR_BLOCKS * FAR_BLOCKS) {
                stale.add(p.pos);
            } else if (standing(level, p)) {
                p.orphanSince = 0;
                p.setVisible(p.interactive || now - p.lastVisibleFrame < 1500L);
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
            tex.point(page.view.texture());
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
