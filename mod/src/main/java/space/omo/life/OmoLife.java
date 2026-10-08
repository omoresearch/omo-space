package space.omo.life;

import dan200.computercraft.api.ComputerCraftAPI;
import dan200.computercraft.api.peripheral.PeripheralLookup;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.creativetab.v1.CreativeModeTabEvents;
import net.fabricmc.fabric.api.creativetab.v1.FabricCreativeModeTab;
import net.fabricmc.fabric.api.object.builder.v1.block.entity.FabricBlockEntityTypeBuilder;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.item.CreativeModeTab;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.SoundType;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockBehaviour;
import net.minecraft.world.level.material.MapColor;
import net.minecraft.world.level.material.PushReaction;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import space.omo.life.block.FrameFinish;
import space.omo.life.block.HermesComputerBlock;
import space.omo.life.block.NamePlateBlock;
import space.omo.life.block.OmoBlockItem;
import space.omo.life.block.OmoMonitorBlock;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.SlimScreenBlock;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.block.StationStatus;
import space.omo.life.block.StatusBlockEntity;
import space.omo.life.block.StatusLampBlock;
import space.omo.life.block.WallForming;
import space.omo.life.builder.BuilderCommands;
import space.omo.life.link.Links;
import space.omo.life.lua.OmoLuaApi;
import space.omo.life.net.OmoNetworking;
import space.omo.life.pocket.OmoPocketItem;
import space.omo.life.station.StationCommands;

/**
 * Omo Space: Hermes computers, terminals and monitors for Minecraft.
 *
 * <p>Three blocks, all built on CC: Tweaked's proven computer and monitor
 * technology (credit: Daniel Ratcliffe, SquidDev and the CC:T contributors):
 * <ul>
 *   <li><b>Hermes Computer</b> - a station whose first tab starts a Hermes
 *       conversation. Its workspace lives on the host in tmux.</li>
 *   <li><b>Terminal</b> - the same station, but the first tab is a plain
 *       persistent shell and nothing launches an agent automatically.</li>
 *   <li><b>Omo Monitor</b> - a CC:T monitor that can also show a pinned web
 *       page (a real Chromium page via MCEF) instead of a terminal.</li>
 * </ul>
 * For builders, three more: the <b>Slim Screen</b> (the monitor as a flat
 * wall-mounted screen, in six frame finishes), and the <b>Name Plate</b> and
 * <b>Status Lamp</b>, which show a nearby station's workspace and state.
 *
 * <p>The blocks store only non-secret bindings in the world: a workspace id,
 * a monitor's page URL. Shells, credentials and browser profiles stay on the
 * host.
 */
public final class OmoLife implements ModInitializer {

    public static final String MOD_ID = "omolife";
    public static final Logger LOG = LoggerFactory.getLogger("Omo Space");

    public static Identifier id(String path) {
        return Identifier.fromNamespaceAndPath(MOD_ID, path);
    }

    // -- blocks -------------------------------------------------------------
    public static final HermesComputerBlock HERMES_COMPUTER = new HermesComputerBlock(
        computerProps("hermes_computer"), true);
    public static final HermesComputerBlock TERMINAL = new HermesComputerBlock(
        computerProps("terminal"), false);
    public static final OmoMonitorBlock MONITOR = new OmoMonitorBlock(
        BlockBehaviour.Properties.of().mapColor(MapColor.COLOR_BLACK).strength(1.5F).sound(SoundType.METAL)
            .setId(ResourceKey.create(Registries.BLOCK, id("monitor"))));
    public static final SlimScreenBlock SLIM_SCREEN = new SlimScreenBlock(
        BlockBehaviour.Properties.of().mapColor(MapColor.COLOR_BLACK).strength(1.5F).sound(SoundType.METAL)
            .noOcclusion().setId(ResourceKey.create(Registries.BLOCK, id("slim_screen"))));
    public static final NamePlateBlock NAME_PLATE = new NamePlateBlock(
        BlockBehaviour.Properties.of().mapColor(MapColor.COLOR_BROWN).strength(0.5F).sound(SoundType.HANGING_SIGN)
            .noCollision().pushReaction(PushReaction.DESTROY)
            .setId(ResourceKey.create(Registries.BLOCK, id("name_plate"))));
    public static final StatusLampBlock STATUS_LAMP = new StatusLampBlock(
        BlockBehaviour.Properties.of().mapColor(MapColor.SAND).strength(0.5F).sound(SoundType.BAMBOO_WOOD)
            .noOcclusion().pushReaction(PushReaction.DESTROY)
            .lightLevel(state -> state.getValue(StatusLampBlock.GLOW).light())
            .setId(ResourceKey.create(Registries.BLOCK, id("status_lamp"))));

    // -- block entities -----------------------------------------------------
    public static BlockEntityType<StationBlockEntity> STATION_BE;
    public static BlockEntityType<OmoMonitorBlockEntity> MONITOR_BE;
    public static BlockEntityType<StatusBlockEntity> STATUS_BE;

    // -- items --------------------------------------------------------------
    public static Item HERMES_COMPUTER_ITEM;
    public static Item TERMINAL_ITEM;
    public static Item MONITOR_ITEM;
    public static Item SLIM_SCREEN_ITEM;
    public static Item NAME_PLATE_ITEM;
    public static Item STATUS_LAMP_ITEM;

