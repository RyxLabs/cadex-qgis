# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparator CGXML: the pairing rules, then whole runs on synthetic deliveries and eTerra
exports with known differences."""

import os
import tempfile
import unittest
import zipfile

import helpers
import synthetic
from synthetic import Building, Land, square

helpers.start_qgis(gui=False)

from cadex.services import cgxml_columns, cgxml_compare as cc, xlsx  # noqa: E402
from cadex.services import cgxml_reader, compare_service  # noqa: E402

OLD, NEW = cc.OLD_SIDE, cc.NEW_SIDE


def entry(file_name, cadgenno="", ie="", uat="", is_new=False):
    return cc.Entry(file_name=file_name, display=file_name, record={}, buildings=[],
                    cadgenno=cadgenno, ie=ie, uat=uat, is_new=is_new)


def names(entries):
    return [e.file_name for e in entries]


class KeyingTests(unittest.TestCase):

    def test_key_by_file_name_without_extension(self):
        index = cc.index_source([entry("90001.cgxml")], cc.FILENAME, OLD)
        self.assertEqual(list(index.by_key), ["90001"])
        self.assertEqual(index.ignored, [])

    def test_key_by_ie(self):
        index = cc.index_source([entry("oricare.cgxml", ie="123456")], cc.IE, OLD)
        self.assertEqual(list(index.by_key), ["123456"])

    def test_old_file_without_ie_is_ignored(self):
        index = cc.index_source([entry("10115.cgxml")], cc.IE, OLD)
        self.assertEqual(index.by_key, {})
        self.assertEqual(index.ignored, [cc.Ignored(OLD, "10115.cgxml", "", "Fără IE")])

    def test_new_file_without_ie_is_kept_apart(self):
        index = cc.index_source([entry("Sector_3__9001.cgxml", is_new=True)], cc.IE, NEW)
        self.assertEqual(index.ignored, [])
        self.assertEqual(names(index.unkeyed), ["Sector_3__9001.cgxml"])

    def test_first_file_of_a_repeated_key_is_kept(self):
        index = cc.index_source([entry("10206.cgxml", ie="777"), entry("10207.cgxml", ie="777")],
                                cc.IE, OLD)
        self.assertEqual(index.by_key["777"].file_name, "10206.cgxml")
        self.assertEqual(index.ignored, [cc.Ignored(
            OLD, "10207.cgxml", "IE 777", "Același IE ca '10206.cgxml'; s-a comparat doar acela.")])

    def test_repeated_ie_in_a_delivery_is_a_split_imobil(self):
        # an eTerra imobil split by the systematic work: each piece keeps its IE
        index = cc.index_source([entry("510514.cgxml", ie="102313"),
                                 entry("510515.cgxml", ie="102313")], cc.IE, NEW)
        self.assertEqual(index.ignored, [])
        kept = index.by_key["102313"]
        self.assertEqual(names([kept] + kept.pieces), ["510514.cgxml", "510515.cgxml"])

    def test_repeated_name_ignores_case(self):
        index = cc.index_source([entry("AB12.cgxml"), entry("ab12.cgxml")], cc.FILENAME, NEW)
        self.assertEqual(len(index.by_key), 1)
        self.assertEqual(index.ignored, [cc.Ignored(
            NEW, "ab12.cgxml", "numele ab12",
            "Același nume ca 'AB12.cgxml'; s-a comparat doar acela.")])

    def test_same_ie_in_two_uats_is_two_imobile(self):
        index = cc.index_source([entry("a.cgxml", ie="72781", uat="44060"),
                                 entry("b.cgxml", ie="72781", uat="97919")], cc.IE, OLD)
        self.assertEqual(len(index.by_key), 2)
        self.assertEqual(index.ignored, [])


class PairingTests(unittest.TestCase):

    def pair(self, old, new, mode=cc.FILENAME):
        return cc.pair_sources(cc.index_source(old, mode, OLD), cc.index_source(new, mode, NEW))

    def test_key_in_both(self):
        pairs, missing = self.pair([entry("1.cgxml", cadgenno="1")],
                                   [entry("1.cgxml", cadgenno="1")])
        self.assertEqual([p.matched_by for p in pairs], ["key"])
        self.assertEqual(missing, [])

    def test_only_new_and_only_old(self):
        _pairs, missing = self.pair([], [entry("2.cgxml", cadgenno="2", ie="99")])
        self.assertEqual([(s, k, e.file_name) for s, k, e in missing],
                         [(cc.ONLY_NEW, "numele 2", "2.cgxml")])
        _pairs, missing = self.pair([entry("3.cgxml", cadgenno="3")], [])
        self.assertEqual([(s, k, e.file_name) for s, k, e in missing],
                         [(cc.ONLY_OLD, "numele 3", "3.cgxml")])

    def test_delivery_files_without_ie_are_only_new(self):
        _pairs, missing = self.pair(
            [entry("70000.cgxml", ie="70000")],
            [entry("Sector_3__9001.cgxml", cadgenno="9001", is_new=True),
             entry("Sector_3__9002.cgxml", cadgenno="9002")], cc.IE)
        self.assertEqual([(s, k, e.file_name) for s, k, e in missing], [
            (cc.ONLY_OLD, "IE 70000", "70000.cgxml"),
            (cc.ONLY_NEW, "fără IE (imobil nou)", "Sector_3__9001.cgxml"),
            (cc.ONLY_NEW, "fără IE", "Sector_3__9002.cgxml")])

    def test_never_across_uats_when_a_side_holds_several(self):
        pairs, missing = self.pair(
            [entry("72781.cgxml", ie="72781", uat="44060"),
             entry("x.cgxml", ie="72781", uat="97919")],
            [entry("n.cgxml", ie="72781", uat="97919")], cc.IE)
        self.assertEqual([p.older.file_name for p in pairs], ["x.cgxml"])
        self.assertEqual([e.file_name for _s, _k, e in missing], ["72781.cgxml"])

    def test_file_without_sirsup_takes_the_single_uat_of_its_side(self):
        pairs, missing = self.pair(
            [entry("1.cgxml", ie="1", uat="44060"), entry("2.cgxml", ie="2")],
            [entry("a.cgxml", ie="1", uat="44060"), entry("b.cgxml", ie="2", uat="44060"),
             entry("c.cgxml", ie="2", uat="97919")], cc.IE)
        self.assertEqual([f"{p.older.file_name}-{p.newer.file_name}" for p in pairs],
                         ["1.cgxml-a.cgxml", "2.cgxml-b.cgxml"])
        self.assertEqual([e.file_name for _s, _k, e in missing], ["c.cgxml"])

    def test_one_uat_per_side_pairs_by_plain_keys(self):
        pairs, _missing = self.pair([entry("1.cgxml", ie="1", uat="44060")],
                                    [entry("a.cgxml", ie="1", uat="44061")], cc.IE)
        self.assertEqual(len(pairs), 1)

    def test_renamed_file_found_by_ie(self):
        pairs, missing = self.pair([entry("vechi.cgxml", ie="500")], [entry("nou.cgxml", ie="500")])
        self.assertEqual([p.matched_by for p in pairs], ["ie"])
        self.assertEqual(missing, [])

    def test_no_pairing_when_only_one_side_has_an_ie_and_numbers_differ(self):
        pairs, missing = self.pair([entry("vechi.cgxml", ie="500", cadgenno="1")],
                                   [entry("nou.cgxml", cadgenno="2")])
        self.assertEqual(pairs, [])
        self.assertEqual(len(missing), 2)

    def test_renamed_delivery_file_found_by_cadastral_number(self):
        pairs, missing = self.pair([entry("13022.cgxml", cadgenno="13022")],
                                   [entry("Sector_13__13022.cgxml", cadgenno="13022")])
        self.assertEqual([p.matched_by for p in pairs], ["cadgenno"])
        self.assertEqual(missing, [])

    def test_cadastral_number_zero_is_none(self):
        # eTerra writes 0 for an imobil without a cadastral number
        def read(name):
            imobile, constructii, _warnings = cgxml_reader.parse_bytes(synthetic.cgxml_bytes(
                Land(rings=[square(500000, 400000, 20)], cadgenno="0", style="eterra")))
            return cc.entry_of((name, name, None), imobile, constructii)
        pairs, _missing = self.pair([read("a.cgxml")], [read("b.cgxml")])
        self.assertEqual(pairs, [])

    def test_no_cadastral_number_pass_when_matching_by_ie(self):
        pairs, _missing = self.pair([entry("500.cgxml", ie="500", cadgenno="7")],
                                    [entry("7.cgxml", ie="501", cadgenno="7")], cc.IE)
        self.assertEqual(pairs, [])


def delivery(number, rings, owner="POPESCU ION", buildings=(), ie="", sector="3", **kw):
    return Land(rings=rings, cadgenno=str(number), ie=ie, sector=sector, owners=(owner,),
                buildings=list(buildings), style="livrare", **kw)


class RunTests(unittest.TestCase):
    """Four deliveries on each side: 10001 unchanged; 10002 with a new owner, a moved
    boundary, a moved construcție and a new one; 10003, with a construcție, removed; 10004
    added; 10005 renamed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_cgxml_")
        self.old = os.path.join(self.tmp, "vechi")
        self.new = os.path.join(self.tmp, "nou")
        x = 500000
        synthetic.write(self.old, "10001.cgxml", delivery(
            10001, [square(x, 400000, 20)], buildings=[Building(1, [square(x + 2, 400002, 5)])]))
        synthetic.write(self.old, "10002.cgxml", delivery(
            10002, [square(x + 20, 400000, 20)],
            buildings=[Building(1, [square(x + 22, 400002, 5)])]))
        synthetic.write(self.old, "10003.cgxml", delivery(
            10003, [square(x + 40, 400000, 20)],
            buildings=[Building(1, [square(x + 42, 400002, 5)])]))
        synthetic.write(self.old, "10005.cgxml", delivery(10005, [square(x + 60, 400000, 20)],
                                                          ie="5005"))
        synthetic.write(self.new, "10001.cgxml", delivery(
            10001, [square(x, 400000, 20)], buildings=[Building(1, [square(x + 2, 400002, 5)])]))
        synthetic.write(self.new, "10002.cgxml", delivery(
            10002, [[(x + 20, 400000), (x + 41, 400000), (x + 41, 400020), (x + 20, 400020)]],
            owner="IONESCU VASILE",
            buildings=[Building(1, [square(x + 23, 400002, 5)]),
                       Building(2, [square(x + 30, 400010, 4)])]))
        synthetic.write(self.new, "10004.cgxml", delivery(10004, [square(x + 80, 400000, 20)]))
        synthetic.write(self.new, "redenumit_5005.cgxml", delivery(
            10005, [square(x + 60, 400000, 20)], ie="5005"))

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, **overrides):
        options = dict(old_paths=[self.old], new_paths=[self.new],
                       excel_path=os.path.join(self.tmp, "comparatie.xlsx"))
        options.update(overrides)
        task = cc.CgxmlCompareTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_counts_and_what_changed(self):
        task = self.run_task()
        self.assertEqual((task.old_files, task.new_files, task.matched), (4, 4, 3))
        self.assertEqual(task.renamed, 1)
        self.assertEqual([o.key for o in task.added], ["numele 10004"])
        self.assertEqual([o.key for o in task.deleted], ["numele 10003"])
        self.assertEqual([o.key for o in task.changed], ["10002"])
        self.assertEqual(task.unchanged, 2)
        changed = task.changed[0]
        self.assertEqual(changed.changes, [("sup_mas", 400.0, 420.0),
                                           ("propr", "POPESCU ION", "IONESCU VASILE")])
        for value, expected in zip(changed.moved, (400.0, 420.0, 20.0)):
            self.assertAlmostEqual(value, expected, places=6)

    def test_buildings_by_their_number(self):
        task = self.run_task()
        self.assertEqual([(o.imobil, o.key) for o in task.buildings_added], [("10002", "C2")])
        self.assertEqual([(o.imobil, o.key) for o in task.buildings_changed], [("10002", "C1")])
        self.assertAlmostEqual(task.buildings_changed[0].moved[2], 10.0, places=6)
        self.assertEqual(task.buildings_unchanged, 1)
        # the construcții of a disappeared imobil disappeared with it
        self.assertEqual([(o.imobil, o.key) for o in task.buildings_deleted], [("10003", "C1")])

    def test_building_columns_can_be_chosen(self):
        # 10002's new owner owns its construcție too
        default = self.run_task(compare_geometry=False, excel_path=None)
        self.assertEqual(default.buildings_changed, [])
        owners = self.run_task(compare_geometry=False, building_columns=["propr"])
        self.assertEqual([(o.imobil, o.key, o.changes) for o in owners.buildings_changed],
                         [("10002", "C1", [("propr", "POPESCU ION", "IONESCU VASILE")])])
        self.assertIn("propr", [key for key, _label in cc.comparable_building_columns()])
        self.assertNotIn("nr_constr", [key for key, _label in cc.comparable_building_columns()])

    def test_changed_imobile_are_classed_as_boundary_data_or_both_changed(self):
        # 10002 has a new owner and a moved boundary; its C1 only moved
        task = self.run_task()
        self.assertEqual([cc.kind_of(o) for o in task.changed], ["Limita și datele"])
        self.assertEqual([cc.kind_of(o) for o in task.buildings_changed], ["Doar limita"])
        rows = task.summary_rows()
        self.assertIn(("Imobile modificate", 1), rows)
        self.assertEqual([r for r in rows if r[0].startswith("   ")],
                         [("   Doar limita", 0), ("   Doar datele", 0),
                          ("   Limita și datele", 1)])
        features = cc.result_features(task.changed, "imobile",
                                     cgxml_columns.default_layout("imobile"), True,
                                     kinds=True).features
        self.assertEqual(features[0]["tip_modif"], "Limita și datele")
        _names, rows = xlsx.read_rows(task.excel_path, "Construcții")
        self.assertEqual(rows[0][:4], ["Imobil", "Construcție", "Stare", "Tip modificare"])
        self.assertIn(["10002", "C1", "Modificată", "Doar limita"], [r[:4] for r in rows])
        # without the geometry every change is in the data, and nothing is split
        task = self.run_task(compare_geometry=False)
        self.assertEqual([r for r in task.summary_rows() if r[0].startswith("   ")], [])
        _names, rows = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertNotIn("Tip modificare", rows[0])

    def test_tolerance_and_folding(self):
        task = self.run_task(tolerance=50.0, columns=["sup_acte"])
        self.assertEqual(task.changed, [])
        synthetic.write(self.new, "10001.cgxml", delivery(
            10001, [square(500000, 400000, 20)], owner="Popescu Ion",
            buildings=[Building(1, [square(500002, 400002, 5)])]))
        self.assertIn("10001", [o.key for o in self.run_task(columns=["propr"]).changed])
        self.assertNotIn("10001", [o.key for o in self.run_task(columns=["propr"],
                                                                fold_text=True).changed])

    def test_excel(self):
        task = self.run_task()
        self.assertEqual(xlsx.sheet_names(task.excel_path),
                         ["Sumar", "Modificate", "Apărute", "Dispărute", "Construcții"])
        _names, rows = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertEqual(rows[0][:5], ["Imobil", "Tip modificare", "Fișier vechi", "Fișier nou",
                                       "Ce s-a modificat"])
        what = [r[4] for r in rows[1:]]
        self.assertIn(cgxml_columns.label_of("imobile", "propr"), what)
        self.assertIn("Geometrie (suprafața, mp)", what)

    def test_unreadable_file_is_listed_not_fatal(self):
        with open(os.path.join(self.old, "stricat.cgxml"), "w", encoding="utf-8") as fp:
            fp.write("<CGXML><Land>")
        task = self.run_task()
        self.assertEqual([(i.side, i.file_name) for i in task.ignored], [(OLD, "stricat.cgxml")])
        self.assertIn("nu poate fi citit", task.ignored[0].reason)
        self.assertIn("Fișiere ignorate", xlsx.sheet_names(task.excel_path))

    def test_the_order_of_parcels_and_the_case_of_numbers_are_no_change(self):
        building = [Building(1, [square(500002, 400002, 5)])]
        synthetic.write(self.old, "10001.cgxml", delivery(
            10001, [square(500000, 400000, 20)], buildings=building,
            parcels=(("A", True), ("CC", True)), plot="T12"))
        synthetic.write(self.new, "10001.cgxml", delivery(
            10001, [square(500000, 400000, 20)], buildings=building,
            parcels=(("CC", True), ("A", True)), plot="12"))
        self.assertNotIn("10001", [o.key for o in self.run_task().changed])
        synthetic.write(self.new, "10001.cgxml", delivery(
            10001, [square(500000, 400000, 20)], buildings=building,
            parcels=(("CC", True),), plot="12"))
        changed = {o.key: o for o in self.run_task().changed}
        self.assertEqual(changed["10001"].changes, [("categ_fol", "A, CC", "CC")])

    def test_zip_and_files_picked_one_by_one(self):
        archive = os.path.join(self.tmp, "vechi.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            for name in os.listdir(self.old):
                zf.write(os.path.join(self.old, name), f"livrare/{name}")
        picked = [os.path.join(self.new, n) for n in ("10001.cgxml", "10002.cgxml")]
        task = self.run_task(old_paths=[archive], new_paths=picked, excel_path=None)
        self.assertEqual((task.old_files, task.new_files), (4, 2))
        self.assertEqual([o.key for o in task.changed], ["10002"])
        self.assertEqual(len(task.deleted), 2)

    def test_eterra_export_against_a_delivery(self):
        eterra = os.path.join(self.tmp, "eterra")
        livrare = os.path.join(self.tmp, "livrare")
        for number, ie in ((1, "70001"), (2, "70002")):
            synthetic.write(eterra, f"{ie}.cgxml", Land(
                rings=[square(500000 + 30 * number, 400000, 20)], ie=ie, style="eterra"))
        synthetic.write(livrare, "Sector_1__501.cgxml", delivery(
            501, [square(500030, 400000, 20)], ie="70001"))
        synthetic.write(livrare, "Sector_1__502.cgxml", delivery(
            502, [square(500200, 400000, 20)], is_new=True))
        task = self.run_task(old_paths=[eterra], new_paths=[livrare], mode=cc.IE,
                             excel_path=None)
        self.assertEqual(task.matched, 1)
        self.assertEqual([o.key for o in task.added], ["fără IE (imobil nou)"])
        self.assertEqual([o.key for o in task.deleted], ["IE 70002"])

    def test_result_features(self):
        task = self.run_task()
        layout = cgxml_columns.default_layout("imobile")
        result = cc.result_features(task.changed, "imobile", layout, with_changes=True)
        fields, features = result.fields, result.features
        self.assertIn("modificari", fields.names())
        self.assertIn("sup_mutata", fields.names())
        self.assertEqual(len(features), 1)
        feature = features[0]
        self.assertEqual(feature["id"], 10002)
        self.assertEqual(feature["modificari"], "sup_mas, propr, geometrie")
        self.assertAlmostEqual(feature["sup_mutata"], 20.0, places=2)
        self.assertAlmostEqual(feature.geometry().area(), 420.0, places=6)
        features = cc.result_features(task.buildings_added, "constructii",
                                     cgxml_columns.default_layout("constructii")).features
        self.assertEqual([f["imobil"] for f in features], ["10002"])


def half(x, side):
    return [(x + 10 * side, 400000), (x + 10 * side + 10, 400000), (x + 10 * side + 10, 400020),
            (x + 10 * side, 400020)]


class EterraAgainstDeliveryTests(unittest.TestCase):
    """An eTerra export against a delivery, as on real data: 70001 split into 501 and 502;
    70002 without coordinates in eTerra; 70003 the same but for the sector, which eTerra
    never fills."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_ie_")
        self.eterra = os.path.join(self.tmp, "eterra")
        self.livrare = os.path.join(self.tmp, "livrare")
        x = 500000
        synthetic.write(self.eterra, "70001.cgxml", Land(rings=[square(x, 400000, 20)],
                                                        ie="70001", style="eterra"))
        synthetic.write(self.eterra, "70002.cgxml", Land(rings=[], measured_area=400.0,
                                                        ie="70002", style="eterra"))
        synthetic.write(self.eterra, "70003.cgxml", Land(rings=[square(x + 100, 400000, 20)],
                                                        ie="70003", style="eterra"))
        synthetic.write(self.livrare, "501.cgxml", delivery(501, [half(x, 0)], ie="70001"))
        synthetic.write(self.livrare, "502.cgxml", delivery(502, [half(x, 1)], ie="70001"))
        synthetic.write(self.livrare, "503.cgxml", delivery(503, [square(x + 50, 400000, 20)],
                                                            ie="70002"))
        synthetic.write(self.livrare, "504.cgxml", delivery(504, [square(x + 100, 400000, 20)],
                                                            ie="70003"))

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, **overrides):
        options = dict(old_paths=[self.eterra], new_paths=[self.livrare], mode=cc.IE,
                       excel_path=os.path.join(self.tmp, "comparatie.xlsx"))
        options.update(overrides)
        task = cc.CgxmlCompareTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_a_split_imobil_is_compared_with_all_its_pieces(self):
        task = self.run_task()
        self.assertEqual(task.ignored, [])
        self.assertEqual(task.matched, 3)
        split = next(o for o in task.changed if o.key == "70001")
        self.assertEqual(split.pieces, ["502.cgxml"])
        # 200 + 200 mp in the same outline: no area or boundary change, only the split
        self.assertEqual(split.changes, [])
        self.assertIsNone(split.moved)
        self.assertEqual(task.split, 1)
        result = cc.result_features(task.changed, "imobile",
                                    cgxml_columns.default_layout("imobile"), True)
        fields, features = result.fields, result.features
        feature = next(f for f in features if f["ie"] == 70001)
        self.assertAlmostEqual(feature.geometry().area(), 400.0, places=6)
        # a field holds one ID, so the IDs of the pieces are in the description
        self.assertEqual(feature["modificari"], "dezmembrat în 2 imobile: 501; 502")
        self.assertFalse(feature["id"])
        from qgis.core import QgsMemoryProviderUtils  # type: ignore
        layer = QgsMemoryProviderUtils.createMemoryLayer("m", fields, cc.WKB_TYPE)
        self.assertTrue(layer.dataProvider().addFeatures(features)[0],
                        layer.dataProvider().errors())
        self.assertEqual(layer.featureCount(), len(features))
        _names, rows = xlsx.read_rows(task.excel_path, "Modificate")
        self.assertIn(["70001", "Dezmembrat", "70001.cgxml", "501.cgxml, 502.cgxml",
                       "Dezmembrat în", None, "501.cgxml, 502.cgxml"], [r[:7] for r in rows])
        self.assertIn(("   Dezmembrat", 1), task.summary_rows())

    def test_an_eterra_imobil_without_coordinates_has_no_boundary_to_compare(self):
        task = self.run_task()
        self.assertNotIn("70002", [o.key for o in task.changed])
        self.assertEqual(task.old_without_geometry, 1)

    def test_a_value_only_one_side_fills_is_no_change(self):
        # eTerra has the deed area and no fence; the delivery the other way round, as on
        # real data, where some eTerra imobile do have a fence
        ring = square(500300, 400000, 20)
        fenced = square(500100, 400000, 20)
        synthetic.write(self.eterra, "70003.cgxml", Land(rings=[fenced], ie="70003",
                                                        enclosed=True, style="eterra"))
        synthetic.write(self.livrare, "504.cgxml", delivery(504, [fenced], ie="70003",
                                                            enclosed=True))
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[ring], ie="70004",
                                                        legal_area=500.0, style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [ring], ie="70004",
                                                            enclosed=False))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual(task.one_sided, {"imprej": 1, "sup_acte": 1})
        self.assertIn("Împrejmuit la 1 imobil", " ".join(task.notes()))
        # a value both sides have still counts
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [ring], ie="70004",
                                                            enclosed=False, legal_area=450.0))
        changed = {o.key: o for o in self.run_task().changed}
        self.assertEqual([c[0] for c in changed["70004"].changes], ["sup_acte"])

    def test_a_zero_in_the_eterra_export_is_no_value(self):
        # eTerra writes 0 for an area or a number of levels it does not have
        ring, house = square(500300, 400000, 20), square(500305, 400005, 8)
        synthetic.write(self.eterra, "70004.cgxml", Land(
            rings=[ring], ie="70004", measured_area=0, legal_area=0, style="eterra",
            buildings=[Building(1, [house], total_area=0, levels=0)]))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", legal_area=420.0,
            buildings=[Building(1, [house], total_area=64.0, levels=1)]))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual(task.buildings_changed, [])
        self.assertEqual(task.one_sided["sup_mas"], 1)
        self.assertEqual(task.one_sided_buildings, {"sup_tot": 1, "etaje": 1})
        self.assertIn("0 la o suprafață", " ".join(task.notes()))
        # no deed area but 0 in the whole export: the column is not compared
        self.assertIn("sup_acte", task.skipped_columns)
        # a zero in the delivery is a value
        synthetic.write(self.eterra, "70004.cgxml", Land(
            rings=[ring], ie="70004", style="eterra",
            buildings=[Building(1, [house], total_area=54.5)]))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", buildings=[Building(1, [house], total_area=0)]))
        task = self.run_task()
        self.assertEqual([o.changes for o in task.buildings_changed], [[("sup_tot", 54.5, 0.0)]])

    def test_a_dash_for_a_number_in_the_eterra_export_is_no_value(self):
        ring, known = square(500300, 400000, 20), square(500100, 400000, 20)
        synthetic.write(self.eterra, "70003.cgxml", Land(rings=[known], ie="70003", plot="12",
                                                        style="eterra"))
        synthetic.write(self.livrare, "504.cgxml", delivery(504, [known], ie="70003", plot="12"))
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[ring], ie="70004", plot="-",
                                                        style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [ring], ie="70004", plot="59"))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual(task.one_sided["nr_tarla"], 1)

    def test_an_area_rounded_to_whole_square_metres_is_no_change(self):
        # eTerra keeps the decimals, a delivery writes whole square metres
        ring, house = square(500300, 400000, 20), square(500305, 400005, 8)
        synthetic.write(self.eterra, "70004.cgxml", Land(
            rings=[ring], ie="70004", legal_area=1250.37, style="eterra",
            buildings=[Building(1, [house], total_area=96.6)]))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", legal_area=1250.0,
            buildings=[Building(1, [house], total_area=97.0)]))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual(task.buildings_changed, [])
        self.assertIn("rotunjirea la metru pătrat", " ".join(task.notes()))
        # a whole square metre more is a change
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", legal_area=1251.0,
            buildings=[Building(1, [house], total_area=98.0)]))
        task = self.run_task()
        changed = {o.key: o for o in task.changed}
        self.assertEqual(changed["70004"].changes, [("sup_acte", 1250.37, 1251.0)])
        self.assertEqual([o.changes for o in task.buildings_changed], [[("sup_tot", 96.6, 98.0)]])

    def test_a_boundary_moved_within_the_precision_of_the_coordinates_is_no_change(self):
        # 4 mm along the sides of a 200 m square move 1.6 mp, over the area tolerance
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[square(501000, 400000, 200)],
                                                        ie="70004", style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [square(501000.004, 400000, 200)], ie="70004"))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertIn("Limitele mutate cu cel mult 1 cm, cât este precizia coordonatelor, nu "
                      "s-au considerat modificări: 1 imobil.", task.notes())
        # between two deliveries every move over the tolerance counts
        both = {o.key: o for o in self.run_task(mode=cc.FILENAME, excel_path=None).changed}
        self.assertIsNotNone(both["505"].moved)
        # and 3 cm is a move
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [square(501000.03, 400000, 200)], ie="70004"))
        changed = {o.key: o for o in self.run_task().changed}
        self.assertIsNotNone(changed["70004"].moved)

    def test_an_address_part_only_one_side_names_is_no_change(self):
        # UAT Bod has the villages Bod and Colonia Bod; eTerra often names the village without
        # the street, the delivery the street and number without the village
        ring = square(500300, 400000, 20)
        synthetic.write(self.eterra, "70004.cgxml", Land(
            rings=[ring], ie="70004", sirsup="40606", siruta="40624", style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", sirsup="40606", siruta="40606", street="Morii",
            postal_number="7"))
        task = self.run_task()
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual(task.one_sided["adresa"], 1)

        def compare(siruta, number):
            synthetic.write(self.eterra, "70004.cgxml", Land(
                rings=[ring], ie="70004", sirsup="40606", siruta="40624", street="Morii",
                postal_number="7", style="eterra"))
            synthetic.write(self.livrare, "505.cgxml", delivery(
                505, [ring], ie="70004", sirsup="40606", siruta=siruta, street="Morii",
                postal_number=number))
            return {o.key: o.changes for o in self.run_task().changed}.get("70004")

        # FN, fără număr, is no number
        self.assertIsNone(compare("40624", "FN"))
        self.assertEqual(compare("40624", "9"), [(
            "adresa", "UAT Bod, loc. Colonia Bod, Strada Morii, Nr. 7",
            "UAT Bod, loc. Colonia Bod, Strada Morii, Nr. 9")])
        # the village named like its UAT is named too, or the change would read as one only
        # eTerra fills
        self.assertEqual(compare("40615", "7"), [(
            "adresa", "UAT Bod, loc. Colonia Bod, Strada Morii, Nr. 7",
            "UAT Bod, loc. Bod, Strada Morii, Nr. 7")])

    def test_columns_the_export_never_fills_are_not_compared(self):
        task = self.run_task()
        self.assertNotIn("70003", [o.key for o in task.changed])
        self.assertIn("sector", task.skipped_columns)
        # between two deliveries the sector is compared as usual
        both = self.run_task(old_paths=[self.livrare], mode=cc.FILENAME, excel_path=None)
        self.assertEqual(both.skipped_columns, [])


def write_edited(folder, name, land, old, new):
    """land's CGXML with the first old text replaced by new, for data the writer does not
    produce."""
    os.makedirs(folder, exist_ok=True)
    data = synthetic.cgxml_bytes(land)
    assert old.encode() in data, old
    with open(os.path.join(folder, name), "wb") as fp:
        fp.write(data.replace(old.encode(), new.encode(), 1))


def summary_section(path, title):
    """The rows of one section of the Sumar sheet."""
    _names, rows = xlsx.read_rows(path, "Sumar")
    rows = [r for r in rows if r and any(v not in (None, "") for v in r)]
    start = next(i for i, r in enumerate(rows)
                 if r[0] == title and all(v in (None, "") for v in r[1:]))
    end = next((i for i in range(start + 1, len(rows))
                if all(v in (None, "") for v in rows[i][1:]) and rows[i][0] != "Categorie"
                and not str(rows[i][0]).startswith(" ")), len(rows))
    return rows[start + 1:end]


class CompareEnginePairingTests(unittest.TestCase):

    def pair(self, old, new, mode=cc.FILENAME):
        return cc.pair_sources(cc.index_source(old, mode, OLD), cc.index_source(new, mode, NEW))

    def test_renamed_pieces_that_share_an_ie_pair_by_their_cadastral_number(self):
        # two pieces of one eTerra imobil keep its IE in both deliveries; v2 renamed the files
        pairs, missing = self.pair(
            [entry("13022.cgxml", cadgenno="13022", ie="70001"),
             entry("9001.cgxml", cadgenno="9001", ie="70001")],
            [entry("Sector_5__13022.cgxml", cadgenno="13022", ie="70001"),
             entry("Sector_3__9001.cgxml", cadgenno="9001", ie="70001")])
        self.assertEqual(sorted((p.older.file_name, p.newer.file_name, p.matched_by)
                                for p in pairs),
                         [("13022.cgxml", "Sector_5__13022.cgxml", "cadgenno"),
                          ("9001.cgxml", "Sector_3__9001.cgxml", "cadgenno")])
        self.assertEqual(missing, [])

    def test_no_renamed_file_is_paired_by_an_ie_that_several_files_share(self):
        pairs, missing = self.pair([entry("a.cgxml", ie="500"), entry("b.cgxml", ie="500")],
                                   [entry("c.cgxml", ie="500")])
        self.assertEqual(pairs, [])
        self.assertEqual(sorted(e.file_name for _s, _k, e in missing),
                         ["a.cgxml", "b.cgxml", "c.cgxml"])
        pairs, _missing = self.pair([entry("a.cgxml", ie="500")],
                                    [entry("c.cgxml", ie="500"), entry("d.cgxml", ie="500")])
        self.assertEqual(pairs, [])


class CompareEngineDeliveryTests(unittest.TestCase):
    """Two deliveries compared by file name."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_engine_")
        self.old = os.path.join(self.tmp, "v1")
        self.new = os.path.join(self.tmp, "v2")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, **overrides):
        options = dict(old_paths=[self.old], new_paths=[self.new],
                       excel_path=os.path.join(self.tmp, "comparatie.xlsx"))
        options.update(overrides)
        task = cc.CgxmlCompareTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_an_emptied_rounded_or_zero_value_between_deliveries_is_a_change(self):
        for folder, legal, rounded, measured in ((self.old, 500.0, 1250.37, 0),
                                                 (self.new, None, 1250.0, 400.0)):
            synthetic.write(folder, "10001.cgxml", delivery(
                10001, [square(500000, 400000, 20)], legal_area=legal))
            synthetic.write(folder, "10002.cgxml", delivery(
                10002, [square(500100, 400000, 20)], legal_area=rounded))
            synthetic.write(folder, "10003.cgxml", delivery(
                10003, [square(500200, 400000, 20)], measured_area=measured))
        task = self.run_task(columns=["sup_mas", "sup_acte"], compare_geometry=False)
        self.assertEqual({o.key: o.changes for o in task.changed}, {
            "10001": [("sup_acte", 500.0, None)],
            "10002": [("sup_acte", 1250.37, 1250.0)],
            "10003": [("sup_mas", 0.0, 400.0)]})
        self.assertEqual((task.one_sided, task.rounded), ({}, {}))

    def test_files_are_named_with_their_folder(self):
        ring = square(500000, 400000, 20)
        synthetic.write(self.old, "9001.cgxml", delivery(9001, [ring]))
        for sector in ("Sector_3", "Sector_4"):
            synthetic.write(os.path.join(self.new, sector), "9001.cgxml", delivery(9001, [ring]))
        with open(os.path.join(self.new, "Sector_4", "stricat.cgxml"), "w",
                  encoding="utf-8") as fp:
            fp.write("<CGXML><Land>")
        task = self.run_task(excel_path=None)
        kept = os.path.join("Sector_3", "9001.cgxml")
        reasons = {i.file_name: i.reason for i in task.ignored}
        self.assertEqual(reasons[os.path.join("Sector_4", "9001.cgxml")],
                         f"Același nume ca '{kept}'; s-a comparat doar acela.")
        self.assertIn(os.path.join("Sector_4", "stricat.cgxml"), reasons)
        self.assertEqual(len(reasons), 2)

    def test_a_construction_number_repeated_in_a_file_counts_on_both_sides(self):
        ring = square(500000, 400000, 40)
        houses = [Building(1, [square(500002, 400002, 5)]),
                  Building(2, [square(500010, 400002, 5)])]
        write_edited(self.old, "10001.cgxml", delivery(10001, [ring], buildings=houses),
                     "<BUILDNO>2</BUILDNO>", "<BUILDNO>1</BUILDNO>")
        synthetic.write(self.new, "10001.cgxml", delivery(10001, [ring], buildings=houses[:1]))
        task = self.run_task(excel_path=None)
        self.assertEqual([(o.imobil, o.key) for o in task.buildings_deleted],
                         [("10001", "C1 (2)")])
        self.assertEqual(task.buildings_unchanged, 1)
        self.assertIn((OLD, "10001.cgxml", "Construcția C1 apare de 2 ori în fișier; s-au "
                       "comparat ca 'C1' și 'C1 (2)'."), task.problems)
        swapped = self.run_task(old_paths=[self.new], new_paths=[self.old], excel_path=None)
        self.assertEqual([(o.imobil, o.key) for o in swapped.buildings_added],
                         [("10001", "C1 (2)")])

    def test_the_report_records_what_was_compared_of_the_constructions(self):
        ring = square(500000, 400000, 40)
        synthetic.write(self.old, "10001.cgxml", delivery(
            10001, [ring], buildings=[Building(1, [square(500002, 400002, 5)])]))
        synthetic.write(self.new, "10001.cgxml", delivery(
            10001, [ring], buildings=[Building(1, [square(500003, 400002, 5)])]))
        task = self.run_task(compare_geometry=False, building_columns=[])
        _names, rows = xlsx.read_rows(task.excel_path, "Sumar")
        facts = {r[0]: r[1] for r in rows if r and len(r) > 1}
        self.assertEqual(facts["Construcții comparate"],
                         "Da: doar construcțiile apărute și dispărute")
        task = self.run_task(building_columns=[])
        _names, rows = xlsx.read_rows(task.excel_path, "Sumar")
        facts = {r[0]: r[1] for r in rows if r and len(r) > 1}
        self.assertEqual(facts["Construcții comparate"], "Da: doar geometria")
        # the kinds of change of the construcții, as for the imobile
        self.assertIn(["   Doar limita", 1],
                      [r[:2] for r in summary_section(task.excel_path, "Construcții")])

    def test_a_point_that_cannot_be_read_leaves_the_boundary_uncompared(self):
        ring = square(500000, 400000, 20)
        synthetic.write(self.old, "10001.cgxml", delivery(10001, [ring]))
        write_edited(self.new, "10001.cgxml", delivery(10001, [ring], owner="IONESCU ANA"),
                     "<X>500020.000</X>", "<X>5000z0.000</X>")
        task = self.run_task()
        changed = {o.key: o for o in task.changed}
        self.assertIsNone(changed["10001"].moved)
        self.assertEqual(cc.kind_of(changed["10001"]), "Limita necomparată")
        self.assertEqual([(side, name) for side, name, _text in task.problems],
                         [(NEW, "10001.cgxml")])
        self.assertIn("coordonate care nu sunt numere", task.problems[0][2])
        self.assertTrue(any("'10001.cgxml'" in note for note in task.notes()), task.notes())
        _names, rows = xlsx.read_rows(task.excel_path, "Observații")
        self.assertEqual(rows[1][:2], [NEW, "10001.cgxml"])

    def test_points_that_close_no_surface_leave_the_boundary_uncompared(self):
        from unittest import mock
        ring, house = square(500000, 400000, 20), square(500002, 400002, 5)
        synthetic.write(self.old, "10001.cgxml", delivery(10001, [ring]))
        synthetic.write(self.old, "10002.cgxml", delivery(
            10002, [ring], buildings=[Building(1, [house])]))
        # two points of an outline
        synthetic.write(self.new, "10001.cgxml", delivery(10001, [ring[:2]], owner="IONESCU ANA"))
        synthetic.write(self.new, "10002.cgxml", delivery(
            10002, [ring], buildings=[Building(1, [house[:2]], destination="CA")]))
        read = cgxml_reader.parse_bytes

        def read_with_remarks(data, source=""):
            imobile, constructii, remarks = read(data, source)
            # the reader's remark on points that close no surface
            if not any(remark.endswith("nu a fost construită") for remark in remarks):
                for records, entity, pronoun in ((imobile, "imobilului", "lui"),
                                                 (constructii, "construcției", "ei")):
                    remarks += [f"punctele {entity} nu închid o suprafață, deci geometria "
                                f"{pronoun} nu a fost construită"
                                for record in records if not record["rings"]]
            return imobile, constructii, remarks

        with mock.patch.object(cgxml_reader, "parse_bytes", read_with_remarks):
            task = self.run_task()
        changed = {o.key: o for o in task.changed}
        self.assertIsNone(changed["10001"].moved)
        self.assertEqual(cc.kind_of(changed["10001"]), "Limita necomparată")
        self.assertEqual([(o.key, o.moved, cc.kind_of(o)) for o in task.buildings_changed],
                         [("C1", None, "Limita necomparată")])
        self.assertIn("Imobile sau construcții fără geometrie, care nu a putut fi construită din "
                      "punctele lor, în 2 fișiere: '10001.cgxml' din sursa nouă, '10002.cgxml' "
                      "din sursa nouă. Limita lor nu s-a comparat.", task.notes())


