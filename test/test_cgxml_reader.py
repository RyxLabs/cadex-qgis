# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CGXML reading on synthetic eTerra exports and deliveries and on broken files. Plain
Python, no QgsApplication."""

import os
import random
import unittest

import helpers
import synthetic

from cadex.services import cgxml_reader as reader

DATA = helpers.DATA_DIR
BROKEN = os.path.join(DATA, "broken")


def read(name, folder=DATA):
    return reader.read_file(os.path.join(folder, name))


def net_area(parts):
    return sum(abs(reader._ring_area(part[0]))
               - sum(abs(reader._ring_area(hole)) for hole in part[1:])
               for part in parts)


class EterraExportTests(unittest.TestCase):

    def setUp(self):
        self.imobile, self.constructii, self.warnings = read("eterra_100001.cgxml")
        self.land = self.imobile[0]

    def test_empty_cadgenno_is_null_not_zero(self):
        # eTerra exports leave CADGENNO and CADSECTOR empty - a 0 would read as a real ID
        self.assertIsNone(self.land["id"])
        self.assertIsNone(self.land["sector"])
        self.assertEqual(self.land["ie"], 100001)

    def test_cadgenno_zero_is_null(self):
        # some eTerra exports write 0 instead of leaving it empty; ANCPI treats 0 as missing
        with open(os.path.join(DATA, "eterra_100001.cgxml"), "rb") as f:
            data = f.read().replace(b"<CADGENNO></CADGENNO>", b"<CADGENNO>0</CADGENNO>", 1)
        imobile, constructii, warnings = reader.parse_bytes(data)
        self.assertIsNone(imobile[0]["id"])
        self.assertIsNone(constructii[0]["id"])
        self.assertEqual(warnings, [])

    def test_creditor_and_notation_are_other_persons(self):
        self.assertEqual(self.land["propr"], "POPESCU ION, POPESCU MARIA")
        self.assertEqual(self.land["alte_pers"],
                         "BANCA EXEMPLU SA (ipotecă); IONESCU VASILE (notare)")
        # the mortgage and the notation are linked to the land only
        self.assertEqual(self.constructii[0]["alte_pers"], "")

    def test_building_identifier_stays_text(self):
        building = self.constructii[0]
        self.assertEqual(building["ie"], "100001-C1")
        self.assertEqual(building["nr_constr"], "C1")
        self.assertEqual(building["propr"], "POPESCU ION, POPESCU MARIA")
        self.assertEqual(building["are_acte"], "Da")

    def test_closed_ring_gives_the_measured_area(self):
        self.assertEqual(len(self.land["rings"]), 1)
        self.assertAlmostEqual(net_area(self.land["rings"]), 500.0)

    def test_values(self):
        self.assertEqual(self.land["tip_teren"], "Intravilan")
        self.assertEqual(self.land["sup_acte"], 500.5)
        self.assertEqual(self.land["imprej"], "Da")
        self.assertEqual(self.land["nr_cad_vechi"], "1543")
        self.assertEqual(self.land["categ_fol"], "CC")
        self.assertEqual(self.warnings, [])


class DeliveryTests(unittest.TestCase):

    def setUp(self):
        self.land = read("livrare_10188.cgxml")[0][0]

    def test_hole_is_subtracted(self):
        self.assertEqual([len(part) for part in self.land["rings"]], [2])
        self.assertAlmostEqual(net_area(self.land["rings"]), 9900.0)

    def test_mixed_parcels(self):
        self.assertEqual(self.land["tip_teren"], "Mixt")

    def test_missing_deed_area_is_null(self):
        self.assertIsNone(self.land["sup_acte"])

    def test_unidentified_possessor_is_listed(self):
        self.assertEqual(self.land["propr"], "NEIDENTIFICAT")

    def test_enclosed_false(self):
        self.assertEqual(self.land["imprej"], "Nu")
        self.assertEqual(self.land["id"], 10188)
        self.assertEqual(self.land["sector"], 3)


class VariantTests(unittest.TestCase):

    def test_namespace_and_link_table_spelling(self):
        land = read("variant_20001.cgxml")[0][0]
        # lowercase RegistrationXentity still links, ADMINISTRARE in part II is not ownership
        self.assertEqual(land["propr"], "COMUNA EXEMPLU")
        self.assertEqual(land["alte_pers"], "CONSILIUL LOCAL EXEMPLU (administrare)")
        # the repeated vertex is dropped, the open ring still closes
        self.assertAlmostEqual(net_area(land["rings"]), 100.0)

    def test_byte_order_mark(self):
        land = read("bom_30003.cgxml")[0][0]
        self.assertEqual(land["id"], 30003)
        self.assertEqual(land["note"], "notă cu diacritice")


class PersonTests(unittest.TestCase):
    """Every person in the file appears: owners in propr, everyone else in alte_pers with
    what they are in the file and, when not linked to the land, where."""

    DOCUMENT = b"""<?xml version="1.0" encoding="utf-8"?>
