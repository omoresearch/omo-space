package space.omo.life.client;

import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.SectionPos;
import net.minecraft.core.particles.DustParticleOptions;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.phys.Vec3;

import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.StationLinked;
import space.omo.life.link.LinkerItem;
import space.omo.life.link.Links;
import space.omo.life.net.LinkPackets;

/**
 * The Linker in hand, on the client: faint gold dust drifts from every
 * linked screen nearby to its station, and the chosen station glows at its
 * corners, so a builder sees the wiring they cannot otherwise see. Also
 * opens the Station Setup when the server sends it.
 */
public final class LinkerClient {

    private static final int EVERY = 4;           // ticks between puffs
    private static final int CHUNKS = 3;          // how far to look for links, in chunks
    private static int ticks;

    private LinkerClient() {
    }

    public static void init() {
        ClientPlayNetworking.registerGlobalReceiver(LinkPackets.OpenSetup.TYPE, (p, ctx) ->
            ctx.client().setScreenAndShow(new StationSetupScreen(p)));
        ClientTickEvents.END_CLIENT_TICK.register(LinkerClient::tick);
    }

    private static void tick(Minecraft mc) {
        ClientLevel level = mc.level;
        if (level == null || mc.player == null || mc.isPaused() || ++ticks % EVERY != 0) {
            return;
        }
        ItemStack held = mc.player.getMainHandItem();
        if (!held.is(Links.LINKER)) {
            return;
        }
        LinkerItem.Selection s = LinkerItem.selection(held);
        if (s != null && s.dimension().equals(level.dimension())) {
            corners(level, s.pos());
        }
        int cx = SectionPos.blockToSectionCoord(mc.player.getBlockX());
        int cz = SectionPos.blockToSectionCoord(mc.player.getBlockZ());
        for (int x = cx - CHUNKS; x <= cx + CHUNKS; x++) {
            for (int z = cz - CHUNKS; z <= cz + CHUNKS; z++) {
                if (!level.hasChunk(x, z)) {
                    continue;
                }
                for (BlockEntity be : level.getChunk(x, z).getBlockEntities().values()) {
                    if (be instanceof StationLinked l && l.linkedStation() != null
                        && !(be instanceof OmoMonitorBlockEntity m && !m.isOrigin())) {      // one line per wall
                        Vec3 from = be instanceof OmoMonitorBlockEntity m ? m.getRenderBoundingBox().getCenter()
                            : Vec3.atCenterOf(be.getBlockPos());
                        line(level, from, Vec3.atCenterOf(l.linkedStation()));
                    }
                }
            }
        }
    }

    /** A dotted line that drifts from the screen toward its station. */
    private static void line(ClientLevel level, Vec3 from, Vec3 to) {
        DustParticleOptions dust = new DustParticleOptions(Links.GOLD, 0.45F);
        Vec3 d = to.subtract(from);
        double len = d.length();
        double phase = (ticks / EVERY % 4) / 4.0;
        for (double t = phase; t < len; t += 1.0) {
            Vec3 p = from.add(d.scale(t / len));
            level.addParticle(dust, p.x, p.y, p.z, 0, 0, 0);
        }
    }

    private static void corners(ClientLevel level, BlockPos pos) {
        DustParticleOptions dust = new DustParticleOptions(Links.GOLD, 0.7F);
        for (int e = 0; e < 8; e++) {
            level.addParticle(dust, pos.getX() + (e & 1) * 1.02 - 0.01, pos.getY() + (e >> 2 & 1) * 1.02 - 0.01,
                pos.getZ() + (e >> 1 & 1) * 1.02 - 0.01, 0, 0, 0);
        }
    }
}
