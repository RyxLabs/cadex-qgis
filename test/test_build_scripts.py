# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release zip and the SIRUTA database builder, run on throwaway folders. No
QgsApplication."""

import contextlib
import io
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import helpers

# appended: first on the path, the plugin folder would make "cadex" import cadex.py
sys.path.append(helpers.PLUGIN_DIR)

from scripts import build_siruta, build_zip  # noqa: E402


def remove_tree(path):
    """Remove a test folder, also the files git writes read-only for its objects, which
    shutil.rmtree cannot remove on Windows."""
    def writable(function, name, _error):
        try:
            os.chmod(name, stat.S_IWRITE)
            function(name)
        except OSError:
            pass

    if not os.path.exists(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


RUNTIME_FILES = ("__init__.py", "cadex.py", "help_texts.py", "processing_provider/provider.py",
                 "services/fmt.py", "metadata.txt", "LICENSE", "README.md", "icons/cadex.svg",
                 "icons/dark/cgxml.svg", "icons/ATTRIBUTION.md", "data/siruta.db")
DEVELOPMENT_FILES = ("JOURNAL.md", "CHANGELOG.md", "SECURITY.md", ".gitignore", ".bandit",
                     ".github/workflows/tests.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
                     ".claude/settings.local.json", "test/test_x.py", "test/data/a.cgxml",
                     "scripts/build_zip.py", "docs/media/demo.gif",
                     "services/__pycache__/fmt.cpython-312.pyc", "libs/ezdxf/__init__.py",
                     "dist/cadex.0.9.0.zip")


def write(root, rel, data=b"x"):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(data)
    return path


def entries(path):
    with zipfile.ZipFile(path) as zf:
        return sorted(zf.namelist())


class ReleaseZipTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cadex_zip_")
        self.addCleanup(remove_tree, self.root)
        for rel in RUNTIME_FILES + DEVELOPMENT_FILES:
            write(self.root, rel)
        write(self.root, "metadata.txt", b"[general]\nversion=1.2.3\n")
        self.expected = sorted(f"cadex/{rel}" for rel in RUNTIME_FILES)
        self.zip_path = os.path.join(self.root, "dist", "cadex.1.2.3.zip")

    def build(self, *args, root=None):
        with mock.patch.object(build_zip, "ROOT", root or self.root), \
                contextlib.redirect_stdout(io.StringIO()):
            return build_zip.build(*args)


class ReleaseZipTests(ReleaseZipTest):

    def test_the_zip_holds_the_runtime_files_in_one_cadex_folder(self):
        self.assertEqual(self.build(), self.zip_path)
        self.assertEqual(entries(self.zip_path), self.expected)
        # the zip of an older version is removed
        self.assertEqual(os.listdir(os.path.dirname(self.zip_path)), ["cadex.1.2.3.zip"])

    def test_hidden_files_and_office_lock_files_stay_out(self):
        for rel in (".DS_Store", "~$raport.xlsx", "services/.secrets.baseline",
                    "dialogs/.~lock.raport.xlsx#", ".venv/lib/site.py"):
            write(self.root, rel)
        self.assertEqual(entries(self.build()), self.expected)

    @unittest.skipUnless(sys.platform == "win32", "Windows file attributes")
    def test_files_windows_marks_hidden_stay_out(self):
        import ctypes
        for rel in ("desktop.ini", "icons/Thumbs.db"):
            path = write(self.root, rel)
            self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(path, 0x2 | 0x4))
        self.assertEqual(entries(self.build()), self.expected)

    def test_a_file_outside_the_allow_list_stops_the_build_and_is_named(self):
        stray = ("cgxml_livrare.gpkg", "symbology-style.db", "services/notes.txt", "demo.qgz")
        for rel in stray:
            write(self.root, rel)
        with self.assertRaises(SystemExit) as raised:
            self.build()
        for rel in stray:
            self.assertIn(rel, str(raised.exception))
        self.assertFalse(os.path.exists(self.zip_path))

    def test_a_missing_required_file_stops_the_build_without_a_zip(self):
        os.remove(os.path.join(self.root, "LICENSE"))
        with self.assertRaises(SystemExit) as raised:
            self.build()
        self.assertIn("LICENSE", str(raised.exception))
        self.assertFalse(os.path.exists(self.zip_path))

    def test_entries_carry_the_same_permissions_on_every_build_machine(self):
        # plugins.qgis.org flags a .py entry with an execute bit
        os.chmod(os.path.join(self.root, "cadex.py"), 0o755)
        with zipfile.ZipFile(self.build()) as zf:
            modes = {info.filename: oct(info.external_attr >> 16) for info in zf.infolist()}
        self.assertEqual(set(modes.values()), {oct(0o100644)}, modes)

    def test_a_zip_built_into_another_folder_removes_no_other_zip(self):
        out = tempfile.mkdtemp(prefix="cadex_zip_out_")
        self.addCleanup(remove_tree, out)
        kept = write(out, "cadex.0.1.0.zip", b"a release kept on purpose")
        path = self.build(out)
        self.assertEqual(path, os.path.join(out, "cadex.1.2.3.zip"))
        self.assertEqual(entries(path), self.expected)
        self.assertEqual(sorted(os.listdir(out)), ["cadex.0.1.0.zip", "cadex.1.2.3.zip"])
        with open(kept, "rb") as fp:
            self.assertEqual(fp.read(), b"a release kept on purpose")
        self.assertEqual(os.listdir(os.path.join(self.root, "dist")), ["cadex.0.9.0.zip"])

    def test_dist_given_by_its_path_keeps_only_the_new_zip(self):
        self.assertEqual(self.build(os.path.join(self.root, "dist")), self.zip_path)
        self.assertEqual(os.listdir(os.path.dirname(self.zip_path)), ["cadex.1.2.3.zip"])


class ReleaseZipBytesTests(ReleaseZipTest):
    """The zip of one commit, built from working trees that differ as a Linux and a Windows
    checkout of it do."""

    def checkout(self, root, line_end, modified):
        """The runtime files in root, text files with line_end, every file last modified at
        modified."""
        for rel in RUNTIME_FILES:
            if rel.endswith(".db"):
                data = b"SQLite\r\nformat 3"
            else:
                text = b"[general]\nversion=1.2.3\n" if rel == "metadata.txt" else b"one\n\ntwo\n"
                data = text.replace(b"\n", line_end)
            os.utime(write(root, rel, data), (modified, modified))
        return root

    def test_text_files_are_written_with_lf_whatever_the_working_tree_holds(self):
        self.checkout(self.root, b"\r\n", 1000000000)
        with zipfile.ZipFile(self.build()) as zf:
            for rel in RUNTIME_FILES:
                if not rel.endswith(".db"):
                    self.assertNotIn(b"\r", zf.read(f"cadex/{rel}"), rel)
            self.assertEqual(zf.read("cadex/cadex.py"), b"one\n\ntwo\n")
            # a database is not text
            self.assertEqual(zf.read("cadex/data/siruta.db"), b"SQLite\r\nformat 3")

    def test_a_windows_and_a_linux_checkout_give_the_same_zip(self):
        other = tempfile.mkdtemp(prefix="cadex_zip_")
        self.addCleanup(remove_tree, other)
        self.checkout(self.root, b"\n", 1000000000)
        self.checkout(other, b"\r\n", 1500000000)
        zips = []
        for root in (self.root, other):
            with open(self.build(root=root), "rb") as fp:
                zips.append(fp.read())
        self.assertEqual(zips[0], zips[1])

    def test_the_zip_names_the_same_system_on_every_build_machine(self):
        with zipfile.ZipFile(self.build()) as zf:
            self.assertEqual({info.create_system for info in zf.infolist()}, {3})

    def test_outside_a_git_checkout_no_entry_carries_a_time_of_the_build_machine(self):
        with zipfile.ZipFile(self.build()) as zf:
            self.assertEqual({info.date_time for info in zf.infolist()}, {(1980, 1, 1, 0, 0, 0)})

    @unittest.skipUnless(shutil.which("git"), "git missing")
    def test_in_a_git_checkout_every_entry_carries_the_time_of_the_commit(self):
        moment = "2026-03-04T05:06:08Z"
        environment = dict(os.environ, GIT_AUTHOR_DATE=moment, GIT_COMMITTER_DATE=moment)
        for command in (["init", "--quiet"],
                        ["-c", "user.name=CADex tests", "-c", "user.email=tests@example.com",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty",
                         "--message", "x"]):
            subprocess.run(["git", "-C", self.root] + command, check=True, env=environment,
                           capture_output=True)
        with zipfile.ZipFile(self.build()) as zf:
            self.assertEqual({info.date_time for info in zf.infolist()}, {(2026, 3, 4, 5, 6, 8)})
            self.assertEqual(sorted(zf.namelist()), self.expected)


CHANGELOG = """# Changelog

