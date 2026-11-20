# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verificare Topologică and Topologie Construcții through processing.run."""

import os
import tempfile
import unittest

import helpers
import synthetic

helpers.start_qgis(gui=False)
helpers.start_processing()

from qgis.core import QgsFeature, QgsProcessingException, QgsProject  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.processing_provider.topology import state_key  # noqa: E402
from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import topology_check_service as tcs  # noqa: E402

box = helpers.box_in_romania


def run(name, parameters):
    return processing.run(f"cadex:{name}", parameters, feedback=helpers.feedback())


class TopologyCheckLayersTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_proc_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def check(self, imobile, **parameters):
        return run("topologycheck", dict(
            parameters, IMOBILE=imobile, OUTPUT_AREAS="TEMPORARY_OUTPUT",
            OUTPUT_POINTS="TEMPORARY_OUTPUT"))

    def test_a_layer_without_an_imobil_to_check_stops_the_run(self):
        empty = helpers.polygon_layer("Imobile goale", [], fields="id:string")
        without_geometry = helpers.polygon_layer("Imobile fără geometrie", [],
                                                 fields="id:string")
        feature = QgsFeature(without_geometry.fields())
        feature.setAttributes(["1"])
        without_geometry.dataProvider().addFeatures([feature])
        report = os.path.join(self.tmp, "raport.xlsx")
        for layer in (empty, without_geometry):
            with self.assertRaises(QgsProcessingException) as raised:
                self.check(layer, OUTPUT_XLSX=report)
            self.assertIn(f"Niciun imobil nu a putut fi verificat: layerul '{layer.name()}' nu "
                          "are niciun element cu geometrie.", str(raised.exception))
            self.assertFalse(os.path.exists(report))

    def test_a_hole_between_two_sectors_is_a_problem_but_not_an_error(self):
        # sector 1 west of x = 250 and sector 2 east of it, a 20 x 20 m hole across the line
        imobile = helpers.polygon_layer("Imobile", [
            (box(0, 0, 240, 100), ["A", 1]), (box(260, 0, 500, 100), ["B", 2]),
            (box(240, 0, 250, 40), ["C", 1]), (box(250, 0, 260, 40), ["D", 2]),
            (box(240, 60, 250, 100), ["E", 1]), (box(250, 60, 260, 100), ["F", 2]),
        ], fields="id:string,sector:integer")
        result = self.check(imobile, ID_FIELD="id", SECTOR_FIELD="sector")
        self.assertEqual((result["PROBLEMS"], result["ERRORS"]), (1, 0))
        self.assertEqual([(f["tip"], f["gravitate"], f["sector"], f["valoare"])
                          for f in result["OUTPUT_AREAS"].getFeatures()],
                         [(tcs.GAP, tcs.WARNING, "1/2", 400.0)])


class TopologyCheckCgxmlTests(unittest.TestCase):
    """Verificare Topologică (fișiere CGXML) on files that hold no coordinates, as an eTerra
    export does."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_cgxml_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def export(self, name, first):
        """A folder of three files without coordinates, with the IEs from first on."""
        folder = os.path.join(self.tmp, name)
        for ie in range(first, first + 3):
            synthetic.write(folder, f"{ie}.cgxml",
                            synthetic.Land(rings=[], ie=str(ie), style="eterra"))
        return folder

    def test_sources_in_which_no_imobil_has_coordinates_stop_the_run(self):
        one, other = self.export("eterra", 70001), self.export("eterra_2", 70101)
        report = os.path.join(self.tmp, "raport.xlsx")
        for sources, reason in (
                ([one], "sursa aleasă nu are niciun imobil cu coordonate."),
                ([one, other], "sursele alese nu au niciun imobil cu coordonate.")):
            with self.assertRaises(QgsProcessingException) as raised:
                run("topologycheckcgxml", {
                    "INPUT": sources, "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                    "OUTPUT_POINTS": "TEMPORARY_OUTPUT", "OUTPUT_XLSX": report})
            self.assertIn(f"Niciun imobil nu a putut fi verificat: {reason}",
                          str(raised.exception))
            self.assertFalse(os.path.exists(report))


class BuildingTopologyToleranceTests(unittest.TestCase):
    """Imobil 1 from x 0 to 20 with a construcție that reaches 5 mm into imobil 2."""

    def tearDown(self):
        QgsProject.instance().clear()

    def cut(self, **parameters):
        imobile = helpers.polygon_layer("Imobile", [
            (box(0, 0, 20, 20), ["1"]), (box(20, 0, 40, 20), ["2"])], fields="id:string")
        constructii = helpers.polygon_layer(
            "Construcții", [(box(15, 5, 20.005, 10), ["C1"])], fields="nr:string")
        result = run("buildingtopology", dict(parameters, IMOBILE=imobile,
                                              CONSTRUCTII=constructii,
                                              OUTPUT="TEMPORARY_OUTPUT"))
        return {state: result[state_key(state)] for state in bts.STATES
                if result[state_key(state)]}

    def test_a_building_within_the_tolerance_of_the_check_is_not_cut(self):
        self.assertEqual(self.cut(), {bts.UNCHANGED: 1})

    def test_with_the_tolerance_set_to_zero_it_is_cut(self):
        self.assertEqual(self.cut(TOLERANCE=0.0), {bts.FIXED: 1})


if __name__ == "__main__":
    helpers.main()
