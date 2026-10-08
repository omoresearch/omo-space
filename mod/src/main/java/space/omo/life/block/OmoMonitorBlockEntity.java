package space.omo.life.block;

import java.net.URI;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponentGetter;
import net.minecraft.core.component.DataComponentMap;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.level.storage.ValueOutput;

import space.omo.life.link.LinkMirror;
import space.omo.life.link.Linkable;

/**
 * An Omo monitor: CC:T's advanced monitor plus one saved binding.
 *
 * <p>Source is either a terminal or a web page. A terminal wall is a normal
 * CC:T monitor an adjacent station draws on or, once the Omo Linker has
 * linked it to a station anywhere nearby, a mirror of that station's screen
 * ({@link LinkMirror}). A page binding is a URL and a zoom; the browser
 * profile, cookies and logins live on the host in MCEF's own directory and
 * never enter the world file. Every panel of a wall carries the same binding
 * (source, page, link); the wall's origin panel owns the page on the client
 * and the mirrored screen on the server.
 *
 * <p>Only an unlinked terminal wall is a CC:T peripheral (see
 * {@code OmoLife}): a station must never redirect its terminal onto glass
 * that Chromium covers or that mirrors another station. Neighbours are told
 * when that changes.
 *
 * <p>Persisted through {@code saveAdditional}/{@code loadAdditional}; the
 * client learns the binding through {@code MonitorUpdateTagMixin}; the item
 * carries a page as custom data, so a broken or picked monitor keeps its
 * page (a link stays with the wall: a moved screen is linked again).
 */
public class OmoMonitorBlockEntity extends MonitorBlockEntity implements Linkable {

    public static final String SOURCE_TERMINAL = "terminal";
    public static final String SOURCE_WEB = "web";

    public static final String NBT_SOURCE = "OmoSource";
    public static final String NBT_URL = "OmoUrl";
    public static final String NBT_ZOOM = "OmoZoom";
    private static final String ITEM_URL = "Url";
    private static final String ITEM_ZOOM = "Zoom";

    public static final int ZOOM_MIN = 25;
    public static final int ZOOM_MAX = 400;

    private String source = SOURCE_TERMINAL;
    private String url = "";
    private int zoom = 100;
    private BlockPos link;

    public OmoMonitorBlockEntity(BlockEntityType<? extends OmoMonitorBlockEntity> type, BlockPos pos,
                                 BlockState state) {
        super(type, pos, state, true);
    }

    public boolean isWeb() {
        return SOURCE_WEB.equals(source) && !url.isEmpty();
    }

    public String source() {
        return source;
    }

    public String url() {
        return url;
    }

    public int zoom() {
        return zoom;
    }

    /** A terminal wall that mirrors a linked station (and so is no peripheral). */
    public boolean isLinked() {
        return link != null && !isWeb();
    }

    /** Whether computers next to this wall may draw on it: an unlinked terminal wall. */
    public boolean isPeripheral() {
        return !isWeb() && link == null;
    }

    @Override
    public BlockPos linkedStation() {
        return isLinked() ? link : null;
    }

    /** Link (or unlink) the whole wall this panel belongs to; a page wall becomes a terminal wall. */
    @Override
    public void setLinkedStation(BlockPos station) {
        set(SOURCE_TERMINAL, "", zoom, station == null ? null : station.immutable());
        shareWithWall();
    }

    /** The top-left panel of this wall: the one that owns the page. */
    public BlockPos origin() {
        return getBlockPos().relative(getRight(), -getXIndex()).relative(getDown(), -getYIndex());
    }

    public boolean isOrigin() {
        return getXIndex() == 0 && getYIndex() == 0;
    }

