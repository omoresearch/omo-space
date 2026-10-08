package space.omo.life.builder;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import java.util.zip.GZIPInputStream;
import java.util.zip.InflaterInputStream;

import net.minecraft.core.BlockPos;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.NbtAccounter;
import net.minecraft.nbt.NbtIo;
import net.minecraft.nbt.StringTag;
import net.minecraft.nbt.Tag;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.dimension.DimensionType;
import net.minecraft.world.level.storage.LevelResource;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.station.BridgeLink;

/**
 * Reads a saved world the way someone downloading it would, and lists what
 * belongs to this PC rather than to the map.
 *
 * <ul>
 *   <li>{@link Kind#STATION}: a Hermes Computer / Terminal still bound to a
 *       workspace here. Harmless on another PC (the id means nothing there),
 *       but it is this PC's bookkeeping; {@code /omo export-clean} turns it
 *       back into a preset.</li>
 *   <li>{@link Kind#ITEM}: a station item or pocket carrying a workspace.</li>
 *   <li>{@link Kind#TOKEN}: the bridge token, anywhere (a sign, a book, a
 *       Lua file). Never printed. Must never ship.</li>
 *   <li>{@link Kind#HOST_PATH}: text naming this PC's home folder.</li>
 *   <li>{@link Kind#KEYED_URL}: a web page address carrying a key or token.</li>
 * </ul>
 * Every file of the save is read: region and entity chunks, player data,
 * {@code level.dat}, saved structures, and computers' files. Runs off the
 * server thread on files the server has just flushed.
 */
public final class ExportCheck {

    public enum Kind { STATION, ITEM, TOKEN, HOST_PATH, KEYED_URL }

    /** One problem: where it is (file, and a block when known) and a short, safe description. */
    public record Finding(Kind kind, String file, ResourceKey<Level> dimension, BlockPos pos, String detail) {
    }

    private static final Pattern KEYED = Pattern.compile("(?i)https?://\\S*[?&](token|key|secret|auth|password|sig)=");
    private static final long TEXT_LIMIT = 2 << 20;

    private final Path root;
    private final Map<Path, ResourceKey<Level>> dimensions = new HashMap<>();
    private final String token = BridgeLink.token();
    private final String home = System.getProperty("user.home", "");
    private final List<Finding> found = new ArrayList<>();

    private ExportCheck(MinecraftServer server) {
        root = server.getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize();
        for (ServerLevel level : server.getAllLevels()) {
            dimensions.put(DimensionType.getStorageFolder(level.dimension(), root).toAbsolutePath().normalize(),
                level.dimension());
        }
    }

    /** Server thread: capture where each dimension lives. Then {@link #run} on any thread. */
    public static ExportCheck of(MinecraftServer server) {
        return new ExportCheck(server);
    }

    public List<Finding> run() {
        try (Stream<Path> files = Files.walk(root)) {
            files.filter(Files::isRegularFile).forEach(this::file);
        } catch (IOException | UncheckedIOException e) {
            OmoLife.LOG.warn("export check: could not walk {}: {}", root, e.toString());
        }
        return found;
    }

    private void file(Path path) {
        String name = path.getFileName().toString();
        String rel = root.relativize(path).toString();
        try {
            if (name.endsWith(".mca")) {
                ResourceKey<Level> dim = dimensions.get(path.getParent().getParent().toAbsolutePath().normalize());
                if (!"poi".equals(path.getParent().getFileName().toString())) {
                    region(path, rel, dim);
                }
            } else if (name.endsWith(".dat") || name.endsWith(".nbt") || name.endsWith(".dat_old")) {
                walk(NbtIo.readCompressed(path, NbtAccounter.unlimitedHeap()), rel, null, null);
            } else if (!name.endsWith(".png") && !name.equals("session.lock") && Files.size(path) < TEXT_LIMIT) {
                text(new String(Files.readAllBytes(path), StandardCharsets.UTF_8), rel, null, null);
            }
        } catch (IOException | RuntimeException e) {
            OmoLife.LOG.debug("export check: skipped {}: {}", rel, e.toString());
        }
    }

