# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Conversie CGXML algorithm through processing.run: the format of each destination,
the outputs it writes, and its layers handed on to the other CADex algorithms."""

import os
import tempfile
import unittest
from unittest import mock

import helpers
import synthetic
from synthetic import Building, Land

helpers.start_qgis(gui=False)
helpers.start_processing()

from qgis.core import QgsProcessingException, QgsProject, QgsVectorLayer  # noqa: E402  # type: ignore
import processing  # noqa: E402  # type: ignore

from cadex.processing_provider import convert  # noqa: E402
from cadex.services import cgxml_columns, roles, settings_service  # noqa: E402

X, Y = 500000, 400000


def rect(x0, y0, x1, y1):
    return [(X + x0, Y + y0), (X + x1, Y + y0), (X + x1, Y + y1), (X + x0, Y + y1)]


class ConversionAlgorithmTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_conv_alg_")
        self.addCleanup(helpers.remove_folder, self.tmp)
        self.addCleanup(QgsProject.instance().clear)
        self.feedback = helpers.feedback()

    def source(self, name="livrare", **lands):
        """A folder with one file per land, named by its keyword."""
        folder = os.path.join(self.tmp, name)
        for file_name, land in lands.items():
            synthetic.write(folder, f"{file_name}.cgxml", land)
        return folder

    def convert(self, source, **outputs):
        outputs.setdefault("OUTPUT_IMOBILE", "TEMPORARY_OUTPUT")
        return processing.run("cadex:cgxmlconvert", dict(INPUT=[source], **outputs),
                              feedback=self.feedback)

    def rename_id_column(self, name):
        """Store the Coloane tab's layout with the ID column of the imobile named name."""
        layout = cgxml_columns.default_layout("imobile")
        layout[0]["name"] = name
        settings_service.set_("cgxml/columns_imobile", cgxml_columns.layout_to_json(layout))
        self.addCleanup(settings_service.set_, "cgxml/columns_imobile", "")


class DestinationFormatTests(ConversionAlgorithmTest):
    """The rules of Shapefile and GeoPackage apply to every destination QGIS writes in those
    formats, whatever its extension."""

    def test_the_format_is_the_one_qgis_writes_the_destination_in(self):
        folder = self.tmp.replace("\\", "/")
        for destination, fmt_key in (
                (f"{folder}/imobile.shp", "shp"), (f"{folder}/imobile.SHP", "shp"),
                (f"{folder}/imobile.shz", "shp"), (f"{folder}/imobile.dbf", "shp"),
                (f"{folder}/imobile.gpkg", "gpkg"),
                (f"ogr:dbname='{folder}/livrare.gpkg' table=\"imobile\" (geom)", "gpkg"),
                # QGIS writes a file of an unknown extension, or of none, as a GeoPackage
                (f"{folder}/imobile.shp.zip", "gpkg"), (f"{folder}/imobile", "gpkg"),
                (f"{folder}/imobile.geojson", ""), (f"{folder}/date.shp/imobile.geojson", ""),
                ("TEMPORARY_OUTPUT", ""), ("memory:", ""), ("", ""),
                ("postgis:dbname='gis' table=\"imobile\"", "")):
            with self.subTest(destination=destination):
                self.assertEqual(convert.destination_format(destination), fmt_key)

    def test_an_unknown_extension_is_a_geopackage_whatever_the_default_format_of_processing(self):
        folder = self.tmp.replace("\\", "/")
        with mock.patch("qgis.core.QgsProcessingUtils.defaultVectorExtension",
                        return_value="shp"):
            self.assertEqual(convert.destination_format(f"{folder}/imobile.date"), "gpkg")
            self.assertEqual(convert.destination_format(f"{folder}/imobile"), "gpkg")

    def test_a_shapefile_under_another_extension_follows_the_shapefile_rules(self):
        self.rename_id_column("identificator")
        source = self.source(a=Land(rings=[rect(0, 0, 20, 20)], cadgenno="501",
                                    notes="ș" * 200))
        result = self.convert(source, OUTPUT_IMOBILE=os.path.join(self.tmp, "imobile.shz"))
        layer = QgsVectorLayer(result["OUTPUT_IMOBILE"], "imobile", "ogr")
        self.assertTrue(layer.isValid(), result["OUTPUT_IMOBILE"])
        # the name is cut to the 10 characters of a Shapefile, and the marker names that field
        self.assertEqual(roles.column_of(layer, "id"), "identifica")
        self.assertEqual([feature["identifica"] for feature in layer.getFeatures()], [501])
        self.assertIn("1 valoare a fost scurtată la 254 de octeți, limita formatului Shapefile "
                      "(o literă cu diacritice ocupă doi octeți).", self.feedback.warnings)

    def test_a_file_of_an_unknown_extension_follows_the_geopackage_rules(self):
        self.rename_id_column("FID")
        source = self.source(a=Land(rings=[rect(0, 0, 20, 20)], cadgenno="501"))
        with self.assertRaises(QgsProcessingException) as raised:
            self.convert(source, OUTPUT_IMOBILE=os.path.join(self.tmp, "imobile.date"))
        self.assertIn("Numele 'FID' al coloanei 'ID imobil' este folosit de GeoPackage",
                      str(raised.exception))

    def test_shapefile_text_columns_hold_long_values_and_take_values_typed_in_later(self):
        number = "12-14-16-18-20-22-24-26-28-30 bis"  # longer than a nr. poștal usually is
        source = self.source(a=Land(rings=[rect(0, 0, 20, 20)], cadgenno="501", street="Mare",
                                    postal_number=number))
        result = self.convert(source, OUTPUT_IMOBILE=os.path.join(self.tmp, "imobile.shp"))
        [feature] = QgsVectorLayer(result["OUTPUT_IMOBILE"], "imobile", "ogr").getFeatures()
        self.assertEqual(feature["nr_postal"], number)
        # the file has no notes, title or old number; the owners are a short text
        typed = {"note": "ă" * 127, "propr": "ă" * 127, "nr_titlu": "12345/67 din 12.03.1995",
                 "cad_vechi": "1234/5", "categ_fol": "CC, A, V"}
        self.assertEqual(helpers.typed_in(result["OUTPUT_IMOBILE"], typed), typed)


