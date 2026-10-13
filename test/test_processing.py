# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Processing algorithms through processing.run, on synthetic data with known results."""

import configparser
import os
import sys
import tempfile
import unittest

import helpers
import synthetic
from synthetic import Building, Land

helpers.start_qgis(gui=False)

from qgis.core import (  # noqa: E402  # type: ignore
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsProcessingContext, QgsProcessingException, QgsProcessingFeedback, QgsProject,
)

# QGIS's own Processing plugin, next to the other bundled Python plugins
sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
from processing.core.Processing import Processing  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.processing_provider import provider as provider_module  # noqa: E402
from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import cgxml_compare, intravilan_service, roles, xlsx  # noqa: E402

# the algorithms refuse a layer whose coordinates look like degrees or lie outside Romania,
# so the hand-built layers are drawn from a point in Romania
ORIGIN = (450000, 350000)


def square(x, y, size):
    return helpers.square(ORIGIN[0] + x, ORIGIN[1] + y, size)


def moved(wkt):
    """wkt drawn from the same point in Romania as square()."""
    geometry = QgsGeometry.fromWkt(wkt)
    geometry.translate(*ORIGIN)
    return geometry.asWkt()


Processing.initialize()
PROVIDER = provider_module.CadexProvider()
QgsApplication.processingRegistry().addProvider(PROVIDER)


def run(name, parameters, feedback=None):
    return processing.run(f"cadex:{name}", parameters, feedback=feedback or QgsProcessingFeedback())


def metadata_version():
    parser = configparser.ConfigParser()
    parser.read(os.path.join(helpers.PLUGIN_DIR, "metadata.txt"), encoding="utf-8")
    return parser.get("general", "version")


class ProviderTests(unittest.TestCase):

    def test_every_algorithm_is_listed_with_help_and_icon(self):
        ids = sorted(a.id() for a in PROVIDER.algorithms())
        self.assertEqual(ids, sorted(f"cadex:{cls.NAME}" for cls in provider_module.ALGORITHMS))
        self.assertEqual(len(ids), 8)
        self.assertEqual(PROVIDER.versionInfo(), metadata_version())
        for algorithm in PROVIDER.algorithms():
            self.assertTrue(algorithm.displayName(), algorithm.id())
            # the toolbox tooltip, one sentence
            self.assertTrue(algorithm.shortDescription().endswith("."), algorithm.id())
            self.assertEqual(algorithm.shortDescription().count(". "), 0, algorithm.id())
            self.assertTrue(algorithm.group(), algorithm.id())
            self.assertIn("Stereo 70", algorithm.shortHelpString(), algorithm.id())
            self.assertFalse(algorithm.icon().isNull(), algorithm.id())

    def test_the_help_panel_shows_whole_sentences_in_any_theme(self):
        for algorithm in PROVIDER.algorithms():
            text = algorithm.shortHelpString()
            # the panel makes a paragraph of every line
            self.assertNotIn("\n", text, algorithm.id())
            # its style sheet colours <b> dark grey, unreadable on a dark theme
            self.assertNotIn("<b>", text, algorithm.id())
        convert = QgsApplication.processingRegistry().algorithmById("cadex:cgxmlconvert")
        self.assertIn("Livrările și exporturile eTerra", convert.shortHelpString())
        self.assertIn("<strong>Fișiere CGXML, arhive ZIP sau foldere</strong>",
                      convert.shortHelpString())


class ToolTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_proc_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def test_cgxml_conversion(self):
        xlsx_path = os.path.join(self.tmp, "conversie.xlsx")
        result = run("cgxmlconvert", {
            "INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
            "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT", "OUTPUT_CONSTRUCTII": "TEMPORARY_OUTPUT",
            "OUTPUT_XLSX": xlsx_path})
        self.assertEqual((result["FILES"], result["UNREAD"], result["IMOBILE"]), (4, 0, 4))
        imobile = result["OUTPUT_IMOBILE"]
        # bom_30003 has no coordinates, so it is in the table and not on the map
        self.assertEqual(imobile.featureCount(), 3)
        self.assertEqual(imobile.crs().authid(), "EPSG:3844")
        self.assertIn("propr", imobile.fields().names())
        self.assertEqual(result["OUTPUT_CONSTRUCTII"].featureCount(), 1)
        self.assertEqual(xlsx.sheet_names(xlsx_path)[:3], ["Sumar", "Imobile", "Construcții"])

    def test_a_converted_file_opened_again_keeps_its_column_roles_batch_and_labels(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        gpkg = os.path.join(self.tmp, "conv.gpkg")
        shp = os.path.join(self.tmp, "conv_constructii.shp")
        result = run("cgxmlconvert", {
            "INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
            "OUTPUT_IMOBILE": f"ogr:dbname='{gpkg}' table=\"imobile\" (geom)",
            "OUTPUT_CONSTRUCTII": shp})
        for uri in (result["OUTPUT_IMOBILE"], result["OUTPUT_CONSTRUCTII"]):
            again = QgsVectorLayer(uri, "din nou", "ogr")
            self.assertTrue(again.isValid(), uri)
            self.assertEqual(roles.column_of(again, "id"), "id", uri)
            self.assertTrue(roles.batch_of(again), uri)
            self.assertTrue(again.labelsEnabled(), uri)

    def test_conversion_reports_invalid_geometry_like_the_window(self):
        folder = os.path.join(self.tmp, "src")
        # the edge back from the right side crosses the bottom one
        spike = [(500000, 400000), (500020, 400000), (500020, 400010), (500010, 399995),
                 (500000, 400010)]
        synthetic.write(folder, "1307.cgxml", Land(rings=[spike], cadgenno="1307"))
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        result = run("cgxmlconvert", {"INPUT": [folder], "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"},
                     Feedback())
        self.assertEqual(result["OUTPUT_IMOBILE"].featureCount(), 1)
        self.assertEqual(warnings, ["1307.cgxml: geometrie invalidă în layerul Imobile, "
                                    "păstrată așa cum este în fișier"])

    def test_nothing_to_convert_is_an_error(self):
        empty = os.path.join(self.tmp, "gol")
        os.makedirs(empty)
        with self.assertRaises(QgsProcessingException):
            run("cgxmlconvert", {"INPUT": [empty], "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"})

    def test_topology_check_without_cgxml_files_is_an_error(self):
        empty = os.path.join(self.tmp, "gol")
        os.makedirs(empty)
        with self.assertRaises(QgsProcessingException):
            run("topologycheckcgxml", {"INPUT": [empty], "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})

    def test_cgxml_comparison(self):
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        synthetic.write(old, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1",
                                             buildings=[Building(1, [square_ring(2, 5)])]))
        synthetic.write(old, "2.cgxml", Land(rings=[square_ring(30)], cadgenno="2"))
        synthetic.write(new, "1.cgxml", Land(rings=[square_ring(0, 21)], cadgenno="1",
                                             buildings=[Building(1, [square_ring(2, 5)])]))
        synthetic.write(new, "3.cgxml", Land(rings=[square_ring(60)], cadgenno="3"))
        result = run("comparecgxml", {
            "OLD": [old], "NEW": [new], "OUTPUT_ADDED": "TEMPORARY_OUTPUT",
            "OUTPUT_DELETED": "TEMPORARY_OUTPUT", "OUTPUT_CHANGED": "TEMPORARY_OUTPUT",
            "OUTPUT_BUILDINGS_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual((result["MATCHED"], result["CHANGED"], result["ONLY_OLD"],
                          result["ONLY_NEW"]), (1, 1, 1, 1))
        changed = next(result["OUTPUT_CHANGED"].getFeatures())
        self.assertEqual(changed["id"], 1)
        self.assertIn("geometrie", changed["modificari"])
        # the measured area changed with the boundary, so only the boundary changed
        self.assertIn("sup_mas", changed["modificari"])
        self.assertEqual(changed["tip_modif"], "Doar limita")
        self.assertEqual(result["OUTPUT_CHANGED"].crs().authid(), "EPSG:3844")
        self.assertEqual(result["OUTPUT_BUILDINGS_CHANGED"].featureCount(), 0)

    def test_case_is_ignored_by_default_only_between_an_eterra_export_and_a_delivery(self):
        eterra, livrare, again = (os.path.join(self.tmp, n) for n in ("eterra", "livrare", "nou"))
        synthetic.write(eterra, "70001.cgxml", Land(rings=[square_ring(0)], ie="70001",
                                                    owners=("Popescu Ion",), style="eterra"))
        synthetic.write(livrare, "501.cgxml", Land(rings=[square_ring(0)], cadgenno="501",
                                                   ie="70001", owners=("POPESCU ION",)))
        synthetic.write(again, "501.cgxml", Land(rings=[square_ring(0)], cadgenno="501",
                                                 ie="70001", owners=("Popescu Ion",)))
        ie = {"OLD": [eterra], "NEW": [livrare], "MODE": 1}
        self.assertEqual(run("comparecgxml", ie)["CHANGED"], 0)
        self.assertEqual(run("comparecgxml", dict(ie, FOLD_TEXT=0))["CHANGED"], 1)
        by_name = {"OLD": [livrare], "NEW": [again], "MODE": 0}
        self.assertEqual(run("comparecgxml", by_name)["CHANGED"], 1)
        self.assertEqual(run("comparecgxml", dict(by_name, FOLD_TEXT=1))["CHANGED"], 0)

    def test_layer_comparison(self):
        old = helpers.polygon_layer("vechi", [(square(0, 0, 10), ["1", "A"]),
                                              (square(20, 0, 10), ["2", "B"])],
                                    fields="ie:string,propr:string")
        new = helpers.polygon_layer("nou", [(square(0, 0, 10), ["1", "A2"]),
                                            (square(40, 0, 10), ["3", "C"])],
                                    fields="ie:string,propr:string")
        result = run("comparelayers", {
            "OLD": old, "OLD_KEY": "ie", "NEW": new, "NEW_KEY": "ie",
            "OUTPUT_ADDED": "TEMPORARY_OUTPUT", "OUTPUT_DELETED": "TEMPORARY_OUTPUT",
            "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual((result["ADDED"], result["DELETED"], result["CHANGED"],
                          result["UNCHANGED"]), (1, 1, 1, 0))
        changed = next(result["OUTPUT_CHANGED"].getFeatures())
        self.assertEqual((changed["modificari"], changed["tip_modif"]), ("propr", "Doar datele"))

    def test_layer_comparison_keys_on_id_by_default(self):
        old = helpers.polygon_layer("vechi", [(square(0, 0, 10), [1101, "A"]),
                                              (square(20, 0, 10), [1102, "B"])],
                                    fields="id:integer,propr:string")
        new = helpers.polygon_layer("nou", [(square(20, 0, 10), [1102, "B2"])],
                                    fields="id:integer,propr:string")
        result = run("comparelayers", {"OLD": old, "NEW": new})
        self.assertEqual((result["ADDED"], result["DELETED"], result["CHANGED"],
                          result["UNCHANGED"]), (0, 1, 1, 0))
        no_id = helpers.polygon_layer("fara id", [(square(0, 0, 10), ["A"])],
                                      fields="propr:string")
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparelayers", {"OLD": no_id, "NEW": new})
        self.assertIn("'fara id' nu are coloana 'id'", str(raised.exception))

    def test_two_converted_deliveries_compare_as_their_files_do(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        layers = []
        # the parcels in another order, the plot once written with its letter
        for name, parcels, plot in (("vechi", (("A", True), ("CC", True)), "T12"),
                                    ("nou", (("CC", True), ("A", True)), "12")):
            folder = os.path.join(self.tmp, name)
            synthetic.write(folder, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1",
                                                    sector="1", parcels=parcels, plot=plot))
            gpkg = os.path.join(self.tmp, f"{name}.gpkg")
            result = run("cgxmlconvert", {
                "INPUT": [folder],
                "OUTPUT_IMOBILE": f"ogr:dbname='{gpkg}' table=\"imobile\" (geom)"})
            layers.append(QgsVectorLayer(result["OUTPUT_IMOBILE"], name, "ogr"))
        result = run("comparelayers", {"OLD": layers[0], "NEW": layers[1]})
        self.assertEqual((result["CHANGED"], result["UNCHANGED"]), (0, 1))
        files = run("comparecgxml", {"OLD": [os.path.join(self.tmp, "vechi")],
                                     "NEW": [os.path.join(self.tmp, "nou")]})
        self.assertEqual((files["CHANGED"], files["UNCHANGED"]), (0, 1))

    def test_an_eterra_layer_against_a_delivery_points_to_the_cgxml_comparison(self):
        layers = []
        # an eTerra export has no cadastral sector
        for name, row in (("eterra", [7339, 50124, None]), ("livrare", [501, 50124, 3])):
            layer = helpers.polygon_layer(name, [(square(0, 0, 10), row)],
                                          fields="id:integer,ie:integer,sector:integer")
            roles.mark_columns(layer, [("id", "id"), ("ie", "ie"), ("sector", "sector")])
            roles.mark_batch(layer, "20260101")
            layers.append(layer)
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        run("comparelayers", {"OLD": layers[0], "NEW": layers[1], "OLD_KEY": "ie",
                              "NEW_KEY": "ie"}, Feedback())
        self.assertEqual(len(warnings), 1)
        self.assertIn("'eterra' este un export eTerra convertit", warnings[0])
        self.assertIn("comparați fișierele CGXML, potrivite după IE", warnings[0])
        # left at 'id', the key of two conversions is the one the window takes: the IE
        result = run("comparelayers", {"OLD": layers[0], "NEW": layers[1]}, Feedback())
        self.assertEqual((result["ADDED"], result["DELETED"]), (0, 0))
        # two deliveries get no warning
        warnings.clear()
        run("comparelayers", {"OLD": layers[1], "NEW": layers[1]}, Feedback())
        self.assertEqual(warnings, [])

        # construcții pair by the IE of their imobil, since the IDs differ
        buildings = {}
        for name, row, columns in (
                ("c eterra", [7339, "C1", 50124], "id:integer,nr_constr:string,ie_imobil:integer"),
                ("c livrare", [501, "C1", 50124], "id:integer,nr_constr:string,ie_imobil:integer"),
                ("c fara ie", [7339, "C1"], "id:integer,nr_constr:string")):
            layer = helpers.polygon_layer(name, [(square(2, 2, 5), row)], fields=columns)
            roles.mark_columns(layer, [(c.split(":")[0], c.split(":")[0])
                                       for c in columns.split(",")])
            roles.mark_batch(layer, "20260101")
            buildings[name] = layer
        parameters = {"OLD": layers[0], "NEW": layers[1], "OLD_KEY": "ie", "NEW_KEY": "ie",
                      "OLD_BUILDINGS": buildings["c eterra"],
                      "NEW_BUILDINGS": buildings["c livrare"]}
        result = run("comparelayers", parameters, Feedback())
        self.assertEqual((result["BUILDINGS_UNCHANGED"], result["BUILDINGS_ADDED"],
                          result["BUILDINGS_DELETED"]), (1, 0, 0))
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparelayers", dict(parameters, OLD_BUILDINGS=buildings["c fara ie"]), Feedback())
        self.assertIn("IE-ul imobilului", str(raised.exception))

    def test_layer_comparison_with_buildings(self):
        imobile = helpers.polygon_layer("imobile", [(square(0, 0, 40), ["1", "A"])],
                                        fields="ie:string,propr:string")
        rows_old = [(square(5, 5, 5), ["1", "C1", "CL"]), (square(15, 5, 5), ["1", "C2", "CL"])]
        rows_new = [(square(5, 5, 5), ["1", "C1", "CA"]), (square(25, 5, 5), ["1", "C3", "CL"])]
        old_b = helpers.polygon_layer("constructii vechi", rows_old,
                                      fields="ie_imobil:string,nr:string,dest:string")
        new_b = helpers.polygon_layer("constructii noi", rows_new,
                                      fields="ie_imobil:string,nr:string,dest:string")
        parameters = {
            "OLD": imobile, "OLD_KEY": "ie", "NEW": imobile, "NEW_KEY": "ie",
            "OLD_BUILDINGS": old_b, "OLD_BUILDING_IMOBIL": "ie_imobil",
            "OLD_BUILDING_NUMBER": "nr",
            "NEW_BUILDINGS": new_b, "NEW_BUILDING_IMOBIL": "ie_imobil",
            "NEW_BUILDING_NUMBER": "nr",
            "OUTPUT_BUILDINGS_ADDED": "TEMPORARY_OUTPUT",
            "OUTPUT_BUILDINGS_CHANGED": "TEMPORARY_OUTPUT"}
        result = run("comparelayers", parameters)
        self.assertEqual((result["UNCHANGED"], result["BUILDINGS_ADDED"],
                          result["BUILDINGS_DELETED"], result["BUILDINGS_CHANGED"],
                          result["BUILDINGS_UNCHANGED"]), (1, 1, 1, 1, 0))
        self.assertEqual([f["nr"] for f in result["OUTPUT_BUILDINGS_ADDED"].getFeatures()],
                         ["C3"])
        changed = next(result["OUTPUT_BUILDINGS_CHANGED"].getFeatures())
        self.assertEqual((changed["nr"], changed["modificari"]), ("C1", "dest"))

        # a converted layer stores which field holds which CGXML column
        for layer in (old_b, new_b):
            roles.mark_columns(layer, [("id", "ie_imobil"), ("nr_constr", "nr")])
        converted = {k: v for k, v in parameters.items() if "BUILDING_" not in k}
        self.assertEqual(run("comparelayers", converted)["BUILDINGS_CHANGED"], 1)

        with self.assertRaises(QgsProcessingException):
            run("comparelayers", dict(converted, NEW_BUILDINGS=None))
        # the imobil on one side only would match nothing
        with self.assertRaises(QgsProcessingException):
            run("comparelayers", dict(parameters, NEW_BUILDING_IMOBIL=None,
                                      NEW_BUILDINGS=helpers.polygon_layer(
                                          "simplu", rows_new,
                                          fields="ie_imobil:string,nr:string,dest:string")))

    def test_a_row_number_never_links_a_building(self):
        # the Processing panel takes the mouse wheel: scrolling over the empty imobil combo
        # moves it to fid, the first field
        imobile = helpers.polygon_layer("imobile", [(square(0, 0, 40), [1101])],
                                        fields="id:integer")
        rows = [(square(5, 5, 5), [1101, "C1"]), (square(15, 5, 5), [1101, "C2"])]
        old_b, new_b = (helpers.gpkg_layer(helpers.polygon_layer(
            name, rows, fields="id:integer,nr_constr:string"),
            os.path.join(self.tmp, f"{name}.gpkg")) for name in ("vechi", "nou"))
        for layer in (old_b, new_b):
            roles.mark_columns(layer, [("id", "id"), ("nr_constr", "nr_constr")])
        parameters = {"OLD": imobile, "NEW": imobile,
                      "OLD_BUILDINGS": old_b, "NEW_BUILDINGS": new_b}
        self.assertEqual(run("comparelayers", parameters)["BUILDINGS_UNCHANGED"], 2)
        for key in ("OLD_BUILDING_IMOBIL", "NEW_BUILDING_NUMBER"):
            with self.assertRaises(QgsProcessingException) as raised:
                run("comparelayers", dict(parameters, **{key: "fid"}))
            self.assertIn("'fid'", str(raised.exception))
            self.assertIn("numerotează rândurile", str(raised.exception))

    def test_a_row_number_is_never_a_key(self):
        rows = [(square(30 * i, 0, 10), [1100 + i]) for i in range(1, 4)]
        old = helpers.gpkg_layer(helpers.polygon_layer("vechi", rows, fields="id:integer"),
                                 os.path.join(self.tmp, "v1.gpkg"))
        new = helpers.gpkg_layer(helpers.polygon_layer("nou", rows[1:], fields="id:integer"),
                                 os.path.join(self.tmp, "v2.gpkg"))
        for key in ("OLD_KEY", "NEW_KEY"):
            with self.assertRaises(QgsProcessingException) as raised:
                run("comparelayers", {"OLD": old, "NEW": new, key: "fid"})
            self.assertIn("numerotează rândurile", str(raised.exception))
        constructii = helpers.gpkg_layer(helpers.polygon_layer(
            "constructii", [(square(32, 2, 3), [1101, "C1"])],
            fields="id:integer,nr_constr:string"), os.path.join(self.tmp, "c.gpkg"))
        with self.assertRaises(QgsProcessingException) as raised:
            run("buildingtopology", {"IMOBILE": old, "CONSTRUCTII": constructii,
                                     "LINK_FIELD": "id", "KEY_FIELD": "fid",
                                     "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertIn("numerotează rândurile", str(raised.exception))

    def test_converted_layers_compare_the_columns_of_the_cgxml_comparison(self):
        layers = {}
        for label, owner in (("vechi", "STAN ANA"), ("nou", "ALEXE VALENTIN")):
            imobile = helpers.polygon_layer(f"imobile {label}",
                                            [(square(0, 0, 40), [1, owner, "X"])],
                                            fields="id:integer,propr:string,uat:string")
            constructii = helpers.polygon_layer(
                f"constructii {label}", [(square(5, 5, 5), [1, "C1", "CL", owner])],
                fields="id:integer,nr_constr:string,destinatie:string,propr:string")
            roles.mark_columns(imobile, [(k, k) for k in ("id", "propr", "uat")])
            roles.mark_columns(constructii, [(k, k) for k in ("id", "nr_constr", "destinatie",
                                                              "propr")])
            for layer in (imobile, constructii):
                roles.mark_batch(layer, label)
            layers[label] = (imobile, constructii)
        result = run("comparelayers", {
            "OLD": layers["vechi"][0], "OLD_KEY": "id", "NEW": layers["nou"][0], "NEW_KEY": "id",
            "OLD_BUILDINGS": layers["vechi"][1], "NEW_BUILDINGS": layers["nou"][1],
            "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        # the owner changed on the imobil, not again on each of its buildings
        self.assertEqual((result["CHANGED"], result["BUILDINGS_CHANGED"]), (1, 0))
        self.assertEqual(next(result["OUTPUT_CHANGED"].getFeatures())["modificari"], "propr")

    def test_cgxml_comparison_of_chosen_building_columns(self):
        old, new = os.path.join(self.tmp, "vechi"), os.path.join(self.tmp, "nou")
        synthetic.write(old, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1", buildings=[
            Building(1, [square_ring(2, 5)], destination="CL")]))
        synthetic.write(new, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1", buildings=[
            Building(1, [square_ring(2, 5)], destination="CA")]))
        result = run("comparecgxml", {"OLD": [old], "NEW": [new]})
        self.assertEqual(result["BUILDINGS_CHANGED"], 1)
        columns = [key for key, _label in cgxml_compare.comparable_building_columns()]
        # the default columns, the destination code left out
        others = [key for key in columns
                  if key in cgxml_compare.BUILDING_COLUMNS and key != "destinatie"]
        result = run("comparecgxml", {"OLD": [old], "NEW": [new], "BUILDING_COLUMNS": others})
        self.assertEqual(result["BUILDINGS_CHANGED"], 0)

    def test_cgxml_comparison_warns_of_eterra_imobile_without_coordinates(self):
        eterra, livrare = os.path.join(self.tmp, "eterra"), os.path.join(self.tmp, "livrare")
        synthetic.write(eterra, "70002.cgxml", Land(rings=[], measured_area=400.0, ie="70002",
                                                    style="eterra"))
        synthetic.write(livrare, "503.cgxml", Land(rings=[square_ring(0)], cadgenno="503",
                                                   ie="70002", sector="3"))
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        run("comparecgxml", {"OLD": [eterra], "NEW": [livrare],
                             "MODE": cgxml_compare.MODES.index(cgxml_compare.IE)}, Feedback())
        self.assertTrue(any("nu are coordonate" in w for w in warnings), warnings)

    def test_compare_geopackages_after_a_removal(self):
        # the second version drops the first imobil, so every fid after it shifts by one
        rows = [(square(30 * i, 0, 10), [str(i), f"P{i}"]) for i in range(1, 5)]
        old = helpers.gpkg_layer(helpers.polygon_layer(
            "vechi", rows, fields="ie:string,propr:string"), os.path.join(self.tmp, "v1.gpkg"))
        new = helpers.gpkg_layer(helpers.polygon_layer(
            "nou", rows[1:], fields="ie:string,propr:string"), os.path.join(self.tmp, "v2.gpkg"))
        result = run("comparelayers", {"OLD": old, "OLD_KEY": "ie", "NEW": new, "NEW_KEY": "ie"})
        self.assertEqual((result["ADDED"], result["DELETED"], result["CHANGED"],
                          result["UNCHANGED"]), (0, 1, 0, 3))

    def test_topology_on_layers(self):
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), ["1", 5]),
            (moved("POLYGON((19.9 0, 40 0, 40 20, 19.9 20, 19.9 0))"), ["2", 5]),
        ], fields="id:string,sector:integer")
        result = run("topologycheck", {
            "IMOBILE": imobile, "ID_FIELD": "id", "SECTOR_FIELD": "sector",
            "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        self.assertEqual(result["PROBLEMS"], 1)
        overlap = next(result["OUTPUT_AREAS"].getFeatures())
        self.assertAlmostEqual(overlap["valoare"], 2.0, places=4)

    def test_topology_reports_an_invalid_imobil(self):
        # Processing stops on an invalid geometry by default, which would hide the finding
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), ["1"]),
            (moved("POLYGON((30 0, 50 20, 50 0, 30 20, 30 0))"), ["2"]),
        ], fields="id:string")
        result = run("topologycheck", {
            "IMOBILE": imobile, "ID_FIELD": "id",
            "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        found = [(f["tip"], f["obiect"]) for key in ("OUTPUT_AREAS", "OUTPUT_POINTS")
                 for f in result[key].getFeatures()]
        self.assertEqual(found, [("Geometrie invalidă", "imobil 2")])

    def test_topology_on_cgxml(self):
        result = run("topologycheckcgxml", {
            "INPUT": [os.path.join(helpers.DATA_DIR, "eterra_100001.cgxml"),
                      os.path.join(helpers.DATA_DIR, "livrare_10188.cgxml")],
            "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        # the 10 x 10 m hole of 10188 is ground of sector 3 that no imobil covers
        self.assertEqual((result["PROBLEMS"], result["ERRORS"]), (1, 1))
        gap = next(result["OUTPUT_AREAS"].getFeatures())
        self.assertEqual(gap["tip"], "Gol între imobile")
        self.assertAlmostEqual(gap["valoare"], 100.0, places=3)

    def test_a_row_number_never_links_a_building_to_its_imobil(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), [1101])],
                                        fields="id:integer")
        constructii = helpers.gpkg_layer(helpers.polygon_layer(
            "Constructii", [(square(10, 10, 20), [1101, "C1"])],
            fields="id:integer,nr_constr:string"), os.path.join(self.tmp, "c.gpkg"))
        for name, extra in (("topologycheck", {"OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                               "OUTPUT_POINTS": "TEMPORARY_OUTPUT",
                                               "ID_FIELD": "id"}),
                            ("buildingtopology", {"OUTPUT": "TEMPORARY_OUTPUT",
                                                  "KEY_FIELD": "id"})):
            parameters = dict(extra, IMOBILE=imobile, CONSTRUCTII=constructii)
            run(name, dict(parameters, LINK_FIELD="id"))
            with self.assertRaises(QgsProcessingException) as raised:
                run(name, dict(parameters, LINK_FIELD="fid"))
            self.assertIn("'fid'", str(raised.exception), name)

    def test_building_topology(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["P1"]),
                                                    (square(100, 0, 100), ["P2"])],
                                        fields="cod:string")
        constructii = helpers.polygon_layer("Constructii", [(square(10, 10, 20), ["C1"]),
                                                            (square(95, 50, 20), ["C3"])],
                                            fields="nr:string")
        result = run("buildingtopology", {
            "IMOBILE": imobile, "CONSTRUCTII": constructii, "THRESHOLD": 50.0,
            "JOIN_FIELDS": ["cod"], "OUTPUT": "TEMPORARY_OUTPUT"})
        from cadex.processing_provider.topology import state_key
        self.assertEqual(result[state_key(bts.UNCHANGED)], 1)
        self.assertEqual(result[state_key(bts.LOSS)], 1)
        layer = result["OUTPUT"]
        self.assertIn("imb_cod", layer.fields().names())
        self.assertEqual(sorted(f["stare"] for f in layer.getFeatures()),
                         sorted([bts.UNCHANGED, bts.LOSS]))

    def test_intravilan_from_wgs84_comes_out_in_stereo70(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["A"])],
                                        fields="cod:string")
        # the intravilan covers the west half of the imobil, stored in WGS 84
        to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3844"),
                                        QgsCoordinateReferenceSystem("EPSG:4326"),
                                        QgsProject.instance())
        zone = QgsGeometry.fromWkt("POLYGON((449900 349900, 450050 349900, 450050 350200, "
                                   "449900 350200, 449900 349900))")
        zone.densifyByCount(50)
        zone.transform(to_wgs)
        intravilan = helpers.polygon_layer("Intravilan", [(zone.asWkt(), [1])], crs="EPSG:4326")
        result = run("intravilanreport", {
            "IMOBILE": imobile, "INTRAVILAN": intravilan, "ID_FIELD": "cod",
            "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertEqual(result["PARTIAL_COUNT"], 1)
        feature = next(result["OUTPUT"].getFeatures())
        self.assertEqual(result["OUTPUT"].crs().authid(), "EPSG:3844")
        self.assertEqual(feature["situatie"], intravilan_service.PARTIAL)
        self.assertAlmostEqual(feature["sup_intra"], 5000, delta=2)

    def test_area_reduction_from_a_csv_file(self):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("nr,suprafata_act,ie\n1,6000,\n2,4000,\n3,2000,50124\n")
        result = run("areareduction", {
            "TABLE": path, "AREA_FIELD": "suprafata_act", "FIXED_FIELD": "ie",
            "CONTOUR_AREA": 10000, "ROUNDING": 1, "OUTPUT": "TEMPORARY_OUTPUT",
            "OUTPUT_XLSX": os.path.join(self.tmp, "diminuare.xlsx")})
        self.assertAlmostEqual(result["FACTOR"], 0.8)
        values = sorted(f["sup_diminuata"] for f in result["OUTPUT"].getFeatures())
        self.assertEqual(values, [2000.0, 3200.0, 4800.0])
        self.assertIn("Diminuare", xlsx.sheet_names(result["OUTPUT_XLSX"]))

    def test_area_reduction_counts_a_parcel_with_an_invalid_geometry(self):
        table = helpers.polygon_layer("Parcele", [
            (square(0, 0, 10), [6000]),
            ("POLYGON((30 0, 50 20, 50 0, 30 20, 30 0))", [4000]),
        ], fields="suprafata_act:integer")
        result = run("areareduction", {
            "TABLE": table, "AREA_FIELD": "suprafata_act", "CONTOUR_AREA": 8000,
            "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertEqual(sorted(f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()),
                         [3200.0, 4800.0])

    def test_loaded_outputs_get_the_window_styles(self):
        from cadex.processing_provider.base import Styler
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), ["1"]),
            (moved("POLYGON((19.9 0, 40 0, 40 20, 19.9 20, 19.9 0))"), ["2"]),
        ], fields="id:string")
        result = processing.runAndLoadResults("cadex:topologycheck", {
            "IMOBILE": imobile, "ID_FIELD": "id",
            "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        layer = QgsProject.instance().mapLayer(result["OUTPUT_AREAS"])
        self.assertIsNotNone(layer)
        self.assertEqual(layer.renderer().type(), "categorizedSymbol")
        # every styler ran and removed itself from Styler._alive
        self.assertTrue(roles.is_report(layer))
        self.assertEqual(Styler._alive, [])

    def test_a_loaded_conversion_is_marked_as_input_for_the_windows(self):
        result = processing.runAndLoadResults("cadex:cgxmlconvert", {
            "INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
            "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT", "OUTPUT_CONSTRUCTII": "TEMPORARY_OUTPUT"})
        imobile = QgsProject.instance().mapLayer(result["OUTPUT_IMOBILE"])
        constructii = QgsProject.instance().mapLayer(result["OUTPUT_CONSTRUCTII"])
        # input for the other tools, not a report; both layers of the run share one batch
        self.assertFalse(roles.is_report(imobile))
        self.assertTrue(roles.batch_of(imobile))
        self.assertEqual(roles.batch_of(imobile), roles.batch_of(constructii))
        self.assertEqual(roles.column_of(constructii, "id"), "id")
        self.assertEqual(roles.column_of(constructii, "nr_constr"), "nr_constr")
        # named after the source folder, so two conversions in one project differ
        self.assertEqual((imobile.name(), constructii.name()), ("Imobile data", "Construcții data"))

    def test_loaded_buildings_layer_lists_only_the_states_found(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["1"])],
                                        fields="id:string")
        constructii = helpers.polygon_layer("Constructii", [(square(90, 10, 20), ["C1"])],
                                            fields="nr:string")
        result = processing.runAndLoadResults("cadex:buildingtopology", {
            "IMOBILE": imobile, "CONSTRUCTII": constructii, "OUTPUT": "TEMPORARY_OUTPUT"})
        layer = QgsProject.instance().mapLayer(result["OUTPUT"])
        self.assertEqual([c.value() for c in layer.renderer().categories()], [bts.LOSS])

    def test_a_failed_run_leaves_no_styler_waiting(self):
        from cadex.processing_provider.base import Styler
        algorithm = QgsApplication.processingRegistry().createAlgorithmById("cadex:comparelayers")
        algorithm.style("layer-id", lambda _layer: None)
        # style() only queues: if processAlgorithm raised after it, QGIS would skip
        # postProcessAlgorithm
        self.assertEqual(Styler._alive, [])
        # a destination the context will not load gets no styler either
        self.assertEqual(algorithm.postProcessAlgorithm(QgsProcessingContext(),
                                                        QgsProcessingFeedback()), {})
        self.assertEqual(Styler._alive, [])
        self.assertEqual(algorithm._pending_styles, [])

    def test_a_source_that_does_not_load_is_a_clean_error(self):
        algorithm = QgsApplication.processingRegistry().createAlgorithmById("cadex:topologycheck")
        context, feedback = QgsProcessingContext(), QgsProcessingFeedback()
        missing = os.path.join(self.tmp, "lipsa.gpkg")
        with self.assertRaises(QgsProcessingException):
            algorithm.snapshot({"IMOBILE": missing}, "IMOBILE", context, feedback)
        with self.assertRaises(QgsProcessingException):
            algorithm.optional_snapshot({"LIMITS": missing}, "LIMITS", context, feedback)
        # an optional input left empty is just absent
        self.assertIsNone(algorithm.optional_snapshot({}, "LIMITS", context, feedback))

    def test_zero_minimum_area_on_a_clean_sector(self):
        # touching neighbours share no area, so a zero threshold must not report them
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 10), ["1"]),
                                                    (square(10, 0, 10), ["2"])], fields="id:string")
        result = run("topologycheck", {"IMOBILE": imobile, "MIN_AREA": 0.0,
                                       "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        self.assertEqual(result["PROBLEMS"], 0)

    def test_overlap_with_the_chosen_roads_sector_is_a_warning(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 20), ["1", "1"]),
                                                    (square(19, 0, 20), ["2", "9"])],
                                        fields="id:string,sector:string")
        parameters = {"IMOBILE": imobile, "ID_FIELD": "id", "SECTOR_FIELD": "sector",
                      "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}
        self.assertEqual(run("topologycheck", parameters)["ERRORS"], 1)
        result = run("topologycheck", dict(parameters, ROADS_SECTOR="9"))
        self.assertEqual((result["PROBLEMS"], result["ERRORS"]), (1, 0))

    def test_a_cancelled_run_raises(self):
        feedback = QgsProcessingFeedback()
        feedback.cancel()
        with self.assertRaises(QgsProcessingException):
            run("topologycheckcgxml", {"INPUT": [helpers.DATA_DIR],
                                       "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, feedback)


class ProcessingBaseTests(unittest.TestCase):
    """What every CADex algorithm gets from CadexAlgorithm and its Styler."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_base_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def intravilan_inputs(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["A"]),
                                                    (square(300, 0, 100), ["B"])],
                                        fields="cod:string")
        intravilan = helpers.polygon_layer("Intravilan", [(square(-10, -10, 150), [1])],
                                           fields="nume:string")
        return imobile, intravilan

    def test_a_report_saved_to_a_file_is_still_a_report_in_another_project(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        imobile, intravilan = self.intravilan_inputs()
        for path in (os.path.join(self.tmp, "Raport intravilan.gpkg"),
                     os.path.join(self.tmp, "raport_intravilan.shp")):
            processing.runAndLoadResults("cadex:intravilanreport", {
                "IMOBILE": imobile, "INTRAVILAN": intravilan, "ID_FIELD": "cod",
                "OUTPUT": path})
            QgsProject.instance().removeAllMapLayers()
            # next day, in a new project
            again = QgsVectorLayer(path, "Raport intravilan", "ogr")
            self.assertTrue(again.isValid(), path)
            # the windows never preselect it as an intravilan contour
            self.assertTrue(roles.is_report(again), path)
            self.assertEqual(again.renderer().type(), "categorizedSymbol", path)

    def test_a_shapefile_output_keeps_romanian_letters(self):
        from qgis.core import QgsProcessingOutputLayerDefinition, QgsVectorLayer  # type: ignore
        imobile, intravilan = self.intravilan_inputs()
        names = ["ȘOSEA ȚĂRANULUI", "Mărășești"]
        imobile.dataProvider().changeAttributeValues(
            {feature.id(): {0: name} for feature, name in zip(imobile.getFeatures(), names)})
        path = os.path.join(self.tmp, "raport.shp")
        # the Processing dialog passes the encoding of the Processing settings
        output = QgsProcessingOutputLayerDefinition(path)
        output.createOptions = {"fileEncoding": "windows-1252"}
        run("intravilanreport", {"IMOBILE": imobile, "INTRAVILAN": intravilan,
                                 "ID_FIELD": "cod", "OUTPUT": output})
        again = QgsVectorLayer(path, "raport", "ogr")
        self.assertEqual(sorted(f["cod"] for f in again.getFeatures()), sorted(names))
        self.assertEqual(sorted(f["situatie"] for f in again.getFeatures()),
                         [intravilan_service.INTEGRAL_EXTRA, intravilan_service.INTEGRAL_INTRA])

    def test_a_report_that_was_not_written_is_an_error_and_not_an_output(self):
        from cadex.processing_provider.base import CadexAlgorithm
        from cadex.services.tasks import CadexTask
        errors = []

        class Feedback(QgsProcessingFeedback):
            def reportError(self, error, fatalError=False):
                errors.append((error, fatalError))

        task = CadexTask("CADex: test")
        task.excel_path = os.path.join(self.tmp, "raport.xlsx")
        results = {}
        CadexAlgorithm.report_output(task, results, "OUTPUT_XLSX", Feedback())
        self.assertEqual(results, {"OUTPUT_XLSX": task.excel_path})
        task.report_error = "Fișierul 'raport.xlsx' nu poate fi scris."
        results = {}
        CadexAlgorithm.report_output(task, results, "OUTPUT_XLSX", Feedback())
        self.assertEqual(results, {})
        # the run goes on: its layers are written
        self.assertEqual(errors, [("Fișierul 'raport.xlsx' nu poate fi scris.", False)])

    def test_features_a_sink_refuses_are_counted_in_a_warning(self):
        from cadex.processing_provider.base import CadexAlgorithm
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        class Sink:
            def __init__(self):
                self.taken = 0

            def addFeature(self, _feature, _flags):
                self.taken += 1
                return self.taken == 1

            def lastError(self):
                return "String of length 28 exceeds maximum field length (10)"

        self.assertEqual(CadexAlgorithm.write(Sink(), ["a", "b", "c"], Feedback()), 1)
        self.assertEqual(warnings, ["2 elemente din 3 nu au putut fi scrise în rezultat. "
                                    "String of length 28 exceeds maximum field length (10)"])

    def test_the_help_panel_shows_an_algorithms_own_text_when_there_is_one(self):
        from unittest import mock
        from cadex import help_texts
        algorithm = QgsApplication.processingRegistry().algorithmById("cadex:intravilanreport")
        own = {"intravilanreport": "<p>Alegeți layerul <b>Imobile</b>.</p>"}
        with mock.patch.object(help_texts, "ALGORITHMS", own, create=True):
            text = algorithm.shortHelpString()
        self.assertTrue(text.startswith("<p>Alegeți layerul <strong>Imobile</strong>.</p>"), text)
        self.assertIn("Stereo 70", text)
        # the window's page describes controls Processing does not have
        self.assertNotIn("Doar imobilele selectate", text)

    def test_metres_labelled_wgs84_are_refused_before_a_run(self):
        imobile = helpers.polygon_layer("imobile geojson", [(square(0, 0, 20), ["1"])],
                                        fields="id:string", crs="EPSG:4326")
        with self.assertRaises(QgsProcessingException) as raised:
            run("topologycheck", {"IMOBILE": imobile, "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                  "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        self.assertIn("'imobile geojson'", str(raised.exception))
        self.assertIn("EPSG:4326", str(raised.exception))

    def test_a_layers_properties_are_read_on_the_main_thread_of_a_background_run(self):
        import threading
        import time
        from qgis.core import (  # type: ignore
            QgsProcessingAlgRunnerTask, QgsProcessingOutputNumber,
            QgsProcessingParameterFeatureSource,
        )
        from qgis.PyQt.QtCore import QCoreApplication  # type: ignore
        from cadex.processing_provider.base import SOURCE_POLYGON, CadexAlgorithm

        class Probe(CadexAlgorithm):
            NAME = "probe"
            TITLE = "Probe"
            GROUP = "analiza"
            TOOL = "intravilan_report"

            def initAlgorithm(self, config=None):
                self.addParameter(QgsProcessingParameterFeatureSource(
                    "INPUT", "Imobile", [SOURCE_POLYGON]))
                self.addOutput(QgsProcessingOutputNumber("COLUMNS", "Coloane"))

            def processAlgorithm(self, parameters, context, feedback):
                snapshot = self.snapshot(parameters, "INPUT", context, feedback)
                if snapshot.name != "Imobile conv" or snapshot.row_number_fields != ["fid"]:
                    raise QgsProcessingException(f"{snapshot.name} {snapshot.row_number_fields}")
                return {"COLUMNS": len(snapshot.cgxml_columns)}

        layer = helpers.gpkg_layer(helpers.polygon_layer(
            "Imobile conv", [(square(0, 0, 20), ["1"])], fields="id:string"),
            os.path.join(self.tmp, "conv.gpkg"))
        roles.mark_columns(layer, [("id", "id")])
        QgsProject.instance().addMapLayer(layer)
        main = threading.get_ident()
        threads = set()
        for name in ("customProperty", "fields", "name"):
            real = getattr(layer, name)
            setattr(layer, name, lambda *args, real=real: threads.add(threading.get_ident())
                    or real(*args))
        context = QgsProcessingContext()
        context.setProject(QgsProject.instance())
        feedback = QgsProcessingFeedback()
        done = []
        task = QgsProcessingAlgRunnerTask(Probe(), {"INPUT": layer.id()}, context, feedback)
        task.executed.connect(lambda ok, results: done.append((ok, dict(results))))
        QgsApplication.taskManager().addTask(task)
        deadline = time.monotonic() + 60
        while not done and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(done, [(True, {"COLUMNS": 1})])
        self.assertEqual(threads, {main})


class TopologyProcessingTests(unittest.TestCase):
    """Verificare Topologică and Topologie Construcții with their parameters left at the
    defaults, on layers from Conversie CGXML and on plain layers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_proc_topo_")
        self.messages = []

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def feedback(self):
        messages = self.messages

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                messages.append(("warning", message))

            def reportError(self, message, fatalError=False):
                messages.append(("error", message))
        return Feedback()

    def warnings(self):
        return [text for kind, text in self.messages if kind == "warning"]

    def layers(self, converted=True):
        """Imobile 1 and 2 in sector 5 under a road of sector 0 that reaches 10 cm into
        both; construcția C1 belongs to imobil 1 but stands mostly on imobil 2."""
        imobile = helpers.polygon_layer("Imobile livrare", [
            (square(0, 0, 20), ["1", 5]),
            (moved("POLYGON((20 0, 40 0, 40 20, 20 20, 20 0))"), ["2", 5]),
            (moved("POLYGON((0 19.9, 40 19.9, 40 25, 0 25, 0 19.9))"), ["3", 0]),
        ], fields="id:string,sector:integer")
        constructii = helpers.polygon_layer("Construcții livrare", [
            (moved("POLYGON((18 5, 26 5, 26 10, 18 10, 18 5))"), ["1", "C1"])],
            fields="id:string,nr_constr:string")
        if converted:
            roles.mark_columns(imobile, [("id", "id"), ("sector", "sector")])
            roles.mark_columns(constructii, [("id", "id"), ("nr_constr", "nr_constr")])
        return imobile, constructii

    def check(self, parameters):
        return run("topologycheck", dict({"OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                          "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, **parameters),
                   self.feedback())

    @staticmethod
    def rows(result):
        return sorted((f["tip"], f["gravitate"], f["obiect"])
                      for key in ("OUTPUT_AREAS", "OUTPUT_POINTS")
                      for f in result[key].getFeatures())

    def test_converted_layers_are_checked_as_in_the_window(self):
        imobile, constructii = self.layers()
        result = self.check({"IMOBILE": imobile, "CONSTRUCTII": constructii})
        self.assertEqual(self.rows(result), [
            ("Construcție pe alt imobil", "Eroare", "construcție 1 C1"),
            ("Suprapunere între imobile", "Avertisment", "imobil 1"),
            ("Suprapunere între imobile", "Avertisment", "imobil 2")])
        self.assertEqual(self.warnings(), [])

    def test_converted_layers_are_cut_as_in_the_window(self):
        imobile, constructii = self.layers()
        result = run("buildingtopology", {"IMOBILE": imobile, "CONSTRUCTII": constructii,
                                          "OUTPUT": "TEMPORARY_OUTPUT"}, self.feedback())
        self.assertEqual([f["stare"] for f in result["OUTPUT"].getFeatures()], [bts.ELSEWHERE])
        # a plain imobile layer gives the building no imobil to link to: it is cut where it
        # stands
        plain, _constructii = self.layers(converted=False)
        result = run("buildingtopology", {"IMOBILE": plain, "CONSTRUCTII": constructii,
                                          "OUTPUT": "TEMPORARY_OUTPUT"}, self.feedback())
        self.assertEqual([f["stare"] for f in result["OUTPUT"].getFeatures()], [bts.LOSS])

    def test_a_building_link_without_the_imobil_column_is_refused(self):
        imobile, constructii = self.layers(converted=False)
        message = "Alegeți și coloana din imobile la care trimite 'Imobilul construcției'."
        with self.assertRaises(QgsProcessingException) as raised:
            run("buildingtopology", {"IMOBILE": imobile, "CONSTRUCTII": constructii,
                                     "LINK_FIELD": "id", "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertIn(message, str(raised.exception))
        with self.assertRaises(QgsProcessingException) as raised:
            self.check({"IMOBILE": imobile, "CONSTRUCTII": constructii, "LINK_FIELD": "id"})
        self.assertIn(message, str(raised.exception))
        # converted imobile record their ID column in cadex/columns
        converted, _constructii = self.layers()
        result = run("buildingtopology", {"IMOBILE": converted, "CONSTRUCTII": constructii,
                                          "LINK_FIELD": "id", "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertEqual([f["stare"] for f in result["OUTPUT"].getFeatures()], [bts.ELSEWHERE])

    def test_a_row_number_never_names_groups_or_bounds_imobile(self):
        # memory layers whose 'fid' column numbers the rows, as in a GeoPackage exported to a
        # Shapefile: a GeoPackage read by a run that refuses stays open until the process
        # ends, so tearDown could not remove it
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), [1, "1", 5]), (square(20, 0, 20), [2, "2", 5])],
            fields="fid:integer,id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [
            (moved("POLYGON((0 0, 40 0, 40 20, 0 20, 0 0))"), [1, 5])],
            fields="fid:integer,nr:integer")
        for name, parameters in (
                ("topologycheck", {"ID_FIELD": "fid"}),
                ("topologycheck", {"SECTOR_FIELD": "fid"}),
                ("topologycheck", {"LIMITS": limits, "LIMIT_FIELD": "fid"}),
                ("topologycheckcgxml", {"LIMITS": limits, "LIMIT_FIELD": "fid"})):
            base = ({"INPUT": [helpers.DATA_DIR], "RECURSIVE": False}
                    if name == "topologycheckcgxml" else {"IMOBILE": imobile})
            with self.assertRaises(QgsProcessingException) as raised:
                run(name, dict(base, OUTPUT_AREAS="TEMPORARY_OUTPUT",
                               OUTPUT_POINTS="TEMPORARY_OUTPUT", **parameters))
            self.assertIn("numerotează rândurile", str(raised.exception), parameters)
        result = self.check({"IMOBILE": imobile, "ID_FIELD": "id", "SECTOR_FIELD": "sector",
                             "LIMITS": limits, "LIMIT_FIELD": "nr"})
        self.assertEqual(result["PROBLEMS"], 0)

    def test_the_roads_sector_without_a_sector_column_is_a_warning(self):
        imobile, _constructii = self.layers(converted=False)
        self.check({"IMOBILE": imobile, "ID_FIELD": "id"})
        self.assertEqual(len(self.warnings()), 1)
        self.assertIn("'Sector cadastral'", self.warnings()[0])
        self.messages.clear()
        self.check({"IMOBILE": imobile, "ID_FIELD": "id", "SECTOR_FIELD": "sector"})
        self.check({"IMOBILE": imobile, "ID_FIELD": "id", "ROADS_SECTOR": ""})
        self.assertEqual(self.warnings(), [])

    def test_a_snapping_tolerance_not_below_the_snapping_distance_is_refused(self):
        imobile, _constructii = self.layers()
        with self.assertRaises(QgsProcessingException) as raised:
            self.check({"IMOBILE": imobile, "SNAP_PRECISION": 0.1, "SNAP_DISTANCE": 0.05})
        self.assertIn("Toleranța de racordare trebuie să fie mai mică", str(raised.exception))

    def test_records_without_coordinates_are_a_warning(self):
        # bom_30003 has no coordinates
        run("topologycheckcgxml", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                   "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                   "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, self.feedback())
        self.assertEqual(self.warnings(),
                         ["1 înregistrare fără coordonate nu a fost verificată."])

    def test_the_report_names_the_tolerances_it_was_made_with(self):
        imobile, constructii = self.layers()
        report = os.path.join(self.tmp, "raport.xlsx")
        result = self.check({"IMOBILE": imobile, "CONSTRUCTII": constructii,
                             "SNAP_DISTANCE": 0.3, "OUTPUT_XLSX": report})
        self.assertEqual(result["OUTPUT_XLSX"], report)
        _names, rows = xlsx.read_rows(report, "Sumar")
        facts = {row[0]: row[1] for row in rows if len(row) > 1}
        self.assertEqual(facts["Layer de imobile"], "Imobile livrare")
        self.assertEqual(facts["Layer de construcții"], "Construcții livrare")
        self.assertEqual(facts["Distanță de racordare"], "0,3 m")
        self.assertEqual(facts["Sectorul drumurilor"], "0")

    def test_a_report_that_cannot_be_written_leaves_the_layers_written(self):
        imobile, _constructii = self.layers()
        report = os.path.join(self.tmp, "raport.xlsx")
        # nobody can open a folder for writing, as nobody can write a workbook open in Excel
        os.makedirs(report)
        result = self.check({"IMOBILE": imobile, "OUTPUT_XLSX": report})
        self.assertNotIn("OUTPUT_XLSX", result)
        self.assertEqual(result["OUTPUT_AREAS"].featureCount(), 2)
        self.assertEqual(len([m for m in self.messages if m[0] == "error"]), 1)
        self.assertIn("Fișierul 'raport.xlsx' nu poate fi scris", self.messages[-1][1])

    def test_the_same_imobil_in_two_files_is_a_warning_not_an_overlap(self):
        folder = os.path.join(self.tmp, "livrare")
        for sub in (folder, os.path.join(folder, "vechi")):
            synthetic.write(sub, "501.cgxml", Land(rings=[square_ring(0, 100)],
                                                   cadgenno="501", sector="3"))
        result = run("topologycheckcgxml", {"INPUT": [folder],
                                            "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                            "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, self.feedback())
        self.assertEqual(result["PROBLEMS"], 0)
        self.assertEqual(len(self.warnings()), 1)
        self.assertIn("501", self.warnings()[0])
        self.assertIn("mai multe fișiere", self.warnings()[0])

    def test_an_outline_sector_no_imobil_carries_is_a_warning(self):
        imobile, _constructii = self.layers()
        limits = helpers.polygon_layer("Sectoare", [(square(0, 0, 40), ["S5"])],
                                       fields="nr:string")
        self.check({"IMOBILE": imobile, "LIMITS": limits, "LIMIT_FIELD": "nr"})
        self.assertEqual(self.warnings(), ["Sectorul S5 din limite nu apare la niciun imobil."])

    def test_a_finding_the_output_cannot_hold_is_a_warning(self):
        from unittest import mock
        from qgis.core import Qgis, QgsFeature  # type: ignore
        from cadex.services import topology_check_service as tcs
        real = tcs.findings_features

        def with_a_line(findings, fields, wkb):
            features = real(findings, fields, wkb)
            if wkb == Qgis.WkbType.Point:
                line = QgsFeature(fields)
                line.setGeometry(QgsGeometry.fromWkt(moved("LINESTRING(0 0, 1 1)")))
                features.append(line)
            return features
        imobile, _constructii = self.layers()
        with mock.patch.object(tcs, "findings_features", with_a_line):
            self.check({"IMOBILE": imobile})
        self.assertEqual(len(self.warnings()), 1)
        self.assertIn("1 element din 1 nu a putut fi scris în rezultat.", self.warnings()[0])


class AnalysisAlgorithmTests(unittest.TestCase):
    """Raport Intravilan / Extravilan and Diminuare Suprafețe in Processing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_analysis_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def csv(self, text):
        path = os.path.join(self.tmp, "parcele.csv")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write(text)
        return path

    def intravilan(self):
        return helpers.polygon_layer("Intravilan", [(square(-10, -10, 1000), [1])],
                                     fields="nume:string")

    def test_a_layer_of_several_contours_is_refused_unless_some_are_selected(self):
        from qgis.core import QgsProcessingFeatureSourceDefinition  # type: ignore
        table = self.csv("nr,suprafata_act\n1,4000\n2,6000\n")
        contour = helpers.polygon_layer("Contur tarlale", [(square(0, 0, 100), [1]),
                                                           (square(200, 0, 100), [2]),
                                                           (square(400, 0, 100), [3])])
        with self.assertRaises(QgsProcessingException) as raised:
            run("areareduction", {"TABLE": table, "AREA_FIELD": "suprafata_act",
                                  "CONTOUR": contour, "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertIn("'Contur tarlale' are 3 elemente", str(raised.exception))
        QgsProject.instance().addMapLayer(contour)
        contour.selectByIds([next(contour.getFeatures()).id()])
        result = run("areareduction", {
            "TABLE": table, "AREA_FIELD": "suprafata_act", "OUTPUT": "TEMPORARY_OUTPUT",
            "CONTOUR": QgsProcessingFeatureSourceDefinition(contour.id(), True)})
        self.assertAlmostEqual(result["FACTOR"], 1.0)

    def test_a_total_row_under_the_parcels_is_left_out(self):
        table = self.csv("nr,proprietar,suprafata_act\n1,A,10000\n2,B,20000\n3,C,15000\n"
                         "TOTAL,,45000\n")
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        result = run("areareduction", {"TABLE": table, "AREA_FIELD": "suprafata_act",
                                       "CONTOUR_AREA": 43200, "OUTPUT": "TEMPORARY_OUTPUT"},
                     Feedback())
        areas = {f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()}
        self.assertEqual([areas[nr] for nr in ("1", "2", "3")], [9600, 19200, 14400])
        self.assertTrue(any("rând de total" in message for message in warnings), warnings)

    def test_a_row_number_is_refused_where_a_column_of_data_is_wanted(self):
        imobile = helpers.gpkg_layer(helpers.polygon_layer(
            "Imobile", [(square(0, 0, 100), ["A"])], fields="cod:string"),
            os.path.join(self.tmp, "imobile.gpkg"))
        for field in ("ID_FIELD", "OWNER_FIELD"):
            with self.assertRaises(QgsProcessingException) as raised:
                run("intravilanreport", {"IMOBILE": imobile, "INTRAVILAN": self.intravilan(),
                                         field: "fid", "OUTPUT": "TEMPORARY_OUTPUT"})
            self.assertIn("'fid' din 'Imobile' doar numerotează rândurile",
                          str(raised.exception), field)
        table = helpers.gpkg_layer(helpers.polygon_layer(
            "Parcele", [(square(0, 0, 10), [6000, "50124"]), (square(20, 0, 10), [4000, None])],
            fields="suprafata_act:integer,ie:string"), os.path.join(self.tmp, "parcele.gpkg"))
        for field in ("AREA_FIELD", "FIXED_FIELD"):
            parameters = {"TABLE": table, "AREA_FIELD": "suprafata_act", "CONTOUR_AREA": 9000,
                          "OUTPUT": "TEMPORARY_OUTPUT", field: "fid"}
            with self.assertRaises(QgsProcessingException) as raised:
                run("areareduction", parameters)
            self.assertIn("'fid' din 'Parcele' doar numerotează rândurile",
                          str(raised.exception), field)

    def test_a_report_that_cannot_be_written_is_an_error_and_the_layer_stays(self):
        errors = []

        class Feedback(QgsProcessingFeedback):
            def reportError(self, error, fatalError=False):
                errors.append(error)

        # nobody can open a folder for writing, just as nobody can write a workbook open in
        # Excel
        excel = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(excel)
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["A"])],
                                        fields="cod:string")
        runs = (("intravilanreport", {"IMOBILE": imobile, "INTRAVILAN": self.intravilan()}),
                ("areareduction", {"TABLE": self.csv("nr,suprafata_act\n1,4000\n2,6000\n"),
                                   "AREA_FIELD": "suprafata_act", "CONTOUR_AREA": 9000}))
        for name, parameters in runs:
            errors.clear()
            result = run(name, dict(parameters, OUTPUT="TEMPORARY_OUTPUT", OUTPUT_XLSX=excel),
                         Feedback())
            self.assertGreater(result["OUTPUT"].featureCount(), 0, name)
            self.assertNotIn("OUTPUT_XLSX", result, name)
            self.assertEqual(len(errors), 1, name)
            self.assertTrue(errors[0].startswith("Fișierul 'raport.xlsx' nu poate fi scris"),
                            errors[0])

    def test_every_imobil_reaches_the_output_whatever_its_repaired_geometry(self):
        spike = moved("POLYGON((0 0, 10 0, 10 10, 5 10, 5 20, 5 10, 0 10, 0 0))")
        imobile = helpers.polygon_layer(
            "Imobile", [(spike, ["1"]), (square(30, 0, 10), ["2"]), (square(60, 0, 10), ["3"])],
            fields="cod:string", geometry="MultiPolygon")
        result = run("intravilanreport", {"IMOBILE": imobile, "INTRAVILAN": self.intravilan(),
                                          "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertEqual(result["INTRAVILAN_COUNT"], 3)
        self.assertEqual(sorted(f["cod"] for f in result["OUTPUT"].getFeatures()),
                         ["1", "2", "3"])

    def test_the_sumar_names_the_table_and_the_contour(self):
        table = helpers.polygon_layer("Parcele tarla 12", [(square(0, 0, 10), [6000]),
                                                           (square(20, 0, 10), [4000])],
                                      fields="suprafata_act:integer")
        contour = helpers.polygon_layer("Contur tarla 12", [(square(0, 0, 100), [1])])
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        run("areareduction", {"TABLE": table, "AREA_FIELD": "suprafata_act", "CONTOUR": contour,
                              "OUTPUT": "TEMPORARY_OUTPUT", "OUTPUT_XLSX": excel})
        _names, rows = xlsx.read_rows(excel, "Sumar")
        facts = {row[0]: row[1] for row in rows if row and row[0]}
        self.assertEqual(facts["Tabel"], "Parcele tarla 12")
        self.assertEqual(facts["Contur"], "Contur tarla 12, tot layerul (1 element)")

    def test_a_row_number_is_refused_also_when_only_selected_imobile_are_used(self):
        from qgis.core import QgsProcessingFeatureSourceDefinition  # type: ignore
        imobile = helpers.gpkg_layer(helpers.polygon_layer(
            "Imobile", [(square(0, 0, 100), ["A"])], fields="cod:string"),
            os.path.join(self.tmp, "imobile.gpkg"))
        QgsProject.instance().addMapLayer(imobile)
        imobile.selectAll()
        with self.assertRaises(QgsProcessingException) as raised:
            run("intravilanreport", {
                "IMOBILE": QgsProcessingFeatureSourceDefinition(imobile.id(), True),
                "INTRAVILAN": self.intravilan(), "ID_FIELD": "fid",
                "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertIn("'fid' din 'Imobile' doar numerotează rândurile", str(raised.exception))

    def test_an_empty_selection_of_the_contour_is_named_as_such(self):
        from qgis.core import QgsProcessingFeatureSourceDefinition  # type: ignore
        contour = helpers.polygon_layer("Contur tarlale", [(square(0, 0, 100), [1]),
                                                           (square(200, 0, 100), [2])])
        QgsProject.instance().addMapLayer(contour)
        with self.assertRaises(QgsProcessingException) as raised:
            run("areareduction", {
                "TABLE": self.csv("nr,suprafata_act\n1,4000\n2,6000\n"),
                "AREA_FIELD": "suprafata_act", "OUTPUT": "TEMPORARY_OUTPUT",
                "CONTOUR": QgsProcessingFeatureSourceDefinition(contour.id(), True)})
        self.assertIn("Nu este selectat niciun element în layerul 'Contur tarlale'",
                      str(raised.exception))

    def test_a_self_intersecting_contour_gives_its_area_as_in_the_window(self):
        # a square with a narrow band crossing its west edge: invalid, with a clear area
        contour = helpers.polygon_layer("Contur tarla 9", [(moved(
            "POLYGON((0 0, 100 0, 100 100, 0 100, 0 50, 110 50, 110 40, 0 40, 0 0))"), [1])])
        geometry = next(contour.getFeatures()).geometry()
        self.assertFalse(geometry.isGeosValid())
        # the layer is in Stereo 70 already, so this is the area the window takes
        area = geometry.area()
        result = run("areareduction", {"TABLE": self.csv("nr,suprafata_act\n1,6000\n2,4000\n"),
                                       "AREA_FIELD": "suprafata_act", "CONTOUR": contour,
                                       "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertAlmostEqual(result["FACTOR"], area / 10000)

    def test_a_negative_area_is_named_as_such(self):
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        result = run("areareduction", {
            "TABLE": self.csv("nr,suprafata_act\n1,-100\n2,6000\n3,4000\n"),
            "AREA_FIELD": "suprafata_act", "CONTOUR_AREA": 9000, "OUTPUT": "TEMPORARY_OUTPUT"},
            Feedback())
        self.assertAlmostEqual(result["FACTOR"], 0.9)
        about_areas = [m for m in warnings if "suprafa" in m]
        self.assertEqual(len(about_areas), 1, warnings)
        self.assertIn("are suprafața negativă și rămâne neschimbat", about_areas[0])

    def test_rows_hidden_in_excel_are_left_out_as_in_the_window(self):
        import zipfile
        path = os.path.join(self.tmp, "parcele.xlsx")
        wb = xlsx.Workbook()
        sheet = wb.add_sheet("Parcele")
        for r, values in enumerate([["nr", "suprafata_act"], [1, 1000], [2, 2000], [3, 3000]],
                                   start=1):
            for c, value in enumerate(values, start=1):
                sheet.set(r, c, value)
        wb.save(path)
        # a filter hides row 3, the way Excel saves it
        with zipfile.ZipFile(path) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="3"', b'<row r="3" hidden="1"')
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in parts.items():
                zf.writestr(name, data)
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

        excel = os.path.join(self.tmp, "diminuare.xlsx")
        result = run("areareduction", {"TABLE": path, "AREA_FIELD": "suprafata_act",
                                       "CONTOUR_AREA": 3600, "OUTPUT": "TEMPORARY_OUTPUT",
                                       "OUTPUT_XLSX": excel}, Feedback())
        self.assertEqual({f["nr"]: f["sup_diminuata"] for f in result["OUTPUT"].getFeatures()},
                         {1: 900, 3: 2700})
        self.assertIn("1 rând ascuns în Excel a fost lăsat deoparte: 3.", warnings)
        _names, rows = xlsx.read_rows(excel, "Sumar")
        facts = {row[0]: row[1] for row in rows if row and row[0]}
        self.assertEqual(facts["Rânduri ascunse în Excel, lăsate deoparte"], "1 rând: 3")


class CompareProcessingTests(unittest.TestCase):
    """The two Comparator algorithms."""

    class Feedback(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.warnings = []
            self.errors = []

        def pushWarning(self, message):
            self.warnings.append(message)

        def reportError(self, error, fatalError=False):
            self.errors.append((error, fatalError))

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_proc_")
        self.old = os.path.join(self.tmp, "vechi")
        self.new = os.path.join(self.tmp, "nou")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def deliveries(self, old=None, new=None):
        """1.cgxml in both folders, its boundary moved in the new one."""
        synthetic.write(self.old, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1",
                                                  **(old or {})))
        synthetic.write(self.new, "1.cgxml", Land(rings=[square_ring(0, 21)], cadgenno="1",
                                                  **(new or {})))

    @staticmethod
    def converted(name, rows, columns="ID_IMOB:integer,ie:integer,sector:integer"):
        """A Conversie CGXML imobile layer whose layout renamed the ID column."""
        layer = helpers.polygon_layer(name, [(square(20 * i, 0, 10), row)
                                             for i, row in enumerate(rows)], fields=columns)
        roles.mark_columns(layer, [("id", "ID_IMOB"), ("ie", "ie"), ("sector", "sector")])
        roles.mark_batch(layer, "20260101")
        return layer

    def test_a_report_that_cannot_be_written_keeps_the_comparison(self):
        self.deliveries()
        report = os.path.join(self.tmp, "lipsa", "raport.xlsx")
        feedback = self.Feedback()
        result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new], "OUTPUT_XLSX": report,
                                      "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"}, feedback)
        self.assertEqual(result["CHANGED"], 1)
        self.assertEqual(result["OUTPUT_CHANGED"].featureCount(), 1)
        self.assertNotIn("OUTPUT_XLSX", result)
        old = helpers.polygon_layer("v1", [(square(0, 0, 10), [1, "A"])],
                                    fields="id:integer,propr:string")
        new = helpers.polygon_layer("v2", [(square(0, 0, 10), [1, "B"])],
                                    fields="id:integer,propr:string")
        result = run("comparelayers", {"OLD": old, "NEW": new, "OUTPUT_XLSX": report}, feedback)
        self.assertEqual(result["CHANGED"], 1)
        self.assertNotIn("OUTPUT_XLSX", result)
        self.assertEqual(len(feedback.errors), 2)
        for error, fatal in feedback.errors:
            self.assertIn("'raport.xlsx' nu poate fi scris", error)
            self.assertFalse(fatal)

    def test_records_without_coordinates_are_named_in_a_warning(self):
        synthetic.write(self.old, "70001.cgxml", Land(rings=[], measured_area=400.0, ie="70001",
                                                      style="eterra"))
        synthetic.write(self.old, "70002.cgxml", Land(rings=[square_ring(100)], ie="70002",
                                                      style="eterra"))
        synthetic.write(self.new, "503.cgxml", Land(rings=[square_ring(100)], cadgenno="503",
                                                    ie="70002", sector="3"))
        feedback = self.Feedback()
        result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new],
                                      "MODE": cgxml_compare.MODES.index(cgxml_compare.IE),
                                      "OUTPUT_DELETED": "TEMPORARY_OUTPUT"}, feedback)
        self.assertEqual(result["ONLY_OLD"], 1)
        self.assertEqual(result["OUTPUT_DELETED"].featureCount(), 0)
        self.assertIn("Înregistrările fără coordonate nu apar în layerele rezultat: 1 la "
                      "'Imobile dispărute'.", feedback.warnings)

    def test_two_conversions_left_at_the_default_key_pair_as_in_the_window(self):
        v1 = self.converted("v1", [[1101, None, 3], [1102, None, 3]])
        v2 = self.converted("v2", [[1101, None, 3], [1102, None, 3], [1103, None, 3]])
        result = run("comparelayers", {"OLD": v1, "NEW": v2})
        self.assertEqual((result["ADDED"], result["DELETED"]), (1, 0))
        # the cadastral numbers of an eTerra export are not the IDs of a delivery
        eterra = self.converted("eterra", [[7339, 70001, None], [None, 70002, None]])
        livrare = self.converted("livrare", [[1101, 70001, 3], [1102, 70002, 3]])
        result = run("comparelayers", {"OLD": eterra, "NEW": livrare}, self.Feedback())
        self.assertEqual((result["ADDED"], result["DELETED"]), (0, 0))

    def test_a_column_the_new_layer_lacks_stops_the_run(self):
        old = helpers.polygon_layer("vechi", [(square(0, 0, 10), [1, "A", "x"])],
                                    fields="id:integer,propr:string,nota:string")
        new = helpers.polygon_layer("nou", [(square(0, 0, 10), [1, "A"])],
                                    fields="id:integer,propr:string")
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparelayers", {"OLD": old, "NEW": new, "FIELDS": ["propr", "nota"]})
        self.assertIn("Layerul 'nou' nu are coloana 'nota'.", str(raised.exception))

    def test_an_empty_source_writes_no_report(self):
        self.deliveries()
        empty = os.path.join(self.tmp, "gol")
        os.makedirs(empty)
        report = os.path.join(self.tmp, "raport.xlsx")
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparecgxml", {"OLD": [empty], "NEW": [self.new], "OUTPUT_XLSX": report})
        self.assertIn("sursa veche", str(raised.exception))
        self.assertFalse(os.path.exists(report))

    def test_the_boundary_alone_can_be_compared(self):
        self.deliveries(new={"owners": ("IONESCU ANA",)})
        result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new], "COLUMNS": [],
                                      "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual(next(result["OUTPUT_CHANGED"].getFeatures())["modificari"], "geometrie")
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparecgxml", {"OLD": [self.old], "NEW": [self.new], "COLUMNS": [],
                                 "COMPARE_GEOMETRY": False})
        self.assertIn("Alegeți cel puțin o coloană", str(raised.exception))

    def test_columns_are_chosen_by_their_key(self):
        self.deliveries(old={"plot": "12"}, new={"plot": "13", "owners": ("IONESCU ANA",)})
        result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new], "COLUMNS": ["propr"],
                                      "COMPARE_GEOMETRY": False,
                                      "OUTPUT_CHANGED": "TEMPORARY_OUTPUT"})
        self.assertEqual(next(result["OUTPUT_CHANGED"].getFeatures())["modificari"], "propr")

    def test_the_unchanged_constructions_are_counted(self):
        house = [Building(1, [square_ring(2, 5)])]
        self.deliveries(old={"buildings": house}, new={"buildings": house})
        result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new]})
        self.assertEqual(result["BUILDINGS_UNCHANGED"], 1)

    def test_the_construction_layers_are_created_by_default(self):
        algorithm = QgsApplication.processingRegistry().algorithmById("cadex:comparecgxml")
        for key in ("OUTPUT_ADDED", "OUTPUT_BUILDINGS_ADDED", "OUTPUT_BUILDINGS_DELETED",
                    "OUTPUT_BUILDINGS_CHANGED"):
            self.assertTrue(algorithm.parameterDefinition(key).createByDefault(), key)

    def test_each_side_keeps_its_own_geometry_type(self):
        from qgis.core import QgsFeature, QgsVectorLayer  # type: ignore
        table = QgsVectorLayer("None?field=id:integer&field=propr:string", "tabel", "memory")
        row = QgsFeature(table.fields())
        row.setAttributes([9, "A"])
        table.dataProvider().addFeatures([row])
        new = helpers.polygon_layer("nou", [(square(0, 0, 10), [1, "B"])],
                                    fields="id:integer,propr:string")
        result = run("comparelayers", {"OLD": table, "NEW": new,
                                       "OUTPUT_ADDED": "TEMPORARY_OUTPUT",
                                       "OUTPUT_DELETED": "TEMPORARY_OUTPUT"})
        self.assertTrue(next(result["OUTPUT_ADDED"].getFeatures()).hasGeometry())
        self.assertFalse(result["OUTPUT_DELETED"].isSpatial())

    def test_records_an_output_refuses_are_named_in_a_warning(self):
        from unittest import mock
        synthetic.write(self.old, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1"))
        for number in (1, 2, 3):
            synthetic.write(self.new, f"{number}.cgxml", Land(rings=[square_ring(100 * number)],
                                                              cadgenno=str(number)))
        real = cgxml_compare.result_features

        def refusing(*args, **kwargs):
            result = real(*args, **kwargs)
            if result.features:
                # the joined IDs of a split imobil in an integer column
                result.features[0].setAttribute("id", "4424; 4472")
            return result

        feedback = self.Feedback()
        with mock.patch.object(cgxml_compare, "result_features", refusing):
            result = run("comparecgxml", {"OLD": [self.old], "NEW": [self.new],
                                          "OUTPUT_ADDED": "TEMPORARY_OUTPUT"}, feedback)
        self.assertEqual(result["OUTPUT_ADDED"].featureCount(), 1)
        self.assertTrue(any(warning.startswith("1 element din 2 nu a putut fi scris")
                            for warning in feedback.warnings), feedback.warnings)


class ConversionProcessingTests(unittest.TestCase):
    """cgxmlconvert: what the window reports, the Shapefile rules, file outputs that cannot
    be written, damaged sources, a cancel and the DXF output."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_conv_proc_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.addCleanup(QgsProject.instance().clear)

    @staticmethod
    def feedback():
        """A feedback that keeps its messages: (warnings, errors as (text, fatal))."""
        warnings, errors = [], []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)

            def reportError(self, error, fatalError=False):
                errors.append((error, fatalError))
        return Feedback(), warnings, errors

    def save_layout(self, which, names, on=()):
        """Store the Coloane tab's layout for which with the given field names, as the window
        does; the keys in on are ticked as well."""
        from cadex.services import cgxml_columns, settings_service
        layout = cgxml_columns.default_layout(which)
        for column in layout:
            column["name"] = names.get(column["key"], column["name"])
            column["on"] = column["on"] or column["key"] in on
        key = f"cgxml/columns_{which}"
        settings_service.set_(key, cgxml_columns.layout_to_json(layout))
        self.addCleanup(settings_service.set_, key, "")

    def test_records_without_coordinates_are_reported_like_in_the_window(self):
        feedback, warnings, _errors = self.feedback()
        result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                      "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"}, feedback)
        self.assertEqual((result["IMOBILE"], result["OUTPUT_IMOBILE"].featureCount()), (4, 3))
        self.assertIn("1 imobil nu are coordonate în fișier și nu apare în rezultat.", warnings)

    @unittest.skipUnless(os.name == "nt", "drive letters are a Windows matter")
    def test_sources_on_two_drives_are_converted(self):
        other = "Y:" if os.path.splitdrive(self.tmp)[0].upper() == "Z:" else "Z:"
        result = run("cgxmlconvert", {
            "INPUT": [helpers.DATA_DIR, other + "\\stick\\livrare_v2.zip"], "RECURSIVE": False,
            "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"})
        self.assertEqual(result["FILES"], 4)

    def test_a_shapefile_destination_refuses_names_that_end_up_the_same(self):
        # both names are cut to 'observatii' in a Shapefile
        self.save_layout("imobile", {"desc_adresa": "observatii_adresa",
                                     "note": "observatii_teren"}, on=("desc_adresa",))
        shapefile = os.path.join(self.tmp, "imobile.shp")
        with self.assertRaises(QgsProcessingException) as raised:
            run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                 "OUTPUT_IMOBILE": shapefile})
        self.assertIn("Coloanele 'Descrierea adresei' și 'Note' ajung la același nume, "
                      "'observatii'", str(raised.exception))
        self.assertFalse(os.path.exists(shapefile))

    def test_a_shapefile_destination_gets_the_names_and_texts_cut_like_the_window(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        shapefile = os.path.join(self.tmp, "imobile.shp")
        self.save_layout("imobile", {"id": "identificator"})
        folder = os.path.join(self.tmp, "src")
        synthetic.write(folder, "501.cgxml", Land(rings=[square_ring(0)], cadgenno="501",
                                                  notes="ș" * 200))
        feedback, warnings, _errors = self.feedback()
        run("cgxmlconvert", {"INPUT": [folder], "OUTPUT_IMOBILE": shapefile}, feedback)
        again = QgsVectorLayer(shapefile, "imobile", "ogr")
        self.assertEqual(roles.column_of(again, "id"), "identifica")
        self.assertEqual([f["identifica"] for f in again.getFeatures()], [501])
        self.assertIn("1 valoare a fost scurtată la 254 de octeți, limita formatului Shapefile "
                      "(o literă cu diacritice ocupă doi octeți).", warnings)

    def test_a_geopackage_destination_refuses_the_names_it_keeps(self):
        self.save_layout("imobile", {"id": "FID"})
        with self.assertRaises(QgsProcessingException) as raised:
            run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                 "OUTPUT_IMOBILE": os.path.join(self.tmp, "imobile.gpkg")})
        self.assertIn("Numele 'FID' al coloanei 'ID imobil' este folosit de GeoPackage",
                      str(raised.exception))
        # a temporary layer has no such column
        result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                      "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"})
        self.assertIn("FID", result["OUTPUT_IMOBILE"].fields().names())

    def test_a_temporary_layer_or_geojson_takes_a_name_with_diacritics(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        # the window saves such a name after an Excel run, where it heads a column
        self.save_layout("imobile", {"id": "număr_imobil"})
        geojson = os.path.join(self.tmp, "imobile.geojson")
        for destination in ("TEMPORARY_OUTPUT", geojson):
            with self.subTest(destination=destination):
                result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                              "OUTPUT_IMOBILE": destination})
                layer = result["OUTPUT_IMOBILE"]
                if destination == geojson:
                    layer = QgsVectorLayer(geojson, "imobile", "ogr")
                self.assertEqual(layer.fields().names()[0], "număr_imobil")
                self.assertEqual(layer.featureCount(), 3)
        # a GeoPackage takes only letters without diacritics, digits and '_'
        with self.assertRaises(QgsProcessingException):
            run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                 "OUTPUT_IMOBILE": os.path.join(self.tmp, "imobile.gpkg")})

    def test_a_shapefile_output_keeps_romanian_letters(self):
        from qgis.core import QgsProcessingOutputLayerDefinition, QgsVectorLayer  # type: ignore
        folder = os.path.join(self.tmp, "src")
        note = "Șoseaua Mărășești, țarc"
        synthetic.write(folder, "501.cgxml", Land(rings=[square_ring(0)], cadgenno="501",
                                                  notes=note))
        path = os.path.join(self.tmp, "imobile.shp")
        # the Processing dialog passes the encoding of the Processing settings
        output = QgsProcessingOutputLayerDefinition(path)
        output.createOptions = {"fileEncoding": "windows-1252"}
        run("cgxmlconvert", {"INPUT": [folder], "OUTPUT_IMOBILE": output})
        self.assertEqual([f["note"] for f in QgsVectorLayer(path, "x", "ogr").getFeatures()],
                         [note])

    def test_a_canceled_conversion_stops_with_the_cancel_message(self):
        feedback = QgsProcessingFeedback()
        feedback.cancel()
        with self.assertRaises(QgsProcessingException) as raised:
            run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR],
                                 "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"}, feedback)
        self.assertIn("Rularea a fost anulată.", str(raised.exception))

    def test_an_excel_table_that_cannot_be_written_leaves_the_layers(self):
        report = os.path.join(self.tmp, "conversie.xlsx")
        # nobody can open a folder for writing, as nobody can write a workbook Excel holds
        os.makedirs(report)
        feedback, _warnings, errors = self.feedback()
        result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                      "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT",
                                      "OUTPUT_XLSX": report}, feedback)
        self.assertEqual(result["OUTPUT_IMOBILE"].featureCount(), 3)
        self.assertNotIn("OUTPUT_XLSX", result)
        [(error, fatal)] = errors
        self.assertTrue(error.startswith("Fișierul 'conversie.xlsx' nu poate fi scris"), error)
        self.assertFalse(fatal)

    def test_a_damaged_archive_among_the_sources_is_reported_and_the_rest_converted(self):
        damaged = os.path.join(self.tmp, "descarcare.zip")
        with open(damaged, "wb") as fp:
            fp.write(b"PK\x03\x04 o descarcare intrerupta")
        feedback, _warnings, errors = self.feedback()
        result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR, damaged], "RECURSIVE": False,
                                      "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"}, feedback)
        self.assertEqual((result["FILES"], result["UNREAD"], result["IMOBILE"]), (4, 1, 4))
        self.assertIn(("descarcare.zip: arhiva ZIP este deteriorată sau incompletă", False),
                      errors)
        # with no readable file there is nothing to convert
        with self.assertRaises(QgsProcessingException) as raised:
            run("cgxmlconvert", {"INPUT": [damaged], "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT"})
        self.assertIn("Niciun fișier nu a putut fi citit", str(raised.exception))

    @unittest.skipUnless(__import__("importlib").util.find_spec("ezdxf"), "ezdxf missing")
    def test_dxf_drawing_with_labels(self):
        import ezdxf
        path = os.path.join(self.tmp, "conversie.dxf")
        result = run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                                      "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT", "OUTPUT_DXF": path})
        self.assertEqual(result["OUTPUT_DXF"], path)
        msp = ezdxf.readfile(path).modelspace()
        # 3 imobile, one of them with a hole, plus the construcție
        self.assertEqual(len([e for e in msp if e.dxftype() == "LWPOLYLINE"]), 5)
        texts = [e.dxf.text for e in msp if e.dxftype() == "TEXT"]
        self.assertIn("ID 10188", texts)
        self.assertIn("IE 100001", texts)

    def test_dxf_without_ezdxf_is_refused_before_reading(self):
        from unittest import mock
        from cadex.services import deps
        with mock.patch.object(deps, "is_installed", lambda _module: False), \
                self.assertRaises(QgsProcessingException) as raised:
            run("cgxmlconvert", {"INPUT": [helpers.DATA_DIR], "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT",
                                 "OUTPUT_DXF": os.path.join(self.tmp, "conversie.dxf")})
        self.assertIn("'ezdxf'", str(raised.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "conversie.dxf")))


