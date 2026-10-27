#!/usr/bin/env python3
"""Builds the Unraid plugin file (internet-health.plg) from the files of this repository.

  build-plugin.py [--out dist/internet-health.plg] [--plugin-url URL]

The plugin version is the first '###YYYY.MM.DD' heading of unraid-plugin/CHANGES.txt (Unraid compares plugin versions as text, so dates
sort correctly; bump it whenever a file inside the plugin changes). The bridge script, its cron file, the Docker template and the install /
remove scripts are embedded, each with its SHA-256, so the plugin needs no download besides itself. See docs/UNRAID-PLUGIN.md.
"""
import argparse
import hashlib
from pathlib import Path
import re
import sys
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = Path("unraid-plugin/internet-health.plg.tmpl")
CHANGES = Path("unraid-plugin/CHANGES.txt")
PLUGIN_URL = "https://raw.githubusercontent.com/d1-m4ss/internet-health/main/unraid-plugin/internet-health.plg"
ICON_URL = "https://raw.githubusercontent.com/d1-m4ss/internet-health/main/docs/icon.png"
VERSION_HEADING = re.compile(r"^###(\d{4}\.\d{2}\.\d{2}[a-z]?)[ \t]*$", re.M)
PLACEHOLDER = re.compile(r"\{\{(VERSION|PLUGIN_URL|ICON_URL|CHANGES|(?:SHA256|INLINE):[^}]+)\}\}")


class BuildError(Exception):
    pass


def embedded(root, relative):
    """The file as it must appear inside CDATA: UTF-8 text with a final newline, no ']]>', no carriage returns."""
    data = (Path(root) / relative).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise BuildError("%s is not UTF-8 text" % relative)
    if "]]>" in text:
        raise BuildError("%s contains ']]>' and cannot sit inside CDATA" % relative)
    if "\r" in text:
        raise BuildError("%s has carriage returns: Unraid runs it on Linux" % relative)
    if not text.endswith("\n"):
        raise BuildError("%s does not end with a newline" % relative)
    return text


def plugin_version(changes):
    found = VERSION_HEADING.search(changes)
    if not found:
        raise BuildError("%s has no '###YYYY.MM.DD' heading" % CHANGES)
    return found.group(1)


def build(root=ROOT, plugin_url=PLUGIN_URL):
    root = Path(root)
    changes = (root / CHANGES).read_text(encoding="utf-8")
    version = plugin_version(changes)

    def fill(match):
        key = match.group(1)
        if key == "VERSION":
            return version
        if key == "PLUGIN_URL":
            return escape(plugin_url, {'"': "&quot;"})
        if key == "ICON_URL":
            return ICON_URL
        if key == "CHANGES":
            return escape(changes.strip())
        kind, relative = key.split(":", 1)
        text = embedded(root, relative)
        return hashlib.sha256(text.encode("utf-8")).hexdigest() if kind == "SHA256" else text

    return PLACEHOLDER.sub(fill, (root / TEMPLATE).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--out", default="dist/internet-health.plg")
    parser.add_argument("--plugin-url", default=PLUGIN_URL)
    args = parser.parse_args(argv)
    try:
        text = build(args.root, args.plugin_url)
    except BuildError as exc:
        print("build-plugin.py: %s" % exc, file=sys.stderr)
        return 1
    out = Path(args.out)
    out = out if out.is_absolute() else Path(args.root) / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("wrote %s (version %s)" % (out, plugin_version((Path(args.root) / CHANGES).read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
