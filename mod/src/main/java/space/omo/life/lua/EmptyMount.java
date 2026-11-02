package space.omo.life.lua;

import java.io.IOException;
import java.nio.channels.SeekableByteChannel;
import java.util.List;

import dan200.computercraft.api.filesystem.FileOperationException;
import dan200.computercraft.api.filesystem.Mount;

/**
 * An empty, read-only folder. Mounted at {@code /cc} on every station
 * computer while it boots ({@link OmoLuaApi#startup}).
 *
 * <p>Why: CraftOS's own {@code rom/startup.lua} begins with
 * {@code require "cc.shell.completion"}, and {@code require} looks in the
 * computer's root folder ({@code /cc/shell/completion.lua}) before
 * {@code /rom/modules}. That folder lives in the save, so a shared map could
 * put code there that runs before the Omo autorun, with the {@code omo} API
 * at hand. With this mount in place the lookup finds nothing and falls
 * through to the ROM.
 */
final class EmptyMount implements Mount {

    static final EmptyMount INSTANCE = new EmptyMount();

    private EmptyMount() {
    }

    @Override
    public boolean exists(String path) {
        return path.isEmpty();
    }

    @Override
    public boolean isDirectory(String path) {
        return path.isEmpty();
    }

    @Override
    public void list(String path, List<String> contents) throws IOException {
        if (!path.isEmpty()) {
            throw new FileOperationException(path, "Not a directory");
        }
    }

    @Override
    public long getSize(String path) throws IOException {
        if (!path.isEmpty()) {
            throw new FileOperationException(path, "No such file");
        }
        return 0;
    }

    @Override
    public SeekableByteChannel openForRead(String path) throws IOException {
        throw new FileOperationException(path, "No such file");
    }
}
