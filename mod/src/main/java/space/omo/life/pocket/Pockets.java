package space.omo.life.pocket;

import dan200.computercraft.shared.ModRegistry;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.computer.core.TerminalSize;
import dan200.computercraft.shared.computer.inventory.ComputerMenuWithoutInventory;
import dan200.computercraft.shared.config.ConfigSpec;
import dan200.computercraft.shared.network.container.ComputerContainerData;
import dan200.computercraft.shared.platform.PlatformHelper;
import dan200.computercraft.shared.pocket.core.PocketBrain;
import dan200.computercraft.shared.pocket.core.PocketHolder;
import dan200.computercraft.shared.pocket.items.PocketComputerItem;
import dan200.computercraft.shared.util.InventoryUtil;

import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import space.omo.life.lua.OmoLuaApi;
import space.omo.life.station.Stations;

/**
 * Every pocket computer a player uses is a station: CC:T's own pocket
 * computers (normal and advanced) as much as the Omo Pockets. Called by
 * {@code PocketComputerMixin} from CC:T's pocket item.
 *
 * <p>A pocket becomes a station when its computer is built for a player who
 * may use stations ({@link Stations#allowed}), and stays one from then on
 * (the station id on its stack says so); an Omo Pocket always is one. Its
 * computer is built with the {@code omo} API and at the station terminal
 * size, so the rom autorun starts the same station screen as on a Hermes
 * Computer, and right-click opens the same GUI with the same header, in
 * either hand. A plain CC:T pocket gets a terminal workspace, an Omo Pocket
 * (Hermes) a Hermes one. {@code craftos} on it still drops to CraftOS.
 */
public final class Pockets {

    /** Set while CC:T builds a station pocket's computer, so the hook lets that call through. */
    private static final ThreadLocal<Boolean> BUILDING = new ThreadLocal<>();

    private Pockets() {
    }

    private static boolean hermes(PocketComputerItem item) {
        return item instanceof OmoPocketItem omo && omo.isHermes();
    }

    private static boolean isStation(PocketComputerItem item, ItemStack stack) {
        return item instanceof OmoPocketItem || !PocketStation.idOn(stack).isEmpty();
    }

    /**
     * CC:T is about to build a pocket's computer (and with it its Lua APIs).
     * Build it as a station instead: under {@link OmoLuaApi#createFor} and at
     * the computer terminal size. Null lets CC:T go on as usual (a computer
     * already runs, or this pocket is not a station).
     */
    public static PocketBrain build(PocketComputerItem item, PocketHolder holder, ItemStack stack) {
        if (BUILDING.get() != null || item.getBrain(holder, stack) != null || !isStation(item, stack)
            && !(holder instanceof PocketHolder.PlayerHolder p && Stations.allowed(p.entity()))) {
            return null;
        }
        PocketStation station = PocketStation.claim(holder.level().getServer(), stack, hermes(item));
        stack.set(ModRegistry.DataComponents.TERMINAL_SIZE.get(),
            new TerminalSize(ConfigSpec.computerTermWidth.get(), ConfigSpec.computerTermHeight.get()));
        PocketBrain brain;
        BUILDING.set(Boolean.TRUE);
        try {
            brain = OmoLuaApi.createFor(station, () -> item.getOrCreateBrain(holder, stack));
        } finally {
            BUILDING.remove();
        }
        station.attach(brain.computer());
        if (station.writeTo(stack)) {
            holder.setChanged();
        }
        return brain;
    }

    /** After CC:T's tick: keep the binding on the stack, and split a copy of a running pocket off. */
    public static void tick(ItemStack stack, PocketHolder holder, PocketBrain brain) {
        PocketStation station = PocketStation.of(brain.computer());
        if (station == null) {
            return;
        }
        if (station.isCopy(stack, holder.level().getGameTime())) {
            PocketStation.split(stack);
            holder.setChanged();
        } else if (station.writeTo(stack)) {
            holder.setChanged();
        }
    }

    /**
     * Right-click, with the station rules: only the world owner may use it,
     * the workspace is made on first use, either hand opens the full screen
     * (CC:T's off-hand typing view has no terminal to put a header on), and
     * the screen is told it has focus (CONTRACT A: {@code omo_focus}). Null
     * lets CC:T handle it: the client side, and a plain pocket in the hands
     * of a player who may not use stations.
     */
    public static InteractionResult use(PocketComputerItem item, Level level, Player player, InteractionHand hand) {
        if (!(player instanceof ServerPlayer sp)) {
            return null;
        }
        ItemStack stack = player.getItemInHand(hand);
        if (!Stations.allowed(sp)) {
            if (!isStation(item, stack)) {
                return null;
            }
            Stations.refuse(sp);
            return InteractionResult.SUCCESS;
        }
        PocketHolder.PlayerHolder holder = new PocketHolder.PlayerHolder(sp, InventoryUtil.getHandSlot(player, hand));
        PocketBrain brain = item.getOrCreateBrain(holder, stack);
        ServerComputer computer = brain.computer();
        PocketStation station = PocketStation.of(computer);
        if (station == null) {
            return null;                                     // built as a plain pocket before; CC:T's own screen
        }
        computer.turnOn();
        if (brain.onRightClick((ServerLevel) level)) {
            return InteractionResult.SUCCESS;                // an upgrade took the click
        }
        station.ensureWorkspace();
        PlatformHelper.get().openMenu(player, stack.getHoverName(), (id, inventory, p) ->
                new ComputerMenuWithoutInventory(ModRegistry.Menus.COMPUTER.get(), id, inventory,
                    who -> holder.isValid(computer), computer),
            new ComputerContainerData(computer, stack));
        computer.queueEvent("omo_focus", new Object[]{"computer"});
        return InteractionResult.SUCCESS;
    }
}