class CompareEngineEterraTests(unittest.TestCase):
    """An eTerra export, the old source, against a delivery."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_engine_ie_")
        self.eterra = os.path.join(self.tmp, "eterra")
        self.livrare = os.path.join(self.tmp, "livrare")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def run_task(self, **overrides):
        options = dict(old_paths=[self.eterra], new_paths=[self.livrare], mode=cc.IE,
                       excel_path=os.path.join(self.tmp, "comparatie.xlsx"))
        options.update(overrides)
        task = cc.CgxmlCompareTask(**options)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def split(self, legal_areas, eterra_legal=400.0):
        """70001, of 400 mp, split into 501 and 502 with these deed areas."""
        synthetic.write(self.eterra, "70001.cgxml", Land(rings=[square(500000, 400000, 20)],
                                                        ie="70001", legal_area=eterra_legal,
                                                        style="eterra"))
        for side, (number, legal) in enumerate(zip((501, 502), legal_areas)):
            synthetic.write(self.livrare, f"{number}.cgxml", delivery(
                number, [half(500000, side)], ie="70001", legal_area=legal))
        return next(o for o in self.run_task().changed if o.key == "70001")

    def test_split_pieces_that_repeat_the_deed_area_keep_it(self):
        split = self.split((400.0, 400.0))
        self.assertEqual(split.changes, [])
        self.assertEqual(split.record["sup_acte"], 400.0)
        # 200 + 200 of a deed of 400 add up
        split = self.split((200.0, 200.0))
        self.assertEqual(split.changes, [])
        self.assertEqual(split.record["sup_acte"], 400.0)
        self.assertEqual(self.split((150.0, 200.0)).changes, [("sup_acte", 400.0, 350.0)])

    def test_a_construction_without_coordinates_in_eterra_has_no_boundary_to_compare(self):
        ring = square(500300, 400000, 20)
        synthetic.write(self.eterra, "70004.cgxml", Land(
            rings=[ring], ie="70004", style="eterra",
            buildings=[Building(1, []), Building(2, [], destination="CA")]))
        synthetic.write(self.livrare, "505.cgxml", delivery(
            505, [ring], ie="70004", buildings=[Building(1, [square(500305, 400005, 8)]),
                                                Building(2, [square(500301, 400001, 3)])]))
        task = self.run_task()
        self.assertEqual([(o.key, cc.kind_of(o)) for o in task.buildings_changed],
                         [("C2", "Limita necomparată")])
        self.assertIsNone(task.buildings_changed[0].moved)
        self.assertEqual(task.buildings_unchanged, 1)
        self.assertIn("2 construcții din exportul eTerra nu au coordonate, deci limita lor nu "
                      "s-a comparat.", task.notes())

    def test_an_imobil_without_coordinates_in_eterra_has_its_own_kind_of_change(self):
        synthetic.write(self.eterra, "70002.cgxml", Land(rings=[], measured_area=380.0,
                                                        ie="70002", style="eterra"))
        synthetic.write(self.livrare, "503.cgxml", delivery(503, [square(500050, 400000, 20)],
                                                            ie="70002"))
        task = self.run_task()
        changed = {o.key: o for o in task.changed}
        self.assertEqual(changed["70002"].changes, [("sup_mas", 380.0, 400.0)])
        self.assertEqual(cc.kind_of(changed["70002"]), "Limita necomparată")
        self.assertIn(("   Limita necomparată", 1), task.summary_rows())
        self.assertIn("Limita necomparată", dict(
            (value, color) for value, _label, color in compare_service.change_categories()))
        result = cc.result_features(task.changed, "imobile",
                                    cgxml_columns.default_layout("imobile"), True, kinds=True)
        self.assertEqual([f["tip_modif"] for f in result.features], ["Limita necomparată"])

    def test_an_eterra_point_that_cannot_be_read_is_not_called_a_missing_coordinate(self):
        ring = square(500300, 400000, 20)
        write_edited(self.eterra, "70004.cgxml", Land(rings=[ring], ie="70004", style="eterra"),
                     "<X>500320.000</X>", "<X>5003z0.000</X>")
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [ring], ie="70004"))
        task = self.run_task()
        self.assertEqual(task.old_without_geometry, 0)
        self.assertNotIn("70004", [o.key for o in task.changed])
        self.assertEqual([n for n in task.notes() if "coordonate" in n], [])
        self.assertIn("Imobile sau construcții fără geometrie, care nu a putut fi construită din "
                      "punctele lor, în 1 fișier: '70004.cgxml' din sursa veche. Limita lor nu "
                      "s-a comparat.", task.notes())

    def test_the_use_category_na_of_eterra_is_compared(self):
        ring = square(500300, 400000, 20)
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[ring], ie="70004",
                                                        parcels=(("NA", True),), style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [ring], ie="70004",
                                                            parcels=(("A", True),)))
        task = self.run_task()
        self.assertEqual({o.key: o.changes for o in task.changed},
                         {"70004": [("categ_fol", "NA", "A")]})

    def test_the_eterra_export_can_be_either_source(self):
        # 70001 split into 501 and 502, 70004 only in eTerra, 505 a new imobil without IE,
        # 70005 with a moved boundary
        self.split((None, None))
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[square(500300, 400000, 20)],
                                                        ie="70004", style="eterra"))
        synthetic.write(self.livrare, "505.cgxml", delivery(505, [square(500400, 400000, 20)],
                                                            is_new=True))
        synthetic.write(self.eterra, "70005.cgxml", Land(rings=[square(500500, 400000, 20)],
                                                        ie="70005", style="eterra"))
        synthetic.write(self.livrare, "506.cgxml", delivery(506, [square(500500, 400000, 21)],
                                                            ie="70005"))
        straight = self.run_task()
        report = os.path.join(self.tmp, "invers.xlsx")
        swapped = self.run_task(old_paths=[self.livrare], new_paths=[self.eterra],
                                excel_path=report)
        self.assertEqual(swapped.ignored, [])
        self.assertEqual((swapped.matched, swapped.split, swapped.unchanged),
                         (straight.matched, straight.split, straight.unchanged))
        self.assertEqual([o.key for o in straight.deleted], ["IE 70004"])
        self.assertEqual([o.key for o in swapped.added], ["IE 70004"])
        self.assertEqual([o.file_new for o in swapped.added], ["70004.cgxml"])
        self.assertEqual([o.file_old for o in swapped.deleted], ["505.cgxml"])
        # every change reads from the old source to the new one
        self.assertEqual(
            {o.key: ([(c, after, before) for c, before, after in o.changes],
                     o.moved and (o.moved[1], o.moved[0], o.moved[2])) for o in straight.changed},
            {o.key: (o.changes, o.moved) for o in swapped.changed})
        split = next(o for o in swapped.changed if o.key == "70001")
        self.assertEqual((split.file_old, split.pieces, split.file_new),
                         ("501.cgxml", ["502.cgxml"], "70001.cgxml"))
        # the result layer shows the new source's imobil: the eTerra one, without a sector
        self.assertIsNone(split.record.get("sector"))
        rows = dict(swapped.summary_rows())
        self.assertEqual(rows["Fișiere din sursa veche în plus, la imobilele dezmembrate"], 1)
        self.assertFalse(any("invers" in note for note in swapped.notes()), swapped.notes())
        _names, detail = xlsx.read_rows(report, "Modificate")
        self.assertIn(["70001", "Dezmembrat", "501.cgxml, 502.cgxml", "70001.cgxml",
                       "Dezmembrat în", "501.cgxml, 502.cgxml", None, None], detail)

    def test_split_pieces_beyond_the_first_are_counted_with_the_new_files(self):
        self.split((None, None))
        synthetic.write(self.livrare, "504.cgxml", delivery(504, [square(500100, 400000, 20)],
                                                            is_new=True))
        rows = dict(self.run_task().summary_rows())
        self.assertEqual(rows["Fișiere din sursa nouă"], 3)
        self.assertEqual(rows["Imobile în ambele surse"], 1)
        self.assertEqual(rows["Fișiere din sursa nouă în plus, la imobilele dezmembrate"], 1)
        self.assertEqual(rows["Imobile apărute"], 1)

    def test_a_split_imobil_whose_pieces_moved_within_the_precision_is_not_moved(self):
        x, y, shift = 501000, 400000, 0.004
        synthetic.write(self.eterra, "70004.cgxml", Land(rings=[square(x, y, 200)], ie="70004",
                                                        style="eterra"))
        # the split line bends, so it has a point inside the old outline
        left = [(x, y), (x + 100, y), (x + 120, y + 100), (x + 100, y + 200), (x, y + 200)]
        right = [(x + 100, y), (x + 200, y), (x + 200, y + 200), (x + 100, y + 200),
                 (x + 120, y + 100)]
        for number, ring in ((505, left), (506, right)):
            synthetic.write(self.livrare, f"{number}.cgxml", delivery(
                number, [[(px + shift, py) for px, py in ring]], ie="70004"))
        task = self.run_task()
        split = next(o for o in task.changed if o.key == "70004")
        self.assertIsNone(split.moved)
        self.assertEqual(task.within_precision["imobile"], 1)

    def test_constructions_name_their_imobil_by_ie_or_id(self):
        self.split((None, None))
        synthetic.write(self.livrare, "502.cgxml", delivery(
            502, [half(500000, 1)], ie="70001",
            buildings=[Building(1, [square(500012, 400002, 3)])]))
        synthetic.write(self.eterra, "70009.cgxml", Land(
            rings=[square(500300, 400000, 20)], ie="70009", style="eterra",
            buildings=[Building(1, [square(500302, 400002, 5)])]))
        synthetic.write(self.livrare, "506.cgxml", delivery(
            506, [square(500500, 400000, 20)], is_new=True,
            buildings=[Building(1, [square(500502, 400002, 5)])]))
        synthetic.write(self.livrare, "507.cgxml", delivery(
            507, [square(500600, 400000, 20)], ie="70010",
            buildings=[Building(1, [square(500602, 400002, 5)])]))
        task = self.run_task(excel_path=None)
        self.assertEqual(sorted(o.imobil for o in task.buildings_added),
                         ["ID 506", "IE 70001", "IE 70010"])
        self.assertEqual([o.imobil for o in task.buildings_deleted], ["IE 70009"])


class CompareEngineReportTests(unittest.TestCase):
    """What the window and Processing receive besides the comparison itself."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_engine_rep_")
        self.old = os.path.join(self.tmp, "v1")
        self.new = os.path.join(self.tmp, "v2")
        house = [Building(1, [square(500002, 400002, 5)])]
        synthetic.write(self.old, "10001.cgxml", delivery(10001, [square(500000, 400000, 20)],
                                                          buildings=house))
        synthetic.write(self.new, "10001.cgxml", delivery(10001, [square(500000, 400000, 21)],
                                                          buildings=house))

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_a_report_that_cannot_be_written_keeps_the_comparison(self):
        task = cc.CgxmlCompareTask(old_paths=[self.old], new_paths=[self.new],
                                   excel_path=os.path.join(self.tmp, "lipsa", "raport.xlsx"))
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([o.key for o in task.changed], ["10001"])
        self.assertIn("'raport.xlsx' nu poate fi scris", task.report_error)

    def test_an_empty_source_stops_the_run_before_the_report(self):
        empty = os.path.join(self.tmp, "gol")
        os.makedirs(empty)
        excel = os.path.join(self.tmp, "raport.xlsx")
        task = cc.CgxmlCompareTask(old_paths=[empty], new_paths=[self.new], excel_path=excel)
        self.assertFalse(task.run())
        self.assertEqual(task.error_msg, "Nu s-a găsit niciun fișier .cgxml în sursa veche.")
        self.assertFalse(os.path.exists(excel))

    def test_the_counts_of_the_constructions(self):
        task = cc.CgxmlCompareTask(old_paths=[self.old], new_paths=[self.new])
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.building_summary_rows(), [
            ("Construcții apărute", 0), ("Construcții dispărute", 0),
            ("Construcții modificate", 0), ("   Doar limita", 0), ("   Doar datele", 0),
            ("   Limita și datele", 0), ("Construcții nemodificate", 1)])

    def test_records_left_out_of_the_result_features_are_counted(self):
        eterra = os.path.join(self.tmp, "eterra")
        synthetic.write(eterra, "70001.cgxml", Land(rings=[], ie="70001", style="eterra"))
        synthetic.write(eterra, "70002.cgxml", Land(rings=[square(500100, 400000, 20)],
                                                   ie="70002", style="eterra"))
        task = cc.CgxmlCompareTask(old_paths=[eterra], new_paths=[self.new], mode=cc.IE)
        self.assertTrue(task.run(), task.error_msg)
        result = cc.result_features(task.deleted, "imobile",
                                    cgxml_columns.default_layout("imobile"))
        self.assertEqual((len(result.features), result.left_out), (1, 1))
        self.assertEqual(cc.left_out_note([("Imobile dispărute", 1), ("Imobile apărute", 0)]),
                         "Înregistrările fără coordonate nu apar în layerele rezultat: 1 la "
                         "'Imobile dispărute'.")
        # a conversion layout that names a column like the kind of change
        layout = cgxml_columns.default_layout("imobile")
        layout[-1]["name"] = "tip_modif"
        result = cc.result_features(task.changed, "imobile", layout, True, kinds=True)
        self.assertEqual(result.kind_field, "tip_modif_2")


