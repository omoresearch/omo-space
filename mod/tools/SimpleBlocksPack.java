import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/**
 * Generates the disabling half of the built-in "simple_blocks" data pack:
 * every crafting recipe and recipe advancement of CC: Tweaked (read from the
 * CC:T jar the mod is built against, so a new CC:T version is covered on the
 * next build), plus the recipes and advancements of the Omo blocks that are
 * hidden by default, each replaced by a file whose Fabric load condition is
 * always false. Nothing is unregistered: placed blocks keep working and
 * /give still works.
 *
 *   java mod/tools/SimpleBlocksPack.java <cc-tweaked.jar> <omolife data dir> <pack dir>
 *
 * The hand-written half of the pack (pack.mcmeta, the CC-free Omo Computer
 * recipe and its unlock) lives in src/main/resources/resourcepacks/simple_blocks
 * and is never overwritten here.
 */
public class SimpleBlocksPack {

    /** Omo blocks and items players still see. Everything else of omolife is hidden by default. */
    static final Set<String> OMO_VISIBLE = Set.of("hermes_computer", "monitor");
    /** Omo advancements that need a hidden item; leaves only, so no child loses its parent. */
    static final Set<String> OMO_HIDDEN_ADVANCEMENTS = Set.of("get_pocket", "link_screen");

    static final String DISABLED = "{\n  \"fabric:load_conditions\": [\n"
        + "    {\"condition\": \"fabric:not\", \"value\": {\"condition\": \"fabric:true\"}}\n  ]\n}\n";

    public static void main(String[] args) throws IOException {
        if (args.length != 3) {
            System.err.println("usage: SimpleBlocksPack <cc-tweaked.jar> <omolife data dir> <pack dir>");
            System.exit(2);
        }
        Path pack = Path.of(args[2]);
        List<String> files = new ArrayList<>();
        files.addAll(fromCcJar(Path.of(args[0])));
        files.addAll(fromOmo(Path.of(args[1])));
        int written = 0;
        for (String rel : files) {
            Path out = pack.resolve(rel);
            if (Files.exists(out)) {
                continue;                       // a hand-written override wins (the CC-free Omo Computer recipe)
            }
            Files.createDirectories(out.getParent());
            Files.writeString(out, DISABLED, StandardCharsets.UTF_8);
            written++;
        }
        if (files.stream().noneMatch(f -> f.startsWith("data/computercraft/recipe/"))) {
            System.err.println("SimpleBlocksPack: no CC: Tweaked recipes found in " + args[0]);
            System.exit(1);
        }
        System.out.println("simple_blocks pack: " + written + " recipes/advancements disabled");
    }

    /** data/computercraft/{recipe,advancement}/**.json in the CC:T jar. */
    static List<String> fromCcJar(Path jar) throws IOException {
        Set<String> out = new TreeSet<>();
        try (ZipFile zip = new ZipFile(jar.toFile())) {
            for (Enumeration<? extends ZipEntry> e = zip.entries(); e.hasMoreElements(); ) {
                String name = e.nextElement().getName();
                if (name.endsWith(".json") && (name.startsWith("data/computercraft/recipe/")
                        || name.startsWith("data/computercraft/advancement/"))) {
                    out.add(name);
                }
            }
        }
        return new ArrayList<>(out);
    }

    /** Recipes, recipe unlocks and advancements of the hidden Omo items. */
    static List<String> fromOmo(Path data) throws IOException {
        Set<String> out = new TreeSet<>();
        Path omo = data.resolve("omolife");
        try (Stream<Path> recipes = Files.list(omo.resolve("recipe"))) {
            recipes.map(p -> stem(p)).filter(n -> !OMO_VISIBLE.contains(n))
                .forEach(n -> out.add("data/omolife/recipe/" + n + ".json"));
        }
        Path unlocks = omo.resolve("advancement/recipes");
        if (Files.isDirectory(unlocks)) {
            try (Stream<Path> walk = Files.walk(unlocks)) {
                walk.filter(p -> p.toString().endsWith(".json") && !OMO_VISIBLE.contains(stem(p)))
                    .forEach(p -> out.add("data/omolife/advancement/" + omo.resolve("advancement").relativize(p)
                        .toString().replace('\\', '/')));
            }
        }
        for (String a : OMO_HIDDEN_ADVANCEMENTS) {
            if (Files.exists(omo.resolve("advancement/" + a + ".json"))) {
                out.add("data/omolife/advancement/" + a + ".json");
            }
        }
        return new ArrayList<>(out);
    }

    private static String stem(Path p) {
        String n = p.getFileName().toString();
        return n.endsWith(".json") ? n.substring(0, n.length() - 5) : n;
    }
}
