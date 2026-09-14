import os
import plistlib
import sys


def sys_platform():
    return sys.platform

import subprocess
import unittest
from pathlib import Path

from filetidy import service


def spec(**overrides):
    defaults = dict(
        name="autotidy",
        paths=["/Users/someone/Downloads", "/Users/someone/Desktop"],
        interval=300,
        min_age=600,
    )
    defaults.update(overrides)
    return service.ServiceSpec(**defaults)


class CommandTests(unittest.TestCase):
    def setUp(self):
        import shutil as _shutil
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(_shutil.rmtree, self.tmp, True)

    def test_command_runs_apply_and_quiet(self):
        argv = service.command_for(spec())
        self.assertIn("run", argv)
        self.assertIn("--apply", argv)
        # Without --quiet a scheduler firing every few minutes would write a
        # "nothing to do" block to the log each time.
        self.assertIn("--quiet", argv)

    def test_command_carries_every_path_and_the_delay(self):
        argv = service.command_for(spec())
        self.assertIn("/Users/someone/Downloads", argv)
        self.assertIn("/Users/someone/Desktop", argv)
        self.assertEqual(argv[argv.index("--min-age") + 1], "600")

    def test_launcher_is_absolute_or_runs_the_module(self):
        argv = service.launcher()
        self.assertTrue(argv)
        # A scheduler has no PATH to speak of, so a bare "filetidy" would fail.
        self.assertTrue(Path(argv[0]).is_absolute(), argv)

    @unittest.skipUnless(sys_platform() == "darwin", "macOS-specific TCC behaviour")
    def test_launcher_avoids_the_shell_wrapper_on_macos(self):
        # pip's console script starts with #!/bin/sh when the install path is
        # long, so scheduling it would make launchd spawn /bin/sh. Disk access
        # must be attributable to one private interpreter, never to /bin/sh.
        argv = service.launcher()
        self.assertEqual(argv[1:], ["-m", "filetidy"])
        self.assertNotIn("/bin/sh", argv[0])

    @unittest.skipIf(sys_platform() == "darwin", "macOS always uses the interpreter")
    def test_launcher_prefers_the_invoked_script(self):
        # Registering from a dedicated virtualenv must schedule *that* copy.
        fake = Path(self.tmp) / "filetidy"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        original = sys.argv
        sys.argv = [str(fake), "service", "install"]
        try:
            self.assertEqual(service.launcher(), [str(fake.resolve())])
        finally:
            sys.argv = original

    def test_launcher_falls_back_when_not_invoked_as_the_script(self):
        import sys

        original = sys.argv
        sys.argv = ["/somewhere/pytest", "-x"]
        try:
            argv = service.launcher()
            self.assertTrue(Path(argv[0]).is_absolute())
        finally:
            sys.argv = original

    def test_label_is_namespaced(self):
        self.assertEqual(spec().label, "com.filetidy.autotidy")


class NormalisePathTests(unittest.TestCase):
    def test_paths_become_absolute(self):
        result = service.normalise_paths(["~"])
        self.assertEqual(result, [str(Path.home().resolve())])

    def test_duplicates_are_dropped(self):
        result = service.normalise_paths(["~", str(Path.home()), "~"])
        self.assertEqual(len(result), 1)

    def test_order_is_preserved(self):
        home = str(Path.home())
        result = service.normalise_paths([home, "/tmp"])
        self.assertEqual(result[0], str(Path(home).resolve()))


class LaunchdTests(unittest.TestCase):
    def test_plist_round_trips(self):
        data = service.build_launchd_plist(spec())
        # plistlib must be able to serialise it, or launchd gets a broken file.
        restored = plistlib.loads(plistlib.dumps(data))
        self.assertEqual(restored["Label"], "com.filetidy.autotidy")
        self.assertEqual(restored["StartInterval"], 300)
        self.assertTrue(restored["RunAtLoad"])

    def test_plist_logs_to_a_file(self):
        data = service.build_launchd_plist(spec())
        self.assertTrue(data["StandardOutPath"].endswith("autotidy.log"))
        self.assertEqual(data["StandardOutPath"], data["StandardErrorPath"])

    def test_plist_stays_out_of_the_way(self):
        data = service.build_launchd_plist(spec())
        self.assertEqual(data["ProcessType"], "Background")
        self.assertTrue(data["LowPriorityIO"])

    def test_plist_path_is_in_launchagents(self):
        path = service.launchd_plist_path("autotidy")
        self.assertEqual(path.parent.name, "LaunchAgents")
        self.assertTrue(path.name.endswith(".plist"))


class WindowsTests(unittest.TestCase):
    def test_schtasks_command_shape(self):
        argv = service.build_schtasks_command(spec())
        self.assertEqual(argv[:2], ["schtasks", "/create"])
        self.assertIn("/f", argv)  # overwrite an existing task rather than fail
        self.assertEqual(argv[argv.index("/tn") + 1], "filetidy_autotidy")

    def test_interval_is_converted_to_whole_minutes(self):
        argv = service.build_schtasks_command(spec(interval=300))
        self.assertEqual(argv[argv.index("/sc") + 1], "minute")
        self.assertEqual(argv[argv.index("/mo") + 1], "5")

    def test_sub_minute_interval_does_not_become_zero(self):
        # schtasks rejects /mo 0, which would make the install fail with a
        # message about an invalid interval rather than anything useful.
        argv = service.build_schtasks_command(spec(interval=30))
        self.assertEqual(argv[argv.index("/mo") + 1], "1")

    def test_paths_with_spaces_survive_quoting(self):
        argv = service.build_schtasks_command(
            spec(paths=["C:\\Users\\A B\\Downloads"]))
        command = argv[argv.index("/tr") + 1]
        self.assertIn("A B", command)
        # list2cmdline must have quoted the argument containing a space.
        self.assertIn('"C:\\Users\\A B\\Downloads"', command)


class SystemdTests(unittest.TestCase):
    def test_units_have_the_required_sections(self):
        unit, timer = service.build_systemd_units(spec())
        self.assertIn("[Service]", unit)
        self.assertIn("Type=oneshot", unit)
        self.assertIn("ExecStart=", unit)
        self.assertIn("[Timer]", timer)
        self.assertIn("[Install]", timer)

    def test_timer_uses_the_interval_and_survives_downtime(self):
        _, timer = service.build_systemd_units(spec(interval=300))
        self.assertIn("OnUnitActiveSec=300s", timer)
        self.assertIn("Persistent=true", timer)


class DispatchTests(unittest.TestCase):
    def test_platform_name_is_one_of_three(self):
        self.assertIn(service.platform_name(), ("macos", "windows", "linux"))

    def test_log_path_is_absolute(self):
        self.assertTrue(service.log_path("autotidy").is_absolute())


if __name__ == "__main__":
    unittest.main()
