package space.omo.life.client;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Texture ids to unregister later, not in the middle of a frame.
 *
 * <p>A wall's page can close while the frame is still being built: the
 * renderer asks {@link WebPages#open} for a near wall, which takes the page
 * of a farther wall whose quad this frame has already submitted. If its
 * texture id left the texture manager right then, drawing that quad looked
 * the id up again, found nothing, and loaded {@code omolife:web/x_y_z} from
 * the resource packs: a "Missing resource" warning per closed page, and a
 * stray missing-texture entry left registered under the id. So the id stays
 * registered until the next client tick, when every frame that used it is
 * done; an id bound again before then is kept.
 *
 * <p>Pure Java (no Minecraft): unit-tested in tests/test_browser_guard.py.
 * Client thread only.
 */
final class LateRelease<T> {

    private final Set<T> pending = new LinkedHashSet<>();

    /** Unregister {@code id} at the next {@link #flush}. */
    void retire(T id) {
        pending.add(id);
    }

    /** {@code id} is in use again: keep it registered. */
    void revive(T id) {
        pending.remove(id);
    }

    boolean contains(T id) {
        return pending.contains(id);
    }

    int size() {
        return pending.size();
    }

    /** Hand every retired id to {@code release}, once; a release that throws never leaves one behind. */
    void flush(Consumer<T> release) {
        if (pending.isEmpty()) {
            return;
        }
        List<T> ids = new ArrayList<>(pending);
        pending.clear();
        RuntimeException first = null;
        for (T id : ids) {
            try {
                release.accept(id);
            } catch (RuntimeException e) {
                if (first == null) {
                    first = e;
                }
            }
        }
        if (first != null) {
            throw first;
        }
    }
}
