package space.omo.life.block;

import com.mojang.serialization.Codec;
import com.mojang.serialization.MapCodec;
import com.mojang.serialization.codecs.RecordCodecBuilder;

import dan200.computercraft.shared.computer.blocks.ComputerBlock;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.network.container.ComputerContainerData;
import dan200.computercraft.shared.platform.PlatformHelper;
import dan200.computercraft.shared.platform.RegistryEntry;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityTicker;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;

import space.omo.life.OmoLife;
import space.omo.life.builder.OmoAdvancements;
import space.omo.life.station.StationSignals;
import space.omo.life.station.Stations;

/**
 * The Hermes Computer and Terminal blocks. One class, one flag: a Hermes
 * Computer's workspace opens with a Hermes tab, a Terminal's with a shell.
 *
 * <p>Built directly on CC:T's {@link ComputerBlock}, so power, redstone,
 * peripherals, the on/off/blinking state and the computer GUI all behave as
 * players already know. What changes is identity: the block entity carries a
 * workspace binding that travels with the item, so breaking and re-placing a
 * station (moving it) keeps the same workspace instead of starting a new one.
 *
 * <p>Redstone: a comparator reads the workspace's state, and a station can
 * also give a plain signal while it works ({@link StationSignals}). Both add
 * to CC:T's own redstone, which Lua keeps driving as usual.
 */
public class HermesComputerBlock extends ComputerBlock<StationBlockEntity> {

    public static final MapCodec<HermesComputerBlock> CODEC = RecordCodecBuilder.mapCodec(i -> i.group(
        propertiesCodec(),
        Codec.BOOL.fieldOf("hermes").forGetter(HermesComputerBlock::isHermes)
    ).apply(i, HermesComputerBlock::new));

    private final boolean hermes;
    private final LateEntry entry;

    public HermesComputerBlock(Properties properties, boolean hermes) {
        this(properties, hermes, new LateEntry());
    }

    private HermesComputerBlock(Properties properties, boolean hermes, LateEntry entry) {
        super(properties, entry);
        this.hermes = hermes;
        this.entry = entry;
    }

    /** Registration order: the block exists before its entity type does. */
    public void bindType(BlockEntityType<StationBlockEntity> type) {
        entry.set(type);
    }

    public boolean isHermes() {
        return hermes;
    }

    @Override
    protected MapCodec<? extends ComputerBlock<?>> codec() {
        return CODEC;
    }

    /**
     * CC:T's handler plus three things: only the world owner may open a
     * station, the stack handed to the client carries the binding and block
     * position (so the station panel knows which workspace this is), and the
     * station screen is told it has focus.
     */
    @Override
    protected InteractionResult useWithoutItem(BlockState state, Level level, BlockPos pos,
                                               Player player, BlockHitResult hit) {
        if (player.isCrouching() || !(level.getBlockEntity(pos) instanceof StationBlockEntity be)) {
            return super.useWithoutItem(state, level, pos, player, hit);
        }
        if (!(player instanceof ServerPlayer sp)) {
            return InteractionResult.SUCCESS;
        }
        if (!Stations.allowed(sp)) {
            Stations.refuse(sp);
            return InteractionResult.SUCCESS;
        }
        if (!be.isUsable(player)) {
            return InteractionResult.SUCCESS;
        }
        be.ensureWorkspace();
        ServerComputer computer = be.createServerComputer();
        computer.turnOn();
        PlatformHelper.get().openMenu(player, be.getName(), be, new ComputerContainerData(computer, be.screenStack()));
        computer.queueEvent("omo_focus", new Object[]{"computer"});   // CONTRACT A: the screen, not a wall, has the keys
        if (hermes) {
            OmoAdvancements.award(sp, OmoAdvancements.OPEN_HERMES);
        }
        return InteractionResult.SUCCESS;
    }

    // -- redstone ---------------------------------------------------------------

    @Override
    protected boolean hasAnalogOutputSignal(BlockState state) {
        return true;
    }

    @Override
    protected int getAnalogOutputSignal(BlockState state, Level level, BlockPos pos, Direction direction) {
        return level.getBlockEntity(pos) instanceof StationBlockEntity be ? StationSignals.comparator(be) : 0;
    }

    /** Strong, like CC:T's own output (whose {@code getSignal} asks this too). */
    @Override
    protected int getDirectSignal(BlockState state, BlockGetter level, BlockPos pos, Direction direction) {
        int lua = super.getDirectSignal(state, level, pos, direction);
        return level.getBlockEntity(pos) instanceof StationBlockEntity be && be.getLevel() != null
            && !be.getLevel().isClientSide() ? Math.max(lua, StationSignals.plain(be)) : lua;
    }

    /** CC:T's ticker, then the station's redstone reading (server only). */
    @Override
    public <U extends BlockEntity> BlockEntityTicker<U> getTicker(Level level, BlockState state,
                                                                  BlockEntityType<U> type) {
        BlockEntityTicker<U> computer = super.getTicker(level, state, type);
        if (level.isClientSide() || computer == null) {
            return computer;
        }
        return (l, pos, s, be) -> {
            computer.tick(l, pos, s, be);
            if (be instanceof StationBlockEntity station) {
                StationSignals.tick(station);
            }
        };
    }

    /**
     * CC:T's block constructor wants the entity type up front, but Fabric
     * registers block entity types after their blocks. This holder is filled
     * in by {@link #bindType} before the first world loads.
     */
    static final class LateEntry implements RegistryEntry<BlockEntityType<StationBlockEntity>> {
        private BlockEntityType<StationBlockEntity> value;

        void set(BlockEntityType<StationBlockEntity> v) {
            value = v;
        }

        @Override
        public BlockEntityType<StationBlockEntity> get() {
            if (value == null) {
                throw new IllegalStateException("Omo station block entity type used before registration");
            }
            return value;
        }

        @Override
        public Identifier id() {
            return OmoLife.id("station");
        }
    }
}
