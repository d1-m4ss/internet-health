"""Versioning and releases: one version in the code, a CHANGELOG that matches it, and the release script."""
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
import netprobe_extensions as npx  # noqa: E402

SAMPLE = """# Changelog

Intro text.

## Unreleased

- **New thing.** Does it.
- Fixed a bug.

## 0.2.0 — 2026-01-02

- Older notes.

## 0.1.0 — 2025-12-01

First public version.
"""


class RepositoryStateTests(unittest.TestCase):
    def test_the_version_in_the_code_is_a_valid_one_and_is_what_the_api_reports(self):
        self.assertRegex(npx.VERSION, r"^\d+\.\d+\.\d+(-dev)?$")
        self.assertEqual(release.read_version(ROOT), npx.VERSION)
        from flask import Flask
        with tempfile.TemporaryDirectory() as tmp:
            from fixture_db import make_database
            make_database(Path(tmp) / "netprobe.sqlite")
            app = Flask(__name__)
            npx.install_routes(app, npx.Extension(str(Path(tmp) / "netprobe.sqlite"), tmp))
            self.assertEqual(app.test_client().get("/api/about").get_json(), {"name": "Internet Pulse", "version": npx.VERSION})

    def test_the_changelog_matches_the_version(self):
        text = (ROOT / "CHANGELOG.md").read_text()
        names = [name for name, _v, _d, _b in release.sections(text)]
        self.assertEqual(names.count("Unreleased"), 1, "exactly one Unreleased section")
        self.assertEqual(names[0], "Unreleased", "Unreleased comes first")
        if npx.VERSION.endswith("-dev"):
            newest = release.last_released(text)
            self.assertTrue(newest is None or newest < release.parse(npx.VERSION), "the development version is above the last released one")
        else:
            self.assertEqual(release.release_notes(text, npx.VERSION).strip() != "", True, "a released version has notes")

    def test_the_page_footer_and_the_image_know_about_the_version(self):
        page = (ROOT / "src" / "index.html").read_text()
        self.assertIn('id="footerVersion"', page)
        self.assertIn("/api/about", page)
        self.assertIn("ARG VERSION", (ROOT / "Dockerfile").read_text())


class ChangelogTests(unittest.TestCase):
    def test_sections_and_notes(self):
        self.assertEqual([(n, v, d) for n, v, d, _b in release.sections(SAMPLE)],
                         [("Unreleased", None, None), ("0.2.0", "0.2.0", "2026-01-02"), ("0.1.0", "0.1.0", "2025-12-01")])
        self.assertEqual(release.release_notes(SAMPLE, "0.2.0"), "- Older notes.\n")
        self.assertEqual(release.release_notes(SAMPLE, "0.1.0"), "First public version.\n")
        with self.assertRaisesRegex(release.ReleaseError, "no section for 9.9.9"):
            release.release_notes(SAMPLE, "9.9.9")

    def test_cutting_a_release_keeps_everything_and_leaves_a_fresh_unreleased(self):
        cut = release.cut_changelog(SAMPLE, "0.3.0", "2026-02-03")
        self.assertIn("## Unreleased\n\n## 0.3.0 — 2026-02-03\n\n- **New thing.**", cut)
        self.assertEqual(release.release_notes(cut, "0.3.0"), "- **New thing.** Does it.\n- Fixed a bug.\n")
        self.assertEqual(release.release_notes(cut, "0.2.0"), "- Older notes.\n")
        self.assertTrue(cut.startswith("# Changelog\n\nIntro text.\n"))
        self.assertEqual(release.cut_changelog(cut.replace("## Unreleased\n\n## 0.3.0", "## Unreleased\n- x\n\n## 0.3.0"), "0.3.1", "2026-02-04").count("## Unreleased"), 1)

    def test_nothing_to_release_is_refused(self):
        empty = SAMPLE.replace("- **New thing.** Does it.\n- Fixed a bug.\n", "")
        with self.assertRaisesRegex(release.ReleaseError, "nothing to release"):
            release.cut_changelog(empty, "0.3.0", "2026-02-03")
        with self.assertRaisesRegex(release.ReleaseError, "no '## Unreleased'"):
            release.cut_changelog("# Changelog\n\n## 0.1.0 — 2025-12-01\n\n- x\n", "0.2.0", "2026-01-01")

    def test_versions(self):
        self.assertEqual(release.parse("1.2.3"), (1, 2, 3))
        self.assertEqual(release.parse("1.2.3-dev"), (1, 2, 3))
        for bad in ("1.2", "v1.2.3", "1.2.3-rc1", "a.b.c", "", "1.2.3.4"):
            with self.assertRaises(release.ReleaseError, msg=bad):
                release.parse(bad)
        self.assertEqual(release.next_dev("1.2.3"), "1.2.4-dev")
        self.assertEqual(release.next_dev("1.9.9"), "1.9.10-dev")
        self.assertEqual(release.last_released(SAMPLE), (0, 2, 0))


