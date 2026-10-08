package space.omo.life.block;

import java.util.Map;

import com.mojang.serialization.MapCodec;

import dan200.computercraft.shared.peripheral.monitor.MonitorEdgeState;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.component.DataComponents;
import net.minecraft.sounds.SoundEvents;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.BlockItemStateProperties;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.LevelReader;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.StateDefinition;
import net.minecraft.world.level.block.state.properties.EnumProperty;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.shapes.CollisionContext;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;

/**
 * The Slim Screen: an Omo Monitor built like a flat TV, 3 px deep, flat
 * against the wall, floor or ceiling it is placed on.
 *
 * <p>Everything else is the monitor's: it joins neighbouring Slim Screens
 * into one wall (never a full monitor: the depths differ), shows a terminal
 * or a pinned page through the same renderer, and has the same remote
 * control and settings. It mounts on the face you click, the way item
 * frames do; clicking the edge of a screen carries that wall on.
 *
 * <p>Its frame has a {@link FrameFinish}: right-click with bone meal or a
 * brown, yellow, orange, black or white dye to refinish the whole wall.
 * Screens placed beside a wall take its finish.
 */
public final class SlimScreenBlock extends OmoMonitorBlock {

    public static final MapCodec<SlimScreenBlock> CODEC = simpleCodec(SlimScreenBlock::new);
    public static final EnumProperty<FrameFinish> FINISH = EnumProperty.create("finish", FrameFinish.class);

    /** The glass is the front of a 3 px panel at the back of the block. */
    public static final double INSET = 13 / 16.0;
    private static final Map<Direction, VoxelShape> SHAPES = Shapes.rotateAll(Block.box(0, 0, 13, 16, 16, 16));

    public SlimScreenBlock(Properties properties) {
        super(properties);
        registerDefaultState(defaultBlockState().setValue(FINISH, FrameFinish.CREAM));
    }

    @Override
    protected MapCodec<SlimScreenBlock> codec() {
        return CODEC;
    }

    @Override
    protected void createBlockStateDefinition(StateDefinition.Builder<Block, BlockState> builder) {
        super.createBlockStateDefinition(builder);
        builder.add(FINISH);
    }

    /** The way the glass faces: CC:T's orientation for floors and ceilings, else the facing. */
    static Direction front(BlockState state) {
        Direction orientation = state.getValue(ORIENTATION);
        return orientation == Direction.NORTH ? state.getValue(FACING) : orientation;
    }

    @Override
    protected VoxelShape getShape(BlockState state, BlockGetter level, BlockPos pos, CollisionContext context) {
        return SHAPES.get(front(state));
    }

    @Override
    public BlockState getStateForPlacement(BlockPlaceContext context) {
        Direction face = context.getClickedFace();
        Level level = context.getLevel();
        BlockPos pos = context.getClickedPos();
        BlockState on = level.getBlockState(pos.relative(face.getOpposite()));
        BlockState state;
        if (on.is(this) && front(on).getAxis() != face.getAxis()) {
            state = on.setValue(STATE, MonitorEdgeState.NONE);
        } else if (face.getAxis() == Direction.Axis.Y) {
            state = defaultBlockState().setValue(ORIENTATION, face)
                .setValue(FACING, context.getHorizontalDirection().getOpposite());
        } else {
            state = defaultBlockState().setValue(FACING, face);
        }
        // Beside a screen facing the same way, take its finish, and on a floor
        // or ceiling its turn too, so the two join whichever way you stand.
        BlockState beside = screenBeside(level, pos, front(state));
        return beside == null ? state
            : state.setValue(FACING, beside.getValue(FACING)).setValue(FINISH, beside.getValue(FINISH));
    }

    /** A Slim Screen next to {@code pos}, in the plane of a screen facing {@code front}, facing it too. */
    private BlockState screenBeside(LevelReader level, BlockPos pos, Direction front) {
        for (Direction d : Direction.values()) {
            BlockState other = level.getBlockState(pos.relative(d));
            if (d.getAxis() != front.getAxis() && other.is(this) && front(other) == front) {
                return other;
            }
        }
        return null;
    }

    /**
     * A finishing item refinishes every panel of the wall and is used up
     * (not in creative). Any other item, or the finish the panel already
     * has, falls through to the empty-hand click: taking control of the wall.
     */
    @Override
    protected InteractionResult useItemOn(ItemStack stack, BlockState state, Level level, BlockPos pos, Player player,
                                          InteractionHand hand, BlockHitResult hit) {
        FrameFinish finish = FrameFinish.of(stack);
        if (finish == null || finish == state.getValue(FINISH)
            || !(level.getBlockEntity(pos) instanceof OmoMonitorBlockEntity panel)) {
            return super.useItemOn(stack, state, level, pos, player, hand, hit);
        }
        if (!level.isClientSide()) {
            BlockPos origin = panel.origin();
            for (int x = 0; x < panel.getWidth(); x++) {
                for (int y = 0; y < panel.getHeight(); y++) {
                    BlockPos p = origin.relative(panel.getRight(), x).relative(panel.getDown(), y);
                    BlockState s = level.getBlockState(p);
                    if (s.is(this)) {
                        level.setBlock(p, s.setValue(FINISH, finish), Block.UPDATE_CLIENTS);
                    }
                }
            }
            level.playSound(null, pos, SoundEvents.DYE_USE, SoundSource.BLOCKS, 1.0F, 1.0F);
            stack.consume(1, player);
        }
        return InteractionResult.SUCCESS;
    }

    /** A Slim Screen item in a finish (cream, the default, carries nothing). */
    public static ItemStack stack(Item item, FrameFinish finish) {
        return finished(new ItemStack(item), finish);
    }

    /** Pick-block keeps the finish, so a builder can copy a gold screen. */
    @Override
    protected ItemStack getCloneItemStack(LevelReader level, BlockPos pos, BlockState state, boolean includeData) {
        return finished(super.getCloneItemStack(level, pos, state, includeData), state.getValue(FINISH));
    }

    private static ItemStack finished(ItemStack stack, FrameFinish finish) {
        if (finish != FrameFinish.CREAM) {
            stack.set(DataComponents.BLOCK_STATE, BlockItemStateProperties.EMPTY.with(FINISH, finish));
        }
        return stack;
    }
}
