package space.omo.life.block;

import java.util.Locale;

import net.minecraft.util.StringRepresentable;

/**
 * A station's state as a Status Lamp or Name Plate shows it, from the
 * bridge's own description of the workspace.
 */
public enum StationGlow implements StringRepresentable {
    /** No station in reach, no workspace yet, or nothing is known about it. */
    OFF(0, 0xFF8C837A),
    /** Live, and every tab is waiting at its prompt. */
    IDLE(12, 0xFF6FD08C),
    /** Live, and Hermes or a command is running in a tab. */
    WORKING(15, 0xFFF7D774),
    /** The host's session server restarted under it: nothing is running. */
    INTERRUPTED(9, 0xFFE5645A);

    private final int light;
    private final int colour;

    StationGlow(int light, int colour) {
        this.light = light;
        this.colour = colour;
    }

    /** The block light a Status Lamp gives off in this state. */
    public int light() {
        return light;
    }

    /** ARGB, the Omo palette's state colours. */
    public int colour() {
        return colour;
    }

    @Override
    public String getSerializedName() {
        return name().toLowerCase(Locale.ROOT);
    }
}