class ReleaseFlowTests(unittest.TestCase):
    """The whole flow in a throw-away git repository."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "netprobe_extensions.py").write_text('import os\n\nVERSION = "0.3.0-dev"  # keep this comment\n\nX = 1\n')
        (self.root / "CHANGELOG.md").write_text(SAMPLE)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-q", "-m", "start"]):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, env=self.env, capture_output=True)
        os.environ.update({k: self.env[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")})
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")])

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_a_release_makes_two_commits_a_tag_and_the_next_dev_version(self):
        plan = release.release("0.3.0", self.root, run_tests=False, day="2026-03-04")
        self.assertEqual(len(plan), 4)
        self.assertEqual(self.git("log", "--format=%s", "-3"), "Start 0.3.1-dev\nRelease 0.3.0\nstart")
        self.assertEqual(self.git("tag", "--list"), "v0.3.0")
        self.assertEqual(self.git("show", "v0.3.0:src/netprobe_extensions.py").splitlines()[2], 'VERSION = "0.3.0"  # keep this comment')
        self.assertEqual(release.read_version(self.root), "0.3.1-dev")
        self.assertIn("keep this comment", (self.root / "src" / "netprobe_extensions.py").read_text())
        changelog = (self.root / "CHANGELOG.md").read_text()
        self.assertIn("## Unreleased\n\n## 0.3.0 — 2026-03-04", changelog)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.git("checkout", "-q", "v0.3.0")  # the tag points at the release commit, where the version and the notes agree
        self.assertEqual(release.check_tag("v0.3.0", self.root), "0.3.0")

    def test_dry_run_changes_nothing(self):
        plan = release.release("0.3.0", self.root, dry_run=True, day="2026-03-04")
        self.assertIn("VERSION 0.3.0-dev -> 0.3.0", plan[0])
        self.assertEqual((self.git("tag", "--list"), self.git("log", "--format=%s")), ("", "start"))
        self.assertEqual(release.read_version(self.root), "0.3.0-dev")

    def test_refusals(self):
        with self.assertRaisesRegex(release.ReleaseError, "not newer"):
            release.release("0.2.0", self.root, run_tests=False)
        with self.assertRaisesRegex(release.ReleaseError, "looks like 1.2.3"):
            release.release("1.0", self.root, run_tests=False)
        (self.root / "dirty.txt").write_text("x")
        with self.assertRaisesRegex(release.ReleaseError, "not clean"):
            release.release("0.3.0", self.root, run_tests=False)
        (self.root / "dirty.txt").unlink()
        release.release("0.3.0", self.root, run_tests=False, day="2026-03-04")
        with self.assertRaisesRegex(release.ReleaseError, "not newer"):
            release.release("0.3.0", self.root, run_tests=False)

    def test_failing_tests_stop_the_release(self):
        (self.root / "tests" / "test_fail.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('no')\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "failing test")
        with self.assertRaisesRegex(release.ReleaseError, "unit tests fail"):
            release.release("0.3.0", self.root, run_tests=True)
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(release.read_version(self.root), "0.3.0-dev")

    def test_check_tag_rejects_a_mismatch(self):
        for tag, message in (("0.3.0", "must look like"), ("v0.3.0", "VERSION = 0.3.0-dev"), ("v0.3.0-rc1", "must look like")):
            with self.assertRaisesRegex(release.ReleaseError, message):
                release.check_tag(tag, self.root)

    def test_command_line(self):
        def run(*args):
            return subprocess.run([sys.executable, str(ROOT / "scripts" / "release.py"), "--root", str(self.root), *args], capture_output=True, text=True)
        self.assertEqual(run("version").stdout.strip(), "0.3.0-dev")
        self.assertEqual(run("notes", "0.2.0").stdout, "- Older notes.\n")
        bad = run("notes", "9.9.9")
        self.assertEqual((bad.returncode, bad.stdout), (1, ""))
        self.assertIn("no section for 9.9.9", bad.stderr)
        self.assertEqual(run("check-tag", "v0.3.0").returncode, 1)


if __name__ == "__main__":
    unittest.main()
