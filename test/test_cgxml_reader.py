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


def document(*rows) -> bytes:
    """A CGXML document holding the given element rows."""
    return ("<?xml version='1.0' encoding='utf-8'?>\n<CGXML>\n" + "\n".join(rows)
            + "\n</CGXML>").encode("utf-8")


def land(values="", land_id=1):
    """A Land element with CADGENNO 501 and the given further elements."""
    return f"<Land><LANDID>{land_id}</LANDID><CADGENNO>501</CADGENNO>{values}</Land>"


def building(building_id=7, number=1, land_id="<LANDID>1</LANDID>", values=""):
    return (f"<Building><BUILDINGID>{building_id}</BUILDINGID>{land_id}"
            f"<BUILDNO>{number}</BUILDNO>{values}</Building>")


def points(coordinates, owner="<IMMOVABLEID>1</IMMOVABLEID>", first=1, numbers=None, ids=None):
    """Points elements with POINTID counting up from first, or the matching entry of ids; NO
    is the same count, or the matching entry of numbers."""
    rows = []
    for index, (x, y) in enumerate(coordinates):
        point_id = ids[index] if ids else first + index
        number = numbers[index] if numbers else first + index
        rows.append(f"<Points><POINTID>{point_id}</POINTID>{owner}<NO>{number}</NO>"
                    f"<X>{x}</X><Y>{y}</Y></Points>")
    return rows


