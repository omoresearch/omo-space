package space.omo.life.station;

import java.util.ArrayList;
import java.util.List;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.Tag;

/**
 * What an authored world asks a station's first workspace to be (CONTRACT P).
 *
 * <pre>
 *   OmoPreset: {Name:"Website · Dev", Kind:"hermes"|"terminal", Profile:"web-dev",
 *               Tabs:[{Name:"Hermes",Kind:"hermes"},{Name:"Shell",Kind:"shell"}]}
 * </pre>
 * On a Hermes Computer / Terminal block entity, or in an Omo Pocket's custom
 * data. Profile and Tabs are optional; Tabs, when given, are the workspace's
 * tabs in order. Never anything secret.
 *
 * <p>A pocket uses it once, when it creates its workspace. A block keeps it
 * as its recipe: the Station Setup screen edits it, and when a map is opened
 * on another PC (where the station's workspace does not exist) the station
 * makes a fresh workspace from it.
 */
public record StationPreset(String name, boolean hermes, String profile, List<Tab> tabs) {

    public static final String NBT = "OmoPreset";

    public record Tab(String name, boolean hermes) {
    }

    /** The preset in {@code tag}, or null when there is none. */
    public static StationPreset from(CompoundTag tag, boolean defaultHermes) {
        if (tag == null || tag.isEmpty()) {
            return null;
        }
        List<Tab> tabs = new ArrayList<>();
        for (Tag t : tag.getListOrEmpty("Tabs")) {
            if (t instanceof CompoundTag c) {
                tabs.add(new Tab(c.getStringOr("Name", ""), "hermes".equals(c.getStringOr("Kind", "shell"))));
            }
        }
        String kind = tag.getStringOr("Kind", defaultHermes ? "hermes" : "terminal");
        return new StationPreset(tag.getStringOr("Name", ""), "hermes".equals(kind), tag.getStringOr("Profile", ""),
            List.copyOf(tabs));
    }

    /** Back to the saved form. */
    public CompoundTag toTag() {
        CompoundTag tag = new CompoundTag();
        tag.putString("Name", name);
        tag.putString("Kind", hermes ? "hermes" : "terminal");
        if (!profile.isEmpty()) {
            tag.putString("Profile", profile);
        }
        if (!tabs.isEmpty()) {
            ListTag list = new ListTag();
            for (Tab t : tabs) {
                CompoundTag c = new CompoundTag();
                c.putString("Name", t.name());
                c.putString("Kind", t.hermes() ? "hermes" : "shell");
                list.add(c);
            }
            tag.put("Tabs", list);
        }
        return tag;
    }

    /** The same preset on the default Hermes profile. */
    public StationPreset withoutProfile() {
        return new StationPreset(name, hermes, "", tabs);
    }

    /** The bridge's {@code tabs} parameter: a JSON list of {name, kind}; "" for none. */
    String tabsParam() {
        if (tabs.isEmpty()) {
            return "";
        }
        JsonArray out = new JsonArray();
        for (Tab t : tabs) {
            JsonObject o = new JsonObject();
            o.addProperty("name", t.name());
            o.addProperty("kind", t.hermes() ? "hermes" : "shell");
            out.add(o);
        }
        return out.toString();
    }
}
