package space.omo.life.builder;

import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.stream.Collectors;

import com.google.gson.JsonElement;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.builder.LiteralArgumentBuilder;
import com.mojang.brigadier.context.CommandContext;

import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.SharedSuggestionProvider;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.builder.ExportCheck.Finding;
import space.omo.life.builder.ExportCheck.Kind;
import space.omo.life.station.StationSignals;
import space.omo.life.station.Stations;

/**
 * {@code /omo} for builders, next to the workspace commands in
 * {@link space.omo.life.station.StationCommands}:
 *
 * <pre>
 *   /omo kit &lt;kit&gt;                               place a room kit where you look, facing you
 *   /omo preset &lt;name&gt; [hermes|terminal] [profile]  the looked-at station makes this workspace when first opened
 *   /omo signal [on|off]                         its redstone reading; a plain signal while working
 *   /omo export-check                            what in this world belongs to this PC, not the map
 *   /omo export-clean                            reset workspace bindings to presets, for sharing
 * </pre>
 * Same access rule as the rest of {@code /omo}: the world owner or an operator.
 */
public final class BuilderCommands {

    private static final AtomicBoolean EXPORTING = new AtomicBoolean();

    private BuilderCommands() {
    }

    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, ctx, env) -> dispatcher.register(
            Commands.literal("omo")
                .requires(BuilderCommands::allowed)
                .then(Commands.literal("kit").then(Commands.argument("kit", StringArgumentType.word())
                    .suggests((c, b) -> SharedSuggestionProvider.suggest(Kits.NAMES, b))
                    .executes(c -> kit(c.getSource(), StringArgumentType.getString(c, "kit")))))
                .then(preset())
                .then(Commands.literal("signal")
                    .executes(c -> signal(c.getSource(), null))
                    .then(Commands.literal("on").executes(c -> signal(c.getSource(), true)))
                    .then(Commands.literal("off").executes(c -> signal(c.getSource(), false))))
                .then(Commands.literal("export-check").executes(c -> export(c.getSource(), false)))
                .then(Commands.literal("export-clean").executes(c -> export(c.getSource(), true)))));
    }

    private static boolean allowed(CommandSourceStack src) {
        ServerPlayer p = src.getPlayer();
        return !src.isPlayer() || p != null && Stations.allowed(p);
    }

    private static LiteralArgumentBuilder<CommandSourceStack> preset() {
        return Commands.literal("preset").then(Commands.argument("name", StringArgumentType.string())
            .executes(c -> preset(c, null, ""))
            .then(Commands.argument("kind", StringArgumentType.word())
                .suggests((c, b) -> SharedSuggestionProvider.suggest(List.of("hermes", "terminal"), b))
                .executes(c -> preset(c, StringArgumentType.getString(c, "kind"), ""))
                .then(Commands.argument("profile", StringArgumentType.word())
                    .suggests((c, b) -> Stations.listProfiles().thenCompose(r -> SharedSuggestionProvider.suggest(
                        r.has("items") ? r.getAsJsonArray("items").asList().stream().map(JsonElement::getAsString)
                            .toList() : List.<String>of(), b)))
                    .executes(c -> preset(c, StringArgumentType.getString(c, "kind"),
                        StringArgumentType.getString(c, "profile"))))));
    }

    /** The Hermes Computer or Terminal block the player looks at. */
    private static StationBlockEntity lookedAt(CommandSourceStack src) {
        ServerPlayer p = src.getPlayer();
        HitResult hit = p == null ? null : p.pick(6.0, 0.0F, false);
        if (hit instanceof BlockHitResult bh && p.level().getBlockEntity(bh.getBlockPos()) instanceof StationBlockEntity be) {
            return be;
        }
        src.sendFailure(Component.literal("Look at a Hermes Computer or Terminal first."));
        return null;
    }

    // -- kits -------------------------------------------------------------------

    private static int kit(CommandSourceStack src, String name) {
        ServerPlayer p = src.getPlayer();
        if (p == null) {
            src.sendFailure(Component.literal("Kits are placed where a player looks; from a command block use "
                + "/place template omolife:" + name));
            return 0;
        }
        String why = Kits.place(p, name);
        if (why != null) {
            src.sendFailure(Component.literal(why));
            return 0;
        }
        src.sendSuccess(() -> Component.literal("Placed kit '" + name + "'. Its stations make their workspaces "
            + "when first opened.").withStyle(ChatFormatting.GOLD), false);
        return 1;
    }

    // -- presets ----------------------------------------------------------------

    private static int preset(CommandContext<CommandSourceStack> c, String kind, String profile) {
        CommandSourceStack src = c.getSource();
        String name = StringArgumentType.getString(c, "name").trim();
        if (kind != null && !kind.equals("hermes") && !kind.equals("terminal")) {
            src.sendFailure(Component.literal("Kind is 'hermes' or 'terminal'."));
            return 0;
        }
        StationBlockEntity be = lookedAt(src);
        if (be == null) {
            return 0;
        }
        boolean hermes = kind == null ? be.isHermes() : kind.equals("hermes");
        CompoundTag preset = new CompoundTag();
        preset.putString("Name", name);
        preset.putString("Kind", hermes ? "hermes" : "terminal");
        if (!profile.isEmpty() && !"default".equals(profile)) {
            preset.putString("Profile", profile);
        }
        String was = be.workspace();
        String wasName = be.workspaceName();
        be.resetToPreset(preset);
        MutableComponent msg = Component.literal("Preset set: '" + name + "', " + (hermes ? "Hermes" : "Terminal")
            + (preset.contains("Profile") ? " on profile " + profile : "")
            + ". Whoever opens this station first gets a workspace made from it.").withStyle(ChatFormatting.GOLD);
        if (!was.isEmpty()) {
            msg.append(Component.literal("\nIts workspace '" + wasName + "' keeps running on this PC. ")
                .withStyle(ChatFormatting.GRAY))
                .append(Component.literal("[undo]").withStyle(s -> s.withColor(ChatFormatting.YELLOW)
                    .withUnderlined(true).withClickEvent(new ClickEvent.RunCommand("/omo rebind " + was))));
        }
        src.sendSuccess(() -> msg, false);
        return 1;
    }

    // -- redstone ---------------------------------------------------------------

    private static int signal(CommandSourceStack src, Boolean on) {
        StationBlockEntity be = lookedAt(src);
        if (be == null) {
            return 0;
        }
        if (on != null) {
            be.setSignalsWork(on);
        }
        int level = StationSignals.comparator(be);
        String meaning = switch (level) {
            case StationSignals.ENDED -> "ended";
            case StationSignals.IDLE -> "idle";
            case StationSignals.WORKING -> "working";
            case StationSignals.INTERRUPTED -> "interrupted";
            default -> be.isBound() ? "bridge not reachable" : "no workspace yet";
        };
        MutableComponent msg = Component.literal("Comparator reads " + level + " (" + meaning + "). Plain signal "
            + (be.signalsWork() ? "on: 15 on every side while working. " : "off. ")).withStyle(ChatFormatting.GOLD);
        String flip = be.signalsWork() ? "off" : "on";
        msg.append(Component.literal("[turn " + flip + "]").withStyle(s -> s.withColor(ChatFormatting.YELLOW)
            .withUnderlined(true).withClickEvent(new ClickEvent.RunCommand("/omo signal " + flip))));
        src.sendSuccess(() -> msg, false);
        return 1;
    }

    // -- sharing a map ------------------------------------------------------------

    private static int export(CommandSourceStack src, boolean clean) {
        if (!EXPORTING.compareAndSet(false, true)) {
            src.sendFailure(Component.literal("An export check is already running."));
            return 0;
        }
        MinecraftServer server = src.getServer();
        src.sendSuccess(() -> Component.literal("Saving and reading the whole world...").withStyle(ChatFormatting.GRAY),
            false);
        scan(server, findings -> {
            if (!clean) {
                EXPORTING.set(false);
                report(src, findings, false);
                return;
            }
            ExportClean.run(server, findings, done -> {
                server.saveEverything(true, true, true);       // twice, so the .dat_old backups are clean too
                scan(server, left -> {
                    EXPORTING.set(false);
                    src.sendSuccess(() -> Component.literal("Reset " + done.stations() + " station"
                        + (done.stations() == 1 ? "" : "s") + " to presets and cleared " + done.items() + " item"
                        + (done.items() == 1 ? "" : "s") + ". The workspaces keep running on this PC.")
                        .withStyle(ChatFormatting.GOLD), false);
                    report(src, left, true);
                });
            });
        });
        return 1;
    }

    /** Save, then read every file off the server thread; {@code then} runs on the server thread. */
    private static void scan(MinecraftServer server, Consumer<List<Finding>> then) {
        server.saveEverything(true, true, true);
        ExportCheck check = ExportCheck.of(server);
        Stations.onServer(server, CompletableFuture.supplyAsync(check::run), then);
    }

    private static void report(CommandSourceStack src, List<Finding> findings, boolean afterClean) {
        MutableComponent out = Component.literal("Export check: ").withStyle(ChatFormatting.GOLD);
        if (findings.isEmpty()) {
            out.append(Component.literal("ready to share. No workspace bindings, tokens or paths from this PC.")
                .withStyle(ChatFormatting.GREEN));
            src.sendSuccess(() -> out, false);
            return;
        }
        Map<Kind, List<Finding>> byKind = findings.stream().collect(Collectors.groupingBy(Finding::kind));
        line(out, byKind, Kind.TOKEN, "The bridge token, never share it; edit it out");
        line(out, byKind, Kind.KEYED_URL, "Web addresses with a key in them");
        line(out, byKind, Kind.HOST_PATH, "Mentions of your home folder");
        line(out, byKind, Kind.STATION, "Stations bound to workspaces on this PC");
        line(out, byKind, Kind.ITEM, "Items carrying a workspace");
        boolean cleanable = byKind.containsKey(Kind.STATION) || byKind.containsKey(Kind.ITEM);
        if (cleanable && !afterClean) {
            out.append(Component.literal("\n "))
                .append(Component.literal("[Clean for sharing]").withStyle(s -> s.withColor(ChatFormatting.YELLOW)
                    .withUnderlined(true).withClickEvent(new ClickEvent.RunCommand("/omo export-clean"))))
                .append(Component.literal(" resets stations to presets and takes workspaces off items; "
                    + "your workspaces keep running.").withStyle(ChatFormatting.GRAY));
        } else if (cleanable) {
            out.append(Component.literal("\n Left: in places not loaded (other players, far chests). Visit them and run "
                + "it again.").withStyle(ChatFormatting.GRAY));
        }
        src.sendSuccess(() -> out, false);
        OmoLife.LOG.info("export check: {}", byKind.entrySet().stream()
            .map(e -> e.getKey() + "=" + e.getValue().size()).collect(Collectors.joining(" ")));
    }

    /** One line per kind: how many, and the first few places. */
    private static void line(MutableComponent out, Map<Kind, List<Finding>> byKind, Kind kind, String what) {
        List<Finding> list = byKind.get(kind);
        if (list == null) {
            return;
        }
        String where = list.stream().limit(3).map(f -> (f.pos() == null ? f.file()
            : f.pos().toShortString() + (f.dimension() == null ? " (" + f.file() + ")" : ""))
            + (kind == Kind.STATION || kind == Kind.ITEM ? " " + f.detail() : ""))
            .collect(Collectors.joining("; "));
        out.append(Component.literal("\n ✘ " + what + " (" + list.size() + "): ").withStyle(ChatFormatting.RED))
            .append(Component.literal(where + (list.size() > 3 ? "; ..." : "")).withStyle(ChatFormatting.GRAY));
    }
}
