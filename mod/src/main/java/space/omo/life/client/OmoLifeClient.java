package space.omo.life.client;

import java.util.List;
import java.util.Set;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.fabricmc.fabric.api.client.rendering.v1.BlockColorRegistry;
import net.fabricmc.fabric.api.client.rendering.v1.BlockEntityRendererRegistry;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.world.InteractionResult;
import net.minecraft.client.color.block.BlockTintSource;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.SlimScreenBlock;
import space.omo.life.client.remote.RemoteWall;
import space.omo.life.net.OmoNetworking;
import space.omo.life.net.StationPackets;
import space.omo.life.world.WorldTools;

/**
 * Client wiring: monitor and name plate renderers, Slim Screen frame tints,
 * screen packets, page lifecycle.
 */
public final class OmoLifeClient implements ClientModInitializer {

    private int tickCounter;

    @Override
    @SuppressWarnings("deprecation") // Fabric's renderer registry: vanilla's register() is private without an access widener
    public void onInitializeClient() {
        AgentLan.register(); // local AI agents can join this single-player world as players
        WorldTools.register(); // World Sense write tools (off unless -Domo.world.tools=true)
        BlockEntityRendererRegistry.register(OmoLife.MONITOR_BE, WebMonitorRenderer::new);
        BlockEntityRendererRegistry.register(OmoLife.STATUS_BE, NamePlateRenderer::new);
        BlockColorRegistry.register(List.of(new BlockTintSource() {       // a Slim Screen's frame finish
            @Override
            public int color(BlockState state) {
                return 0xFF000000 | state.getValue(SlimScreenBlock.FINISH).tint();
            }

            @Override
            public Set<Property<?>> relevantProperties() {
                return Set.of(SlimScreenBlock.FINISH);
            }
        }), OmoLife.SLIM_SCREEN);

        ClientPlayNetworking.registerGlobalReceiver(OmoNetworking.OpenMonitor.TYPE, (p, ctx) ->
            ctx.client().setScreenAndShow(new MonitorScreen(p.origin(), p.source(), p.url(), p.zoom(), p.cols(),
                p.rows())));
        ClientPlayNetworking.registerGlobalReceiver(StationPackets.Info.TYPE, (p, ctx) -> StationScreens.onInfo(p));

        // WebPages.tick closes a page within seconds of its wall going away
        // (broken, chunk unloaded, rebound), unless the re-formed wall claims
        // it first; leaving the world closes them all. No orphan Chromium pages.
        ClientPlayConnectionEvents.DISCONNECT.register((handler, client) -> {
            WebPages.closeAll();
            WebPages.forgetAllowedWalls();
            BridgeClient.get().close();
        });
        ClientLifecycleEvents.CLIENT_STOPPING.register(client -> {
            WebPages.shutdown();
            BridgeClient.get().close();
        });
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            if (++tickCounter % 20 == 0) {
                WebPages.tick();
            }
            if (tickCounter % 4 == 0) {
                WebPages.audioTick();
            }
            HermesNotices.tick(client);
        });
        // On a server this player does not host, a web wall loads only after a
        // right-click on it (see WebPages.allowWall). PASS: the click goes on as usual.
        UseBlockCallback.EVENT.register((player, level, hand, hit) -> {
            if (level.isClientSide() && level.getBlockEntity(hit.getBlockPos()) instanceof OmoMonitorBlockEntity be
                && be.isWeb()) {
                WebPages.allowWall(be.origin());
            }
            return InteractionResult.PASS;
        });
        HermesNotices.init();  // Hermes notify events as HUD cards during play
        RemoteWall.init();
        LinkerClient.init();
        startTestDriver();
        OmoLife.LOG.info("Omo Space client ready{}", WebPages.available() ? "" : " (no MCEF: web pages off)");
    }

    /**
     * The acceptance-test driver only exists in test builds
     * ({@code OMO_TEST=1 mod/build.sh}) and only starts with
     * {@code -Domo.test.port=N}.
     */
    private static void startTestDriver() {
        if (System.getProperty("omo.test.port") == null) {
            return;
        }
        try {
            Class.forName("space.omo.life.client.TestDriver").getMethod("start").invoke(null);
        } catch (ClassNotFoundException e) {
            OmoLife.LOG.warn("omo.test.port is set but this is a release build without the test driver");
        } catch (ReflectiveOperationException e) {
            OmoLife.LOG.error("test driver failed to start", e);
        }
    }
}
