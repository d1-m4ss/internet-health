"""The Unraid plugin file: what it embeds, that the hashes match, and what its install / remove scripts do to the host."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts" / "build-plugin.py")
build_plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_plugin)

BRIDGE = "/boot/config/custom/netprobe-unraid-notify.sh"
CRON = "/boot/config/plugins/dynamix/netprobe-notify.cron"
TEMPLATE = "/boot/config/plugins/internet-health/internet-health.xml"


def inline(element):
    return element.find("INLINE").text


class PluginFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = build_plugin.build(ROOT)
        cls.plugin = ET.fromstring(cls.text)
        cls.files = {f.get("Name"): f for f in cls.plugin.findall("FILE") if f.get("Name")}

    def test_header_names_the_plugin_and_takes_the_version_from_the_changes_file(self):
        first = (ROOT / "unraid-plugin" / "CHANGES.txt").read_text().splitlines()[0]
        self.assertEqual(self.plugin.get("name"), "internet-health")
        self.assertEqual(first, "###" + self.plugin.get("version"))
        self.assertEqual(self.plugin.get("pluginURL"), build_plugin.PLUGIN_URL)
        self.assertEqual(self.plugin.get("min"), "6.9.0")

    def test_it_embeds_exactly_the_repository_files(self):
        self.assertEqual(sorted(self.files), sorted([BRIDGE, CRON, TEMPLATE]))
        self.assertEqual(inline(self.files[BRIDGE]), (ROOT / "src/netprobe-unraid-notify.sh").read_text())
        self.assertEqual(inline(self.files[CRON]), (ROOT / "scripts/netprobe-notify.cron").read_text())
        self.assertEqual(inline(self.files[TEMPLATE]), (ROOT / "docs/unraid/internet-health.xml").read_text())

    def test_every_hash_is_the_sha256_of_the_embedded_text(self):
        for name, element in self.files.items():
            with self.subTest(name):
                self.assertEqual(element.find("SHA256").text, hashlib.sha256(inline(element).encode()).hexdigest())

    def test_the_script_starts_at_its_shebang_and_the_cron_runs_the_installed_script(self):
        self.assertEqual(inline(self.files[BRIDGE]).splitlines()[0], "#!/bin/bash")
        cron_lines = [l for l in inline(self.files[CRON]).splitlines() if l and not l.startswith("#")]
        self.assertEqual(cron_lines, ["* * * * * /usr/bin/bash %s 2>&1 | /usr/bin/logger -t netprobe-notify" % BRIDGE])

    def test_install_and_remove_scripts_run_in_bash_and_remove_is_marked(self):
        runs = [f for f in self.plugin.findall("FILE") if f.get("Run")]
        self.assertEqual([(f.get("Run"), f.get("Method")) for f in runs], [("/bin/bash", None), ("/bin/bash", "remove")])
        self.assertEqual(inline(runs[0]), (ROOT / "unraid-plugin/install.sh").read_text())
        self.assertEqual(inline(runs[1]), (ROOT / "unraid-plugin/remove.sh").read_text())

    def test_the_template_in_the_plugin_still_points_at_the_published_image(self):
        template = ET.fromstring(inline(self.files[TEMPLATE]))
        self.assertEqual(template.findtext("Repository"), "ghcr.io/d1-m4ss/internet-health:latest")

    def test_a_file_that_cannot_sit_in_cdata_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ("unraid-plugin", "src", "scripts", "docs/unraid"):
                (root / relative).mkdir(parents=True)
            for relative in ("unraid-plugin/internet-health.plg.tmpl", "unraid-plugin/CHANGES.txt", "unraid-plugin/install.sh"):
                (root / relative).write_text((ROOT / relative).read_text())
            (root / "unraid-plugin/remove.sh").write_text("echo ]]>\n")
            for relative in ("src/netprobe-unraid-notify.sh", "scripts/netprobe-notify.cron", "docs/unraid/internet-health.xml"):
                (root / relative).write_text((ROOT / relative).read_text())
            with self.assertRaisesRegex(build_plugin.BuildError, r"unraid-plugin/remove.sh contains '\]\]>'"):
                build_plugin.build(root)


class HostScriptTests(unittest.TestCase):
    """install.sh / remove.sh against a scratch folder standing in for /boot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.boot = Path(self.tmp.name) / "boot"
        self.calls = Path(self.tmp.name) / "update_cron.calls"
        self.fake = Path(self.tmp.name) / "update_cron"
        self.fake.write_text("#!/bin/sh\necho called >> '%s'\n" % self.calls)
        self.fake.chmod(0o755)
        self.plugdir = self.boot / "config/plugins/internet-health"
        self.plugdir.mkdir(parents=True)
        (self.plugdir / "internet-health.xml").write_text("<Container>shipped</Container>")
        self.user_template = self.boot / "config/plugins/dockerMan/templates-user/my-InternetHealth.xml"

    def run_script(self, name):
        env = dict(os.environ, INTERNET_HEALTH_BOOT=str(self.boot), INTERNET_HEALTH_UPDATE_CRON=str(self.fake))
        return subprocess.run(["bash", str(ROOT / "unraid-plugin" / name)], env=env, capture_output=True, text=True)

    def test_install_copies_the_template_and_reloads_cron(self):
        done = self.run_script("install.sh")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.user_template.read_text(), "<Container>shipped</Container>")
        self.assertEqual(self.calls.read_text(), "called\n")

    def test_install_never_overwrites_the_users_template(self):
        self.user_template.parent.mkdir(parents=True)
        self.user_template.write_text("<Container>my settings</Container>")
        done = self.run_script("install.sh")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.user_template.read_text(), "<Container>my settings</Container>")
        self.assertIn("left as it is", done.stdout)

    def test_remove_deletes_the_bridge_cron_and_plugin_folder_but_keeps_the_template(self):
        bridge = self.boot / "config/custom/netprobe-unraid-notify.sh"
        cron = self.boot / "config/plugins/dynamix/netprobe-notify.cron"
        for path in (bridge, cron):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        self.user_template.parent.mkdir(parents=True)
        self.user_template.write_text("<Container>mine</Container>")
        done = self.run_script("remove.sh")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([bridge.exists(), cron.exists(), self.plugdir.exists()], [False, False, False])
        self.assertEqual(self.user_template.read_text(), "<Container>mine</Container>")
        self.assertEqual(self.calls.read_text(), "called\n")


if __name__ == "__main__":
    unittest.main()
