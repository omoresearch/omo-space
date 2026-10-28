package space.omo.life.client;

import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Things to close later, each at its own due time, keyed by identity.
 * {@link WebPages} queues browsers here until they are old enough to close
 * safely. Pure Java (no Minecraft, no MCEF): unit-tested in
 * tests/test_browser_guard.py. Client thread only.
 */
final class ClosingQueue<T> {

    private final Map<T, Long> due = new IdentityHashMap<>();

    /** Queue {@code item} to close at {@code at}; queuing it again keeps the first time. */
    void add(T item, long at) {
        due.putIfAbsent(item, at);
    }

    boolean contains(T item) {
        return due.containsKey(item);
    }

    int size() {
        return due.size();
    }

    /**
     * Take out every item due by {@code now} and hand it to {@code close}.
     * The due items are collected before any is removed or closed, so a
     * close that touches this queue cannot corrupt the walk, and every one
     * is closed even if another's close throws (removing while iterating an IdentityHashMap invalidates its
     * entries; reading one afterwards crashed the game on 28 Oct).
     */
    void drain(long now, Consumer<T> close) {
        List<T> ready = new ArrayList<>();
        for (Map.Entry<T, Long> e : due.entrySet()) {
            if (now >= e.getValue()) {
                ready.add(e.getKey());
            }
        }
        for (T item : ready) {
            due.remove(item);
        }
        closeEach(ready, close);
    }

    /** Take out everything, due or not, and hand it to {@code close}. */
    void drainAll(Consumer<T> close) {
        List<T> all = new ArrayList<>(due.keySet());
        due.clear();
        closeEach(all, close);
    }

    /** Close every item even if some throw; then rethrow the first failure. */
    private static <T> void closeEach(List<T> items, Consumer<T> close) {
        RuntimeException first = null;
        for (T item : items) {
            try {
                close.accept(item);
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
