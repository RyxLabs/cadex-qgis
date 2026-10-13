# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer and field name matching. No QgsApplication."""

import unittest

import helpers  # noqa: F401  sys.path bootstrap

from cadex.services import roles


class FoldTests(unittest.TestCase):

    def test_both_comma_and_cedilla_forms_fold_the_same(self):
        # ș/ş and ț/ţ are different code points, Romanian data mixes them inside one file
        self.assertEqual(roles.fold("Construcții"), "constructii")
        self.assertEqual(roles.fold("Construcţii"), "constructii")
        self.assertEqual(roles.fold("ȘOSEAUA"), roles.fold("ŞOSEAUA"))
        self.assertEqual(roles.fold("Împrejmuit"), "imprejmuit")

    def test_normalize_collapses_separators(self):
        for text in ("Suprafata act", "suprafata_act", "SUPRAFAȚA-ACT", " suprafata  act "):
            self.assertEqual(roles.normalize(text), "suprafata_act")

    def test_parse_patterns_drops_blanks_and_duplicates(self):
        self.assertEqual(roles.parse_patterns("imobil, , Imobil; teren\nlot*"),
                         ["imobil", "teren", "lot*"])
        self.assertEqual(roles.parse_patterns(""), [])
        self.assertEqual(roles.parse_patterns(None), [])


class LayerMatchTests(unittest.TestCase):

    def test_fragment_anywhere_in_the_name(self):
        patterns = roles.parse_patterns("imobil, teren")
        self.assertTrue(roles.layer_matches("Imobile_Cristian_S3", patterns))
        self.assertTrue(roles.layer_matches("TEREN livrare", patterns))
        self.assertFalse(roles.layer_matches("Constructii", patterns))

    def test_diacritics_in_the_layer_name(self):
        self.assertTrue(roles.layer_matches(
            "Construcţii sector 2", roles.parse_patterns("constructi")))

    def test_wildcard_matches_the_whole_name(self):
        patterns = roles.parse_patterns("s*_imob*")
        self.assertTrue(roles.layer_matches("S3_imobile", patterns))
        self.assertFalse(roles.layer_matches("imobile_S3", patterns))

    def test_no_patterns_match_nothing(self):
        self.assertFalse(roles.layer_matches("Imobile", []))


class PickFieldTests(unittest.TestCase):

    def test_a_whole_name_match_comes_before_a_fragment_match(self):
        fields = ["suprafata_ie", "IE", "id"]
        self.assertEqual(roles.pick_field(fields, roles.parse_patterns("ie")), "IE")

    def test_short_pattern_never_matches_as_fragment(self):
        # as a fragment, "ie" would match any field name that contains those two letters
        self.assertIsNone(roles.pick_field(["suprafata_intravilan_ie"],
                                           roles.parse_patterns("ie")))

    def test_long_pattern_matches_as_fragment(self):
        self.assertEqual(roles.pick_field(["NUME_PROPRIETAR"],
                                          roles.parse_patterns("proprietar")),
                         "NUME_PROPRIETAR")

    def test_pattern_order_is_priority(self):
        fields = ["nr_cad", "id"]
        self.assertEqual(roles.pick_field(fields, roles.parse_patterns("id, nr_cad")), "id")
        self.assertEqual(roles.pick_field(fields, roles.parse_patterns("nr_cad, id")), "nr_cad")

    def test_spaces_and_case_in_excel_headers(self):
        self.assertEqual(roles.pick_field(["Nr. crt", "Suprafata act (mp)"],
                                          roles.parse_patterns("suprafata act")),
                         "Suprafata act (mp)")

    def test_nothing_matches(self):
        self.assertIsNone(roles.pick_field(["a", "b"], roles.parse_patterns("proprietar")))


if __name__ == "__main__":
    unittest.main()
