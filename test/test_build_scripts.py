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
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import helpers

# appended: first on the path, the plugin folder would make "cadex" import cadex.py
sys.path.append(helpers.PLUGIN_DIR)

from scripts import build_siruta, build_zip  # noqa: E402

RUNTIME_FILES = ("__init__.py", "cadex.py", "help_texts.py", "processing_provider/provider.py",
                 "services/fmt.py", "metadata.txt", "LICENSE", "README.md", "icons/cadex.svg",
                 "icons/dark/cgxml.svg", "icons/ATTRIBUTION.md", "data/siruta.db")
DEVELOPMENT_FILES = ("JOURNAL.md", ".gitignore", ".bandit", ".github/workflows/tests.yml",
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


class ReleaseZipTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cadex_zip_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in RUNTIME_FILES + DEVELOPMENT_FILES:
            write(self.root, rel)
        write(self.root, "metadata.txt", b"[general]\nversion=1.2.3\n")
        self.expected = sorted(f"cadex/{rel}" for rel in RUNTIME_FILES)
        self.zip_path = os.path.join(self.root, "dist", "cadex.1.2.3.zip")

    def build(self, *args):
        with mock.patch.object(build_zip, "ROOT", self.root), \
                contextlib.redirect_stdout(io.StringIO()):
            return build_zip.build(*args)

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
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
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
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
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
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
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
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
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
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        source = sqlite_file(os.path.join(base, "siruta.db"), SIRUTA_SOURCE)
        drive, rest = os.path.splitdrive(os.path.realpath(source))
        shared = "\\\\localhost\\" + drive[0] + "$" + rest
        if not os.path.isfile(shared):
            self.skipTest("the administrative share of the drive is not reachable")
        counts = build_siruta.build(shared, os.path.join(base, "out", "siruta.db"))
        self.assertEqual(counts, {"counties": 1, "admin_units": 1, "localities": 1})


if __name__ == "__main__":
    unittest.main()
