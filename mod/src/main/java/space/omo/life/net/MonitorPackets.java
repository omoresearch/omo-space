package space.omo.life.net;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;

import space.omo.life.OmoLife;
import space.omo.life.remote.RemoteWalls;

/**
 * Packets for using a monitor wall from where you stand: take control of it
 * (keyboard and crosshair go to the wall), let go. The server decides who
 * may control what ({@link RemoteWalls}); every serverbound packet names the
 * wall it is for, and is dropped unless that player controls that wall.
 */
public final class MonitorPackets {

    private MonitorPackets() {
    }

    /** Client to server: right-click on a wall panel beyond vanilla reach ({@code settings}: Shift held). */
    public record UseWall(BlockPos pos, boolean settings) implements CustomPacketPayload {
        public static final Type<UseWall> TYPE = new Type<>(OmoLife.id("use_wall"));
        public static final StreamCodec<RegistryFriendlyByteBuf, UseWall> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, UseWall::pos,
            ByteBufCodecs.BOOL, UseWall::settings,
            UseWall::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /**
     * Server to client: the player now controls the wall at {@code origin}
     * ({@code kind} "terminal" or "web"), or no longer does ({@code kind} "").
     * {@code label} names what they type into: a workspace, a site.
     */
    public record WallControl(BlockPos origin, String kind, String label, boolean hermes)
        implements CustomPacketPayload {
        public static final String TERMINAL = "terminal";
        public static final String WEB = "web";
        public static final Type<WallControl> TYPE = new Type<>(OmoLife.id("wall_control"));
        public static final StreamCodec<RegistryFriendlyByteBuf, WallControl> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, WallControl::origin,
            ByteBufCodecs.STRING_UTF8, WallControl::kind,
            ByteBufCodecs.STRING_UTF8, WallControl::label,
            ByteBufCodecs.BOOL, WallControl::hermes,
            WallControl::new);

        public static WallControl released(BlockPos origin) {
            return new WallControl(origin, "", "", false);
        }

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: a key went down (or repeated) or up while controlling a terminal wall. */
    public record WallKey(BlockPos origin, int key, boolean down, boolean repeat) implements CustomPacketPayload {
        public static final Type<WallKey> TYPE = new Type<>(OmoLife.id("wall_key"));
        public static final StreamCodec<RegistryFriendlyByteBuf, WallKey> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, WallKey::origin,
            ByteBufCodecs.VAR_INT, WallKey::key,
            ByteBufCodecs.BOOL, WallKey::down,
            ByteBufCodecs.BOOL, WallKey::repeat,
            WallKey::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: one typed character or a paste, already in CC's charset. */
    public record WallText(BlockPos origin, boolean paste, byte[] text) implements CustomPacketPayload {
        public static final Type<WallText> TYPE = new Type<>(OmoLife.id("wall_text"));
        public static final StreamCodec<RegistryFriendlyByteBuf, WallText> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, WallText::origin,
            ByteBufCodecs.BOOL, WallText::paste,
            ByteBufCodecs.BYTE_ARRAY, WallText::text,
            WallText::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /**
     * Client to server: the crosshair pointer on a terminal wall. {@code u, v}
     * run 0..1 across the wall's glass; the server turns them into cells at
     * the wall's current text scale.
     */
    public record WallMouse(BlockPos origin, int kind, int button, float u, float v) implements CustomPacketPayload {
        public static final int CLICK = 0;
        public static final int DRAG = 1;
        public static final int UP = 2;
        public static final int SCROLL = 3;
        public static final Type<WallMouse> TYPE = new Type<>(OmoLife.id("wall_mouse"));
        public static final StreamCodec<RegistryFriendlyByteBuf, WallMouse> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, WallMouse::origin,
            ByteBufCodecs.VAR_INT, WallMouse::kind,
            ByteBufCodecs.VAR_INT, WallMouse::button,
            ByteBufCodecs.FLOAT, WallMouse::u,
            ByteBufCodecs.FLOAT, WallMouse::v,
            WallMouse::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: the player let go of the wall (Esc, walked away, opened a screen). */
    public record WallRelease(BlockPos origin) implements CustomPacketPayload {
        public static final Type<WallRelease> TYPE = new Type<>(OmoLife.id("wall_release"));
        public static final StreamCodec<RegistryFriendlyByteBuf, WallRelease> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, WallRelease::origin,
            WallRelease::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    public static void register() {
        PayloadTypeRegistry.clientboundPlay().register(WallControl.TYPE, WallControl.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(UseWall.TYPE, UseWall.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(WallKey.TYPE, WallKey.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(WallText.TYPE, WallText.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(WallMouse.TYPE, WallMouse.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(WallRelease.TYPE, WallRelease.CODEC);

        ServerPlayNetworking.registerGlobalReceiver(UseWall.TYPE, (p, ctx) ->
            RemoteWalls.useFromAfar(ctx.player(), p.pos(), p.settings()));
        ServerPlayNetworking.registerGlobalReceiver(WallKey.TYPE, (p, ctx) ->
            RemoteWalls.key(ctx.player(), p.origin(), p.key(), p.down(), p.repeat()));
        ServerPlayNetworking.registerGlobalReceiver(WallText.TYPE, (p, ctx) ->
            RemoteWalls.text(ctx.player(), p.origin(), p.paste(), p.text()));
        ServerPlayNetworking.registerGlobalReceiver(WallMouse.TYPE, (p, ctx) ->
            RemoteWalls.mouse(ctx.player(), p.origin(), p.kind(), p.button(), p.u(), p.v()));
        ServerPlayNetworking.registerGlobalReceiver(WallRelease.TYPE, (p, ctx) ->
            RemoteWalls.release(ctx.player(), p.origin()));
        ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> RemoteWalls.forget(handler.player));
    }
}