## 1.2.3 - {dated}

### Added

- A report of the imobile without
  coordinates.

### Changed

### Fixed

- The area of an imobil with a hole.

## 1.2.2 - 2026-01-15

- An older entry.
"""
METADATA = """[general]
version=1.2.3
changelog=1.2.3:
    Added:
    * A report of the imobile without coordinates
    Fixed:
    * The area of an imobil with a hole
    1.2.2:
    * An older entry
"""


class ReleaseNotesTests(unittest.TestCase):
    """The notes of a release, from throwaway checkouts."""

    release_notes = helpers.github_script("release_notes")

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cadex_notes_")
        self.addCleanup(remove_tree, self.root)
        self.checkout()

    def checkout(self, changelog=CHANGELOG, metadata=METADATA, dated="2026-02-20"):
        write(self.root, "CHANGELOG.md", changelog.format(dated=dated).encode("utf-8"))
        write(self.root, "metadata.txt", metadata.encode("utf-8"))

    def refusal(self, tag="v1.2.3"):
        with self.assertRaises(SystemExit) as raised:
            self.release_notes.notes(tag, self.root)
        return str(raised.exception)

    def test_the_notes_are_the_section_of_the_version_without_its_empty_headings(self):
        self.assertEqual(
            self.release_notes.notes("v1.2.3", self.root),
            "### Added\n\n- A report of the imobile without\n  coordinates.\n\n"
            "### Fixed\n\n- The area of an imobil with a hole.\n")

    def test_a_tag_that_is_not_the_version_of_the_plugin_is_refused(self):
        for tag in ("v1.2.2", "1.2.3", "v1.2.3-rc1"):
            self.assertIn("v1.2.3", self.refusal(tag), tag)

    def test_a_version_that_is_not_the_newest_section_is_refused(self):
        self.checkout(metadata=METADATA.replace("version=1.2.3", "version=1.2.4"))
        self.assertIn("The newest section of CHANGELOG.md is 1.2.3", self.refusal("v1.2.4"))

    def test_a_section_without_the_date_of_the_release_is_refused(self):
        self.checkout(dated="unreleased")
        self.assertIn("headed 'unreleased'", self.refusal())

    def test_a_section_that_lists_nothing_is_refused(self):
        self.checkout(changelog="## 1.2.3 - {dated}\n\n### Added\n\n## 1.2.2 - 2026-01-15\n")
        self.assertIn("lists nothing", self.refusal())

    def test_entries_the_plugin_manager_would_not_show_are_refused(self):
        self.checkout(metadata=METADATA.replace("    * The area of an imobil with a hole\n", ""))
        self.assertIn("changelog of metadata.txt", self.refusal())

    def test_entries_the_plugin_manager_would_show_under_another_heading_are_refused(self):
        self.checkout(metadata=METADATA.replace("    Fixed:\n", ""))
        self.assertIn("under the same headings", self.refusal())


def sqlite_file(path, script):
    db = sqlite3.connect(path)
    db.executescript(script)
    db.commit()
    db.close()
    return path


SIRUTA_SOURCE = """
    CREATE TABLE counties (county_id INTEGER, county_code TEXT, name TEXT);
    CREATE TABLE admin_units (admin_unit_id INTEGER, siruta INTEGER, county_id INTEGER,
                              name TEXT);
    CREATE TABLE localities (siruta INTEGER, admin_unit_id INTEGER, name TEXT);
    INSERT INTO counties VALUES (1, 'AA', 'Județul A');
    INSERT INTO admin_units VALUES (7, 1001, 1, 'Comuna A');
    INSERT INTO localities VALUES (1002, 7, 'Satul A'), (1003, 7, 'Neidentificata');
