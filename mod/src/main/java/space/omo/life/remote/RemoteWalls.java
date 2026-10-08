package space.omo.life.remote;

import java.nio.ByteBuffer;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import dan200.computercraft.core.input.EventComputerInput;
import dan200.computercraft.core.terminal.Terminal;
import dan200.computercraft.core.util.StringUtil;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.peripheral.monitor.ServerMonitor;

import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.level.Level;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlock;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.builder.OmoAdvancements;
import space.omo.life.link.LinkMirror;
import space.omo.life.mixin.MonitorComputersAccessor;
import space.omo.life.net.MonitorPackets.WallControl;
import space.omo.life.net.MonitorPackets.WallMouse;
import space.omo.life.net.OmoNetworking;
import space.omo.life.station.Stations;

/**
 * Server side of controlling a monitor wall from where you stand.
 *
 * <p>Right-click a wall (up close, or from up to {@link #RANGE} blocks) and
 * you control it until you let go: on a terminal wall your keys and the
 * crosshair go to the Omo station the wall is attached to, as CC events
 * (contract A: {@code omo_focus}, CC's own {@code key}/{@code key_up}/
 * {@code char}/{@code paste}, {@code omo_mouse}, {@code omo_release}); on a
 * web wall the client drives its own page and the server only grants it.
 * The station is found through CC:T's peripheral attachments on the wall,
 * the same computers that receive {@code monitor_touch}, or, for a wall the
 * Omo Linker linked, at its link. A linked wall mirrors the computer's own
 * screen ({@link LinkMirror}), so it is typed into as that screen: keys go
 * to the computer surface ({@code omo_focus "computer"}) and the pointer
 * becomes CC's own {@code mouse_click}/{@code mouse_drag}/{@code mouse_up}/
 * {@code mouse_scroll} at the station cell under it. Shift+right-click
 * opens the wall's settings instead.
 *
 * <p>Every input names its wall and is dropped unless that player controls
 * that wall, is still near it, and the wall still leads to a station.
 */
public final class RemoteWalls {

    /** How far away a wall can be taken over, in blocks. */
    public static final double RANGE = 24;
    /** Kept a little longer while controlling, so stepping back does not drop it. */
    private static final double KEEP = RANGE + 8;
    private static final String[] MOUSE_KINDS = {"click", "drag", "up", "scroll"};
    private static final String[] CC_MOUSE = {"mouse_click", "mouse_drag", "mouse_up", "mouse_scroll"};

    private static final class Session {
        final ResourceKey<Level> dimension;
        final BlockPos origin;
        final boolean web;
        int keys;
        int chars;
        int mouse;
        int lastX = -1;
        int lastY = -1;

        Session(ResourceKey<Level> dimension, BlockPos origin, boolean web) {
            this.dimension = dimension;
            this.origin = origin.immutable();
            this.web = web;
        }
    }

    /** The station computer behind a terminal wall, and whether the wall is linked (a mirror) or adjacent. */
    private record Station(EventComputerInput.QueueEvent computer, StationBlockEntity block, boolean linked) {
    }

    private static final Map<UUID, Session> SESSIONS = new HashMap<>();

    private RemoteWalls() {
    }

    /**
     * A right-click on a wall's front face (vanilla reach). False means "not
     * ours": a terminal wall no station drives keeps CC:T's own touch.
     */
    public static boolean use(ServerPlayer player, OmoMonitorBlockEntity panel, boolean settings) {
        OmoMonitorBlockEntity wall = wallOf(panel);
        Station station = wall.isWeb() ? null : station(wall);
        if (!settings && !wall.isWeb() && station == null) {
            return false;
        }
        if (!Stations.allowed(player)) {
            Stations.refuse(player);
            return true;
        }
        if (settings) {
            OmoNetworking.openMonitorScreen(player, wall);
            return true;
        }
        Session current = SESSIONS.get(player.getUUID());
        if (current != null && current.origin.equals(wall.getBlockPos())) {
            return true;                          // a held right-click repeats; the wall is already theirs
        }
        end(player, true);
        Session s = new Session(player.level().dimension(), wall.getBlockPos(), wall.isWeb());
        SESSIONS.put(player.getUUID(), s);
        if (s.web) {
            ServerPlayNetworking.send(player, new WallControl(s.origin, WallControl.WEB,
                OmoMonitorBlockEntity.host(wall.url()), false));
        } else {
            // CONTRACT A: an adjacent wall is its own surface; a linked wall is the computer's screen.
            station.computer().queueEvent("omo_focus", new Object[]{station.linked() ? "computer" : "monitor"});
            ServerPlayNetworking.send(player, new WallControl(s.origin, WallControl.TERMINAL,
                station.block().workspaceName(), station.block().isHermes()));
        }
        OmoLife.LOG.info("{} controls monitor wall {} ({})", player.getPlainTextName(), s.origin.toShortString(),
            s.web ? wall.url() : (station.linked() ? "linked " : "") + "station computer "
                + station.block().getComputerID());
        return true;
    }