class UnreadableSourceProcessingTests(unittest.TestCase):
    """CGXML sources that hold a damaged archive."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_proc_unread_")
        self.damaged = os.path.join(self.tmp, "livrare.zip")
        with open(self.damaged, "wb") as fp:
            fp.write(b"nu este o arhiva")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_a_topology_check_of_a_damaged_archive_is_refused_with_the_reason(self):
        with self.assertRaises(QgsProcessingException) as raised:
            run("topologycheckcgxml", {"INPUT": [self.damaged],
                                       "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        self.assertIn("'livrare.zip' (arhiva ZIP este deteriorată sau incompletă)",
                      str(raised.exception))

    def test_a_comparison_with_a_damaged_archive_on_one_side_is_refused(self):
        new = os.path.join(self.tmp, "nou")
        synthetic.write(new, "1.cgxml", Land(rings=[square_ring(0)], cadgenno="1"))
        with self.assertRaises(QgsProcessingException) as raised:
            run("comparecgxml", {"OLD": [self.damaged], "NEW": [new],
                                 "OUTPUT_ADDED": "TEMPORARY_OUTPUT"})
        self.assertIn("Sursa veche nu poate fi citită în întregime: 'livrare.zip'",
                      str(raised.exception))

    def test_a_topology_check_where_no_file_can_be_read_is_refused(self):
        folder = os.path.join(self.tmp, "stricate")
        os.makedirs(folder)
        for name in ("501.cgxml", "502.cgxml"):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as fp:
                fp.write("<CGXML><Land>")
        with self.assertRaises(QgsProcessingException) as raised:
            run("topologycheckcgxml", {"INPUT": [folder], "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT"})
        message = str(raised.exception)
        self.assertIn("Niciun fișier nu a putut fi citit", message)
        self.assertIn("501.cgxml", message)

    def test_a_topology_check_where_no_file_can_be_read_leaves_the_earlier_report(self):
        folder = os.path.join(self.tmp, "stricate")
        os.makedirs(folder)
        with open(os.path.join(folder, "501.cgxml"), "w", encoding="utf-8") as fp:
            fp.write("<CGXML><Land>")
        report = os.path.join(self.tmp, "raport.xlsx")
        with open(report, "wb") as fp:
            fp.write(b"raportul de ieri")
        with self.assertRaises(QgsProcessingException):
            run("topologycheckcgxml", {"INPUT": [folder], "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_POINTS": "TEMPORARY_OUTPUT",
                                       "OUTPUT_XLSX": report})
        with open(report, "rb") as fp:
            self.assertEqual(fp.read(), b"raportul de ieri")

    def test_a_damaged_file_beside_a_file_without_coordinates_is_an_error_of_the_run(self):
        folder = os.path.join(self.tmp, "eterra")
        synthetic.write(folder, "70001.cgxml", Land(rings=[], ie="70001", style="eterra"))
        with open(os.path.join(folder, "70002.cgxml"), "w", encoding="utf-8") as fp:
            fp.write("<CGXML><Land>")
        errors = []

        class Feedback(QgsProcessingFeedback):
            def reportError(self, error, fatalError=False):
                errors.append(error)

        result = run("topologycheckcgxml", {"INPUT": [folder], "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                            "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, Feedback())
        self.assertEqual(result["PROBLEMS"], 0)
        self.assertTrue(any(error.startswith("70002.cgxml:") for error in errors), errors)


class HelpTextProcessingTests(unittest.TestCase):
    """The help panel of each algorithm in the Processing Toolbox."""

    @staticmethod
    def labels(definition):
        """A parameter's label as the help may name it: whole, or without the explanation
        after a colon or in brackets ('Coloana CF/IE (parcelele cu valoare...)' is 'Coloana
        CF/IE')."""
        text = definition.description()
        short = text
        for mark in (" (", ":"):
            short = short.split(mark)[0]
        return {f"<strong>{text}</strong>", f"<strong>{short.strip()}</strong>"}

    def test_each_algorithm_names_every_parameter_of_its_panel(self):
        for algorithm in PROVIDER.algorithms():
            text = algorithm.shortHelpString()
            for definition in algorithm.parameterDefinitions():
                self.assertTrue(any(label in text for label in self.labels(definition)),
                                f"{algorithm.id()}, {definition.description()}")

    def test_the_panel_names_the_selection_option_and_the_header_row_processing_uses(self):
        texts = {algorithm.id(): algorithm.shortHelpString()
                 for algorithm in PROVIDER.algorithms()}
        # QGIS's own option of a layer parameter limits a run to the selected features
        for name in ("cadex:intravilanreport", "cadex:areareduction"):
            self.assertIn("'Doar entitățile selectate'", texts[name], name)
        # OGR reads the table with its first row as the column names
        self.assertIn("numele coloanelor pe primul rând", texts["cadex:areareduction"])

    def test_the_paragraph_on_coordinate_systems_is_shown_where_a_layer_is_read(self):
        from cadex import help_texts
        # these two read only CGXML files, whose coordinates are always in Stereo 70
        cgxml_only = {"cadex:cgxmlconvert", "cadex:comparecgxml"}
        paragraph = " ".join(help_texts.STEREO70.split())
        for algorithm in PROVIDER.algorithms():
            self.assertEqual(paragraph in algorithm.shortHelpString(),
                             algorithm.id() not in cgxml_only, algorithm.id())


class TopologyReusedIdProcessingTests(unittest.TestCase):
    """Verificare Topologică (fișiere CGXML) on two imobile 1 km apart with one ID."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_proc_reused_")
        for name, x in (("501.cgxml", 0), ("505.cgxml", 1000)):
            synthetic.write(self.tmp, name, Land(rings=[square_ring(x, 100)], cadgenno="501",
                                                 sector="3"))

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_both_are_checked_and_named_with_their_files(self):
        messages = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                messages.append(message)

            def pushInfo(self, info):
                messages.append(info)

        result = run("topologycheckcgxml", {"INPUT": [self.tmp],
                                            "OUTPUT_AREAS": "TEMPORARY_OUTPUT",
                                            "OUTPUT_POINTS": "TEMPORARY_OUTPUT"}, Feedback())
        self.assertEqual(result["PROBLEMS"], 0)
        self.assertIn("1 imobil apare în mai multe fișiere, în locuri diferite: 501. Fiecare "
                      "fișier s-a verificat ca imobil separat.", messages)
        self.assertIn("Imobilul 501 apare în locuri diferite, în fișierele: 501.cgxml, "
                      "505.cgxml. Fiecare fișier s-a verificat ca imobil separat.", messages)


class DiminuareProcessingTableTests(unittest.TestCase):
    """Diminuare Suprafețe in Processing on a table and a contour read from files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_dim_table_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    def test_a_parcel_without_cf_ie_has_an_empty_cf_ie_cell_in_the_excel(self):
        table = helpers.gpkg_layer(helpers.polygon_layer(
            "Parcele", [(square(0, 0, 10), [6000, "50124"]), (square(20, 0, 10), [4000, None])],
            fields="suprafata_act:integer,ie:string"), os.path.join(self.tmp, "parcele.gpkg"))
        excel = os.path.join(self.tmp, "diminuare.xlsx")
        run("areareduction", {"TABLE": table, "AREA_FIELD": "suprafata_act",
                              "FIXED_FIELD": "ie", "CONTOUR_AREA": 9000,
                              "OUTPUT": "TEMPORARY_OUTPUT", "OUTPUT_XLSX": excel})
        _names, rows = xlsx.read_rows(excel, "Diminuare")
        column = rows[0].index("ie")
        self.assertEqual([row[column] for row in rows[1:]], ["50124", None])

    def test_a_contour_without_area_is_named_as_such(self):
        table = os.path.join(self.tmp, "parcele.csv")
        with open(table, "w", encoding="utf-8") as fp:
            fp.write("nr,suprafata_act\n1,4000\n2,6000\n")
        # three points on one line
        flat = helpers.polygon_layer("Contur gol", [(moved("POLYGON((0 0, 10 0, 20 0, 0 0))"),
                                                     [1])])
        with self.assertRaises(QgsProcessingException) as raised:
            run("areareduction", {"TABLE": table, "AREA_FIELD": "suprafata_act",
                                  "CONTOUR": flat, "OUTPUT": "TEMPORARY_OUTPUT"})
        self.assertIn("'Contur gol' are suprafața 0", str(raised.exception))


class IntravilanProcessingFileTests(unittest.TestCase):
    """Raport Intravilan saved to a file by Processing and not added to the project."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_intra_file_")

    def tearDown(self):
        QgsProject.instance().clear()
        helpers.remove_folder(self.tmp)

    @staticmethod
    def parameters(path):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 100), ["A"]),
                                                    (square(300, 0, 100), ["B"])],
                                        fields="cod:string")
        intravilan = helpers.polygon_layer("Intravilan", [(square(-10, -10, 150), [1])],
                                           fields="nume:string")
        return {"IMOBILE": imobile, "INTRAVILAN": intravilan, "ID_FIELD": "cod",
                "OUTPUT": path}

    def test_a_report_saved_without_opening_it_is_still_a_report_in_another_project(self):
        from qgis.core import QgsVectorLayer  # type: ignore
        for path in (os.path.join(self.tmp, "Raport intravilan.gpkg"),
                     os.path.join(self.tmp, "raport_intravilan.shp")):
            run("intravilanreport", self.parameters(path))
            # next day, in a new project
            again = QgsVectorLayer(path, "Raport intravilan", "ogr")
            self.assertTrue(again.isValid(), path)
            # the windows never preselect it as an intravilan contour
            self.assertTrue(roles.is_report(again), path)
            self.assertEqual(again.renderer().type(), "categorizedSymbol", path)

    def test_a_report_saved_over_an_earlier_result_keeps_none_of_its_markers(self):
        from qgis.core import (  # type: ignore
            QgsCoordinateTransformContext, QgsVectorFileWriter, QgsVectorLayer,
        )
        from cadex.services import layer_style
        for name, driver in (("rezultat.gpkg", "GPKG"), ("rezultat.shp", "ESRI Shapefile")):
            path = os.path.join(self.tmp, name)
            parameters = self.parameters(path)
            # a conversion saved earlier at that path, its markers in the style kept with it
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = driver
            QgsVectorFileWriter.writeAsVectorFormatV3(
                parameters["IMOBILE"], path, QgsCoordinateTransformContext(), options)
            earlier = QgsVectorLayer(path, "earlier", "ogr")
            roles.mark_batch(earlier, roles.new_batch())
            roles.mark_columns(earlier, [("id", "cod")])
            layer_style.store_with_file(earlier)
            del earlier
            run("intravilanreport", parameters)
            # next day, in a new project
            again = QgsVectorLayer(path, "again", "ogr")
            self.assertTrue(roles.is_report(again), name)
            self.assertEqual(roles.batch_of(again), "", name)
            self.assertEqual(roles.columns_of(again), {}, name)

    def test_a_report_opened_in_the_project_counts_its_imobile_without_a_gdal_error(self):
        from osgeo import gdal  # type: ignore
        path = os.path.join(self.tmp, "raport.gpkg")
        failures = []

        def handler(error_class, _number, message):
            if error_class >= gdal.CE_Failure:
                failures.append(message)
        gdal.PushErrorHandler(handler)
        try:
            processing.runAndLoadResults("cadex:intravilanreport", self.parameters(path))
            [layer] = [layer for layer in QgsProject.instance().mapLayers().values()
                       if os.path.normcase(layer.source().split("|")[0])
                       == os.path.normcase(path)]
            count = layer.featureCount()
        finally:
            gdal.PopErrorHandler()
        self.assertEqual(failures, [])
        self.assertEqual(count, 2)
        self.assertTrue(roles.is_report(layer))
        self.assertEqual(layer.renderer().type(), "categorizedSymbol")


class SirutaProcessingTests(unittest.TestCase):

    def test_a_conversion_without_the_siruta_database_completes_with_a_warning(self):
        import sqlite3
        from unittest import mock
        from cadex.services import siruta
        self.addCleanup(setattr, siruta, "_problem", "")
        self.addCleanup(setattr, siruta, "_tables", None)
        siruta._tables = None
        warnings = []

        class Feedback(QgsProcessingFeedback):
            def pushWarning(self, message):
                warnings.append(message)
                super().pushWarning(message)
        # as when the plugin folder denies access to its database
        refused = sqlite3.OperationalError("unable to open database file")
        with mock.patch.object(siruta.sqlite3, "connect", side_effect=refused):
            result = run("cgxmlconvert", {
                "INPUT": [helpers.DATA_DIR], "RECURSIVE": False,
                "OUTPUT_IMOBILE": "TEMPORARY_OUTPUT", "OUTPUT_CONSTRUCTII": "TEMPORARY_OUTPUT"},
                Feedback())
        self.assertEqual(result["IMOBILE"], 4)
        self.assertIn("Baza de date SIRUTA a pluginului nu a putut fi citită, deci denumirile "
                      "de UAT, județ și localitate lipsesc din rezultat.", warnings)


def square_ring(x, size=20):
    return synthetic.square(500000 + x, 400000, size)


def tearDownModule():
    QgsApplication.processingRegistry().removeProvider(PROVIDER)
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