    private static int clampZoom(int z) {
        return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, z <= 0 ? 100 : z));
    }

    /** Pin a page (it replaces a link). */
    public void setWeb(String newUrl, int newZoom) {
        set(SOURCE_WEB, newUrl == null ? "" : newUrl.trim(), clampZoom(newZoom), null);
    }

    /** Show a terminal: the linked station's, or else the adjacent one's. */
    public void setTerminal() {
        set(SOURCE_TERMINAL, "", zoom, link);
    }

    private void set(String s, String u, int z, BlockPos l) {
        if (s.equals(source) && u.equals(url) && z == zoom && Objects.equals(l, link)) {
            return;
        }
        boolean wasPeripheral = isPeripheral();
        boolean wasLinked = isLinked();
        source = s;
        url = u;
        zoom = z;
        link = l;
        setChanged();
        if (level instanceof ServerLevel server) {
            server.sendBlockUpdated(worldPosition, getBlockState(), getBlockState(), 3);
            if (wasPeripheral != isPeripheral()) {
                // The peripheral appears / disappears: adjacent computers re-query.
                server.updateNeighborsAt(worldPosition, getBlockState().getBlock());
            }
            if (isLinked()) {
                LinkMirror.track(this);
            } else if (wasLinked) {
                LinkMirror.release(this);
            }
        }
    }

    /** Copy the binding onto another panel of the same wall. */
    public void copyBindingTo(OmoMonitorBlockEntity other) {
        if (other != this) {
            other.set(source, url, zoom, link);
        }
    }

    /** Give every panel of this panel's wall the binding this one has. */
    public void shareWithWall() {
        if (level == null) {
            return;
        }
        BlockPos base = origin();
        for (int x = 0; x < getWidth(); x++) {
            for (int y = 0; y < getHeight(); y++) {
                if (level.getBlockEntity(base.relative(getRight(), x).relative(getDown(), y))
                    instanceof OmoMonitorBlockEntity other) {
                    copyBindingTo(other);
                }
            }
        }
    }

    /**
     * CC:T has just (re)formed the wall this panel is the origin of: give
     * every panel one binding. A page wins over the terminal (a plain monitor
     * placed next to a web wall joins the page), and the page most panels
     * already show wins when two web walls are joined; without a page, the
     * link most panels carry wins, so a linked wall that grows stays linked.
     */
    public void unifyWall() {
        if (level == null || level.isClientSide()) {
            return;
        }
        List<OmoMonitorBlockEntity> panels = new ArrayList<>();
        Map<String, Integer> votes = new HashMap<>();
        OmoMonitorBlockEntity best = null;
        int bestVotes = 0;
        for (int x = 0; x < getWidth(); x++) {
            for (int y = 0; y < getHeight(); y++) {
                BlockPos p = getBlockPos().relative(getRight(), x).relative(getDown(), y);
                if (level.getBlockEntity(p) instanceof OmoMonitorBlockEntity panel) {
                    panels.add(panel);
                    String key = panel.isWeb() ? "web " + panel.url + "@" + panel.zoom
                        : panel.link != null ? "link " + panel.link.toShortString() : null;
                    int v = key == null ? 0 : votes.merge(key, 1, Integer::sum) + (panel.isWeb() ? 10_000 : 0);
                    if (v > bestVotes) {
                        best = panel;
                        bestVotes = v;
                    }
                }
            }
        }
        if (best != null) {
            for (OmoMonitorBlockEntity panel : panels) {
                best.copyBindingTo(panel);
            }
        }
    }

    /** Append the binding to a tag (used for the client update tag). */
    public void writeBinding(CompoundTag tag) {
        tag.putString(NBT_SOURCE, source);
        tag.putString(NBT_URL, url);
        tag.putInt(NBT_ZOOM, zoom);
        if (link != null) {
            tag.put(Linkable.NBT, Linkable.toTag(this, link));
        }
    }

    @Override
    public void saveAdditional(ValueOutput out) {
        super.saveAdditional(out);
        out.putString(NBT_SOURCE, source);
        out.putString(NBT_URL, url);
        out.putInt(NBT_ZOOM, zoom);
        Linkable.save(out, this, link);
    }

    @Override
    public void loadAdditional(ValueInput in) {
        super.loadAdditional(in);
        source = in.getStringOr(NBT_SOURCE, source);
        url = in.getStringOr(NBT_URL, url);
        zoom = in.getIntOr(NBT_ZOOM, zoom);
        link = Linkable.load(in, this);
        if (isLinked() && level instanceof ServerLevel) {
            LinkMirror.track(this);                     // /clone and structure blocks load a placed panel
        }
    }

    // -- a linked wall is mirrored while it is loaded ---------------------------------------

    @Override
    public void clearRemoved() {
        super.clearRemoved();
        if (isLinked() && level instanceof ServerLevel) {
            LinkMirror.track(this);
        }
    }

    @Override
    public void setRemoved() {
        super.setRemoved();
        if (level instanceof ServerLevel) {
            LinkMirror.untrack(this);
        }
    }

    // -- item components: the page rides along with the item ---------------------

    @Override
    protected void collectImplicitComponents(DataComponentMap.Builder builder) {
        super.collectImplicitComponents(builder);
        if (isWeb()) {
            CompoundTag tag = new CompoundTag();
            tag.putString(ITEM_URL, url);
            tag.putInt(ITEM_ZOOM, zoom);
            builder.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
        }
    }

    @Override
    protected void applyImplicitComponents(DataComponentGetter components) {
        super.applyImplicitComponents(components);
        CustomData data = components.get(DataComponents.CUSTOM_DATA);
        if (data != null) {
            CompoundTag tag = data.copyTag();
            String u = safeUrl(tag.getStringOr(ITEM_URL, ""));
            if (u != null) {
                source = SOURCE_WEB;
                url = u;
                zoom = clampZoom(tag.getIntOr(ITEM_ZOOM, 100));
            }
        }
    }

    /**
     * Keep monitor pages to http(s). Cookies and profile data stay in the
     * host browser profile; the world only ever holds a URL. Bare hosts get
     * a scheme (loopback: http, anything else: https). Null = not allowed.
     */
    public static String safeUrl(String raw) {
        if (raw == null) {
            return null;
        }
        String v = raw.trim();
        if (v.isEmpty() || v.length() > 2000) {
            return null;
        }
        String lower = v.toLowerCase(Locale.ROOT);
        if (lower.startsWith("http://") || lower.startsWith("https://")) {
            return v;
        }
        if (lower.matches("^[a-z][a-z0-9+.-]*:(?!\\d).*")) {
            return null;                    // file:, javascript:, data:, chrome:, ...
        }
        return (v.matches("^(localhost|127\\.0\\.0\\.1|\\[::1\\])(:\\d+)?(/.*)?$") ? "http://" : "https://") + v;
    }

    /** A page's site, for labels: "example.com" for https://www.example.com/a. */
    public static String host(String url) {
        try {
            String h = URI.create(url).getHost();
            return h == null || h.isEmpty() ? url : h.replaceFirst("^www\\.", "");
        } catch (IllegalArgumentException e) {
            return url;
        }
    }
}
