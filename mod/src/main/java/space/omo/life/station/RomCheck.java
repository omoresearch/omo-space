package space.omo.life.station;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.packs.resources.Resource;

import space.omo.life.OmoLife;

/**
 * Refuses station tickets while the CraftOS that computers boot is not the
 * installed mods' own (see {@link RomCompare} for why and the rule).
 *
 * <p>Checked when the server starts and after every data pack reload, from
 * the same resource manager CC:T builds {@code bios.lua} and {@code /rom}
 * from. Until the first check has run, tickets wait.
 */
public final class RomCheck {

    private static final String WAITING = "the server is still starting";
    private static volatile String problem = WAITING;

    private RomCheck() {
    }

    /** Null when station computers boot the mods' own ROM; otherwise why tickets are refused. */
    public static String problem() {
        return problem;
    }

    public static void register() {
        ServerLifecycleEvents.SERVER_STARTED.register(RomCheck::check);
        ServerLifecycleEvents.END_DATA_PACK_RELOAD.register((server, resources, ok) -> check(server));
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> problem = WAITING);
    }

    static void check(MinecraftServer server) {
        Map<String, byte[]> effective = new HashMap<>();
        List<String> unreadable = new ArrayList<>();
        Map<Identifier, Resource> found = server.getResourceManager()
            .listResources("lua", id -> "computercraft".equals(id.getNamespace()));
        Map<String, String> packs = new HashMap<>();
        for (Map.Entry<Identifier, Resource> e : found.entrySet()) {
            String path = e.getKey().getPath();
            if (!RomCompare.governed(path)) {
                continue;
            }
            try (InputStream in = e.getValue().open()) {
                effective.put(path, in.readAllBytes());
                packs.put(path, e.getValue().sourcePackId());
            } catch (IOException ex) {
                unreadable.add(path);
            }
        }
        List<String> bad = new ArrayList<>(RomCompare.problems(effective, RomCheck::shipped));
        bad.addAll(unreadable);
        if (bad.isEmpty()) {
            problem = null;
            OmoLife.LOG.info("Omo stations: computer ROM verified ({} files)", effective.size());
            return;
        }
        problem = "this world changes the computer ROM; Omo stations are off";
        List<String> shown = new ArrayList<>();
        for (String b : bad.subList(0, Math.min(8, bad.size()))) {
            shown.add(b + (packs.containsKey(b) ? " [" + packs.get(b) + "]" : ""));
        }
        OmoLife.LOG.warn("Omo stations will not connect: a data pack replaces or adds CraftOS files that station "
            + "computers boot ({} file(s): {}). Remove that data pack from the world to use stations.",
            bad.size(), String.join(", ", shown));
    }

    /** What each installed mod ships at {@code data/computercraft/<path>}. */
    private static List<byte[]> shipped(String path) {
        List<byte[]> out = new ArrayList<>();
        for (ModContainer mod : FabricLoader.getInstance().getAllMods()) {
            Optional<Path> p = mod.findPath("data/computercraft/" + path);
            if (p.isPresent()) {
                try {
                    out.add(Files.readAllBytes(p.get()));
                } catch (IOException | RuntimeException ignored) {
                    // unreadable here: not a match
                }
            }
        }
        return out;
    }
}