"""
PREVIOUS_TARGET = "CREATE TABLE counties (name TEXT); INSERT INTO counties VALUES ('Județul A');"


class SirutaBuildTests(unittest.TestCase):

    def test_a_source_in_a_folder_with_hash_and_percent_in_its_name_is_read(self):
        base = tempfile.mkdtemp(prefix="cadex_siruta_")
        self.addCleanup(remove_tree, base)
        folder = os.path.join(base, "SIRUTA #2 100%")
        os.makedirs(folder)
        source = sqlite_file(os.path.join(folder, "siruta.db"), SIRUTA_SOURCE)
        target = os.path.join(base, "out", "siruta.db")
        counts = build_siruta.build(source, target)
        self.assertEqual(counts, {"counties": 1, "admin_units": 1, "localities": 1})
        built = sqlite3.connect(target)
        self.addCleanup(built.close)
        self.assertEqual(built.execute("SELECT uat, name FROM localities").fetchall(),
                         [(1001, "Satul A")])

    def test_a_source_without_the_siruta_tables_keeps_the_previous_database(self):
        base = tempfile.mkdtemp(prefix="cadex_siruta_")
        self.addCleanup(remove_tree, base)
        source = sqlite_file(os.path.join(base, "other.db"), "CREATE TABLE parcels (id INTEGER);")
        target = sqlite_file(os.path.join(base, "siruta.db"), PREVIOUS_TARGET)
        with self.assertRaises(sqlite3.OperationalError):
            build_siruta.build(source, target)
        self.assertEqual(sorted(os.listdir(base)), ["other.db", "siruta.db"])
        kept = sqlite3.connect(target)
        self.addCleanup(kept.close)
        self.assertEqual(kept.execute("SELECT name FROM counties").fetchall(), [("Județul A",)])

    @unittest.skipUnless(sys.platform == "win32", "Windows does not replace a file held open")
    def test_a_database_open_in_another_program_is_kept_and_no_partial_file_is_left(self):
        base = tempfile.mkdtemp(prefix="cadex_siruta_")
        self.addCleanup(remove_tree, base)
        source = sqlite_file(os.path.join(base, "source.db"), SIRUTA_SOURCE)
        target = sqlite_file(os.path.join(base, "siruta.db"), PREVIOUS_TARGET)
        # as a QGIS layer or an SQLite browser holds it
        held = sqlite3.connect(target)
        self.addCleanup(held.close)
        self.assertEqual(held.execute("SELECT name FROM counties").fetchall(), [("Județul A",)])
        with self.assertRaises(PermissionError):
            build_siruta.build(source, target)
        # a leftover siruta.db.partial would stop build_zip
        self.assertEqual(sorted(os.listdir(base)), ["siruta.db", "source.db"])
        self.assertEqual(held.execute("SELECT name FROM counties").fetchall(), [("Județul A",)])


class SirutaBuildShareTests(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "win32", "network paths in the form \\\\server\\share")
    def test_a_source_on_a_network_share_is_read(self):
        base = tempfile.mkdtemp(prefix="cadex_siruta_")
        self.addCleanup(remove_tree, base)
        source = sqlite_file(os.path.join(base, "siruta.db"), SIRUTA_SOURCE)
        drive, rest = os.path.splitdrive(os.path.realpath(source))
        shared = "\\\\localhost\\" + drive[0] + "$" + rest
        if not os.path.isfile(shared):
            self.skipTest("the administrative share of the drive is not reachable")
        counts = build_siruta.build(shared, os.path.join(base, "out", "siruta.db"))
        self.assertEqual(counts, {"counties": 1, "admin_units": 1, "localities": 1})


if __name__ == "__main__":
    unittest.main()