# a 10 m square and a 2 m square inside it, in Stereo 70
A, B, C, D = (500000, 400000), (500010, 400000), (500010, 400010), (500000, 400010)
E, F, G, H = (500002, 400002), (500004, 400002), (500004, 400004), (500002, 400004)


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
        self.assert_refused("external_entity.cgxml", "DOCTYPE")

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
        corners = [A, ("nan", 400000), C, D]
        data = document(
            "<Land><LANDID>1</LANDID><CADGENNO>nan</CADGENNO><E2IDENTIFIER>inf</E2IDENTIFIER>"
            "<MEASUREDAREA>NaN</MEASUREDAREA></Land>",
            *points(corners))
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
        bowtie = [A, C, B, D]
        for coordinates in (bowtie, [A, B]):
            data = document(
                "<Land><LANDID>1</LANDID><CADGENNO>501</CADGENNO></Land>",
                "<Building><BUILDINGID>7</BUILDINGID><LANDID>1</LANDID>"
                "<BUILDNO>1</BUILDNO></Building>",
                *points(coordinates),
                *points(coordinates, owner="<BUILDINGID>7</BUILDINGID>"))
            [land], [building], warnings = reader.parse_bytes(data, "501.cgxml")
            with self.subTest(points=len(coordinates)):
                self.assertEqual((land["rings"], building["rings"]), ([], []))
                self.assertEqual(warnings, [
                    "punctele imobilului nu închid o suprafață, deci geometria lui nu a fost "
                    "construită",
                    "punctele construcției nu închid o suprafață, deci geometria ei nu a fost "
                    "construită"])

    def test_a_provisional_or_possession_entry_keeps_its_right(self):
        data = document(
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
        data = document(
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


def shapes(coordinates):
    """(the ring sizes of each part, the area, the warnings) of an imobil with these points."""
    [record], _constructii, warnings = reader.parse_bytes(
        document(land(), *points(coordinates)), "501.cgxml")
    return [[len(ring) for ring in part] for part in record["rings"]], \
        net_area(record["rings"]), warnings


class RepeatedPointTests(unittest.TestCase):
    """A point written twice in a row is one corner, wherever it stands in the outline."""

    def test_the_first_point_written_twice_is_one_corner(self):
        self.assertEqual(shapes([A, A, B, C, D]), ([[4]], 100.0, []))
        self.assertEqual(shapes([A, A, B, C, D, A]), ([[4]], 100.0, []))

    def test_the_closing_point_written_twice_is_not_a_corner_of_the_next_outline(self):
        self.assertEqual(shapes([A, B, C, D, A, A, E, F, G, H, E]), ([[4, 4]], 96.0, []))

    def test_the_first_point_of_a_hole_written_twice_is_one_corner(self):
        self.assertEqual(shapes([A, B, C, D, A, E, E, F, G, H, E]), ([[4, 4]], 96.0, []))


class OutlineSplitTests(unittest.TestCase):
    """The points of an imobil are split into its outlines: an outline ends where its first
    point comes again, the last one may be left open, a point written twice in a row is one
    corner, and an outline may start on the corner where the one before it closed."""

    # 50 mp triangles: A B C, two more that start on A and touch the others only there, and
    # one apart
    N, P = (499990, 400000), (499990, 399990)
    Q, R = (500010, 399990), (500000, 399990)
    K, L, M = (500100, 400100), (500110, 400100), (500110, 400110)
    # two triangular holes of the square A B C D, of 10.5 and 2 mp; the first starts on A
    S, T = (500005, 400002), (500002, 400005)
    U, V, W = (500006, 400006), (500008, 400006), (500008, 400008)

    SQUARE, HOLE = [A, B, C, D], [E, F, G, H]
    CASES = (
        ("one outline left open", [A, B, C, D], [SQUARE]),
        ("one closed outline", [A, B, C, D, A], [SQUARE]),
        ("closed outlines one after another",
         [A, B, C, D, A, E, F, G, H, E, K, L, M, K], [SQUARE, HOLE, [K, L, M]]),
        ("a closed outline, then one left open", [A, B, C, D, A, E, F, G, H], [SQUARE, HOLE]),
        ("the first point twice, left open", [A, A, B, C, D], [SQUARE]),
        ("the first point twice, closed", [A, A, B, C, D, A], [SQUARE]),
        ("the first point of the second outline twice",
         [A, B, C, D, A, E, E, F, G, H, E], [SQUARE, HOLE]),
        ("the first point of the second outline twice, left open",
         [A, B, C, D, A, E, E, F, G, H], [SQUARE, HOLE]),
        ("a middle point twice, left open", [A, B, B, C, D], [SQUARE]),
        ("a middle point twice, closed", [A, B, C, C, D, A], [SQUARE]),
        ("the last point twice, left open", [A, B, C, D, D], [SQUARE]),
        ("the last point before the closing one twice", [A, B, C, D, D, A], [SQUARE]),
        ("the closing point twice", [A, B, C, D, A, A], [SQUARE]),
        ("the closing point twice, an outline after it",
         [A, B, C, D, A, A, E, F, G, H, E], [SQUARE, HOLE]),
        ("the closing point twice, two outlines after it",
         [A, B, C, D, A, A, E, F, G, H, E, K, L, M, K], [SQUARE, HOLE, [K, L, M]]),
        ("the closing point of every outline twice",
         [A, B, C, D, A, A, E, F, G, H, E, E, K, L, M, K, K], [SQUARE, HOLE, [K, L, M]]),
        ("a point twice before the closing point, the closing point twice, a point twice in "
         "the next outline", [A, B, C, D, D, A, A, E, F, G, G, H, E], [SQUARE, HOLE]),
        ("every point twice",
         [A, A, B, B, C, C, D, D, A, A, E, E, F, F, G, G, H, H, E, E], [SQUARE, HOLE]),
        ("two outlines that start on one corner",
         [A, B, C, A, A, N, P, A], [[A, B, C], [A, N, P]]),
        ("two outlines that start on one corner, an outline after them",
         [A, B, C, A, A, N, P, A, K, L, M, K], [[A, B, C], [A, N, P], [K, L, M]]),
        ("three outlines that start on one corner, an outline after them",
         [A, B, C, A, A, N, P, A, A, Q, R, A, K, L, M, K],
         [[A, B, C], [A, N, P], [A, Q, R], [K, L, M]]),
        ("two outlines that start on one corner, the second left open",
         [A, B, C, A, A, N, P], [[A, B, C], [A, N, P]]),
        ("the second outline starts on the corner of the first, written twice",
         [A, B, C, A, A, A, N, P, A, K, L, M, K], [[A, B, C], [A, N, P], [K, L, M]]),
        ("a hole that starts on the first corner of its outline, a hole after it",
         [A, B, C, D, A, A, S, T, A, U, V, W, U], [SQUARE, [A, S, T], [U, V, W]]),
        ("an outline that ends where the next one starts, that corner written once",
         [A, B, C, A, N, P, A], [[A, B, C], [N, P, A]]),
        ("two points", [A, B], []),
        ("no points", [], []),
    )

    def test_each_way_of_writing_the_outlines(self):
        for title, written, outlines in self.CASES:
            with self.subTest(title):
                self.assertEqual(reader.split_rings(written), outlines)

    def test_parts_that_start_on_one_corner_are_each_a_part(self):
        written = [A, B, C, A, A, self.N, self.P, A, self.K, self.L, self.M, self.K]
        self.assertEqual(shapes(written), ([[3], [3], [3]], 150.0, []))

    def test_a_hole_that_starts_on_the_first_corner_of_its_outline_is_a_hole(self):
        written = [A, B, C, D, A, A, self.S, self.T, A, self.U, self.V, self.W, self.U]
        self.assertEqual(shapes(written), ([[4, 3, 3]], 87.5, []))


class PointOrderTests(unittest.TestCase):
    """The points of an imobil or a construcție are joined in POINTID order; NO is only the
    name of a point."""

    def read(self, *rows):
        imobile, constructii, warnings = reader.parse_bytes(
            document(land(), *rows), "501.cgxml")
        return imobile[0], constructii, warnings

    def test_numbers_that_start_again_at_each_outline_do_not_mix_the_outlines(self):
        record, _constructii, warnings = self.read(
            *points([A, B, C, D, A], numbers=[1, 2, 3, 4, 5]),
            *points([E, F, G, H, E], first=6, numbers=[1, 2, 3, 4, 5]))
        self.assertEqual(record["rings"], [[[A, B, C, D], [E, F, G, H]]])
        self.assertEqual(warnings, [])

    def test_points_listed_out_of_order_are_joined_by_pointid(self):
        first, second, third, fourth = points([A, B, C, D], numbers=["d", "c", "b", "a"])
        record, _constructii, _warnings = self.read(third, first, fourth, second)
        self.assertEqual(record["rings"], [[[A, B, C, D]]])

    def test_without_a_distinct_whole_pointid_on_every_point_the_order_of_the_file_is_kept(self):
        for ids in (["7", "7", "7", "7"], ["4", "3", "", "1"], ["4", "3", "3,5", "1"]):
            with self.subTest(ids=ids):
                record, _constructii, _warnings = self.read(
                    *points([A, B, C, D], ids=ids, numbers=[4, 3, 2, 1]))
                self.assertEqual(record["rings"], [[[A, B, C, D]]])

    def test_a_point_with_a_building_id_belongs_to_the_building(self):
        record, [construction], warnings = self.read(
            building(), *points([A, B, C, D]),
            *points([E, F, G, H], first=10,
                    owner="<IMMOVABLEID>1</IMMOVABLEID><BUILDINGID>7</BUILDINGID>"))
        self.assertEqual(record["rings"], [[[A, B, C, D]]])
        self.assertEqual(construction["rings"], [[[E, F, G, H]]])
        self.assertEqual(warnings, [])


class LinkTests(unittest.TestCase):
    """A parcel, a construcție or a point that does not carry the LANDID of the file's only
    imobil is read as that imobil's and reported."""

    PARCEL = ("<Parcel><PARCELID>1</PARCELID>{link}<USECATEGORY>CC</USECATEGORY>"
              "<INTRAVILAN>true</INTRAVILAN><LANDPLOTNO>12</LANDPLOTNO>"
              "<PARCELNO>345</PARCELNO><TITLENO>77</TITLENO></Parcel>")
    BUILDING_POINTS = points([E, F, G, H], owner="<BUILDINGID>7</BUILDINGID>", first=10)

    def test_a_parcel_without_the_id_of_the_imobil_is_read_as_its_parcel(self):
        for link in ("", "<LANDID>9</LANDID>"):
            with self.subTest(link=link):
                [record], _constructii, warnings = reader.parse_bytes(
                    document(land(), self.PARCEL.format(link=link)), "501.cgxml")
                self.assertEqual(
                    (record["tip_teren"], record["nr_tarla"], record["nr_parcele"],
                     record["categ_fol"], record["nr_titlu"]),
                    ("Intravilan", "12", "345", "CC", "77"))
                self.assertEqual(warnings, ["1 parcelă nu are LANDID-ul imobilului și a fost "
                                            "citită ca parcelă a lui"])

    def test_a_building_without_the_id_of_the_imobil_is_read_as_its_building(self):
        registration = (
            "<Registration><REGISTRATIONID>1</REGISTRATIONID>"
            "<REGISTRATIONTYPE>INTAB</REGISTRATIONTYPE><RIGHTTYPE>PROPRIETATE</RIGHTTYPE>"
            "<LBPARTNO>2</LBPARTNO></Registration>"
            "<RegistrationXEntity><REGISTRATIONID>1</REGISTRATIONID>"
            "<BUILDINGID>7</BUILDINGID></RegistrationXEntity>"
            "<Person><PERSONID>1</PERSONID><REGISTRATIONID>1</REGISTRATIONID>"
            "<FIRSTNAME>DAN</FIRSTNAME><LASTNAME>RADU</LASTNAME></Person>")
        for link in ("", "<LANDID>9</LANDID>"):
            with self.subTest(link=link):
                [record], [construction], warnings = reader.parse_bytes(
                    document(land(), building(land_id=link), registration,
                             *points([A, B, C, D]), *self.BUILDING_POINTS), "501.cgxml")
                self.assertEqual((construction["id"], construction["nr_constr"],
                                  construction["propr"]), (501, "C1", "RADU DAN"))
                self.assertEqual(construction["rings"], [[[E, F, G, H]]])
                self.assertEqual(record["alte_pers"], "RADU DAN (proprietate, C1)")
                self.assertEqual(warnings, ["1 construcție nu are LANDID-ul imobilului și a "
                                            "fost citită ca a lui"])

    def test_points_without_the_id_of_the_imobil_are_read_as_its_points(self):
        for owner in ("", "<IMMOVABLEID>9</IMMOVABLEID>"):
            with self.subTest(owner=owner):
                [record], _constructii, warnings = reader.parse_bytes(
                    document(land(), *points([A, B, C, D], owner=owner)), "501.cgxml")
                self.assertEqual(record["rings"], [[[A, B, C, D]]])
                self.assertEqual(warnings, ["4 puncte nu au IMMOVABLEID-ul imobilului și au "
                                            "fost citite ca puncte ale lui"])

    def test_points_of_a_building_that_is_not_in_the_file_are_left_out(self):
        [record], constructii, warnings = reader.parse_bytes(
            document(land(), *points([A, B, C, D]), *self.BUILDING_POINTS), "501.cgxml")
        self.assertEqual((record["rings"], constructii), ([[[A, B, C, D]]], []))
        self.assertEqual(warnings, ["4 puncte au BUILDINGID-ul unei construcții care nu este "
                                    "în fișier și nu au fost folosite"])

    def test_a_building_id_of_zero_on_a_point_of_the_imobil_names_no_building(self):
        zero = points([A, B, C, D], owner="<IMMOVABLEID>1</IMMOVABLEID><BUILDINGID>0</BUILDINGID>")
        [record], constructii, warnings = reader.parse_bytes(
            document(land(), *zero), "501.cgxml")
        self.assertEqual((record["rings"], constructii, warnings), ([[[A, B, C, D]]], [], []))
        [record], [construction], warnings = reader.parse_bytes(
            document(land(), building(), *zero, *self.BUILDING_POINTS), "501.cgxml")
        self.assertEqual((record["rings"], construction["rings"], warnings),
                         ([[[A, B, C, D]]], [[[E, F, G, H]]], []))

    def test_an_immovable_id_of_zero_on_a_point_of_a_building_names_no_imobil(self):
        [record], [construction], warnings = reader.parse_bytes(
            document(land(), building(), *points([A, B, C, D]),
                     *points([E, F, G, H], first=10,
                             owner="<IMMOVABLEID>0</IMMOVABLEID><BUILDINGID>7</BUILDINGID>")),
            "501.cgxml")
        self.assertEqual((record["rings"], construction["rings"], warnings),
                         ([[[A, B, C, D]]], [[[E, F, G, H]]], []))

    def test_the_points_of_a_building_with_the_id_zero_are_its_points(self):
        [record], [construction], warnings = reader.parse_bytes(
            document(land(), building(building_id=0), *points([A, B, C, D]),
                     *points([E, F, G, H], first=10, owner="<BUILDINGID>0</BUILDINGID>")),
            "501.cgxml")
        self.assertEqual((record["rings"], construction["rings"], warnings),
                         ([[[A, B, C, D]]], [[[E, F, G, H]]], []))

    def test_in_a_file_with_several_imobile_an_element_without_their_ids_is_left_out(self):
        imobile, constructii, warnings = reader.parse_bytes(
            document(land(), land(land_id=2), self.PARCEL.format(link=""),
                     building(land_id="<LANDID>9</LANDID>"),
                     *points([A, B, C, D], owner="")), "501.cgxml")
        self.assertEqual([(r["tip_teren"], r["rings"]) for r in imobile], [("", []), ("", [])])
        self.assertEqual(constructii, [])
        self.assertEqual(warnings, [
            "1 parcelă nu are LANDID-ul unui imobil din fișier și nu a fost citită",
            "1 construcție nu are LANDID-ul unui imobil din fișier și nu a fost citită",
            "4 puncte nu au IMMOVABLEID-ul unui imobil din fișier și nu au fost folosite"])


class CoordinateRangeTests(unittest.TestCase):
    """Coordinates that are not Stereo 70 metres in Romania give no geometry."""

    def outline(self, coordinates):
        [record], [construction], warnings = reader.parse_bytes(
            document(land(), building(), *points(coordinates),
                     *points(coordinates, owner="<BUILDINGID>7</BUILDINGID>", first=10)),
            "501.cgxml")
        return record, construction, warnings

    def test_an_outline_in_degrees_gives_no_geometry_and_names_the_point(self):
        record, construction, warnings = self.outline(
            [(25.1, 45.1), (25.2, 45.1), (25.2, 45.2), (25.1, 45.2)])
        self.assertEqual((record["rings"], construction["rings"]), ([], []))
        # not counted with the records whose file holds no coordinates
        self.assertTrue(record["_has_points"] and construction["_has_points"])
        self.assertEqual(warnings, [
            "punctul 1 are coordonate în afara României, deci geometria imobilului nu a fost "
            "construită",
            "punctul 10 are coordonate în afara României, deci geometria construcției nu a "
            "fost construită"])

    def test_a_point_left_at_zero_gives_no_geometry(self):
        record, construction, warnings = self.outline([A, B, (0, 0), C, D])
        self.assertEqual((record["rings"], construction["rings"]), ([], []))
        self.assertIn("punctul 3 are coordonate în afara României, deci geometria imobilului "
                      "nu a fost construită", warnings)

    def test_coordinates_at_the_corners_of_romania_are_read(self):
        # the extent of EPSG:3844's area of use, land and sea, in its own metres
        for x, y in ((116400, 215600), (1018900, 771870)):
            with self.subTest(x=x, y=y):
                [record], _constructii, warnings = reader.parse_bytes(document(
                    land(), *points([(x, y), (x + 10, y), (x + 10, y + 10), (x, y + 10)])),
                    "501.cgxml")
                self.assertEqual((len(record["rings"]), warnings), (1, []))


class DoctypeTests(unittest.TestCase):
    """A CGXML file declares no document type, which is where a file defines entities that
    expand to many times their size."""

    TEXT = ("<?xml version='1.0' encoding='{encoding}'?>{doctype}<CGXML><Land>"
            "<LANDID>1</LANDID><CADGENNO>501</CADGENNO><NOTES>{note}</NOTES></Land></CGXML>")
    ENTITY = "<!DOCTYPE CGXML [<!ENTITY nota \"" + "a" * 1000 + "\">]>"

    def test_a_file_that_declares_a_document_type_is_refused(self):
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                data = self.TEXT.format(encoding=encoding, doctype=self.ENTITY,
                                        note="&nota;" * 100).encode(encoding)
                with self.assertRaises(reader.CgxmlError) as refused:
                    reader.parse_bytes(data, "501.cgxml")
                self.assertIn("DOCTYPE", str(refused.exception))

    def test_a_document_type_without_entities_is_refused_too(self):
        data = self.TEXT.format(encoding="utf-8", doctype="<!DOCTYPE CGXML>", note="x")
        with self.assertRaises(reader.CgxmlError):
            reader.parse_bytes(data.encode("utf-8"), "501.cgxml")

    def test_the_word_in_a_note_is_not_a_declaration(self):
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                data = self.TEXT.format(encoding=encoding, doctype="",
                                        note="fără DOCTYPE, cu ș și ț").encode(encoding)
                [record], _constructii, _warnings = reader.parse_bytes(data, "501.cgxml")
                self.assertEqual((record["id"], record["note"]),
                                 (501, "fără DOCTYPE, cu ș și ț"))


class NumberTests(unittest.TestCase):
    """A number is read with every digit, and a value that is not a number is left empty and
    reported with its field."""

    @staticmethod
    def identified(cadgenno, ie):
        return document(f"<Land><LANDID>1</LANDID><E2IDENTIFIER>{ie}</E2IDENTIFIER>"
                        f"<CADGENNO>{cadgenno}</CADGENNO></Land>")

    def test_a_long_identifier_keeps_every_digit(self):
        [record], _constructii, warnings = reader.parse_bytes(
            self.identified("12345678901234567", "9007199254740993"), "x.cgxml")
        self.assertEqual((record["id"], record["ie"]), (12345678901234567, 9007199254740993))
        self.assertEqual(warnings, [])

    def test_a_whole_number_written_with_a_decimal_zero_is_read(self):
        [record], _constructii, warnings = reader.parse_bytes(
            self.identified("10188.0", "50124.00"), "x.cgxml")
        self.assertEqual((record["id"], record["ie"]), (10188, 50124))
        self.assertEqual(warnings, [])

    def test_a_number_longer_than_a_layer_field_holds_is_left_empty_and_reported(self):
        long = "99999999999999999999"
        [record], _constructii, warnings = reader.parse_bytes(
            self.identified(long, "-" + long), "x.cgxml")
        self.assertEqual((record["id"], record["ie"]), (None, None))
        # the text stays, so the Comparator can pair the files by it
        self.assertEqual(record["id_text"], long)
        self.assertEqual(warnings, [f"ID prea lung '{long}'", f"IE prea lung '-{long}'"])

    def test_a_value_that_is_not_a_number_is_left_empty_and_reported(self):
        data = document(
            land("<CADSECTOR>12A</CADSECTOR><MEASUREDAREA>1234,56</MEASUREDAREA>"
                 "<PARCELLEGALAREA>1.234,56</PARCELLEGALAREA>"),
            building(values="<MEASUREDAREA>55,5</MEASUREDAREA><TOTALAREA>cca 120</TOTALAREA>"
                            "<LEVELSNO>P+1</LEVELSNO>"))
        [record], [construction], warnings = reader.parse_bytes(data, "501.cgxml")
        self.assertEqual((record["sector"], record["sup_mas"], record["sup_acte"]),
                         (None, None, None))
        self.assertEqual((construction["sup_mas"], construction["sup_tot"],
                          construction["etaje"]), (None, None, None))
        self.assertEqual(warnings, [
            "sector nenumeric '12A'",
            "suprafață măsurată nenumerică '1234,56'",
            "suprafață din acte nenumerică '1.234,56'",
            "număr de niveluri nenumeric 'P+1'",
            "suprafață construită la sol nenumerică '55,5'",
            "suprafață construită desfășurată nenumerică 'cca 120'"])


class ManyOutlineTests(unittest.TestCase):

    @staticmethod
    def squares(count):
        """Points of count closed 2 m squares, 3 m apart."""
        rows = []
        for n in range(count):
            x, y = 500000 + 3 * (n % 50), 400000 + 3 * (n // 50)
            rows += points([(x, y), (x + 2, y), (x + 2, y + 2), (x, y + 2), (x, y)],
                           first=1 + 5 * n)
        return rows

    def test_outlines_apart_from_each_other_are_not_compared_point_by_point(self):
        from unittest import mock
        inside = reader._point_in_ring
        calls = []
        with mock.patch.object(reader, "_point_in_ring",
                               lambda *args: calls.append(1) or inside(*args)):
            [record], _constructii, warnings = reader.parse_bytes(
                document(land(), *self.squares(300)), "501.cgxml")
        self.assertEqual((len(record["rings"]), warnings), (300, []))
        self.assertEqual(len(calls), 0)

    def test_an_imobil_with_more_outlines_than_are_read_gets_no_geometry(self):
        from unittest import mock
        with mock.patch.object(reader, "MAX_RINGS", 5):
            [within], _constructii, no_warnings = reader.parse_bytes(
                document(land(), *self.squares(5)), "501.cgxml")
            [beyond], _constructii, warnings = reader.parse_bytes(
                document(land(), *self.squares(6)), "501.cgxml")
        self.assertEqual((len(within["rings"]), no_warnings), (5, []))
        self.assertEqual((beyond["rings"], beyond["_has_points"]), ([], True))
        self.assertEqual(warnings, ["imobilul are 6 contururi, peste limita de 5, deci "
                                    "geometria lui nu a fost construită"])


class DeclaredAreaTests(unittest.TestCase):
    """The area of the outline built from the coordinates against the measured area the file
    declares."""

    def warnings(self, land_area, building_area="", coordinates=(A, B, C, D)):
        _imobile, _constructii, warnings = reader.parse_bytes(document(
            land(f"<MEASUREDAREA>{land_area}</MEASUREDAREA>"),
            building(values=f"<MEASUREDAREA>{building_area}</MEASUREDAREA>"),
            *points(coordinates),
            *points([E, F, G, H], owner="<BUILDINGID>7</BUILDINGID>", first=20)), "501.cgxml")
        return warnings

    def test_an_outline_with_another_area_than_the_declared_one_is_reported(self):
        self.assertEqual(self.warnings("150", "9.5"), [
            "suprafața imobilului calculată din coordonate este 100 mp, iar cea măsurată "
            "din fișier este 150 mp",
            "suprafața construcției C1 calculată din coordonate este 4 mp, iar cea "
            "măsurată din fișier este 9,5 mp"])

    def test_a_declared_area_rounded_to_the_metre_is_the_same_area(self):
        self.assertEqual(self.warnings("100.4", "4"), [])
        self.assertEqual(self.warnings("99", "5"), [])
        # the hole is taken out of the area
        self.assertEqual(self.warnings("96", "4", (A, B, C, D, A, E, F, G, H, E)), [])
        self.assertEqual(len(self.warnings("100", "4", (A, B, C, D, A, E, F, G, H, E))), 1)

    def test_an_area_that_is_not_declared_is_not_compared(self):
        for area in ("", "0", "0.0"):
            with self.subTest(area=area):
                self.assertEqual(self.warnings(area, area), [])

    def test_a_record_without_geometry_is_not_compared(self):
        [_record], _constructii, warnings = reader.parse_bytes(
            document(land("<MEASUREDAREA>100</MEASUREDAREA>")), "501.cgxml")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
