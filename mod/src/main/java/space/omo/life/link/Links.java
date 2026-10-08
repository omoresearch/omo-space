package space.omo.life.link;

import dan200.computercraft.shared.computer.core.ServerComputer;

import net.fabricmc.fabric.api.creativetab.v1.CreativeModeTabEvents;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.ChatFormatting;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Registry;
import net.minecraft.core.particles.DustParticleOptions;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.block.StationLinked;
import space.omo.life.builder.OmoAdvancements;
import space.omo.life.net.LinkPackets;
import space.omo.life.station.Stations;

/**
 * Linking screens to stations, server side, and the clicks that do it.
 *
 * <p>With the Linker in hand (see {@link LinkerItem}): right-click a station
 * to choose it; right-click a {@link StationLinked} (a whole monitor wall, a name
 * plate, a lamp) to link it to the chosen station, same dimension, within
 * {@link Linkable#RANGE} blocks; Shift+right-click a linked one to unlink
 * it; Shift+right-click a station for its Station Setup. With an empty hand,
 * Shift+right-click on a station opens the setup too. Only players who may
 * use stations ({@link Stations#allowed}) link anything.
 *
 * <p>Everything answers on the action bar, with a chime and a trail of gold
 * dust between the screen and its station.
 */
public final class Links {

    /** Omo gold, for the dust between a screen and its station. */
    public static final int GOLD = 0xDDA744;

    public static Item LINKER;

    private Links() {
    }

    public static void register() {
        ResourceKey<Item> key = ResourceKey.create(Registries.ITEM, OmoLife.id("linker"));
        LINKER = Registry.register(BuiltInRegistries.ITEM, key,
            new LinkerItem(new Item.Properties().setId(key).stacksTo(1)));
        CreativeModeTabEvents.modifyOutputEvent(OmoLife.TAB).register(out -> out.accept(LINKER));
        UseBlockCallback.EVENT.register(Links::useBlock);
        LinkMirror.register();
        LinkPackets.register();
    }

    /**
     * Right-clicks on blocks, before the block hears of them (both sides:
     * the client answers SUCCESS so the click reaches the server and the arm
     * swings; the server does the work).
     */
    private static InteractionResult useBlock(Player player, Level level, InteractionHand hand, BlockHitResult hit) {
        if (hand != InteractionHand.MAIN_HAND || player.isSpectator()) {
            return InteractionResult.PASS;
        }
        ItemStack held = player.getMainHandItem();
        BlockEntity be = level.getBlockEntity(hit.getBlockPos());
        boolean shift = player.isSecondaryUseActive();
        if (!held.is(LINKER)) {
            if (!(held.isEmpty() && shift && be instanceof StationBlockEntity station)) {
                return InteractionResult.PASS;
            }
            if (player instanceof ServerPlayer sp) {
                StationSetup.open(sp, station);
            }
            return InteractionResult.SUCCESS;
        }
        if (!(be instanceof StationBlockEntity) && !(be instanceof StationLinked)) {
            return InteractionResult.PASS;
        }
        if (player instanceof ServerPlayer sp) {
            if (!Stations.allowed(sp)) {
                Stations.refuse(sp);
            } else if (be instanceof StationBlockEntity station) {
                if (shift) {
                    StationSetup.open(sp, station);
                } else {
                    choose(sp, held, station);
                }
            } else if (shift) {
                unlink(sp, (StationLinked) be);
            } else {
                link(sp, held, (StationLinked) be);
            }
        }
        return InteractionResult.SUCCESS;
    }

    private static void choose(ServerPlayer player, ItemStack linker, StationBlockEntity station) {
        station.ensureWorkspace();
        LinkerItem.select(linker, new LinkerItem.Selection(station.getBlockPos(), player.level().dimension(),
            station.displayName()));
        say(player, "Linking to " + label(station.displayName(), station.getBlockPos())
            + " · right-click screens to link them", true);
        chime(player, station.getBlockPos(), 1.4F);
        outline(player.level(), station.getBlockPos());
    }

