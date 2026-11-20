# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The notes of a release: the section of CHANGELOG.md for the version of metadata.txt.

Usage: python .github/scripts/release_notes.py <tag> [file to write the notes to]

Stops, with the reason, when the tag is not "v" + the version of metadata.txt, when the
newest section of CHANGELOG.md is another version, when that section is not dated or lists
nothing, and when the changelog of metadata.txt lists other entries for the version, or lists
them under other headings.
"""

import configparser
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# "## 0.3.0 - 2026-11-20", "## 0.3.0 - unreleased"
SECTION = re.compile(r"^## (\d+\.\d+\.\d+)(?:\s+-\s+(.*))?$")
VERSION_LINE = re.compile(r"^(\d+\.\d+\.\d+):?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# "### Fixed" in CHANGELOG.md, "Fixed:" in the changelog of metadata.txt, which the Plugin
# Manager shows as plain text; the Romanian entries come first, under the Romanian headings
KINDS = ("Adăugat|Modificat|Învechit|Eliminat|Corectat|Securitate|"
         "Added|Changed|Deprecated|Removed|Fixed|Security")
HEADING = re.compile(rf"^(?:### ({KINDS})|({KINDS}):)$")


def sections(text: str) -> list:
    """[(version, what follows it in the heading, body lines)] of a changelog, in its order."""
    found = []
    for line in text.splitlines():
        match = SECTION.match(line.rstrip())
        if match:
            found.append((match.group(1), (match.group(2) or "").strip(), []))
        elif found:
            found[-1][2].append(line.rstrip())
    return found


def entries(lines) -> list:
    """[(heading, items)] of lines: the list items under each heading, each on one line and
    without a final period. Items before the first heading have the heading ""; a heading
    that lists nothing is left out."""
    groups = []
    open_item = False
    for line in lines:
        text = line.strip()
        heading = HEADING.match(text)
        if heading:
            groups.append((heading.group(1) or heading.group(2), []))
            open_item = False
        elif text.startswith(("- ", "* ")):
            if not groups:
                groups.append(("", []))
            groups[-1][1].append(text[2:].strip())
            open_item = True
        elif not text or text.startswith("#"):
            open_item = False
        elif open_item:
            # a wrapped line of the item above
            groups[-1][1][-1] += " " + text
    return [(heading, [item.rstrip(".") for item in items]) for heading, items in groups if items]


def metadata_changelog(value: str) -> dict:
    """{version: entries} of the changelog field of metadata.txt."""
    versions = {}
    lines = None
    for line in value.splitlines():
        match = VERSION_LINE.match(line.strip())
        if match:
            lines = versions.setdefault(match.group(1), [])
        elif lines is not None:
            lines.append(line)
    return {version: entries(lines) for version, lines in versions.items()}


def markdown(lines) -> str:
    """The body of a section as release notes: headings that list nothing are left out."""
    kept = []
    block = []

    def close():
        if not block or not block[0].startswith("#") or entries(block):
            kept.extend(block)
        del block[:]

    for line in lines:
        if line.startswith("#"):
            close()
        block.append(line)
    close()
    return "\n".join(kept).strip() + "\n"


def notes(tag: str, root: str = ROOT) -> str:
    """The notes of the release tagged tag, from the checkout at root."""
    parser = configparser.ConfigParser()
    parser.read(os.path.join(root, "metadata.txt"), encoding="utf-8")
    version = parser.get("general", "version")
    if tag != f"v{version}":
        raise SystemExit(f"The tag is {tag}, the version of metadata.txt is {version}: the "
                         f"tag of this version is v{version}.")
    with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as fp:
        found = sections(fp.read())
    if not found or found[0][0] != version:
        newest = found[0][0] if found else "none"
        raise SystemExit(f"The newest section of CHANGELOG.md is {newest}, the version of "
                         f"metadata.txt is {version}.")
    _version, dated, body = found[0]
    if not DATE.match(dated):
        raise SystemExit(f"The section {version} of CHANGELOG.md is headed '{dated}': write "
                         "the date of the release there, as YYYY-MM-DD.")
    listed = entries(body)
    if not listed:
        raise SystemExit(f"The section {version} of CHANGELOG.md lists nothing.")
    shown = metadata_changelog(parser.get("general", "changelog", fallback="")).get(version)
    if shown != listed:
        raise SystemExit(f"The changelog of metadata.txt, which the Plugin Manager shows, does "
                         f"not list the entries of CHANGELOG.md for {version} under the same "
                         "headings.")
    return markdown(body)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    text = notes(sys.argv[1])
    if len(sys.argv) == 3:
        with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
    else:
        sys.stdout.write(text)
