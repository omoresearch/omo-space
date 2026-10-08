package space.omo.life.client.remote;

import java.nio.ByteBuffer;
import java.util.Optional;

import dan200.computercraft.core.input.ComputerInput;
import dan200.computercraft.core.input.UserComputerInput;

import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.client.WebPages;
import space.omo.life.client.WebView;
import space.omo.life.link.Links;
import space.omo.life.net.MonitorPackets.UseWall;
import space.omo.life.net.MonitorPackets.WallControl;
import space.omo.life.net.MonitorPackets.WallKey;
import space.omo.life.net.MonitorPackets.WallMouse;
import space.omo.life.net.MonitorPackets.WallRelease;
import space.omo.life.net.MonitorPackets.WallText;
import space.omo.life.remote.RemoteWalls;

/**
 * Using a monitor wall from where you stand, on the client.
 *
 * <p>While the player controls a wall (granted by the server, see
 * {@link RemoteWalls}) no screen opens: the world stays in view and the
 * mouse still turns the camera, so the crosshair is the pointer. Keyboard,
 * mouse buttons and the wheel are taken before the game sees them
 * (KeyboardCaptureMixin, MouseCaptureMixin), so W/A/S/D type letters instead
 * of walking. Esc lets go; so does opening any screen, walking away, or the
 * wall changing. A terminal wall's keys go through CC:T's own
 * {@link UserComputerInput}, the logic behind its computer GUI, so the
 * station gets exactly the events that GUI would queue; a web wall's input
 * goes straight into its page.
 *
 * <p>Without controlling anything, looking at a web wall hovers its page and
 * the wheel scrolls it.
 */
public final class RemoteWall {

    /** Beyond this (blocks from the wall's middle) the client lets go. */
    private static final double KEEP = RemoteWalls.RANGE + 8;
    private static final int KEY_ESCAPE = 256;
    private static final long DOUBLE_CLICK_MS = 300;
    private static final long ASK_MS = 500;

    /** What the player controls: a terminal wall or a web wall, and what it leads to. */
    public record Control(BlockPos origin, boolean web, String label, boolean hermes) {
    }

    private static Control active;
    private static UserComputerInput keys;
    /** The crosshair on the controlled wall (may be off its glass), or on a looked-at wall. */
    private static WallPointer.Hit pointer;
    private static WallPointer.Hit look;
    private static BlockPos lookWall;
    private static long lookSince;
    private static int heldButtons;
    private static float sentU = Float.NaN;
    private static float sentV = Float.NaN;
    private static long lastClickAt;
    private static int lastClickButton = -1;
    private static long askedUntil;

    private RemoteWall() {
    }

