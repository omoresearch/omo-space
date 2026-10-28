package space.omo.life.client;

import java.util.List;

import com.mojang.blaze3d.vertex.PoseStack;
import com.mojang.math.Axis;

import dan200.computercraft.client.render.BoundedBlockEntityRenderer;
import dan200.computercraft.client.render.monitor.MonitorBlockEntityRenderer;
import dan200.computercraft.client.render.text.FixedWidthFontRenderer;
import dan200.computercraft.core.terminal.Terminal;
import dan200.computercraft.shared.peripheral.monitor.ClientMonitor;
import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;
import dan200.computercraft.shared.util.DirectionUtil;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.renderer.SubmitNodeCollector;
import net.minecraft.client.renderer.blockentity.BlockEntityRendererProvider;
import net.minecraft.client.renderer.blockentity.state.BlockEntityRenderState;
import net.minecraft.client.renderer.feature.ModelFeatureRenderer;
import net.minecraft.client.renderer.rendertype.RenderType;
import net.minecraft.client.renderer.rendertype.RenderTypes;
import net.minecraft.client.renderer.state.level.CameraRenderState;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import space.omo.life.block.OmoMonitorBlock;
import space.omo.life.block.OmoMonitorBlockEntity;

/**
 * Renders an Omo monitor: CC:T's terminal when bound to a terminal, a live
 * Chromium page when bound to a web page.
 *
 * <p>The page path draws one textured quad exactly where CC:T draws its
 * terminal (same transform, same margins), sampling the browser's GPU
 * texture through a texture-manager alias. Both are opaque and write depth:
 * the monitor's block model is see-through where the screen goes (CC:T's
 * cutout frame textures), so an opaque backing covers the whole opening, as
 * CC:T's terminal background does, and the page itself (an opaque Chromium
 * page) sits on it. Full-bright and unshaded, so a dashboard stays readable
 * at night, like a real screen. Only the origin panel of a wall draws, as
 * CC:T does; the page follows the wall's size. A wall without a page shows
 * dark glass and, in words sized to the wall, why.
 *
 * <p>With Iris installed (shaders on or off) CC:T's
 * own monitor path smears a terminal wall across the sky: its prebuilt
 * buffers rely on a model-view transform Iris replaces. Terminal walls then
 * draw the way CC:T draws a pocket computer's screen, quad by quad through
 * the normal render pipeline, which Iris handles.
 */
