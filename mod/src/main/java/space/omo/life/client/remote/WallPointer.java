package space.omo.life.client.remote;

import org.joml.Matrix4f;
import org.joml.Vector3f;

import com.mojang.math.Axis;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;
import dan200.computercraft.shared.util.DirectionUtil;

import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import space.omo.life.block.OmoMonitorBlock;
import space.omo.life.block.OmoMonitorBlockEntity;

/**
 * Where the crosshair is on a monitor wall's glass.
 *
 * <p>Uses the same transform the renderers use (CC:T's terminal and the web
 * page), so {@code u, v} (0..1 across the glass, top-left origin) is exactly
 * the point drawn under the crosshair, whatever way the wall faces.
 */
public final class WallPointer {

    private static final float BORDER = (float) (MonitorBlockEntity.RENDER_BORDER + MonitorBlockEntity.RENDER_MARGIN);

    /**
     * A point on a wall: its origin panel, the panel under the crosshair (null
     * for a bare projection), glass coordinates, distance from the eye.
     */
    public record Hit(OmoMonitorBlockEntity wall, BlockPos panel, float u, float v, double distance) {
        public boolean onGlass() {
            return u >= 0 && u <= 1 && v >= 0 && v <= 1;
        }
    }

    private WallPointer() {
    }

    /** The wall under the crosshair within {@code range}, if nothing else is in the way. */
    public static Hit aim(Minecraft mc, double range) {
        Player player = mc.player;
        if (player == null || mc.level == null) {
            return null;
        }
        Vec3 eye = player.getEyePosition(1.0F);
        Vec3 look = player.getViewVector(1.0F);
        BlockHitResult hit = mc.level.clip(new ClipContext(eye, eye.add(look.scale(range)),
            ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, player));
        if (hit.getType() != HitResult.Type.BLOCK
            || !(mc.level.getBlockEntity(hit.getBlockPos()) instanceof OmoMonitorBlockEntity panel)
            || panel.getFront() != hit.getDirection()
            || !(mc.level.getBlockEntity(panel.origin()) instanceof OmoMonitorBlockEntity wall)) {
            return null;
        }
        Hit h = project(wall, eye, look);
        return h != null && h.onGlass() ? new Hit(wall, panel.getBlockPos(), h.u(), h.v(), h.distance()) : null;
    }

    /** The crosshair ray against the plane of {@code wall}'s glass; u, v may fall outside 0..1. */
    public static Hit project(OmoMonitorBlockEntity wall, Vec3 eye, Vec3 look) {
        float w = wall.getWidth() - 2 * BORDER;
        float h = wall.getHeight() - 2 * BORDER;
        BlockPos o = wall.getBlockPos();
        Matrix4f toWall = new Matrix4f()
            .translate(0.5F, 0.5F, 0.5F)
            .rotate(Axis.YN.rotationDegrees(wall.getDirection().toYRot()))
            .rotate(Axis.XP.rotationDegrees(DirectionUtil.toPitchAngle(wall.getFront())))
            .translate(-0.5F + BORDER, wall.getHeight() - 0.5F - BORDER,
                0.5F - (float) OmoMonitorBlock.glassInset(wall.getBlockState()))
            .invert();
        Vector3f p = toWall.transformPosition(new Vector3f(
            (float) (eye.x - o.getX()), (float) (eye.y - o.getY()), (float) (eye.z - o.getZ())));
        Vector3f d = toWall.transformDirection(new Vector3f((float) look.x, (float) look.y, (float) look.z));
        if (d.z >= -1e-4F || p.z <= 0) {
            return null;                                   // looking away from the glass, or behind it
        }
        float t = -p.z / d.z;
        return new Hit(wall, null, (p.x + t * d.x) / w, -(p.y + t * d.y) / h, t);
    }
}
