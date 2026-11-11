# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for vibrance. They talk to a fake kscreen-doctor, never to real displays.

    python3 -m unittest discover -s tests -v
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "vibrance"
FAKE = HERE / "fake_kscreen_doctor.py"

sys.dont_write_bytecode = True  # no __pycache__ next to the script
_loader = importlib.machinery.SourceFileLoader("vibrance", str(TOOL))
vibrance = importlib.util.module_from_spec(importlib.util.spec_from_loader("vibrance", _loader))
_loader.exec_module(vibrance)

# A laptop panel that used its built-in profile and an untouched external monitor.
LAPTOP = dict(name="eDP-1", uuid="b61e0bdd-7ca2-4572-8d3f-000000000001", enabled=True, hdr="incapable",
              wcg="incapable", icc="", source="EDID", tradeoff="preferAccuracy")
MONITOR = dict(name="HDMI-A-1", uuid="89df7ee4-613e-45df-8546-000000000002", enabled=True, hdr="disabled",
               wcg="disabled", icc="", source="sRGB", tradeoff="preferEfficiency")
PRINT_DISPLAYS = ["/bin/sh", "-c", '/bin/cat "$FAKE_KSCREEN_STATE"; exit 3']


class Sandbox:
    """A private HOME with fake displays; PATH contains nothing but the fake kscreen-doctor."""

    def __init__(self, test, outputs, plasma=(6, 8)):
        tmp = tempfile.TemporaryDirectory(prefix="vibrance-test-")
        test.addCleanup(tmp.cleanup)
        self.test, self.dir = test, Path(tmp.name)
        self.display_file, self.log = self.dir / "displays.json", self.dir / "calls.log"
        self.set_displays(outputs, plasma)
        self.log.write_text("")
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        launcher = bin_dir / "kscreen-doctor"
        launcher.write_text("#!/bin/sh\nexec %s -I %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(str(FAKE))))
        launcher.chmod(0o755)
        self.env = {"PATH": str(bin_dir), "HOME": str(self.dir / "home"),
                    "XDG_DATA_HOME": str(self.dir / "data"), "XDG_STATE_HOME": str(self.dir / "state"),
                    "FAKE_KSCREEN_STATE": str(self.display_file), "FAKE_KSCREEN_LOG": str(self.log)}

    def set_displays(self, outputs, plasma=(6, 8)):
        self.display_file.write_text(json.dumps({"plasma": list(plasma), "outputs": [dict(o) for o in outputs]}))

    def call(self, *args, **env):
        return subprocess.run([sys.executable, "-I", str(TOOL), *args], env={**self.env, **env},
                              capture_output=True, text=True, timeout=60)

    def ok(self, *args, **env):
        result = self.call(*args, **env)
        self.test.assertEqual(result.returncode, 0, result.stderr)
        self.test.assertEqual(result.stderr, "")
        return result.stdout.splitlines()

    def color(self, name):
        for output in json.loads(self.display_file.read_text())["outputs"]:
            if output["name"] == name:
                return output["source"], output["icc"], output["tradeoff"]
        raise KeyError(name)

    def calls(self):
        """Arguments of every kscreen-doctor invocation that changed something."""
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [line["args"] for line in lines if line["args"] != ["-o"]]

    def saved(self):
        path = self.dir / "state" / "vibrance" / "displays.json"
        return json.loads(path.read_text()) if path.exists() else None

    def profile(self, percent, revision=vibrance.PROFILE_REVISION):
        return str(self.dir / "data" / "vibrance" / ("vibrance-%d-r%d.icc" % (percent, revision)))


class SwitchingTest(unittest.TestCase):
    def test_on_changes_every_display_with_one_configuration(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        self.assertEqual(box.ok("on"), ["eDP-1: vibrance 150%", "HDMI-A-1: vibrance 150%"])
        path = box.profile(150)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(box.calls(), [[
            "output.eDP-1.iccprofile." + path, "output.eDP-1.colorProfileSource.ICC",
            "output.eDP-1.colorPowerTradeoff.preferEfficiency",
            "output.HDMI-A-1.iccprofile." + path, "output.HDMI-A-1.colorProfileSource.ICC"]])
        self.assertEqual(box.color("eDP-1"), ("ICC", path, "preferEfficiency"))
        self.assertEqual(box.color("HDMI-A-1"), ("ICC", path, "preferEfficiency"))
        self.assertEqual(box.saved(), {
            LAPTOP["uuid"]: {"name": "eDP-1", "percent": 150,
                             "restore": {"source": "EDID", "icc": "", "tradeoff": "preferAccuracy"}},
            MONITOR["uuid"]: {"name": "HDMI-A-1", "percent": 150,
                              "restore": {"source": "sRGB", "icc": "", "tradeoff": "preferEfficiency"}}})

    def test_off_restores_the_previous_settings_and_keeps_the_levels(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        box.ok("on", "170")
        self.assertEqual(box.ok("off"), ["eDP-1: vibrance off (color profile source: EDID)",
                                         "HDMI-A-1: vibrance off (color profile source: sRGB)"])
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        self.assertEqual(box.color("HDMI-A-1"), ("sRGB", "", "preferEfficiency"))
        self.assertEqual(len(box.calls()), 2)
        self.assertEqual(box.saved(), {LAPTOP["uuid"]: {"name": "eDP-1", "percent": 170},
                                       MONITOR["uuid"]: {"name": "HDMI-A-1", "percent": 170}})
        self.assertEqual(box.ok("off"), ["vibrance is not active"])
        self.assertEqual([line.split(None, 1) for line in box.ok("status")],
                         [["eDP-1", "off (last level 170%)"], ["HDMI-A-1", "off (last level 170%)"]])

    def test_each_display_keeps_its_own_level(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        box.ok("on", "-o", "eDP-1=160", "-o", "HDMI-A-1=120")
        self.assertEqual(box.color("eDP-1")[1], box.profile(160))
        self.assertEqual(box.color("HDMI-A-1")[1], box.profile(120))
        self.assertEqual(box.ok("off", "-o", "HDMI-A-1"), ["HDMI-A-1: vibrance off (color profile source: sRGB)"])
        self.assertEqual(box.color("eDP-1")[1], box.profile(160))
        self.assertEqual(box.ok("on"), ["eDP-1: vibrance is already at 160%", "HDMI-A-1: vibrance 120%"])
        self.assertEqual([line.split(None, 1) for line in box.ok("status")],
                         [["eDP-1", "on, 160%"], ["HDMI-A-1", "on, 120%"]])

    def test_a_level_without_a_display_applies_to_the_selected_ones(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        self.assertEqual(box.ok("on", "130", "-o", "HDMI-A-1"), ["HDMI-A-1: vibrance 130%"])
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        box.ok("on", "200", "-o", "all", "-o", "eDP-1=110")
        self.assertEqual(box.color("eDP-1")[1], box.profile(110))
        self.assertEqual(box.color("HDMI-A-1")[1], box.profile(200))

    def test_toggle(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        self.assertEqual(box.ok("toggle", "175"), ["eDP-1: vibrance 175%", "HDMI-A-1: vibrance 175%"])
        box.ok("toggle", "-o", "eDP-1")
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        self.assertEqual(box.color("HDMI-A-1")[1], box.profile(175))
        box.ok("toggle")  # one display is still on, so everything goes off
        self.assertEqual(box.color("HDMI-A-1"), ("sRGB", "", "preferEfficiency"))
        box.ok("toggle")  # and back on at the remembered level
        self.assertEqual(box.color("eDP-1"), ("ICC", box.profile(175), "preferEfficiency"))

    def test_the_users_own_profile_comes_back(self):
        box = Sandbox(self, [])
        own = box.dir / "calibrated.icc"
        own.write_bytes(b"profile")
        box.set_displays([dict(MONITOR, source="ICC", icc=str(own))])
        box.ok("on")
        self.assertEqual(box.color("HDMI-A-1")[1], box.profile(150))
        self.assertEqual(box.ok("off"), ["HDMI-A-1: vibrance off (color profile source: ICC)"])
        self.assertEqual(box.color("HDMI-A-1"), ("ICC", str(own), "preferEfficiency"))
        box.ok("on")
        own.unlink()  # KWin would refuse a configuration that names a missing file
        box.ok("off")
        self.assertEqual(box.color("HDMI-A-1"), ("sRGB", "", "preferEfficiency"))

    def test_off_without_saved_settings_falls_back_to_srgb(self):
        box = Sandbox(self, [LAPTOP])
        box.ok("on")
        (box.dir / "state" / "vibrance" / "displays.json").unlink()
        self.assertEqual(box.ok("off"), ["eDP-1: vibrance off (color profile source: sRGB)"])
        self.assertEqual(box.color("eDP-1"), ("sRGB", "", "preferEfficiency"))

    def test_settings_follow_the_monitor_not_the_connector(self):
        box = Sandbox(self, [MONITOR])
        box.ok("on", "160")
        box.set_displays([dict(MONITOR, uuid="00000000-0000-4000-8000-00000000000f")])  # another monitor, same port
        self.assertEqual(box.ok("on"), ["HDMI-A-1: vibrance 150%"])
        self.assertEqual(sorted(entry["percent"] for entry in box.saved().values()), [150, 160])

    def test_a_profile_of_an_older_revision_is_replaced(self):
        box = Sandbox(self, [])
        old = Path(box.profile(150, revision=0))
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old")
        box.set_displays([dict(MONITOR, source="ICC", icc=str(old))])
        self.assertEqual(box.ok("status")[0].split(None, 1), ["HDMI-A-1", "on, 150%"])
        self.assertEqual(box.ok("on", "150"), ["HDMI-A-1: vibrance 150%"])
        self.assertEqual(box.calls(), [["output.HDMI-A-1.iccprofile." + box.profile(150)]])
        self.assertNotIn("restore", box.saved()[MONITOR["uuid"]])  # it was ours already


class ProblemTest(unittest.TestCase):
    def assertFails(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(message, result.stderr)

    def test_a_rejected_profile_changes_nothing(self):
        box = Sandbox(self, [LAPTOP])
        self.assertFails(box.call("on", FAKE_KSCREEN_REJECT="1"), "KWin rejected the configuration: ICC profile")
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        self.assertIsNone(box.saved())

    def test_level_limits(self):
        box = Sandbox(self, [LAPTOP])
        for percent in ("99", "273"):
            self.assertFails(box.call("on", percent), "between 100 and 272")
            self.assertFails(box.call("on", "-o", "eDP-1=" + percent), "between 100 and 272")
        self.assertEqual(box.calls(), [])
        box.ok("on", "272")
        box.ok("on", "100")

    def test_unknown_and_disabled_displays(self):
        box = Sandbox(self, [LAPTOP, dict(MONITOR, enabled=False)])
        self.assertFails(box.call("on", "-o", "DP-9"), "no display named DP-9 (available: HDMI-A-1, eDP-1)")
        self.assertFails(box.call("on", "-o", "HDMI-A-1"), "HDMI-A-1: the display is disabled")
        self.assertEqual(box.ok("on"), ["eDP-1: vibrance 150%"])
        self.assertEqual(box.ok("status")[1].split(None, 1), ["HDMI-A-1", "unavailable: the display is disabled"])

    def test_hdr_and_wide_gamut_displays_are_left_alone(self):
        for mode in (dict(hdr="enabled", wcg="enabled"), dict(wcg="enabled")):
            box = Sandbox(self, [LAPTOP, dict(MONITOR, **mode)])
            out = box.ok("on")
            self.assertEqual(out[1], "eDP-1: vibrance 150%")
            self.assertTrue(out[0].startswith("HDMI-A-1: skipped, HDR or wide color gamut is enabled"), out[0])
            self.assertEqual(box.color("HDMI-A-1"), ("sRGB", "", "preferEfficiency"))
            self.assertFails(box.call("on", "-o", "HDMI-A-1"), "HDMI-A-1: HDR or wide color gamut is enabled")
            self.assertIn("unavailable: HDR", box.ok("status")[1])
        box = Sandbox(self, [dict(MONITOR, hdr="enabled")])
        self.assertFails(box.call("on"), "cannot be used on any of the displays")

    def test_status_points_out_the_slow_path(self):
        box = Sandbox(self, [LAPTOP])
        self.assertEqual(box.ok("status"), ["eDP-1  off"])
        box.ok("on")
        self.assertEqual(box.ok("status"), ["eDP-1  on, 150%"])
        box.set_displays([dict(LAPTOP, source="ICC", icc=box.profile(150))])  # switched back in System Settings
        self.assertIn("on, 150% (color accuracy is preferred", box.ok("status")[0])
        self.assertEqual(box.ok("on"), ["eDP-1: vibrance 150%"])
        self.assertEqual(box.color("eDP-1")[2], "preferEfficiency")

    def test_plasma_6_2_has_no_power_preference_and_no_error_messages(self):
        box = Sandbox(self, [LAPTOP, MONITOR], plasma=(6, 2))
        box.ok("on", "140")
        self.assertEqual(box.calls(), [[
            "output.eDP-1.iccprofile." + box.profile(140), "output.eDP-1.colorProfileSource.ICC",
            "output.HDMI-A-1.iccprofile." + box.profile(140), "output.HDMI-A-1.colorProfileSource.ICC"]])
        self.assertEqual(sorted(box.saved()), ["HDMI-A-1", "eDP-1"])  # no UUIDs before Plasma 6.4
        self.assertIsNone(box.saved()["eDP-1"]["restore"]["tradeoff"])
        box.ok("off")
        self.assertEqual(box.color("eDP-1")[:2], ("EDID", ""))
        self.assertFails(box.call("on", FAKE_KSCREEN_REJECT="1"), "KWin did not apply")

    def test_unsupported_sessions_get_a_clear_message(self):
        for box, reason in ((Sandbox(self, [LAPTOP], plasma=(6, 0)), "eDP-1: this Plasma version is too old"),
                            (Sandbox(self, [dict(LAPTOP, capable=False)]), "eDP-1: it has no color profile setting")):
            self.assertFails(box.call("on"), "cannot be used on any of the displays")
            self.assertFails(box.call("on", "-o", "eDP-1"), reason)
            self.assertEqual(box.calls(), [])
        # a virtual display (screen sharing) next to a real one is passed over
        box = Sandbox(self, [LAPTOP, dict(MONITOR, name="Virtual-share", capable=False)])
        self.assertEqual(box.ok("on"), ["Virtual-share: skipped, it has no color profile setting (a virtual display, "
                                        "or not a Plasma Wayland session)", "eDP-1: vibrance 150%"])
        self.assertEqual(len(box.calls()[0]), 3)
        box = Sandbox(self, [LAPTOP])
        self.assertFails(box.call("on", PATH=str(box.dir / "home")), "kscreen-doctor not found")

    def test_usage_errors(self):
        box = Sandbox(self, [LAPTOP])
        for args in (["run", "150"], ["on", "150", "--", "true"], ["on", "-o", "=150"], ["on", "-o", "eDP-1=x"],
                     ["on", "much"], []):
            self.assertEqual(box.call(*args).returncode, 2, args)
        self.assertEqual(box.ok("--version"), ["vibrance " + vibrance.__version__])
        self.assertIn("Steam launch options", "\n".join(box.ok("--help")))


class RunTest(unittest.TestCase):
    def test_run_enables_only_while_the_command_runs(self):
        box = Sandbox(self, [LAPTOP])
        result = box.call("run", "200", "--", *PRINT_DISPLAYS)
        self.assertEqual((result.returncode, result.stderr), (3, ""))
        during = json.loads(result.stdout)["outputs"][0]  # nothing but the command writes to stdout
        self.assertEqual((during["source"], during["icc"]), ("ICC", box.profile(200)))
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        self.assertEqual(box.saved(), {LAPTOP["uuid"]: {"name": "eDP-1"}})  # run does not set the level

    def test_run_returns_to_the_level_that_was_active_before(self):
        box = Sandbox(self, [LAPTOP, MONITOR])
        box.ok("on", "160", "-o", "eDP-1")
        result = box.call("run", "200", "--", *PRINT_DISPLAYS)
        self.assertEqual([o["icc"] for o in json.loads(result.stdout)["outputs"]], [box.profile(200)] * 2)
        self.assertEqual(box.color("eDP-1"), ("ICC", box.profile(160), "preferEfficiency"))
        self.assertEqual(box.color("HDMI-A-1"), ("sRGB", "", "preferEfficiency"))
        self.assertEqual(box.saved()[LAPTOP["uuid"]]["percent"], 160)
        self.assertEqual(box.ok("off", "-o", "eDP-1"), ["eDP-1: vibrance off (color profile source: EDID)"])

    def test_the_command_starts_even_when_enabling_fails(self):
        box = Sandbox(self, [LAPTOP])
        for args, env in ((["run"], {"FAKE_KSCREEN_REJECT": "1"}), (["run", "999"], {}), (["run", "-o", "DP-9"], {}),
                          (["run"], {"PATH": str(box.dir / "home")})):
            result = box.call(*args, "--", "/bin/sh", "-c", "echo started; exit 4", **env)
            self.assertEqual((result.returncode, result.stdout), (4, "started\n"), result.stderr)
            self.assertIn("starting the command without it", result.stderr)
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))
        self.assertEqual(box.call("run", "--", str(box.dir / "missing-game")).returncode, 127)
        self.assertEqual(box.call("run", "--", str(box.display_file)).returncode, 126)  # not executable
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))

    def test_an_unwritable_profile_directory_does_not_block_the_command(self):
        box = Sandbox(self, [LAPTOP])
        (box.dir / "data").write_text("")  # a file where the profile directory should be
        result = box.call("run", "--", "/bin/sh", "-c", "echo started")
        self.assertEqual((result.returncode, result.stdout), (0, "started\n"), result.stderr)
        self.assertIn("starting the command without it", result.stderr)
        result = box.call("on")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_on_without_a_level_keeps_the_current_one(self):
        box = Sandbox(self, [MONITOR])
        box.ok("on", "170")
        (box.dir / "state" / "vibrance" / "displays.json").unlink()
        self.assertEqual(box.ok("on"), ["HDMI-A-1: vibrance is already at 170%"])

    def test_steam_environment_is_kept_away_from_kscreen_doctor(self):
        box = Sandbox(self, [LAPTOP])
        steam = {"LD_LIBRARY_PATH": "/steam-runtime", "SYSTEM_LD_LIBRARY_PATH": "", "WAYLAND_DISPLAY": "wayland-0",
                 "QT_QPA_PLATFORM": "xcb"}
        result = box.call("run", "--", "/bin/sh", "-c", 'echo "$LD_LIBRARY_PATH $QT_QPA_PLATFORM"', **steam)
        self.assertEqual((result.returncode, result.stdout), (0, "/steam-runtime xcb\n"), result.stderr)
        seen = [json.loads(line)["env"] for line in box.log.read_text().splitlines()]
        self.assertEqual(len(seen), 6)  # look, apply, check - before and after the command
        for env in seen:
            self.assertEqual(env, {"LD_LIBRARY_PATH": None, "LD_PRELOAD": None, "QT_QPA_PLATFORM": "wayland"})

    def test_run_forwards_signals_and_still_restores(self):
        box = Sandbox(self, [LAPTOP])
        wrapper = subprocess.Popen([sys.executable, "-I", str(TOOL), "run", "--", "/bin/sleep", "30"], env=box.env)
        try:
            deadline = time.monotonic() + 20
            while box.color("eDP-1")[0] != "ICC":
                self.assertLess(time.monotonic(), deadline, "vibrance was never enabled")
                time.sleep(0.05)
            wrapper.send_signal(signal.SIGTERM)  # may arrive before or after the command has started
            self.assertEqual(wrapper.wait(timeout=20), 128 + signal.SIGTERM)
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
        self.assertEqual(box.color("eDP-1"), ("EDID", "", "preferAccuracy"))


def icc_tags(blob):
    count = struct.unpack_from(">I", blob, 128)[0]
    entries = [struct.unpack_from(">4sII", blob, 132 + 12 * i) for i in range(count)]
    return {signature: blob[offset:offset + size] for signature, offset, size in entries}


class ProfileTest(unittest.TestCase):
    def test_make_writes_a_display_profile(self):
        box = Sandbox(self, [])
        target = box.dir / "out" / "test.icc"
        self.assertEqual(box.ok("make", "150", str(target)), [str(target)])
        self.assertEqual(box.ok("make", "150"), [box.profile(150)])
        blob = target.read_bytes()
        self.assertEqual(blob, Path(box.profile(150)).read_bytes())
        self.assertEqual(blob, vibrance.build_profile(150))
        self.assertEqual(struct.unpack_from(">I", blob)[0], len(blob))
        self.assertEqual(blob[12:24] + blob[36:40], b"mntrRGB XYZ acsp")
        zeroed = blob[:44] + bytes(4) + blob[48:64] + bytes(4) + blob[68:84] + bytes(16) + blob[100:]
        self.assertEqual(blob[84:100], hashlib.md5(zeroed).digest())
        self.assertEqual(sorted(icc_tags(blob)), sorted([b"desc", b"cprt", b"wtpt", b"chad", b"rXYZ", b"gXYZ", b"bXYZ",
                                                         b"rTRC", b"gTRC", b"bTRC", b"chrm"]))
        self.assertEqual(box.calls(), [])

    def test_tone_curve_is_gamma_with_an_unambiguous_black(self):
        curve = icc_tags(vibrance.build_profile(150))[b"rTRC"]
        self.assertEqual(curve[:4], b"curv")
        size = struct.unpack_from(">I", curve, 8)[0]
        table = struct.unpack_from(">%dH" % size, curve, 12)
        self.assertEqual((size, table[0], table[-1]), (4096, 0, 65535))
        self.assertTrue(all(a <= b for a, b in zip(table, table[1:])))
        # lcms2 reads position 1/65535 as round(table[1] * 4095 / 65536): it has to be above zero
        self.assertGreaterEqual(table[1] * 4095, 32768)
        self.assertLess(table[1], 16)  # the first sample KWin asks for besides zero
        for index in range(128, size, 97):
            self.assertAlmostEqual(table[index], 65535 * (index / (size - 1)) ** 2.2, delta=0.51)

    def test_kwin_derives_the_saturation_matrix(self):
        srgb = vibrance.rgb_to_xyz_matrix(vibrance.PRIMARIES, vibrance.WHITE_D65)
        luma = srgb[1]
        for percent in (100, 125, 150, 200, 272):
            scale = percent / 100
            display, xy = vibrance.narrowed_gamut(percent)
            # What KWin builds from the chrm tag and the white point: the display's RGB -> XYZ matrix ...
            from_tag = vibrance.rgb_to_xyz_matrix(xy, vibrance.WHITE_D65)
            # ... and the conversion of sRGB content into that display space.
            matrix = vibrance.mat_mul(vibrance.mat_inv(from_tag), srgb)
            for i in range(3):
                for j in range(3):
                    wanted = (1 - scale) * luma[j] + (scale if i == j else 0)
                    self.assertAlmostEqual(matrix[i][j], wanted, places=9)
                self.assertAlmostEqual(sum(matrix[i]), 1.0, places=9)  # white stays white

    def test_level_limit_matches_kwins_smallest_gamut(self):
        def area(percent):
            (ax, ay), (bx, by), (cx, cy) = vibrance.narrowed_gamut(percent)[1]
            return abs(ax * (by - cy) + bx * (cy - ay) + cx * (ay - by)) / 2
        self.assertGreater(area(vibrance.MAX_PERCENT), 0.0204)  # Colorimetry::isValid wants 0.02
        self.assertLess(area(vibrance.MAX_PERCENT + 2), 0.0204)
        chrm = icc_tags(vibrance.build_profile(vibrance.MAX_PERCENT))[b"chrm"]
        stored = [value / 65536 for value in struct.unpack_from(">6I", chrm, 12)]
        self.assertAlmostEqual(area(vibrance.MAX_PERCENT),
                               abs(stored[0] * (stored[3] - stored[5]) + stored[2] * (stored[5] - stored[1])
                                   + stored[4] * (stored[1] - stored[3])) / 2, places=5)


if __name__ == "__main__":
    unittest.main()
