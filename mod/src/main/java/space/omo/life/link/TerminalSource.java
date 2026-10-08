package space.omo.life.link;

import dan200.computercraft.core.terminal.Terminal;

/**
 * What every CC:T {@code ServerComputer} offers linked walls (added by
 * {@code ServerComputerMixin}): its terminal, and a counter that moves each
 * server tick the terminal changed, so {@link LinkMirror} copies a screen
 * only when there is something new on it.
 */
public interface TerminalSource {

    Terminal omolife$terminal();

    int omolife$terminalVersion();
}
