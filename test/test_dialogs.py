# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dialogs and the plugin class with a fake iface. Needs a GUI QgsApplication; the offscreen
platform works. CADEX_SCREENSHOTS=<folder> saves a picture of each dialog."""

import os
import shutil
import sys
import tempfile
import time
import unittest

import helpers
import synthetic

helpers.start_qgis(gui=True)

from qgis.core import QgsProject  # noqa: E402  # type: ignore
from qgis.PyQt.QtCore import Qt  # noqa: E402  # type: ignore
from qgis.PyQt.QtWidgets import QApplication  # noqa: E402  # type: ignore

from cadex import features  # noqa: E402
from cadex.cadex import Cadex  # noqa: E402
from cadex.dialogs import _ui_helpers  # noqa: E402
from cadex.dialogs.base_dialog import elapsed_text  # noqa: E402
from cadex.dialogs.building_topology_dialog import BuildingTopologyDialog  # noqa: E402
from cadex.dialogs.cgxml_dialog import CgxmlDialog  # noqa: E402
from cadex.dialogs.compare_dialog import CompareDialog  # noqa: E402
from cadex.dialogs.diminuare_dialog import DiminuareDialog  # noqa: E402
from cadex.dialogs.help_dialog import HelpDialog  # noqa: E402
from cadex.dialogs.intravilan_dialog import IntravilanDialog  # noqa: E402
from cadex.dialogs.settings_dialog import SettingsDialog  # noqa: E402
from cadex.dialogs.topology_check_dialog import TopologyCheckDialog  # noqa: E402
from cadex.dialogs.widgets import (  # noqa: E402
    CgxmlSource, LayerChecklist, PathRow, fill_field_combo, preselect_layer,
)
from cadex.services import cgxml_compare, settings_service  # noqa: E402
from cadex.services import topology_check_service as tcs  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot  # noqa: E402


def square(x, y, size):
    # the windows refuse a layer whose coordinates look like degrees or lie outside Romania,
    # so the hand-built layers are drawn from a point in Romania
    return helpers.square(450000 + x, 350000 + y, size)


SCREENSHOTS = os.environ.get("CADEX_SCREENSHOTS")

if os.environ.get("CADEX_DARK"):
    # a palette like QGIS's Night Mapping theme
    from qgis.PyQt.QtGui import QColor, QPalette  # type: ignore
    _palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#323232"),
                        (QPalette.ColorRole.WindowText, "#E6E6E6"),
                        (QPalette.ColorRole.Base, "#262626"),
                        (QPalette.ColorRole.AlternateBase, "#2E2E2E"),
                        (QPalette.ColorRole.Text, "#E6E6E6"),
                        (QPalette.ColorRole.Button, "#3C3C3C"),
                        (QPalette.ColorRole.ButtonText, "#E6E6E6")):
        _palette.setColor(role, QColor(color))
    QApplication.setStyle("Fusion")
    QApplication.setPalette(_palette)


def shoot(dialog, name):
    if not SCREENSHOTS:
        return
    os.makedirs(SCREENSHOTS, exist_ok=True)
    # the offscreen platform has no fonts on Windows, so the window is shown off screen
    dialog.move(-10000, -10000)
    dialog.show()
    # the first grab starts the Windows style's check box animations; the second comes after
    # they end
    dialog.grab()
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
    dialog.grab().save(os.path.join(SCREENSHOTS, f"{name}.png"))
    dialog.hide()


class DialogTest(unittest.TestCase):

    def setUp(self):
        settings_service.reset_all()
        self.iface = helpers.fake_iface()
        self.project = QgsProject.instance()
        self.project.clear()
        self.imobile_s1 = helpers.polygon_layer(
            "Imobile_S1", [(square(0, 0, 100), ["A", "POPESCU"])],
            fields="ID_IMOBIL:string,Proprietar:string")
        self.imobile_s2 = helpers.polygon_layer(
            "IMOBILE sector 2", [(square(200, 0, 100), ["B", "NEIDENTIFICAT"])],
            fields="ID_IMOBIL:string,Proprietar:string")
        self.constructii = helpers.polygon_layer(
            "Construcţii", [(square(10, 10, 20), ["C1"])], fields="nr:string")
        self.intravilan = helpers.polygon_layer(
            "limita intravilan", [(square(-10, -10, 150), ["T1"])], fields="nume:string")
        self.contur = helpers.polygon_layer(
            "Contur tarla 12", [(square(0, 0, 100), [1])])
        self.project.addMapLayers([self.imobile_s1, self.imobile_s2, self.constructii,
                                   self.intravilan, self.contur])
        self.tmp = tempfile.mkdtemp(prefix="cadex_dlg_")

    def tearDown(self):
        self.project.clear()
        helpers.remove_folder(self.tmp)

    def names(self, checklist):
        return sorted(layer.name() for layer in checklist.checked_layers())

    @staticmethod
    def bar_text(dialog):
        """Every message shown in the dialog's message bar."""
        from qgis.PyQt.QtWidgets import QLabel  # type: ignore
        return " ".join([item.text() for item in dialog.message_bar.items()]
                        + [label.text() for item in dialog.message_bar.items()
                           for label in item.findChildren(QLabel)])


class ChecklistTests(DialogTest):

    def test_layers_are_preselected_for_each_role_by_name(self):
        self.assertEqual(self.names(LayerChecklist("roles/imobile")),
                         ["IMOBILE sector 2", "Imobile_S1"])
        # the cedilla ţ in the layer name still matches "constructi"
        self.assertEqual(self.names(LayerChecklist("roles/constructii")), ["Construcţii"])
        self.assertEqual(self.names(LayerChecklist("roles/intravilan")), ["limita intravilan"])

    def test_own_names_from_settings(self):
        settings_service.set_("roles/imobile", "sector 2")
        self.assertEqual(self.names(LayerChecklist("roles/imobile")), ["IMOBILE sector 2"])

    def test_follows_the_project_and_keeps_ticks(self):
        checklist = LayerChecklist("roles/imobile")
        checklist.set_checked_ids([self.imobile_s1.id()])
        extra = helpers.polygon_layer("Imobile_S3", [])
        self.project.addMapLayer(extra)
        self.assertEqual(checklist.count(), 6)
        self.assertEqual(self.names(checklist), ["Imobile_S1"])
        self.project.removeMapLayer(self.imobile_s1.id())
        self.assertEqual(self.names(checklist), [])
        checklist.disconnect_project()

    def test_a_renamed_layer_is_listed_under_its_new_name(self):
        checklist = LayerChecklist("roles/imobile")
        self.imobile_s1.setName("Drumuri")
        self.assertIn("Drumuri", checklist.texts())
        self.assertNotIn("Imobile_S1", checklist.texts())
        # nothing ticked by hand yet, so the ticks follow the new name
        self.assertEqual(self.names(checklist), ["IMOBILE sector 2"])
        checklist.disconnect_project()

    def test_new_project_gets_the_name_preselection_again(self):
        checklist = LayerChecklist("roles/imobile")
        checklist.set_checked_ids([self.imobile_s1.id()])
        self.project.clear()
        self.assertEqual(checklist.count(), 0)
        self.project.addMapLayers([helpers.polygon_layer("Imobile noi", []),
                                   helpers.polygon_layer("Drumuri", [])])
        self.assertEqual(self.names(checklist), ["Imobile noi"])
        self.assertEqual(checklist._count.text(), "1 din 2 bifate")
        checklist.disconnect_project()

    def test_report_layers_are_never_preselected(self):
        checklist = LayerChecklist("roles/imobile")
        _ui_helpers.add_layers([helpers.polygon_layer("Imobile apărute", []),
                                helpers.polygon_layer("Imobile modificate", [])], "Comparator")
        # a converted layer is data for the other tools, not a report
        _ui_helpers.add_layers([helpers.polygon_layer("Imobile convertite", [])], report=False)
        expected = ["IMOBILE sector 2", "Imobile convertite", "Imobile_S1"]
        self.assertEqual(self.names(checklist), expected)
        self.assertEqual(self.names(LayerChecklist("roles/imobile")), expected)
        checklist.disconnect_project()

    def test_a_layer_marked_as_report_after_it_is_added_loses_its_tick(self):
        # Processing adds its outputs to the project first and marks them afterwards
        from cadex.services import roles
        checklist = LayerChecklist("roles/constructii")
        output = helpers.polygon_layer("Construcții apărute", [])
        self.project.addMapLayer(output)
        self.assertIn("Construcții apărute", self.names(checklist))
        roles.mark_report(output)
        self.assertEqual(self.names(checklist), ["Construcţii"])
        checklist.disconnect_project()

    def test_of_two_conversions_of_the_same_imobile_only_the_latest_is_preselected(self):
        from cadex.services import roles

        def converted(batch, ids, uat="Cristian"):
            layer = helpers.polygon_layer("Imobile", [(square(0, 0, 10), [i, uat]) for i in ids],
                                          fields="id:integer,uat:string")
            roles.mark_columns(layer, [("id", "id"), ("uat", "uat")])
            roles.mark_batch(layer, batch)
            _ui_helpers.add_layers([layer], report=False)
            return layer

        older = converted("20261007090000000000", [1101, 1102])
        newer = converted("20261007100000000000", [1101, 1103])
        # another sector, converted on its own
        sector = converted("20261007080000000000", [2201, 2202])
        # another UAT, whose IDs repeat those of the first
        other_uat = converted("20261007070000000000", [1101, 1102], uat="Bod")
        checklist = LayerChecklist("roles/imobile")
        ticked = {layer.id() for layer in checklist.checked_layers()}
        self.assertIn(newer.id(), ticked)
        self.assertNotIn(older.id(), ticked)
        self.assertIn(sector.id(), ticked)
        self.assertIn(other_uat.id(), ticked)
        # layers that come from no conversion keep their name preselection
        self.assertIn(self.imobile_s1.id(), ticked)
        checklist.disconnect_project()

    def test_each_run_gets_a_group_of_its_own(self):
        from qgis.core import QgsLayerTreeGroup  # type: ignore
        for _run in range(3):
            _ui_helpers.add_layers([helpers.polygon_layer("Probleme (suprafețe)", []),
                                    helpers.polygon_layer("Probleme (puncte)", [])],
                                   "Verificare Topologică")
        groups = [node.name() for node in self.project.layerTreeRoot().children()
                  if isinstance(node, QgsLayerTreeGroup)]
        # the newest on top
        self.assertEqual(groups, ["Verificare Topologică (3)", "Verificare Topologică (2)",
                                  "Verificare Topologică"])

    def test_layer_combo_skips_report_layers(self):
        from qgis.gui import QgsMapLayerComboBox  # type: ignore
        _ui_helpers.add_layers([helpers.polygon_layer("Contur raport", [])])
        combo = QgsMapLayerComboBox()
        self.assertTrue(preselect_layer(combo, "roles/contur"))
        self.assertEqual(combo.currentLayer().name(), "Contur tarla 12")

    def test_field_combo(self):
        from qgis.PyQt.QtWidgets import QComboBox  # type: ignore
        combo = QComboBox()
        fill_field_combo(combo, ["fid", "ID_IMOBIL", "Proprietar"], "fields/owner", "(niciunul)")
        self.assertEqual(combo.currentData(), "Proprietar")
        fill_field_combo(combo, ["Tip_proprietate", "Nume_proprietar"], "fields/owner")
        self.assertEqual(combo.currentData(), "Nume_proprietar")
        # the measured area is not the deed area
        fill_field_combo(combo, ["Nr", "Suprafata masurata", "SUP_ACT"], "fields/area_deed")
        self.assertEqual(combo.currentData(), "SUP_ACT")
        fill_field_combo(combo, ["Nr", "CF"], "fields/ie", "(niciunul)")
        self.assertEqual(combo.currentData(), "CF")
        fill_field_combo(combo, ["a", "b"], "fields/owner", "(niciunul)")
        self.assertEqual(combo.currentData(), "")


class WidgetTests(DialogTest):

    def test_path_row_takes_only_what_its_filter_lets_through(self):
        excel = PathRow("save", "", "Excel (*.xlsx)", ".xlsx")
        self.assertTrue(excel._accepts(os.path.join(self.tmp, "raport.XLSX")))
        self.assertFalse(excel._accepts(os.path.join(self.tmp, "raport.csv")))
        self.assertFalse(excel._accepts(self.tmp))
        table = PathRow("open", "", "Excel sau CSV (*.xlsx *.csv);;Toate fișierele (*)")
        self.assertTrue(table._accepts(os.path.join(self.tmp, "parcele.txt")))
        folder = PathRow("folder", "")
        self.assertTrue(folder._accepts(self.tmp))
        self.assertFalse(folder._accepts(os.path.join(self.tmp, "raport.xlsx")))

    def test_cgxml_source_counts_what_it_holds(self):
        source = CgxmlSource()
        source.set_paths([helpers.DATA_DIR])
        source.refresh()
        self.assertEqual(source.count(), 11)
        self.assertIn("11", source.status.text())
        source.set_paths([os.path.join(self.tmp, "nu_exista")])
        source.refresh()
        self.assertIsNone(source.count())
        self.assertEqual(source.status.text(), "Calea aleasă nu există.")

    def test_each_cgxml_picker_opens_on_a_path_of_its_own_kind(self):
        from qgis.PyQt.QtWidgets import QFileDialog  # type: ignore
        opened = {}
        originals = {name: getattr(QFileDialog, name) for name in (
            "getOpenFileName", "getOpenFileNames", "getExistingDirectory")}

        def recorder(name):
            def pick(_parent, _title, start, *_filter):
                opened[name] = start
                return "" if name == "getExistingDirectory" else ("" if name == "getOpenFileName"
                                                                  else [], "")
            return staticmethod(pick)

        for name in originals:
            setattr(QFileDialog, name, recorder(name))
        try:
            source = CgxmlSource()
            archive = os.path.join(self.tmp, "livrare.zip")
            for name in ("livrare.zip", "1.cgxml", "2.cgxml"):
                open(os.path.join(self.tmp, name), "wb").close()
            source.set_paths([archive])
            # a picker opens at a path the count found
            source.refresh()
            source.count()
            source._browse_zip()
            source._browse_files()
            source._browse_folder()
            # the .cgxml picker opens in the archive's folder: its filter cannot select a .zip
            self.assertEqual(opened, {"getOpenFileName": archive,
                                      "getOpenFileNames": self.tmp,
                                      "getExistingDirectory": self.tmp})
            files = os.path.join(self.tmp, "1.cgxml")
            source.set_paths([files, os.path.join(self.tmp, "2.cgxml")])
            source.refresh()
            source.count()
            source._browse_zip()
            source._browse_files()
            self.assertEqual((opened["getOpenFileName"], opened["getOpenFileNames"]),
                             (self.tmp, files))
        finally:
            for name, original in originals.items():
                setattr(QFileDialog, name, original)

    def test_result_text_escapes_names_and_formats_counts(self):
        html = _ui_helpers.result_html(
            "S-au verificat 2 imobile.", counts=[("Suprapunere", 1234, "#D64545"), None,
                                                 ("Suprafață", "12 mp", None)],
            notes=["Layer creat: <test> & co."])
        self.assertIn("1.234", html)
        self.assertIn("bgcolor='#D64545'", html)
        self.assertIn("&lt;test&gt; &amp; co.", html)
        self.assertNotIn("<test>", html)
        # a row under another is indented
        html = _ui_helpers.result_html("", counts=[("Modificate", 3, None),
                                                   ("   Doar limita", 1, "#E3A008")])
        self.assertIn("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Doar limita", html)

    def test_questions_have_romanian_buttons_and_default_to_no(self):
        from qgis.PyQt.QtWidgets import QMessageBox  # type: ignore
        asked = []
        original = QMessageBox.exec

        def fake_exec(box):
            asked.append(([b.text() for b in box.buttons()], box.defaultButton().text()))
            return 0
        QMessageBox.exec = fake_exec
        try:
            dialog = CgxmlDialog(iface=self.iface)
            existing = os.path.join(self.tmp, "rezultat.gpkg")
            open(existing, "w").close()
            # closed without a choice counts as no
            self.assertFalse(dialog.confirm_overwrite([existing]))
        finally:
            QMessageBox.exec = original
        buttons, default = asked[0]
        self.assertEqual(sorted(buttons), ["Anulează", "Înlocuiește"])
        self.assertEqual(default, "Anulează")
        dialog.deleteLater()

    def test_elapsed_text(self):
        self.assertEqual(elapsed_text(7.9), "0:07")
        self.assertEqual(elapsed_text(135), "2:15")
        self.assertEqual(elapsed_text(3723), "1:02:03")


