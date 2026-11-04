package space.omo.life.mixin;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import net.fabricmc.loader.api.FabricLoader;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArg;

/**
 * Runs the monitor-wall Chromium without the GPU, as an ordinary Chrome.
 *
 * <p>MCEF renders off-screen and copies frames to the CPU anyway, but its
 * Chromium still starts a GPU process. On NVIDIA (Linux) that process fails
 * {@code gbm_bo_import} -> "CreateSharedImage: could not create backing" ->
 * "Context was lost" and restarts over and over; Chromium finally aborts the
 * whole browser with a deliberate SIGILL inside libcef.so, which takes the
 * JVM (and the game) down with it. Software rendering avoids that path.
 *
 * <p>MCEF also sets {@code user_agent_product = "MCEF-Modern/0"}, which
 * replaces the {@code Chrome/<version>} token of the user agent. Sites that
 * sniff for Chrome then serve their fallback: Instagram, for one, left most
 * text in Times New Roman with broken layout. {@code --user-agent} restores a
 * normal Chrome on Linux UA for the same Chromium major version.
 *
 * <p>MCEF Modern hard-codes its switches in {@code MCEFApiImpl}'s constructor
 * ({@code CefAppBuilder.addJcefArgs(...)}) and has no config, so we append ours
 * to that call. Lives in the optional {@code omolife.mcef.mixins.json} so the
 * game still loads without MCEF installed.
 *
 * <p>Desktop apps on walls (opt-in, off by default; see docs/DESKTOP-APPS.md): when
 * {@code config/omolife-cef-debug-port.txt} holds a port, Chromium also gets
 * {@code --remote-debugging-port=<port>} (DevTools, bound to 127.0.0.1) so a local
 * bridge can attach to wall pages, and {@code --disable-features=LocalNetworkAccessChecks}
 * so those pages may call the bridge's 127.0.0.1 file API (CEF ignores the permission
 * prompt Chromium would show for that and denies the request), and
 * {@code --enable-unsafe-swiftshader}: with {@code --disable-gpu} walls have no WebGL at all since
 * Chromium dropped the automatic SwiftShader fallback, and app UIs (Kino's timeline) need it.
 * These switches widen what
 * local programs and wall pages can do, so they only exist while the file does.
 */
@Mixin(targets = "net.dimaskama.mcef.impl.MCEFApiImpl", remap = false)
public abstract class McefArgsMixin {

    private static final String[] OMOLIFE_EXTRA_ARGS = {
        "--disable-gpu",
        "--disable-gpu-compositing",
    };

    /** Chromium major of the installed JCEF build (its build_meta.json), else MCEF 0.3.3's 146. */
    private static String omolife$chromeMajor() {
        try {
            Path meta = FabricLoader.getInstance().getGameDir()
                .resolve("config/mcef-modern/jcef/build_meta.json");
            Matcher m = Pattern.compile("chromium-(\\d+)\\.").matcher(Files.readString(meta));
            if (m.find()) {
                return m.group(1);
            }
        } catch (Exception ignored) {
            // first start: JCEF not downloaded yet
        }
        return "146";
    }

    @ModifyArg(
        method = "<init>",
        at = @At(value = "INVOKE",
                 target = "Lme/friwi/jcefmaven/CefAppBuilder;addJcefArgs([Ljava/lang/String;)V"),
        remap = false)
    private String[] omolife$noGpu(String[] args) {
        String ua = "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/"
            + omolife$chromeMajor() + ".0.0.0 Safari/537.36";
        // MCEF Modern passes --disable-web-security, which turns off the same-origin policy:
        // any page on a wall (e.g. from a downloaded map) could then read localhost services
        // and other sites with the player's cookies. Walls never need it, so drop it.
        String[] kept = Arrays.stream(args).filter(a -> !omolife$unsafe(a)).toArray(String[]::new);
        String[] bridge = omolife$desktopBridgeArgs();
        String[] out = Arrays.copyOf(kept, kept.length + OMOLIFE_EXTRA_ARGS.length + 1 + bridge.length);
        System.arraycopy(OMOLIFE_EXTRA_ARGS, 0, out, kept.length, OMOLIFE_EXTRA_ARGS.length);
        out[kept.length + OMOLIFE_EXTRA_ARGS.length] = ua;
        System.arraycopy(bridge, 0, out, kept.length + OMOLIFE_EXTRA_ARGS.length + 1, bridge.length);
        return out;
    }

    /** The desktop-app bridge switches, from config/omolife-cef-debug-port.txt; none without the file. */
    private static String[] omolife$desktopBridgeArgs() {
        try {
            Path f = FabricLoader.getInstance().getGameDir().resolve("config/omolife-cef-debug-port.txt");
            if (Files.isRegularFile(f)) {
                int port = Integer.parseInt(Files.readString(f).trim());
                if (port >= 1024 && port <= 65535) {
                    return new String[] {
                        "--remote-debugging-port=" + port,
                        "--disable-features=LocalNetworkAccessChecks",
                        "--enable-unsafe-swiftshader",
                    };
                }
            }
        } catch (Exception ignored) {
            // unreadable or not a number: no bridge
        }
        return new String[0];
    }

    private static boolean omolife$unsafe(String arg) {
        if (arg == null) {
            return false;
        }
        String a = arg.trim().toLowerCase(java.util.Locale.ROOT);
        return a.startsWith("--disable-web-security")
            || a.startsWith("--allow-file-access-from-files")
            || a.startsWith("--allow-universal-access-from-files")
            || a.startsWith("--allow-running-insecure-content")
            || a.startsWith("--disable-site-isolation-trials");
    }
}
