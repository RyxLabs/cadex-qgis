# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test helpers: sys.path setup so "import cadex" works as in a QGIS profile, one
QgsApplication per process, the CADex Processing provider, layer builders, event waits and a
fake iface."""

import atexit
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
_PROVIDER = None
# test folders remove_folder() could not remove yet
_LEFT_OVER = []

# the CADex windows and algorithms refuse a layer whose coordinates look like degrees or lie
# outside Romania, so hand-built layers are drawn from this point in Romania
ORIGIN = (450000, 350000)


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
        # main() ends with os._exit, which skips atexit, and calls _clean_up itself; this is
        # for a script that never calls main()
        atexit.register(_clean_up)
    return _QGS


def start_processing():
    """Start QGIS's Processing plugin and register the CADex provider, which stop_qgis()
    removes. Returns the provider."""
    global _PROVIDER
    if _PROVIDER is None:
        from qgis.core import QgsApplication  # type: ignore
        # QGIS's own Processing plugin, next to the other bundled Python plugins
        sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
        from processing.core.Processing import Processing  # type: ignore
        from cadex.processing_provider import provider as provider_module
        Processing.initialize()
        _PROVIDER = provider_module.CadexProvider()
        QgsApplication.processingRegistry().addProvider(_PROVIDER)
    return _PROVIDER


def stop_qgis():
    global _QGS, _PROVIDER
    if _QGS is not None:
        import gc
        from qgis.PyQt.QtCore import QCoreApplication, QEvent  # type: ignore
        if _PROVIDER is not None:
            from qgis.core import QgsApplication  # type: ignore
            QgsApplication.processingRegistry().removeProvider(_PROVIDER)
            _PROVIDER = None
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
    stop_qgis() or once the process has ended."""
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


def _clean_up():
    """Stop QGIS and remove the profile and the test folders: at once, or once this process
    has ended for those Windows still holds."""
    stop_qgis()
    if _LEFT_OVER:
        _remove_after_exit(_LEFT_OVER)


def main():
    """unittest.main for a module that starts QGIS, with the test result as exit code.

    QGIS 3.40 crashes during Python's shutdown after a Processing algorithm has raised with a
    GeoPackage layer as input, so the process ends without that shutdown."""
    import unittest
    result = unittest.main(exit=False).result
    _clean_up()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)


