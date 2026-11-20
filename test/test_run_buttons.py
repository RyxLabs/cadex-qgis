# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The run button of each tool window, pressed as the user presses it: with valid inputs,
the task of that tool starts. Needs a GUI QgsApplication; the offscreen platform works."""

import os
import tempfile
import unittest

import helpers
import synthetic

helpers.start_qgis(gui=True)

from qgis.core import QgsProject  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import QLabel  # noqa: E402  # type: ignore

from cadex.dialogs.building_topology_dialog import BuildingTopologyDialog  # noqa: E402
from cadex.dialogs.cgxml_dialog import CgxmlDialog  # noqa: E402
from cadex.dialogs.compare_dialog import CompareDialog  # noqa: E402
from cadex.dialogs.intravilan_dialog import IntravilanDialog  # noqa: E402
from cadex.dialogs.topology_check_dialog import TopologyCheckDialog  # noqa: E402
from cadex.services import (  # noqa: E402
    building_topology_service, cgxml_export, compare_service, intravilan_service,
    settings_service, topology_check_service,
)


square = helpers.square_in_romania


class RunButtonTests(unittest.TestCase):

    def setUp(self):
        settings_service.reset_all()
        self.project = QgsProject.instance()
        self.project.clear()
        self.addCleanup(self.project.clear)
        self.tmp = tempfile.mkdtemp(prefix="cadex_run_")
        self.addCleanup(helpers.remove_folder, self.tmp)

    def layer(self, name, rows, fields="id:string"):
        layer = helpers.polygon_layer(name, rows, fields=fields)
        self.project.addMapLayer(layer)
        return layer

    def window(self, build):
        dialog = build(iface=helpers.fake_iface())
        self.addCleanup(dialog.deleteLater)
        dialog.confirm = lambda *args, **kwargs: True
        return dialog

    def started_by_the_button(self, dialog):
        """Show the window and press its run button; returns the one task the window starts,
        which is kept from running."""
        dialog.show()
        self.assertTrue(dialog.run_btn.isEnabled())
        started = helpers.tasks_started_by_button(dialog)
        warnings = " ".join(label.text() for item in dialog.message_bar.items()
                            for label in item.findChildren(QLabel))
        self.assertEqual(len(started), 1, warnings)
        return started[0]

    def test_conversie_cgxml(self):
        source = os.path.join(self.tmp, "livrare")
        synthetic.write(source, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        dialog = self.window(CgxmlDialog)
        dialog.source.set_paths([source])
        dialog.source.refresh()
        dialog.output_row.set_path(os.path.join(self.tmp, "rezultat"))
        task = self.started_by_the_button(dialog)
        self.assertIsInstance(task, cgxml_export.ConversionTask)
        self.assertEqual(task.input_paths, [source])

    def test_verificare_topologica(self):
        imobile = self.layer("Imobile", [(square(0, 0, 10), ["1"]), (square(10, 0, 10), ["2"])])
        dialog = self.window(TopologyCheckDialog)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([imobile.id()])
        dialog.constructii.set_checked_ids([])
        dialog.excel_row.edit.setText("")
        task = self.started_by_the_button(dialog)
        self.assertIsInstance(task, topology_check_service.TopologyCheckTask)

    def test_topologie_constructii(self):
        imobile = self.layer("Imobile", [(square(0, 0, 40), ["1"])])
        constructii = self.layer("Construcții", [(square(5, 5, 10), ["C1"])], "nr:string")
        dialog = self.window(BuildingTopologyDialog)
        dialog.imobile.set_checked_ids([imobile.id()])
        dialog.constructii.set_checked_ids([constructii.id()])
        task = self.started_by_the_button(dialog)
        self.assertIsInstance(task, building_topology_service.BuildingTopologyTask)

    def test_raport_intravilan(self):
        imobile = self.layer("Imobile", [(square(0, 0, 40), ["1"])])
        intravilan = self.layer("limita intravilan", [(square(-10, -10, 30), ["T"])],
                                "nume:string")
        dialog = self.window(IntravilanDialog)
        dialog.imobile.set_checked_ids([imobile.id()])
        dialog.intravilan.set_checked_ids([intravilan.id()])
        dialog.gpkg_row.set_path("")
        dialog.excel_row.set_path("")
        dialog.add_to_project.setChecked(True)
        task = self.started_by_the_button(dialog)
        self.assertIsInstance(task, intravilan_service.IntravilanTask)

    def test_comparator(self):
        fields = "id:integer,propr:string"
        old = self.layer("Parcele v1", [(square(0, 0, 10), [1, "A"])], fields)
        new = self.layer("Parcele v2", [(square(0, 0, 10), [1, "B"])], fields)
        dialog = self.window(CompareDialog)
        dialog.mode.set_index(0)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        dialog.excel_row.edit.setText("")
        task = self.started_by_the_button(dialog)
        self.assertIsInstance(task, compare_service.CompareTask)
        self.assertEqual((task.key_old, task.key_new), ("id", "id"))


def tearDownModule():
    settings_service.reset_all()
    QgsProject.instance().clear()
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
