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
        String[] out = Arrays.copyOf(args, args.length + OMOLIFE_EXTRA_ARGS.length + 1);
        System.arraycopy(OMOLIFE_EXTRA_ARGS, 0, out, args.length, OMOLIFE_EXTRA_ARGS.length);
        out[out.length - 1] = ua;
        return out;
    }
}
