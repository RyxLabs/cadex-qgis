# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversie CGXML window run again onto a result that is open in the project. Needs a
GUI QgsApplication; the offscreen platform works."""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import helpers
import synthetic

helpers.start_qgis(gui=True)

from qgis.core import QgsProject  # noqa: E402  # type: ignore

from cadex.dialogs.cgxml_dialog import CgxmlDialog  # noqa: E402
from cadex.services import cgxml_export, roles, settings_service  # noqa: E402


class LongTextTests(unittest.TestCase):
    """Excel holds at most 32,767 characters in a cell; a longer text is cut."""

    def setUp(self):
        settings_service.reset_all()
        QgsProject.instance().clear()
        self.addCleanup(QgsProject.instance().clear)
        self.tmp = tempfile.mkdtemp(prefix="cadex_conv_win_")
        self.addCleanup(helpers.remove_folder, self.tmp)

    def result_of(self, land):
        """(notes, warning flag) of the result window after converting land to Excel."""
        source = os.path.join(self.tmp, "livrare")
        synthetic.write(source, "1.cgxml", land)
        dialog = CgxmlDialog(iface=helpers.fake_iface())
        self.addCleanup(dialog.deleteLater)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("xlsx"))
        dialog.source.set_paths([source])
        dialog.source.refresh()
        helpers.count_files(dialog.source)
        dialog.output_row.set_path(os.path.join(self.tmp, "out"))
        dialog.base_edit.setText("rezultat")
        dialog.confirm = lambda *args, **kwargs: True
        shown = []
        dialog.show_result = lambda headline, **kwargs: shown.append(kwargs)
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        [task] = started
        dialog._on_finished(task.run(), task)
        [result] = shown
        return result["notes"], result["warning"]

    def test_the_result_names_the_cells_cut_to_the_length_of_an_excel_cell(self):
        owners = tuple(f"PROPRIETAR NUMARUL {n:05d}" for n in range(2000))
        notes, warning = self.result_of(synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1", owners=owners))
        self.assertIn("1 celulă din fișierul Excel a fost scurtată la 32.767 de caractere, cât "
                      "cuprinde o celulă Excel.", notes)
        self.assertTrue(warning)

    def test_a_result_whose_texts_fit_has_no_note_on_cut_cells(self):
        notes, warning = self.result_of(synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        self.assertFalse([note for note in notes if "scurtat" in note])
        self.assertFalse(warning)


class RerunTests(unittest.TestCase):
    """A run onto files open in the project removes their layers first. When the run does
    not replace the files, the layers come back as they were."""

    def setUp(self):
        settings_service.reset_all()
        self.iface = helpers.fake_iface()
        self.project = QgsProject.instance()
        self.project.clear()
        self.addCleanup(self.project.clear)
        self.tmp = tempfile.mkdtemp(prefix="cadex_conv_win_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.source = os.path.join(self.tmp, "livrare")
        for n in (1, 2):
            square = synthetic.square(500000 + 50 * n, 400000, 20)
            synthetic.write(self.source, f"{n}.cgxml", synthetic.Land(
                rings=[square], cadgenno=str(n), buildings=[synthetic.Building(
                    1, [synthetic.square(500005 + 50 * n, 400005, 5)])]))

    def window(self, fmt_key):
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(fmt_key))
        dialog.add_to_project.setChecked(True)
        dialog.source.set_paths([self.source])
        dialog.source.refresh()
        helpers.count_files(dialog.source)
        dialog.output_row.set_path(os.path.join(self.tmp, "out"))
        dialog.base_edit.setText("rezultat")
        dialog.confirm = lambda *args, **kwargs: True
        dialog.show_result = lambda headline, **kwargs: None
        dialog.fail = lambda message: None
        dialog.notice = lambda message: None
        return dialog

    @staticmethod
    def run_now(dialog, before=None):
        """Press Convertește and run the task at once; before(task) is called first."""
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        [task] = started
        if before is not None:
            before(task)
        ok = task.run()
        dialog._on_finished(ok, task)
        return ok, task

    def tree(self):
        """[(group, layer name, shown, file of the layer, its ID column)] of the project's
        layer tree, top to bottom."""
        rows = []
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            rows.append((node.parent().name(), layer.name(), node.itemVisibilityChecked(),
                         os.path.basename(layer.source().split("|")[0]),
                         roles.column_of(layer, "id")))
        return rows

    def arranged(self, dialog):
        """The first result with a layer renamed, one hidden and another layer above them."""
        ok, task = self.run_now(dialog)
        self.assertTrue(ok, task.error_msg)
        root = self.project.layerTreeRoot()
        imobile, constructii = [node.layer() for node in root.findLayers()]
        imobile.setName("Imobile verificate")
        root.findLayer(constructii.id()).setItemVisibilityChecked(False)
        other = helpers.polygon_layer("Limite", [(helpers.square(500000, 400000, 10), [1])])
        self.project.addMapLayer(other)
        rows = self.tree()
        self.assertEqual([row[1] for row in rows],
                         ["Limite", "Imobile verificate", "Construcții rezultat"])
        return rows

    def test_a_run_that_fails_puts_the_layers_back_as_they_were(self):
        for fmt_key in ("gpkg", "shp"):
            with self.subTest(fmt=fmt_key):
                self.project.clear()
                dialog = self.window(fmt_key)
                before = self.arranged(dialog)
                refused = OSError(28, "No space left on device")
                with mock.patch.object(cgxml_export, "write_vector", side_effect=refused):
                    ok, _task = self.run_now(dialog)
                self.assertFalse(ok)
                self.assertEqual(self.tree(), before)

    def test_a_canceled_run_puts_the_layers_back_as_they_were(self):
        dialog = self.window("gpkg")
        before = self.arranged(dialog)
        ok, _task = self.run_now(dialog, before=lambda task: task.cancel())
        self.assertFalse(ok)
        self.assertEqual(self.tree(), before)

    def test_a_shapefile_result_that_was_drawn_is_replaced_by_the_next_run(self):
        from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings  # type: ignore
        from qgis.PyQt.QtCore import QSize  # type: ignore
        dialog = self.window("shp")
        ok, task = self.run_now(dialog)
        self.assertTrue(ok, task.error_msg)
        results = [layer for layer in self.project.mapLayers().values()
                   if layer.providerType() == "ogr"]
        self.assertEqual(len(results), 2)
        # drawing the layers makes QGIS hold the Shapefiles open
        settings = QgsMapSettings()
        settings.setLayers(results)
        settings.setDestinationCrs(results[0].crs())
        settings.setExtent(results[0].extent())
        settings.setOutputSize(QSize(200, 200))
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        del job, results
        ok, task = self.run_now(dialog)
        self.assertTrue(ok, task.error_msg)

    def test_a_run_that_replaces_the_files_adds_its_own_layers_only(self):
        dialog = self.window("gpkg")
        self.arranged(dialog)
        ok, task = self.run_now(dialog)
        self.assertTrue(ok, task.error_msg)
        self.assertEqual([row[1] for row in self.tree()],
                         ["Imobile rezultat", "Construcții rezultat", "Limite"])

    def other_project(self, task=None):
        """Clear the project as File > New does and add one layer to it."""
        self.project.clear()
        self.project.addMapLayer(helpers.polygon_layer(
            "Parcele B", [(helpers.square(500000, 400000, 10), [1])]))

    def test_a_run_that_fails_puts_no_layer_into_a_project_opened_meanwhile(self):
        dialog = self.window("gpkg")
        self.arranged(dialog)
        refused = OSError(28, "No space left on device")
        with mock.patch.object(cgxml_export, "write_vector", side_effect=refused):
            ok, _task = self.run_now(dialog, before=self.other_project)
        self.assertFalse(ok)
        self.assertEqual([row[1] for row in self.tree()], ["Parcele B"])

    def test_a_canceled_run_puts_no_layer_into_a_project_opened_meanwhile(self):
        dialog = self.window("gpkg")
        self.arranged(dialog)

        def meanwhile(task):
            self.other_project()
            task.cancel()
        ok, _task = self.run_now(dialog, before=meanwhile)
        self.assertFalse(ok)
        self.assertEqual([row[1] for row in self.tree()], ["Parcele B"])

    def test_a_run_stopped_by_unloading_the_plugin_puts_the_layers_back_as_they_were(self):
        dialog = self.window("gpkg")
        before = self.arranged(dialog)
        dialog.start_task = lambda task, message="": None
        dialog._run()
        self.assertEqual([row[1] for row in self.tree()], ["Limite"])
        # what the plugin does with each of its windows when it is unloaded
        dialog.dispose()
        self.assertEqual(self.tree(), before)

    def test_layers_put_back_on_unloading_come_back_after_the_run_has_moved_its_files(self):
        from cadex.services import tasks
        dialog = self.window("gpkg")
        self.arranged(dialog)
        # the run goes to the task manager, on a thread of its own
        del dialog.start_task
        moving, moved = threading.Event(), threading.Event()
        put_in_place = cgxml_export._put_in_place

        def slow(*args, **kwargs):
            moving.set()
            time.sleep(0.5)
            try:
                put_in_place(*args, **kwargs)
            finally:
                moved.set()
        put_back = dialog._put_back
        # whether the run had moved its files when each layer came back
        after_move = []
        dialog._put_back = lambda described: (after_move.append(moved.is_set()),
                                              put_back(described))
        with mock.patch.object(cgxml_export, "_put_in_place", slow):
            dialog._run()
            task = dialog._task
            self.assertTrue(helpers.wait(moving.is_set, 30))
            # what the plugin does with each of its windows when it is unloaded
            dialog.dispose()
            tasks.wait_for([task], 10)
        self.assertEqual(after_move, [True])


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
