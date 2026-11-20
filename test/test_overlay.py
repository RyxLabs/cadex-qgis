# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Polygon overlays on shapes whose edges run a fraction of a millimetre apart, as the
imobile of a sector and its outline do."""

import unittest

import helpers

helpers.start_qgis(gui=False)

from qgis.core import QgsGeometry  # noqa: E402  # type: ignore

from cadex.services import overlay  # noqa: E402

square = helpers.square

# an imobil of 1,000 mp turned 17 degrees, coordinates rounded to the millimetre
IMOBIL = ("POLYGON((450134.616 351269.844, 450153.742 351275.691, 450139.123 351323.507, "
          "450119.997 351317.659, 450134.616 351269.844))")
# the ground around it, the imobil included. Along the imobil's western edge runs a slit a
# third of a millimetre wide at most: the neighbour's corner 450127.306 351293.752 lies that
# far from the edge, which has no point there
AROUND = ("POLYGON((450134.616 351269.844, 450122.23166050669 351310.34999855474, "
          "450127.306 351293.752, 450136.14417241089 351264.844, 450114.997 351264.844, "
          "450114.997 351328.507, 450158.742 351328.507, 450158.742 351264.844, "
          "450136.14459981176 351264.844, 450134.616 351269.844))")


def wkt(text):
    geometry = QgsGeometry.fromWkt(text)
    assert not geometry.isNull(), text
    return geometry


class NearlyCoincidentEdgeTests(unittest.TestCase):

    def setUp(self):
        self.imobil, self.around = wkt(IMOBIL), wkt(AROUND)

    def test_an_imobil_inside_the_ground_around_it_intersects_it_whole(self):
        shared = overlay.intersection(self.imobil, self.around)
        self.assertAlmostEqual(shared.area(), self.imobil.area(), places=2)

    def test_the_intersection_is_the_same_whichever_shape_comes_first(self):
        shared = overlay.intersection(self.around, self.imobil)
        self.assertAlmostEqual(shared.area(), self.imobil.area(), places=2)

    def test_no_part_of_the_imobil_lies_outside_the_ground_around_it(self):
        outside = overlay.difference(self.imobil, self.around)
        self.assertLess(outside.area(), 0.01)

    def test_the_ground_without_the_imobil_is_smaller_by_its_area(self):
        rest = overlay.difference(self.around, self.imobil)
        self.assertAlmostEqual(rest.area(), self.around.area() - self.imobil.area(), places=2)

    def test_the_union_with_the_imobil_adds_no_area(self):
        both = overlay.union([self.around, self.imobil])
        self.assertAlmostEqual(both.area(), self.around.area(), places=2)

    def test_the_symmetric_difference_is_the_ground_without_the_imobil(self):
        apart = overlay.sym_difference(self.imobil, self.around)
        self.assertAlmostEqual(apart.area(), self.around.area() - self.imobil.area(), places=2)


class PlainShapeTests(unittest.TestCase):

    def test_overlapping_squares(self):
        a, b = wkt(square(0, 0, 10)), wkt(square(5, 0, 10))
        self.assertAlmostEqual(overlay.intersection(a, b).area(), 50.0, places=6)
        self.assertAlmostEqual(overlay.difference(a, b).area(), 50.0, places=6)
        self.assertAlmostEqual(overlay.sym_difference(a, b).area(), 100.0, places=6)
        self.assertAlmostEqual(overlay.union([a, b]).area(), 150.0, places=6)

    def test_squares_apart_share_nothing(self):
        a, b = wkt(square(0, 0, 10)), wkt(square(50, 0, 10))
        shared = overlay.intersection(a, b)
        self.assertTrue(shared.isEmpty())
        self.assertAlmostEqual(overlay.difference(a, b).area(), 100.0, places=6)

    def test_squares_sharing_an_edge_share_no_area(self):
        a, b = wkt(square(0, 0, 10)), wkt(square(10, 0, 10))
        self.assertEqual(overlay.intersection(a, b).area(), 0.0)
        self.assertAlmostEqual(overlay.union([a, b]).area(), 200.0, places=6)

    def test_the_union_of_nothing_is_no_geometry(self):
        self.assertTrue(overlay.union([]).isNull())

    def test_stereo_70_coordinates_keep_their_millimetres(self):
        a = wkt(square(450000.123, 350000.456, 10))
        b = wkt(square(450005.123, 350000.456, 10))
        shared = overlay.intersection(a, b)
        box = shared.boundingBox()
        self.assertAlmostEqual(box.xMinimum(), 450005.123, places=5)
        self.assertAlmostEqual(box.yMinimum(), 350000.456, places=5)


if __name__ == "__main__":
    helpers.main()
