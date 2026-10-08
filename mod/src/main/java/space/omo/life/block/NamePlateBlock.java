package space.omo.life.block;

import java.util.Map;

import com.mojang.serialization.MapCodec;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.util.RandomSource;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.LevelReader;
import net.minecraft.world.level.ScheduledTickAccess;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.EntityBlock;
import net.minecraft.world.level.block.FaceAttachedHorizontalDirectionalBlock;
import net.minecraft.world.level.block.SimpleWaterloggedBlock;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.StateDefinition;
import net.minecraft.world.level.block.state.properties.AttachFace;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.BooleanProperty;
import net.minecraft.world.level.material.FluidState;
import net.minecraft.world.level.material.Fluids;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.shapes.CollisionContext;
import net.minecraft.world.phys.shapes.Shapes;
import net.minecraft.world.phys.shapes.VoxelShape;

import space.omo.life.OmoLife;

/**
 * The Name Plate: a small plaque with a station's live workspace name and a
 * state dot. It hangs on a wall, stands on a desk or hangs from a ceiling,
 * like a button is placed; right-click it to see which station it shows.
 * The text is drawn by {@code NamePlateRenderer}; see {@link StatusBlockEntity}.
 */
public final class NamePlateBlock extends FaceAttachedHorizontalDirectionalBlock implements EntityBlock, SimpleWaterloggedBlock {

    public static final MapCodec<NamePlateBlock> CODEC = simpleCodec(NamePlateBlock::new);
    public static final BooleanProperty WATERLOGGED = BlockStateProperties.WATERLOGGED;

    // Shapes for a plate whose face looks north.
    private static final Map<Direction, VoxelShape> WALL = Shapes.rotateHorizontal(Block.box(1, 5, 15, 15, 11, 16));
    private static final Map<Direction, VoxelShape> FLOOR = Shapes.rotateHorizontal(
        Shapes.or(Block.box(1, 1, 7.5, 15, 7, 8.5), Block.box(3, 0, 6, 13, 1, 10)));
    private static final Map<Direction, VoxelShape> CEILING = Shapes.rotateHorizontal(
        Shapes.or(Block.box(1, 8, 7.5, 15, 14, 8.5), Block.box(3, 14, 7.5, 13, 16, 8.5)));

    public NamePlateBlock(Properties properties) {
        super(properties);
        registerDefaultState(stateDefinition.any().setValue(FACING, Direction.NORTH).setValue(FACE, AttachFace.WALL)
            .setValue(WATERLOGGED, false));
    }

    @Override
    protected MapCodec<NamePlateBlock> codec() {
        return CODEC;
    }

    @Override
    protected void createBlockStateDefinition(StateDefinition.Builder<Block, BlockState> builder) {
        builder.add(FACING, FACE, WATERLOGGED);
    }

    /** The way the plate's text faces: out of the wall, or towards whoever placed it. */
    public static Direction front(BlockState state) {
        Direction facing = state.getValue(FACING);
        return state.getValue(FACE) == AttachFace.WALL ? facing : facing.getOpposite();
    }

    @Override
    protected VoxelShape getShape(BlockState state, BlockGetter level, BlockPos pos, CollisionContext context) {
        return (switch (state.getValue(FACE)) {
            case WALL -> WALL;
            case FLOOR -> FLOOR;
            case CEILING -> CEILING;
        }).get(front(state));
    }

    @Override
    public BlockState getStateForPlacement(BlockPlaceContext context) {
        BlockState state = super.getStateForPlacement(context);
        return state == null ? null
            : state.setValue(WATERLOGGED, context.getLevel().getFluidState(context.getClickedPos()).is(Fluids.WATER));
    }

    @Override
    protected BlockState updateShape(BlockState state, LevelReader level, ScheduledTickAccess ticks, BlockPos pos,
                                     Direction direction, BlockPos neighbourPos, BlockState neighbour,
                                     RandomSource random) {
        if (state.getValue(WATERLOGGED)) {
            ticks.scheduleTick(pos, Fluids.WATER, Fluids.WATER.getTickDelay(level));
        }
        return super.updateShape(state, level, ticks, pos, direction, neighbourPos, neighbour, random);
    }

    @Override
    protected FluidState getFluidState(BlockState state) {
        return state.getValue(WATERLOGGED) ? Fluids.WATER.getSource(false) : super.getFluidState(state);
    }

    @Override
    protected InteractionResult useWithoutItem(BlockState state, Level level, BlockPos pos, Player player,
                                               BlockHitResult hit) {
        return StatusLampBlock.tell(level, pos, player);
    }

    @Override
    public BlockEntity newBlockEntity(BlockPos pos, BlockState state) {
        return new StatusBlockEntity(OmoLife.STATUS_BE, pos, state);
    }
}
