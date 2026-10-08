import java.io.FileInputStream;
import java.io.InputStream;

import org.squiddev.cobalt.LuaState;
import org.squiddev.cobalt.compiler.LuaC;

/**
 * Compile Lua files with CC:T's own Lua compiler (Cobalt), so a syntax error
 * fails the build instead of dropping a player at a CraftOS prompt.
 *
 *   java -cp cobalt.jar mod/tools/LuaCheck.java file.lua ...
 */
public class LuaCheck {
    public static void main(String[] args) throws Exception {
        LuaState state = new LuaState();
        int bad = 0;
        for (String path : args) {
            try (InputStream in = new FileInputStream(path)) {
                LuaC.compile(state, in, "@" + path);
            } catch (Exception e) {
                System.err.println("lua: " + e.getMessage());
                bad++;
            }
        }
        if (bad > 0) {
            System.exit(1);
        }
    }
}