<CGXML>
  <Land><LANDID>1</LANDID><CADGENNO>501</CADGENNO></Land>
  <Building><BUILDINGID>7</BUILDINGID><LANDID>1</LANDID><BUILDNO>1</BUILDNO></Building>
  <IU><IUID>3</IUID><BUILDINGID>7</BUILDINGID><IDENTIFIER>Ap1</IDENTIFIER></IU>
  <Registration><REGISTRATIONID>1</REGISTRATIONID><REGISTRATIONTYPE>INTAB</REGISTRATIONTYPE>
    <RIGHTTYPE>PROPRIETATE</RIGHTTYPE><LBPARTNO>2</LBPARTNO></Registration>
  <Registration><REGISTRATIONID>2</REGISTRATIONID><REGISTRATIONTYPE>INTAB</REGISTRATIONTYPE>
    <RIGHTTYPE>PROPRIETATE</RIGHTTYPE><LBPARTNO>2</LBPARTNO></Registration>
  <Registration><REGISTRATIONID>3</REGISTRATIONID><REGISTRATIONTYPE>INTAB</REGISTRATIONTYPE>
    <RIGHTTYPE>PROPRIETATE</RIGHTTYPE><LBPARTNO>2</LBPARTNO></Registration>
  <Registration><REGISTRATIONID>4</REGISTRATIONID><REGISTRATIONTYPE>INTAB</REGISTRATIONTYPE>
    <RIGHTTYPE>UZUFRUCT VIAGER</RIGHTTYPE><LBPARTNO>3</LBPARTNO></Registration>
  <RegistrationXEntity><REGISTRATIONXENTITYID>1</REGISTRATIONXENTITYID>
    <REGISTRATIONID>1</REGISTRATIONID><LANDID>1</LANDID></RegistrationXEntity>
  <RegistrationXEntity><REGISTRATIONXENTITYID>2</REGISTRATIONXENTITYID>
    <REGISTRATIONID>2</REGISTRATIONID><BUILDINGID>7</BUILDINGID></RegistrationXEntity>
  <RegistrationXEntity><REGISTRATIONXENTITYID>3</REGISTRATIONXENTITYID>
    <REGISTRATIONID>3</REGISTRATIONID><IUID>3</IUID></RegistrationXEntity>
  <RegistrationXEntity><REGISTRATIONXENTITYID>4</REGISTRATIONXENTITYID>
    <REGISTRATIONID>4</REGISTRATIONID><LANDID>1</LANDID></RegistrationXEntity>
  <Person><PERSONID>1</PERSONID><REGISTRATIONID>1</REGISTRATIONID>
    <FIRSTNAME>ANA</FIRSTNAME><LASTNAME>POPA</LASTNAME></Person>
  <Person><PERSONID>2</PERSONID><REGISTRATIONID>2</REGISTRATIONID>
    <FIRSTNAME>DAN</FIRSTNAME><LASTNAME>RADU</LASTNAME></Person>
  <Person><PERSONID>3</PERSONID><REGISTRATIONID>3</REGISTRATIONID>
    <FIRSTNAME>ELENA</FIRSTNAME><LASTNAME>DUMA</LASTNAME></Person>
  <Person><PERSONID>4</PERSONID><REGISTRATIONID>4</REGISTRATIONID>
    <FIRSTNAME>ION</FIRSTNAME><LASTNAME>POPA</LASTNAME></Person>
  <Person><PERSONID>5</PERSONID><FIRSTNAME>MIHAI</FIRSTNAME><LASTNAME>STAN</LASTNAME></Person>
