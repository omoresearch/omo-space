package space.omo.life.block;

import com.mojang.serialization.MapCodec;

import net.minecraft.core.BlockPos;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.BlockGetter;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.EntityBlock;
import net.minecraft.world.level.block.LanternBlock;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.StateDefinition;
import net.minecraft.world.level.block.state.properties.EnumProperty;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.shapes.CollisionContext;
import net.minecraft.world.phys.shapes.VoxelShape;

import space.omo.life.OmoLife;

/**
 * The Status Lamp: a paper andon that glows with its station's state. Warm
 * gold while Hermes or a command is working, green while the workspace
 * waits at its prompts, red once it was interrupted, dark without a
 * workspace. Placed like a lantern (standing, or hanging from a ceiling);
 * right-click it to see which station it follows.
 *
 * <p>The state is a block state, so the light and the lit paper are plain
 * vanilla lighting and models: nothing to draw per frame, however many
 * lamps a build has.
 */
public final class StatusLampBlock extends LanternBlock implements EntityBlock {

    public static final MapCodec<StatusLampBlock> CODEC = simpleCodec(StatusLampBlock::new);
    public static final EnumProperty<StationGlow> GLOW = EnumProperty.create("glow", StationGlow.class);

    private static final VoxelShape STANDING = Block.column(10, 0, 13);
    private static final VoxelShape HANGING_SHAPE = Block.column(10, 2, 16);

    public StatusLampBlock(Properties properties) {
        super(properties);
        registerDefaultState(defaultBlockState().setValue(GLOW, StationGlow.OFF));
    }

    @Override
    public MapCodec<StatusLampBlock> codec() {
        return CODEC;
    }

    @Override
    protected void createBlockStateDefinition(StateDefinition.Builder<Block, BlockState> builder) {
        super.createBlockStateDefinition(builder);
        builder.add(GLOW);
    }

    @Override
    protected VoxelShape getShape(BlockState state, BlockGetter level, BlockPos pos, CollisionContext context) {
        return state.getValue(HANGING) ? HANGING_SHAPE : STANDING;
    }

    @Override
    protected InteractionResult useWithoutItem(BlockState state, Level level, BlockPos pos, Player player,
                                               BlockHitResult hit) {
        return tell(level, pos, player);
    }

    /** Right-click on a plate or lamp: the action bar says which station it shows, and how it is. */
    static InteractionResult tell(Level level, BlockPos pos, Player player) {
        if (!level.isClientSide() && level.getBlockEntity(pos) instanceof StatusBlockEntity be) {
            player.sendOverlayMessage(be.describe());
        }
        return InteractionResult.SUCCESS;
    }

    @Override
    public BlockEntity newBlockEntity(BlockPos pos, BlockState state) {
        return new StatusBlockEntity(OmoLife.STATUS_BE, pos, state);
    }
}