    private static void link(ServerPlayer player, ItemStack linker, StationLinked target) {
        LinkerItem.Selection s = LinkerItem.selection(linker);
        ServerLevel level = player.level();
        BlockPos at = ((BlockEntity) target).getBlockPos();
        if (s == null) {
            say(player, "Right-click a station first: that is what screens link to.", false);
            return;
        }
        if (!s.dimension().equals(level.dimension())) {
            say(player, label(s.name(), s.pos()) + " is in another dimension.", false);
            return;
        }
        if (!at.closerThan(s.pos(), Linkable.RANGE)) {
            say(player, "Too far: screens link within " + Linkable.RANGE + " blocks of their station (this is "
                + (int) Math.sqrt(at.distSqr(s.pos())) + ").", false);
            return;
        }
        if (!level.isLoaded(s.pos()) || !(level.getBlockEntity(s.pos()) instanceof StationBlockEntity station)) {
            LinkerItem.select(linker, null);
            say(player, "The chosen station is gone; choose another.", false);
            return;
        }
        target.setLinkedStation(s.pos());
        ServerComputer computer = station.createServerComputer();
        if (!computer.isOn()) {
            computer.turnOn();                              // a linked screen shows a running station
        }
        say(player, "Linked to " + label(station.displayName(), s.pos()) + " · "
            + (int) Math.sqrt(at.distSqr(s.pos())) + " blocks", true);
        chime(player, at, 1.8F);
        OmoAdvancements.award(player, OmoAdvancements.LINK_SCREEN);
        trail(level, at, s.pos());
        OmoLife.LOG.info("{} linked {} to station {}", player.getPlainTextName(), at.toShortString(),
            s.pos().toShortString());
    }

    private static void unlink(ServerPlayer player, StationLinked target) {
        BlockPos at = ((BlockEntity) target).getBlockPos();
        if (target.linkedStation() == null) {
            say(player, "This is not linked to a station.", false);
            return;
        }
        target.setLinkedStation(null);
        say(player, "Unlinked.", true);
        chime(player, at, 0.9F);
    }

    // -- feedback -------------------------------------------------------------------------

    /** "Website · Dev", or where the station stands when it has no name yet. */
    public static String label(String name, BlockPos pos) {
        return name == null || name.isEmpty() ? "the station at " + pos.toShortString() : name;
    }

    public static void say(ServerPlayer player, String text, boolean good) {
        player.sendSystemMessage(Component.literal(text).withStyle(good ? ChatFormatting.GOLD : ChatFormatting.GRAY),
            true);
    }

    private static void chime(ServerPlayer player, BlockPos at, float pitch) {
        player.level().playSound(null, at, SoundEvents.AMETHYST_BLOCK_CHIME, SoundSource.BLOCKS, 0.7F, pitch);
    }

    /** Gold dust along the corners of a station: it is the chosen one. */
    private static void outline(ServerLevel level, BlockPos pos) {
        DustParticleOptions dust = new DustParticleOptions(GOLD, 0.8F);
        for (int i = 0; i < 12; i++) {
            double t = i / 11.0;
            for (int e = 0; e < 4; e++) {
                double x = pos.getX() + ((e & 1) == 0 ? -0.02 : 1.02);
                double z = pos.getZ() + ((e & 2) == 0 ? -0.02 : 1.02);
                level.sendParticles(dust, x, pos.getY() + t, z, 1, 0, 0, 0, 0);
            }
        }
    }

    /** Gold dust from a screen to its station. */
    private static void trail(ServerLevel level, BlockPos from, BlockPos to) {
        DustParticleOptions dust = new DustParticleOptions(GOLD, 0.7F);
        Vec3 a = Vec3.atCenterOf(from);
        Vec3 d = Vec3.atCenterOf(to).subtract(a);
        int steps = (int) Math.max(4, d.length() * 3);
        for (int i = 0; i <= steps; i++) {
            Vec3 p = a.add(d.scale(i / (double) steps));
            level.sendParticles(dust, p.x, p.y, p.z, 1, 0, 0, 0, 0);
        }
    }
}
