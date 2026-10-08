package space.omo.life.block;

import com.mojang.serialization.MapCodec;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlock;
import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;
import dan200.computercraft.shared.platform.RegistryEntry;

import net.minecraft.core.BlockPos;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;

import space.omo.life.OmoLife;
import space.omo.life.builder.OmoAdvancements;
import space.omo.life.remote.RemoteWalls;

/**
 * The Omo Monitor block: CC:T's advanced monitor with a source binding.
 *
 * <p>Right-clicking the screen face takes control of the wall: typing and
 * the crosshair go to its station or its page, from up to
 * {@link RemoteWalls#RANGE} blocks away. Shift+right-click opens the
 * monitor's own controls (source: terminal or web page; URL; zoom;
 * interact). A terminal wall no Omo station drives keeps CC:T's behaviour,
 * so Lua programs that rely on {@code monitor_touch} keep working. The page
 * binding travels with the item (see {@link OmoMonitorBlockEntity}).
 */
public class OmoMonitorBlock extends MonitorBlock {

    public static final MapCodec<OmoMonitorBlock> CODEC = simpleCodec(OmoMonitorBlock::new);

    private final LateEntry entry;

    public OmoMonitorBlock(Properties properties) {
        this(properties, new LateEntry());
    }

    /**
     * How far behind the front of the block the glass is, in blocks: 0 for
     * a monitor, more for a {@link SlimScreenBlock}. The renderer, the
     * crosshair and the line-of-sight check all measure from the glass.
     */
    public static double glassInset(BlockState state) {
        return state.getBlock() instanceof SlimScreenBlock ? SlimScreenBlock.INSET : 0;
    }

    private OmoMonitorBlock(Properties properties, LateEntry entry) {
        super(properties, entry);
        this.entry = entry;
    }

    public void bindType(BlockEntityType<OmoMonitorBlockEntity> type) {
        entry.set(type);
    }

    @Override
    protected MapCodec<? extends OmoMonitorBlock> codec() {
        return CODEC;
    }

    /**
     * Called from {@code MonitorInteractMixin}: CC:T declares
     * {@code useWithoutItem} final on {@link MonitorBlock}. Right-click takes
     * control of the wall, Shift+right-click opens its settings (see
     * {@link RemoteWalls}). PASS hands a terminal wall that no station drives
     * back to CC:T, whose click is {@code monitor_touch}.
     */
    public InteractionResult omoUse(Level level, BlockPos pos, Player player, BlockHitResult hit) {
        if (!(level.getBlockEntity(pos) instanceof OmoMonitorBlockEntity be) || be.getFront() != hit.getDirection()) {
            return InteractionResult.PASS;
        }
        if (player instanceof ServerPlayer sp && !RemoteWalls.use(sp, be, sp.isCrouching())) {
            return InteractionResult.PASS;
        }
        return InteractionResult.SUCCESS;
    }

    /** CC:T joins the wall as the player places it; a big enough one earns "Big Screen Energy". */
    @Override
    public void setPlacedBy(Level level, BlockPos pos, BlockState state, LivingEntity placer, ItemStack stack) {
        super.setPlacedBy(level, pos, state, placer, stack);
        if (placer instanceof ServerPlayer sp && level.getBlockEntity(pos) instanceof OmoMonitorBlockEntity be
            && Math.max(be.getWidth(), be.getHeight()) >= 3 && Math.min(be.getWidth(), be.getHeight()) >= 2) {
            OmoAdvancements.award(sp, OmoAdvancements.BIG_SCREEN);
        }
    }

    static final class LateEntry implements RegistryEntry<BlockEntityType<? extends MonitorBlockEntity>> {
        private BlockEntityType<? extends MonitorBlockEntity> value;

        void set(BlockEntityType<? extends MonitorBlockEntity> v) {
            value = v;
        }

        @Override
        public BlockEntityType<? extends MonitorBlockEntity> get() {
            if (value == null) {
                throw new IllegalStateException("Omo monitor block entity type used before registration");
            }
            return value;
        }

        @Override
        public Identifier id() {
            return OmoLife.id("monitor");
        }
    }
}