    /** A right-click on a wall beyond vanilla reach: the same, once the wall is really in sight. */
    public static void useFromAfar(ServerPlayer player, BlockPos pos, boolean settings) {
        Level level = player.level();
        if (!level.isLoaded(pos) || !(level.getBlockEntity(pos) instanceof OmoMonitorBlockEntity panel)
            || !facing(player, panel) || !inSight(player, panel)) {
            return;
        }
        if (!use(player, panel, settings)) {
            player.sendSystemMessage(Component.literal("This monitor is not attached to an Omo station."), true);
        } else if (!settings && Stations.allowed(player)) {
            OmoAdvancements.award(player, OmoAdvancements.CONTROL_AFAR);
        }
    }

    public static void key(ServerPlayer player, BlockPos origin, int key, boolean down, boolean repeat) {
        Station st = target(player, origin);
        if (st != null && key >= 0 && key <= 0xFFFF) {
            EventComputerInput in = new EventComputerInput(st.computer()::queueEvent);
            if (down) {
                in.keyDown(key, repeat);
            } else {
                in.keyUp(key);
            }
            SESSIONS.get(player.getUUID()).keys++;
        }
    }

    /** One CC-charset character, or a paste, exactly as CC's own terminal GUI queues them. */
    public static void text(ServerPlayer player, BlockPos origin, boolean paste, byte[] text) {
        Station st = target(player, origin);
        if (st == null || text.length == 0) {
            return;
        }
        EventComputerInput in = new EventComputerInput(st.computer()::queueEvent);
        if (paste) {
            in.paste(ByteBuffer.wrap(text, 0, Math.min(text.length, StringUtil.MAX_PASTE_LENGTH)));
        } else {
            in.charTyped(text[0]);
        }
        SESSIONS.get(player.getUUID()).chars++;
    }

    /**
     * The crosshair on the wall: {@code omo_mouse kind button x y} in the
     * wall's own cells, or on a linked wall CC's mouse event at the station
     * cell under it.
     */
    public static void mouse(ServerPlayer player, BlockPos origin, int kind, int button, float u, float v) {
        Station st = target(player, origin);
        if (st == null || kind < 0 || kind >= MOUSE_KINDS.length || !Float.isFinite(u) || !Float.isFinite(v)) {
            return;
        }
        OmoMonitorBlockEntity wall = (OmoMonitorBlockEntity) player.level().getBlockEntity(origin);
        ServerMonitor monitor = wall.getCachedServerMonitor();
        Terminal term = monitor == null ? null : monitor.getTerminal();
        LinkMirror.Layout layout = st.linked() ? LinkMirror.layout(wall) : null;
        if (term == null || st.linked() && layout == null) {
            return;
        }
        int x = cell(u, term.getWidth());
        int y = cell(v, term.getHeight());
        if (layout != null) {
            int[] at = layout.toStation(x, y);
            x = at[0];
            y = at[1];
        }
        Session s = SESSIONS.get(player.getUUID());
        if (kind == WallMouse.DRAG && x == s.lastX && y == s.lastY) {
            return;                                   // CC drags are per cell, like mouse_drag
        }
        s.lastX = x;
        s.lastY = y;
        s.mouse++;
        if (layout != null) {
            st.computer().queueEvent(CC_MOUSE[kind], new Object[]{button, x, y});
        } else {
            st.computer().queueEvent("omo_mouse", new Object[]{MOUSE_KINDS[kind], button, x, y});
        }
    }

    public static void release(ServerPlayer player, BlockPos origin) {
        Session s = SESSIONS.get(player.getUUID());
        if (s != null && s.origin.equals(origin)) {
            end(player, false);
        }
    }

    /** The player left the game. */
    public static void forget(ServerPlayer player) {
        end(player, false);
    }

    // -- internals ----------------------------------------------------------------------

    private static int cell(float f, int cells) {
        return Math.max(1, Math.min(cells, (int) Math.floor(Math.max(0F, Math.min(1F, f)) * cells) + 1));
    }

