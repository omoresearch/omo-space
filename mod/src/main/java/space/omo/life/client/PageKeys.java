package space.omo.life.client;

import java.util.Locale;

/**
 * How a Minecraft (GLFW) key becomes a key event a web page understands.
 *
 * <p>MCEF Modern hands JCEF an AWT key event whose key char is the AWT key
 * code. On Linux JCEF reads that char as the X keysym of the key (the only
 * exceptions are Backspace, Delete, Enter, Esc, Tab and the arrows), so every
 * key whose AWT code is not also its keysym arrived as some other key: Shift,
 * Ctrl and Alt had no key at all, F5 was "p", Home was "$", the keypad digits
 * were letters, the backtick an "À". Pages using {@code event.key} or
 * {@code event.keyCode} for shortcuts and game controls broke.
 *
 * <p>Enter needs a character as well. Chromium only submits a form or breaks
 * a line on the Enter <em>character</em>, and GLFW, which reports characters,
 * never reports one for Enter (or any control character). So a page saw
 * Enter go down and up and did nothing.
 *
 * <p>Pure lookup, no MCEF or Minecraft types, so it can be checked against a
 * real Chromium without starting the game.
 */
public final class PageKeys {

    /** GLFW_MOD_* bits. */
    private static final int MOD_SHIFT = 1;
    private static final int MOD_CONTROL = 2;
    private static final int MOD_ALT = 4;
    private static final int MOD_SUPER = 8;

    /** java.awt.event.InputEvent *_DOWN_MASK bits (not imported: no AWT here). */
    private static final int AWT_SHIFT = 1 << 6;
    private static final int AWT_CTRL = 1 << 7;
    private static final int AWT_META = 1 << 8;
    private static final int AWT_ALT = 1 << 9;

    public static final int GLFW_ENTER = 257;
    public static final int GLFW_KP_ENTER = 335;

    public static final boolean LINUX =
        System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("linux");

    private PageKeys() {
    }

    /** GLFW modifier bits as AWT extended modifiers (MCEF's own mapping). */
    public static int awtModifiers(int glfwMods) {
        int awt = 0;
        if ((glfwMods & MOD_SHIFT) != 0) {
            awt |= AWT_SHIFT;
        }
        if ((glfwMods & MOD_CONTROL) != 0) {
            awt |= AWT_CTRL;
        }
        if ((glfwMods & MOD_ALT) != 0) {
            awt |= AWT_ALT;
        }
        if ((glfwMods & MOD_SUPER) != 0) {
            awt |= AWT_META;
        }
        return awt;
    }

    /** The AWT virtual key code for a GLFW key, as MCEF Modern maps it (0 = none). */
    public static int awtKeyCode(int glfw) {
        if (glfw == 32 || glfw == 44 || (glfw >= 45 && glfw <= 57) || glfw == 59 || glfw == 61
            || (glfw >= 65 && glfw <= 93)) {
            return glfw;                                   // printable keys share ASCII codes
        }
        if (glfw >= 290 && glfw <= 301) {
            return 112 + (glfw - 290);                     // F1..F12
        }
        if (glfw >= 320 && glfw <= 329) {
            return 96 + (glfw - 320);                      // keypad 0..9
        }
        return switch (glfw) {
            case 39 -> 222;                                // '
            case 96 -> 192;                                // `
            case 256 -> 27;
            case 257, 335 -> 10;                           // Enter, keypad Enter
            case 258 -> 9;
            case 259 -> 8;
            case 260 -> 155;
            case 261 -> 127;
            case 262 -> 39;
            case 263 -> 37;
            case 264 -> 40;
            case 265 -> 38;
            case 266 -> 33;
            case 267 -> 34;
            case 268 -> 36;
            case 269 -> 35;
            case 280 -> 20;
            case 281 -> 145;
            case 282 -> 144;
            case 283 -> 154;
            case 284 -> 19;
            case 330 -> 110;
            case 331 -> 111;
            case 332 -> 106;
            case 333 -> 109;
            case 334 -> 107;
            case 336 -> 61;
            case 340, 344 -> 16;
            case 341, 345 -> 17;
            case 342, 346 -> 18;
            case 343, 347 -> 157;
            default -> 0;
        };
    }

