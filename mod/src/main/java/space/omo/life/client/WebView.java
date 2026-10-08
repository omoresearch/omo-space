package space.omo.life.client;

import com.mojang.blaze3d.textures.GpuTextureView;

import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;

/**
 * One browser page, as the monitor code sees it. The only implementation
 * lives in {@link McefRuntime}, the one class that touches MCEF, so the mod
 * loads and runs (terminals, stations, monitors) when MCEF is not installed.
 */
public interface WebView {

    /** The page's live GPU texture, or null before the first paint. */
    GpuTextureView texture();

    void resize(int width, int height);

    void load(String url);

    /** The URL the page is showing now, or null if unknown. */
    String url();

    void back();

    void forward();

    void reload();

    void frameRate(int fps);

    void focus(boolean focused);

    void mouseMoved(int x, int y);

    void mouseClicked(MouseButtonEvent event, boolean doubleClick);

    void mouseReleased(MouseButtonEvent event);

    void mouseScrolled(int x, int y, double amount);

    void keyPressed(KeyEvent event);

    void keyReleased(KeyEvent event);

    void charTyped(CharacterEvent event);

    void close();
}