</CGXML>"""

    def test_everyone_is_listed_with_their_role(self):
        imobile, constructii, _warnings = reader.parse_bytes(self.DOCUMENT, "501.cgxml")
        land, building = imobile[0], constructii[0]
        self.assertEqual(land["propr"], "POPA ANA")
        self.assertEqual(land["alte_pers"],
                         "RADU DAN (proprietate, C1); DUMA ELENA (proprietate, Ap1); "
                         "POPA ION (uzufruct viager); STAN MIHAI (fără înscriere)")
        self.assertEqual(building["propr"], "RADU DAN")
        self.assertEqual(building["alte_pers"], "DUMA ELENA (proprietate, Ap1)")


class BrokenFileTests(unittest.TestCase):

    def assert_refused(self, name, fragment):
        with self.assertRaises(reader.CgxmlError) as ctx:
            read(name, BROKEN)
        self.assertIn(fragment, str(ctx.exception))

    def test_empty(self):
        self.assert_refused("empty.cgxml", "nu are conținut")

    def test_truncated(self):
        self.assert_refused("truncated.cgxml", "rândul")

    def test_wrong_encoding(self):
        self.assert_refused("wrong_encoding.cgxml", "UTF-8")

    def test_blank_lines_before_the_declaration_are_read(self):
        with open(os.path.join(DATA, "livrare_10188.cgxml"), "rb") as fp:
            data = fp.read()
        imobile, _constructii, _warnings = reader.parse_bytes(b"\r\n  " + data, "x")
        self.assertEqual(len(imobile), 1)

    def test_external_entity_is_never_resolved(self):
        self.assert_refused("external_entity.cgxml", "entitate")

    def test_other_xml(self):
        self.assert_refused("not_cgxml.cgxml", "nu este un fișier CGXML")

    def test_no_land(self):
        self.assert_refused("no_land.cgxml", "Land")

    def test_bad_values_are_reported_not_invented(self):
        imobile, _constructii, warnings = read("bad_values_30002.cgxml", BROKEN)
        land = imobile[0]
        self.assertIsNone(land["id"])
        self.assertIsNone(land["ie"])
        self.assertIsNone(land["sector"])
        self.assertIsNone(land["sup_mas"])
        # one unreadable coordinate means no geometry at all, not a polygon with a vertex missing
        self.assertEqual(land["rings"], [])
        joined = " ".join(warnings)
        self.assertIn("30002A", joined)
        self.assertIn("IE-30002", joined)
        self.assertIn("punctul 3", joined)

    def test_oversized_file_is_refused_before_reading(self):
        original = reader.MAX_FILE_BYTES
        reader.MAX_FILE_BYTES = 100
        try:
            with self.assertRaises(reader.CgxmlError):
                read("eterra_100001.cgxml")
        finally:
            reader.MAX_FILE_BYTES = original


class RingTests(unittest.TestCase):

    SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_closing_point_ends_a_ring(self):
        points = self.SQUARE + [(0, 0)] + [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]
        self.assertEqual([len(r) for r in reader.split_rings(points)], [4, 4])

    def test_open_ring_and_repeated_vertex(self):
        points = [(0, 0), (10, 0), (10, 0), (10, 10), (0, 10)]
        self.assertEqual(reader.split_rings(points), [self.SQUARE])

    def test_too_few_points_is_no_ring(self):
        self.assertEqual(reader.split_rings([(0, 0), (1, 0)]), [])

    def test_hole_island_and_separate_part(self):
        outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
        hole = [(10, 10), (60, 10), (60, 60), (10, 60)]
        island = [(20, 20), (30, 20), (30, 30), (20, 30)]
        separate = [(200, 0), (210, 0), (210, 10), (200, 10)]
        parts = reader.organize_rings([island, separate, hole, outer])
        self.assertEqual([len(p) for p in parts], [2, 1, 1])
        # 10000 - 2500 + 100 + 100: the island inside the hole counts again
        self.assertAlmostEqual(net_area(parts), 7700.0)

    def test_zero_area_ring_is_dropped(self):
        sliver = [(0, 0), (5, 0), (10, 0)]
        self.assertEqual(reader.organize_rings([self.SQUARE, sliver]), [[self.SQUARE]])

    def test_land_class(self):
        self.assertEqual(reader.land_class([]), "")
        self.assertEqual(reader.land_class(["true", "TRUE"]), "Intravilan")
        self.assertEqual(reader.land_class(["false", ""]), "Extravilan")
        self.assertEqual(reader.land_class(["true", ""]), "Mixt")


class GeneratedCorpusTests(unittest.TestCase):
    """200 random imobile, half eTerra style and half delivery style: plain rings, holes and
    islands in holes. Each must read back with its declared area and owners."""

    def test_random_shapes_read_back_exactly(self):
        rng = random.Random(20260928)
        for index in range(200):
            style = "eterra" if index % 2 else "livrare"
            land = synthetic.random_land(rng, index, style)
            with self.subTest(index=index, style=style):
                imobile, constructii, warnings = reader.parse_bytes(
                    synthetic.cgxml_bytes(land), f"{index}.cgxml")
                record = imobile[0]
                self.assertEqual(warnings, [])
                self.assertAlmostEqual(net_area(record["rings"]), record["sup_mas"], delta=0.01)
                self.assertEqual(len(record["rings"]), 2 if len(land.rings) == 3 else 1)
                self.assertEqual(record["propr"], land.owners[0])
                self.assertEqual(record["ie"], int(land.ie) if land.ie else None)
                self.assertEqual(record["id"], int(land.cadgenno) if land.cadgenno else None)
                self.assertEqual(len(constructii), len(land.buildings))
                for building in constructii:
                    self.assertAlmostEqual(net_area(building["rings"]), building["sup_mas"],
                                           delta=0.01)
                    self.assertEqual(building["propr"], land.owners[0])


class ConversionReaderTests(unittest.TestCase):
    """Values written by a script, outlines that enclose no area, provisional and possession
    entries, and co-owners who share a name."""

    SQUARE = [(500000, 400000), (500010, 400000), (500010, 400010), (500000, 400010)]

    @staticmethod
    def cgxml(*rows) -> bytes:
        """A CGXML document with one Land, LANDID 1, and the given element rows."""
        return ("<?xml version='1.0' encoding='utf-8'?>\n<CGXML>\n" + "\n".join(rows)
                + "\n</CGXML>").encode("utf-8")

    @staticmethod
    def points(owner_tag, owner_id, coordinates):
        return [f"<Points><POINTID>{n}</POINTID><{owner_tag}>{owner_id}</{owner_tag}>"
                f"<NO>{n}</NO><X>{x}</X><Y>{y}</Y></Points>"
                for n, (x, y) in enumerate(coordinates, 1)]

    @staticmethod
    def registration(reg_id, kind, right, part=2):
        return (f"<Registration><REGISTRATIONID>{reg_id}</REGISTRATIONID>"
                f"<REGISTRATIONTYPE>{kind}</REGISTRATIONTYPE><RIGHTTYPE>{right}</RIGHTTYPE>"
                f"<LBPARTNO>{part}</LBPARTNO></Registration>"
                f"<RegistrationXEntity><REGISTRATIONXENTITYID>{reg_id}</REGISTRATIONXENTITYID>"
                f"<REGISTRATIONID>{reg_id}</REGISTRATIONID><LANDID>1</LANDID>"
                "</RegistrationXEntity>")

    @staticmethod
    def person(person_id, reg_id, last, first, code):
        return (f"<Person><PERSONID>{person_id}</PERSONID>"
                f"<REGISTRATIONID>{reg_id}</REGISTRATIONID><FIRSTNAME>{first}</FIRSTNAME>"
                f"<LASTNAME>{last}</LASTNAME><IDCODE>{code}</IDCODE></Person>")

    def test_nan_and_inf_are_reported_like_any_value_that_is_not_a_number(self):
        # a script writing an empty spreadsheet cell as "nan" or "inf"
        corners = [self.SQUARE[0], ("nan", 400000), self.SQUARE[2], self.SQUARE[3]]
        data = self.cgxml(
            "<Land><LANDID>1</LANDID><CADGENNO>nan</CADGENNO><E2IDENTIFIER>inf</E2IDENTIFIER>"
            "<MEASUREDAREA>NaN</MEASUREDAREA></Land>",
            *self.points("IMMOVABLEID", 1, corners))
        [land], _constructii, warnings = reader.parse_bytes(data, "1.cgxml")
        self.assertIsNone(land["id"])
        self.assertIsNone(land["ie"])
        self.assertIsNone(land["sup_mas"])
        self.assertEqual(land["rings"], [])
        self.assertIn("ID nenumeric 'nan'", warnings)
        self.assertIn("IE nenumeric 'inf'", warnings)
        self.assertIn("punctul 2 are coordonate care nu sunt numere, deci geometria imobilului "
                      "nu a fost construită", warnings)

    def test_points_that_enclose_no_area_are_reported(self):
        bowtie = [self.SQUARE[0], self.SQUARE[2], self.SQUARE[1], self.SQUARE[3]]
        for coordinates in (bowtie, self.SQUARE[:2]):
            data = self.cgxml(
                "<Land><LANDID>1</LANDID><CADGENNO>501</CADGENNO></Land>",
                "<Building><BUILDINGID>7</BUILDINGID><LANDID>1</LANDID>"
                "<BUILDNO>1</BUILDNO></Building>",
                *self.points("IMMOVABLEID", 1, coordinates),
                *self.points("BUILDINGID", 7, coordinates))
            [land], [building], warnings = reader.parse_bytes(data, "501.cgxml")
            with self.subTest(points=len(coordinates)):
                self.assertEqual((land["rings"], building["rings"]), ([], []))
                self.assertEqual(warnings, [
                    "punctele imobilului nu închid o suprafață, deci geometria lui nu a fost "
                    "construită",
                    "punctele construcției nu închid o suprafață, deci geometria ei nu a fost "
                    "construită"])

    def test_a_provisional_or_possession_entry_keeps_its_right(self):
        data = self.cgxml(
            "<Land><LANDID>1</LANDID><CADGENNO>501</CADGENNO></Land>",
            self.registration(1, "PROVISIONALENTRY", "IPOTECA", 3),
            self.registration(2, "POSESION_REGISTATION", "CONCESIUNE"),
            self.registration(3, "PROVISIONALENTRY", "PROPRIETATE", 3),
            self.person(1, 1, "BANCA", "EXEMPLU", "0"),
            self.person(2, 2, "COMUNA", "EXEMPLU", "0"),
            self.person(3, 3, "STAN", "MIHAI", "0"))
        [land], _constructii, _warnings = reader.parse_bytes(data, "501.cgxml")
        self.assertEqual(land["alte_pers"],
                         "BANCA EXEMPLU (ipotecă, înscriere provizorie); "
                         "COMUNA EXEMPLU (concesiune, posesie); "
                         "STAN MIHAI (înscriere provizorie)")

    def test_co_owners_with_the_same_name_are_each_listed(self):
        data = self.cgxml(
            "<Land><LANDID>1</LANDID><CADGENNO>501</CADGENNO></Land>",
            self.registration(1, "INTAB", "PROPRIETATE"),
            self.registration(2, "INTAB", "PROPRIETATE"),
            # father and son, each with his own personal code
            self.person(1, 1, "POPA", "ION", "1000000000001"),
            self.person(2, 1, "POPA", "ION", "1000000000002"),
            # the father again in a second deed, and a co-owner without a code
            self.person(3, 2, "POPA", "ION", "1000000000001"),
            self.person(4, 1, "POPA", "ANA", "9999999999999"),
            self.person(5, 2, "POPA", "ANA", "0000000000000"))
        [land], _constructii, _warnings = reader.parse_bytes(data, "501.cgxml")
        self.assertEqual(land["propr"], "POPA ION, POPA ION, POPA ANA")


if __name__ == "__main__":
    unittest.main()
