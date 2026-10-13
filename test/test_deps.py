# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Installing the optional packages: finding the Python of a QGIS installation, the pip
command line and the install question. pip itself never runs."""

import os
import sys
import tempfile
import unittest
from unittest import mock

import helpers

helpers.start_qgis(gui=True)

from qgis.PyQt.QtWidgets import QMessageBox  # noqa: E402  # type: ignore

from cadex.dialogs import _ui_helpers  # noqa: E402
from cadex.services import deps  # noqa: E402

ABSENT = "cadex_absent_package"


class PipResult:

    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@unittest.skipUnless(sys.platform == "win32", "the Windows installation layouts")
class DepsPythonTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_deps_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def touch(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
        return path

    def find(self, executable, prefix):
        with mock.patch.object(sys, "executable", executable), \
                mock.patch.object(sys, "prefix", prefix):
            return deps.find_python()

    def test_the_python_of_a_standalone_or_osgeo4w_installation(self):
        qgis = self.touch("bin", "qgis-ltr-bin.exe")
        python = self.touch("apps", "Python312", "python.exe")
        self.assertEqual(self.find(qgis, os.path.dirname(python)), python)

    def test_the_newest_python_in_apps_when_the_prefix_holds_none(self):
        qgis = self.touch("bin", "qgis-bin.exe")
        self.touch("apps", "Python39", "python.exe")
        python = self.touch("apps", "Python312", "python.exe")
        self.assertEqual(self.find(qgis, os.path.join(self.tmp, "altceva")), python)

    def test_the_python_of_a_build_inside_apps(self):
        qgis = self.touch("apps", "qgis", "bin", "qgis.exe")
        python = self.touch("apps", "Python312", "python.exe")
        self.assertEqual(self.find(qgis, os.path.join(self.tmp, "altceva")), python)

    def test_no_python_found(self):
        # qgis.exe itself would start a second QGIS instead of pip
        qgis = self.touch("bin", "qgis-bin.exe")
        self.assertIsNone(self.find(qgis, os.path.join(self.tmp, "altceva")))

    def test_a_python_installed_next_to_the_qgis_folder_is_not_used(self):
        # pip would put wheels built for that Python's version into cadex_libs
        qgis = self.touch("QGIS 3.40", "bin", "qgis-bin.exe")
        self.touch("Python313", "python.exe")
        self.assertIsNone(self.find(qgis, os.path.join(self.tmp, "altceva")))


class DepsInstallTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_libs_")
        self.libs = os.path.join(self.tmp, "cadex_libs")
        self.pip_calls = []
        self.questions = []
        self.messages = []
        self.patches = [
            mock.patch.object(deps, "LIBS_DIR", self.libs),
            mock.patch.object(deps, "find_python", lambda: "python.exe"),
            mock.patch.object(QMessageBox, "warning", self.message),
            mock.patch.object(QMessageBox, "information", self.message),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        if self.libs in sys.path:
            sys.path.remove(self.libs)
        helpers.remove_folder(self.tmp)

    def message(self, _parent, title, text, *_args):
        self.messages.append((title, text))

    def answer(self, yes):
        def ask(_parent, _title, message, yes_text, no_text, default_yes=False):
            self.questions.append((message, yes_text, no_text, default_yes))
            return yes
        return mock.patch.object(_ui_helpers, "ask", ask)

    def pip(self, result):
        def run(args, timeout=180):
            self.pip_calls.append(args)
            return result
        return mock.patch.object(deps, "_run_pip", run)

    def test_pip_installs_the_tested_versions_into_the_cadex_folder(self):
        with self.pip(PipResult(0)):
            ok, error = deps._pip_install("python.exe", *deps.PACKAGES["ezdxf"])
        self.assertEqual((ok, error), (True, ""))
        [args] = self.pip_calls
        self.assertEqual(args[:4], ["python.exe", "-m", "pip", "install"])
        self.assertEqual(args[args.index("--target") + 1], self.libs)
        # numpy comes with QGIS and must not be replaced
        self.assertIn("--no-deps", args)
        # a package folder left damaged by an earlier install is replaced, not kept
        self.assertIn("--upgrade", args)
        specs = args[args.index("--no-deps") + 1:]
        self.assertEqual([spec.split("==")[0] for spec in specs],
                         ["ezdxf", "pyparsing", "fonttools", "typing_extensions"])
        for spec in specs:
            self.assertRegex(spec, r"^[a-z_]+==\d+\.\d+\.\d+$")

    def test_the_install_question_defaults_to_cancel_and_a_no_installs_nothing(self):
        with self.answer(False), self.pip(PipResult(0)):
            self.assertFalse(deps.ensure(ABSENT))
        [(message, yes_text, no_text, default_yes)] = self.questions
        self.assertIn(f"'{ABSENT}'", message)
        self.assertEqual((yes_text, no_text, default_yes), ("Instalează", "Anulează", False))
        self.assertEqual(self.pip_calls, [])
        self.assertEqual(self.messages, [])

    def test_a_failed_install_shows_pips_error_and_the_manual_command(self):
        with self.answer(True), self.pip(PipResult(1, "No matching distribution found")):
            self.assertFalse(deps.ensure(ABSENT))
        [(_title, text)] = self.messages
        self.assertIn(f"Pachetul '{ABSENT}' nu a putut fi instalat.", text)
        self.assertIn(f"python -m pip install {ABSENT}", text)
        self.assertIn("No matching distribution found", text)

    def test_an_install_that_still_does_not_import_names_the_folder_and_the_reason(self):
        with self.answer(True), self.pip(PipResult(0)):
            self.assertFalse(deps.ensure(ABSENT))
        [(_title, text)] = self.messages
        self.assertIn(f"'{self.libs}'", text)
        self.assertIn(f"No module named '{ABSENT}'", text)
        # a restart would not change anything
        self.assertNotIn("repornirea", text)

    def test_nothing_is_asked_or_installed_for_an_importable_package(self):
        with self.answer(True), self.pip(PipResult(0)):
            self.assertTrue(deps.ensure("json"))
        self.assertEqual((self.questions, self.pip_calls, self.messages), ([], [], []))


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