class UnreadableSourceCompareTests(unittest.TestCase):
    """A comparison with a damaged archive among the files of one side."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_unread_")
        self.new = os.path.join(self.tmp, "nou")
        synthetic.write(self.new, "10001.cgxml", delivery(10001, [square(500000, 400000, 20)]))
        self.damaged = os.path.join(self.tmp, "vechi.zip")
        with open(self.damaged, "wb") as fp:
            fp.write(b"nu este o arhiva")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_a_damaged_archive_stops_the_comparison_instead_of_reporting_its_imobile(self):
        for old_paths in ([self.damaged], [self.damaged, self.new]):
            task = cc.CgxmlCompareTask(old_paths=old_paths, new_paths=[self.new])
            self.assertFalse(task.run())
            self.assertTrue(task.error_msg.startswith(
                "Sursa veche nu poate fi citită în întregime: 'vechi.zip' (arhiva ZIP este "
                "deteriorată sau incompletă)."), task.error_msg)
            self.assertEqual(task.added, [])


class CompareEngineUnbuiltTests(unittest.TestCase):
    """IE mode. In the eTerra file of 70004, a point of C1 is not a number and C2 has no
    points at all; in the delivery, C2 has another destination."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_cmp_unbuilt_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_a_construction_without_points_is_told_apart_from_one_that_was_not_built(self):
        eterra, livrare = (os.path.join(self.tmp, side) for side in ("eterra", "livrare"))
        ring = square(500300, 400000, 20)
        data = synthetic.cgxml_bytes(Land(
            rings=[ring], ie="70004", style="eterra",
            buildings=[Building(1, [square(500305, 400005, 8)]), Building(2, [])]))
        # eTerra writes millimetres; this is the first X of C1
        self.assertIn(b"<X>500305.000</X>", data)
        os.makedirs(eterra)
        with open(os.path.join(eterra, "70004.cgxml"), "wb") as fp:
            fp.write(data.replace(b"<X>500305.000</X>", b"<X>5003z5.000</X>", 1))
        synthetic.write(livrare, "505.cgxml", delivery(
            505, [ring], ie="70004",
            buildings=[Building(1, [square(500305, 400005, 8)]),
                       Building(2, [square(500301, 400001, 3)], destination="CA")]))
        task = cc.CgxmlCompareTask(old_paths=[eterra], new_paths=[livrare], mode=cc.IE)
        self.assertTrue(task.run(), task.error_msg)
        notes = task.notes()
        self.assertIn("1 construcție din exportul eTerra nu are coordonate, deci limita ei nu "
                      "s-a comparat.", notes)
        # C1, whose point could not be read, is still named as not built
        self.assertTrue(any(note.startswith("Imobile sau construcții fără geometrie")
                            and "'70004.cgxml'" in note for note in notes), notes)