    /** The station behind the player's session on {@code origin}, or null (and the session ends). */
    private static Station target(ServerPlayer player, BlockPos origin) {
        Session s = SESSIONS.get(player.getUUID());
        if (s == null || s.web || !s.origin.equals(origin)) {
            return null;
        }
        Level level = player.level();
        Station st = level.dimension() == s.dimension && level.isLoaded(origin)
            && player.getEyePosition().distanceToSqr(Vec3.atCenterOf(origin)) < KEEP * KEEP
            && level.getBlockEntity(origin) instanceof OmoMonitorBlockEntity wall
            && wall.origin().equals(origin) && !wall.isWeb() ? station(wall) : null;
        if (st == null) {
            end(player, true);
        }
        return st;
    }

    /** End a session: the station hears {@code omo_release}; the client is told if it did not ask. */
    private static void end(ServerPlayer player, boolean tellClient) {
        Session s = SESSIONS.remove(player.getUUID());
        if (s == null) {
            return;
        }
        if (!s.web && player.level().getBlockEntity(s.origin) instanceof OmoMonitorBlockEntity wall) {
            Station st = station(wall);
            if (st != null) {
                st.computer().queueEvent("omo_release", new Object[0]);
            }
        }
        if (tellClient) {
            ServerPlayNetworking.send(player, WallControl.released(s.origin));
        }
        OmoLife.LOG.info("{} let go of monitor wall {} (keys {}, chars {}, mouse {})", player.getPlainTextName(),
            s.origin.toShortString(), s.keys, s.chars, s.mouse);
    }

    private static OmoMonitorBlockEntity wallOf(OmoMonitorBlockEntity panel) {
        return panel.getLevel() != null && panel.getLevel().getBlockEntity(panel.origin()) instanceof OmoMonitorBlockEntity o
            ? o : panel;
    }

    /**
     * The Omo station a terminal wall shows: the linked one while it runs, or
     * one of the computers CC:T attached to a panel whose neighbour is a
     * station with that id.
     */
    private static Station station(OmoMonitorBlockEntity wall) {
        Level level = wall.getLevel();
        if (level == null) {
            return null;
        }
        if (wall.isLinked()) {
            StationBlockEntity linked = wall.station();
            ServerComputer computer = linked == null ? null : linked.getServerComputer();
            return computer == null || !computer.isOn() ? null : new Station(computer::queueEvent, linked, true);
        }
        BlockPos base = wall.origin();
        for (int x = 0; x < wall.getWidth(); x++) {
            for (int y = 0; y < wall.getHeight(); y++) {
                BlockPos p = base.relative(wall.getRight(), x).relative(wall.getDown(), y);
                if (!(level.getBlockEntity(p) instanceof OmoMonitorBlockEntity panel)) {
                    continue;
                }
                Station[] found = new Station[1];
                ((MonitorComputersAccessor) panel).omolife$computers().forEach(c -> {
                    for (Direction d : Direction.values()) {
                        if (found[0] == null && level.getBlockEntity(p.relative(d)) instanceof StationBlockEntity st
                            && st.getComputerID() == c.getID()) {
                            found[0] = new Station(c::queueEvent, st, false);
                        }
                    }
                });
                if (found[0] != null) {
                    return found[0];
                }
            }
        }
        return null;
    }

    /** The player is in front of the wall's glass, not behind or beside it. */
    private static boolean facing(ServerPlayer player, OmoMonitorBlockEntity panel) {
        Vec3 toEye = player.getEyePosition().subtract(Vec3.atCenterOf(panel.getBlockPos()));
        Direction f = panel.getFront();
        return toEye.x * f.getStepX() + toEye.y * f.getStepY() + toEye.z * f.getStepZ()
            > 0.5 - OmoMonitorBlock.glassInset(panel.getBlockState());
    }

    /** Within range, and nothing but the wall itself between the player's eye and the panel. */
    private static boolean inSight(ServerPlayer player, OmoMonitorBlockEntity panel) {
        Vec3 eye = player.getEyePosition();
        Vec3 face = Vec3.atCenterOf(panel.getBlockPos())
            .relative(panel.getFront(), 0.5 - OmoMonitorBlock.glassInset(panel.getBlockState()));
        if (eye.distanceToSqr(face) > (RANGE + 1) * (RANGE + 1)) {
            return false;
        }
        BlockHitResult hit = player.level().clip(new ClipContext(eye, face.relative(panel.getFront(), -0.25),
            ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, player));
        return hit.getType() == HitResult.Type.BLOCK
            && player.level().getBlockEntity(hit.getBlockPos()) instanceof OmoMonitorBlockEntity hitPanel
            && hitPanel.origin().equals(panel.origin());
    }
}