public final class WebMonitorRenderer
    implements BoundedBlockEntityRenderer<OmoMonitorBlockEntity, WebMonitorRenderer.State> {

    public static final class State extends BlockEntityRenderState {
        Direction direction;
        Direction front;
        int width;
        int height;
        BlockPos origin;
        boolean web;
        boolean isOrigin;
        String url;
        int zoom;
        double inset;
        /** How far the screen's layers stand off the glass, x their near spacing (see {@link #depthSpread}). */
        float spread = 1;
        MonitorBlockEntityRenderer.State terminalState;
        ClientMonitor monitor;
    }

    /** Lightmap coordinate for full brightness (sky 15, block 15). */
    private static final int FULL_BRIGHT = 0xF000F0;
    private static final int VIEW_DISTANCE = 64;
    private static final boolean QUADS = FabricLoader.getInstance().isModLoaded("iris");
    /** CC:T's margin around a monitor's text, in blocks. */
    private static final float TEXT_MARGIN = (float) (MonitorBlockEntity.RENDER_MARGIN * 1.1);

    private final MonitorBlockEntityRenderer terminal;

    public WebMonitorRenderer(BlockEntityRendererProvider.Context ctx) {
        this.terminal = new MonitorBlockEntityRenderer(ctx);
    }

    @Override
    public State createRenderState() {
        State s = new State();
        s.terminalState = terminal.createRenderState();
        return s;
    }

    @Override
    public void extractRenderState(OmoMonitorBlockEntity be, State state, float partial, Vec3 cameraPos,
                                   ModelFeatureRenderer.CrumblingOverlay crumbling) {
        BlockEntityRenderState.extractBase(be, state, crumbling);
        state.web = be.isWeb();
        state.isOrigin = be.getXIndex() == 0 && be.getYIndex() == 0;
        state.url = be.url();
        state.zoom = be.zoom();
        state.direction = be.getDirection();
        state.front = be.getFront();
        state.inset = OmoMonitorBlock.glassInset(be.getBlockState());
        state.width = be.getWidth();
        state.height = be.getHeight();
        state.origin = be.origin();
        state.spread = depthSpread(cameraPos.distanceTo(Vec3.atCenterOf(state.origin)));
        // Only the origin panel draws, for terminals as for pages: CC:T's
        // renderer draws the whole wall relative to the panel it is given, so
        // delegating from every panel stacked one copy per panel.
        if (!state.web && state.isOrigin) {
            if (QUADS) {
                state.monitor = be.getOriginClientMonitor();
            } else {
                terminal.extractRenderState(be, state.terminalState, partial, cameraPos, crumbling);
            }
        }
    }

    @Override
    public void submit(State state, PoseStack pose, SubmitNodeCollector collector, CameraRenderState camera) {
        if (!state.isOrigin) {
            return;
        }
        // A Slim Screen's glass is set back against its wall: draw the whole wall there.
        pose.pushPose();
        pose.translate(-state.front.getStepX() * state.inset, -state.front.getStepY() * state.inset,
            -state.front.getStepZ() * state.inset);
        submitWall(state, pose, collector, camera);
        pose.popPose();
    }

    private void submitWall(State state, PoseStack pose, SubmitNodeCollector collector, CameraRenderState camera) {
        if (!state.web && !QUADS) {
            terminal.submit(state.terminalState, pose, collector, camera);
            return;
        }
        float border = (float) (MonitorBlockEntity.RENDER_BORDER + MonitorBlockEntity.RENDER_MARGIN);
        float margin = (float) MonitorBlockEntity.RENDER_MARGIN;
        float w = state.width - 2 * border;
        float h = state.height - 2 * border;

        pose.pushPose();
        pose.translate(0.5, 0.5, 0.5);
        pose.mulPose(Axis.YN.rotationDegrees(state.direction.toYRot()));
        pose.mulPose(Axis.XP.rotationDegrees(DirectionUtil.toPitchAngle(state.front)));
        pose.translate(-0.5 + border, state.height - 0.5 - border, 0.5);
        pose.scale(1, 1, state.spread);          // layers stay apart in depth from far away
        if (!state.web) {
            terminalQuads(pose, collector, state.monitor == null ? null : state.monitor.getTerminal(), w, h);
            pose.popPose();
            return;
        }

        WebPages.Page page = WebPages.open(state.origin, state.url);

        boolean live = page != null && page.ready();
        // The whole opening, from the frame in (the margin included), is opaque.
        glass(pose, collector, -margin, margin, w + margin, -h - margin,
            live ? 0xFF000000 : OmoStyle.ESPRESSO, live ? 0xFF000000 : OmoStyle.ESPRESSO_DEEP);
        if (!live) {
            String why = page == null ? WebPages.refusal() : page.status();
            placeholder(pose, collector, w, h,
                why.isEmpty() ? "Loading " + OmoMonitorBlockEntity.host(state.url) + "…" : why);
            pose.popPose();
            return;
        }

        page.markVisible();
        int[] px = WebPages.pixelSize(state.width, state.height, state.zoom);
        page.resizeForBlock(px[0], px[1]);
        quad(pose, collector, RenderTypes.text(WebPages.texture(page)), w, h);
        pose.popPose();
    }

    /**
     * A terminal wall drawn quad by quad, with CC:T's own font code and
     * layout (the transform above is CC:T's): the {@link #QUADS} path.
     */
    private static void terminalQuads(PoseStack pose, SubmitNodeCollector collector, Terminal term, float w, float h) {
        if (term == null) {
            FixedWidthFontRenderer.drawEmptyTerminal(pose, collector, -TEXT_MARGIN, TEXT_MARGIN,
                w + 2 * TEXT_MARGIN, -(h + 2 * TEXT_MARGIN));
            return;
        }
        float xScale = w / (term.getWidth() * FixedWidthFontRenderer.FONT_WIDTH);
        float yScale = h / (term.getHeight() * FixedWidthFontRenderer.FONT_HEIGHT);
        float xMargin = TEXT_MARGIN / xScale;
        float yMargin = TEXT_MARGIN / yScale;
        pose.scale(xScale, -yScale, 1);
        collector.submitCustomGeometry(pose, FixedWidthFontRenderer.TERMINAL_TEXT, (p, vc) ->
            FixedWidthFontRenderer.drawTerminal(p.pose(), vc, 0, 0, term, yMargin, yMargin, xMargin, xMargin));
    }

    /**
     * The screen's layers (glass at 0.5 mm, page or text at 1 mm off the
     * block face) z-fight from afar: depth precision falls with distance
     * squared, and shader packs (Iris) make it worse, so at certain angles the
     * page flickers through the block. Spread the layers out with distance:
     * 1x up close, ~8x (4 mm) at 40 blocks, capped at 40x (2 cm), which is
     * invisible from where it applies.
     */
    static float depthSpread(double blocks) {
        return (float) Math.max(1.0, Math.min(40.0, blocks * blocks * 0.0048));
    }

    /** An opaque, depth-writing, full-bright rectangle just in front of the block face. */
    private static void glass(PoseStack pose, SubmitNodeCollector collector, float x0, float y0, float x1, float y1,
                              int top, int bottom) {
        collector.submitCustomGeometry(pose, RenderTypes.textBackground(), (p, vc) -> {
            vc.addVertex(p, x0, y1, 0.0005f).setColor(bottom).setLight(FULL_BRIGHT);
            vc.addVertex(p, x1, y1, 0.0005f).setColor(bottom).setLight(FULL_BRIGHT);
            vc.addVertex(p, x1, y0, 0.0005f).setColor(top).setLight(FULL_BRIGHT);
            vc.addVertex(p, x0, y0, 0.0005f).setColor(top).setLight(FULL_BRIGHT);
        });
    }

    /**
     * A short, honest line about why there is no page, sized to the wall:
     * about 26 characters across, as large as fits, readable across a room.
     */
    private static void placeholder(PoseStack pose, SubmitNodeCollector collector, float w, float h, String why) {
        Font font = Minecraft.getInstance().font;
        int lineH = font.lineHeight + 1;
        float scale = Math.min(0.045F, w / (26 * 6.0F));
        List<FormattedCharSequence> lines = font.split(Component.literal(why), (int) (w * 0.88F / scale));
        float fit = h * 0.8F / (lineH * (lines.size() + 1) + 2);
        if (fit < scale) {
            scale = fit;
            lines = font.split(Component.literal(why), (int) (w * 0.88F / scale));
        }
        float blockH = lineH * (lines.size() + 1) * scale;
        pose.pushPose();
        pose.translate(w / 2.0F, -h / 2.0F + blockH / 2.0F, 0.002F);
        pose.scale(scale, -scale, scale);
        submitLine(pose, collector, font, Component.literal("Omo Monitor").getVisualOrderText(), 0, OmoStyle.GOLD);
        int y = lineH + 2;
        for (FormattedCharSequence line : lines) {
            submitLine(pose, collector, font, line, y, OmoStyle.CREAM);
            y += lineH;
        }
        pose.popPose();
    }

    private static void submitLine(PoseStack pose, SubmitNodeCollector collector, Font font,
                                   FormattedCharSequence line, int y, int colour) {
        collector.submitText(pose, -font.width(line) / 2.0F, y, line, false, Font.DisplayMode.POLYGON_OFFSET,
            FULL_BRIGHT, colour, 0, 0);
    }

    /** The page: RenderTypes.text is textured, full-bright, unshaded and writes depth. */
    private static void quad(PoseStack pose, SubmitNodeCollector collector, RenderType type, float w, float h) {
        int colour = 0xFFFFFFFF;
        collector.submitCustomGeometry(pose, type, (p, vc) -> {
            vc.addVertex(p, 0, -h, 0.001f).setColor(colour).setUv(0, 1).setLight(FULL_BRIGHT);
            vc.addVertex(p, w, -h, 0.001f).setColor(colour).setUv(1, 1).setLight(FULL_BRIGHT);
            vc.addVertex(p, w, 0, 0.001f).setColor(colour).setUv(1, 0).setLight(FULL_BRIGHT);
            vc.addVertex(p, 0, 0, 0.001f).setColor(colour).setUv(0, 0).setLight(FULL_BRIGHT);
        });
    }

    @Override
    public AABB getRenderBoundingBox(OmoMonitorBlockEntity be) {
        return be.getRenderBoundingBox();
    }

    @Override
    public boolean shouldRenderOffScreen() {
        return true;         // the origin panel draws the whole wall, even when it is itself out of view
    }

    @Override
    public int getViewDistance() {
        return VIEW_DISTANCE;
    }

    /** Same distance as {@link #getViewDistance}, measured to the middle of the whole wall. */
    @Override
    public boolean shouldRender(OmoMonitorBlockEntity be, Vec3 camera) {
        AABB box = be.getRenderBoundingBox();
        Vec3 centre = box == null ? Vec3.atCenterOf(be.getBlockPos()) : box.getCenter();
        return camera.distanceToSqr(centre) < (double) VIEW_DISTANCE * VIEW_DISTANCE;
    }
}
