"""The bridge's clipboard must follow the player's desktop after a login change.

A GNOME Shell crash starts a new login with a new Xwayland cookie. The bridge keeps
the old XAUTHORITY it inherited, so it must try the newer cookies on the session's
own DISPLAY before falling through to some other open X server this user owns
(a hidden recording Xorg that accepts anyone).
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bridge"))
os.environ.setdefault("OMO_TMUX", "/bin/true")
import omobridge as b  # noqa: E402


class LiveDisplay(unittest.TestCase):
    def setUp(self):
        self.rundir = tempfile.mkdtemp()
        self.old = os.path.join(self.rundir, ".mutter-Xwaylandauth.OLD")
        self.new = os.path.join(self.rundir, ".mutter-Xwaylandauth.NEW")
        for p, t in ((self.old, 1000), (self.new, 2000)):
            open(p, "w").close()
            os.utime(p, (t, t))
        b._live_x._cache = None
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.rundir, "DISPLAY": ":1",
                                                "XAUTHORITY": self.old})
        self.env.start()
        self.nodisp = mock.patch.object(b, "DISPLAY", "")
        self.nodisp.start()
        self.cands = mock.patch.object(b, "_display_candidates", return_value=[":1", ":98"])
        self.cands.start()

    def tearDown(self):
        for p in (self.cands, self.nodisp, self.env):
            p.stop()
        b._live_x._cache = None

    def accepts(self, table):
        return mock.patch.object(b, "_x_ok", side_effect=lambda d, x=None: (d, x) in table)

    def test_cookies_inherited_first_then_newest(self):
        self.assertEqual(b._xauthorities()[:2], [self.old, self.new])

    def test_stale_cookie_uses_new_cookie_on_own_display_not_open_xorg(self):
        # :1 only takes the new cookie; :98 (hidden film Xorg) takes anything.
        table = {(":1", self.new), (":98", self.old), (":98", self.new)}
        with self.accepts(table):
            self.assertEqual(b._live_x(), (":1", self.new))
            self.assertEqual(b._desktop_env()["XAUTHORITY"], self.new)

    def test_falls_back_to_other_display_only_when_own_is_gone(self):
        with self.accepts({(":98", self.old)}):
            self.assertEqual(b._live_x()[0], ":98")

    def test_cache_expires_and_moves_back_to_desktop(self):
        with self.accepts({(":98", self.old)}):
            self.assertEqual(b._live_x()[0], ":98")
        with self.accepts({(":1", self.new), (":98", self.old)}):
            self.assertEqual(b._live_x()[0], ":98")  # still cached
            disp, xauth, t = b._live_x._cache
            b._live_x._cache = (disp, xauth, t - b.LIVE_DISPLAY_TTL - 1)
            self.assertEqual(b._live_x()[0], ":1")

    def test_pinned_display_wins(self):
        with mock.patch.object(b, "DISPLAY", ":77"):
            self.assertEqual(b._live_x()[0], ":77")


if __name__ == "__main__":
    unittest.main()
