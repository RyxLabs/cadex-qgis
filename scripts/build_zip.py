# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build cadex.<version>.zip for plugins.qgis.org: one top-level cadex/ folder holding the
files on the allow-list. Any other file outside the development folders stops the build.

Usage: python scripts/build_zip.py [output folder, default dist/]

A build into the plugin's dist/ folder removes the zips of older versions there; any other
output folder keeps its files.

One commit gives the same zip on Windows and on Linux, as long as both Pythons' zlib compress
alike: text files are written with LF line endings whatever the working tree holds, and every
entry carries the time of the commit.
"""

import configparser
import hashlib
import os
import stat
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "cadex"

# skipped at any depth, like every name starting with "."
SKIP_DIRS = {"__pycache__", "test", "scripts", "docs", "libs", "dist", "venv"}
SKIP_FILES = {"JOURNAL.md", "CHANGELOG.md", "SECURITY.md"}
# besides every .py file and the .svg files under icons/
SHIPPED = {"metadata.txt", "LICENSE", "README.md", "icons/ATTRIBUTION.md", "data/siruta.db"}
REQUIRED = ("__init__.py", "metadata.txt", "LICENSE", "icons/cadex.svg", "data/siruta.db",
            "processing_provider/provider.py", "help_texts.py")
# plugins.qgis.org flags a .py entry with an execute bit
ENTRY_MODE = (stat.S_IFREG | 0o644) << 16
# git's autocrlf checks these out with CRLF on Windows
TEXT_SUFFIXES = (".py", ".svg", ".md", ".txt")
TEXT_FILES = {"LICENSE"}
# the earliest time a zip entry can hold, for a build outside a git checkout
NO_COMMIT_TIME = (1980, 1, 1, 0, 0, 0)


def plugin_version() -> str:
    parser = configparser.ConfigParser()
    parser.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    return parser.get("general", "version")


def hidden(folder: str, name: str) -> bool:
    # dot files, Office lock files (~$raport.xlsx), and files Windows marks hidden or system,
    # such as desktop.ini and Thumbs.db
    if name.startswith((".", "~$")):
        return True
    attributes = getattr(os.stat(os.path.join(folder, name)), "st_file_attributes", 0)
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def shipped(rel: str) -> bool:
    return (rel.endswith(".py") or rel in SHIPPED
            or (rel.startswith("icons/") and rel.endswith(".svg")))


def contents(root: str) -> tuple:
    """(files the zip ships, files that are neither shipped nor skipped), as paths relative to
    root with "/"."""
    ship, stray = [], []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not hidden(folder, d))
        for name in sorted(files):
            rel = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
            if rel in SKIP_FILES or hidden(folder, name):
                continue
            (ship if shipped(rel) else stray).append(rel)
    return ship, stray


def entry_bytes(root: str, rel: str) -> bytes:
    """The bytes the zip holds for a shipped file: text files with LF line endings."""
    with open(os.path.join(root, *rel.split("/")), "rb") as fp:
        data = fp.read()
    if rel.endswith(TEXT_SUFFIXES) or rel in TEXT_FILES:
        data = data.replace(b"\r\n", b"\n")
    return data


def entry_time(root: str) -> tuple:
    """The modification time of every entry, in UTC: the time of the commit checked out in
    root, so each release has its own. If all releases had the same time, unpacking over an
    older copy of the plugin could leave an edited .py file of unchanged size with an
    unchanged modification time, and Python would keep using its stale .pyc."""
    if not os.path.exists(os.path.join(root, ".git")):
        return NO_COMMIT_TIME
    try:
        seconds = subprocess.run(["git", "-C", root, "log", "-1", "--format=%ct"],
                                 capture_output=True, text=True, check=True).stdout
        return time.gmtime(int(seconds))[:6]
    except (OSError, ValueError, subprocess.CalledProcessError):
        return NO_COMMIT_TIME


def build(dist: str = None) -> str:
    ship, stray = contents(ROOT)
    if stray:
        raise SystemExit("Not on the allow-list in scripts/build_zip.py, remove or add them:\n  "
                         + "\n  ".join(stray))
    missing = [rel for rel in REQUIRED if rel not in ship]
    if missing:
        raise SystemExit("Missing from the plugin: " + ", ".join(missing))

    own_dist = os.path.join(ROOT, "dist")
    dist = os.path.abspath(dist or own_dist)
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, f"{PACKAGE}.{plugin_version()}.zip")
    stamp = entry_time(ROOT)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in ship:
            info = zipfile.ZipInfo(f"{PACKAGE}/{rel}", stamp)
            info.external_attr = ENTRY_MODE
            # 3 is Unix; left alone, zipfile records the system of the build machine
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, entry_bytes(ROOT, rel))
    if same_folder(dist, own_dist):
        for name in os.listdir(dist):
            if (name.startswith(f"{PACKAGE}.") and name.endswith(".zip")
                    and os.path.join(dist, name) != out):
                os.remove(os.path.join(dist, name))
    size_kb = os.path.getsize(out) / 1024
    with open(out, "rb") as fp:
        digest = hashlib.sha256(fp.read()).hexdigest()
    dated = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} UTC".format(*stamp)
    print(f"{out}\n{len(ship)} files, {size_kb:.0f} KB, entries dated {dated}\n"
          f"SHA-256 {digest}")
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else None)