class BuildingsOutputTests(ConversionAlgorithmTest):

    def test_without_a_construction_that_has_coordinates_no_construcții_output_is_written(self):
        source = self.source(a=Land(rings=[rect(0, 0, 20, 20)], cadgenno="501"))
        target = os.path.join(self.tmp, "constructii.gpkg")
        result = self.convert(source, OUTPUT_CONSTRUCTII=target)
        self.assertNotIn("OUTPUT_CONSTRUCTII", result)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(result["OUTPUT_IMOBILE"].featureCount(), 1)
        self.assertIn("Sursa nu are construcții cu coordonate, deci nu s-a scris un layer de "
                      "construcții.", self.feedback.warnings)

    def test_with_a_construction_the_output_is_written(self):
        source = self.source(a=Land(rings=[rect(0, 0, 20, 20)], cadgenno="501",
                                    buildings=[Building(1, [rect(5, 5, 10, 10)])]))
        result = self.convert(source, OUTPUT_CONSTRUCTII="TEMPORARY_OUTPUT")
        self.assertEqual(result["OUTPUT_CONSTRUCTII"].featureCount(), 1)
        self.assertEqual(self.feedback.warnings, [])


class ChainedAlgorithmTests(ConversionAlgorithmTest):
    """The layers of a conversion passed straight to another CADex algorithm, as a model or
    a script does, are read with their ID, sector and construcție columns."""

    def delivery(self):
        """Two imobile of sector 5 that overlap by 10 cm, the first with a construcție that
        reaches 30 cm into the second."""
        return self.source(
            a=Land(rings=[rect(0, 0, 20.1, 40)], cadgenno="1001", sector="5",
                   buildings=[Building(1, [rect(12, 20, 20.4, 30)])]),
            b=Land(rings=[rect(20, 0, 40, 40)], cadgenno="1002", sector="5"))

    def findings(self, imobile, constructii):
        result = processing.run("cadex:topologycheck", {
            "IMOBILE": imobile, "CONSTRUCTII": constructii,
            "OUTPUT_AREAS": "TEMPORARY_OUTPUT", "OUTPUT_POINTS": "TEMPORARY_OUTPUT"},
            feedback=self.feedback)
        return sorted((feature["tip"], feature["sector"], feature["detalii"])
                      for feature in result["OUTPUT_AREAS"].getFeatures())

    EXPECTED = [
        ("Construcție în afara imobilului", "5",
         "Un punct al construcției 1001 C1 iese din imobilul 1001 cu 30 cm (3 mp în afară)."),
        ("Suprapunere între imobile", "5", "Imobilele 1001 și 1002 se suprapun pe 4 mp."),
    ]

    def test_temporary_layers_carry_the_columns_of_the_conversion(self):
        result = self.convert(self.delivery(), OUTPUT_CONSTRUCTII="TEMPORARY_OUTPUT")
        imobile, constructii = result["OUTPUT_IMOBILE"], result["OUTPUT_CONSTRUCTII"]
        self.assertEqual((roles.column_of(imobile, "id"), roles.column_of(imobile, "sector"),
                          roles.column_of(constructii, "nr_constr")),
                         ("id", "sector", "nr_constr"))
        self.assertTrue(roles.batch_of(imobile))
        self.assertEqual(roles.batch_of(imobile), roles.batch_of(constructii))
        self.assertEqual(self.findings(imobile, constructii), self.EXPECTED)

    def test_files_passed_on_by_their_path_carry_the_columns_of_the_conversion(self):
        for extension in ("gpkg", "shp"):
            with self.subTest(extension=extension):
                result = self.convert(
                    self.delivery(),
                    OUTPUT_IMOBILE=os.path.join(self.tmp, f"imobile.{extension}"),
                    OUTPUT_CONSTRUCTII=os.path.join(self.tmp, f"constructii.{extension}"))
                self.assertEqual(
                    self.findings(result["OUTPUT_IMOBILE"], result["OUTPUT_CONSTRUCTII"]),
                    self.EXPECTED)


def tearDownModule():
    helpers.stop_qgis()


class ExcelOutputTests(ConversionAlgorithmTest):

    def test_a_text_cut_to_the_length_of_an_excel_cell_is_named_in_a_warning(self):
        owners = tuple(f"PROPRIETAR NUMARUL {n:05d}" for n in range(2000))
        source = self.source(multi=Land(rings=[rect(0, 0, 20, 20)], cadgenno="1", owners=owners))
        report = os.path.join(self.tmp, "raport.xlsx")
        results = self.convert(source, OUTPUT_XLSX=report)
        self.assertEqual(results["OUTPUT_XLSX"], report)
        self.assertEqual([w for w in self.feedback.warnings if "scurtat" in w], [
            "1 celulă din fișierul Excel a fost scurtată la 32.767 de caractere, cât cuprinde "
            "o celulă Excel."])

    def test_texts_that_fit_give_no_warning_about_cut_cells(self):
        source = self.source(unu=Land(rings=[rect(0, 0, 20, 20)], cadgenno="1"))
        self.convert(source, OUTPUT_XLSX=os.path.join(self.tmp, "raport.xlsx"))
        self.assertEqual([w for w in self.feedback.warnings if "scurtat" in w], [])


if __name__ == "__main__":
    helpers.main()