def github_script(name):
    """A script of .github/scripts as a module; that folder is no package."""
    import importlib.util
    path = os.path.join(PLUGIN_DIR, ".github", "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def feedback():
    """A Processing feedback that keeps what an algorithm tells it: infos, warnings, and
    errors as (text, fatal)."""
    from qgis.core import QgsProcessingFeedback  # type: ignore

    class Feedback(QgsProcessingFeedback):

        def __init__(self):
            super().__init__()
            self.infos, self.warnings, self.errors = [], [], []

        def pushInfo(self, info):
            self.infos.append(info)

        def pushWarning(self, warning):
            self.warnings.append(warning)

        def reportError(self, error, fatalError=False):
            self.errors.append((error, fatalError))

    return Feedback()


def settle(seconds=0.2):
    """Process events for seconds."""
    import time
    from qgis.PyQt.QtWidgets import QApplication  # type: ignore
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def wait(condition, timeout=30.0) -> bool:
    """Process events until condition() is true or timeout seconds have passed; returns
    condition()."""
    import time
    from qgis.PyQt.QtWidgets import QApplication  # type: ignore
    end = time.monotonic() + timeout
    while not condition() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    return bool(condition())


def cgxml_count(source, timeout=30.0):
    """Wait for the count of a CGXML source box; the .cgxml files it found, None for a source
    that is empty, missing or cannot be read in full."""
    wait(source.counted, timeout)
    found, problem, _unreadable = source.outcome()
    return None if problem or not source.paths() else found


def count_files(*sources):
    """Count the files of each CGXML source box now: a press of the run button before the
    count acts only once the count ends. Returns the counts."""
    return tuple(cgxml_count(source) for source in sources)


def tasks_started_by_button(dialog, timeout=10.0) -> list:
    """Show the window and press its run button, with the start of its task replaced by a
    list; returns that list. A window that reads CGXML files starts its task once the files
    are counted, so the press is waited on for up to timeout seconds."""
    started = []
    dialog.start_task = lambda task, message="": started.append(task)
    dialog.show()
    dialog.run_btn.click()
    wait(lambda: started, timeout)
    return started


def summary_facts(path, sheet="Sumar") -> dict:
    """{first cell: second cell} of the rows of a report's Sumar sheet."""
    from cadex.services import xlsx
    _names, rows = xlsx.read_rows(path, sheet)
    return {row[0]: row[1] for row in rows if len(row) > 1 and row[0]}


def square(x, y, size):
    return (f"POLYGON(({x} {y}, {x + size} {y}, {x + size} {y + size}, "
            f"{x} {y + size}, {x} {y}))")


def square_in_romania(x, y, size):
    """square() drawn from ORIGIN."""
    return square(ORIGIN[0] + x, ORIGIN[1] + y, size)


def box_in_romania(x0, y0, x1, y1):
    """The rectangle from (x0, y0) to (x1, y1) as WKT, drawn from ORIGIN."""
    x0, y0, x1, y1 = ORIGIN[0] + x0, ORIGIN[1] + y0, ORIGIN[0] + x1, ORIGIN[1] + y1
    return f"POLYGON(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"


def same_geometry(first, second) -> bool:
    """Whether two QgsGeometry hold the same vertices in the same order. QGIS 4.2 names this
    isExactlyEqual and deprecates equals."""
    from qgis.core import QgsGeometry  # type: ignore
    exactly = getattr(QgsGeometry, "isExactlyEqual", QgsGeometry.equals)
    return exactly(first, second)


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


def row_layer(name, fields, rows, geometry="Polygon", in_project=False):
    """A memory layer of rows: (wkt or None, [values]), or [values], which gets a 10 m square
    of its own, 30 m east of the one before."""
    from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer  # type: ignore
    layer = QgsVectorLayer(f"{geometry}?crs=EPSG:3844&" + "&".join(
        f"field={f}" for f in fields.split(",")), name, "memory")
    features = []
    for i, row in enumerate(rows):
        wkt, values = row if isinstance(row, tuple) else (square_in_romania(30 * i, 0, 10), row)
        feature = QgsFeature(layer.fields())
        if wkt:
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
        feature.setAttributes(list(values))
        features.append(feature)
    added, _features = layer.dataProvider().addFeatures(features)
    assert added, layer.dataProvider().lastError()
    layer.updateExtents()
    if in_project:
        QgsProject.instance().addMapLayer(layer)
    return layer


def converted_layer(name, batch, fields, rows, in_project=False):
    """A row_layer() as Conversie CGXML writes it: each field holds the CGXML column of its
    name, and the layer carries the batch of its conversion."""
    from qgis.core import QgsProject  # type: ignore
    from cadex.services import roles
    layer = row_layer(name, fields, rows)
    roles.mark_columns(layer, [(f.split(":")[0], f.split(":")[0]) for f in fields.split(",")])
    roles.mark_batch(layer, batch)
    if in_project:
        QgsProject.instance().addMapLayer(layer)
    return layer


def gpkg_layer(layer, path, layer_name=None):
    """The layer saved as a GeoPackage and opened from it, with the fid column GeoPackage
    adds. layer_name names its table; without it, the table takes the name of the file."""
    from qgis.core import (  # type: ignore
        QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
    )
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    if layer_name:
        options.layerName = layer_name
    QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(),
                                              options)
    source = f"{path}|layername={layer_name}" if layer_name else path
    return QgsVectorLayer(source, layer.name(), "ogr")


def typed_in(path, values):
    """What the first feature of the layer file at path holds once values, {field name:
    text}, are typed into the attribute table and saved. A text that the validator of the
    table and of the form refuses cannot be typed, so its field keeps what it had."""
    from qgis.core import QgsVectorLayer  # type: ignore
    from qgis.gui import QgsFieldValidator  # type: ignore
    from qgis.PyQt.QtGui import QValidator  # type: ignore
    layer = QgsVectorLayer(path, "typed", "ogr")
    feature = next(layer.getFeatures())
    layer.startEditing()
    for name, value in values.items():
        index = layer.fields().indexOf(name)
        validator = QgsFieldValidator(None, layer.fields().at(index), "")
        if validator.validate(value, 0)[0] == QValidator.State.Acceptable:
            layer.changeAttributeValue(feature.id(), index, value)
    if not layer.commitChanges():
        raise AssertionError(layer.commitErrors())
    del layer
    stored = next(QgsVectorLayer(path, "stored", "ogr").getFeatures())
    return {name: stored[name] for name in values}


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