class DialogBuildTests(DialogTest):

    def test_cgxml_dialog(self):
        dialog = CgxmlDialog(iface=self.iface)
        self.assertEqual(dialog.windowTitle(), "Conversie CGXML")
        dialog.source.set_paths([helpers.DATA_DIR])
        dialog.source.recursive.setChecked(True)
        dialog.source.refresh()
        self.assertEqual(dialog.source.count(), 11)
        self.assertIn("11", dialog.source.status.text())
        self.assertEqual(dialog.base_edit.text(), "cgxml_data")
        dialog.output_row.set_path(self.tmp)
        self.assertEqual(dialog.targets_label.text(), "Se va crea cgxml_data.gpkg.")
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("dxf"))
        self.assertTrue(dialog.tabs.isTabEnabled(dialog.labels_tab_index))
        dialog._on_tab_changed(dialog.labels_tab_index)
        labels = {c["key"]: c for c in dialog.labels["imobile"].config()}
        self.assertTrue(labels["id"]["on"])
        self.assertEqual(labels["ie"]["prefix"], "IE ")
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("gpkg"))
        self.assertFalse(dialog.tabs.isTabEnabled(dialog.labels_tab_index))
        shoot(dialog, "cgxml")
        dialog.tabs.setCurrentIndex(dialog.columns_tab_index)
        shoot(dialog, "cgxml_columns")
        dialog.deleteLater()

    def test_enter_in_a_path_field_confirms_the_path_and_runs_nothing(self):
        from qgis.PyQt.QtCore import Qt  # type: ignore
        from qgis.PyQt.QtTest import QTest  # type: ignore
        dialog = CgxmlDialog(iface=self.iface)
        dialog.output_row.set_path(self.tmp)
        dialog.show()
        dialog.source.edit.setText(helpers.DATA_DIR)
        QTest.keyClick(dialog.source.edit, Qt.Key.Key_Return)
        # the count follows the path; the conversion starts only from the button
        self.assertEqual(dialog.source.count(), 11)
        self.assertIn("11", dialog.source.status.text())
        QTest.keyClick(dialog.output_row.edit, Qt.Key.Key_Enter)
        self.assertIsNone(dialog._task)
        self.assertEqual(os.listdir(self.tmp), [])
        dialog.close()
        dialog.deleteLater()

    def test_the_output_goes_next_to_the_source_folder(self):
        delivery = os.path.join(self.tmp, "livrare")
        shutil.copytree(helpers.DATA_DIR, delivery)
        dialog = CgxmlDialog(iface=self.iface)
        dialog.source.set_paths([delivery])
        dialog.source.refresh()
        dialog.source.count()
        # not inside it, where the result would be copied along with the delivery
        self.assertEqual(dialog.output_row.path(), self.tmp)
        self.assertEqual(dialog.base_edit.text(), "cgxml_livrare")
        archive = os.path.join(self.tmp, "arhiva", "livrare.zip")
        os.makedirs(os.path.dirname(archive))
        open(archive, "wb").close()
        other = CgxmlDialog(iface=self.iface)
        other.output_row.set_path("")
        other.source.set_paths([archive])
        other.source.refresh()
        other.source.count()
        self.assertEqual(other.output_row.path(), os.path.dirname(archive))
        dialog.deleteLater()
        other.deleteLater()

    def test_converted_layers_carry_their_run_and_columns(self):
        from cadex.services import cgxml_columns, cgxml_export, roles
        task = cgxml_export.ConversionTask(
            input_paths=helpers.DATA_DIR, recursive=False, output_dir=self.tmp,
            base_name="conv", fmt="gpkg",
            layouts={w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")},
            layer_names={"imobile": "Imobile", "constructii": "Construcții"}, label_config={})
        self.assertTrue(task.run(), task.error_msg)
        dialog = CgxmlDialog(iface=self.iface)
        imobile, constructii = dialog._load_layers(task)
        self.assertTrue(roles.batch_of(imobile))
        self.assertEqual(roles.batch_of(imobile), roles.batch_of(constructii))
        self.assertEqual(roles.column_of(constructii, "nr_constr"), "nr_constr")
        dialog.deleteLater()

    def test_converted_layers_are_named_after_their_source(self):
        # two deliveries converted into one project have different names in every layer list
        from cadex.services import cgxml_columns, cgxml_export
        task = cgxml_export.ConversionTask(
            input_paths=helpers.DATA_DIR, recursive=False, output_dir=self.tmp,
            base_name="cgxml_livrare_v2", fmt="gpkg",
            layouts={w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")},
            layer_names={"imobile": "Imobile", "constructii": "Construcții"}, label_config={})
        self.assertTrue(task.run(), task.error_msg)
        dialog = CgxmlDialog(iface=self.iface)
        self.assertEqual([layer.name() for layer in dialog._load_layers(task)],
                         ["Imobile livrare_v2", "Construcții livrare_v2"])
        dialog.deleteLater()

    def test_a_converted_file_opened_again_keeps_its_column_roles_batch_and_labels(self):
        # opened the next day in another project: the other tools read the column roles
        # from the file, and the outlines and labels are drawn again
        from qgis.core import QgsVectorLayer  # type: ignore
        from cadex.services import cgxml_columns, cgxml_export, roles
        for fmt_key in ("gpkg", "shp"):
            task = cgxml_export.ConversionTask(
                input_paths=helpers.DATA_DIR, recursive=False,
                output_dir=os.path.join(self.tmp, fmt_key), base_name="conv", fmt=fmt_key,
                layouts={w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")},
                layer_names={"imobile": "Imobile", "constructii": "Construcții"},
                label_config={})
            os.makedirs(task.output_dir, exist_ok=True)
            self.assertTrue(task.run(), task.error_msg)
            dialog = CgxmlDialog(iface=self.iface)
            dialog.add_to_project.setChecked(False)
            self.assertEqual(dialog._load_layers(task), [])
            dialog.deleteLater()
            for which in ("imobile", "constructii"):
                again = QgsVectorLayer(task.outputs[which], "din nou", "ogr")
                self.assertEqual(roles.column_of(again, "id"), "id", (fmt_key, which))
                self.assertTrue(roles.batch_of(again), (fmt_key, which))
                self.assertTrue(again.labelsEnabled(), (fmt_key, which))

    def test_cgxml_warning_opens_the_tab_to_fix(self):
        dialog = CgxmlDialog(iface=self.iface)
        dialog.source.set_paths([helpers.DATA_DIR])
        dialog.source.refresh()
        dialog.output_row.set_path(self.tmp)
        _ui_helpers.set_all_check_states(dialog.columns["imobile"].tree, False)
        dialog._run()
        self.assertEqual(dialog.tabs.currentIndex(), dialog.columns_tab_index)
        self.assertEqual(len(dialog.message_bar.items()), 1)
        self.assertIsNone(dialog._task)
        dialog.deleteLater()

    def test_intravilan_dialog(self):
        dialog = IntravilanDialog(iface=self.iface)
        self.assertEqual(self.names(dialog.imobile), ["IMOBILE sector 2", "Imobile_S1"])
        self.assertEqual(self.names(dialog.intravilan), ["limita intravilan"])
        self.assertEqual(dialog.id_combo.currentData(), "ID_IMOBIL")
        self.assertEqual(dialog.owner_combo.currentData(), "Proprietar")
        self.assertEqual(dialog.field_list.count(), 2)
        shoot(dialog, "intravilan")
        dialog.done(0)

    def test_building_topology_dialog(self):
        dialog = BuildingTopologyDialog(iface=self.iface)
        self.assertEqual(self.names(dialog.constructii), ["Construcţii"])
        self.assertEqual(dialog.join_list.count(), 2)
        shoot(dialog, "topologie")
        dialog.done(0)

    def test_building_topology_legend_lists_only_the_states_found(self):
        from cadex.services import building_topology_service as bts
        crossing = helpers.polygon_layer(
            "Construcții noi", [(square(90, 10, 20), ["C2"])], fields="nr:string")
        self.project.addMapLayer(crossing)
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(self.imobile_s1), LayerSnapshot(self.imobile_s2)],
            constructii=[LayerSnapshot(self.constructii), LayerSnapshot(crossing)],
            buffer_m=0.002, threshold=1.0, join_fields=[])
        self.assertTrue(task.run(), task.error_msg)
        dialog = BuildingTopologyDialog(iface=self.iface)
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append(kw)
        dialog._on_finished(True, task)
        legends = {layer.name(): [c.value() for c in layer.renderer().categories()]
                   for layer in shown[0]["layers"]}
        # 'Construcţii' lies inside its imobil, 'Construcții noi' crosses the limit
        self.assertEqual(legends, {"Construcţii_corectat": [bts.UNCHANGED],
                                   "Construcții noi_corectat": [bts.LOSS]})
        dialog.deleteLater()

    def test_row_numbers_are_never_offered_as_data(self):
        # GeoPackage numbers its rows in fid, and adding or removing a feature renumbers them;
        # the second layer is a Shapefile-style export that kept fid as a plain column
        old = helpers.gpkg_layer(helpers.polygon_layer(
            "Imobile vechi", [(square(0, 0, 10), ["1", "A"])], fields="ie:string,propr:string"),
            os.path.join(self.tmp, "vechi.gpkg"))
        new = helpers.polygon_layer("Imobile noi", [(square(0, 0, 10), [1, "1", "A"])],
                                    fields="fid:integer,ie:string,propr:string")
        self.project.clear()
        self.project.addMapLayers([old, new])
        self.assertEqual(old.fields().names(), ["fid", "ie", "propr"])

        compare = CompareDialog(iface=self.iface)
        compare.old_combo.setLayer(old)
        compare.new_combo.setLayer(new)
        self.assertEqual(compare.field_list.texts(), ["propr"])
        intravilan = IntravilanDialog(iface=self.iface)
        self.assertEqual(intravilan.field_list.texts(), ["ie", "propr"])
        buildings = BuildingTopologyDialog(iface=self.iface)
        self.assertEqual(buildings.join_list.texts(), ["ie", "propr"])
        for dialog in (compare, intravilan, buildings):
            dialog.deleteLater()

    def test_row_numbers_are_never_offered_as_keys(self):
        # a row number pairs nothing: every layer has rows 1, 2, 3...
        imobile = helpers.gpkg_layer(helpers.polygon_layer(
            "Imobile gpkg", [(square(0, 0, 10), [1101, "A"])], fields="id:integer,propr:string"),
            os.path.join(self.tmp, "imobile.gpkg"))
        constructii = helpers.gpkg_layer(helpers.polygon_layer(
            "Construcții gpkg", [(square(2, 2, 3), [1101, "C1"])],
            fields="id:integer,nr_constr:string"), os.path.join(self.tmp, "constructii.gpkg"))
        self.project.clear()
        self.project.addMapLayers([imobile, constructii])

        def offered(combo):
            return [combo.itemData(i) for i in range(combo.count())]

        compare = CompareDialog(iface=self.iface)
        compare.old_combo.setLayer(imobile)
        compare.new_combo.setLayer(constructii)
        topology = TopologyCheckDialog(iface=self.iface)
        buildings = BuildingTopologyDialog(iface=self.iface)
        intravilan = IntravilanDialog(iface=self.iface)
        combos = {"compare old": compare.old_key, "compare new": compare.new_key,
                  "topology id": topology.id_combo, "topology sector": topology.sector_combo,
                  "topology link": topology.link_combo, "buildings link": buildings.link_combo,
                  "buildings key": buildings.key_combo, "intravilan id": intravilan.id_combo,
                  "intravilan owner": intravilan.owner_combo}
        for name, combo in combos.items():
            self.assertIn("id", offered(combo), name)
            self.assertNotIn("fid", offered(combo), name)
        for dialog in (compare, topology, buildings, intravilan):
            dialog.deleteLater()

    def test_compare_dialog(self):
        old = helpers.polygon_layer("eterra 2025", [], fields="IE:integer,propr:string,sup:double")
        new = helpers.polygon_layer("eterra 2026", [], fields="ie:string,propr:string,sup:double")
        self.project.addMapLayers([old, new])
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        self.assertEqual(dialog.old_key.currentData(), "IE")
        self.assertEqual(dialog.new_key.currentData(), "ie")
        self.assertEqual(dialog.field_list.texts(), ["propr", "sup"])
        self.assertIn("'IE'", dialog.key_hint.text())
        shoot(dialog, "comparator")
        dialog._swap()
        self.assertEqual(dialog.old_combo.currentLayer().name(), "eterra 2026")
        self.assertEqual(dialog.new_combo.currentLayer().name(), "eterra 2025")
        self.assertEqual((dialog.old_key.currentData(), dialog.new_key.currentData()),
                         ("ie", "IE"))
        dialog.deleteLater()

    def converted(self, label, batch, buildings, imobil="1"):
        """An imobile layer with one imobil and the construcții layer of the same Conversie
        CGXML run."""
        from cadex.services import roles
        imobile = helpers.polygon_layer(f"Imobile {label}", [(square(0, 0, 40), [imobil, 1600.0])],
                                        fields="id:string,sup_mas:double")
        constructii = helpers.polygon_layer(
            f"Construcții {label}",
            [(square(x, 5, 5), [imobil, number, area]) for x, number, area in buildings],
            fields="id:string,nr_constr:string,sup_mas:double")
        roles.mark_columns(imobile, [("id", "id"), ("sup_mas", "sup_mas")])
        roles.mark_columns(constructii, [("id", "id"), ("nr_constr", "nr_constr"),
                                         ("sup_mas", "sup_mas")])
        for layer in (imobile, constructii):
            roles.mark_batch(layer, batch)
        self.project.addMapLayers([imobile, constructii])
        return imobile, constructii

    def test_each_comparator_mode_keeps_its_own_excel_report(self):
        # a layer comparison after an eTerra comparison must not offer to overwrite its report
        layers_xlsx = os.path.join(self.tmp, "layere.xlsx")
        cgxml_xlsx = os.path.join(self.tmp, "cgxml.xlsx")
        dialog = CompareDialog(iface=self.iface)
        dialog.mode.set_index(0)
        dialog.excel_row.edit.setText(layers_xlsx)
        dialog.mode.set_index(1)
        self.assertEqual(dialog.excel_row.edit.text(), "")
        dialog.excel_row.edit.setText(cgxml_xlsx)
        dialog.mode.set_index(0)
        self.assertEqual(dialog.excel_row.edit.text(), layers_xlsx)
        dialog.save_remembered()
        dialog.deleteLater()
        again = CompareDialog(iface=self.iface)
        self.assertEqual(again.excel_row.edit.text(), layers_xlsx)
        again.mode.set_index(1)
        self.assertEqual(again.excel_row.edit.text(), cgxml_xlsx)
        again.deleteLater()

    def test_compare_dialog_starts_on_two_imobile_layers(self):
        dialog = CompareDialog(iface=self.iface)
        chosen = {dialog.old_combo.currentLayer().id(), dialog.new_combo.currentLayer().id()}
        self.assertEqual(chosen, {self.imobile_s1.id(), self.imobile_s2.id()})
        dialog.deleteLater()
        # of the converted ones, the two latest runs, the older as the old version
        newest, _b = self.converted("2026", "20260101", [(5, "C1", 25.0)])
        oldest, _b = self.converted("2024", "20240101", [(5, "C1", 25.0)])
        older, _b = self.converted("2025", "20250101", [(5, "C1", 25.0)])
        dialog = CompareDialog(iface=self.iface)
        self.assertEqual([dialog.old_combo.currentLayer().id(),
                          dialog.new_combo.currentLayer().id()], [older.id(), newest.id()])
        self.assertTrue(dialog.buildings_box.isChecked())
        dialog.deleteLater()
        # another sector converted after them is no version of theirs
        self.converted("sector 4", "20270101", [(5, "C1", 25.0)], imobil="2")
        dialog = CompareDialog(iface=self.iface)
        self.assertEqual([dialog.old_combo.currentLayer().id(),
                          dialog.new_combo.currentLayer().id()], [older.id(), newest.id()])
        dialog.deleteLater()

    def test_compare_dialog_reopened_after_a_conversion_starts_on_its_versions(self):
        dialog = CompareDialog(iface=self.iface)
        dialog.show()
        dialog.close()
        older, _b = self.converted("2025", "20250101", [(5, "C1", 25.0)])
        newer, _b = self.converted("2026", "20260101", [(5, "C1", 25.0)])
        _ui_helpers.add_layers([helpers.polygon_layer("Imobile apărute", [])], "Comparator")
        dialog.show()
        pair = lambda: [dialog.old_combo.currentLayer().id(),  # noqa: E731
                        dialog.new_combo.currentLayer().id()]
        self.assertEqual(pair(), [older.id(), newer.id()])
        # layers chosen by hand stay
        dialog.old_combo.setLayer(self.imobile_s1)
        dialog.old_combo.activated.emit(dialog.old_combo.currentIndex())
        dialog.close()
        dialog.show()
        self.assertEqual(pair(), [self.imobile_s1.id(), newer.id()])
        dialog.close()
        dialog.deleteLater()

    def eterra_and_delivery(self, buildings_ie=True):
        """Converted imobile and construcții of an eTerra export and of a delivery; the
        cadastral numbers of the export are not the IDs of the delivery."""
        from cadex.services import roles
        imobile = {}
        for label, batch, number, sector in (("eterra", "20250101", 7339, None),
                                             ("livrare", "20260101", 501, 3)):
            layer = helpers.polygon_layer(f"Imobile {label}",
                                          [(square(0, 0, 40), [number, 50124, sector])],
                                          fields="id:integer,ie:integer,sector:integer")
            with_ie = buildings_ie or label == "livrare"
            constructii = helpers.polygon_layer(
                f"Construcții {label}",
                [(square(5, 5, 5), [number, "C1"] + ([50124] if with_ie else []))],
                fields="id:integer,nr_constr:string" + (",ie_imobil:integer" if with_ie else ""))
            roles.mark_columns(layer, [("id", "id"), ("ie", "ie"), ("sector", "sector")])
            roles.mark_columns(constructii, [("id", "id"), ("nr_constr", "nr_constr")]
                               + ([("ie_imobil", "ie_imobil")] if with_ie else []))
            for converted in (layer, constructii):
                roles.mark_batch(converted, batch)
            self.project.addMapLayers([layer, constructii])
            imobile[label] = layer
        return imobile["eterra"], imobile["livrare"]

    def test_compare_dialog_pairs_eterra_buildings_by_the_ie_of_their_imobil(self):
        eterra, livrare = self.eterra_and_delivery()
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(eterra)
        dialog.new_combo.setLayer(livrare)
        self.assertTrue(dialog.buildings_box.isChecked())
        self.assertEqual([dialog._building_key("old"), dialog._building_key("new")],
                         [["ie_imobil", "nr_constr"], ["ie_imobil", "nr_constr"]])
        dialog.deleteLater()

    def test_compare_dialog_leaves_eterra_buildings_without_the_ie_of_their_imobil(self):
        eterra, livrare = self.eterra_and_delivery(buildings_ie=False)
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(eterra)
        dialog.new_combo.setLayer(livrare)
        self.assertFalse(dialog.buildings_box.isChecked())
        dialog.deleteLater()

    def test_compare_dialog_keys_converted_imobile_like_the_cgxml_comparison(self):
        from cadex.services import roles

        def converted(name, batch, rows):
            layer = helpers.polygon_layer(name, [(square(0, 0, 10), row) for row in rows],
                                          fields="id:integer,ie:integer,sector:integer")
            roles.mark_columns(layer, [("id", "id"), ("ie", "ie"), ("sector", "sector")])
            roles.mark_batch(layer, batch)
            self.project.addMapLayer(layer)
            return layer

        # a new imobil gets its IE at registration, so most of a delivery has none yet
        v1 = converted("Imobile v1", "20250101", [[1101, None, 3], [1102, 50124, 3]])
        v2 = converted("Imobile v2", "20260101", [[1101, None, 3], [1102, 50124, 3]])
        # an eTerra export has no cadastral sector, and the cadastral numbers some of its
        # imobile have are not the IDs of a delivery
        eterra = converted("Imobile eTerra", "20240101", [[None, 50124, None],
                                                          [7339, 50125, None]])
        dialog = CompareDialog(iface=self.iface)
        keys = lambda: (dialog.old_key.currentData(), dialog.new_key.currentData())  # noqa: E731
        dialog.old_combo.setLayer(v1)
        dialog.new_combo.setLayer(v2)
        self.assertEqual(keys(), ("id", "id"))
        self.assertEqual(dialog.eterra_hint.text(), "")
        dialog.old_combo.setLayer(eterra)
        self.assertEqual(keys(), ("ie", "ie"))
        # the files compare with the eTerra rules
        self.assertIn("Layerul vechi este un export eTerra convertit", dialog.eterra_hint.text())
        self.assertIn("comparați fișierele CGXML, potrivite după IE", dialog.eterra_hint.text())
        # a key chosen by hand stays
        dialog.old_combo.setLayer(v1)
        for combo in (dialog.old_key, dialog.new_key):
            combo.setCurrentIndex(combo.findData("ie"))
            combo.activated.emit(combo.currentIndex())
        dialog.new_combo.setLayer(eterra)
        dialog.new_combo.setLayer(v2)
        self.assertEqual(keys(), ("ie", "ie"))
        dialog.deleteLater()

    def test_compare_dialog_ticks_the_columns_of_the_cgxml_comparison(self):
        from cadex.services import roles
        layers = {}
        for label, batch in (("v1", "20250101"), ("v2", "20260101")):
            imobile = helpers.polygon_layer(f"Imobile {label}", [(square(0, 0, 40), [1, "A", "X"])],
                                            fields="id:integer,propr:string,uat:string")
            constructii = helpers.polygon_layer(
                f"Construcții {label}", [(square(5, 5, 5), [1, "C1", "CL", "A"])],
                fields="id:integer,nr_constr:string,destinatie:string,propr:string")
            roles.mark_columns(imobile, [(k, k) for k in ("id", "propr", "uat")])
            roles.mark_columns(constructii, [(k, k) for k in ("id", "nr_constr", "destinatie",
                                                              "propr")])
            for layer in (imobile, constructii):
                roles.mark_batch(layer, batch)
            self.project.addMapLayers([imobile, constructii])
            layers[label] = imobile
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(layers["v1"])
        dialog.new_combo.setLayer(layers["v2"])
        # the SIRUTA names repeat the address; a construcție repeats the owner of its imobil
        self.assertEqual(dialog.field_list.texts(), ["propr", "uat"])
        self.assertEqual(dialog.field_list.checked_texts(), ["propr"])
        self.assertEqual(dialog.building_fields.texts(), ["destinatie", "propr"])
        self.assertEqual(dialog.building_fields.checked_texts(), ["destinatie"])
        # plain layers: every column both have
        dialog.old_combo.setLayer(self.imobile_s1)
        dialog.new_combo.setLayer(self.imobile_s2)
        self.assertEqual(dialog.field_list.checked_texts(), ["Proprietar"])
        dialog.deleteLater()

    def test_compare_dialog_column_ticks_follow_the_layers_until_set_by_hand(self):
        from cadex.services import roles

        def imobile(label, batch=None):
            layer = helpers.polygon_layer(f"Imobile {label}", [(square(0, 0, 40), [1, "A", "X"])],
                                          fields="id:integer,propr:string,uat:string")
            if batch:
                roles.mark_columns(layer, [(k, k) for k in ("id", "propr", "uat")])
                roles.mark_batch(layer, batch)
            self.project.addMapLayer(layer)
            return layer

        plain_old, plain_new = imobile("a"), imobile("b")
        v1, v2 = imobile("v1", "20250101"), imobile("v2", "20260101")
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(plain_old)
        dialog.new_combo.setLayer(plain_new)
        # plain layers: every column both have
        self.assertEqual(dialog.field_list.checked_texts(), dialog.field_list.texts())
        # two conversions: the columns of the CGXML comparison, whatever was ticked before
        dialog.old_combo.setLayer(v1)
        dialog.new_combo.setLayer(v2)
        self.assertEqual(dialog.field_list.checked_texts(), ["propr"])
        # a column ticked by hand stays ticked
        item = dialog.field_list.list.findItems("uat", Qt.MatchFlag.MatchExactly)[0]
        item.setCheckState(Qt.CheckState.Checked)
        dialog.new_combo.setLayer(plain_new)
        dialog.new_combo.setLayer(v2)
        self.assertEqual(dialog.field_list.checked_texts(), ["propr", "uat"])
        # and so does "Niciunul"
        dialog.field_list.set_all(False)
        dialog.old_combo.setLayer(plain_old)
        dialog.old_combo.setLayer(v1)
        self.assertEqual(dialog.field_list.checked_texts(), [])
        dialog.deleteLater()

    def test_compare_dialog_takes_the_buildings_converted_with_the_imobile(self):
        old, old_b = self.converted("2025", "20250101", [(5, "C1", 25.0)])
        new, new_b = self.converted("2026", "20260101", [(5, "C1", 25.0)])
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        self.assertTrue(dialog.buildings_box.isChecked())
        self.assertEqual([dialog.building_combos[side].currentLayer().id()
                          for side in ("old", "new")], [old_b.id(), new_b.id()])
        self.assertEqual(dialog._building_key("old"), ["id", "nr_constr"])
        self.assertEqual(dialog._building_key("new"), ["id", "nr_constr"])
        self.assertEqual(dialog.building_fields.texts(), ["sup_mas"])
        # plain layers come without buildings
        dialog.new_combo.setLayer(self.imobile_s2)
        self.assertFalse(dialog.buildings_box.isChecked())
        dialog.new_combo.setLayer(new)
        self.assertTrue(dialog.buildings_box.isChecked())
        # a choice made by hand stays
        dialog.buildings_box.setChecked(False)
        dialog.buildings_box.clicked.emit(False)
        dialog.old_combo.setLayer(self.imobile_s1)
        dialog.old_combo.setLayer(old)
        self.assertFalse(dialog.buildings_box.isChecked())
        dialog.deleteLater()

    def test_compare_dialog_never_offers_a_row_number_as_building_key(self):
        constructii = helpers.gpkg_layer(helpers.polygon_layer(
            "Construcții gpkg", [(square(5, 5, 5), [1101, "C1"])],
            fields="id:integer,nr_constr:string"), os.path.join(self.tmp, "c.gpkg"))
        self.project.addMapLayer(constructii)
        dialog = CompareDialog(iface=self.iface)
        dialog.building_combos["old"].setLayer(constructii)
        imobil, number = dialog.building_keys["old"]
        for combo in (imobil, number):
            names = [combo.itemData(i) for i in range(combo.count())]
            self.assertIn("nr_constr", names)
            self.assertNotIn("fid", names)
        dialog.deleteLater()

    def test_compare_dialog_compares_the_buildings_too(self):
        old, old_b = self.converted("2025", "20250101", [(5, "C1", 25.0), (15, "C2", 25.0)])
        new, new_b = self.converted("2026", "20260101", [(5, "C1", 26.0), (25, "C3", 25.0)])
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        dialog.excel_row.edit.setText("")
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(len(started), 1, self.bar_text(dialog))
        task = started[0]
        self.assertEqual((task.buildings.key_old, task.buildings.key_new),
                         (["id", "nr_constr"], ["id", "nr_constr"]))
        self.assertEqual(task.buildings.fields, ["sup_mas"])
        self.assertTrue(task.run(), task.error_msg)

        shown = []
        dialog.show_result = lambda headline, **kw: shown.append((headline, kw))
        dialog._on_layers_finished(True, task)
        headline, kw = shown[0]
        self.assertIn("Layerul vechi de construcții are 2 înregistrări, cel nou 2 înregistrări.",
                      headline)
        layers = {layer.name(): layer for layer in kw["layers"]}
        self.assertEqual(sorted(layers), ["Construcții apărute [Construcții 2026]",
                                          "Construcții dispărute [Construcții 2025]",
                                          "Construcții modificate [Construcții 2026]"])
        modified = layers["Construcții modificate [Construcții 2026]"]
        changed = next(modified.getFeatures())
        self.assertEqual((changed["nr_constr"], changed["modificari"], changed["tip_modif"]),
                         ("C1", "sup_mas", "Doar datele"))
        # one legend entry per kind of change, so each can be hidden
        self.assertEqual(modified.renderer().type(), "categorizedSymbol")
        self.assertEqual([c.value() for c in modified.renderer().categories()], ["Doar datele"])
        counts = [c for c in kw["counts"] if c is not None]
        self.assertIn(("Construcții apărute", 1), [c[:2] for c in counts])
        self.assertIn(("Construcții nemodificate", 0), [c[:2] for c in counts])
        self.assertIn(("Nemodificate", 1), [c[:2] for c in counts])
        labels = [c[0] for c in counts]
        position = labels.index("Construcții modificate")
        self.assertEqual(counts[position + 1:position + 4],
                         [("   Doar limita", 0, "#E3A008"), ("   Doar datele", 1, "#7C5CC4"),
                          ("   Limita și datele", 0, "#C2410C")])
        dialog.deleteLater()

    def test_the_case_option_starts_ticked_only_for_an_eterra_export_and_a_delivery(self):
        # their texts are typed by different people, so 'EROILOR' and 'Eroilor' are one street
        dialog = CompareDialog(iface=self.iface)
        dialog.mode.set_index(0)
        self.assertFalse(dialog.fold_text.isChecked())
        dialog.mode.set_index(1)
        dialog.match_combo.setCurrentIndex(dialog.match_combo.findData(cgxml_compare.FILENAME))
        self.assertFalse(dialog.fold_text.isChecked())
        dialog.match_combo.setCurrentIndex(dialog.match_combo.findData(cgxml_compare.IE))
        self.assertTrue(dialog.fold_text.isChecked())
        # each kind of comparison keeps its own choice
        dialog.fold_text.setChecked(False)
        dialog.match_combo.setCurrentIndex(dialog.match_combo.findData(cgxml_compare.FILENAME))
        dialog.fold_text.setChecked(True)
        dialog.mode.set_index(0)
        self.assertFalse(dialog.fold_text.isChecked())
        dialog.mode.set_index(1)
        self.assertTrue(dialog.fold_text.isChecked())
        dialog.save_remembered()
        dialog.deleteLater()
        again = CompareDialog(iface=self.iface)
        again.mode.set_index(1)
        again.match_combo.setCurrentIndex(again.match_combo.findData(cgxml_compare.IE))
        self.assertFalse(again.fold_text.isChecked())
        again.match_combo.setCurrentIndex(again.match_combo.findData(cgxml_compare.FILENAME))
        self.assertTrue(again.fold_text.isChecked())
        again.deleteLater()

    def test_compare_dialog_needs_the_number_of_each_building(self):
        old, _old_b = self.converted("2025", "20250101", [(5, "C1", 25.0)])
        new, _new_b = self.converted("2026", "20260101", [(5, "C1", 25.0)])
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        dialog.excel_row.edit.setText("")
        dialog.building_keys["new"][1].setCurrentIndex(0)
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("numărul construcției", self.bar_text(dialog))
        # the imobil column on one side only would never match
        dialog.building_keys["new"][1].setCurrentIndex(
            dialog.building_keys["new"][1].findData("nr_constr"))
        dialog.building_keys["new"][0].setCurrentIndex(0)
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("coloana imobilului", self.bar_text(dialog))
        dialog.deleteLater()

    def test_compare_dialog_cgxml_building_columns(self):
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        for folder in (old, new):
            synthetic.write(folder, "1.cgxml", synthetic.Land(
                rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        dialog = CompareDialog(iface=self.iface)
        dialog.mode.set_index(1)
        defaults = [key for key, _label in cgxml_compare.comparable_building_columns()
                    if key in cgxml_compare.BUILDING_COLUMNS]
        self.assertEqual(dialog.cgxml_building_fields.checked_data(), defaults)
        dialog.buildings.setChecked(False)
        self.assertFalse(dialog.cgxml_column_tabs.isTabEnabled(1))
        dialog.buildings.setChecked(True)
        self.assertTrue(dialog.cgxml_column_tabs.isTabEnabled(1))

        building_list = dialog.cgxml_building_fields.list
        first = next(building_list.item(i) for i in range(building_list.count())
                     if building_list.item(i).data(Qt.ItemDataRole.UserRole) == defaults[0])
        first.setCheckState(Qt.CheckState.Unchecked)
        wanted = dialog.cgxml_building_fields.checked_data()
        self.assertEqual(wanted, defaults[1:])
        dialog.old_source.set_paths([old])
        dialog.new_source.set_paths([new])
        dialog.excel_row.edit.setText("")
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started[0].building_columns, wanted)
        dialog.deleteLater()
        again = CompareDialog(iface=self.iface)
        self.assertEqual(again.cgxml_building_fields.checked_data(), wanted)
        again.deleteLater()

    def test_compare_result_has_notes_on_what_an_eterra_export_cannot_compare(self):
        eterra, livrare = os.path.join(self.tmp, "eterra"), os.path.join(self.tmp, "livrare")
        synthetic.write(eterra, "70002.cgxml", synthetic.Land(
            rings=[], measured_area=400.0, ie="70002", style="eterra"))
        synthetic.write(livrare, "503.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="503", ie="70002",
            sector="3"))
        task = cgxml_compare.CgxmlCompareTask(old_paths=[eterra], new_paths=[livrare],
                                              mode=cgxml_compare.IE)
        self.assertTrue(task.run(), task.error_msg)
        dialog = CompareDialog(iface=self.iface)
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append(kw)
        dialog._on_cgxml_finished(True, task)
        notes = " ".join(shown[0]["notes"])
        self.assertIn("1 imobil din exportul eTerra nu are coordonate", notes)
        self.assertIn("necompletat", notes)
        dialog.deleteLater()

    def test_diminuare_dialog(self):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "w", encoding="utf-8-sig") as fp:
            fp.write("Nr;Suprafata act;IE\n1;6000;\n2;4000;\n3;2000;50124\n")
        dialog = DiminuareDialog(iface=self.iface)
        # the contour layer comes from the "contur" name pattern
        self.assertEqual(dialog.layer_combo.currentLayer().name(), "Contur tarla 12")
        dialog.file_row.set_path(path)
        dialog._load_file()
        self.assertEqual(dialog.area_combo.currentData(), "Suprafata act")
        self.assertEqual(dialog.fixed_combo.currentData(), "IE")
        # nothing to compute before the contour area is known, and the status line shows why
        self.assertIsNone(dialog._result)
        self.assertIn("suprafața conturului", dialog.status.text())
        self.assertFalse(dialog.save_btn.isEnabled())
        dialog.selected_only.setChecked(False)
        dialog._take_area()
        self.assertEqual(dialog.contour_spin.value(), 10000.0)
        # the result follows the inputs, no button to press
        dialog.round_combo.setCurrentIndex(dialog.round_combo.findData(2))
        self.assertTrue(dialog.save_btn.isEnabled())
        result = dialog._result[0]
        self.assertAlmostEqual(result.factor, 0.8)
        self.assertEqual(result.new_areas, [4800.0, 3200.0, 2000.0])
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual(dialog.table.item(2, 0).text(), "4")  # sheet row numbers, header on row 1
        shoot(dialog, "diminuare")
        dialog.close()
        dialog.deleteLater()

    def test_diminuare_takes_the_selected_contour_or_the_only_one(self):
        dialog = DiminuareDialog(iface=self.iface)
        # one contour and nothing selected: that contour
        self.assertFalse(dialog.selected_only.isChecked())
        dialog._take_area()
        self.assertEqual(dialog.contour_spin.value(), 10000.0)
        self.contur.selectAll()
        self.assertTrue(dialog.selected_only.isChecked())
        self.contur.removeSelection()
        self.assertFalse(dialog.selected_only.isChecked())
        # several contours, e.g. every tarla of the sector: the selected one, never their sum
        tarlale = helpers.polygon_layer("Contur tarlale", [(square(0, 0, 100), [1]),
                                                           (square(200, 0, 100), [2])])
        self.project.addMapLayer(tarlale)
        dialog.layer_combo.setLayer(tarlale)
        self.assertTrue(dialog.selected_only.isChecked())
        # a click on the box is the user's choice, kept from then on
        dialog.selected_only.click()
        tarlale.selectByIds([next(tarlale.getFeatures()).id()])
        self.assertFalse(dialog.selected_only.isChecked())
        dialog.close()
        dialog.deleteLater()

    def test_diminuare_table_keeps_its_rows_in_a_short_window(self):
        from cadex.dialogs.diminuare_dialog import TABLE_MIN_ROWS
        dialog = DiminuareDialog(iface=self.iface)
        dialog.show()
        dialog.resize(dialog.width(), 500)
        QApplication.processEvents()
        self.assertEqual(dialog.height(), 500)
        row = dialog.table.verticalHeader().defaultSectionSize()
        self.assertGreaterEqual(dialog.table.viewport().height(), TABLE_MIN_ROWS * row)
        # the body scrolls instead
        self.assertGreater(dialog._scroll.verticalScrollBar().maximum(), 0)
        dialog.close()
        dialog.deleteLater()

    def test_a_window_that_grows_stays_on_the_screen(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.show()
        QApplication.processEvents()
        available = QApplication.primaryScreen().availableGeometry()
        # placed for a short window near the bottom, as Qt centres it before the body is
        # measured; then the window grows to the full height of the body
        dialog.resize(dialog.width(), 150)
        dialog.move(available.left() + 10, available.bottom() - 200)
        dialog._fit_height_now()
        self.assertGreater(dialog.height(), 150)
        frame = dialog.frameGeometry()
        self.assertTrue(available.contains(frame), (available, frame))
        dialog.close()
        dialog.deleteLater()

    def test_a_window_closed_partly_off_the_screen_reopens_on_it(self):
        dialog = DiminuareDialog(iface=self.iface)
        dialog.show()
        QApplication.processEvents()
        available = QApplication.primaryScreen().availableGeometry()
        dialog.move(available.right() - 50, available.bottom() - 50)
        dialog.close()
        dialog.deleteLater()
        again = DiminuareDialog(iface=self.iface)
        again.show()
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            QApplication.processEvents()
        frame = again.frameGeometry()
        self.assertTrue(available.contains(frame), (available, frame))
        again.close()
        again.deleteLater()

    def test_topology_check_dialog(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        self.assertEqual(self.names(dialog.imobile), ["IMOBILE sector 2", "Imobile_S1"])
        self.assertEqual(self.names(dialog.constructii), ["Construcţii"])
        self.assertEqual(dialog.id_combo.currentData(), "ID_IMOBIL")
        # no sector column in the fixtures, and the link is never guessed
        self.assertEqual(dialog.sector_combo.currentData(), "")
        self.assertEqual(dialog.link_combo.currentData(), "")
        # no layer is named like sector outlines, so the combo box stays empty
        self.assertIsNone(dialog.limit_combo.currentLayer())
        self.assertIs(dialog.source_stack.currentWidget(), dialog.layers_box)
        self.assertTrue(dialog.limits_group.isCollapsed())
        self.assertEqual(dialog.tolerances.title(), "Toleranțe (valorile implicite)")
        dialog.source_mode.set_index(1)
        self.assertIs(dialog.source_stack.currentWidget(), dialog.cgxml_box)
        shoot(dialog, "verificare_cgxml")
        dialog.source_mode.set_index(0)
        dialog.min_area.setValue(1.0)
        self.assertEqual(dialog.tolerances.title(), "Toleranțe (modificate)")
        dialog._reset_tolerances()
        self.assertEqual(dialog.tolerances.title(), "Toleranțe (valorile implicite)")
        dialog.roads_sector.setText("99")
        self.assertEqual(dialog.tolerances.title(), "Toleranțe (modificate)")
        dialog._reset_tolerances()
        self.assertEqual(dialog.roads_sector.text(), "0")
        dialog.limits_group.setChecked(True)
        self.assertFalse(dialog.limits_group.isCollapsed())
        shoot(dialog, "verificare")
        dialog.deleteLater()

    def test_the_mouse_wheel_scrolls_the_window_past_its_fields(self):
        from qgis.PyQt.QtCore import QPoint, QPointF  # type: ignore
        from qgis.PyQt.QtGui import QWheelEvent  # type: ignore
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.tolerances.setCollapsed(False)
        dialog.show()
        QApplication.processEvents()
        before = (dialog.min_area.value(), dialog.id_combo.currentText())
        for widget in (dialog.min_area, dialog.id_combo):
            centre = widget.rect().center()
            event = QWheelEvent(
                QPointF(centre), QPointF(widget.mapToGlobal(centre)), QPoint(0, 0),
                QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(widget, event)
            # left unaccepted, a wheel event from the mouse goes on to the scrolling body;
            # Qt does not pass on one sent like this, so the scrolling cannot be checked here
            self.assertFalse(event.isAccepted(), widget)
        self.assertEqual((dialog.min_area.value(), dialog.id_combo.currentText()), before)
        dialog.close()
        dialog.deleteLater()

    def test_a_long_warning_wraps_instead_of_scrolling_in_one_line(self):
        from qgis.PyQt.QtWidgets import QLabel  # type: ignore
        dialog = DiminuareDialog(iface=self.iface)
        message = ("Nu este selectat niciun element în layerul 'Contur tarla 12'. Selectați "
                   "conturul pe hartă sau debifați 'Doar elementele selectate'.")
        dialog.warn(message)
        labels = [label for item in dialog.message_bar.items()
                  for label in item.findChildren(QLabel) if message in label.text()]
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].wordWrap())
        dialog.deleteLater()

    def test_a_question_comes_before_a_run_on_two_versions_of_the_same_imobile(self):
        copy = helpers.polygon_layer(
            "Imobile_S1 v2", [(square(0, 0, 101), ["A", "POPESCU"])],
            fields="ID_IMOBIL:string,Proprietar:string")
        self.project.addMapLayer(copy)
        for build in (TopologyCheckDialog, IntravilanDialog):
            dialog = build(iface=self.iface)
            dialog.imobile.set_checked_ids([self.imobile_s1.id(), copy.id()])
            questions = []
            dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
            dialog._run()
            self.assertEqual(len(questions), 1, build.__name__)
            self.assertIn("'Imobile_S1'", questions[0])
            self.assertIn("'Imobile_S1 v2'", questions[0])
            self.assertIn("A", questions[0])
            self.assertIsNone(dialog._task)
            # imobile split by sector share no identifier, and nothing is asked
            questions.clear()
            dialog.imobile.set_checked_ids([self.imobile_s1.id(), self.imobile_s2.id()])
            dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
            dialog.excel_row.edit.setText("")
            dialog._run()
            self.assertEqual(questions, [], build.__name__)
            dialog.cancel_task()
            dialog.deleteLater()

    def test_the_imobil_column_of_converted_buildings_is_preselected(self):
        from cadex.services import roles
        imobile = helpers.polygon_layer("Imobile conv", [(square(500, 0, 20), [1101])],
                                        fields="id:integer")
        constructii = helpers.polygon_layer("Construcții conv", [(square(505, 5, 5), [1101, "C1"])],
                                            fields="id:integer,nr_constr:string")
        roles.mark_columns(imobile, [("id", "id")])
        roles.mark_columns(constructii, [("id", "id"), ("nr_constr", "nr_constr")])
        self.project.addMapLayers([imobile, constructii])
        for build in (TopologyCheckDialog, BuildingTopologyDialog):
            dialog = build(iface=self.iface)
            dialog.constructii.set_checked_ids([constructii.id()])
            # the conversion stored the imobil column of the buildings
            self.assertEqual(dialog.link_combo.currentData(), "id", build.__name__)
            # a plain layer's 'id' may be the building's own number, so it is not guessed
            dialog.constructii.set_checked_ids([self.constructii.id()])
            self.assertEqual(dialog.link_combo.currentData(), "", build.__name__)
            # a choice made by hand stays
            dialog.constructii.set_checked_ids([constructii.id()])
            dialog.link_combo.setCurrentIndex(0)
            dialog.link_combo.activated.emit(0)
            dialog.constructii.set_checked_ids([])
            dialog.constructii.set_checked_ids([constructii.id()])
            self.assertEqual(dialog.link_combo.currentData(), "", build.__name__)
            dialog.deleteLater()

    def test_topology_check_result_layers(self):
        imobile = helpers.polygon_layer("Imobile T", [
            (square(0, 0, 10), ["1"]),
            (square(9.9, 0, 10), ["2"]),
            ("POLYGON((30 0, 40 10, 40 0, 30 10, 30 0))", ["3"]),
        ], fields="id:string")
        self.project.addMapLayer(imobile)
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id")
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        created = dialog.result_layers(task)
        self.assertEqual([layer.name() for layer in created],
                         ["Probleme topologice (suprafețe)", "Probleme topologice (puncte)"])
        areas, points = created
        # both in one group at the top of the layer tree
        group = self.project.layerTreeRoot().findGroup("Verificare Topologică")
        self.assertIsNotNone(group)
        self.assertEqual({node.layerId() for node in group.findLayers()},
                         {areas.id(), points.id()})
        self.assertEqual([f["tip"] for f in areas.getFeatures()], [tcs.OVERLAP])
        self.assertEqual([f["tip"] for f in points.getFeatures()], [tcs.INVALID])
        self.assertAlmostEqual(next(areas.getFeatures())["valoare"], 1.0, places=4)
        for layer in (areas, points):
            self.assertEqual(layer.renderer().type(), "categorizedSymbol")
            self.assertEqual(layer.crs().authid(), "EPSG:3844")
        headline, counts, notes = dialog.summary(task, created)
        self.assertEqual(headline, "S-au verificat 3 imobile, cu 0 construcții.")
        self.assertIn((tcs.OVERLAP, 1, tcs.KIND_COLORS[tcs.OVERLAP]), counts)
        self.assertIn("Probleme topologice (suprafețe)", notes[-1])
        dialog.deleteLater()

    def test_a_failure_while_showing_results_is_reported(self):
        from cadex.services.tasks import CadexTask

        class Quick(CadexTask):
            def run(self):
                return True

        called = []

        def broken(ok, _task):
            called.append(ok)
            raise AttributeError("'NoneType' object has no attribute 'type'")

        dialog = TopologyCheckDialog(iface=self.iface)
        failures = []
        dialog.fail = failures.append
        # in QGIS this hook is the generic "Python error" bar
        escaped = []
        previous_hook = sys.excepthook
        sys.excepthook = lambda *exc: escaped.append(exc)
        try:
            dialog.start_task(Quick("CADex: test", broken))
            end = time.monotonic() + 10
            while not called and time.monotonic() < end:
                QApplication.processEvents()
                time.sleep(0.01)
            QApplication.processEvents()
        finally:
            sys.excepthook = previous_hook
        self.assertEqual(called, [True])
        self.assertEqual(escaped, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("'NoneType' object has no attribute 'type'", failures[0])
        # the window is usable again
        self.assertFalse(dialog._processing)
        self.assertTrue(dialog._run_btn.isEnabled())
        dialog.deleteLater()

    def test_compare_dialog_cgxml_mode(self):
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        synthetic.write(old, "1.cgxml", synthetic.Land(rings=[synthetic.square(500000, 400000, 20)],
                                                       cadgenno="1"))
        synthetic.write(new, "1.cgxml", synthetic.Land(rings=[synthetic.square(500000, 400000, 21)],
                                                       cadgenno="1"))
        synthetic.write(new, "2.cgxml", synthetic.Land(rings=[synthetic.square(500100, 400000, 20)],
                                                       cadgenno="2"))
        dialog = CompareDialog(iface=self.iface)
        dialog.mode.set_index(1)
        self.assertIs(dialog.pages.currentWidget(), dialog.cgxml_page)
        self.assertTrue(dialog.buildings.isVisibleTo(dialog))
        dialog.old_source.set_paths([old])
        dialog.new_source.set_paths([new])
        dialog.old_source.refresh()
        dialog.new_source.refresh()
        self.assertEqual((dialog.old_source.count(), dialog.new_source.count()), (1, 2))
        shoot(dialog, "comparator_cgxml")

        shown = []
        dialog.show_result = lambda headline, **kw: shown.append((headline, kw))
        task = cgxml_compare.CgxmlCompareTask(old_paths=[old], new_paths=[new])
        self.assertTrue(task.run(), task.error_msg)
        dialog._on_cgxml_finished(True, task)
        headline, kw = shown[0]
        self.assertEqual(headline, "1 imobil găsit în ambele surse s-a modificat.")
        self.assertEqual([layer.name() for layer in kw["layers"]],
                         ["Imobile apărute", "Imobile modificate"])
        group = self.project.layerTreeRoot().findGroup("Comparator CGXML")
        self.assertIsNotNone(group)
        self.assertTrue(all(layer.crs().authid() == "EPSG:3844" for layer in kw["layers"]))
        dialog.deleteLater()

    def test_result_window(self):
        from cadex.dialogs.result_dialog import ResultDialog
        layer = helpers.polygon_layer("rezultat", [(square(0, 0, 100), [1])])
        self.project.addMapLayer(layer)
        path = os.path.join(self.tmp, "raport.xlsx")
        open(path, "w").close()
        dialog = ResultDialog(None, "Comparator", "S-au verificat 2 imobile.",
                              counts=[("Suprapunere", 1, "#D64545")], notes=["O notă."],
                              file_path=path, details="rând 1\nrând 2", warning=True,
                              layers=[layer], iface=self.iface)
        self.assertEqual(dialog.open_button.text(), "Deschide fișierul")
        self.assertIsNotNone(dialog.zoom_button)
        self.assertFalse(dialog.details.isVisibleTo(dialog))
        shoot(dialog, "rezultat")
        canvas = self.iface.mapCanvas()
        canvas.setDestinationCrs(layer.crs())
        dialog.zoom_button.click()
        self.assertTrue(canvas.extent().contains(layer.extent()))

    def test_a_long_result_stays_on_the_screen_and_scrolls(self):
        from qgis.PyQt.QtWidgets import QScrollArea, QToolButton  # type: ignore
        from cadex.dialogs.result_dialog import ResultDialog
        available = QApplication.primaryScreen().availableGeometry()
        # an eTerra comparison lists several long notes; a smaller screen fills up the same way
        notes = [f"Nota {i}: " + "valori completate doar într-una din surse " * 4
                 for i in range(30)]
        dialog = ResultDialog(None, "Comparator", "Gata.", notes=notes, details="rând")
        for expand in (False, True):
            if expand:
                dialog.findChildren(QToolButton)[0].click()
            dialog.show()
            end = time.monotonic() + 0.3
            while time.monotonic() < end:
                QApplication.processEvents()
            frame = dialog.frameGeometry()
            self.assertTrue(available.contains(frame), (expand, available, frame))
            self.assertGreater(dialog.findChild(QScrollArea).verticalScrollBar().maximum(), 0)
        dialog.close()
        dialog.deleteLater()

    def test_a_geopackage_result_opens_its_folder(self):
        from cadex.dialogs.result_dialog import ResultDialog
        path = os.path.join(self.tmp, "cgxml_livrare.gpkg")
        open(path, "w").close()
        opened = []
        original = _ui_helpers.open_path
        _ui_helpers.open_path = opened.append
        self.addCleanup(setattr, _ui_helpers, "open_path", original)
        dialog = ResultDialog(None, "Conversie CGXML", "Gata.", file_path=path)
        self.assertEqual(dialog.open_button.text(), "Deschide folderul")
        dialog.open_button.click()
        self.assertEqual(opened, [self.tmp])
        dialog.deleteLater()

    def test_enter_closes_the_result_window_and_opens_nothing(self):
        from qgis.PyQt.QtCore import Qt  # type: ignore
        from qgis.PyQt.QtTest import QTest  # type: ignore
        from cadex.dialogs.result_dialog import ResultDialog
        layer = helpers.polygon_layer("rezultat", [(square(0, 0, 100), [1])])
        self.project.addMapLayer(layer)
        path = os.path.join(self.tmp, "raport.xlsx")
        open(path, "w").close()
        opened = []
        original = _ui_helpers.open_path
        _ui_helpers.open_path = opened.append
        self.addCleanup(setattr, _ui_helpers, "open_path", original)
        dialog = ResultDialog(None, "Conversie CGXML", "Gata.", file_path=path,
                              details="rând", layers=[layer], iface=self.iface)
        dialog.show()
        QApplication.processEvents()
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        self.assertEqual(opened, [])
        self.assertFalse(dialog.isVisible())

    def test_help_opens_at_a_tool(self):
        dialog = HelpDialog(iface=self.iface)
        dialog.select_tool("compare_layers")
        self.assertEqual(dialog.tools.currentItem().text(), "Comparator")
        page = dialog.page.toPlainText()
        self.assertIn("Două seturi de fișiere CGXML", page)
        self.assertIn("Comparator (fișiere CGXML)", page)
        dialog.deleteLater()

    def test_mode_switch_is_remembered(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(1)
        dialog.save_remembered()
        dialog.deleteLater()
        again = TopologyCheckDialog(iface=self.iface)
        self.assertEqual(again.source_mode.index(), 1)
        self.assertIs(again.source_stack.currentWidget(), again.cgxml_box)
        again.deleteLater()

    def test_intravilan_without_geopackage_gives_a_temporary_layer(self):
        from cadex.services import intravilan_service
        task = intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(self.imobile_s1)], intravilan=[LayerSnapshot(self.intravilan)],
            excel_fields=[], id_field="ID_IMOBIL", owner_field=None, tolerance=0.5)
        self.assertTrue(task.run(), task.error_msg)
        dialog = IntravilanDialog(iface=self.iface)
        layer = dialog._result_layer(task)
        self.assertEqual(layer.providerType(), "memory")
        self.assertEqual(layer.featureCount(), 1)
        self.assertEqual(layer.crs().authid(), "EPSG:3844")
        self.assertEqual(layer.renderer().type(), "categorizedSymbol")
        # the legend has no rows for the statuses no imobil has
        self.assertEqual([c.value() for c in layer.renderer().categories()],
                         [intravilan_service.INTEGRAL_INTRA])
        dialog.deleteLater()

    def test_intravilan_does_not_overwrite_its_own_input(self):
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        path = os.path.join(self.tmp, "intravilan.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        # the table the report replaces
        options.layerName = "raport_intravilan"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            self.intravilan, path, QgsCoordinateTransformContext(), options)
        from_file = QgsVectorLayer(f"{path}|layername=raport_intravilan",
                                   "intravilan din fisier", "ogr")
        self.project.addMapLayer(from_file)
        dialog = IntravilanDialog(iface=self.iface)
        self.assertIn("intravilan din fisier", self.names(dialog.intravilan))
        dialog.gpkg_row.edit.setText(path)
        dialog.confirm = lambda *args: True
        dialog._run()
        self.assertIsNone(dialog._task)
        self.assertEqual(len(dialog.message_bar.items()), 1)
        self.assertIs(self.project.mapLayer(from_file.id()), from_file)
        self.assertTrue(os.path.exists(path))
        dialog.deleteLater()

    def test_settings_dialog(self):
        dialog = SettingsDialog(iface=self.iface)
        dialog._edits["roles/imobile"].setText("sector 2")
        self.assertIn("IMOBILE sector 2", dialog._matches["roles/imobile"].text())
        self.assertNotIn("Imobile_S1", dialog._matches["roles/imobile"].text())
        dialog._save()
        self.assertEqual(settings_service.get("roles/imobile"), "sector 2")
        dialog = SettingsDialog(iface=self.iface)
        dialog._reset()
        dialog._save()
        self.assertEqual(settings_service.get("roles/imobile"),
                         settings_service.DEFAULTS["roles/imobile"])
        shoot(SettingsDialog(iface=self.iface), "setari")

    def test_settings_list_only_the_layers_the_tools_would_preselect(self):
        _ui_helpers.add_layers([helpers.polygon_layer("Imobile apărute", [])], "Comparator")
        dialog = SettingsDialog(iface=self.iface)
        matches = dialog._matches["roles/imobile"].text()
        self.assertIn("Imobile_S1", matches)
        self.assertNotIn("Imobile apărute", matches)
        dialog.deleteLater()

    def test_help_dialog(self):
        dialog = HelpDialog(iface=self.iface)
        shoot(dialog, "ajutor")
        dialog.deleteLater()


class PluginTests(DialogTest):

    def test_init_and_unload(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.assertEqual(set(plugin.actions), {f.method for f in features.CATALOG})
        toolbar = self.iface.toolbars[0]
        self.assertEqual(len([a for a in toolbar.actions() if not a.isSeparator()]),
                         len([m for m in features.TOOLBAR if m]))
        self.assertEqual(len(self.iface.vector_menu), len(features.MENU))
        for action in plugin.actions.values():
            self.assertFalse(action.icon().isNull(), action.text())
            self.assertIn("<b>", action.toolTip())
        plugin.unload()
        self.assertEqual(plugin.actions, {})
        self.assertEqual(self.iface.vector_menu, [])

    def test_tooltips_keep_their_text_after_the_shortcut_registration(self):
        from qgis.gui import QgsGui  # type: ignore
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        action = plugin.actions["convert_cgxml"]
        intended = action.toolTip()
        # QGIS registers the main window's actions for its shortcut list some time after the
        # plugins are loaded, after the first event loop passes, which wraps the whole
        # tooltip in <b>
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            QApplication.processEvents()
        QgsGui.shortcutsManager().registerAction(action)
        self.addCleanup(QgsGui.shortcutsManager().unregisterAction, action)
        self.assertEqual(action.toolTip(), intended)

    def test_tool_windows_are_reused_and_removed_on_unload(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        first = plugin.compare_layers()
        self.assertFalse(first.isModal())
        self.assertIs(plugin.compare_layers(), first)
        first.reject()
        self.assertIs(plugin.compare_layers(), first)
        checklist = plugin.check_topology().imobile
        plugin.unload()
        self.assertEqual(plugin._dialogs, {})
        self.assertFalse(checklist._connected)

    def test_saved_names_reach_the_tool_windows(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        hidden = plugin.check_topology()
        hidden.reject()
        shown = plugin.compare_layers()
        settings_service.set_("roles/imobile", "sector 2")
        plugin._settings_saved()
        self.assertIs(plugin.compare_layers(), shown)
        rebuilt = plugin.check_topology()
        self.assertEqual(self.names(rebuilt.imobile), ["IMOBILE sector 2"])

    def test_a_reopened_window_shows_the_current_layer_names_and_fields(self):
        from qgis.core import QgsField  # type: ignore
        from cadex.qt_compat import FIELD_STRING
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        plugin.intravilan_report().reject()
        self.imobile_s1.setName("Imobile sector 1")
        self.imobile_s1.dataProvider().addAttributes([QgsField("NR_CAD", FIELD_STRING)])
        self.imobile_s1.updateFields()
        window = plugin.intravilan_report()
        self.assertIn("Imobile sector 1", window.imobile.texts())
        self.assertNotIn("Imobile_S1", window.imobile.texts())
        self.assertGreaterEqual(window.id_combo.findData("NR_CAD"), 0)

    def test_the_help_button_opens_the_plugin_help_at_the_tool(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        tool = plugin.check_topology()
        tool._on_help()
        help_window = plugin._dialogs["open_help"]
        self.assertEqual(help_window.tools.currentItem().text(), "Verificare Topologică")
        plugin.unload()

    def test_the_processing_provider_is_added_and_removed_with_the_plugin(self):
        from qgis.core import QgsApplication  # type: ignore
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.assertIsNotNone(QgsApplication.processingRegistry().providerById("cadex"))
        plugin.unload()
        self.assertIsNone(QgsApplication.processingRegistry().providerById("cadex"))

    def test_welcome_only_once(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.assertTrue(settings_service.get("general/welcome_shown"))
        count = len(self.iface.messageBar().items())
        plugin.unload()
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.assertEqual(len(self.iface.messageBar().items()), count)
        plugin.unload()


class ServicesMergedColumnsDialogTests(DialogTest):
    """Imobile layers whose columns differ only in the case of their names."""

    def test_a_join_column_brings_the_values_of_every_imobile_layer_whatever_its_spelling(self):
        first = helpers.polygon_layer("Imobile S1", [(square(0, 0, 100), ["P1"])],
                                      fields="COD:string")
        second = helpers.polygon_layer("Imobile S2", [(square(200, 0, 100), ["P2"])],
                                       fields="cod:string")
        constructii = helpers.polygon_layer(
            "Construcții", [(square(10, 10, 20), ["C1"]), (square(210, 10, 20), ["C2"])],
            fields="nr:string")
        self.project.clear()
        self.project.addMapLayers([first, second, constructii])
        dialog = BuildingTopologyDialog(iface=self.iface)
        dialog.imobile.set_checked_ids([first.id(), second.id()])
        dialog.constructii.set_checked_ids([constructii.id()])
        dialog.join_group.setChecked(True)
        spellings = [text for text in dialog.join_list.texts() if text.lower() == "cod"]
        self.assertTrue(spellings)
        for spelling in spellings:
            # only this entry of the column is ticked
            for i in range(dialog.join_list.list.count()):
                item = dialog.join_list.list.item(i)
                item.setCheckState(Qt.CheckState.Checked if item.text() == spelling
                                   else Qt.CheckState.Unchecked)
            started = []
            dialog.start_task = lambda task, message="": started.append(task)
            dialog._run()
            [task] = started
            self.assertEqual([field.name() for field in task.join_fields], ["COD"], spelling)
            self.assertTrue(task.run(), task.error_msg)
            self.assertEqual(
                {row["attrs"][0]: row["join"].get("COD") for row in task.results[0]},
                {"C1": "P1", "C2": "P2"}, spelling)
        dialog.deleteLater()


class WidgetsShellTest(DialogTest):
    """Helpers for the tests of the shared widgets, the base window and the plugin shell."""

    @staticmethod
    def settle(seconds=0.2):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    @staticmethod
    def wait(condition, timeout=30.0):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        return bool(condition())

    @staticmethod
    def count_rebuilds(checklist):
        """The list gets one entry per rebuild of the checklist after this call."""
        calls = []
        rebuild = checklist._rebuild

        def counted():
            calls.append(True)
            rebuild()
        checklist._rebuild = counted
        return calls

    @staticmethod
    def cgxml_files():
        return sorted(os.path.join(helpers.DATA_DIR, name) for name in os.listdir(helpers.DATA_DIR)
                      if name.endswith(".cgxml"))

    def count_listings(self):
        """The list gets the paths of every listing of CGXML sources after this call."""
        from cadex.services import cgxml_sources
        calls = []
        listing = cgxml_sources.list_sources

        def counted(paths, recursive=True):
            calls.append(paths)
            return listing(paths, recursive)
        cgxml_sources.list_sources = counted
        self.addCleanup(setattr, cgxml_sources, "list_sources", listing)
        return calls

    def out_of_reach(self, folder):
        """Every existence check of a path in folder waits, as on a network drive that is out
        of reach, until release is set or 3 s pass, then finds nothing. Returns (the threads
        that checked, release)."""
        import threading
        from unittest import mock
        release = threading.Event()
        threads = []

        def slow(real):
            def check(path):
                if str(path).startswith(folder):
                    threads.append(threading.current_thread())
                    release.wait(3)
                    return False
                return real(path)
            return check
        for name in ("exists", "isdir", "isfile"):
            patcher = mock.patch.object(os.path, name, slow(getattr(os.path, name)))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(release.set)
        return threads, release


class WidgetsFieldComboTests(WidgetsShellTest):

    def sector_layer(self):
        layer = helpers.polygon_layer(
            "Imobile sectoare", [(square(0, 0, 10), ["1", 0]), (square(10.05, 0, 10), ["2", 0])],
            fields="id:string,sector:integer")
        self.project.addMapLayer(layer)
        return layer

    def test_an_empty_entry_picked_by_the_user_stays_when_the_project_changes(self):
        layer = self.sector_layer()
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([layer.id()])
        self.assertEqual(dialog.sector_combo.currentData(), "sector")
        dialog.sector_combo.setCurrentIndex(0)
        dialog.sector_combo.activated.emit(0)
        dialog.show()
        self.project.addMapLayer(helpers.polygon_layer("Drumuri", []))
        self.settle()
        self.assertIn("Drumuri", dialog.imobile.texts())
        self.assertEqual(dialog.sector_combo.currentData(), "")
        dialog.close()
        dialog.deleteLater()

    def test_a_remembered_empty_entry_stays_when_the_project_changes(self):
        layer = self.sector_layer()
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([layer.id()])
        dialog.sector_combo.setCurrentIndex(0)
        dialog.sector_combo.activated.emit(0)
        dialog.save_remembered()
        dialog.deleteLater()
        again = TopologyCheckDialog(iface=self.iface)
        again.imobile.set_checked_ids([layer.id()])
        self.assertEqual(again.sector_combo.currentData(), "")
        self.project.addMapLayer(helpers.polygon_layer("Ape", []))
        self.assertIn("Ape", again.imobile.texts())
        self.assertEqual(again.sector_combo.currentData(), "")
        again.deleteLater()

    def test_a_field_combo_records_whether_the_user_chose(self):
        from qgis.PyQt.QtWidgets import QComboBox  # type: ignore
        from cadex.dialogs.widgets import user_chose
        combo = QComboBox()
        fill_field_combo(combo, ["Nr", "CF"], "fields/ie", "(niciunul)")
        self.assertEqual(combo.currentData(), "CF")
        self.assertFalse(user_chose(combo))
        combo.setCurrentIndex(0)
        combo.activated.emit(0)
        self.assertTrue(user_chose(combo))
        fill_field_combo(combo, ["Nr", "CF", "IE"], "fields/ie", "(niciunul)", combo.currentData())
        self.assertEqual(combo.currentData(), "")
        # when the chosen field is gone from the list, the preselection by name applies
        combo.setCurrentIndex(combo.findData("Nr"))
        combo.activated.emit(combo.currentIndex())
        fill_field_combo(combo, ["CF"], "fields/ie", "(niciunul)", combo.currentData())
        self.assertEqual(combo.currentData(), "CF")
        self.assertFalse(user_chose(combo))


class WidgetsChecklistTests(WidgetsShellTest):

    def shown_topology(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.show()
        self.settle()
        return dialog

    def test_a_burst_of_project_changes_rebuilds_a_shown_list_once(self):
        dialog = self.shown_topology()
        rebuilds = self.count_rebuilds(dialog.imobile)
        added = [helpers.polygon_layer(f"Strat {i}", []) for i in range(5)]
        for layer in added:
            self.project.addMapLayer(layer)
        for layer in added[:3]:
            layer.setName(layer.name() + " redenumit")
        self.settle()
        self.assertEqual(len(rebuilds), 1)
        self.assertIn("Strat 2 redenumit", dialog.imobile.texts())
        dialog.close()
        dialog.deleteLater()

    def test_a_closed_window_rebuilds_its_lists_when_it_opens_again(self):
        dialog = self.shown_topology()
        dialog.close()
        rebuilds = self.count_rebuilds(dialog.imobile)
        for i in range(3):
            self.project.addMapLayer(helpers.polygon_layer(f"Imobile noi {i}", []))
        self.settle()
        self.assertEqual(rebuilds, [])
        dialog.show()
        self.assertEqual(len(rebuilds), 1)
        listed = [dialog.imobile.list.item(i).text() for i in range(dialog.imobile.list.count())]
        self.assertIn("Imobile noi 2", listed)
        dialog.close()
        dialog.deleteLater()

    def test_reading_a_project_rebuilds_each_list_once(self):
        self.project.clear()
        layers = [helpers.gpkg_layer(
            helpers.polygon_layer(f"Imobile {i}", [(square(0, 0, 10), [i])]),
            os.path.join(self.tmp, f"imobile_{i}.gpkg")) for i in range(4)]
        self.project.addMapLayers(layers)
        path = os.path.join(self.tmp, "proiect.qgz")
        self.assertTrue(self.project.write(path))
        dialog = self.shown_topology()
        rebuilds = self.count_rebuilds(dialog.imobile)

        # QGIS shows the progress of the project, and processes events, between its layers
        def show_progress(_text):
            QApplication.processEvents()
        self.project.loadingLayer.connect(show_progress)
        try:
            self.assertTrue(self.project.read(path))
        finally:
            self.project.loadingLayer.disconnect(show_progress)
        self.settle()
        self.assertEqual(len(rebuilds), 1)
        self.assertEqual(dialog.imobile.texts(), [f"Imobile {i}" for i in range(4)])
        dialog.close()
        dialog.deleteLater()

    def test_a_layer_tooltip_shows_no_password(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        layer = QgsVectorLayer("Polygon?crs=EPSG:3844&field=id:integer&note= password=s3cret ",
                               "Imobile server", "memory")
        self.assertTrue(layer.isValid())
        self.project.addMapLayer(layer)
        checklist = LayerChecklist("roles/imobile")
        tips = [checklist.list.item(i).toolTip() for i in range(checklist.count())]
        self.assertTrue(any(tip.startswith("Imobile server") for tip in tips), tips)
        self.assertFalse(any("s3cret" in tip for tip in tips), tips)
        checklist.disconnect_project()


class WidgetsCgxmlSourceTests(WidgetsShellTest):

    def test_a_slow_source_is_counted_while_the_window_stays_usable(self):
        import threading
        from cadex.services import cgxml_sources
        release = threading.Event()
        listing = cgxml_sources.list_sources

        def slow(paths, recursive=True):
            # a folder on a network drive that is out of reach
            release.wait(5)
            return listing(paths, recursive)
        cgxml_sources.list_sources = slow
        self.addCleanup(setattr, cgxml_sources, "list_sources", listing)
        self.addCleanup(release.set)
        source = CgxmlSource()
        source.show()
        source.set_paths([helpers.DATA_DIR])
        started = time.monotonic()
        source.refresh()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(source.status.text(), "Se caută fișierele...")
        release.set()
        self.assertTrue(self.wait(lambda: "11" in source.status.text()), source.status.text())
        self.assertEqual(source.count(), 11)
        source.close()
        source.deleteLater()

    def test_a_remembered_source_of_a_mode_not_shown_is_not_read(self):
        from cadex.services import dialog_memory
        dialog_memory.save("CompareDialog", "old_cgxml", helpers.DATA_DIR)
        dialog_memory.save("CompareDialog", "mode", 0)
        listings = self.count_listings()
        dialog = CompareDialog(iface=self.iface)
        dialog.show()
        self.settle()
        self.assertEqual(listings, [])
        dialog.mode.set_index(1)
        self.assertTrue(self.wait(lambda: "11" in dialog.old_source.status.text()),
                        dialog.old_source.status.text())
        self.assertEqual(len(listings), 1)
        dialog.close()
        dialog.deleteLater()

    def test_leaving_the_field_unchanged_does_not_read_the_source_again(self):
        listings = self.count_listings()
        source = CgxmlSource()
        source.show()
        source.set_paths([helpers.DATA_DIR])
        source.refresh()
        self.assertEqual(source.count(), 11)
        source.edit.editingFinished.emit()
        self.settle()
        self.assertEqual(len(listings), 1)
        source.recursive.setChecked(False)
        self.assertEqual(source.count(), len(self.cgxml_files()))
        self.assertEqual(len(listings), 2)
        source.close()
        source.deleteLater()

    def test_return_counts_an_unchanged_path_again(self):
        from qgis.PyQt.QtTest import QTest  # type: ignore
        folder = os.path.join(self.tmp, "livrare")
        source = CgxmlSource()
        source.show()
        source.edit.setText(folder)
        QTest.keyClick(source.edit, Qt.Key.Key_Return)
        self.assertIsNone(source.count())
        self.assertEqual(source.status.text(), "Calea aleasă nu există.")
        # the network drive holding it is back
        shutil.copytree(helpers.DATA_DIR, folder)
        QTest.keyClick(source.edit, Qt.Key.Key_Return)
        self.assertEqual(source.count(), 11)
        self.assertEqual(source.status.text(), "S-au găsit 11 fișiere CGXML.")
        source.close()
        source.deleteLater()

    def test_conversion_opens_at_once_with_its_folders_out_of_reach(self):
        import threading
        from cadex.services import dialog_memory
        share = os.path.join(self.tmp, "nas")
        dialog_memory.save("CgxmlDialog", "input", os.path.join(share, "UAT"))
        dialog_memory.save("CgxmlDialog", "output", share)
        threads, release = self.out_of_reach(share)
        dialog = CgxmlDialog(iface=self.iface)
        dialog.show()
        self.settle()
        self.assertNotIn(threading.main_thread(), threads)
        self.assertEqual(dialog.source.status.text(), "Se caută fișierele...")
        self.assertEqual(dialog.targets_label.text(), "Se va crea cgxml_UAT.gpkg.")
        release.set()
        self.assertTrue(self.wait(lambda: dialog.source.status.text() != "Se caută fișierele..."))
        self.settle()
        self.assertNotIn(threading.main_thread(), threads)
        self.assertEqual(dialog.source.status.text(), "Calea aleasă nu există.")
        self.assertEqual(dialog.output_row.path(), share)
        self.assertEqual(dialog.targets_label.text(), "Se va crea cgxml_UAT.gpkg.")
        dialog.close()
        dialog.deleteLater()

    def test_a_source_out_of_reach_sets_no_output_folder(self):
        import threading
        from cadex.services import dialog_memory
        share = os.path.join(self.tmp, "nas")
        dialog_memory.save("CgxmlDialog", "input", os.path.join(share, "UAT"))
        threads, release = self.out_of_reach(share)
        dialog = CgxmlDialog(iface=self.iface)
        dialog.show()
        self.settle()
        release.set()
        self.assertTrue(self.wait(lambda: dialog.source.status.text() != "Se caută fișierele..."))
        self.settle()
        self.assertNotIn(threading.main_thread(), threads)
        self.assertEqual(dialog.source.status.text(), "Calea aleasă nu există.")
        self.assertEqual(dialog.output_row.path(), "")
        dialog.close()
        dialog.deleteLater()

    def test_conversion_names_the_files_it_would_replace(self):
        open(os.path.join(self.tmp, "cgxml_data.gpkg"), "wb").close()
        dialog = CgxmlDialog(iface=self.iface)
        dialog.source.set_paths([helpers.DATA_DIR])
        dialog.source.refresh()
        dialog.source.count()
        dialog.output_row.set_path(self.tmp)
        self.assertTrue(self.wait(lambda: "deja" in dialog.targets_label.text()),
                        dialog.targets_label.text())
        self.assertEqual(dialog.targets_label.text(),
                         "cgxml_data.gpkg există deja și va fi înlocuit.")
        dialog.base_edit.setText("alt_nume")
        self.settle()
        self.assertEqual(dialog.targets_label.text(), "Se va crea alt_nume.gpkg.")
        dialog.deleteLater()

    def test_a_damaged_archive_is_reported_in_romanian(self):
        archive = os.path.join(self.tmp, "livrare.zip")
        with open(archive, "wb") as stream:
            stream.write(b"o descarcare intrerupta")
        source = CgxmlSource()
        source.set_paths([archive])
        source.refresh()
        self.assertIsNone(source.count())
        self.assertEqual(source.status.text(),
                         "Arhiva ZIP 'livrare.zip' este deteriorată sau incompletă.")

    def test_missing_paths_are_named(self):
        present = self.cgxml_files()[0]
        source = CgxmlSource()
        source.set_paths([present, os.path.join(self.tmp, "lipsa.cgxml")])
        source.refresh()
        self.assertIsNone(source.count())
        self.assertEqual(source.status.text(), "Calea 'lipsa.cgxml' nu există.")
        source.set_paths([present, os.path.join(self.tmp, "nu_exista") + os.sep,
                          os.path.join(self.tmp, "lipsa.cgxml")])
        source.refresh()
        source.count()
        self.assertEqual(source.status.text(), "Căile 'nu_exista', 'lipsa.cgxml' nu există.")

    def test_a_path_typed_outside_the_quotes_is_reported(self):
        first, second = self.cgxml_files()[:2]
        source = CgxmlSource()
        source.edit.setText(f'"{first}" {second}')
        source.refresh()
        self.assertEqual(source.count(), 1)
        self.assertIn("scrieți fiecare cale între ghilimele", source.status.text())

    def test_dropping_a_delivery_selection_keeps_its_cgxml_files(self):
        from qgis.PyQt.QtCore import QMimeData, QPoint, QPointF, QUrl  # type: ignore
        from qgis.PyQt.QtGui import QDragEnterEvent, QDropEvent  # type: ignore
        files = []
        for stem in ("1001", "1002"):
            for extension in (".cgxml", ".pdf"):
                files.append(os.path.join(self.tmp, stem + extension))
                open(files[-1], "wb").close()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in files])
        source = CgxmlSource()
        enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        source.edit.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        source.edit.dropEvent(drop)
        self.assertEqual(source.paths(), [os.path.normpath(p) for p in files
                                          if p.endswith(".cgxml")])
        self.assertEqual(source.count(), 2)
        self.assertEqual(source.status.text(), "S-au găsit 2 fișiere CGXML. 2 fișiere care nu "
                                               "sunt .cgxml sau .zip au fost ignorate.")


class WidgetsPathTests(WidgetsShellTest):

    def test_a_path_copied_with_quotes_is_used_without_them(self):
        report = os.path.join(self.tmp, "raport.xlsx")
        save = PathRow("save", "", "Excel (*.xlsx)", ".xlsx")
        save.edit.setText(f'"{report}"')
        self.assertEqual(save.path(), report)
        table = os.path.join(self.tmp, "parcele.csv")
        open(table, "w").close()
        opened = PathRow("open", "", "Excel sau CSV (*.xlsx *.csv)")
        opened.edit.setText(f' "{table}" ')
        self.assertEqual(opened.path(), table)
        folder = PathRow("folder", "")
        folder.edit.setText(f'"{self.tmp}"')
        self.assertTrue(os.path.isdir(folder.path()))

    def test_a_report_picked_in_the_file_browser_is_asked_about_once(self):
        # the file browser shows no overwrite question; the window shows its own, with
        # Romanian buttons, before the run
        from qgis.PyQt.QtWidgets import QFileDialog  # type: ignore
        seen = {}
        original = QFileDialog.getSaveFileName

        def pick(*_args, **kwargs):
            seen.update(kwargs)
            return os.path.join(self.tmp, "raport.xlsx"), ""
        QFileDialog.getSaveFileName = staticmethod(pick)
        self.addCleanup(setattr, QFileDialog, "getSaveFileName", original)
        row = PathRow("save", "", "Excel (*.xlsx)", ".xlsx")
        row._browse()
        self.assertEqual(seen.get("options"), QFileDialog.Option.DontConfirmOverwrite)
        self.assertEqual(row.path(), os.path.join(self.tmp, "raport.xlsx"))


class WidgetsQuestionTests(WidgetsShellTest):

    def test_questions_leave_no_window_behind(self):
        from qgis.PyQt.QtCore import QCoreApplication, QEvent  # type: ignore
        from qgis.PyQt.QtWidgets import QMessageBox  # type: ignore
        original = QMessageBox.exec
        QMessageBox.exec = lambda box: 0
        self.addCleanup(setattr, QMessageBox, "exec", original)
        dialog = CgxmlDialog(iface=self.iface)
        for _question in range(5):
            self.assertFalse(dialog.confirm("Suprascriere", "Continuați?"))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(dialog.findChildren(QMessageBox), [])
        dialog.deleteLater()


class ShellOutputTests(WidgetsShellTest):

    def test_a_report_that_cannot_be_written_stops_the_run_before_it_starts(self):
        # no user, root included, can open a folder for writing, just as nobody can write a
        # workbook Excel holds open
        report = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(report)
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.excel_row.edit.setText(report)
        started, failures, questions = [], [], []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog.fail = failures.append
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or True
        dialog._run()
        self.assertEqual(started, [])
        self.assertEqual(questions, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("Fișierul 'raport.xlsx' nu poate fi scris", failures[0])
        dialog.deleteLater()

    def test_a_report_that_could_not_be_written_becomes_a_result_note(self):
        from cadex.services.tasks import CadexTask
        dialog = TopologyCheckDialog(iface=self.iface)
        task = CadexTask("CADex: test")
        self.assertEqual(dialog.report_notes(task), [])
        task.report_error = "Fișierul 'raport.xlsx' nu poate fi scris."
        self.assertEqual(dialog.report_notes(task), ["Fișierul 'raport.xlsx' nu poate fi scris."])
        dialog.deleteLater()

    def test_the_duplicate_question_gives_the_number_of_imobile_the_layers_share(self):
        rows = [(square(i * 20, 0, 10), [key, "X"]) for i, key in enumerate("ABC")]
        fields = "ID_IMOBIL:string,Proprietar:string"
        first = helpers.polygon_layer("Imobile v1", rows, fields=fields)
        second = helpers.polygon_layer("Imobile v2", rows + [(square(100, 0, 10), ["D", "Y"])],
                                       fields=fields)
        self.project.addMapLayers([first, second])
        dialog = IntravilanDialog(iface=self.iface)
        questions = []
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
        self.assertFalse(dialog.confirm_distinct([first, second], "ID_IMOBIL"))
        self.assertTrue(questions[0].startswith(
            "3 imobile apar în ambele layere, 'Imobile v1' și 'Imobile v2'"), questions[0])
        dialog.deleteLater()

    def gpkg_results(self, path, *names):
        """Layers of one GeoPackage, added to the project."""
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        layers = []
        for i, name in enumerate(names):
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = name
            if i:
                options.actionOnExistingFile = \
                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            QgsVectorFileWriter.writeAsVectorFormatV3(
                helpers.polygon_layer(name, [(square(0, 0, 10), [1])]), path,
                QgsCoordinateTransformContext(), options)
            layers.append(QgsVectorLayer(f"{path}|layername={name}", name, "ogr"))
        self.project.addMapLayers(layers)
        return layers

    def test_replacing_a_result_open_in_the_project_removes_its_layer_after_asking(self):
        path = os.path.join(self.tmp, "rezultat.gpkg")
        layer_id = self.gpkg_results(path, "Rezultat")[0].id()
        dialog = IntravilanDialog(iface=self.iface)
        questions = []
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or True
        self.assertTrue(dialog.release_files([path]))
        self.assertEqual(questions, [
            "Fișierul rezultat este deschis în proiect ca layerul 'Rezultat'. Pentru a-l "
            "înlocui, layerul trebuie eliminat din proiect. Continuați?"])
        self.assertIsNone(self.project.mapLayer(layer_id))
        dialog.deleteLater()

    def test_declining_to_replace_a_result_keeps_its_layers(self):
        path = os.path.join(self.tmp, "rezultat.gpkg")
        layers = self.gpkg_results(path, "Imobile", "Constructii")
        dialog = IntravilanDialog(iface=self.iface)
        questions = []
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
        self.assertFalse(dialog.release_files([path]))
        self.assertTrue(questions[0].startswith(
            "Fișierul rezultat este deschis în proiect ca layerele '"), questions[0])
        self.assertIn("Pentru a-l înlocui, layerele trebuie eliminate", questions[0])
        self.assertIn("'Imobile'", questions[0])
        self.assertIn("'Constructii'", questions[0])
        self.assertTrue(all(self.project.mapLayer(layer.id()) for layer in layers))
        other = os.path.join(self.tmp, "altul.gpkg")
        self.gpkg_results(other, "Altul")
        questions.clear()
        self.assertFalse(dialog.release_files([path, other]))
        self.assertTrue(questions[0].startswith("Fișierele rezultate sunt deschise în proiect "
                                                "ca layerele"), questions[0])
        dialog.deleteLater()


class ShellTaskTests(WidgetsShellTest):

    def topology_on(self, layer):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([layer.id()])
        dialog.constructii.set_checked_ids([])
        dialog.excel_row.edit.setText("")
        return dialog

    def test_a_check_runs_in_the_background_and_adds_its_layers(self):
        imobile = helpers.polygon_layer("Imobile T", [(square(0, 0, 10), ["1"]),
                                                      (square(9.9, 0, 10), ["2"])],
                                        fields="id:string")
        self.project.addMapLayer(imobile)
        dialog = self.topology_on(imobile)
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append((headline, kw))
        dialog._run()
        self.assertTrue(dialog._processing)
        self.assertTrue(self.wait(lambda: shown, 60))
        headline, kw = shown[0]
        self.assertEqual(headline, "S-au verificat 2 imobile, cu 0 construcții.")
        self.assertIn("Probleme topologice (suprafețe)", [layer.name() for layer in kw["layers"]])
        self.assertIn((tcs.OVERLAP, 1, tcs.KIND_COLORS[tcs.OVERLAP]), kw["counts"])
        self.assertFalse(dialog._processing)
        self.assertTrue(dialog._run_btn.isEnabled())
        dialog.deleteLater()

    def test_cancelling_a_check_gives_the_window_back_without_results(self):
        rows = [(square(x * 10.0, y * 10.0, 10.02), [f"{x}-{y}"])
                for x in range(60) for y in range(60)]
        imobile = helpers.polygon_layer("Imobile mari", rows, fields="id:string")
        self.project.addMapLayer(imobile)
        dialog = self.topology_on(imobile)
        shown, notices = [], []
        dialog.show_result = lambda headline, **kw: shown.append(headline)
        dialog.notice = notices.append
        dialog._run()
        self.assertTrue(self.wait(lambda: dialog._task is None or dialog._task.progress() > 0))
        self.assertIsNotNone(dialog._task, "the check ended before it could be cancelled")
        dialog.cancel_task()
        self.assertTrue(self.wait(lambda: dialog._task is None, 120))
        self.assertEqual(notices, ["Verificarea a fost anulată."])
        self.assertEqual(shown, [])
        self.assertIsNone(self.project.layerTreeRoot().findGroup("Verificare Topologică"))
        self.assertFalse(dialog._processing)
        self.assertTrue(dialog._run_btn.isEnabled())
        dialog.deleteLater()


class ShellSettingsTests(WidgetsShellTest):

    def test_help_opened_from_settings_takes_input_while_settings_is_open(self):
        from qgis.PyQt.QtCore import QEvent, QObject  # type: ignore
        from qgis.PyQt.QtWidgets import QToolButton  # type: ignore
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        blocked = []

        class Recorder(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Type.WindowBlocked and isinstance(obj, HelpDialog):
                    blocked.append(obj)
                return False

        recorder = Recorder()
        QApplication.instance().installEventFilter(recorder)
        self.addCleanup(QApplication.instance().removeEventFilter, recorder)
        opened = []

        def shown_help():
            return {w for w in QApplication.topLevelWidgets()
                    if isinstance(w, HelpDialog) and w.isVisible()}

        def press_help(settings):
            before = shown_help()
            button = next(b for b in settings.findChildren(QToolButton)
                          if b.toolTip() == "Cum se folosește acest instrument")
            button.click()
            QApplication.processEvents()
            opened.extend(shown_help() - before)
        press_help(plugin.open_settings())
        self.assertEqual(len(opened), 1)
        # a window that a modal window blocks gets WindowBlocked, hidden windows included
        self.assertNotIn(opened[0], blocked)

    def test_saving_unchanged_settings_keeps_the_tool_windows_as_left(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        convert = plugin.convert_cgxml()
        convert.source.set_paths([helpers.DATA_DIR])
        convert.format_combo.setCurrentIndex(convert.format_combo.findData("dxf"))
        convert.reject()
        topology = plugin.check_topology()
        topology.reject()
        plugin.open_settings()._save()
        self.assertIs(plugin.check_topology(), topology)
        again = plugin.convert_cgxml()
        self.assertIs(again, convert)
        self.assertEqual(again.source.paths(), [helpers.DATA_DIR])
        self.assertEqual(again.format_combo.currentData(), "dxf")

    def test_new_name_patterns_keep_the_windows_that_use_none(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        convert = plugin.convert_cgxml()
        convert.source.set_paths([helpers.DATA_DIR])
        convert.reject()
        topology = plugin.check_topology()
        topology.reject()
        settings_service.set_("roles/imobile", "sector 2")
        plugin._settings_saved()
        self.assertIsNot(plugin.check_topology(), topology)
        self.assertIs(plugin.convert_cgxml(), convert)

    def test_the_preview_lists_the_layers_the_tools_would_preselect(self):
        from cadex.services import roles
        self.project.addMapLayers([
            helpers.polygon_layer("Intravilan 2024", [(square(0, 0, 10), [1])]),
            helpers.polygon_layer("Limita intravilan linie", [("LINESTRING(0 0, 10 0)", [1])],
                                  geometry="LineString"),
            helpers.polygon_layer("intravilan_tabel", [], geometry="None"),
            helpers.polygon_layer("Contur intravilan", [(square(0, 0, 10), [1])])])
        for name, batch in (("Imobile v1", "20250101"), ("Imobile v2", "20260101")):
            layer = helpers.polygon_layer(name, [(square(0, 0, 10), [1101, "Cristian"])],
                                          fields="id:integer,uat:string")
            roles.mark_columns(layer, [("id", "id"), ("uat", "uat")])
            roles.mark_batch(layer, batch)
            self.project.addMapLayer(layer)
        dialog = SettingsDialog(iface=self.iface)
        self.assertEqual(dialog._matches["roles/intravilan"].text(),
                         "În proiectul deschis: Contur intravilan, Intravilan 2024, "
                         "limita intravilan")
        imobile = dialog._matches["roles/imobile"].text()
        self.assertIn("Imobile v2", imobile)
        self.assertNotIn("Imobile v1", imobile)
        self.assertEqual(dialog._matches["roles/contur"].text(),
                         "În proiectul deschis: Contur tarla 12")
        dialog.deleteLater()

    def test_diminuare_takes_no_intravilan_or_imobile_layer_as_its_contour(self):
        self.project.addMapLayer(helpers.polygon_layer("Contur intravilan",
                                                       [(square(0, 0, 500), [1])]))
        dialog = DiminuareDialog(iface=self.iface)
        self.assertEqual(dialog.layer_combo.currentLayer().name(), "Contur tarla 12")
        dialog.deleteLater()
        self.project.removeMapLayer(self.contur.id())
        self.project.addMapLayers([helpers.polygon_layer("Imobile tarla 12", []),
                                   helpers.polygon_layer("Perimetru tarla 12", [])])
        dialog = DiminuareDialog(iface=self.iface)
        self.assertEqual(dialog.layer_combo.currentLayer().name(), "Perimetru tarla 12")
        dialog.deleteLater()


class ShellHelpTests(WidgetsShellTest):

    def test_the_help_window_fits_on_a_small_screen(self):
        from qgis.PyQt.QtCore import QSettings  # type: ignore
        QSettings().remove("CADex/HelpDialog/geometry")
        dialog = HelpDialog(iface=self.iface)
        dialog.show()
        self.settle(0.3)
        available = QApplication.primaryScreen().availableGeometry()
        self.assertTrue(available.contains(dialog.frameGeometry()),
                        (available, dialog.frameGeometry()))
        dialog.close()
        dialog.deleteLater()

    def test_every_tab_of_a_short_help_window_scrolls_to_its_end(self):
        from qgis.PyQt.QtWidgets import QLabel, QScrollArea  # type: ignore
        dialog = HelpDialog(iface=self.iface)
        dialog.show()
        dialog.resize(dialog.minimumSize())
        for index in (1, 2):
            dialog.tabs.setCurrentIndex(index)
            self.settle()
            area = dialog.tabs.widget(index).findChild(QScrollArea)
            self.assertIsNotNone(area, index)
            label = area.widget().findChild(QLabel)
            reachable = area.viewport().height() + area.verticalScrollBar().maximum()
            self.assertGreaterEqual(reachable, label.heightForWidth(label.width()), index)
        dialog.close()
        dialog.deleteLater()


class ShellThemeTests(WidgetsShellTest):

    def test_the_chosen_mode_dims_like_the_run_button_while_a_task_runs(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.show()
        self.settle()
        dialog._set_running(True)
        self.settle()

        def fill(button):
            image = button.grab().toImage()
            return image.pixelColor(4, image.height() // 2).name()

        chosen = dialog.source_mode.buttons[dialog.source_mode.index()]
        self.assertEqual(fill(chosen), fill(dialog._run_btn))
        dialog._set_running(False)
        dialog.close()
        dialog.deleteLater()


class TopologyWindowTests(DialogTest):
    """What the Verificare Topologică window shows after a run."""

    @staticmethod
    def shown(dialog, task):
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append(dict(kw, headline=headline))
        dialog._on_finished(True, task)
        return shown[0]

    def test_a_source_where_no_imobil_could_be_read_is_not_shown_as_clean(self):
        task = tcs.TopologyCheckTask(cgxml_paths=os.path.join(helpers.DATA_DIR, "broken"),
                                     recursive=False)
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = self.shown(dialog, task)
        self.assertTrue(result["warning"])
        self.assertIn("Niciun imobil nu a putut fi verificat.", result["notes"])
        self.assertNotIn("Nu s-a găsit nicio problemă.", result["notes"])
        self.assertIn("Lista completă a problemelor este în detalii.", result["notes"])
        dialog.deleteLater()

    def test_a_building_link_without_the_imobil_id_is_refused(self):
        constructii = helpers.polygon_layer("Construcții legate", [(square(10, 10, 5), ["A"])],
                                            fields="imobil:string")
        self.project.addMapLayer(constructii)
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([self.imobile_s1.id()])
        dialog.constructii.set_checked_ids([constructii.id()])
        dialog.excel_row.edit.setText("")
        dialog.id_combo.setCurrentIndex(dialog.id_combo.findData(""))
        dialog.link_combo.setCurrentIndex(dialog.link_combo.findData("imobil"))
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("Alegeți și coloana din imobile la care trimite 'Imobilul construcției'.",
                      self.bar_text(dialog))
        dialog.id_combo.setCurrentIndex(dialog.id_combo.findData("ID_IMOBIL"))
        dialog._run()
        self.assertEqual(len(started), 1)
        dialog.deleteLater()

    def test_a_report_that_could_not_be_written_is_a_note_and_not_offered(self):
        imobile = helpers.polygon_layer("Imobile T", [(square(0, 0, 10), ["1"])],
                                        fields="id:string")
        # nobody can open a folder for writing, as nobody can write a workbook open in Excel
        report = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(report)
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     excel_path=report)
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = self.shown(dialog, task)
        self.assertIn(task.report_error, result["notes"])
        self.assertTrue(result["warning"])
        self.assertIsNone(result["file_path"])
        dialog.deleteLater()

    def test_the_same_imobil_in_two_files_is_named_in_a_note(self):
        folder = os.path.join(self.tmp, "livrare")
        for sub in (folder, os.path.join(folder, "vechi")):
            synthetic.write(sub, "501.cgxml", synthetic.Land(
                rings=[synthetic.square(500000, 400000, 100)], cadgenno="501", sector="3"))
        task = tcs.TopologyCheckTask(cgxml_paths=folder, recursive=True)
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = self.shown(dialog, task)
        self.assertEqual(result["headline"], "S-a verificat 1 imobil, cu 0 construcții.")
        self.assertIn("1 imobil apare în mai multe fișiere: 501. S-a verificat dintr-un singur "
                      "fișier.", result["notes"])
        self.assertIn("Lista completă a problemelor este în detalii.", result["notes"])
        self.assertIn(os.path.join("vechi", "501.cgxml"), result["details"])
        self.assertTrue(result["warning"])
        dialog.deleteLater()

    def test_an_outline_sector_no_imobil_carries_is_named_in_a_note(self):
        imobile = helpers.polygon_layer("Imobile S", [(square(0, 0, 100), ["1", 5])],
                                        fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [(square(0, 0, 100), ["S5"])],
                                       fields="nr:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits),
                                     limit_field="nr")
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = self.shown(dialog, task)
        self.assertIn("Sectorul S5 din limite nu apare la niciun imobil.", result["notes"])
        self.assertTrue(result["warning"])
        dialog.deleteLater()

    def test_features_without_geometry_are_counted_in_a_note(self):
        from qgis.core import QgsFeature  # type: ignore
        imobile = helpers.polygon_layer("Imobile T", [(square(0, 0, 10), ["1"])],
                                        fields="id:string")
        empty = QgsFeature(imobile.fields())
        empty.setAttributes(["2"])
        imobile.dataProvider().addFeatures([empty])
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id")
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = self.shown(dialog, task)
        self.assertIn("1 element fără geometrie nu a fost verificat.", result["notes"])
        self.assertTrue(result["warning"])
        dialog.deleteLater()

    def test_a_finding_the_layer_cannot_hold_is_counted_in_a_note(self):
        from unittest import mock
        from qgis.core import Qgis, QgsFeature, QgsGeometry  # type: ignore
        real = tcs.findings_features

        def with_a_line(findings, fields, wkb):
            features = real(findings, fields, wkb)
            if wkb == Qgis.WkbType.Point:
                line = QgsFeature(fields)
                line.setGeometry(QgsGeometry.fromWkt("LINESTRING(450000 350000, 450001 350001)"))
                features.append(line)
            return features
        # the bow tie gives a point finding
        imobile = helpers.polygon_layer("Imobile T", [
            ("POLYGON((450030 350000, 450040 350010, 450040 350000, 450030 350010, "
             "450030 350000))", ["1"])], fields="id:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id")
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        with mock.patch.object(tcs, "findings_features", with_a_line):
            result = self.shown(dialog, task)
        notes = [n for n in result["notes"] if "nu a putut fi scrisă" in n]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("1 problemă din 2 nu a putut fi scrisă în layerul "
                                            "'Probleme topologice (puncte)'."), notes[0])
        self.assertTrue(result["warning"])
        dialog.deleteLater()


class BuildingTopologyWindowTests(DialogTest):
    """What the Topologie Construcții window shows after a run."""

    def finish(self, constructii):
        from cadex.services import building_topology_service as bts
        task = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(self.imobile_s1)], constructii=[LayerSnapshot(constructii)],
            buffer_m=0.002, threshold=1.0, join_fields=[])
        self.assertTrue(task.run(), task.error_msg)
        dialog = BuildingTopologyDialog(iface=self.iface)
        dialog.threshold.setValue(1.0)
        shown = []
        dialog.show_result = lambda headline, **kw: shown.append(kw)
        dialog._on_finished(True, task)
        dialog.deleteLater()
        return shown[0]

    def test_the_loss_note_names_the_state_column_of_the_result(self):
        # the layer has a 'stare' column of its own, so the result's state is in 'stare_2'
        constructii = helpers.polygon_layer("Construcții vechi",
                                            [(square(90, 10, 20), ["C2", "bună"])],
                                            fields="nr:string,stare:string")
        result = self.finish(constructii)
        self.assertIn("1 construcție pierde mai mult de 1 mp. Filtrați layerul după coloana "
                      "'stare_2' pentru a o verifica.", result["notes"])

    def test_a_building_the_layer_cannot_hold_is_counted_in_a_note(self):
        from unittest import mock
        from qgis.core import QgsFeature, QgsGeometry  # type: ignore
        from cadex.services import building_topology_service as bts
        real = bts.result_features

        def with_a_line(rows, fields, join_fields, wkb_type):
            features = real(rows, fields, join_fields, wkb_type)
            line = QgsFeature(fields)
            line.setGeometry(QgsGeometry.fromWkt("LINESTRING(450000 350000, 450001 350001)"))
            return features + [line]
        with mock.patch.object(bts, "result_features", with_a_line):
            result = self.finish(self.constructii)
        notes = [n for n in result["notes"] if "nu a putut fi scrisă" in n]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("1 construcție din 2 nu a putut fi scrisă în "
                                            "layerul 'Construcţii_corectat'."), notes[0])
        self.assertTrue(result["warning"])


class DiminuareWindowTests(WidgetsShellTest):
    """Diminuare Suprafețe in its window, on parcel tables as surveyors keep them."""

    def table(self, text, name="parcele.csv"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        return path

    def workbook(self, rows, hidden=()):
        """An .xlsx of rows; the Excel rows numbered in hidden are hidden the way a filter
        hides them."""
        import zipfile
        from cadex.services import xlsx
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        for r, values in enumerate(rows, start=1):
            for c, value in enumerate(values, start=1):
                sheet.set(r, c, value)
        wb.save(path)
        with zipfile.ZipFile(path) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        for number in hidden:
            parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
                f'<row r="{number}"'.encode(), f'<row r="{number}" hidden="1"'.encode())
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        return path

    def window(self, path, contour=None):
        dialog = DiminuareDialog(iface=self.iface)
        # the error and result windows are modal
        dialog.failures, dialog.results = [], []
        dialog.fail = dialog.failures.append
        dialog.show_result = lambda headline, **kwargs: dialog.results.append(headline)
        dialog.file_row.set_path(path)
        dialog._load_file()
        if contour is not None:
            dialog.contour_spin.setValue(contour)
        return dialog

    def saved_facts(self, dialog, name):
        """The Informații facts of the Sumar sheet that Salvează Excel writes."""
        from unittest import mock
        from cadex.dialogs import diminuare_dialog
        from cadex.services import xlsx
        path = os.path.join(self.tmp, name)
        with mock.patch.object(diminuare_dialog.QFileDialog, "getSaveFileName",
                               return_value=(path, "")):
            dialog._save()
        _names, rows = xlsx.read_rows(path, "Sumar")
        return {row[0]: row[1] for row in rows if row and row[0]}

    def test_a_total_row_under_the_parcels_is_left_out(self):
        from cadex.dialogs.diminuare_dialog import BAD_TINT
        path = self.table("Nr;Proprietar;Suprafata act;IE\n1;A;10000;\n2;B;20000;\n"
                          "3;C;15000;\nTOTAL;;45000;\n")
        dialog = self.window(path, 43200)
        result = dialog._result[0]
        self.assertEqual(result.new_areas[:3], [9600, 19200, 14400])
        # listed by its row number in the sheet and coloured like a row without an area
        self.assertIn("total", dialog.status.text())
        self.assertIn(": 5.", dialog.status.text())
        self.assertEqual(dialog.table.item(3, 1).background().color().name(), BAD_TINT.lower())
        dialog.deleteLater()

    def test_the_second_of_two_columns_with_the_same_name_is_read(self):
        path = self.table("Nr;Proprietar;Suprafata;CF;Suprafata\n1;A;1000;;900\n"
                          "2;B;2000;;1800\n")
        dialog = self.window(path, 2430)
        # the right-hand 'Suprafata', the last column
        dialog.area_combo.setCurrentIndex(dialog.area_combo.count() - 1)
        self.assertEqual(dialog._result[0].total_deed, 2700)
        dialog.deleteLater()

    def test_no_cf_ie_column_stays_chosen_after_a_reload_and_in_a_new_window(self):
        path = self.table("Nr;Suprafata act;IE\n1;6000;\n2;4000;\n3;2000;50124\n")
        dialog = self.window(path, 10000)
        self.assertEqual(dialog.fixed_combo.currentData(), "IE")
        self.assertEqual(dialog._result[0].total_fixed, 2000)
        # the user picks '(nicio parcelă nu are CF/IE)', so every parcel is reduced
        dialog.fixed_combo.setCurrentIndex(0)
        dialog.fixed_combo.activated.emit(0)
        self.assertEqual(dialog._result[0].total_fixed, 0)
        dialog._load_file(keep_sheet=True)
        self.assertEqual(dialog.fixed_combo.currentData(), "")
        self.assertEqual(dialog._result[0].total_fixed, 0)
        dialog.reject()
        dialog.deleteLater()
        again = DiminuareDialog(iface=self.iface)
        self.assertTrue(self.wait(lambda: again._rows))
        self.assertEqual(again.fixed_combo.currentData(), "")
        again.deleteLater()

    def test_half_a_square_metre_is_rounded_up_without_a_false_note(self):
        path = self.table("Nr;Suprafata act;IE\n1;501;50124\n2;600;\n")
        dialog = self.window(path, 1000.5)
        dialog.round_combo.setCurrentIndex(dialog.round_combo.findData(0))
        self.assertEqual(dialog._result[0].total_new, 1001)
        self.assertNotIn("zecimale", dialog.status.text())
        dialog.deleteLater()

    def test_a_table_that_cannot_be_read_clears_the_previous_result(self):
        dialog = self.window(self.table("Nr;Suprafata act\n1;6000\n2;4000\n"), 9000)
        self.assertTrue(dialog.save_btn.isEnabled())
        # an Excel 97-2003 file renamed to .xlsx
        renamed = os.path.join(self.tmp, "vechi.xlsx")
        with open(renamed, "wb") as fp:
            fp.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 not a zip archive")
        dialog.file_row.set_path(renamed)
        dialog._load_file()
        self.assertIsNone(dialog._result)
        self.assertFalse(dialog.save_btn.isEnabled())
        self.assertEqual(dialog.table.rowCount(), 0)
        dialog.deleteLater()

    def test_the_sumar_records_where_the_contour_area_came_from(self):
        dialog = self.window(self.table("Nr;Suprafata act\n1;6000\n2;4000\n"))
        self.assertEqual(dialog.layer_combo.currentLayer().name(), "Contur tarla 12")
        # typed from the documentation, with a contour layer in the combo box
        dialog.contour_spin.setValue(9500)
        facts = self.saved_facts(dialog, "manual.xlsx")
        self.assertEqual(facts["Contur"], "introdus manual")
        # a CSV has no sheets
        self.assertEqual(facts["Fișier sursă"], "parcele.csv")
        dialog.selected_only.setChecked(False)
        dialog._take_area()
        facts = self.saved_facts(dialog, "din_geometrie.xlsx")
        self.assertEqual(facts["Contur"], "Contur tarla 12, tot layerul (1 element)")
        dialog.contour_spin.setValue(9800)
        self.assertEqual(self.saved_facts(dialog, "corectat.xlsx")["Contur"], "introdus manual")
        dialog.deleteLater()

    def test_replacing_a_saved_result_is_asked_and_can_be_declined(self):
        from unittest import mock
        from cadex.dialogs import diminuare_dialog
        dialog = self.window(self.table("Nr;Suprafata act\n1;6000\n2;4000\n"), 9000)
        path = os.path.join(self.tmp, "rezultat.xlsx")
        with open(path, "wb") as fp:
            fp.write(b"ieri")
        questions = []
        dialog.confirm = lambda title, message, *a, **k: questions.append(message) or False
        with mock.patch.object(diminuare_dialog.QFileDialog, "getSaveFileName",
                               return_value=(path, "")):
            dialog._save()
        self.assertEqual(questions, ["Fișierul rezultat.xlsx există deja. Doriți să fie înlocuit?"])
        with open(path, "rb") as fp:
            self.assertEqual(fp.read(), b"ieri")
        dialog.deleteLater()

    def test_a_cf_ie_number_from_an_xlsx_shows_as_written(self):
        path = self.workbook([["Nr", "Suprafata act", "IE"], [1, 6000, None], [2, 4000, 50124]])
        dialog = self.window(path, 9000)
        self.assertEqual(dialog.table.item(1, 2).text(), "50124")
        dialog.deleteLater()

    def test_rows_hidden_in_excel_are_left_out_and_counted(self):
        path = self.workbook([["Nr", "Suprafata act"], [1, 1000], [2, 2000], [3, 3000]],
                             hidden=[3])
        dialog = self.window(path, 3600)
        self.assertEqual(dialog._result[0].total_deed, 4000)
        self.assertIn("ascuns în Excel", dialog.rows_label.text())
        self.assertIn(": 3.", dialog.rows_label.text())
        dialog.deleteLater()

    def test_many_hidden_rows_are_counted_in_the_sumar_and_the_first_listed(self):
        # a sector table filtered down to one tarla; a whole UAT can hide thousands of rows,
        # more numbers than an Excel cell holds
        rows = [["Nr", "Suprafata act"]] + [[n, 1000] for n in range(1, 61)]
        path = self.workbook(rows, hidden=range(3, 53))
        dialog = self.window(path, 9000)
        self.assertEqual(len(dialog._rows), 10)
        facts = self.saved_facts(dialog, "rezultat.xlsx")
        self.assertEqual(facts["Rânduri ascunse în Excel, lăsate deoparte"],
                         "50 de rânduri: " + ", ".join(str(n) for n in range(3, 18)) + "...")
        dialog.deleteLater()

    def test_a_negative_area_is_named_as_such(self):
        dialog = self.window(self.table("Nr;Suprafata act\n1;-100\n2;6000\n3;4000\n"), 9000)
        self.assertEqual(dialog.status.text(),
                         "1 rând cu suprafața negativă a rămas neschimbat: 2.")
        self.assertEqual(dialog._result[0].new_areas, [None, 5400, 3600])
        dialog.deleteLater()

    def test_a_remembered_table_out_of_reach_does_not_hold_up_the_window(self):
        import threading
        from cadex.services import dialog_memory
        share = os.path.join(self.tmp, "nas")
        dialog_memory.save("DiminuareDialog", "file", os.path.join(share, "parcele.xlsx"))
        threads, release = self.out_of_reach(share)
        started = time.monotonic()
        dialog = DiminuareDialog(iface=self.iface)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(self.wait(lambda: threads))
        self.assertNotIn(threading.main_thread(), threads)
        release.set()
        dialog.deleteLater()

    def test_a_remembered_table_is_read_once_the_window_is_open(self):
        from cadex.services import dialog_memory
        path = self.table("Nr;Suprafata act\n1;6000\n2;4000\n")
        dialog_memory.save("DiminuareDialog", "file", path)
        dialog = DiminuareDialog(iface=self.iface)
        self.assertTrue(self.wait(lambda: dialog._rows))
        self.assertEqual(dialog.area_combo.currentData(), "Suprafata act")
        dialog.deleteLater()


class IntravilanWindowTests(WidgetsShellTest):
    """Raport Intravilan / Extravilan run from its window, through the task manager."""

    def window(self, imobile, gpkg="", excel="", add=True):
        dialog = IntravilanDialog(iface=self.iface)
        dialog.imobile.set_checked_ids([layer.id() for layer in imobile])
        dialog.intravilan.set_checked_ids([self.intravilan.id()])
        dialog.gpkg_row.set_path(gpkg)
        dialog.excel_row.set_path(excel)
        dialog.add_to_project.setChecked(add)
        dialog.shown, dialog.failures, dialog.questions = [], [], []
        dialog.show_result = lambda headline, **kwargs: dialog.shown.append(kwargs)
        dialog.fail = dialog.failures.append
        dialog.confirm = lambda title, message, *a, **k: dialog.questions.append(message) or True
        return dialog

    def run_window(self, dialog):
        """Run the report and wait for the window to take its result back."""
        dialog._run()
        self.assertTrue(self.wait(lambda: dialog._task is None, 60))
        return dialog.shown[0] if dialog.shown else None

    def gpkg_layers(self, path, *names):
        """Tables of one GeoPackage, each opened as a project layer."""
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        layers = []
        for i, name in enumerate(names):
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = name
            if i:
                options.actionOnExistingFile = \
                    QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            QgsVectorFileWriter.writeAsVectorFormatV3(
                helpers.polygon_layer(name, [(square(1000, 1000, 10), [1])]), path,
                QgsCoordinateTransformContext(), options)
            layers.append(QgsVectorLayer(f"{path}|layername={name}", name, "ogr"))
        return layers

    def test_two_sector_geopackages_give_a_saved_layer_with_all_their_imobile(self):
        sectors = [helpers.gpkg_layer(helpers.polygon_layer(
            f"Imobile sector {n}", [(square(200 * i, 300 * n, 100), [f"{n}0{i}"])
                                    for i in range(1, n + 2)], fields="id:string"),
            os.path.join(self.tmp, f"sector{n}.gpkg")) for n in (1, 2)]
        self.project.addMapLayers(sectors)
        dialog = self.window(sectors, gpkg=os.path.join(self.tmp, "raport.gpkg"))
        shown = self.run_window(dialog)
        [layer] = shown["layers"]
        self.assertEqual(sorted(f["id"] for f in layer.getFeatures()),
                         ["101", "102", "201", "202", "203"])
        self.assertFalse(shown["warning"])
        dialog.deleteLater()

    def test_owner_columns_of_different_widths_keep_every_imobil_in_the_temporary_layer(self):
        long_owner = "POPESCU ION, POPESCU MARIA"
        first = helpers.polygon_layer("Imobile S1", [(square(0, 0, 100), ["A", "SCURT"])],
                                      fields="ID:string,propr:string(10)")
        second = helpers.polygon_layer("Imobile S2", [(square(200, 0, 100), ["B", long_owner]),
                                                      (square(400, 0, 100), ["C", "X"])],
                                       fields="id:string,PROPR:string(100)")
        self.project.addMapLayers([first, second])
        dialog = self.window([first, second])
        [layer] = self.run_window(dialog)["layers"]
        self.assertEqual(sorted(f["propr"] for f in layer.getFeatures()),
                         sorted(["SCURT", long_owner, "X"]))
        dialog.deleteLater()

    def test_a_spike_does_not_cut_the_temporary_layer_short(self):
        x, y = 450000, 350000
        spike = (f"POLYGON(({x} {y}, {x + 10} {y}, {x + 10} {y + 10}, {x + 5} {y + 10}, "
                 f"{x + 5} {y + 20}, {x + 5} {y + 10}, {x} {y + 10}, {x} {y}))")
        imobile = helpers.polygon_layer(
            "Imobile", [(spike, ["1"]), (square(30, 0, 10), ["2"]), (square(60, 0, 10), ["3"])],
            fields="id:string", geometry="MultiPolygon")
        self.project.addMapLayer(imobile)
        dialog = self.window([imobile])
        [layer] = self.run_window(dialog)["layers"]
        self.assertEqual(sorted(f["id"] for f in layer.getFeatures()), ["1", "2", "3"])
        dialog.deleteLater()

    def test_a_saved_report_is_never_taken_for_an_intravilan_contour(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        from cadex.services import roles
        gpkg = os.path.join(self.tmp, "raport_intravilan.gpkg")
        # saved, not added to the project
        dialog = self.window([self.imobile_s1], gpkg=gpkg, add=False)
        self.run_window(dialog)
        dialog.deleteLater()
        # the next day, in another project
        self.project.clear()
        again = QgsVectorLayer(f"{gpkg}|layername=raport_intravilan", "raport_intravilan", "ogr")
        self.assertTrue(again.isValid())
        self.assertTrue(roles.is_report(again))
        self.assertEqual(again.renderer().type(), "categorizedSymbol")
        contour = helpers.polygon_layer("limita intravilan", [(square(-10, -10, 150), ["T1"])],
                                        fields="nume:string")
        self.project.addMapLayers([again, contour])
        self.assertEqual(self.names(LayerChecklist("roles/intravilan")), ["limita intravilan"])

    def test_an_existing_geopackage_keeps_its_other_layers_in_the_file_and_the_project(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        path = os.path.join(self.tmp, "proiect.gpkg")
        limits, old_report = self.gpkg_layers(path, "limite", "raport_intravilan")
        self.project.addMapLayers([limits, old_report])
        dialog = self.window([self.imobile_s1], gpkg=path)
        shown = self.run_window(dialog)
        self.assertEqual(len(dialog.questions), 2, dialog.questions)
        self.assertIn("'raport_intravilan'", dialog.questions[0])
        self.assertIn("celelalte layere din fișier rămân neschimbate", dialog.questions[0])
        self.assertIn("'raport_intravilan'", dialog.questions[1])
        self.assertNotIn("'limite'", dialog.questions[1])
        self.assertIs(self.project.mapLayer(limits.id()), limits)
        self.assertIsNone(self.project.mapLayer(old_report.id()))
        [layer] = shown["layers"]
        self.assertEqual([f["ID_IMOBIL"] for f in layer.getFeatures()], ["A"])
        self.assertEqual(QgsVectorLayer(f"{path}|layername=limite", "l", "ogr").featureCount(),
                         1)
        dialog.deleteLater()

    def test_a_report_is_written_beside_the_contour_in_the_project_geopackage(self):
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        from cadex.services import intravilan_service
        path = os.path.join(self.tmp, "proiect.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "intravilan"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            self.intravilan, path, QgsCoordinateTransformContext(), options)
        contour = QgsVectorLayer(f"{path}|layername=intravilan", "intravilan din proiect", "ogr")
        self.project.addMapLayer(contour)
        dialog = self.window([self.imobile_s1], gpkg=path)
        dialog.intravilan.set_checked_ids([contour.id()])
        shown = self.run_window(dialog)
        self.assertIsNotNone(shown, self.bar_text(dialog))
        self.assertIs(self.project.mapLayer(contour.id()), contour)
        [layer] = shown["layers"]
        self.assertEqual([f["situatie"] for f in layer.getFeatures()],
                         [intravilan_service.INTEGRAL_INTRA])
        self.assertEqual(QgsVectorLayer(f"{path}|layername=intravilan", "c", "ogr")
                         .featureCount(), 1)
        dialog.deleteLater()

    def test_a_failed_run_puts_the_previous_result_back_in_its_place(self):
        from unittest import mock
        from cadex.services import intravilan_service
        path = os.path.join(self.tmp, "rezultat.gpkg")
        [previous] = self.gpkg_layers(path, "raport_intravilan")
        limits = helpers.polygon_layer("Limite sectoare", [(square(0, 0, 10), [1])])
        group = self.project.layerTreeRoot().insertGroup(0, "Rapoarte")
        self.project.addMapLayers([limits, previous], False)
        group.addLayer(limits)
        group.addLayer(previous)
        dialog = self.window([self.imobile_s1], gpkg=path)
        with mock.patch.object(intravilan_service.IntravilanTask, "_compute",
                               side_effect=RuntimeError("calcul întrerupt")):
            self.assertIsNone(self.run_window(dialog))
        self.assertEqual(len(dialog.failures), 1)
        self.assertIs(self.project.mapLayer(previous.id()), previous)
        self.assertEqual([node.name() for node in group.children()],
                         ["Limite sectoare", "raport_intravilan"])
        dialog.deleteLater()

    def test_a_report_that_cannot_be_written_is_refused_before_the_run(self):
        excel = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(excel)
        dialog = self.window([self.imobile_s1], gpkg=os.path.join(self.tmp, "raport.gpkg"),
                             excel=excel)
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started, [])
        self.assertEqual(dialog.questions, [])
        self.assertEqual(len(dialog.failures), 1)
        self.assertIn("Fișierul 'raport.xlsx' nu poate fi scris", dialog.failures[0])
        dialog.deleteLater()

    def test_a_report_that_could_not_be_written_is_a_note_and_not_offered(self):
        from cadex.services import intravilan_service
        excel = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(excel)
        task = intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(self.imobile_s1)], intravilan=[LayerSnapshot(self.intravilan)],
            excel_fields=[], id_field="ID_IMOBIL", owner_field=None, tolerance=0.5,
            excel_path=excel)
        ok = task.run()
        dialog = self.window([self.imobile_s1])
        dialog._on_finished(ok, task)
        [shown] = dialog.shown
        self.assertTrue(shown["warning"])
        self.assertTrue(any(note.startswith("Fișierul 'raport.xlsx' nu poate fi scris")
                            for note in shown["notes"]), shown["notes"])
        self.assertIsNone(shown["file_path"])
        dialog.deleteLater()

    def test_imobile_the_saved_layer_refused_are_a_note(self):
        from cadex.services import intravilan_service
        gpkg = os.path.join(self.tmp, "raport.gpkg")
        task = intravilan_service.IntravilanTask(
            imobile=[LayerSnapshot(self.imobile_s1), LayerSnapshot(self.imobile_s2)],
            intravilan=[LayerSnapshot(self.intravilan)], excel_fields=[], id_field="ID_IMOBIL",
            owner_field=None, tolerance=0.5, gpkg_path=gpkg)
        self.assertTrue(task.run(), task.error_msg)
        # what a GeoPackage that refused one imobil leaves in the task
        task.expected, task.written = 2, 1
        task.write_error = "UNIQUE constraint failed: raport_intravilan.fid"
        dialog = self.window([self.imobile_s1, self.imobile_s2])
        dialog._on_finished(True, task)
        [shown] = dialog.shown
        self.assertTrue(shown["warning"])
        self.assertIn("1 imobil din 2 nu a putut fi scris în layerul rezultat. "
                      "UNIQUE constraint failed: raport_intravilan.fid", shown["notes"])
        dialog.deleteLater()


class CompareWindowTests(DialogTest):
    """The Comparator window: the layers and keys it preselects, and its result window."""

    def setUp(self):
        super().setUp()
        self.shown = []
        self.failures = []

    def window(self):
        dialog = CompareDialog(iface=self.iface)
        dialog.show_result = lambda headline, **kw: self.shown.append((headline, kw))
        dialog.fail = self.failures.append
        return dialog

    def converted(self, name, batch, rows, fields="id:integer,ie:integer,sector:integer"):
        """A Conversie CGXML imobile layer, one row per imobil."""
        from cadex.services import roles
        layer = helpers.polygon_layer(name, [(square(20 * i, 0, 10), row)
                                             for i, row in enumerate(rows)], fields=fields)
        roles.mark_columns(layer, [(f.split(":")[0], f.split(":")[0])
                                   for f in fields.split(",")])
        roles.mark_batch(layer, batch)
        self.project.addMapLayer(layer)
        return layer

    def folders(self):
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        return old, new

    def files(self, old, new, **options):
        task = cgxml_compare.CgxmlCompareTask(old_paths=[old], new_paths=[new], **options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    @staticmethod
    def started(dialog):
        """The tasks the window starts, kept instead of run."""
        tasks = []
        dialog.start_task = lambda task, message="": tasks.append(task)
        return tasks

    def test_a_report_that_cannot_be_written_keeps_the_comparison(self):
        from cadex.services import compare_service
        old, new = self.folders()
        synthetic.write(old, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        synthetic.write(new, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 21)], cadgenno="1"))
        report = os.path.join(self.tmp, "lipsa", "raport.xlsx")
        dialog = self.window()
        task = cgxml_compare.CgxmlCompareTask(old_paths=[old], new_paths=[new],
                                              excel_path=report)
        dialog._on_cgxml_finished(task.run(), task)
        v1 = helpers.polygon_layer("v1", [(square(0, 0, 10), [1, "A"])],
                                   fields="id:integer,propr:string")
        v2 = helpers.polygon_layer("v2", [(square(0, 0, 10), [1, "B"])],
                                   fields="id:integer,propr:string")
        task = compare_service.CompareTask(
            old=LayerSnapshot(v1), new=LayerSnapshot(v2), key_old="id", key_new="id",
            fields=["propr"], compare_geometry=True, tolerance=0.1, excel_path=report)
        dialog._on_layers_finished(task.run(), task)
        self.assertEqual(self.failures, [])
        self.assertEqual([[layer.name() for layer in kw["layers"]] for _h, kw in self.shown],
                         [["Imobile modificate"], ["Modificate [v2]"]])
        for _headline, kw in self.shown:
            self.assertTrue(any("'raport.xlsx' nu poate fi scris" in note
                                for note in kw["notes"]), kw["notes"])
            self.assertTrue(kw["warning"])
            # no button opens a report that was not written
            self.assertIsNone(kw["file_path"])
        dialog.deleteLater()

    def test_records_without_coordinates_are_named_in_a_note(self):
        eterra, livrare = self.folders()
        synthetic.write(eterra, "70001.cgxml", synthetic.Land(
            rings=[], measured_area=400.0, ie="70001", style="eterra"))
        synthetic.write(eterra, "70002.cgxml", synthetic.Land(
            rings=[synthetic.square(500100, 400000, 20)], ie="70002", style="eterra"))
        synthetic.write(livrare, "503.cgxml", synthetic.Land(
            rings=[synthetic.square(500100, 400000, 20)], cadgenno="503", ie="70002",
            sector="3"))
        task = self.files(eterra, livrare, mode=cgxml_compare.IE)
        dialog = self.window()
        dialog._on_cgxml_finished(True, task)
        _headline, kw = self.shown[0]
        self.assertIn("Înregistrările fără coordonate nu apar în layerele rezultat: 1 la "
                      "'Imobile dispărute'.", kw["notes"])
        self.assertNotIn("Imobile dispărute", [layer.name() for layer in kw["layers"]])
        dialog.deleteLater()

    def test_plain_layers_pair_on_the_id_when_new_imobile_have_no_ie(self):
        fields = "id:integer,ie:integer,propr:string"
        v1 = helpers.polygon_layer("Parcele v1", [(square(0, 0, 10), [1, 70001, "A"]),
                                                  (square(20, 0, 10), [2, 70002, "B"])],
                                   fields=fields)
        # an imobil gets its IE only at registration
        v2 = helpers.polygon_layer("Parcele v2", [(square(0, 0, 10), [1, 70001, "A"]),
                                                  (square(20, 0, 10), [2, 70002, "B"]),
                                                  (square(40, 0, 10), [3, None, "C"])],
                                   fields=fields)
        self.project.addMapLayers([v1, v2])
        dialog = self.window()
        dialog.old_combo.setLayer(v1)
        dialog.new_combo.setLayer(v2)
        self.assertEqual((dialog.old_key.currentData(), dialog.new_key.currentData()),
                         ("id", "id"))
        dialog.excel_row.edit.setText("")
        tasks = self.started(dialog)
        dialog._run()
        [task] = tasks
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.added, ["3"])
        dialog.deleteLater()

    def test_the_swap_button_swaps_the_constructions_too(self):
        a = helpers.polygon_layer("Parcele A", [(square(0, 0, 40), [1])], fields="id:integer")
        b = helpers.polygon_layer("Parcele B", [(square(0, 0, 40), [1])], fields="id:integer")
        houses_a = helpers.polygon_layer("Cladiri A", [(square(5, 5, 5), [1, "C1"])],
                                         fields="imobil:integer,nr:string")
        houses_b = helpers.polygon_layer("Cladiri B", [(square(5, 5, 5), [1, "C1"]),
                                                       (square(15, 5, 5), [1, "C2"])],
                                         fields="id_imobil:integer,numar:string")
        self.project.addMapLayers([a, b, houses_a, houses_b])
        dialog = self.window()
        dialog.old_combo.setLayer(a)
        dialog.new_combo.setLayer(b)
        dialog.buildings_box.setChecked(True)
        dialog.buildings_box.clicked.emit(True)
        for side, layer, keys in (("old", houses_a, ("imobil", "nr")),
                                  ("new", houses_b, ("id_imobil", "numar"))):
            dialog.building_combos[side].setLayer(layer)
            for combo, key in zip(dialog.building_keys[side], keys):
                combo.setCurrentIndex(combo.findData(key))
        dialog._swap()
        self.assertEqual([dialog.building_combos[side].currentLayer().name()
                          for side in ("old", "new")], ["Cladiri B", "Cladiri A"])
        self.assertEqual([dialog._building_key("old"), dialog._building_key("new")],
                         [["id_imobil", "numar"], ["imobil", "nr"]])
        dialog.excel_row.edit.setText("")
        tasks = self.started(dialog)
        dialog._run()
        [task] = tasks
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([task.old.name, task.new.name, task.buildings.old.name,
                          task.buildings.new.name],
                         ["Parcele B", "Parcele A", "Cladiri B", "Cladiri A"])
        self.assertEqual(task.buildings.deleted, ["1 C2"])
        dialog.deleteLater()

    def test_a_swap_is_kept_when_the_window_opens_again(self):
        self.converted("Imobile v1", "20250101", [[1, None, 3]])
        self.converted("Imobile v2", "20260101", [[1, None, 3], [2, None, 3]])
        dialog = self.window()
        pair = lambda: (dialog.old_combo.currentLayer().name(),  # noqa: E731
                        dialog.new_combo.currentLayer().name())
        dialog.show()
        self.assertEqual(pair(), ("Imobile v1", "Imobile v2"))
        dialog._swap()
        dialog.close()
        dialog.show()
        self.assertEqual(pair(), ("Imobile v2", "Imobile v1"))
        dialog.close()
        dialog.deleteLater()

    def test_a_second_conversion_of_the_same_files_is_no_earlier_version(self):
        v1 = self.converted("Imobile livrare_v1", "20250101", [[1, None, 3], [2, None, 3]])
        v2 = self.converted("Imobile livrare_v2", "20260101",
                            [[1, None, 3], [2, None, 3], [3, None, 3]])
        again = self.converted("Imobile livrare_v2", "20260102",
                               [[1, None, 3], [2, None, 3], [3, None, 3]])
        dialog = self.window()
        self.assertEqual([dialog.old_combo.currentLayer().id(),
                          dialog.new_combo.currentLayer().id()], [v1.id(), again.id()])
        dialog.deleteLater()
        # a delivery that corrects only the data holds the same imobile as the one before it
        self.project.removeMapLayers([v2.id(), again.id()])
        corrected = self.converted("Imobile livrare_v3", "20270101", [[1, None, 3], [2, None, 3]])
        dialog = self.window()
        self.assertEqual([dialog.old_combo.currentLayer().id(),
                          dialog.new_combo.currentLayer().id()], [v1.id(), corrected.id()])
        dialog.deleteLater()

    def test_the_earlier_conversion_is_preselected_as_the_old_version_whatever_it_holds(self):
        livrare = self.converted("Imobile livrare", "20250101",
                                 [[501, 50124, 3], [502, 50125, 3]])
        # converted after the delivery; an eTerra export has no cadastral sectors
        eterra = self.converted("Imobile eterra", "20260101",
                                [[None, 50124, None], [None, 50125, None]])
        dialog = self.window()
        self.assertEqual([dialog.old_combo.currentLayer().id(),
                          dialog.new_combo.currentLayer().id()], [livrare.id(), eterra.id()])
        dialog.deleteLater()

    def test_the_report_of_the_file_comparison_is_shown_when_the_window_opens_on_it(self):
        report = os.path.join(self.tmp, "fisiere.xlsx")
        dialog = self.window()
        dialog.mode.set_index(1)
        dialog.excel_row.edit.setText(report)
        dialog.save_remembered()
        dialog.deleteLater()
        again = self.window()
        self.assertEqual(again.mode.index(), 1)
        self.assertEqual(again.excel_row.edit.text(), report)
        again.mode.set_index(0)
        again.mode.set_index(1)
        self.assertEqual(again.excel_row.edit.text(), report)
        again.deleteLater()

    def test_a_key_column_renamed_while_the_window_is_open_leaves_its_list(self):
        v1 = helpers.polygon_layer("v1", [(square(0, 0, 10), ["1", "A"])],
                                   fields="nr:string,propr:string")
        v2 = helpers.polygon_layer("v2", [(square(0, 0, 10), ["1", "B"])],
                                   fields="nr:string,propr:string")
        self.project.addMapLayers([v1, v2])
        dialog = self.window()
        dialog.old_combo.setLayer(v1)
        dialog.new_combo.setLayer(v2)
        self.assertEqual(dialog.new_key.currentData(), "nr")
        v2.startEditing()
        v2.renameAttribute(v2.fields().indexOf("nr"), "numar")
        v2.commitChanges()
        offered = [dialog.new_key.itemData(i) for i in range(dialog.new_key.count())]
        self.assertEqual(offered, ["numar", "propr"])
        dialog.deleteLater()

    def test_no_imobil_found_in_both_sources_is_a_warning_headline(self):
        old, new = self.folders()
        synthetic.write(old, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        synthetic.write(new, "2.cgxml", synthetic.Land(
            rings=[synthetic.square(500100, 400000, 20)], cadgenno="2"))
        dialog = self.window()
        dialog._on_cgxml_finished(True, self.files(old, new))
        headline, kw = self.shown[0]
        self.assertEqual(headline, "Niciun imobil nu a fost găsit în ambele surse.")
        self.assertTrue(kw["warning"])
        dialog.deleteLater()

    def test_the_file_comparison_counts_the_unchanged_constructions(self):
        old, new = self.folders()
        for folder in (old, new):
            synthetic.write(folder, "1.cgxml", synthetic.Land(
                rings=[synthetic.square(500000, 400000, 20)], cadgenno="1",
                buildings=[synthetic.Building(1, [synthetic.square(500002, 400002, 5)])]))
        dialog = self.window()
        dialog._on_cgxml_finished(True, self.files(old, new))
        _headline, kw = self.shown[0]
        counts = [count[:2] for count in kw["counts"] if count is not None]
        self.assertIn(("Imobile nemodificate", 1), counts)
        self.assertIn(("Construcții nemodificate", 1), counts)
        dialog.deleteLater()

    def test_records_a_result_layer_refuses_are_named_in_a_note(self):
        from unittest import mock
        old, new = self.folders()
        synthetic.write(old, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        for number, x in ((1, 500000), (2, 500100), (3, 500200)):
            synthetic.write(new, f"{number}.cgxml", synthetic.Land(
                rings=[synthetic.square(x, 400000, 20)], cadgenno=str(number)))
        task = self.files(old, new)
        real = cgxml_compare.result_features

        def refusing(*args, **kwargs):
            result = real(*args, **kwargs)
            if result.features:
                # the joined IDs of a split imobil in an integer column
                result.features[0].setAttribute("id", "4424; 4472")
            return result

        dialog = self.window()
        with mock.patch.object(cgxml_compare, "result_features", refusing):
            dialog._on_cgxml_finished(True, task)
        _headline, kw = self.shown[0]
        added = next(layer for layer in kw["layers"] if layer.name() == "Imobile apărute")
        self.assertEqual(added.featureCount(), 1)
        self.assertTrue(any(note.startswith("1 element din 2 nu a putut fi scris în layerul "
                                            "'Imobile apărute'.") for note in kw["notes"]),
                        kw["notes"])
        self.assertTrue(kw["warning"])
        dialog.deleteLater()

    def test_constructions_compared_on_nothing_are_named_in_a_note(self):
        old, new = self.folders()
        for folder, x in ((old, 500002), (new, 500003)):
            synthetic.write(folder, "1.cgxml", synthetic.Land(
                rings=[synthetic.square(500000, 400000, 20)], cadgenno="1",
                buildings=[synthetic.Building(1, [synthetic.square(x, 400002, 5)])]))
        task = self.files(old, new, compare_geometry=False, building_columns=[])
        dialog = self.window()
        dialog._on_cgxml_finished(True, task)
        _headline, kw = self.shown[0]
        self.assertTrue(any("doar construcțiile apărute și dispărute" in note
                            for note in kw["notes"]), kw["notes"])
        dialog.deleteLater()

    def test_a_key_picked_by_hand_holds_only_for_its_layers(self):
        fields = "id:integer,ie:integer,propr:string"
        a = helpers.polygon_layer("Parcele a", [(square(0, 0, 10), [1, 70001, "A"])],
                                  fields=fields)
        b = helpers.polygon_layer("Parcele b", [(square(0, 0, 10), [1, 70001, "B"])],
                                  fields=fields)
        self.project.addMapLayers([a, b])
        v1 = self.converted("Imobile v1", "20250101", [[1, None, 3], [2, None, 3]])
        v2 = self.converted("Imobile v2", "20260101", [[1, None, 3], [3, None, 3]])
        dialog = self.window()
        keys = lambda: (dialog.old_key.currentData(), dialog.new_key.currentData())  # noqa: E731
        dialog.old_combo.setLayer(a)
        dialog.new_combo.setLayer(b)
        for combo in (dialog.old_key, dialog.new_key):
            combo.setCurrentIndex(combo.findData("ie"))
            combo.activated.emit(combo.currentIndex())
        # one of the layers it was picked for is still chosen
        dialog.old_combo.setLayer(v1)
        self.assertEqual(keys(), ("ie", "ie"))
        # two other layers: two deliveries pair on the imobil ID
        dialog.new_combo.setLayer(v2)
        self.assertEqual(keys(), ("id", "id"))
        dialog.deleteLater()

    def test_the_changed_layer_is_styled_by_its_kind_whatever_the_column_names(self):
        from cadex.services import cgxml_columns
        layout = cgxml_columns.default_layout("imobile")
        layout[-1]["name"] = "tip_modif"
        settings_service.set_("cgxml/columns_imobile", cgxml_columns.layout_to_json(layout))
        old, new = self.folders()
        synthetic.write(old, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        synthetic.write(new, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 21)], cadgenno="1"))
        dialog = self.window()
        dialog._on_cgxml_finished(True, self.files(old, new))
        _headline, kw = self.shown[0]
        changed = next(layer for layer in kw["layers"] if layer.name() == "Imobile modificate")
        self.assertEqual(changed.renderer().classAttribute(), "tip_modif_2")
        self.assertEqual(next(changed.getFeatures())["tip_modif_2"], "Doar limita")
        dialog.deleteLater()

    def test_the_remarks_on_the_files_are_in_the_details(self):
        old, new = self.folders()
        land = synthetic.Land(rings=[synthetic.square(500000, 400000, 20)], cadgenno="1")
        synthetic.write(old, "1.cgxml", land)
        os.makedirs(new)
        with open(os.path.join(new, "1.cgxml"), "wb") as fp:
            fp.write(synthetic.cgxml_bytes(land).replace(b"<X>500020.000</X>",
                                                         b"<X>5000z0.000</X>", 1))
        dialog = self.window()
        dialog._on_cgxml_finished(True, self.files(old, new))
        _headline, kw = self.shown[0]
        self.assertIn("Sursa nouă, 1.cgxml:", kw["details"])
        self.assertIn("coordonate care nu sunt numere", kw["details"])
        self.assertTrue(kw["warning"])
        dialog.deleteLater()


class ColumnSpellingWindowTests(DialogTest):
    """Imobile layers that spell the same column in different letter case."""

    def test_a_column_spelled_two_ways_is_offered_once_and_read_from_every_layer(self):
        first = helpers.polygon_layer("Imobile T1", [(square(0, 0, 100), ["5"])],
                                      fields="ID:string")
        second = helpers.polygon_layer("Imobile T2", [(square(95, 0, 100), ["7"])],
                                       fields="id:string")
        self.project.clear()
        self.project.addMapLayers([first, second])
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(0)
        dialog.imobile.set_checked_ids([first.id(), second.id()])
        dialog.excel_row.edit.setText("")
        offered = [dialog.id_combo.itemData(i) for i in range(dialog.id_combo.count())]
        self.assertEqual([name for name in offered if name and name.lower() == "id"], ["ID"])
        dialog.id_combo.setCurrentIndex(dialog.id_combo.findData("ID"))
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        [task] = started
        self.assertTrue(task.run(), task.error_msg)
        self.assertIn("Imobilele 5 și 7 se suprapun pe 500 mp.",
                      [finding.detail for finding in task.findings])
        dialog.deleteLater()


class UnreadableSourceWindowTests(DialogTest):
    """CGXML sources that hold a damaged archive."""

    def setUp(self):
        super().setUp()
        self.damaged = os.path.join(self.tmp, "livrare.zip")
        with open(self.damaged, "wb") as fp:
            fp.write(b"nu este o arhiva")

    def test_a_topology_check_of_a_damaged_archive_is_refused_with_the_reason(self):
        dialog = TopologyCheckDialog(iface=self.iface)
        dialog.source_mode.set_index(1)
        dialog.cgxml.set_paths([self.damaged])
        dialog.excel_row.edit.setText("")
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("'livrare.zip' (arhiva ZIP este deteriorată sau incompletă)",
                      self.bar_text(dialog))
        dialog.deleteLater()

    def test_a_comparison_with_a_damaged_archive_names_the_archive(self):
        new = os.path.join(self.tmp, "nou")
        synthetic.write(new, "503.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="503", sector="3"))
        dialog = CompareDialog(iface=self.iface)
        dialog.mode.set_index(1)
        dialog.old_source.set_paths([self.damaged])
        dialog.new_source.set_paths([new])
        dialog.excel_row.edit.setText("")
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("Sursa veche nu poate fi folosită. Arhiva ZIP 'livrare.zip' este "
                      "deteriorată sau incompletă.", self.bar_text(dialog))
        dialog.deleteLater()


class ConversionWindowTests(WidgetsShellTest):
    """Conversie CGXML: the checks before a run and the notes of the result window."""

    def conversion(self, *paths):
        """The window on paths, by default the synthetic files, writing to the temporary
        folder; returns it with the list of the tasks it would start."""
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.source.set_paths(list(paths) or [helpers.DATA_DIR])
        dialog.source.refresh()
        dialog.output_row.set_path(self.tmp)
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog.confirm = lambda *args, **kwargs: True
        return dialog, started

    @staticmethod
    def use_format(dialog, fmt_key):
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(fmt_key))

    @staticmethod
    def rename_id_column(dialog, name):
        dialog.columns["imobile"].tree.topLevelItem(0).setText(1, name)

    def finished(self, task):
        """(notes, warning) of the result window for a finished task."""
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.add_to_project.setChecked(False)
        shown = []
        dialog.show_result = lambda headline, **kwargs: shown.append(kwargs)
        dialog._on_finished(True, task)
        return shown[0]["notes"], shown[0]["warning"]

    def task(self, source, fmt_key="gpkg", layout=None):
        from cadex.services import cgxml_columns, cgxml_export
        task = cgxml_export.ConversionTask(
            input_paths=source, recursive=True, output_dir=os.path.join(self.tmp, "out"),
            base_name="conv", fmt=fmt_key,
            layouts=layout or {w: cgxml_columns.default_layout(w)
                               for w in ("imobile", "constructii")},
            layer_names={"imobile": "Imobile", "constructii": "Construcții"}, label_config={})
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_layer_names_that_end_up_the_same_in_the_file_are_refused(self):
        dialog, started = self.conversion()
        for fmt_key, first, second in (("gpkg", "Imobile", "imobile"),
                                       ("shp", "Imobile-2024", "Imobile 2024"),
                                       ("dxf", "Construcții A", "constructii_a")):
            with self.subTest(fmt=fmt_key):
                self.use_format(dialog, fmt_key)
                dialog.name_imobile.setText(first)
                dialog.name_constructii.setText(second)
                dialog._run()
                self.assertEqual(started, [])
                self.assertIn("nume", self.bar_text(dialog))
        self.use_format(dialog, "gpkg")
        dialog.name_constructii.setText("Construcții")
        dialog._run()
        self.assertEqual(len(started), 1)

    def test_field_name_rules_apply_only_to_the_formats_that_write_field_names(self):
        dialog, started = self.conversion()
        # Excel heads its columns with the labels, DXF writes no columns
        self.rename_id_column(dialog, "nr_cadastral_vechi_ș")
        self.use_format(dialog, "xlsx")
        dialog._run()
        self.assertEqual(len(started), 1)
        _ui_helpers.set_all_check_states(dialog.columns["imobile"].tree, False)
        self.use_format(dialog, "dxf")
        dialog._run()
        self.assertEqual(len(started), 2)
        self.use_format(dialog, "xlsx")
        dialog._run()
        self.assertEqual(len(started), 2)
        self.assertIn("Nu este bifată nicio coloană.", self.bar_text(dialog))

    def test_the_field_names_a_geopackage_reserves_are_refused(self):
        dialog, started = self.conversion()
        self.rename_id_column(dialog, "FID")
        dialog._run()
        self.assertEqual(started, [])
        self.assertIn("Numele 'FID' al coloanei 'ID imobil' este folosit de GeoPackage",
                      self.bar_text(dialog))
        # a Shapefile has no such column
        self.use_format(dialog, "shp")
        dialog._run()
        self.assertEqual(len(started), 1)

    @staticmethod
    def hold_like_excel(path):
        """Open path the way Excel holds a workbook: others may read it, nobody may write it.
        Returns the release."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE)
        handle = kernel32.CreateFileW(path, 0xC0000000, 0x1, None, 3, 0, None)
        return lambda: kernel32.CloseHandle(handle)

    @unittest.skipUnless(os.name == "nt", "Excel's way of holding a file is a Windows lock")
    def test_an_output_open_in_another_program_stops_the_run_before_it_starts(self):
        dialog, started = self.conversion()
        self.use_format(dialog, "xlsx")
        target = os.path.join(self.tmp, "cgxml_data.xlsx")
        with open(target, "wb") as fp:
            fp.write(b"x")
        failures = []
        dialog.fail = failures.append
        release = self.hold_like_excel(target)
        try:
            dialog._run()
        finally:
            release()
        self.assertEqual(started, [])
        [failure] = failures
        self.assertTrue(failure.startswith("Fișierul 'cgxml_data.xlsx' nu poate fi scris"),
                        failure)

    def test_a_run_reads_no_folder_on_the_window_thread(self):
        listings = self.count_listings()
        dialog, started = self.conversion()
        dialog.show()
        self.assertEqual(dialog.source.count(), 11)
        self.assertEqual(len(listings), 1)
        dialog._run()
        self.assertEqual(len(started), 1)
        self.assertEqual(len(listings), 1)
        dialog.close()

    def test_a_damaged_archive_among_the_sources_does_not_stop_the_conversion(self):
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        dialog, started = self.conversion(helpers.DATA_DIR, damaged)
        dialog._run()
        [task] = started
        self.assertTrue(task.run(), task.error_msg)
        self.assertIn(("descarcare.zip", "arhiva ZIP este deteriorată sau incompletă"),
                      task.errors)

    def test_an_unreadable_folder_is_not_counted_as_a_file(self):
        from unittest import mock
        locked = os.path.join(self.tmp, "sector2")
        os.makedirs(locked)
        scandir = os.scandir

        def refuse(path="."):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(locked):
                raise PermissionError(13, "Permission denied", locked)
            return scandir(path)
        source = CgxmlSource()
        self.addCleanup(source.deleteLater)
        source.set_paths([helpers.DATA_DIR, locked])
        with mock.patch.object(os, "scandir", refuse):
            source.refresh()
            self.assertIsNone(source.count())
        self.assertEqual(source.status.text(),
                         "Sursa 'sector2' nu poate fi citită: acces refuzat.")

    def test_a_source_with_nothing_readable_leaves_the_previous_result_alone(self):
        from unittest import mock
        from qgis.core import QgsVectorLayer  # type: ignore
        previous = self.task(helpers.DATA_DIR)
        layer = QgsVectorLayer(previous.outputs["imobile"], "Imobile", "ogr")
        self.project.addMapLayer(layer)
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        locked = os.path.join(self.tmp, "sector2")
        os.makedirs(locked)
        scandir = os.scandir

        def refuse(path="."):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(locked):
                raise PermissionError(13, "Access is denied", locked)
            return scandir(path)
        for source, reason in (
                (damaged, "Arhiva ZIP 'descarcare.zip' este deteriorată sau incompletă."),
                (locked, "Sursa 'sector2' nu poate fi citită: acces refuzat.")):
            with self.subTest(source=os.path.basename(source)):
                dialog, started = self.conversion(source)
                dialog.output_row.set_path(os.path.join(self.tmp, "out"))
                dialog.base_edit.setText("conv")
                questions = []
                dialog.confirm = lambda *args, **kwargs: questions.append(args[1]) or True
                with mock.patch.object(os, "scandir", refuse):
                    dialog._run()
                self.assertEqual((started, questions), ([], []))
                self.assertIn(layer.id(), self.project.mapLayers())
                self.assertIn(reason, self.bar_text(dialog))

    def test_a_protected_subfolder_leaves_the_source_readable(self):
        from unittest import mock
        drive = os.path.join(self.tmp, "D")
        shutil.copytree(helpers.DATA_DIR, os.path.join(drive, "livrare"),
                        ignore=shutil.ignore_patterns("broken"))
        # as System Volume Information at the root of a drive
        protected = os.path.join(drive, "System Volume Information")
        os.makedirs(protected)
        scandir = os.scandir

        def refuse(path="."):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(protected):
                raise PermissionError(13, "Access is denied", protected)
            return scandir(path)
        dialog, started = self.conversion(drive)
        with mock.patch.object(os, "scandir", refuse):
            dialog.source.refresh()
            self.assertEqual(dialog.source.count(), 4)
        self.assertEqual(dialog.source.status.text(), "S-au găsit 4 fișiere CGXML.")
        dialog._run()
        [task] = started
        with mock.patch.object(os, "scandir", refuse):
            self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.errors, [])

    def test_the_result_separates_missing_coordinates_and_refused_features(self):
        from cadex.services import cgxml_columns
        source = os.path.join(self.tmp, "src")
        shutil.copytree(helpers.DATA_DIR, source)
        shutil.copy(os.path.join(source, "livrare_10188.cgxml"),
                    os.path.join(source, "copie_10188.cgxml"))
        layout = {w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")}
        # the window refuses this name; here it makes GeoPackage refuse the second 10188, as
        # a full disk would refuse any feature
        layout["imobile"][0]["name"] = "fid"
        notes, warning = self.finished(self.task(source, layout=layout))
        self.assertIn("1 imobil nu are coordonate în fișier și nu apare în rezultat.", notes)
        self.assertIn("1 imobil are puncte din care nu s-a putut construi geometria și nu "
                      "apare în rezultat.", notes)
        self.assertTrue(any(n.startswith("1 element din 4 nu a putut fi scris în layerul "
                                         "'Imobile'.") for n in notes), notes)
        self.assertTrue(warning)

    def test_a_result_without_buildings_has_a_note_that_no_buildings_layer_was_written(self):
        source = os.path.join(self.tmp, "fara_constructii")
        synthetic.write(source, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1"))
        notes, warning = self.finished(self.task(source, "shp"))
        self.assertIn("Sursa nu are construcții cu coordonate, deci nu s-a scris un layer de "
                      "construcții.", notes)
        self.assertFalse(warning)


class CompareKeyColumnWindowTests(DialogTest):
    """The Comparator window when the key column of a layer is renamed or deleted while the
    window is open."""

    def layer(self, name, fields):
        layer = helpers.polygon_layer(name, [(square(0, 0, 10), ["A", 1]),
                                             (square(20, 0, 10), ["B", 2])], fields=fields)
        self.project.addMapLayer(layer)
        return layer

    def window(self, fields):
        old, new = self.layer("v1", fields), self.layer("v2", fields)
        dialog = CompareDialog(iface=self.iface)
        dialog.old_combo.setLayer(old)
        dialog.new_combo.setLayer(new)
        dialog.excel_row.edit.setText("")
        return dialog, new

    @staticmethod
    def run_window(dialog):
        """(the tasks the run started, the warnings it showed)"""
        tasks, warnings = [], []
        dialog.start_task = lambda task, message="": tasks.append(task)
        dialog.warn = lambda message, *_args, **_kwargs: warnings.append(message)
        dialog._run()
        return tasks, warnings

    @staticmethod
    def edit(layer, change):
        layer.startEditing()
        change(layer)
        layer.commitChanges()

    def test_a_key_picked_by_hand_and_renamed_is_asked_for_again(self):
        dialog, new = self.window("propr:string,nr:integer")
        for combo in (dialog.old_key, dialog.new_key):
            combo.setCurrentIndex(combo.findData("nr"))
            combo.activated.emit(combo.currentIndex())
        self.edit(new, lambda layer: layer.renameAttribute(layer.fields().indexOf("nr"), "numar"))
        self.assertEqual((dialog.old_key.currentData(), dialog.new_key.currentData()),
                         ("nr", None))
        self.assertEqual(dialog.key_hint.text(), "Coloana cheie 'nr' nu mai există în layerul "
                                                 "'v2'. Alegeți coloana cheie.")
        self.assertEqual(self.run_window(dialog),
                         ([], ["Alegeți coloana cheie pentru ambele layere."]))
        # the column picked in its place stays chosen when the fields change again
        dialog.new_key.setCurrentIndex(dialog.new_key.findData("numar"))
        dialog.new_key.activated.emit(dialog.new_key.currentIndex())
        self.edit(new, lambda layer: layer.renameAttribute(0, "proprietar"))
        self.assertEqual(dialog.new_key.currentData(), "numar")
        dialog.deleteLater()

    def test_a_preselected_key_column_deleted_is_asked_for_again(self):
        dialog, new = self.window("propr:string,id:integer")
        self.assertEqual(dialog.new_key.currentData(), "id")
        self.edit(new, lambda layer: layer.deleteAttribute(layer.fields().indexOf("id")))
        self.assertIsNone(dialog.new_key.currentData())
        self.assertEqual(self.run_window(dialog),
                         ([], ["Alegeți coloana cheie pentru ambele layere."]))
        # another layer gets its key preselected as usual
        dialog.new_combo.setLayer(self.layer("v3", "propr:string,id:integer"))
        self.assertEqual(dialog.new_key.currentData(), "id")
        dialog.deleteLater()


class TopologyReusedIdWindowTests(DialogTest):
    """What the Verificare Topologică window shows for two imobile with one ID."""

    def test_the_files_of_two_imobile_with_one_id_are_listed_in_the_details(self):
        for name, x in (("501.cgxml", 500000), ("505.cgxml", 501000)):
            synthetic.write(self.tmp, name, synthetic.Land(
                rings=[synthetic.square(x, 400000, 100)], cadgenno="501", sector="3"))
        task = tcs.TopologyCheckTask(cgxml_paths=self.tmp, recursive=True)
        self.assertTrue(task.run(), task.error_msg)
        dialog = TopologyCheckDialog(iface=self.iface)
        result = TopologyWindowTests.shown(dialog, task)
        self.assertEqual(result["headline"], "S-au verificat 2 imobile, cu 0 construcții.")
        self.assertEqual(result["details"], "Imobilul 501 apare în locuri diferite, în "
                                            "fișierele: 501.cgxml, 505.cgxml. Fiecare fișier "
                                            "s-a verificat ca imobil separat.")
        self.assertTrue(result["warning"])
        dialog.deleteLater()


class IntravilanWindowTest(WidgetsShellTest):
    """Runs the Raport Intravilan window into a GeoPackage, with its questions answered."""

    def window(self, gpkg):
        dialog = IntravilanDialog(iface=self.iface)
        dialog.imobile.set_checked_ids([self.imobile_s1.id()])
        dialog.intravilan.set_checked_ids([self.intravilan.id()])
        dialog.gpkg_row.set_path(gpkg)
        dialog.excel_row.set_path("")
        dialog.add_to_project.setChecked(True)
        dialog.shown, dialog.failures, dialog.questions = [], [], []
        dialog.show_result = lambda headline, **kwargs: dialog.shown.append(kwargs)
        dialog.fail = dialog.failures.append
        dialog.confirm = lambda title, message, *a, **k: dialog.questions.append(message) or True
        self.addCleanup(dialog.deleteLater)
        return dialog

    def run_window(self, dialog):
        """Run the report and wait for the window to take its result back."""
        dialog._run()
        self.assertTrue(self.wait(lambda: dialog._task is None, 60))
        return dialog.shown[0] if dialog.shown else None


class IntravilanResultLayerTests(IntravilanWindowTest):
    """The layer Raport Intravilan saves in a GeoPackage and adds to the project."""

    def test_a_file_that_is_not_a_geopackage_is_replaced_after_the_usual_question(self):
        for name, content in (("gol.gpkg", b""),
                              ("text.gpkg", "nu este un GeoPackage".encode("utf-8"))):
            path = os.path.join(self.tmp, name)
            with open(path, "wb") as fp:
                fp.write(content)
            dialog = self.window(path)
            shown = self.run_window(dialog)
            self.assertEqual(dialog.questions,
                             [f"Fișierul {name} există deja. Doriți să fie înlocuit?"])
            self.assertEqual(dialog.failures, [])
            [layer] = shown["layers"]
            self.assertEqual([f["ID_IMOBIL"] for f in layer.getFeatures()], ["A"])

    def test_a_saved_report_opens_in_the_project_without_a_gdal_error(self):
        from osgeo import gdal  # type: ignore
        from cadex.services import roles
        failures = []

        def handler(error_class, _number, message):
            if error_class >= gdal.CE_Failure:
                failures.append(message)
        gdal.PushErrorHandler(handler)
        try:
            shown = self.run_window(self.window(os.path.join(self.tmp, "raport.gpkg")))
            [layer] = shown["layers"]
            count = layer.featureCount()
        finally:
            gdal.PopErrorHandler()
        self.assertEqual(failures, [])
        self.assertEqual(count, 1)
        self.assertTrue(roles.is_report(layer))
        self.assertEqual(layer.renderer().type(), "categorizedSymbol")

    def test_a_report_beside_other_layers_is_named_after_the_tool(self):
        from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter  # type: ignore
        path = os.path.join(self.tmp, "proiect.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = "limite"
        QgsVectorFileWriter.writeAsVectorFormatV3(
            helpers.polygon_layer("Limite", [(square(0, 0, 50), [1])]), path,
            QgsCoordinateTransformContext(), options)
        [layer] = self.run_window(self.window(path))["layers"]
        self.assertEqual(layer.name(), "Raport intravilan")
        # a rerun names the layer it takes out of the project
        rerun = self.window(path)
        self.run_window(rerun)
        self.assertIn("ca layerul 'Raport intravilan'", rerun.questions[1])
        # in a GeoPackage of its own, the report is named after the file
        own = os.path.join(self.tmp, "raport_sector3.gpkg")
        [layer] = self.run_window(self.window(own))["layers"]
        self.assertEqual(layer.name(), "raport_sector3")


class IntravilanGeoPackageWindowTests(IntravilanWindowTest):
    """Raport Intravilan written into a GeoPackage that another program or an earlier result
    has used."""

    def geopackage(self, path, table, layer):
        from qgis.core import QgsCoordinateTransformContext, QgsVectorFileWriter  # type: ignore
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = table
        QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(),
                                                  options)

    def project_geopackage(self, path):
        self.geopackage(path, "limite", helpers.polygon_layer("Limite", [(square(0, 0, 50), [1])]))

    def test_a_geopackage_another_program_is_writing_to_is_refused_before_the_run(self):
        import sqlite3
        path = os.path.join(self.tmp, "proiect.gpkg")
        self.project_geopackage(path)
        # DB Browser for SQLite with unsaved changes; the file can still be read
        holder = sqlite3.connect(path, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("CREATE TABLE alt_program(x)")
        try:
            dialog = self.window(path)
            dialog._run()
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertIsNone(dialog._task)
        self.assertEqual(dialog.questions, [])
        self.assertEqual(len(dialog.failures), 1)
        self.assertTrue(dialog.failures[0].startswith(
            "Fișierul 'proiect.gpkg' nu poate fi scris: este deschis în alt program"),
            dialog.failures)

    def test_a_report_is_coloured_by_its_status_when_the_geopackage_cannot_keep_its_style(self):
        import sqlite3
        path = os.path.join(self.tmp, "proiect.gpkg")
        self.project_geopackage(path)
        # another program's table under the name QGIS keeps the styles in
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE layer_styles (nota TEXT)")
        connection.commit()
        connection.close()
        [layer] = self.run_window(self.window(path))["layers"]
        self.assertEqual(layer.renderer().type(), "categorizedSymbol")
        self.assertEqual(layer.renderer().classAttribute(), "situatie")

    def test_a_report_written_over_another_result_keeps_none_of_its_markers(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        from cadex.services import layer_style, roles
        path = os.path.join(self.tmp, "proiect.gpkg")
        # a table under the report's name, with a conversion's markers saved in its style
        self.geopackage(path, "raport_intravilan", self.imobile_s1)
        uri = f"{path}|layername=raport_intravilan"
        earlier = QgsVectorLayer(uri, "earlier", "ogr")
        roles.mark_batch(earlier, roles.new_batch())
        roles.mark_columns(earlier, [("id", "ID_IMOBIL")])
        layer_style.store_with_file(earlier)
        del earlier
        self.run_window(self.window(path))
        # next day, in a new project
        again = QgsVectorLayer(uri, "again", "ogr")
        self.assertTrue(roles.is_report(again))
        self.assertEqual(roles.batch_of(again), "")
        self.assertEqual(roles.columns_of(again), {})


class CgxmlSourceBrowseTests(WidgetsShellTest):
    """Where the buttons of the CGXML source field open their file dialog."""

    def opened_at(self, source):
        """{button: the path its file dialog opens at, seconds until it opened}, the dialog
        closed at once."""
        from unittest import mock
        from qgis.PyQt.QtWidgets import QFileDialog  # type: ignore
        seen = {}
        buttons = (("folder", source._browse_folder, "getExistingDirectory", ""),
                   ("zip", source._browse_zip, "getOpenFileName", ("", "")),
                   ("files", source._browse_files, "getOpenFileNames", ([], "")))
        for name, browse, picker, nothing in buttons:
            started = time.monotonic()

            def pick(*args, button=name, since=started, answer=nothing, **_kwargs):
                seen[button] = (args[2], time.monotonic() - since)
                return answer
            with mock.patch.object(QFileDialog, picker, pick):
                browse()
        return seen

    def test_the_buttons_open_at_once_while_a_path_out_of_reach_is_counted(self):
        import threading
        share = os.path.join(self.tmp, "nas")
        threads, release = self.out_of_reach(share)
        source = CgxmlSource()
        self.addCleanup(source.deleteLater)
        source.show()
        source.set_paths([os.path.join(share, "UAT")])
        source.refresh()
        self.assertEqual(source.status.text(), "Se caută fișierele...")
        for button, (start, seconds) in self.opened_at(source).items():
            self.assertEqual(start, "", button)
            self.assertLess(seconds, 1.0, button)
        self.assertNotIn(threading.main_thread(), threads)
        release.set()
        self.assertTrue(self.wait(lambda: source.status.text() == "Calea aleasă nu există."))
        self.assertEqual({button: start for button, (start, _s) in self.opened_at(source).items()},
                         {"folder": "", "zip": "", "files": ""})
        self.assertNotIn(threading.main_thread(), threads)
        source.close()

    def test_the_buttons_open_at_the_chosen_source(self):
        import zipfile
        archive = os.path.join(self.tmp, "livrare.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(self.cgxml_files()[0], "a.cgxml")
        first, second = self.cgxml_files()[:2]
        source = CgxmlSource()
        self.addCleanup(source.deleteLater)
        source.show()
        for paths, expected in (
                ([helpers.DATA_DIR], {"folder": helpers.DATA_DIR, "zip": helpers.DATA_DIR,
                                      "files": helpers.DATA_DIR}),
                ([archive], {"folder": self.tmp, "zip": archive, "files": self.tmp}),
                ([first, second], {"folder": helpers.DATA_DIR, "zip": helpers.DATA_DIR,
                                   "files": first})):
            with self.subTest(paths=[os.path.basename(p) for p in paths]):
                source.set_paths(paths)
                source.refresh()
                source.count()
                self.assertEqual({button: start for button, (start, _s)
                                  in self.opened_at(source).items()}, expected)
        source.close()

    def test_the_buttons_open_at_a_folder_typed_into_the_field(self):
        import threading
        from unittest import mock
        from cadex.services import cgxml_sources
        typed = os.path.join(self.tmp, "UAT")
        os.makedirs(typed)
        at_typed = {"folder": typed, "zip": typed, "files": typed}
        source = CgxmlSource()
        self.addCleanup(source.deleteLater)
        source.show()
        source.set_paths([helpers.DATA_DIR])
        source.refresh()
        source.count()
        # typed without Return: the buttons take no focus, so the field is not left either
        source.edit.setText(typed)
        self.assertEqual({button: start for button, (start, _s)
                          in self.opened_at(source).items()}, at_typed)
        # Return pressed, and the files of the folder are still being listed
        listing = threading.Event()
        self.addCleanup(listing.set)
        list_sources = cgxml_sources.list_sources

        def slow(*args, **kwargs):
            listing.wait(5)
            return list_sources(*args, **kwargs)
        with mock.patch.object(cgxml_sources, "list_sources", slow):
            source.refresh()
            self.assertEqual({button: start for button, (start, _s)
                              in self.opened_at(source).items()}, at_typed)
            self.assertEqual(source.status.text(), "Se caută fișierele...")
            listing.set()
            self.assertEqual(source.count(), 0)
        source.close()


@unittest.skipUnless(os.name == "nt", "a file held open by another program is a Windows lock")
class CgxmlSourceInUseTests(WidgetsShellTest):
    """The status of the CGXML source field for an archive another program holds open."""

    @staticmethod
    def hold_exclusively(path):
        """Open path the way a program that shares it with nobody does. Returns the release."""
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.HANDLE)
        generic_read, share_nothing, open_existing = 0x80000000, 0, 3
        handle = kernel32.CreateFileW(path, generic_read, share_nothing, None, open_existing,
                                      0, None)
        return lambda: kernel32.CloseHandle(handle)

    def test_an_archive_open_in_another_program_is_named_as_in_use(self):
        import zipfile
        archive = os.path.join(self.tmp, "livrare.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(self.cgxml_files()[0], "a.cgxml")
        source = CgxmlSource()
        self.addCleanup(source.deleteLater)
        source.set_paths([archive])
        release = self.hold_exclusively(archive)
        try:
            source.refresh()
            self.assertIsNone(source.count())
        finally:
            release()
        self.assertEqual(source.status.text(),
                         "Arhiva ZIP 'livrare.zip' nu poate fi deschisă: este folosită de alt "
                         "program sau nu aveți drept de acces la ea.")


class ConversionCountWindowTests(WidgetsShellTest):
    """The counts in the result window of Conversie CGXML."""

    def test_a_damaged_archive_is_not_counted_among_the_files_read(self):
        from cadex.services import cgxml_columns, cgxml_export
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        task = cgxml_export.ConversionTask(
            input_paths=[helpers.DATA_DIR, damaged], recursive=True,
            output_dir=os.path.join(self.tmp, "out"), base_name="conv", fmt="xlsx",
            layouts={w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")},
            layer_names={"imobile": "Imobile", "constructii": "Construcții"}, label_config={})
        self.assertTrue(task.run(), task.error_msg)
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        shown = []
        dialog.show_result = lambda headline, **kwargs: shown.append(headline)
        dialog._on_finished(True, task)
        # 11 files, 6 of them unreadable, and the archive
        self.assertEqual(shown, ["S-au citit 5 fișiere: 5 imobile și 1 construcție."])


class ConversionRerunWindowTests(WidgetsShellTest):
    """Conversie CGXML run again onto the files of its previous result."""

    def window(self, source):
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData("shp"))
        dialog.add_to_project.setChecked(True)
        dialog.source.set_paths([source])
        dialog.source.refresh()
        dialog.output_row.set_path(os.path.join(self.tmp, "out"))
        dialog.base_edit.setText("rezultat")
        dialog.confirm = lambda *args, **kwargs: True
        dialog.show_result = lambda headline, **kwargs: None
        dialog.fail = lambda message: None
        return dialog

    def run_now(self, dialog):
        """Press Convertește and run the task at once, before the event loop runs again."""
        started = []
        dialog.start_task = lambda task, message="": started.append(task)
        dialog._run()
        [task] = started
        ok = task.run()
        dialog._on_finished(ok, task)
        return ok, task

    def test_a_shapefile_result_that_was_drawn_is_replaced_by_the_next_run(self):
        from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings  # type: ignore
        from qgis.PyQt.QtCore import QSize  # type: ignore
        # QGIS can hold the Shapefiles until it exits
        self.addCleanup(helpers.remove_folder, self.tmp)
        source = os.path.join(self.tmp, "src")
        os.makedirs(source)
        for name in ("livrare_10188.cgxml", "eterra_100001.cgxml"):
            shutil.copy(os.path.join(helpers.DATA_DIR, name), source)
        dialog = self.window(source)
        ok, task = self.run_now(dialog)
        self.assertTrue(ok, task.error_msg)
        results = [layer for layer in self.project.mapLayers().values()
                   if layer.providerType() == "ogr"]
        self.assertEqual(len(results), 2)
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


class SirutaConversionWindowTests(WidgetsShellTest):

    def test_a_conversion_without_the_siruta_database_completes_with_a_note(self):
        import sqlite3
        from unittest import mock
        from cadex.services import cgxml_columns, cgxml_export, siruta
        self.addCleanup(setattr, siruta, "_problem", "")
        self.addCleanup(setattr, siruta, "_tables", None)
        siruta._tables = None
        task = cgxml_export.ConversionTask(
            input_paths=helpers.DATA_DIR, recursive=False,
            output_dir=os.path.join(self.tmp, "out"), base_name="conv", fmt="gpkg",
            layouts={w: cgxml_columns.default_layout(w) for w in ("imobile", "constructii")},
            layer_names={"imobile": "Imobile", "constructii": "Construcții"}, label_config={})
        # as when the plugin folder denies access to its database
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(siruta.sqlite3, "connect", side_effect=refused):
            self.assertTrue(task.run(), task.error_msg)
        self.assertEqual({record["uat"] for record in task.imobile}, {""})
        dialog = CgxmlDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        dialog.add_to_project.setChecked(False)
        shown = []
        dialog.show_result = lambda headline, **kwargs: shown.append(kwargs)
        dialog._on_finished(True, task)
        self.assertIn("Baza de date SIRUTA a pluginului nu a putut fi citită, deci denumirile "
                      "de UAT, județ și localitate lipsesc din rezultat.", shown[0]["notes"])
        self.assertTrue(shown[0]["warning"])


class SirutaCompareWindowTests(DialogTest):

    def test_a_comparison_without_the_siruta_database_shows_its_note_as_a_warning(self):
        import sqlite3
        from unittest import mock
        from cadex.services import siruta
        self.addCleanup(setattr, siruta, "_problem", "")
        self.addCleanup(setattr, siruta, "_tables", None)
        siruta._tables = None
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        synthetic.write(old, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 20)], cadgenno="1", sector="3"))
        synthetic.write(new, "1.cgxml", synthetic.Land(
            rings=[synthetic.square(500000, 400000, 21)], cadgenno="1", sector="3"))
        task = cgxml_compare.CgxmlCompareTask(old_paths=[old], new_paths=[new])
        # as when the plugin folder denies access to its database
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(siruta.sqlite3, "connect", side_effect=refused):
            self.assertTrue(task.run(), task.error_msg)
        dialog = CompareDialog(iface=self.iface)
        self.addCleanup(dialog.deleteLater)
        shown = []
        dialog.show_result = lambda headline, **kwargs: shown.append(kwargs)
        dialog._on_cgxml_finished(True, task)
        self.assertIn("Baza de date SIRUTA a pluginului nu a putut fi citită, deci denumirile "
                      "de UAT, județ și localitate lipsesc din rezultat.", shown[0]["notes"])
        self.assertTrue(shown[0]["warning"])


class ShellSettingsWindowTests(WidgetsShellTest):
    """Setări open beside the other windows of QGIS and of the plugin."""

    def plugin(self):
        plugin = Cadex(self.iface)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        return plugin

    def open_settings(self, plugin):
        """(plugin.open_settings(), the class names of the modal windows shown meanwhile). A
        modal window is closed after 0.3 s, so the test goes on."""
        from qgis.PyQt.QtCore import QTimer  # type: ignore
        modal = []

        def close():
            dialog = QApplication.activeModalWidget()
            if dialog is not None:
                modal.append(type(dialog).__name__)
                dialog.reject()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(close)
        timer.start(300)
        self.addCleanup(timer.stop)
        settings = plugin.open_settings()
        self.settle(0.4)
        return settings, modal

    @staticmethod
    def shown_help():
        return [w for w in QApplication.topLevelWidgets()
                if isinstance(w, HelpDialog) and w.isVisible()]

    def test_a_help_window_open_before_setari_takes_clicks_while_setari_is_open(self):
        from qgis.PyQt.QtTest import QTest  # type: ignore
        from qgis.PyQt.QtWidgets import QPushButton  # type: ignore
        plugin = self.plugin()
        help_window = plugin.open_help()
        self.settle(0.1)
        settings, modal = self.open_settings(plugin)
        self.assertEqual(modal, [])
        self.assertTrue(settings.isVisible())
        close = next(b for b in help_window.findChildren(QPushButton) if b.text() == "Închide")
        # through the window system, where Qt drops the input of a window a modal one blocks
        QTest.mouseClick(help_window.windowHandle(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier,
                         close.mapTo(help_window, close.rect().center()))
        self.settle(0.1)
        self.assertFalse(help_window.isVisible())

    def test_the_help_opened_from_setari_stays_open_after_setari_closes(self):
        from qgis.PyQt.QtWidgets import QToolButton  # type: ignore
        plugin = self.plugin()
        settings, modal = self.open_settings(plugin)
        self.assertEqual(modal, [])
        next(b for b in settings.findChildren(QToolButton)
             if b.toolTip() == "Cum se folosește acest instrument").click()
        self.settle(0.1)
        settings.reject()
        self.settle(0.1)
        [help_window] = self.shown_help()
        self.assertEqual(help_window.tools.currentItem().text(), "Setări")

    def test_setari_opened_again_is_the_open_window_and_after_closing_shows_the_saved_names(
            self):
        plugin = self.plugin()
        settings, modal = self.open_settings(plugin)
        self.assertEqual(modal, [])
        settings._edits["roles/imobile"].setText("sector 2")
        self.assertIs(plugin.open_settings(), settings)
        self.assertEqual(settings._edits["roles/imobile"].text(), "sector 2")
        settings.reject()
        again = plugin.open_settings()
        self.assertIsNot(again, settings)
        self.assertEqual(again._edits["roles/imobile"].text(),
                         settings_service.DEFAULTS["roles/imobile"])

    def test_saving_new_names_in_setari_rebuilds_the_hidden_tool_windows(self):
        plugin = self.plugin()
        topology = plugin.check_topology()
        topology.reject()
        settings, modal = self.open_settings(plugin)
        self.assertEqual(modal, [])
        settings._edits["roles/imobile"].setText("sector 2")
        settings._save()
        self.assertFalse(settings.isVisible())
        rebuilt = plugin.check_topology()
        self.assertIsNot(rebuilt, topology)
        self.assertEqual(self.names(rebuilt.imobile), ["IMOBILE sector 2"])

    def test_unloading_the_plugin_closes_an_open_setari(self):
        from qgis.PyQt import sip  # type: ignore
        from qgis.PyQt.QtCore import QCoreApplication, QEvent  # type: ignore
        plugin = self.plugin()

        def project_receivers():
            # the windows of earlier tests are deleted first, with their connections
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            return (self.project.receivers(self.project.layersAdded),
                    self.project.receivers(self.project.layersRemoved))
        before = project_receivers()
        settings, modal = self.open_settings(plugin)
        self.assertEqual(modal, [])
        plugin.unload()
        after = project_receivers()
        self.assertTrue(sip.isdeleted(settings))
        self.assertEqual(after, before)

    def test_the_preview_follows_the_project_while_setari_is_open(self):
        main = self.iface.mainWindow()
        main.show()
        self.addCleanup(main.hide)
        settings = SettingsDialog(iface=self.iface, parent=main)
        self.addCleanup(settings.deleteLater)
        settings.show()
        self.addCleanup(settings.hide)
        self.settle(0.1)
        added = helpers.polygon_layer("Imobile sector 9", [])
        self.project.addMapLayer(added)
        self.assertIn("Imobile sector 9", settings._matches["roles/imobile"].text())
        self.project.removeMapLayer(added.id())
        self.assertNotIn("Imobile sector 9", settings._matches["roles/imobile"].text())
        renamed = helpers.polygon_layer("Strat 9", [])
        self.project.addMapLayer(renamed)
        # renamed in the Layers panel of the main window
        main.activateWindow()
        self.settle(0.1)
        renamed.setName("Imobile sector 9")
        settings.activateWindow()
        self.settle(0.1)
        self.assertIn("Imobile sector 9", settings._matches["roles/imobile"].text())


def tearDownModule():
    settings_service.reset_all()
    QgsProject.instance().clear()
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
