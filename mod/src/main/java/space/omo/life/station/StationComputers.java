package space.omo.life.station;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.WeakHashMap;

import dan200.computercraft.shared.computer.core.ServerComputer;

/**
 * Which CC:T computers are Omo stations, server side. A station's computer
 * (block or pocket) is registered the moment it is built, so CC:T's own
 * paths (its {@code /computercraft} command, its computer GUI and input
 * packets, monitors attached to it) can ask "is this a station?" and hold
 * the station rule ({@link Stations#allowed}) there too. Weak keys: a
 * computer that CC:T dropped is forgotten with it.
 */
public final class StationComputers {

    private static final Map<ServerComputer, Station> STATIONS = Collections.synchronizedMap(new WeakHashMap<>());

    private StationComputers() {
    }

    public static void mark(ServerComputer computer, Station station) {
        if (computer != null && station != null) {
            STATIONS.put(computer, station);
        }
    }

    /** The station behind a computer, or null for a plain CC:T computer. */
    public static Station of(ServerComputer computer) {
        return computer == null ? null : STATIONS.get(computer);
    }

    /** The station whose computer has this CC id (what attached peripherals report), or null. */
    public static Station byComputerId(int id) {
        List<Map.Entry<ServerComputer, Station>> all;
        synchronized (STATIONS) {
            all = new ArrayList<>(STATIONS.entrySet());
        }
        for (Map.Entry<ServerComputer, Station> e : all) {
            if (e.getKey().getID() == id && e.getValue().isPresent()) {
                return e.getValue();
            }
        }
        return null;
    }
}