    public static void init() {
        ClientPlayNetworking.registerGlobalReceiver(WallControl.TYPE, (p, ctx) -> onControl(p));
        ClientTickEvents.END_CLIENT_TICK.register(RemoteWall::tick);
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> clear());
        HudElementRegistry.addLast(OmoLife.id("remote_wall"), RemoteWallHud::extract);
    }

    public static Control active() {
        return active;
    }

    public static WallPointer.Hit pointer() {
        return active != null ? pointer : look;
    }

    /** Milliseconds the crosshair has rested on the looked-at wall (for the hint). */
    static long lookingFor() {
        return look == null ? -1 : System.currentTimeMillis() - lookSince;
    }

    // -- control ----------------------------------------------------------------------------

    private static void onControl(WallControl p) {
        if (p.kind().isEmpty()) {
            if (active != null && active.origin().equals(p.origin())) {
                stop(false);
            }
            return;
        }
        if (active != null) {
            stop(false);                         // the server already moved this player on
        }
        boolean web = WallControl.WEB.equals(p.kind());
        if (web && WebPages.get(p.origin()) == null) {
            ClientPlayNetworking.send(new WallRelease(p.origin()));
            say(WebPages.refusal().isEmpty() ? "That page is not open yet." : WebPages.refusal());
            return;
        }
        active = new Control(p.origin(), web, p.label(), p.hermes());
        KeyMapping.releaseAll();                 // nothing keeps walking or mining underneath
        if (web) {
            view().ifPresent(v -> v.focus(true));
        } else {
            keys = new UserComputerInput(new Sender(p.origin()), false, 0, 0);
        }
        heldButtons = 0;
        sentU = sentV = Float.NaN;
    }

    /** Let go; {@code tellServer} unless the server ended it. */
    private static void stop(boolean tellServer) {
        Control c = active;
        if (c == null) {
            return;
        }
        if (c.web()) {
            view().ifPresent(v -> {
                for (int b = 0; b < 3; b++) {
                    if ((heldButtons & (1 << b)) != 0) {
                        v.mouseReleased(pageEvent(new MouseButtonInfo(b, 0)));
                    }
                }
                v.focus(false);
            });
        } else {
            keys.releaseInputs();                // key_up for every key still held, as CC's GUI does
            for (int b = 0; b < 3; b++) {
                if ((heldButtons & (1 << b)) != 0) {
                    mouse(WallMouse.UP, b + 1);
                }
            }
            keys = null;
        }
        if (tellServer) {
            ClientPlayNetworking.send(new WallRelease(c.origin()));
        }
        active = null;
        pointer = null;
        heldButtons = 0;
    }

    private static void clear() {
        active = null;
        keys = null;
        pointer = look = null;
        heldButtons = 0;
    }

    // -- input, from the capture mixins (true = the game never sees it) ----------------------

    public static boolean key(KeyEvent event, int action) {
        Minecraft mc = Minecraft.getInstance();
        if (active == null || mc.gui.screen() != null) {
            return false;
        }
        if (mc.options.keyScreenshot.matches(event) || mc.options.keyFullscreen.matches(event)) {
            return false;
        }
        if (event.key() == KEY_ESCAPE) {
            if (action == 1) {
                stop(true);
            }
            return true;
        }
        boolean down = action != 0;
        if (active.web()) {
            view().ifPresent(v -> {
                if (down) {
                    v.keyPressed(event);
                } else {
                    v.keyReleased(event);
                }
            });
        } else if (down && action == 1 && event.isPaste()) {
            keys.paste(mc.keyboardHandler.getClipboard());
        } else if (down) {
            keys.keyDown(event.key());
        } else {
            keys.keyUp(event.key());
        }
        return true;
    }

    public static boolean chars(CharacterEvent event) {
        if (active == null || Minecraft.getInstance().gui.screen() != null) {
            return false;
        }
        if (active.web()) {
            view().ifPresent(v -> v.charTyped(event));
        } else {
            keys.codepointTyped(event.codepoint());
        }
        return true;
    }

    public static boolean button(MouseButtonInfo info, int action) {
        if (active == null || Minecraft.getInstance().gui.screen() != null) {
            return false;
        }
        int b = info.button();
        if (b < 0 || b > 2) {
            return true;
        }
        boolean press = action == 1;
        if (press && (pointer == null || !pointer.onGlass())) {
            return true;                         // a click beside the glass does nothing
        }
        if (!press && (heldButtons & (1 << b)) == 0) {
            return true;
        }
        heldButtons = press ? heldButtons | (1 << b) : heldButtons & ~(1 << b);
        if (active.web()) {
            view().ifPresent(v -> {
                MouseButtonEvent e = pageEvent(info);
                if (press) {
                    long now = System.currentTimeMillis();
                    boolean twice = b == lastClickButton && now - lastClickAt < DOUBLE_CLICK_MS;
                    lastClickAt = now;
                    lastClickButton = b;
                    v.mouseMoved((int) e.x(), (int) e.y());
                    v.mouseClicked(e, twice);
                } else {
                    v.mouseReleased(e);
                }
            });
        } else {
            mouse(press ? WallMouse.CLICK : WallMouse.UP, b + 1);
        }
        return true;
    }

    public static boolean scroll(double dy) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.gui.screen() != null || dy == 0) {
            return false;
        }
        if (active == null) {
            // Looking at a web wall: the wheel reads the page instead of changing the hotbar slot.
            if (look == null || !look.wall().isWeb()) {
                return false;
            }
            WebPages.Page page = WebPages.get(look.wall().getBlockPos());
            if (page == null || page.view() == null) {
                return false;
            }
            page.view().mouseScrolled(px(look, page), py(look, page), dy);
            return true;
        }
        if (pointer != null && pointer.onGlass()) {
            if (active.web()) {
                WebPages.Page page = WebPages.get(active.origin());
                if (page != null && page.view() != null) {
                    page.view().mouseScrolled(px(pointer, page), py(pointer, page), dy);
                }
            } else {
                mouse(WallMouse.SCROLL, dy > 0 ? -1 : 1);
            }
        }
        return true;
    }

    /**
     * The use key (right-click). Beyond vanilla reach, a wall under the
     * crosshair is asked for; up close vanilla calls the block as usual.
     */
    public static boolean use() {
        Minecraft mc = Minecraft.getInstance();
        if (active != null) {
            return true;
        }
        if (mc.player == null || mc.hitResult != null && mc.hitResult.getType() != HitResult.Type.MISS
            || mc.player.getMainHandItem().is(Links.LINKER)) {       // the Linker works at arm's length
            return false;
        }
        WallPointer.Hit h = WallPointer.aim(mc, RemoteWalls.RANGE);
        if (h == null) {
            return false;
        }
        long now = System.currentTimeMillis();
        if (now >= askedUntil) {                 // a held button repeats every few ticks: ask once
            askedUntil = now + ASK_MS;
            ClientPlayNetworking.send(new UseWall(h.panel(), mc.player.isShiftKeyDown()));
            mc.player.swing(InteractionHand.MAIN_HAND);
        }
        return true;
    }

    // -- per tick: pointer, drags, hover, letting go ----------------------------------------

    private static void tick(Minecraft mc) {
        if (mc.level == null || mc.player == null) {
            if (active != null || look != null) {
                clear();
            }
            return;
        }
        Vec3 eye = mc.player.getEyePosition(1.0F);
        if (active == null) {
            track(mc, mc.gui.screen() == null ? WallPointer.aim(mc, RemoteWalls.RANGE) : null);
            return;
        }
        OmoMonitorBlockEntity wall = mc.level.getBlockEntity(active.origin()) instanceof OmoMonitorBlockEntity m
            && m.origin().equals(active.origin()) && m.isWeb() == active.web() ? m : null;
        if (mc.gui.screen() != null || wall == null
            || eye.distanceToSqr(wall.getRenderBoundingBox().getCenter()) > KEEP * KEEP) {
            stop(true);
            return;
        }
        pointer = WallPointer.project(wall, eye, mc.player.getViewVector(1.0F));
        if (pointer == null || (pointer.u() == sentU && pointer.v() == sentV)) {
            return;
        }
        sentU = pointer.u();
        sentV = pointer.v();
        if (active.web()) {
            WebPages.Page page = WebPages.get(active.origin());
            if (page != null && page.view() != null && pointer.onGlass()) {
                page.view().mouseMoved(px(pointer, page), py(pointer, page));
            }
        } else if (heldButtons != 0) {
            mouse(WallMouse.DRAG, Integer.numberOfTrailingZeros(heldButtons) + 1);
        }
    }

    /** Not controlling: remember the looked-at wall (hint, scroll) and hover web pages. */
    private static void track(Minecraft mc, WallPointer.Hit h) {
        BlockPos wall = h == null ? null : h.wall().getBlockPos();
        if (wall == null || !wall.equals(lookWall)) {
            lookSince = System.currentTimeMillis();
        }
        lookWall = wall;
        look = h;
        if (h != null && h.wall().isWeb() && (h.u() != sentU || h.v() != sentV)) {
            sentU = h.u();
            sentV = h.v();
            WebPages.Page page = WebPages.get(wall);
            if (page != null && page.view() != null) {
                page.view().mouseMoved(px(h, page), py(h, page));
            }
        }
    }

    // -- helpers ----------------------------------------------------------------------------

    private static void mouse(int kind, int button) {
        if (pointer != null) {
            ClientPlayNetworking.send(new WallMouse(active.origin(), kind, button,
                clamp(pointer.u()), clamp(pointer.v())));
        }
    }

    private static float clamp(float f) {
        return Math.max(0F, Math.min(1F, f));
    }

    private static Optional<WebView> view() {
        WebPages.Page page = active == null ? null : WebPages.get(active.origin());
        return Optional.ofNullable(page == null ? null : page.view());
    }

    private static int px(WallPointer.Hit h, WebPages.Page page) {
        return (int) (clamp(h.u()) * page.width());
    }

    private static int py(WallPointer.Hit h, WebPages.Page page) {
        return (int) (clamp(h.v()) * page.height());
    }

    /** A mouse event at the pointer, in page pixels (MCEF reads the position from the event). */
    private static MouseButtonEvent pageEvent(MouseButtonInfo info) {
        WebPages.Page page = WebPages.get(active.origin());
        WallPointer.Hit h = pointer;
        return page == null || h == null ? new MouseButtonEvent(0, 0, info)
            : new MouseButtonEvent(px(h, page), py(h, page), info);
    }

    private static void say(String text) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.player != null) {
            mc.player.sendOverlayMessage(Component.literal(text));
        }
    }

    /**
     * CC:T's keyboard logic ({@link UserComputerInput}) talks to this: every
     * event it would queue on a computer goes to the server for the station
     * behind the wall. Pointer events are separate ({@code omo_mouse}), so
     * the mouse half of the interface stays unused.
     */
    private record Sender(BlockPos origin) implements ComputerInput {
        @Override
        public void keyDown(int key, boolean repeat) {
            ClientPlayNetworking.send(new WallKey(origin, key, true, repeat));
        }

        @Override
        public void keyUp(int key) {
            ClientPlayNetworking.send(new WallKey(origin, key, false, false));
        }

        @Override
        public void charTyped(byte ch) {
            ClientPlayNetworking.send(new WallText(origin, false, new byte[]{ch}));
        }

        @Override
        public void paste(ByteBuffer text) {
            byte[] bytes = new byte[text.remaining()];
            text.get(bytes);
            ClientPlayNetworking.send(new WallText(origin, true, bytes));
        }

        @Override
        public void mouseClick(int button, int x, int y) {
        }

        @Override
        public void mouseUp(int button, int x, int y) {
        }

        @Override
        public void mouseDrag(int button, int x, int y) {
        }

        @Override
        public void mouseScroll(int direction, int x, int y) {
        }
    }
}
