#!/usr/bin/env python3
"""Release helper for Internet Health (versions follow https://semver.org).

  release.py version                  print the version in the code (src/netprobe_extensions.py)
  release.py notes 1.0.0              print the CHANGELOG section of a released version (the GitHub Release text)
  release.py check-tag v1.0.0         exit 1 unless the tag matches the code's version and the CHANGELOG has its section
  release.py release 1.0.0 [--dry-run] [--no-tests] [--date YYYY-MM-DD]
        sets the version, turns "## Unreleased" of the CHANGELOG into "## 1.0.0 - date", commits "Release 1.0.0", tags v1.0.0,
        then starts the next development version (1.0.1-dev) in a second commit. Nothing is pushed: it prints the command.

The version lives in one place, `VERSION` in src/netprobe_extensions.py (the three files are all that runs in the container), and is shown
by GET /api/about and in the page footer. Publishing the container image is done by .github/workflows/release.yml when a tag v* is pushed.
"""
import argparse
from datetime import date as _date
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CODE = Path("src/netprobe_extensions.py")
CHANGELOG = Path("CHANGELOG.md")
VERSION_LINE = re.compile(r'^(VERSION = ")([^"]+)(")', re.M)
RELEASE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
DEV = re.compile(r"^(\d+)\.(\d+)\.(\d+)-dev$")
HEADING = re.compile(r"^## (Unreleased|(\d+\.\d+\.\d+))(?: [—-] (\d{4}-\d{2}-\d{2}))?[ \t]*$", re.M)


class ReleaseError(Exception):
    pass


def parse(version):
    match = RELEASE.match(version) or DEV.match(version)
    if not match:
        raise ReleaseError("%r is not a version like 1.2.3" % version)
    return tuple(int(x) for x in match.groups())


def read_version(root=ROOT):
    found = VERSION_LINE.search((Path(root) / CODE).read_text())
    if not found:
        raise ReleaseError("no VERSION = \"...\" line in %s" % CODE)
    parse(found.group(2))
    return found.group(2)


def write_version(version, root=ROOT):
    path = Path(root) / CODE
    text = path.read_text()
    if not VERSION_LINE.search(text):
        raise ReleaseError("no VERSION line to update")
    path.write_text(VERSION_LINE.sub(lambda m: m.group(1) + version + m.group(3), text, count=1))


def sections(text):
    """[(name, version or None, released date or None, body)] in file order; name is 'Unreleased' or the version."""
    marks = list(HEADING.finditer(text))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((mark.group(1), mark.group(2), mark.group(3), text[mark.end():end].strip("\n")))
    return out


def release_notes(text, version):
    for name, ver, _date_, body in sections(text):
        if ver == version:
            if not body.strip():
                raise ReleaseError("the CHANGELOG section of %s is empty" % version)
            return body.strip() + "\n"
    raise ReleaseError("the CHANGELOG has no section for %s" % version)


def last_released(text):
    versions = [parse(ver) for _n, ver, _d, _b in sections(text) if ver]
    return max(versions) if versions else None


def cut_changelog(text, version, day):
    """'## Unreleased' + its notes become '## <version> - <day>'; a fresh empty '## Unreleased' goes above."""
    found = [s for s in sections(text) if s[0] == "Unreleased"]
    if not found:
        raise ReleaseError("the CHANGELOG has no '## Unreleased' section")
    if not found[0][3].strip():
        raise ReleaseError("'## Unreleased' is empty: there is nothing to release")
    mark = next(m for m in HEADING.finditer(text) if m.group(1) == "Unreleased")
    return text[:mark.start()] + "## Unreleased\n\n## %s — %s" % (version, day) + text[mark.end():]


def next_dev(version):
    major, minor, patch = parse(version)[:3]
    return "%d.%d.%d-dev" % (major, minor, patch + 1)


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and result.returncode:
        raise ReleaseError("git %s failed: %s" % (" ".join(args), (result.stderr or result.stdout).strip()))
    return result.stdout.strip()


def check_tag(tag, root=ROOT):
    if not tag.startswith("v") or not RELEASE.match(tag[1:]):
        raise ReleaseError("tag %r must look like v1.2.3" % tag)
    version = tag[1:]
    if read_version(root) != version:
        raise ReleaseError("the code says VERSION = %s but the tag is %s: run scripts/release.py release %s" % (read_version(root), tag, version))
    release_notes((Path(root) / CHANGELOG).read_text(), version)
    return version


def release(version, root=ROOT, dry_run=False, run_tests=True, day=None):
    if not RELEASE.match(version):
        raise ReleaseError("a release version looks like 1.2.3 (got %r)" % version)
    root = Path(root)
    changelog = (root / CHANGELOG).read_text()
    previous = last_released(changelog)
    if previous and parse(version) <= previous:
        raise ReleaseError("%s is not newer than the last released version %s" % (version, ".".join(map(str, previous))))
    if git(root, "status", "--porcelain"):
        raise ReleaseError("the working tree is not clean: commit or stash first")
    if git(root, "tag", "--list", "v" + version):
        raise ReleaseError("the tag v%s already exists" % version)
    day = day or _date.today().isoformat()
    new_changelog = cut_changelog(changelog, version, day)
    follow = next_dev(version)
    plan = ["VERSION %s -> %s" % (read_version(root), version), "CHANGELOG: Unreleased -> %s (%s)" % (version, day),
            "commit 'Release %s', tag v%s" % (version, version), "VERSION -> %s, commit 'Start %s'" % (follow, follow)]
    if dry_run:
        return plan
    if run_tests:
        done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=root, capture_output=True, text=True)
        if done.returncode:
            sys.stderr.write((done.stdout + done.stderr)[-3000:])
            raise ReleaseError("the unit tests fail: not releasing")
    write_version(version, root)
    (root / CHANGELOG).write_text(new_changelog)
    git(root, "add", str(CODE), str(CHANGELOG))
    git(root, "commit", "-q", "-m", "Release %s" % version)
    git(root, "tag", "-a", "v" + version, "-m", "Internet Health %s" % version)
    write_version(follow, root)
    git(root, "add", str(CODE))
    git(root, "commit", "-q", "-m", "Start %s" % follow)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    sub.add_parser("notes").add_argument("version")
    sub.add_parser("check-tag").add_argument("tag")
    rel = sub.add_parser("release")
    rel.add_argument("version")
    rel.add_argument("--dry-run", action="store_true")
    rel.add_argument("--no-tests", action="store_true")
    rel.add_argument("--date")
    args = parser.parse_args(argv)
    try:
        if args.command == "version":
            print(read_version(args.root))
        elif args.command == "notes":
            sys.stdout.write(release_notes((Path(args.root) / CHANGELOG).read_text(), args.version))
        elif args.command == "check-tag":
            print("OK: %s" % check_tag(args.tag, args.root))
        else:
            for line in release(args.version, args.root, args.dry_run, not args.no_tests, args.date):
                print(("would: " if args.dry_run else "done:  ") + line)
            if not args.dry_run:
                print("\nNext: check `git log -3`, then publish with\n  git push origin main v%s\n(the tag starts the image build and the GitHub Release)" % args.version)
    except ReleaseError as exc:
        print("release.py: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