    /**
     * The X keysym JCEF needs as the key char of a pressed/released event on
     * Linux, or 0 when the key has none (the event is then skipped, as MCEF
     * effectively did: a 0 keysym is no key). Printable keys keep their
     * ASCII code, which is already their keysym, so text keys behave as before.
     */
    public static int linuxKeysym(int glfw) {
        if (glfw == 32 || glfw == 39 || glfw == 44 || (glfw >= 45 && glfw <= 57) || glfw == 59 || glfw == 61
            || (glfw >= 65 && glfw <= 93) || glfw == 96) {
            return glfw;                                   // ASCII: the keysym is the character
        }
        if (glfw >= 290 && glfw <= 314) {
            return 0xFFBE + (glfw - 290);                  // XK_F1..XK_F25
        }
        if (glfw >= 320 && glfw <= 329) {
            return 0xFFB0 + (glfw - 320);                  // XK_KP_0..XK_KP_9
        }
        return switch (glfw) {
            case 256 -> 0xFF1B;                            // Escape
            case 257 -> 0xFF0D;                            // Return
            case 258 -> 0xFF09;                            // Tab
            case 259 -> 0xFF08;                            // BackSpace
            case 260 -> 0xFF63;                            // Insert
            case 261 -> 0xFFFF;                            // Delete
            case 262 -> 0xFF53;                            // Right
            case 263 -> 0xFF51;                            // Left
            case 264 -> 0xFF54;                            // Down
            case 265 -> 0xFF52;                            // Up
            case 266 -> 0xFF55;                            // Prior (Page Up)
            case 267 -> 0xFF56;                            // Next (Page Down)
            case 268 -> 0xFF50;                            // Home
            case 269 -> 0xFF57;                            // End
            case 280 -> 0xFFE5;                            // Caps_Lock
            case 281 -> 0xFF14;                            // Scroll_Lock
            case 282 -> 0xFF7F;                            // Num_Lock
            case 283 -> 0xFF61;                            // Print
            case 284 -> 0xFF13;                            // Pause
            case 330 -> 0xFFAE;                            // KP_Decimal
            case 331 -> 0xFFAF;                            // KP_Divide
            case 332 -> 0xFFAA;                            // KP_Multiply
            case 333 -> 0xFFAD;                            // KP_Subtract
            case 334 -> 0xFFAB;                            // KP_Add
            case 335 -> 0xFF8D;                            // KP_Enter
            case 336 -> 0xFFBD;                            // KP_Equal
            case 340 -> 0xFFE1;                            // Shift_L
            case 341 -> 0xFFE3;                            // Control_L
            case 342 -> 0xFFE9;                            // Alt_L
            case 343 -> 0xFFEB;                            // Super_L
            case 344 -> 0xFFE2;                            // Shift_R
            case 345 -> 0xFFE4;                            // Control_R
            case 346 -> 0xFFEA;                            // Alt_R
            case 347 -> 0xFFEC;                            // Super_R
            case 348 -> 0xFF67;                            // Menu
            default -> 0;
        };
    }

    /** Enter and keypad Enter also type the character {@code '\r'} (see the class comment). */
    public static boolean typesReturn(int glfw) {
        return glfw == GLFW_ENTER || glfw == GLFW_KP_ENTER;
    }

    /**
     * Injected into every frame on Linux. JCEF passes the keysym where
     * Chromium expects a hardware keycode, so {@code KeyboardEvent.code} was
     * wrong for every key ("Numpad1" for W, "Space" for A, "F2" for D, "" for
     * Enter and the arrows): pages and games that read WASD by
     * {@code event.code} never moved ({@code location} was as wrong: W came
     * from the numpad). This answers both for real (trusted) key events from
     * the key code Chromium reports correctly, using the US layout like GLFW's
     * own key names (so it names the physical key); {@code keypress} reuses
     * the code of the key that went down. Events a page makes itself keep
     * what it gave them. Left and right modifiers both read as left: the
     * side does not survive JCEF.
     */
    public static final String CODE_SHIM = """
        (() => {
          if (window.__omoKeyCodeShim) return;
          window.__omoKeyCodeShim = true;
          const native = Object.getOwnPropertyDescriptor(KeyboardEvent.prototype, 'code');
          if (!native || !native.get) return;
          const named = {8:'Backspace',9:'Tab',12:'NumpadEqual',13:'Enter',19:'Pause',20:'CapsLock',
            27:'Escape',32:'Space',33:'PageUp',34:'PageDown',35:'End',36:'Home',37:'ArrowLeft',
            38:'ArrowUp',39:'ArrowRight',40:'ArrowDown',44:'PrintScreen',45:'Insert',46:'Delete',
            93:'ContextMenu',106:'NumpadMultiply',107:'NumpadAdd',109:'NumpadSubtract',
            110:'NumpadDecimal',111:'NumpadDivide',144:'NumLock',145:'ScrollLock',186:'Semicolon',
            187:'Equal',188:'Comma',189:'Minus',190:'Period',191:'Slash',192:'Backquote',
            219:'BracketLeft',220:'Backslash',221:'BracketRight',222:'Quote'};
          const sided = {16:'ShiftLeft',17:'ControlLeft',18:'AltLeft',91:'MetaLeft',92:'MetaRight'};
          const codeOf = k => {
            if (k >= 65 && k <= 90) return 'Key' + String.fromCharCode(k);
            if (k >= 48 && k <= 57) return 'Digit' + (k - 48);
            if (k >= 96 && k <= 105) return 'Numpad' + (k - 96);
            if (k >= 112 && k <= 135) return 'F' + (k - 111);
            return sided[k] || named[k] || '';
          };
          const locationOf = k => sided[k] ? (k === 92 ? 2 : 1) : (k >= 96 && k <= 111) || k === 12 ? 3 : 0;
          let down = '';
          addEventListener('keydown', e => { if (e.isTrusted) down = codeOf(e.keyCode); }, true);
          const nativeLocation = Object.getOwnPropertyDescriptor(KeyboardEvent.prototype, 'location');
          Object.defineProperty(KeyboardEvent.prototype, 'code', {
            configurable: true, enumerable: true,
            get() {
              if (!this.isTrusted) return native.get.call(this);
              return this.type === 'keypress' ? down : codeOf(this.keyCode);
            }
          });
          if (nativeLocation && nativeLocation.get) {
            Object.defineProperty(KeyboardEvent.prototype, 'location', {
              configurable: true, enumerable: true,
              get() {
                if (!this.isTrusted) return nativeLocation.get.call(this);
                return this.type === 'keypress' ? 0 : locationOf(this.keyCode);
              }
            });
          }
        })();
        """;
}