    public static final ResourceKey<CreativeModeTab> TAB = ResourceKey.create(
        Registries.CREATIVE_MODE_TAB, id("omo"));

    private static BlockBehaviour.Properties computerProps(String name) {
        return BlockBehaviour.Properties.of().mapColor(MapColor.STONE).strength(2.0F)
            .setId(ResourceKey.create(Registries.BLOCK, id(name)));
    }

    @Override
    public void onInitialize() {
        Registry.register(BuiltInRegistries.BLOCK, id("hermes_computer"), HERMES_COMPUTER);
        Registry.register(BuiltInRegistries.BLOCK, id("terminal"), TERMINAL);
        Registry.register(BuiltInRegistries.BLOCK, id("monitor"), MONITOR);
        Registry.register(BuiltInRegistries.BLOCK, id("slim_screen"), SLIM_SCREEN);
        Registry.register(BuiltInRegistries.BLOCK, id("name_plate"), NAME_PLATE);
        Registry.register(BuiltInRegistries.BLOCK, id("status_lamp"), STATUS_LAMP);

        STATION_BE = Registry.register(BuiltInRegistries.BLOCK_ENTITY_TYPE, id("station"),
            FabricBlockEntityTypeBuilder.<StationBlockEntity>create(
                (pos, state) -> new StationBlockEntity(STATION_BE, pos, state),
                HERMES_COMPUTER, TERMINAL).build());
        MONITOR_BE = Registry.register(BuiltInRegistries.BLOCK_ENTITY_TYPE, id("monitor"),
            FabricBlockEntityTypeBuilder.<OmoMonitorBlockEntity>create(
                (pos, state) -> new OmoMonitorBlockEntity(MONITOR_BE, pos, state),
                MONITOR, SLIM_SCREEN).build());
        STATUS_BE = Registry.register(BuiltInRegistries.BLOCK_ENTITY_TYPE, id("status"),
            FabricBlockEntityTypeBuilder.<StatusBlockEntity>create(
                (pos, state) -> new StatusBlockEntity(STATUS_BE, pos, state),
                NAME_PLATE, STATUS_LAMP).build());
        HERMES_COMPUTER.bindType(STATION_BE);
        TERMINAL.bindType(STATION_BE);
        MONITOR.bindType(MONITOR_BE);
        SLIM_SCREEN.bindType(MONITOR_BE);

        HERMES_COMPUTER_ITEM = registerItem("hermes_computer", HERMES_COMPUTER);
        TERMINAL_ITEM = registerItem("terminal", TERMINAL);
        MONITOR_ITEM = registerItem("monitor", MONITOR);
        SLIM_SCREEN_ITEM = registerItem("slim_screen", SLIM_SCREEN);
        NAME_PLATE_ITEM = registerItem("name_plate", NAME_PLATE);
        STATUS_LAMP_ITEM = registerItem("status_lamp", STATUS_LAMP);

        Registry.register(BuiltInRegistries.CREATIVE_MODE_TAB, TAB,
            FabricCreativeModeTab.builder()
                .title(Component.translatable("itemGroup.omolife.omo"))
                .icon(() -> new ItemStack(HERMES_COMPUTER_ITEM))
                .build());
        // Stations, screens (a Slim Screen in every finish), pockets, then the builder blocks.
        CreativeModeTabEvents.modifyOutputEvent(TAB).register(out -> {
            out.accept(HERMES_COMPUTER_ITEM);
            out.accept(TERMINAL_ITEM);
            out.accept(MONITOR_ITEM);
            for (FrameFinish finish : FrameFinish.values()) {
                out.accept(SlimScreenBlock.stack(SLIM_SCREEN_ITEM, finish));
            }
            out.accept(OmoPocketItem.HERMES);
            out.accept(OmoPocketItem.TERMINAL);
            out.accept(NAME_PLATE_ITEM);
            out.accept(STATUS_LAMP_ITEM);
        });

        // A terminal-bound Omo monitor is a CC:T "monitor" peripheral, so
        // peripheral.find("monitor") and term.redirect work as players
        // expect. A web-bound one is not (Chromium covers that glass), nor a
        // linked one (it mirrors its station).
        PeripheralLookup.get().registerForBlockEntity((be, side) -> be.isPeripheral() ? be.peripheral() : null, MONITOR_BE);
        PeripheralLookup.get().registerForBlockEntity((be, side) -> be.peripheral(), STATION_BE);

        // The `omo` Lua API: only station computers get it (see OmoLuaApi).
        ComputerCraftAPI.registerAPIFactory(OmoLuaApi::create);

        OmoNetworking.registerCommon();
        StationCommands.register();
        BuilderCommands.register();
        OmoPocketItem.register();
        WallForming.register();
        StationStatus.register();
        Links.register();
        LOG.info("Omo Space ready: Hermes Computer, Terminal, Monitor, Slim Screen, Name Plate, Status Lamp");
    }

    private static Item registerItem(String name, Block block) {
        ResourceKey<Item> key = ResourceKey.create(Registries.ITEM, id(name));
        Item item = new OmoBlockItem(block, new Item.Properties().setId(key).useBlockDescriptionPrefix(), name);
        return Registry.register(BuiltInRegistries.ITEM, key, item);
    }
}