class SirutaCompareTests(unittest.TestCase):

    NOTE = ("Baza de date SIRUTA a pluginului nu a putut fi citită, deci denumirile de UAT, "
            "județ și localitate lipsesc din rezultat.")

    def setUp(self):
        from cadex.services import siruta
        tmp = tempfile.mkdtemp(prefix="cadex_cmp_siruta_")
        self.addCleanup(helpers.remove_folder, tmp)
        self.old, self.new = os.path.join(tmp, "v1"), os.path.join(tmp, "v2")
        synthetic.write(self.old, "10001.cgxml",
                        delivery(10001, [square(500000, 400000, 20)]))
        synthetic.write(self.new, "10001.cgxml",
                        delivery(10001, [square(500000, 400000, 21)]))
        self.addCleanup(setattr, siruta, "_problem", "")
        self.addCleanup(setattr, siruta, "_tables", None)
        siruta._tables = None

    def compare(self, readable=True):
        """A comparison run; with readable False, opening the SIRUTA database fails as when
        the plugin folder denies access to it."""
        import sqlite3
        from contextlib import nullcontext
        from unittest import mock
        from cadex.services import siruta
        refused = sqlite3.OperationalError("unable to open database file")
        task = cc.CgxmlCompareTask(old_paths=[self.old], new_paths=[self.new])
        with nullcontext() if readable else mock.patch.object(
                siruta.sqlite3, "connect", side_effect=refused):
            self.assertTrue(task.run(), task.error_msg)
        return task

    def test_a_comparison_without_the_siruta_database_completes_with_a_note(self):
        task = self.compare(readable=False)
        self.assertEqual([o.key for o in task.changed], ["10001"])
        self.assertIn(self.NOTE, task.notes())

    def test_the_next_comparison_has_the_names_once_the_database_can_be_read_again(self):
        self.assertEqual(self.compare(readable=False).changed[0].record["uat"], "")
        task = self.compare()
        self.assertNotEqual(task.changed[0].record["uat"], "")
        self.assertNotIn(self.NOTE, task.notes())


def tearDownModule():
    helpers.stop_qgis()


if __name__ == "__main__":
    helpers.main()
