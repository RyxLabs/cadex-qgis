# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test helpers: sys.path setup so "import cadex" works as in a QGIS profile, one
QgsApplication per process, polygon layer builders and a fake iface."""

import os
import shutil
import sys
import tempfile

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(TEST_DIR)
PLUGINS_DIR = os.path.dirname(PLUGIN_DIR)
DATA_DIR = os.path.join(TEST_DIR, "data")
if PLUGINS_DIR not in sys.path:
    sys.path.insert(0, PLUGINS_DIR)

_QGS = None
_PROFILE = None
# test folders remove_folder() could not remove yet
_LEFT_OVER = []


def start_qgis(gui=True):
    """One QgsApplication per process: QGIS allows a single initQgis/exitQgis cycle, so
    every test module runs in its own process."""
    global _QGS, _PROFILE
    if _QGS is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        # a new profile per process, so no run reads the settings of the user's QGIS or of
        # another test run
        _PROFILE = tempfile.mkdtemp(prefix="cadex_tests_")
        os.environ["QGIS_CUSTOM_CONFIG_PATH"] = _PROFILE
        from qgis.core import QgsApplication  # type: ignore
        from qgis.PyQt.QtCore import QCoreApplication  # type: ignore
        QCoreApplication.setOrganizationName("CADexTests")
        QCoreApplication.setApplicationName("CADexTests")
        _QGS = QgsApplication([], gui)
        _QGS.initQgis()
    return _QGS


def stop_qgis():
    global _QGS
    if _QGS is not None:
        import gc
        from qgis.PyQt.QtCore import QCoreApplication, QEvent  # type: ignore
        # a parentless dialog whose lambda slots capture self is a reference cycle; collected
        # at interpreter shutdown, after the application, it crashes Qt6
        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _QGS.exitQgis()
        _QGS = None
        if _PROFILE:
            _LEFT_OVER.append(_PROFILE)
        for path in _LEFT_OVER:
            shutil.rmtree(path, ignore_errors=True)
        _LEFT_OVER[:] = [path for path in _LEFT_OVER if os.path.exists(path)]


def remove_folder(path):
    """Remove a test's temporary folder. On Windows QGIS keeps a file it has read open until
    exitQgis, some files until its process ends, so a folder still holding one is removed by
    stop_qgis() or, through main(), once the process has ended."""
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        _LEFT_OVER.append(path)


def _remove_after_exit(paths):
    """Start a process that removes paths once this process has ended and Windows has closed
    its files: QGIS never closes the profile's user-history.db, and OGR keeps some files it
    has read or written open after exitQgis."""
    # runs this Python on the fixed script below, with the run's own folders as arguments
    import subprocess  # nosec B404
    script = ("import os, shutil, sys, time\n"
              "paths, end = sys.argv[1:], time.monotonic() + 60\n"
              "while paths and time.monotonic() < end:\n"
              "    time.sleep(0.05)\n"
              "    for path in paths:\n"
              "        shutil.rmtree(path, ignore_errors=True)\n"
              "    paths = [path for path in paths if os.path.exists(path)]\n")
    if os.name == "nt":
        options = {"creationflags": subprocess.DETACHED_PROCESS
                   | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        options = {"start_new_session": True}
    # it works in the Python folder: Windows cannot remove a running process's working
    # directory, and -c puts the working directory first on sys.path
    subprocess.Popen([sys.executable, "-c", script, *paths],  # nosec B603
                     cwd=os.path.dirname(sys.executable),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **options)


def main():
    """unittest.main for a module that starts QGIS, with the test result as exit code.

    QGIS 3.40 crashes during Python's shutdown after a Processing algorithm has raised with a
    GeoPackage layer as input, so the process ends without that shutdown."""
    import unittest
    result = unittest.main(exit=False).result
    stop_qgis()
    if _LEFT_OVER:
        _remove_after_exit(_LEFT_OVER)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)


def square(x, y, size):
    return (f"POLYGON(({x} {y}, {x + size} {y}, {x + size} {y + size}, "
            f"{x} {y + size}, {x} {y}))")


def polygon_layer(name, rows, fields="id:integer", crs="EPSG:3844",
                  geometry="Polygon"):
    """A memory polygon layer from [(wkt, [attribute values])]."""
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer  # type: ignore
    layer = QgsVectorLayer(f"{geometry}?crs={crs}&" + "&".join(
        f"field={f}" for f in fields.split(",")), name, "memory")
    features = []
    for wkt, values in rows:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        feature.setAttributes(list(values))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.updateExtents()
    return layer


def gpkg_layer(layer, path):
    """The layer saved as a GeoPackage and opened from it, with the fid column GeoPackage
    adds."""
    from qgis.core import (  # type: ignore
        QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
    )
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(),
                                              options)
    return QgsVectorLayer(path, layer.name(), "ogr")


def fake_iface():
    """Only what the CADex dialogs and plugin class call on iface."""
    from qgis.PyQt.QtCore import QObject, pyqtSignal  # type: ignore
    from qgis.PyQt.QtWidgets import QMainWindow  # type: ignore
    from qgis.gui import QgsMapCanvas, QgsMessageBar  # type: ignore

    class FakeIface(QObject):
        currentLayerChanged = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self._window = QMainWindow()
            self._canvas = QgsMapCanvas(self._window)
            self._bar = QgsMessageBar(self._window)
            self._active = None
            self.vector_menu = []
            self.toolbars = []

        def mainWindow(self):
            return self._window

        def mapCanvas(self):
            return self._canvas

        def messageBar(self):
            return self._bar

        def activeLayer(self):
            return self._active

        def setActiveLayer(self, layer):
            self._active = layer

        def addToolBar(self, name):
            toolbar = self._window.addToolBar(name)
            self.toolbars.append(toolbar)
            return toolbar

        def addPluginToVectorMenu(self, menu, action):
            self.vector_menu.append((menu, action))

        def removePluginVectorMenu(self, menu, action):
            self.vector_menu.remove((menu, action))

    return FakeIface()
