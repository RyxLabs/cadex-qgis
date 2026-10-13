# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source rules: license headers, Qt6-compatible spellings, catalog entries with methods and
icons, complete metadata, dark icons generated from the light ones. No QgsApplication."""

import ast
import configparser
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import unittest

import helpers

ROOT = helpers.PLUGIN_DIR
# appended: first on the path, the plugin folder would make "cadex" import cadex.py
sys.path.append(ROOT)

from cadex import features  # noqa: E402
from scripts import build_icon_themes, build_zip, icon_tokens  # noqa: E402


def python_sources(root):
    """(every .py file of the checkout at root, the .py files its release zip ships). Only the
    folder names below root count, so a checkout under a folder named dist or test is read in
    full."""
    sources = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"
                   and not (folder == root and d in ("libs", "dist", "venv"))]
        sources += [os.path.join(folder, name) for name in files if name.endswith(".py")]
    shipped = {os.path.join(root, *rel.split("/")) for rel in build_zip.contents(root)[0]}
    return sorted(sources), sorted(p for p in sources if p in shipped)


def relative(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


SOURCES, RUNTIME = python_sources(ROOT)

# unscoped enum members missing on Qt6; the scoped spelling works on every supported build
UNSCOPED = re.compile(
    r"\bQt\.(Checked|Unchecked|UserRole|AlignCenter|AlignRight|AlignLeft|WaitCursor|"
    r"ItemIsUserCheckable|ItemIsEditable|Horizontal|Vertical)\b"
    r"|\bQMessageBox\.(Yes|No|Ok|Cancel|Warning|Information|Critical|AcceptRole|ActionRole)\b"
    r"|\bQgsWkbTypes\.(Polygon|MultiPolygon|PolygonGeometry|LineGeometry|PointGeometry)\b"
    r"|\bQgsMapLayerProxyModel\.(PolygonLayer|VectorLayer)\b"
    r"|\bQgsVectorFileWriter\.(NoError|CreateOrOverwriteLayer|CreateOrOverwriteFile)\b"
    r"|\bQHeaderView\.(Stretch|Interactive|ResizeToContents)\b")


def read(path):
    with open(path, encoding="utf-8") as fp:
        return fp.read()


class SourceTests(unittest.TestCase):

    def test_every_file_carries_the_license_header(self):
        for path in SOURCES:
            head = read(path).splitlines()[:3]
            self.assertIn("# SPDX-License-Identifier: GPL-2.0-or-later", head, path)
            self.assertTrue(any("SPDX-FileCopyrightText" in line for line in head), path)

    def test_no_pyqt5_imports_and_no_exec_underscore(self):
        for path in RUNTIME:
            tree = ast.parse(read(path), path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                               else [alias.name for alias in node.names])
                    for module in modules:
                        self.assertFalse(module.startswith(("PyQt5", "PyQt6")),
                                         f"{path}: import {module}, use qgis.PyQt")
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "exec_",
                                        f"{path}: Qt6 has no exec_(), use exec()")

    def test_enums_are_scoped(self):
        for path in RUNTIME:
            for number, line in enumerate(read(path).splitlines(), start=1):
                match = UNSCOPED.search(line)
                self.assertIsNone(match, f"{path}:{number}: {line.strip()}")

    def test_openpyxl_stays_out(self):
        # see services/xlsx.py
        for path in RUNTIME:
            tree = ast.parse(read(path), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertNotIn("openpyxl", [a.name for a in node.names], path)
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual((node.module or "").split(".")[0], "openpyxl", path)

    def test_services_import_no_widgets_at_module_level(self):
        for path in RUNTIME:
            if not relative(path).startswith("services/"):
                continue
            tree = ast.parse(read(path), path)
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("QtWidgets", node.module or "", path)

    def test_what_qgis_process_loads_has_no_gui_imports(self):
        # qgis_process builds the plugin class and the Processing provider without a GUI
        root_modules = ("cadex.py", "__init__.py", "features.py", "help_texts.py", "icons.py",
                        "i18n.py", "qt_compat.py")
        headless = [p for p in RUNTIME
                    if relative(p).split("/")[0] in ("processing_provider", "services")
                    or relative(p) in root_modules]
        self.assertTrue(any(relative(p).startswith("processing_provider/") for p in headless))
        for path in headless:
            tree = ast.parse(read(path), path)
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertNotIn("QtWidgets", module, path)
                    self.assertNotEqual(module, "qgis.gui", path)
                    self.assertNotEqual(module.split(".")[0], "dialogs", path)


class SourceLineLengthTests(unittest.TestCase):
    maxDiff = None

    def test_shipped_lines_are_at_most_100_characters(self):
        long_lines = [f"{relative(path)}:{number} ({len(line)})"
                      for path in RUNTIME
                      for number, line in enumerate(read(path).splitlines(), start=1)
                      if len(line) > 100]
        self.assertEqual(long_lines, [])


class SourceSelectionTests(unittest.TestCase):

    def test_a_checkout_under_folders_named_dist_libs_or_test_is_checked_in_full(self):
        base = tempfile.mkdtemp(prefix="cadex_sources_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root = os.path.join(base, "dist", "libs", "test", "services", "cadex")
        for rel in ("cadex.py", "services/fmt.py", "processing_provider/provider.py",
                    "test/helpers.py", "scripts/build_zip.py", "libs/ezdxf/__init__.py",
                    "dist/old.py", ".git/hooks/check.py", ".venv/site.py",
                    "services/__pycache__/fmt.py"):
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()

        sources, runtime = python_sources(root)

        def names(paths):
            return [os.path.relpath(p, root).replace(os.sep, "/") for p in paths]
        self.assertEqual(names(sources), ["cadex.py", "processing_provider/provider.py",
                                          "scripts/build_zip.py", "services/fmt.py",
                                          "test/helpers.py"])
        self.assertEqual(names(runtime), ["cadex.py", "processing_provider/provider.py",
                                          "services/fmt.py"])

    def test_the_checks_cover_the_plugin_modules(self):
        runtime = {relative(p) for p in RUNTIME}
        for name in ("cadex.py", "services/fmt.py", "processing_provider/provider.py",
                     "dialogs/widgets.py"):
            self.assertIn(name, runtime)
        self.assertFalse([p for p in runtime if p.startswith(("test/", "scripts/"))])
        sources = {relative(p) for p in SOURCES}
        self.assertTrue({"test/helpers.py", "scripts/build_zip.py"} <= sources)


class CatalogTests(unittest.TestCase):

    def setUp(self):
        self.plugin_methods = {
            node.name for node in ast.walk(ast.parse(read(os.path.join(ROOT, "cadex.py"))))
            if isinstance(node, ast.FunctionDef)}

    def test_every_entry_has_a_method(self):
        for feat in features.CATALOG:
            self.assertIn(feat.method, self.plugin_methods, feat.method)

    def test_toolbar_and_menu_only_name_catalog_entries(self):
        methods = {f.method for f in features.CATALOG}
        for name in [m for m in features.TOOLBAR if m] + list(features.MENU):
            self.assertIn(name, methods)

    def test_icons_exist_in_both_themes(self):
        for feat in features.CATALOG:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "icons", feat.icon)), feat.icon)
            if feat.icon not in build_icon_themes.SKIP:
                self.assertTrue(os.path.isfile(os.path.join(ROOT, "icons", "dark", feat.icon)),
                                feat.icon)

    def test_labels_are_unique(self):
        labels = [f.label for f in features.CATALOG]
        self.assertEqual(len(labels), len(set(labels)))


class IconTests(unittest.TestCase):

    def test_dark_set_is_generated_from_the_light_one(self):
        folder = os.path.join(ROOT, "icons")
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".svg") or name in build_icon_themes.SKIP:
                continue
            source = read(os.path.join(folder, name))
            build_icon_themes.check(name, source)
            self.assertEqual(read(os.path.join(folder, "dark", name)),
                             build_icon_themes.to_dark(source),
                             f"{name}: run scripts/build_icon_themes.py")

    def test_palette_contrast(self):
        for fg, bg in ((icon_tokens.FG_LIGHT, icon_tokens.BG_DEFAULT),
                       (icon_tokens.AC_LIGHT, icon_tokens.BG_DEFAULT),
                       (icon_tokens.FG_DARK, icon_tokens.BG_NIGHT),
                       (icon_tokens.AC_DARK, icon_tokens.BG_NIGHT)):
            self.assertGreaterEqual(icon_tokens.contrast(fg, bg), 3.0, (fg, bg))

    @unittest.skipUnless(importlib.util.find_spec("qgis"), "dialogs/theme.py imports qgis")
    def test_theme_text_contrast(self):
        # section titles and links on the window, white on the primary button
        from cadex.dialogs import theme
        for fg, bg in ((theme.ACCENT, icon_tokens.BG_DEFAULT), (theme.ACCENT, "#FFFFFF"),
                       (theme.ACCENT_CYAN, icon_tokens.BG_NIGHT),
                       (theme.ON_ACCENT, theme.ACCENT)):
            self.assertGreaterEqual(icon_tokens.contrast(fg, bg), 4.5, (fg, bg))

    def test_brand_mark_renders_in_qt(self):
        # QtSvg draws CSS rgba() and style="fill: url()" as solid black
        mark = read(os.path.join(ROOT, "icons", "cadex.svg"))
        self.assertNotIn("rgba(", mark)
        self.assertNotIn("style=", mark)


class MetadataTests(unittest.TestCase):

    def test_required_fields(self):
        parser = configparser.ConfigParser()
        parser.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
        general = parser["general"]
        for key in ("name", "qgisMinimumVersion", "qgisMaximumVersion", "description",
                    "about", "version", "author", "email", "repository", "tracker",
                    "homepage", "icon", "license", "changelog"):
            self.assertTrue(general.get(key, "").strip(), key)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, general["icon"])))
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", general["version"]))
        self.assertIn(general["version"], general["changelog"])
        # the Qt6 builds of QGIS 3.x do not load a plugin without it
        self.assertEqual(general.get("supportsQt6"), "True")

    def test_siruta_database_ships(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "data", "siruta.db")))


if __name__ == "__main__":
    unittest.main()