    /** Every chunk of an Anvil region file (gzip, zlib or uncompressed). */
    private void region(Path path, String rel, ResourceKey<Level> dim) throws IOException {
        try (RandomAccessFile raf = new RandomAccessFile(path.toFile(), "r")) {
            if (raf.length() < 8192) {
                return;
            }
            byte[] header = new byte[4096];
            raf.readFully(header);
            for (int i = 0; i < 1024; i++) {
                int sector = (header[i * 4] & 255) << 16 | (header[i * 4 + 1] & 255) << 8 | header[i * 4 + 2] & 255;
                if (sector == 0 || sector * 4096L + 5 > raf.length()) {
                    continue;
                }
                raf.seek(sector * 4096L);
                int length = raf.readInt();
                int type = raf.readByte();
                if (length <= 1 || length > 16 << 20 || (type & 128) != 0) {
                    continue;                          // empty, corrupt, or stored in an external .mcc file
                }
                byte[] data = new byte[length - 1];
                raf.readFully(data);
                InputStream in = new ByteArrayInputStream(data);
                in = switch (type) {
                    case 1 -> new GZIPInputStream(in);
                    case 2 -> new InflaterInputStream(in);
                    case 3 -> in;
                    default -> null;
                };
                if (in != null) {
                    walk(NbtIo.read(new DataInputStream(in), NbtAccounter.unlimitedHeap()), rel, dim, null);
                }
            }
        }
    }

    /** Depth-first through a tag, remembering the nearest block (block entity x/y/z or entity Pos). */
    private void walk(Tag tag, String rel, ResourceKey<Level> dim, BlockPos pos) {
        if (tag instanceof StringTag(String s)) {
            text(s, rel, dim, pos);
        } else if (tag instanceof ListTag list) {
            for (Tag t : list) {
                walk(t, rel, dim, pos);
            }
        } else if (tag instanceof CompoundTag c) {
            BlockPos here = where(c, pos);
            String ws = c.getStringOr(StationBlockEntity.NBT_WORKSPACE, "");
            if (!ws.isEmpty()) {
                String label = c.getStringOr(StationBlockEntity.NBT_WS_NAME, ws);
                boolean block = "omolife:station".equals(c.getStringOr("id", "")) && c.contains("x");
                found.add(new Finding(block ? Kind.STATION : Kind.ITEM, rel, dim, here, "'" + label + "'"));
            }
            for (Map.Entry<String, Tag> e : c.entrySet()) {
                walk(e.getValue(), rel, dim, here);
            }
        }
    }

    private static BlockPos where(CompoundTag c, BlockPos outer) {
        if (c.contains("x") && c.contains("y") && c.contains("z") && c.contains("id")) {
            return new BlockPos(c.getIntOr("x", 0), c.getIntOr("y", 0), c.getIntOr("z", 0));
        }
        ListTag p = c.getList("Pos").orElse(null);
        if (p != null && p.size() == 3) {
            return BlockPos.containing(p.getDoubleOr(0, 0), p.getDoubleOr(1, 0), p.getDoubleOr(2, 0));
        }
        return outer;
    }

    private void text(String s, String rel, ResourceKey<Level> dim, BlockPos pos) {
        if (token != null && token.length() >= 16 && s.contains(token)) {
            found.add(new Finding(Kind.TOKEN, rel, dim, pos, "the bridge token"));
        }
        if (home.length() > 1 && s.contains(home)) {
            found.add(new Finding(Kind.HOST_PATH, rel, dim, pos, home));
        }
        Matcher m = KEYED.matcher(s);
        if (m.find()) {
            found.add(new Finding(Kind.KEYED_URL, rel, dim, pos, "an address with '" + m.group(1) + "=' in it"));
        }
    }
}
