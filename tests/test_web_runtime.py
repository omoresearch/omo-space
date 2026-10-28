#!/usr/bin/env python3
"""Browser startup regression tests: real adapter and pinned MCEF API bytecode.

No game, Chromium, network, bridge or desktop is started. Only Minecraft's
client executor and MCEF's native bootstrap implementation are replaced; the
blocking MCEFApi.getInstance() implementation comes from the installed mod jar.

JAVA_HOME=/path/to/jdk25 OMO_MODS_DIR=/path/to/mods \
    python3 -m unittest discover -s tests -p test_web_runtime.py -v
"""
import os
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class WebRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        home = Path.home()
        java_home = Path(os.environ.get("JAVA_HOME", home / ".local/share/omo/toolchain/jdk25"))
        cls.java = java_home / "bin/java"
        javac = java_home / "bin/javac"
        mods = Path(os.environ.get("OMO_MODS_DIR", home / ".local/share/omo/runtime/game/mods"))
        candidates = sorted(mods.glob("mcef-modern-0.3.3+mc26.2.*.jar"))
        if not cls.java.is_file() or not javac.is_file() or not candidates:
            raise unittest.SkipTest("needs JDK 25 and pinned MCEF jar (JAVA_HOME / OMO_MODS_DIR)")
        cls.temp = tempfile.TemporaryDirectory(prefix="omo-web-runtime-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.classes = root / "classes"
        cls.classes.mkdir()
        # Retain the real public API, including getInstance()'s join(), but no
        # native bootstrap/JCEF classes. The small impl shim controls its future.
        api_jar = root / "mcef-api.jar"
        with zipfile.ZipFile(candidates[0]) as source, zipfile.ZipFile(api_jar, "w") as target:
            for name in source.namelist():
                if name.startswith("net/dimaskama/mcef/api/") and name.endswith(".class"):
                    target.writestr(name, source.read(name))
        cls.cp = os.pathsep.join((str(cls.classes), str(api_jar)))
        stubs = {
            "net/minecraft/client/Minecraft.java": """
                package net.minecraft.client;
                public final class Minecraft {
                    private static final Minecraft INSTANCE = new Minecraft();
                    public final java.util.Queue<Runnable> pending = new java.util.concurrent.ConcurrentLinkedQueue<>();
                    public static Minecraft getInstance() { return INSTANCE; }
                    public void execute(Runnable task) { pending.add(task); }
                    public void drain() { Runnable task; while ((task = pending.poll()) != null) task.run(); }
                }
            """,
            "net/dimaskama/mcef/impl/MCEFApiImpl.java": """
                package net.dimaskama.mcef.impl;
                import java.util.concurrent.CompletableFuture;
                import net.dimaskama.mcef.api.MCEFApi;
                public final class MCEFApiImpl {
                    public static CompletableFuture<MCEFApi> future = new CompletableFuture<>();
                    public static int calls;
                    public static MCEFApi.Initialization initialize() {
                        calls++;
                        return new MCEFApi.Initialization() {
                            public Stage getStage() { return Stage.DOWNLOADING; }
                            public float getPercentage() { return 0; }
                            public CompletableFuture<MCEFApi> getFuture() { return future; }
                        };
                    }
                }
            """,
            "org/cef/browser/CefBrowser.java": """
                package org.cef.browser;
                public class CefBrowser {
                    public void loadURL(String url) { }
                    public String getURL() { return ""; }
                    public void goBack() { }
                    public void goForward() { }
                    public void reload() { }
                    public void setWindowlessFrameRate(int fps) { }
                    public java.awt.Component getUIComponent() { return null; }
                }
            """,
            # McefRuntime logs through OmoLife.LOG; the real class registers blocks.
            "space/omo/life/OmoLife.java": """
                package space.omo.life;
                public final class OmoLife {
                    public static final Log LOG = new Log();
                    public static final class Log {
                        public void warn(String msg, Object... args) { }
                        public void debug(String msg, Object... args) { }
                    }
                }
            """,
            # The real CefHooks needs JCEF natives and Gson; these cases only
            # cover startup, so its hook points are inert here.
            "space/omo/life/client/CefHooks.java": """
                package space.omo.life.client;
                final class CefHooks {
                    static int started;
                    static void started() { started++; }
                    static void attach(org.cef.browser.CefBrowser b, java.util.function.Consumer<String> title,
                                       java.util.function.Consumer<String> popup) { }
                    static void detach(org.cef.browser.CefBrowser b) { }
                    static void setVolume(org.cef.browser.CefBrowser b, double v) { }
                }
            """,
            "space/omo/life/client/WebRuntimeHarness.java": """
                package space.omo.life.client;
                import java.util.ArrayList;
                import java.util.concurrent.*;
                import net.dimaskama.mcef.api.MCEFApi;
                import net.dimaskama.mcef.impl.MCEFApiImpl;
                import net.minecraft.client.Minecraft;
                public final class WebRuntimeHarness {
                    private static void check(boolean ok, String why) {
                        if (!ok) throw new AssertionError(why);
                    }
                    public static void main(String[] args) throws Exception {
                        switch (args[0]) {
                            case "pending" -> pending();
                            case "success" -> success();
                            case "failure" -> failure();
                            default -> throw new AssertionError("unknown case");
                        }
                    }
                    private static void pending() throws Exception {
                        ExecutorService render = Executors.newSingleThreadExecutor(r -> {
                            Thread t = new Thread(r, "test-render-thread"); t.setDaemon(true); return t;
                        });
                        try {
                            try {
                                check(!render.submit(McefRuntime::ready).get(500, TimeUnit.MILLISECONDS),
                                    "pending Chromium must not be ready");
                            } catch (TimeoutException e) {
                                throw new AssertionError("ready() blocked the render thread on unfinished Chromium startup", e);
                            }
                            check(MCEFApiImpl.calls == 0, "readiness must not start Chromium as a side effect");
                        } finally {
                            MCEFApiImpl.future.complete((url, transparent) -> null);
                            render.shutdownNow();
                        }
                        System.out.println("PASS pending: readiness returns without joining or starting Chromium");
                    }
                    private static void success() throws Exception {
                        var status = new ArrayList<String>();
                        int[] readyCalls = {0};
                        String[] openedUrl = {null};
                        boolean[] transparent = {true};
                        Thread client = Thread.currentThread();
                        Runnable onReady = () -> {
                            check(Thread.currentThread() == client, "callback must run on the client thread");
                            check(McefRuntime.ready(), "publish the API before creating waiting pages");
                            readyCalls[0]++;
                        };
                        McefRuntime.initialize(status::add, onReady);
                        McefRuntime.initialize(status::add, onReady);
                        check(MCEFApiImpl.calls == 1, "initialize Chromium once for all pages");
                        check(status.size() == 1 && status.get(0).contains("DOWNLOADING"), "report startup progress");
                        MCEFApi instance = (url, opaque) -> {
                            openedUrl[0] = url; transparent[0] = opaque; return null;
                        };
                        Thread download = new Thread(() -> MCEFApiImpl.future.complete(instance), "test-download");
                        download.start(); download.join();
                        check(readyCalls[0] == 0, "download thread must not create pages");
                        check(!McefRuntime.ready(), "API is not usable until client callback publishes it");
                        Minecraft.getInstance().drain();
                        check(McefRuntime.ready() && readyCalls[0] == 1, "initialize waiting pages exactly once");
                        check(CefHooks.started == 1, "start the Firefox cookie import once Chromium is up");
                        check(status.get(status.size() - 1).isEmpty(), "clear startup status after success");
                        check(McefRuntime.create("https://example.invalid/") != null, "create a page from the published API");
                        check("https://example.invalid/".equals(openedUrl[0]) && !transparent[0], "preserve URL and opaque rendering");
                        check(MCEFApiImpl.calls == 1, "readiness and creation must not call the blocking API getter");
                        System.out.println("PASS success: one async startup; API and pages published on client thread");
                    }
                    private static void failure() {
                        var status = new ArrayList<String>();
                        int[] readyCalls = {0};
                        McefRuntime.initialize(status::add, () -> readyCalls[0]++);
                        MCEFApiImpl.future.completeExceptionally(new IllegalStateException("native bootstrap failed"));
                        Minecraft.getInstance().drain();
                        check(!McefRuntime.ready(), "failed initialization must remain unavailable, not throw on render");
                        check(readyCalls[0] == 0, "failed startup must not create browsers");
                        check(status.get(status.size() - 1).contains("Chromium failed to start: native bootstrap failed"),
                            "report the failure instead of propagating it through the renderer");
                        System.out.println("PASS failure: native startup error reported, no renderer exception or page creation");
                    }
                }
            """,
        }
        # Inert signature types for input and rendering. They are never invoked;
        # every startup method under test is the unmodified production source.
        stubs["net/minecraft/client/input/MouseButtonEvent.java"] = (
            "package net.minecraft.client.input; public class MouseButtonEvent { }")
        # Same shapes as Minecraft's records, for the key handling in McefRuntime.View.
        stubs["net/minecraft/client/input/KeyEvent.java"] = (
            "package net.minecraft.client.input; public record KeyEvent(int key, int scancode, int modifiers) { }")
        stubs["net/minecraft/client/input/CharacterEvent.java"] = (
            "package net.minecraft.client.input; public record CharacterEvent(int codepoint) { }")
        for name in ("GpuTexture", "GpuTextureView"):
            stubs[f"com/mojang/blaze3d/textures/{name}.java"] = (
                f"package com.mojang.blaze3d.textures; public class {name} {{ }}")
        stubs["com/mojang/blaze3d/platform/cursor/CursorType.java"] = (
            "package com.mojang.blaze3d.platform.cursor; public class CursorType { }")
        sources = []
        for relative, source in stubs.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source))
            sources.append(str(path))
        client = REPO / "mod/src/main/java/space/omo/life/client"
        sources += [str(client / "McefRuntime.java"), str(client / "WebView.java"), str(client / "PageKeys.java")]
        compiled = subprocess.run([str(javac), "-proc:none", "-cp", str(api_jar),
                                   "-d", str(cls.classes), *sources],
                                  capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(f"adapter harness did not compile:\n{compiled.stdout}{compiled.stderr}")

    def run_case(self, name):
        result = subprocess.run([str(self.java), "-cp", self.cp,
                                 "space.omo.life.client.WebRuntimeHarness", name],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_readiness_does_not_block_render_thread_during_first_download(self):
        self.run_case("pending")

    def test_success_publishes_api_on_client_thread_once(self):
        self.run_case("success")

    def test_failed_startup_reports_error_without_throwing_from_readiness(self):
        self.run_case("failure")


if __name__ == "__main__":
    unittest.main()
