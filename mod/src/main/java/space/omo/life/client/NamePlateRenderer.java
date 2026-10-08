package space.omo.life.client;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.math.Axis;

import net.minecraft.client.gui.Font;
import net.minecraft.client.renderer.SubmitNodeCollector;
import net.minecraft.client.renderer.blockentity.BlockEntityRenderer;
import net.minecraft.client.renderer.blockentity.BlockEntityRendererProvider;
import net.minecraft.client.renderer.blockentity.state.BlockEntityRenderState;
import net.minecraft.client.renderer.feature.ModelFeatureRenderer;
import net.minecraft.client.renderer.state.level.CameraRenderState;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.AttachFace;
import net.minecraft.world.phys.Vec3;

import space.omo.life.block.NamePlateBlock;
import space.omo.life.block.StationGlow;
import space.omo.life.block.StatusBlockEntity;

/**
 * Draws a Name Plate's text: a state dot and the workspace name, and under
 * them, smaller, what the station is and how it is. Lit like a small screen
 * (full bright), sized like a sign's text and shrunk to fit a long name.
 */
public final class NamePlateRenderer implements BlockEntityRenderer<StatusBlockEntity, NamePlateRenderer.State> {

    public static final class State extends BlockEntityRenderState {
        boolean plate;
        Direction front;
        AttachFace face;
        String name;
        String kind;
        StationGlow glow;
    }

    private static final int FULL_BRIGHT = 0xF000F0;
    /** Text width on the plate's face (14 px wide, a 1 px rim, a margin), in blocks. */
    private static final float TEXT_W = 11 / 16F;
    /** At most a sign's text size, in blocks per font pixel. */
    private static final float MAX_SCALE = 1 / 110F;

    private final Font font;

    public NamePlateRenderer(BlockEntityRendererProvider.Context ctx) {
        this.font = ctx.font();
    }

    @Override
    public State createRenderState() {
        return new State();
    }

    @Override
    public void extractRenderState(StatusBlockEntity be, State state, float partial, Vec3 cameraPos,
                                   ModelFeatureRenderer.CrumblingOverlay crumbling) {
        BlockEntityRenderState.extractBase(be, state, crumbling);
        BlockState block = be.getBlockState();
        state.plate = block.getBlock() instanceof NamePlateBlock;
        if (!state.plate) {
            return;                                   // a Status Lamp: its block state is all it shows
        }
        state.front = NamePlateBlock.front(block);
        state.face = block.getValue(NamePlateBlock.FACE);
        state.name = be.name();
        state.kind = be.kind();
        state.glow = be.glow();
    }

    @Override
    public void submit(State state, PoseStack pose, SubmitNodeCollector collector, CameraRenderState camera) {
        if (!state.plate) {
            return;
        }
        // Plate centre and face, for a plate looking south; then turned to look where it does.
        float centreY = switch (state.face) {
            case WALL -> 8 / 16F;
            case FLOOR -> 4 / 16F;
            case CEILING -> 11 / 16F;
        };
        float faceZ = state.face == AttachFace.WALL ? -7 / 16F : 1 / 32F;
        pose.pushPose();
        pose.translate(0.5F, centreY, 0.5F);
        pose.mulPose(Axis.YP.rotationDegrees(-state.front.toYRot()));
        pose.translate(0, 0, faceZ + 0.002F);

        Component title = state.kind.isEmpty() ? Component.translatable("omolife.plate.none")
            : state.name.isEmpty() ? Component.translatable(state.kind.equals("hermes") ? "block.omolife.hermes_computer"
            : "block.omolife.terminal") : Component.literal(state.name);
        Component detail = Component.translatable(state.kind.isEmpty() ? "omolife.plate.hint"
            : "omolife.plate." + state.kind, Component.translatable("omolife.status." + state.glow.getSerializedName()));
        String dot = "● ";
        int titleW = font.width(dot) + font.width(title);
        int detailW = font.width(detail);
        float scale = Math.min(MAX_SCALE, TEXT_W / Math.max(titleW, detailW * 0.7F));
        float small = scale * 0.7F;
        float top = (font.lineHeight * scale + font.lineHeight * small) / 2;

        pose.pushPose();
        pose.translate(-titleW * scale / 2, top, 0);
        pose.scale(scale, -scale, scale);
        line(pose, collector, Component.literal(dot).getVisualOrderText(), 0, state.glow.colour());
        line(pose, collector, title.getVisualOrderText(), font.width(dot),
            state.kind.isEmpty() ? OmoStyle.MUTED : OmoStyle.CREAM);
        pose.popPose();

        pose.pushPose();
        pose.translate(-detailW * small / 2, top - font.lineHeight * scale, 0);
        pose.scale(small, -small, small);
        line(pose, collector, detail.getVisualOrderText(), 0,
            state.kind.isEmpty() ? OmoStyle.FAINT : OmoStyle.accent(state.kind.equals("hermes")));
        pose.popPose();
        pose.popPose();
    }

    private void line(PoseStack pose, SubmitNodeCollector collector, FormattedCharSequence text, float x, int colour) {
        collector.submitText(pose, x, 0, text, false, Font.DisplayMode.POLYGON_OFFSET, FULL_BRIGHT, colour, 0, 0);
    }
}
