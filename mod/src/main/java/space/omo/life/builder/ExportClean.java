package space.omo.life.builder;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.decoration.ItemFrame;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.phys.AABB;

import space.omo.life.block.StationBlockEntity;
import space.omo.life.builder.ExportCheck.Finding;
import space.omo.life.builder.ExportCheck.Kind;
import space.omo.life.station.StationPreset;
import space.omo.life.station.Stations;

/**
 * Makes a world safe to share: every station bound to a workspace on this PC
 * becomes an unbound station with a preset of the same name, kind, Hermes
 * profile and tabs (CONTRACT P), so each player's copy makes its own
 * workspace; workspace bindings come off station items and pockets in
 * containers, item frames, dropped items and online players' inventories.
 * The workspaces themselves keep running on this PC. Tokens and host paths
 * written into signs, books or Lua files are left for the builder to edit.
 */
final class ExportClean {

    record Result(int stations, int items) {
    }

    private ExportClean() {
    }

    /** Server thread. {@code done} runs on the server thread once every station is reset. */
    static void run(MinecraftServer server, List<Finding> findings, Consumer<Result> done) {
        Map<StationBlockEntity, CompletableFuture<JsonObject>> stations = new LinkedHashMap<>();
        int items = 0;
        for (Finding f : findings) {
            ServerLevel level = f.dimension() == null || f.pos() == null ? null : server.getLevel(f.dimension());
            if (level == null) {
                continue;
            }
            level.getChunkAt(f.pos());                           // loads it if nobody is near
            if (f.kind() == Kind.STATION && level.getBlockEntity(f.pos()) instanceof StationBlockEntity be
                && be.isBound() && !stations.containsKey(be)) {
                stations.put(be, Stations.getWorkspace(be.workspace()));
            } else if (f.kind() == Kind.ITEM) {
                items += stripAround(level, f);
            }
        }
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            items += strip(p.getInventory()) + strip(p.getEnderChestInventory());
        }
        int cleanedItems = items;
        List<CompletableFuture<JsonObject>> replies = new ArrayList<>(stations.values());
        Stations.onServer(server, CompletableFuture.allOf(replies.toArray(CompletableFuture[]::new)), v -> {
            stations.forEach((be, reply) -> be.resetToPreset(presetFor(be, reply.join())));
            done.accept(new Result(stations.size(), cleanedItems));
        });
    }

    /** The preset that makes the same workspace again: its name, kind, profile and tabs. */
    static CompoundTag presetFor(StationBlockEntity be, JsonObject ws) {
        if (be.preset() != null) {
            return be.recipe().toTag();                  // set up in game (Station Setup): keep its tabs and profile
        }
        boolean known = ws != null && !ws.has("error");
        CompoundTag p = new CompoundTag();
        p.putString("Name", known ? ws.get("name").getAsString() : be.workspaceName());
        p.putString("Kind", known ? ws.get("kind").getAsString() : be.isHermes() ? "hermes" : "terminal");
        String profile = known && ws.has("profile") ? ws.get("profile").getAsString() : "";
        if (!profile.isEmpty() && !"default".equals(profile)) {
            p.putString("Profile", profile);
        }
        if (known) {
            ListTag tabs = new ListTag();
            for (JsonElement el : ws.getAsJsonArray("tabs")) {
                CompoundTag t = new CompoundTag();
                t.putString("Name", el.getAsJsonObject().get("name").getAsString());
                t.putString("Kind", el.getAsJsonObject().get("kind").getAsString());
                tabs.add(t);
            }
            p.put("Tabs", tabs);
        }
        return p;
    }

    /** Containers at the finding's block, and item frames / dropped items / container entities by it. */
    private static int stripAround(ServerLevel level, Finding f) {
        int n = level.getBlockEntity(f.pos()) instanceof Container c ? strip(c) : 0;
        for (Entity e : level.getEntities((Entity) null, new AABB(f.pos()).inflate(1.5))) {
            if (e instanceof ItemFrame frame) {
                ItemStack s = frame.getItem().copy();
                if (strip(s)) {
                    frame.setItem(s);
                    n++;
                }
            } else if (e instanceof ItemEntity drop) {
                ItemStack s = drop.getItem().copy();
                if (strip(s)) {
                    drop.setItem(s);
                    n++;
                }
            } else if (e instanceof Container c) {
                n += strip(c);
            }
        }
        return n;
    }

    private static int strip(Container c) {
        int n = 0;
        for (int i = 0; i < c.getContainerSize(); i++) {
            if (strip(c.getItem(i))) {
                c.setChanged();
                n++;
            }
        }
        return n;
    }

    /**
     * Take the workspace binding off one stack (in place). A named pocket
     * keeps its name as a preset, so the copy's first workspace is called the same.
     */
    static boolean strip(ItemStack stack) {
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        CompoundTag tag = data == null ? null : data.copyTag();
        if (tag == null || tag.getStringOr(StationBlockEntity.NBT_WORKSPACE, "").isEmpty()) {
            return false;
        }
        String name = tag.getStringOr(StationBlockEntity.NBT_WS_NAME, "");
        tag.remove(StationBlockEntity.NBT_WORKSPACE);
        tag.remove(StationBlockEntity.NBT_WS_NAME);
        tag.remove(StationBlockEntity.NBT_STATION);
        if (!name.isEmpty() && !tag.contains(StationPreset.NBT) && tag.contains(StationBlockEntity.NBT_HERMES)) {
            CompoundTag preset = new CompoundTag();
            preset.putString("Name", name);
            tag.put(StationPreset.NBT, preset);
        }
        CustomData.set(DataComponents.CUSTOM_DATA, stack, tag);
        return true;
    }
}
