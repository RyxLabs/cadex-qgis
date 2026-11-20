# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verificare Topologică on hand-built sectors with known defects, and on configurations
that resemble defects but are valid. The task also runs on layers and CGXML fixtures."""

import math
import os
import tempfile
import time
import unittest
from collections import defaultdict
from unittest import mock

import helpers
import synthetic

helpers.start_qgis(gui=False)

from qgis.core import QgsGeometry  # noqa: E402  # type: ignore

from cadex.services import building_topology_service as bts  # noqa: E402
from cadex.services import topology_check_service as tcs  # noqa: E402
from cadex.services import xlsx  # noqa: E402
from cadex.services.layer_sources import LayerSnapshot  # noqa: E402

square = helpers.square


def box(x0, y0, x1, y1):
    return f"({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0})"


# the outlines of sector 1, x from 0 to 250, and of sector 2, x from 250 to 500
WEST = QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 250, 100)})")
EAST = QgsGeometry.fromWkt(f"POLYGON({box(250, 0, 500, 100)})")


def turned_grid(columns, rows, block):
    """[(wkt, [id, sector])] of columns x rows adjacent imobile of 20 x 50 m, in sectors of
    block x block imobile, turned 17 degrees and rounded to the millimetre like a delivery.
    Every other column is shifted by 25 m, so corners lie on the long edges of the
    neighbours, which have no point there."""
    cos, sin = math.cos(math.radians(17)), math.sin(math.radians(17))

    def point(x, y):
        return (f"{round(450000 + x * cos - y * sin, 3)!r} "
                f"{round(350000 + x * sin + y * cos, 3)!r}")

    result = []
    for i in range(columns):
        for j in range(rows):
            x, y = 20.0 * i, 50.0 * j + (25.0 if i % 2 else 0.0)
            corners = [point(x, y), point(x + 20, y), point(x + 20, y + 50), point(x, y + 50)]
            sector = (i // block) * 1000 + j // block + 1
            result.append((f"POLYGON(({', '.join(corners + corners[:1])}))",
                           [str(100000 + i * rows + j), sector]))
    return result


def straight_grid(columns, rows, block):
    """(imobile, outlines) at Stereo 70 coordinates: columns x rows adjacent imobile of
    20 x 50 m as [(wkt, [id, sector])], and the rectangle around each sector of block x
    block imobile as [(wkt, [sector])]."""
    def rectangle(column, row, width, height):
        x, y = 450000 + 20 * column, 350000 + 50 * row
        return f"POLYGON({box(x, y, x + 20 * width, y + 50 * height)})"

    imobile, outlines = [], {}
    for i in range(columns):
        for j in range(rows):
            sector = (i // block) * 1000 + j // block + 1
            imobile.append((rectangle(i, j, 1, 1), [str(100000 + i * rows + j), sector]))
            outlines[sector] = (rectangle(i - i % block, j - j % block, block, block), [sector])
    return imobile, list(outlines.values())


def parcel(label, wkt, sector="5", key=None):
    return tcs.Parcel(label, sector, QgsGeometry.fromWkt(wkt), key=key)


def building(label, wkt, link=None, parcel_index=None):
    return tcs.Building(label, QgsGeometry.fromWkt(wkt), link=link, parcel=parcel_index)


def kinds(findings, kind):
    return [f for f in findings if f.kind == kind]


def sector_limits(parcels, limits, min_area=tcs.MIN_AREA):
    """The findings of check_sector_limits for the imobile parcels and the outlines limits,
    [(sector or None, geometry)], with the outlines and gaps found first, as a check does."""
    coverage = tcs.Coverage(parcels)
    outlines = tcs.assign_outlines(coverage, limits)
    gaps = tcs.check_gaps(coverage, min_area,
                          outlines=[outline for outline in outlines if outline.members])
    return tcs.check_sector_limits(coverage, outlines, [gap.geometry for gap in gaps], min_area)


class SectorTests(unittest.TestCase):
    """Sector 5 is 100 x 100 m. B overlaps A by 0.1 x 50 m; C has a vertex on A's edge that
    A lacks; D has two holes its enclaves do not fill (a 0.2 m ring around F1, a 3 mm ring
    around F2); nothing covers the 5 m strip along the top. A sector 0 road overlaps C by
    0.1 x 50 m."""

    def setUp(self):
        self.parcels = [
            parcel("A", f"POLYGON({box(0, 0, 50, 50)})"),
            parcel("B", f"POLYGON({box(49.9, 0, 100, 50)})"),
            parcel("C", "POLYGON((0 50, 25 50, 50 50, 50 100, 0 100, 0 50))"),
            parcel("D", f"POLYGON({box(50, 50, 100, 95)}, {box(59.8, 59.8, 70.2, 70.2)}, "
                        f"{box(79.997, 79.997, 90.003, 90.003)})"),
            parcel("F1", f"POLYGON({box(60, 60, 70, 70)})"),
            parcel("F2", f"POLYGON({box(80, 80, 90, 90)})"),
            parcel("R", f"POLYGON({box(0, 99.9, 50, 105)})", sector="0"),
        ]

    def test_overlaps_with_the_road_as_a_warning(self):
        found = {(f.subject, f.other): f for f in tcs.check_overlaps(tcs.Coverage(self.parcels))}
        self.assertEqual(set(found), {("imobil A", "imobil B"), ("imobil C", "imobil R")})
        self.assertAlmostEqual(found[("imobil A", "imobil B")].value, 5.0, places=3)
        self.assertEqual(found[("imobil A", "imobil B")].severity, tcs.ERROR)
        self.assertEqual(found[("imobil C", "imobil R")].severity, tcs.WARNING)
        self.assertEqual(found[("imobil C", "imobil R")].sector, "0/5")

    def test_gaps_have_their_true_area_with_the_enclave_taken_off(self):
        areas = sorted(f.value for f in tcs.check_gaps(tcs.Coverage(self.parcels)))
        # the rings, not the 108 and 100 mp squares a union that ignores the enclave would report
        self.assertEqual(len(areas), 2)
        self.assertAlmostEqual(areas[0], 10.006 ** 2 - 100, places=3)
        self.assertAlmostEqual(areas[1], 10.4 ** 2 - 100, places=3)

    def test_the_roads_sector_can_be_another_or_none(self):
        parcels = self.parcels[:-1] + [parcel("R", f"POLYGON({box(0, 99.9, 50, 105)})",
                                              sector="99")]
        for roads, expected in (("99", tcs.WARNING), (None, tcs.ERROR), ("0", tcs.ERROR)):
            found = {(f.subject, f.other): f.severity
                     for f in tcs.check_overlaps(tcs.Coverage(parcels), roads=roads)}
            self.assertEqual(found[("imobil C", "imobil R")], expected, roads)

    def test_gap_details_name_the_imobile_around(self):
        details = sorted(f.detail for f in tcs.check_gaps(tcs.Coverage(self.parcels)))
        self.assertTrue(all("între imobilele D, F" in d for d in details), details)
        alone = tcs.check_gaps(
            tcs.Coverage([parcel("A", f"POLYGON({box(0, 0, 50, 50)}, {box(10, 10, 20, 20)})")]))
        self.assertEqual([f.detail for f in alone], ["Gol de 100 mp în interiorul imobilului A."])

    def test_the_edge_strip_needs_the_sector_outline(self):
        self.assertNotIn(250.0, [round(f.value, 3) for f in tcs.check_gaps(tcs.Coverage(self.parcels))])
        found = sector_limits(
            self.parcels, [("5", QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 100, 100)})"))])
        uncovered = kinds(found, tcs.UNCOVERED)
        # the rings are gaps already, only the strip is new
        self.assertEqual([round(f.value, 3) for f in uncovered], [250.0])
        self.assertEqual(kinds(found, tcs.OUTSIDE_SECTOR), [])

    def test_an_outline_without_a_number_takes_the_sector_inside_it(self):
        found = sector_limits(
            self.parcels, [(None, QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 100, 100)})"))])
        self.assertEqual([f.sector for f in kinds(found, tcs.UNCOVERED)], ["5"])

    def test_imobil_extending_outside_its_sector(self):
        outline = QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 100, 99)})")
        found = kinds(sector_limits(self.parcels, [("5", outline)]),
                      tcs.OUTSIDE_SECTOR)
        self.assertEqual([f.subject for f in found], ["imobil C"])
        self.assertAlmostEqual(found[0].value, 50.0, places=3)

    def test_ring_vertices_are_unsnapped_the_t_junction_is_not(self):
        found = tcs.check_snapping(tcs.Coverage(self.parcels))
        by_subject = {}
        for f in found:
            by_subject.setdefault(f.subject, []).append(f.value)
        self.assertEqual(sorted(by_subject), ["imobil D", "imobil F1", "imobil F2"])
        # F2's corners are 3 mm from D's hole edges, values kept to 0.1 mm
        self.assertEqual(by_subject["imobil F2"], [0.003] * 4)
        self.assertEqual(sorted(by_subject["imobil F1"]), [0.2] * 4)
        # D's hole corners are diagonal to the enclave corners
        self.assertEqual(sorted(by_subject["imobil D"]),
                         [round(0.003 * 2 ** 0.5, 4)] * 4 + [round(0.2 * 2 ** 0.5, 4)] * 4)

    def test_vertices_of_a_reported_gap_are_not_repeated(self):
        gaps = tcs.check_gaps(tcs.Coverage(self.parcels))
        self.assertEqual(tcs.check_snapping(tcs.Coverage(self.parcels),
                                            known_gaps=[g.geometry for g in gaps]), [])

    def test_roads_are_left_out_of_gaps_and_snapping(self):
        roads = [parcel("R1", f"POLYGON({box(0, 0, 10, 1)})", sector="0"),
                 parcel("R2", f"POLYGON({box(10.03, 0, 20, 1)})", sector="0"),
                 parcel("R3", f"POLYGON({box(0, 1, 20, 2)})", sector="0")]
        self.assertEqual(tcs.check_snapping(tcs.Coverage(roads)), [])
        self.assertEqual(tcs.check_gaps(tcs.Coverage(roads)), [])


class SnappingTests(unittest.TestCase):

    def test_short_edge_along_a_third_imobil_is_not_a_miss(self):
        # A's vertex 9 cm from B's corner sits on C's edge, nothing is open
        parcels = [
            parcel("A", "POLYGON((0 0, 10 0, 10 5, 10 5.09, 10 10, 0 10, 0 0))"),
            parcel("B", f"POLYGON({box(10, 0, 20, 5)})"),
            parcel("C", f"POLYGON({box(10, 5, 20, 10)})"),
        ]
        self.assertEqual(tcs.check_snapping(tcs.Coverage(parcels)), [])

    def test_open_sliver_is_caught_at_the_vertices(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 9.97, 10)})"),
                   parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        found = tcs.check_snapping(tcs.Coverage(parcels))
        # not enclosed, so no gap - the vertices are what shows it
        self.assertEqual(tcs.check_gaps(tcs.Coverage(parcels)), [])
        self.assertEqual(len(found), 4)
        for f in found:
            self.assertAlmostEqual(f.value, 0.03, places=4)
            self.assertIn("cu un gol între ele", f.detail)
        # a 5 cm snapping precision ignores the smaller offsets
        self.assertEqual(tcs.check_snapping(tcs.Coverage(parcels), precision=0.05), [])

    def test_vertex_poking_into_the_neighbour(self):
        parcels = [parcel("A", "POLYGON((0 0, 10 0, 10.03 5, 10 10, 0 10, 0 0))"),
                   parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        found = tcs.check_snapping(tcs.Coverage(parcels))
        self.assertEqual(len(found), 1)
        self.assertIn("în interiorul imobilului B", found[0].detail)
        self.assertEqual(len(tcs.check_overlaps(tcs.Coverage(parcels))), 1)

    def test_millimetre_rounding_is_not_a_miss(self):
        # a rounded vertex and a rounded neighbour segment can be 1.4 mm apart
        parcels = [parcel("A", f"POLYGON({box(0, 0, 9.9985, 10)})"),
                   parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        self.assertEqual(tcs.check_snapping(tcs.Coverage(parcels)), [])

    def test_wide_open_gap_up_to_half_a_metre(self):
        near = [parcel("A", f"POLYGON({box(0, 0, 9.6, 10)})"),
                parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        self.assertEqual(len(tcs.check_snapping(tcs.Coverage(near))), 4)
        far = [parcel("A", f"POLYGON({box(0, 0, 9.4, 10)})"),
               parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        self.assertEqual(tcs.check_snapping(tcs.Coverage(far)), [])

    def test_across_numbered_sectors_it_still_counts(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 9.97, 10)})", sector="5"),
                   parcel("B", f"POLYGON({box(10, 0, 20, 10)})", sector="6")]
        found = tcs.check_snapping(tcs.Coverage(parcels))
        self.assertEqual({f.sector for f in found}, {"5/6"})


class BuildingTests(unittest.TestCase):

    def setUp(self):
        self.parcels = [parcel("1", f"POLYGON({box(0, 0, 20, 20)})", key="1"),
                        parcel("2", f"POLYGON({box(20, 0, 40, 20)})", key="2")]

    def check(self, buildings, **kw):
        return tcs.check_buildings(buildings, tcs.Coverage(self.parcels), **kw)

    def test_inside_and_rounding_pass(self):
        found = self.check([building("in", square(5, 5, 5), link="1"),
                            building("mm", f"POLYGON({box(15, 11, 20.001, 16)})", link="1")])
        self.assertEqual(found, [])

    def test_outside_its_imobil(self):
        found = self.check([building("out", f"POLYGON({box(15, 5, 20.05, 10)})", link="1")])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_OUTSIDE])
        self.assertAlmostEqual(found[0].value, 0.05, places=4)
        self.assertEqual(found[0].other, "imobil 2")
        self.assertTrue(found[0].is_area)

    def test_on_another_imobil_by_the_link(self):
        found = self.check([building("C1", f"POLYGON({box(19, 12, 26, 18)})", link="1")])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_ELSEWHERE])
        self.assertEqual(found[0].other, "imobil 2")
        self.assertAlmostEqual(found[0].value, 36.0, places=3)

    def test_without_a_link_the_building_belongs_to_the_imobil_it_overlaps_most(self):
        found = self.check([building("C1", f"POLYGON({box(19, 12, 26, 18)})")])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_OUTSIDE])
        self.assertIn("iese din imobilul 2 cu", found[0].detail)
        self.assertAlmostEqual(found[0].value, 1.0, places=4)

    def test_the_source_link_overrides_a_wrong_link_value(self):
        found = self.check([building("C1", f"POLYGON({box(19, 12, 26, 18)})", link="2",
                                     parcel_index=0)])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_ELSEWHERE])

    def test_no_imobil_and_an_unknown_link(self):
        found = self.check([building("far", square(50, 50, 5)),
                            building("lost", square(30, 2, 5), link="9")])
        self.assertEqual(sorted(f.kind for f in found),
                         sorted([tcs.BUILDING_ALONE, tcs.LINK_MISSING]))
        missing = kinds(found, tcs.LINK_MISSING)[0]
        self.assertEqual(missing.severity, tcs.WARNING)

    def test_overlapping_buildings(self):
        found = self.check([building("a", f"POLYGON({box(2, 12, 4, 14)})", link="1"),
                            building("b", f"POLYGON({box(3, 12, 5, 14)})", link="1")])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_OVERLAP])
        self.assertAlmostEqual(found[0].value, 2.0, places=4)

    def test_tolerance_is_honoured(self):
        wkt = f"POLYGON({box(15, 5, 20.05, 10)})"
        self.assertEqual(self.check([building("out", wkt, link="1")], tolerance=0.06), [])


class ZeroThresholdTests(unittest.TestCase):
    """A minimum area of 0 is allowed. Shapes that only touch share no area and must not be
    reported."""

    def test_neighbours_sharing_an_edge_do_not_overlap(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 10, 10)})"),
                   parcel("B", f"POLYGON({box(10, 0, 20, 10)})")]
        self.assertEqual(tcs.check_overlaps(tcs.Coverage(parcels), min_area=0.0), [])

    def test_an_imobil_filling_its_sector_is_not_outside_it(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 10, 10)})")]
        outline = QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 10, 10)})")
        found = sector_limits(parcels, [("5", outline)], min_area=0.0)
        self.assertEqual(found, [])

    def test_buildings_sharing_a_wall_do_not_overlap(self):
        parcels = [parcel("1", f"POLYGON({box(0, 0, 20, 20)})", key="1")]
        found = tcs.check_buildings([building("a", f"POLYGON({box(2, 2, 6, 6)})", link="1"),
                                     building("b", f"POLYGON({box(6, 2, 10, 6)})", link="1")],
                                    tcs.Coverage(parcels), min_area=0.0)
        self.assertEqual(found, [])

    def test_every_finding_has_a_geometry(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 10, 10)})"),
                   parcel("B", f"POLYGON({box(9.9, 0, 20, 10)})"),
                   parcel("C", f"POLYGON({box(20, 0, 30, 10)})")]
        found = tcs.check_overlaps(tcs.Coverage(parcels), min_area=0.0)
        self.assertEqual([(f.subject, f.other) for f in found], [("imobil A", "imobil B")])
        self.assertTrue(all(f.geometry is not None and f.is_area for f in found))


class SliverTests(unittest.TestCase):
    """Overlaps and gaps narrower than the snapping precision are coordinate rounding, however
    long the boundary: 1 mm along 100 m is 0.1 mp, ten times the minimum area."""

    def test_a_rounding_sliver_along_a_long_boundary_is_not_an_overlap(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 100.001, 100)})"),
                   parcel("B", f"POLYGON({box(100, 0, 200, 100)})")]
        self.assertEqual(tcs.check_overlaps(tcs.Coverage(parcels)), [])

    def test_an_overlap_wider_than_the_precision_is_reported(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 100.003, 100)})"),
                   parcel("B", f"POLYGON({box(100, 0, 200, 100)})")]
        found = tcs.check_overlaps(tcs.Coverage(parcels))
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0].value, 0.3, places=3)

    def test_a_zero_precision_reports_every_sliver(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 100.001, 100)})"),
                   parcel("B", f"POLYGON({box(100, 0, 200, 100)})")]
        self.assertEqual(len(tcs.check_overlaps(tcs.Coverage(parcels), min_width=0.0)), 1)

    def test_a_rounding_gap_between_imobile_is_not_a_gap(self):
        def sector(offset):
            return [parcel("A", f"POLYGON({box(0, 0, 100, 100)})"),
                    parcel("B", f"POLYGON({box(100 + offset, 0, 200, 100)})"),
                    parcel("C", f"POLYGON({box(0, 100, 200, 110)})"),
                    parcel("D", f"POLYGON({box(0, -10, 200, 0)})")]
        self.assertEqual(tcs.check_gaps(tcs.Coverage(sector(0.001))), [])
        self.assertEqual(len(tcs.check_gaps(tcs.Coverage(sector(0.003)))), 1)

    def test_a_rounding_strip_along_the_sector_outline_is_not_reported(self):
        outline = [("5", QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 100, 100)})"))]
        short = [parcel("A", f"POLYGON({box(0, 0, 100, 99.999)})")]
        self.assertEqual(sector_limits(short, outline), [])
        long = [parcel("A", f"POLYGON({box(0, 0, 100, 100.001)})")]
        self.assertEqual(sector_limits(long, outline), [])

    def test_a_rounding_sliver_between_buildings_is_not_an_overlap(self):
        parcels = [parcel("1", f"POLYGON({box(0, 0, 40, 40)})", key="1")]
        found = tcs.check_buildings([building("a", f"POLYGON({box(2, 2, 10.001, 32)})", link="1"),
                                     building("b", f"POLYGON({box(10, 2, 20, 32)})", link="1")],
                                    tcs.Coverage(parcels))
        self.assertEqual(found, [])

    def test_the_task_uses_the_snapping_precision(self):
        imobile = helpers.polygon_layer("Imobile", [
            (f"POLYGON({box(0, 0, 100.001, 100)})", ["1"]),
            (f"POLYGON({box(100, 0, 200, 100)})", ["2"]),
        ], fields="id:string")
        loose = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id")
        self.assertTrue(loose.run(), loose.error_msg)
        self.assertEqual(tcs.counts_by_kind(loose.findings)[tcs.OVERLAP], 0)
        strict = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                       snap_precision=0.0005)
        self.assertTrue(strict.run(), strict.error_msg)
        self.assertEqual(tcs.counts_by_kind(strict.findings)[tcs.OVERLAP], 1)


class InvalidAndKeyTests(unittest.TestCase):

    def test_bow_tie_in_romanian_with_the_spot(self):
        found = tcs.check_invalid([parcel("X", "POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))")])
        self.assertEqual(len(found), 1)
        self.assertIn("se intersectează cu el însuși", found[0].detail)
        self.assertIn("5 5", found[0].detail)
        self.assertEqual(found[0].geometry.asPoint().x(), 5.0)

    def test_invalid_imobil_still_takes_part_in_the_other_checks(self):
        parcels = [parcel("X", "POLYGON((0 0, 10 10, 10 0, 0 10, 0 0))"),
                   parcel("Y", f"POLYGON({box(9, 0, 20, 10)})")]
        self.assertEqual(len(tcs.check_overlaps(tcs.Coverage(parcels))), 1)

    def test_normalize_sector(self):
        for raw in (5, 5.0, "5", " 05 ", "5,0"):
            self.assertEqual(tcs.normalize_sector(raw), "5", raw)
        self.assertIsNone(tcs.normalize_sector(None))
        self.assertIsNone(tcs.normalize_sector("  "))
        self.assertEqual(tcs.normalize_sector("12A"), "12A")

    def test_identifiers_keep_their_leading_zeros(self):
        # the comparator's rule: a real from a Shapefile equals the integer, text stays text
        self.assertEqual(tcs.normalize_key(100001.0), "100001")
        self.assertEqual(tcs.normalize_key("0123"), "0123")
        self.assertEqual(tcs.normalize_key("100001-C1"), "100001-C1")

    def test_unknown_geos_reason_keeps_the_original(self):
        self.assertIn("Something new", tcs.geos_reason("Something new"))


class TaskTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_layers_end_to_end(self):
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), ["1", 5]),
            (f"POLYGON({box(19.9, 0, 40, 20)})", ["2", 5]),
            (f"POLYGON({box(0, 20, 40, 25)})", ["3", 0]),
        ], fields="id:string,sector:integer")
        constructii = helpers.polygon_layer("Construcții", [
            (f"POLYGON({box(15, 5, 20.05, 10)})", ["1"]),
            (square(25, 5, 5), ["2"]),
        ], fields="imobil:string")
        limits = helpers.polygon_layer("Sectoare", [(f"POLYGON({box(0, 0, 40, 20)})", [5])],
                                       fields="nr:integer")
        excel = os.path.join(self.tmp, "verificare.xlsx")
        task = tcs.TopologyCheckTask(
            imobile=[LayerSnapshot(imobile)], constructii=[LayerSnapshot(constructii)],
            id_field="id", sector_field="sector", link_field="imobil",
            limits=LayerSnapshot(limits), limit_field="nr", excel_path=excel,
            facts=[("Sursă", "layere")])
        self.assertTrue(task.run(), task.error_msg)
        counts = tcs.counts_by_kind(task.findings)
        self.assertEqual(counts[tcs.OVERLAP], 1)
        # the 1/2 overlap is 0.1 m wide; construcția 1 reaches 5 cm into imobil 2 but belongs
        # to imobil 1
        self.assertEqual(counts[tcs.BUILDING_OUTSIDE], 1)
        self.assertEqual(counts[tcs.UNCOVERED], 0)
        self.assertEqual((task.parcel_count, task.building_count, task.sector_count), (3, 2, 1))
        sheets, rows = xlsx.read_rows(excel, "Probleme")
        self.assertEqual(sheets, ["Sumar", "Probleme"])
        self.assertEqual(rows[0][:3], ["Tip", "Gravitate", "Sector"])
        self.assertEqual(len(rows) - 1, len(task.findings))

    def test_buildings_from_layers_are_named_by_imobil_and_number(self):
        layer = helpers.polygon_layer("Construcții", [
            (square(0, 0, 5), ["1203", "C2"]),
            (square(10, 0, 5), [None, "C1"]),
            (square(20, 0, 5), [None, None]),
        ], fields="imobil:string,nr:string")
        buildings = tcs.buildings_from_layers([LayerSnapshot(layer)], link_field="imobil",
                                              number_field="nr")
        # as a CGXML file names them; without the imobil the row number distinguishes it
        self.assertEqual([b.label for b in buildings],
                         ["1203 C2", "Construcții #2 (C1)", "Construcții #3"])
        plain = tcs.buildings_from_layers([LayerSnapshot(layer)], link_field="imobil")
        self.assertEqual(plain[0].label, "Construcții #1")

    def test_cgxml_folder(self):
        task = tcs.TopologyCheckTask(cgxml_paths=helpers.DATA_DIR, recursive=False)
        self.assertTrue(task.run(), task.error_msg)
        self.assertGreater(task.parcel_count, 0)
        self.assertGreater(task.building_count, 0)
        # the fixtures are separate imobile, each with its construcții inside
        building_kinds = {tcs.BUILDING_OUTSIDE, tcs.BUILDING_ELSEWHERE, tcs.BUILDING_ALONE}
        self.assertEqual([f for f in task.findings if f.kind in building_kinds], [])

    def test_cgxml_read_errors_are_collected(self):
        task = tcs.TopologyCheckTask(cgxml_paths=os.path.join(helpers.DATA_DIR, "broken"),
                                     recursive=False)
        self.assertTrue(task.run(), task.error_msg)
        self.assertTrue(task.read_errors)


class TopologyLimitsTests(unittest.TestCase):
    """A 100 x 100 m sector outline. A fills its left half and has a 10 x 10 m hole; B fills
    the right half and reaches 5 m past the outline."""

    outline = QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 100, 100)})")

    def parcels(self, sector):
        return [parcel("A", f"POLYGON({box(0, 0, 50, 100)}, {box(20, 20, 30, 30)})", sector),
                parcel("B", f"POLYGON({box(50, 0, 105, 100)})", sector)]

    def test_an_outline_takes_the_imobile_inside_it_when_none_carries_its_sector(self):
        # imobile without a sector column, with an outline without one, with a numbered
        # outline, and with an outline numbered otherwise than the imobile
        for imobile, outline in ((None, None), (None, "5"), ("12", "S12")):
            parcels = self.parcels(imobile)
            found = (tcs.check_gaps(tcs.Coverage(parcels))
                     + sector_limits(parcels, [(outline, self.outline)]))
            # the hole once, as a gap, and B's 500 mp outside the outline
            self.assertEqual([(f.kind, round(f.value, 3)) for f in found],
                             [(tcs.GAP, 100.0), (tcs.OUTSIDE_SECTOR, 500.0)], (imobile, outline))
            self.assertEqual(found[1].subject, "imobil B")

    def test_uncovered_ground_of_an_outline_without_a_sector_names_no_unknown_sector(self):
        parcels = [parcel("A", f"POLYGON({box(0, 0, 50, 95)})", None),
                   parcel("B", f"POLYGON({box(50, 0, 100, 95)})", None)]
        found = sector_limits(parcels, [(None, self.outline)])
        self.assertEqual([f.detail for f in found],
                         ["Suprafață de 500 mp din limita sectorului, neacoperită de niciun "
                          "imobil."])

    def test_an_outline_sector_no_imobil_carries_is_listed(self):
        imobile = helpers.polygon_layer("Imobile", [
            (f"POLYGON({box(0, 0, 100, 100)})", ["1", 12]),
            (f"POLYGON({box(100, 0, 200, 100)})", ["2", 13]),
        ], fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [
            (f"POLYGON({box(0, 0, 100, 100)})", ["S12"]),
            (f"POLYGON({box(100, 0, 200, 100)})", ["13"]),
        ], fields="nr:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits),
                                     limit_field="nr")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.unmatched_sectors, ["S12"])
        self.assertEqual(task.findings, [])


class TopologyCrackTests(unittest.TestCase):
    """Four imobile around a 20 x 20 m unparcelled pocket. W and S are 1 mm apart from the
    pocket out to the sector edge, less than the snapping precision."""

    def sector(self, crack):
        return [parcel("S", f"POLYGON({box(0, 0, 60, 20)})"),
                parcel("N", f"POLYGON({box(0, 40, 60, 60)})"),
                parcel("W", f"POLYGON({box(0, 20 + crack, 20, 40)})"),
                parcel("E", f"POLYGON({box(40, 20, 60, 40)})")]

    def test_a_gap_open_to_the_outside_through_a_rounding_crack_is_found(self):
        for crack in (0.0, 0.001):
            found = tcs.check_gaps(tcs.Coverage(self.sector(crack)))
            self.assertEqual([round(f.value, 3) for f in found], [400.0], crack)
            self.assertEqual(found[0].detail, "Gol de 400 mp între imobilele S, N, W, E.")

    def test_such_a_gap_is_not_reported_again_as_uncovered_sector_ground(self):
        outline = [("5", QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 60, 60)})"))]
        self.assertEqual(sector_limits(self.sector(0.001), outline), [])


class TopologyDuplicateTests(unittest.TestCase):
    """A delivery folder that also holds an older copy of a file in a subfolder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_dup_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def write(self, folder, name, x, cadgenno, sirsup="40900"):
        land = synthetic.Land(
            rings=[synthetic.square(x, 400000, 100), synthetic.square(x + 20, 400020, 10)],
            cadgenno=cadgenno, sector="3", sirsup=sirsup, siruta=sirsup,
            buildings=[synthetic.Building(1, [synthetic.square(x + 50, 400050, 10)])])
        synthetic.write(folder, name, land)

    def test_the_same_imobil_in_two_files_is_checked_once(self):
        self.write(self.tmp, "a.cgxml", 500000, "501")
        self.write(os.path.join(self.tmp, "vechi"), "a.cgxml", 500000, "501")
        # the same ID in another UAT is another imobil
        self.write(self.tmp, "b.cgxml", 501000, "501", sirsup="41000")
        task = tcs.TopologyCheckTask(cgxml_paths=self.tmp, recursive=True)
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([f.detail for f in task.findings],
                         ["Gol de 100 mp în interiorul imobilului 501.",
                          "Gol de 100 mp în interiorul imobilului 501."])
        self.assertEqual(task.duplicates, [("501", ["a.cgxml", os.path.join("vechi", "a.cgxml")])])
        self.assertEqual((task.parcel_count, task.building_count), (2, 2))

    def test_two_features_of_a_layer_with_the_same_id_are_two_imobile(self):
        # a data error, or a column chosen as ID that is not unique: the left imobil has a
        # 10 x 20 m notch, the right one reaches 10 cm into it
        imobile = helpers.polygon_layer("Imobile", [
            ("POLYGON((0 0, 50 0, 50 40, 40 40, 40 60, 50 60, 50 100, 0 100, 0 0))", ["5"]),
            (f"POLYGON({box(49.9, 0, 100, 100)})", ["5"])], fields="id:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(sorted(f.detail for f in task.findings if f.kind != tcs.UNSNAPPED), [
            "Gol de 198 mp între imobilele 5, 5.", "Imobilele 5 și 5 se suprapun pe 8 mp."])
        self.assertEqual(len(kinds(task.findings, tcs.UNSNAPPED)), 2)
        self.assertEqual((task.parcel_count, task.notes()), (2, []))

    def test_a_building_in_two_versions_of_its_imobil_is_checked_in_its_own_file(self):
        # the newer version moved its east limit 3 m west, across the construcție
        for folder, width in ((self.tmp, 97), (os.path.join(self.tmp, "vechi"), 100)):
            synthetic.write(folder, "501.cgxml", synthetic.Land(
                rings=[[(500000, 400000), (500000 + width, 400000),
                        (500000 + width, 400100), (500000, 400100)]],
                cadgenno="501", sector="3",
                buildings=[synthetic.Building(1, [synthetic.square(500090, 400040, 10)])]))
        task = tcs.TopologyCheckTask(cgxml_paths=self.tmp, recursive=True)
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([(f.kind, f.other, f.detail) for f in task.findings], [
            (tcs.BUILDING_OUTSIDE, "",
             "Un punct al construcției 501 C1 iese din imobilul 501 cu 300 cm (30 mp în afară).")])
        self.assertEqual((task.parcel_count, task.building_count), (1, 1))


class TopologyExcelTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_xlsx_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_the_summary_counts_errors_and_warnings_of_a_kind_apart(self):
        # two overlaps between imobile, then one with a road
        parcels = [parcel("A", f"POLYGON({box(0, 0, 50, 50)})"),
                   parcel("B", f"POLYGON({box(49.9, 0, 100, 50)})"),
                   parcel("D", f"POLYGON({box(150, 0, 200, 50)})"),
                   parcel("E", f"POLYGON({box(149.9, 0, 160, 50)})"),
                   parcel("C", f"POLYGON({box(0, 50, 50, 100)})"),
                   parcel("R", f"POLYGON({box(0, 99.9, 50, 105)})", sector="0")]
        path = os.path.join(self.tmp, "raport.xlsx")
        tcs.write_excel(path, tcs.check_overlaps(tcs.Coverage(parcels)), [("Sursă", "test")])
        _names, rows = xlsx.read_rows(path, "Sumar")
        overlaps = [(row[1], row[2]) for row in rows if row and row[0] == tcs.OVERLAP]
        self.assertEqual(overlaps, [(tcs.ERROR, 2), (tcs.WARNING, 1)])


class TopologyBuildingPlacementTests(unittest.TestCase):
    """Imobil 1 in sector 5 and imobil 2 in sector 6, side by side."""

    def setUp(self):
        self.parcels = [parcel("1", f"POLYGON({box(0, 0, 20, 20)})", sector="5", key="1"),
                        parcel("2", f"POLYGON({box(20, 0, 40, 20)})", sector="6", key="2")]

    def check(self, buildings):
        return tcs.check_buildings(buildings, tcs.Coverage(self.parcels))

    def test_a_linked_building_on_no_imobil_stands_alone(self):
        found = self.check([building("far", f"POLYGON({box(120, 5, 125, 10)})", link="1")])
        self.assertEqual([f.kind for f in found], [tcs.BUILDING_ALONE])
        self.assertEqual(found[0].detail,
                         "Construcția far aparține imobilului 1, dar nu se află pe niciun imobil.")
        self.assertEqual(found[0].sector, "5")

    def test_an_unknown_link_on_no_imobil_names_no_imobil_as_used(self):
        found = self.check([building("lost", f"POLYGON({box(120, 5, 125, 10)})", link="99")])
        missing = kinds(found, tcs.LINK_MISSING)
        self.assertEqual([f.detail for f in missing], [
            "Construcția lost indică imobilul '99', care nu există în datele verificate."])
        self.assertEqual(len(kinds(found, tcs.BUILDING_ALONE)), 1)
        # on an imobil, that imobil is the one used
        found = self.check([building("near", f"POLYGON({box(25, 5, 30, 10)})", link="99")])
        self.assertIn("S-a folosit imobilul pe care se află.", found[0].detail)
        self.assertEqual(found[0].sector, "6")

    def test_building_findings_carry_the_sector_of_their_imobil(self):
        found = self.check([building("a", f"POLYGON({box(22, 2, 26, 6)})", link="2"),
                            building("b", f"POLYGON({box(24, 2, 28, 6)})", link="2")])
        self.assertEqual([(f.kind, f.sector) for f in found], [(tcs.BUILDING_OVERLAP, "6")])

    def test_the_neighbour_of_a_building_outside_its_imobil_is_the_imobil_it_reaches(self):
        found = self.check([building("out", f"POLYGON({box(15, 5, 20.05, 10)})", link="1")])
        self.assertEqual([(f.kind, f.other) for f in found], [(tcs.BUILDING_OUTSIDE, "imobil 2")])
        far = self.check([building("out", f"POLYGON({box(-0.05, 5, 5, 10)})", link="1")])
        self.assertEqual([(f.kind, f.other) for f in far], [(tcs.BUILDING_OUTSIDE, "")])

    def test_an_invalid_building_is_reported_in_the_sector_of_its_imobil(self):
        imobile = helpers.polygon_layer("Imobile", [
            (f"POLYGON({box(0, 0, 20, 20)})", ["1", 5]),
            (f"POLYGON({box(20, 0, 40, 20)})", ["2", 6]),
        ], fields="id:string,sector:integer")
        constructii = helpers.polygon_layer("Construcții", [
            ("POLYGON((25 5, 30 10, 30 5, 25 10, 25 5))", ["2"])], fields="imobil:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)],
                                     constructii=[LayerSnapshot(constructii)], id_field="id",
                                     sector_field="sector", link_field="imobil")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([(f.kind, f.sector) for f in task.findings], [(tcs.INVALID, "6")])


class TopologyTaskOutputTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_task_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def test_a_report_that_cannot_be_written_keeps_the_findings(self):
        imobile = helpers.polygon_layer("Imobile", [
            (square(0, 0, 20), ["1"]), (f"POLYGON({box(19.9, 0, 40, 20)})", ["2"])],
            fields="id:string")
        # nobody can open a folder for writing, as nobody can write a workbook open in Excel
        report = os.path.join(self.tmp, "raport.xlsx")
        os.makedirs(report)
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     excel_path=report)
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([f.kind for f in task.findings], [tcs.OVERLAP])
        self.assertIn("Fișierul 'raport.xlsx' nu poate fi scris", task.report_error)

    def test_features_without_geometry_are_counted_as_not_checked(self):
        from qgis.core import QgsFeature  # type: ignore
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 20), ["1"])],
                                        fields="id:string")
        constructii = helpers.polygon_layer("Construcții", [(square(5, 5, 5), ["1"])],
                                            fields="imobil:string")
        for layer in (imobile, constructii):
            empty = QgsFeature(layer.fields())
            empty.setAttributes(["9"])
            layer.dataProvider().addFeatures([empty])
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)],
                                     constructii=[LayerSnapshot(constructii)], id_field="id",
                                     link_field="imobil")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual((task.parcel_count, task.building_count, task.skipped), (1, 1, 2))

    def test_the_report_names_the_tolerances_it_was_made_with(self):
        imobile = helpers.polygon_layer("Imobile", [(square(0, 0, 20), ["1"])],
                                        fields="id:string")
        report = os.path.join(self.tmp, "raport.xlsx")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     snap_precision=0.003, roads_sector=None,
                                     excel_path=report, facts=[("Layer de imobile", "Imobile")])
        self.assertTrue(task.run(), task.error_msg)
        facts = helpers.summary_facts(report)
        self.assertEqual(facts["Layer de imobile"], "Imobile")
        self.assertEqual(facts["Toleranță de racordare"], "0,003 m")
        self.assertEqual(facts["Distanță de racordare"], "0,5 m")
        self.assertEqual(facts["Sectorul drumurilor"], "-")


class TopologyCopiesTests(unittest.TestCase):
    """Copies of a delivery's files next to it: an older copy in a subfolder, or two
    archives of the same name from two folders; and two imobile with one ID."""

    X, Y = 500000, 400000

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadex_topo_copies_")

    def tearDown(self):
        helpers.remove_folder(self.tmp)

    def rect(self, x0, y0, x1, y1):
        return [(self.X + x0, self.Y + y0), (self.X + x1, self.Y + y0),
                (self.X + x1, self.Y + y1), (self.X + x0, self.Y + y1)]

    def delivery(self, folder):
        """10001 and 10002 overlap by 1 m; a point of 10003 is 5 cm short of 10002."""
        for number, ring in (("10001", self.rect(0, 0, 21, 30)),
                             ("10002", self.rect(20, 0, 40, 30)),
                             ("10003", [(self.X + 40.05, self.Y)] + self.rect(40, 0, 60, 30)[1:])):
            synthetic.write(folder, f"{number}.cgxml",
                            synthetic.Land(rings=[ring], cadgenno=number, sector="5"))

    def check(self, paths):
        task = tcs.TopologyCheckTask(cgxml_paths=paths, recursive=True)
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_an_older_copy_of_the_delivery_in_a_subfolder_reports_each_problem_once(self):
        self.delivery(self.tmp)
        alone = self.check(self.tmp)
        self.assertEqual(sorted(f.kind for f in alone.findings),
                         [tcs.UNSNAPPED, tcs.UNSNAPPED, tcs.OVERLAP])
        self.delivery(os.path.join(self.tmp, "vechi"))
        both = self.check(self.tmp)
        self.assertEqual(sorted(f.detail for f in both.findings),
                         sorted(f.detail for f in alone.findings))
        self.assertEqual((both.parcel_count, [imobil for imobil, _files in both.duplicates]),
                         (3, ["10001", "10002", "10003"]))

    def test_an_imobil_in_several_files_is_checked_from_the_file_nearest_the_chosen_folder(self):
        synthetic.write(self.tmp, "cg_501.cgxml", synthetic.Land(
            rings=[self.rect(0, 0, 20, 20)], cadgenno="501", sector="3"))
        synthetic.write(self.tmp, "cg_502.cgxml", synthetic.Land(
            rings=[self.rect(20, 0, 40, 20)], cadgenno="502", sector="3"))
        # an older version that overlapped 502, in a subfolder listed before the delivery
        synthetic.write(os.path.join(self.tmp, "arhiva"), "cg_501.cgxml", synthetic.Land(
            rings=[self.rect(0, 0, 21, 20)], cadgenno="501", sector="3"))
        task = self.check(self.tmp)
        self.assertEqual(task.findings, [])
        self.assertEqual(task.duplicates,
                         [("501", ["cg_501.cgxml", os.path.join("arhiva", "cg_501.cgxml")])])

    def test_an_exact_copy_of_a_file_without_id_or_ie_is_checked_once(self):
        synthetic.write(self.tmp, "10002.cgxml", synthetic.Land(
            rings=[self.rect(20, 0, 40, 30)], cadgenno="10002", sector="5"))
        # an imobil without ID or IE that reaches 1 m into 10002, and its copy
        for folder in (self.tmp, os.path.join(self.tmp, "vechi")):
            synthetic.write(folder, "drum.cgxml", synthetic.Land(
                rings=[self.rect(0, 0, 21, 30)], sector="5"))
        # a file of the same name elsewhere is another imobil
        synthetic.write(os.path.join(self.tmp, "alt"), "drum.cgxml", synthetic.Land(
            rings=[self.rect(60, 0, 80, 30)], sector="5"))
        task = self.check(self.tmp)
        self.assertEqual([f.detail for f in task.findings],
                         ["Imobilele 10002 și drum.cgxml se suprapun pe 30 mp."])
        self.assertEqual(task.parcel_count, 3)
        self.assertEqual(task.duplicates,
                         [("drum.cgxml", ["drum.cgxml", os.path.join("vechi", "drum.cgxml")])])

    def test_two_archives_of_the_same_name_from_two_folders_are_copies(self):
        import zipfile
        archives = []
        for version, width in (("v1", 100), ("v2", 97)):
            folder = os.path.join(self.tmp, version)
            path = synthetic.write(folder, "501.cgxml", synthetic.Land(
                rings=[self.rect(0, 0, width, 100)], cadgenno="501", sector="3"))
            archive = os.path.join(folder, "livrare.zip")
            with zipfile.ZipFile(archive, "w") as zf:
                zf.write(path, "501.cgxml")
            os.remove(path)
            archives.append(archive)
        task = self.check(archives)
        self.assertEqual((task.findings, task.parcel_count), ([], 1))
        # both are named livrare.zip/501.cgxml, so the list gives their whole paths
        self.assertEqual(task.duplicates, [("501", [os.path.join(archive, "501.cgxml")
                                                    for archive in archives])])

    def test_two_imobile_with_one_id_in_different_places_are_both_checked(self):
        # a 3 x 3 block of 20 m imobile whose middle file carries the ID of an imobil 1 km
        # away, with a construcție 5 m into its east neighbour
        for n in range(9):
            row, col = divmod(n, 3)
            middle = n == 4
            synthetic.write(self.tmp, f"{502 + n}.cgxml", synthetic.Land(
                rings=[self.rect(20 * col, 20 * row, 20 * col + 20, 20 * row + 20)],
                cadgenno="501" if middle else str(502 + n), sector="3",
                buildings=[synthetic.Building(1, [synthetic.square(self.X + 35, self.Y + 25,
                                                                   10)])] if middle else []))
        synthetic.write(self.tmp, "501.cgxml", synthetic.Land(
            rings=[self.rect(1000, 0, 1020, 20)], cadgenno="501", sector="3"))
        task = self.check(self.tmp)
        self.assertEqual([f.detail for f in task.findings], [
            "Un punct al construcției 501 C1 iese din imobilul 501 cu 500 cm (50 mp în afară)."])
        self.assertEqual((task.parcel_count, task.building_count), (10, 1))
        self.assertIn("1 imobil apare în mai multe fișiere, în locuri diferite: 501. Fiecare "
                      "fișier s-a verificat ca imobil separat.", task.notes())
        self.assertEqual((task.duplicates, task.reused_keys),
                         ([], [("501", ["501.cgxml", "506.cgxml"])]))


class TopologyOutlineEdgeTests(unittest.TestCase):
    """The outlines of sectors 1 (x from 0 to 250) and 2 (x from 250 to 500), and an imobil
    X across the edge between them."""

    outlines = [("1", WEST), ("2", EAST)]

    def outside(self, wkt):
        x = parcel("X", wkt, None)
        # the interior point GEOS computes for X lies on the edge between the outlines
        self.assertEqual(x.clean.pointOnSurface().asPoint().x(), 250)
        return [(f.sector, f.detail) for f in sector_limits([x], self.outlines)
                if f.kind == tcs.OUTSIDE_SECTOR]

    def test_an_imobil_on_the_edge_belongs_to_the_outline_it_overlaps_most(self):
        # 1.000 mp left of the edge, 2.600 mp right of it
        found = self.outside("POLYGON((240 0, 300 0, 300 40, 260 40, 260 100, 240 100, 240 0))")
        self.assertEqual(found, [("2", "Imobilul X iese din limita sectorului 2 cu 1.000 mp.")])

    def test_an_imobil_halved_by_the_edge_is_outside_one_of_the_outlines(self):
        found = self.outside(f"POLYGON({box(240, 0, 260, 100)})")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0][1].endswith(" cu 1.000 mp."), found)

    def test_an_imobil_on_the_edge_of_an_outline_no_imobil_carries_stays_in_its_sector(self):
        # only sector 2 is carried; X lies mostly in its outline
        x = parcel("X", "POLYGON((240 0, 300 0, 300 40, 260 40, 260 100, 240 100, 240 0))", "2")
        self.assertEqual(x.clean.pointOnSurface().asPoint().x(), 250)
        parcels = [x, parcel("R", f"POLYGON({box(300, 0, 500, 100)})", "2"),
                   parcel("R2", f"POLYGON({box(260, 40, 300, 100)})", "2")]
        found = [(f.sector, f.detail) for f in sector_limits(parcels, self.outlines)
                 if f.kind == tcs.OUTSIDE_SECTOR]
        self.assertEqual(found, [("2", "Imobilul X iese din limita sectorului 2 cu 1.000 mp.")])


class TopologyDissolvedOutlineTests(unittest.TestCase):
    """Sector outlines made from the imobile themselves, as the Dissolve algorithm of QGIS
    makes them from the sector column."""

    def test_no_imobil_lies_outside_them_and_none_of_their_ground_is_uncovered(self):
        rows = turned_grid(30, 30, 15)
        sectors = defaultdict(list)
        for wkt, (_number, sector) in rows:
            sectors[sector].append(QgsGeometry.fromWkt(wkt))
        outlines = []
        for sector, shapes in sectors.items():
            outline = QgsGeometry.unaryUnion(shapes)
            outline.convertToMultiType()
            outlines.append((outline.asWkt(17), [sector]))
        imobile = helpers.polygon_layer("Imobile", rows, fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", outlines, fields="nr:integer",
                                       geometry="MultiPolygon")
        self.assertEqual((imobile.featureCount(), limits.featureCount()), (900, 4))
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits),
                                     limit_field="nr")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([f.detail for f in task.findings], [])


class TopologyBetweenSectorsTests(unittest.TestCase):
    """Sector 1 west of x = 250 and sector 2 east of it, with a 20 x 20 m hole across the
    line between them."""

    outlines = [("1", WEST), ("2", EAST)]

    def parcels(self, west="1", east="2"):
        return [parcel("A", f"POLYGON({box(0, 0, 240, 100)})", west),
                parcel("B", f"POLYGON({box(260, 0, 500, 100)})", east),
                parcel("C", f"POLYGON({box(240, 0, 250, 40)})", west),
                parcel("D", f"POLYGON({box(250, 0, 260, 40)})", east),
                parcel("E", f"POLYGON({box(240, 60, 250, 100)})", west),
                parcel("F", f"POLYGON({box(250, 60, 260, 100)})", east)]

    def test_a_hole_enclosed_by_imobile_of_two_sectors_is_a_warning(self):
        found = tcs.check_gaps(tcs.Coverage(self.parcels()))
        self.assertEqual([(f.kind, f.severity, f.sector, f.value) for f in found],
                         [(tcs.GAP, tcs.WARNING, "1/2", 400.0)])
        self.assertEqual(found[0].detail, "Gol de 400 mp între sectoarele 1 și 2, mărginit de "
                                          "imobilele A, B, C, D, E, F.")
        self.assertEqual(found[0].other, "A, B, C, D, ...")

    def test_the_same_hole_inside_one_sector_is_an_error(self):
        found = tcs.check_gaps(tcs.Coverage(self.parcels("1", "1")))
        self.assertEqual([(f.severity, f.sector, f.detail) for f in found],
                         [(tcs.ERROR, "1", "Gol de 400 mp între imobilele A, B, C, D, E, F.")])

    def test_with_sector_outlines_the_hole_is_uncovered_ground_of_each_sector(self):
        def ground(outlines):
            task = self.task(self.parcels(), outlines)
            return sorted((f.kind, f.severity, f.sector, f.value) for f in task.findings
                          if f.kind in (tcs.GAP, tcs.UNCOVERED))
        self.assertEqual(ground(self.outlines), [(tcs.UNCOVERED, tcs.ERROR, "1", 200.0),
                                                 (tcs.UNCOVERED, tcs.ERROR, "2", 200.0)])
        # outlines that end 5 m short of the line between the sectors: the strip of the hole
        # between them belongs to no sector
        apart = [("1", QgsGeometry.fromWkt(f"POLYGON({box(0, 0, 245, 100)})")),
                 ("2", QgsGeometry.fromWkt(f"POLYGON({box(255, 0, 500, 100)})"))]
        self.assertEqual(ground(apart), [(tcs.GAP, tcs.WARNING, "1/2", 200.0),
                                         (tcs.UNCOVERED, tcs.ERROR, "1", 100.0),
                                         (tcs.UNCOVERED, tcs.ERROR, "2", 100.0)])

    def task(self, parcels, outlines):
        imobile = helpers.polygon_layer(
            "Imobile", [(p.geometry.asWkt(), [p.label, p.sector]) for p in parcels],
            fields="id:string,sector:string")
        limits = helpers.polygon_layer(
            "Sectoare", [(shape.asWkt(), [sector]) for sector, shape in outlines],
            fields="nr:string")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits),
                                     limit_field="nr")
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_a_point_left_unsnapped_across_a_gap_between_sectors_is_an_error(self):
        # a strip 5 cm wide between an imobil of sector 1 and one of sector 2, closed at both
        # ends, with a point of the first imobil halfway along it: too narrow for a road
        imobile = helpers.polygon_layer("Imobile", [
            ("POLYGON((0 0, 9.95 0, 9.95 5, 9.95 10, 0 10, 0 0))", ["A", 1]),
            (f"POLYGON({box(10, 0, 20, 10)})", ["B", 2]),
            (f"POLYGON({box(0, 10, 20, 20)})", ["C", 1]),
            (f"POLYGON({box(0, -10, 20, 0)})", ["D", 2])], fields="id:string,sector:integer")
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector")
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual([(f.kind, f.severity, f.value) for f in task.findings],
                         [(tcs.GAP, tcs.WARNING, 0.5), (tcs.UNSNAPPED, tcs.ERROR, 0.05)])

    def test_without_a_sector_column_the_hole_is_reported_once_whatever_the_outlines(self):
        parcels = self.parcels(None, None)
        found = tcs.check_gaps(tcs.Coverage(parcels)) + sector_limits(parcels, self.outlines)
        self.assertEqual([(f.kind, f.detail) for f in found],
                         [(tcs.GAP, "Gol de 400 mp între imobilele A, B, C, D, E, F.")])

    def test_a_gap_of_a_sector_enclosed_by_another_is_reported_once_in_its_own_sector(self):
        # sector 5 is a ring around sector 6, whose only imobil has a 10 x 10 m hole
        parcels = [parcel("5a", f"POLYGON({box(0, 0, 100, 30)})", "5"),
                   parcel("5b", f"POLYGON({box(0, 30, 30, 70)})", "5"),
                   parcel("5c", f"POLYGON({box(70, 30, 100, 70)})", "5"),
                   parcel("5d", f"POLYGON({box(0, 70, 100, 100)})", "5"),
                   parcel("6a", f"POLYGON({box(30, 30, 70, 70)}, {box(45, 45, 55, 55)})", "6")]
        found = tcs.check_gaps(tcs.Coverage(parcels))
        self.assertEqual([(f.severity, f.sector, f.detail) for f in found],
                         [(tcs.ERROR, "6", "Gol de 100 mp în interiorul imobilului 6a.")])


class TopologyOutlineMembersTests(unittest.TestCase):
    """The outlines of sectors 1 (x from 0 to 250) and 2 (x from 250 to 500)."""

    # 5.000 mp in the east outline and an arm of 200 mp into the west one, where the
    # interior point GEOS computes for it lies
    ARM = "POLYGON((250 0, 300 0, 300 100, 250 100, 250 51, 150 51, 150 49, 250 49, 250 0))"
    # a delivery of sector 2 alone: no imobil of the sector of the west outline is in it
    SECTOR_2 = [(f"POLYGON({box(250, 0, 400, 100)})", ["201", 2]),
                (f"POLYGON({box(400, 0, 500, 100)})", ["202", 2])]
    NOT_CHECKED = ["O limită de sector nu cuprinde niciun imobil și nu a fost verificată."]

    def outside(self, parcels, outlines):
        return [f.detail for f in sector_limits(parcels, outlines)
                if f.kind == tcs.OUTSIDE_SECTOR]

    def test_outlines_without_a_sector_are_told_apart_when_the_imobile_have_none(self):
        # X lies half in each outline
        parcels = [parcel("A", f"POLYGON({box(0, 0, 230, 100)})", None),
                   parcel("X", f"POLYGON({box(230, 0, 270, 100)})", None),
                   parcel("B", f"POLYGON({box(270, 0, 500, 100)})", None)]
        self.assertEqual(self.outside(parcels, [(None, WEST), (None, EAST)]),
                         ["Imobilul X iese din limita sectorului cu 2.000 mp."])

    def test_an_imobil_without_a_sector_belongs_to_the_outline_it_overlaps_most(self):
        x = parcel("X", self.ARM, None)
        self.assertLess(x.clean.pointOnSurface().asPoint().x(), 250)
        self.assertEqual(self.outside([x], [("1", WEST), ("2", EAST)]),
                         ["Imobilul X iese din limita sectorului 2 cu 200 mp."])

    def test_an_imobil_is_checked_against_the_outline_of_its_own_sector_only(self):
        # no imobil carries sector 1
        x = parcel("X", self.ARM, "2")
        self.assertEqual(self.outside([x], [("1", WEST), ("2", EAST)]),
                         ["Imobilul X iese din limita sectorului 2 cu 200 mp."])

    def test_an_outline_that_no_checked_imobil_belongs_to_has_no_uncovered_ground(self):
        # the imobile of sector 2 against the outlines of three sectors
        parcels = [parcel("201", f"POLYGON({box(250, 0, 400, 100)})", "2"),
                   parcel("202", f"POLYGON({box(400, 0, 500, 100)})", "2")]
        far = QgsGeometry.fromWkt(f"POLYGON({box(500, 0, 750, 100)})")
        outlines = [("1", WEST), ("2", EAST), ("3", far)]
        self.assertEqual(sector_limits(parcels, outlines), [])
        self.assertEqual(tcs.unmatched_sectors(parcels, outlines), ["1", "3"])

    def test_an_unnumbered_outline_with_no_imobil_in_it_is_named_in_a_note(self):
        imobile = helpers.polygon_layer(
            "Imobile", [(f"POLYGON({box(0, 0, 250, 100)})", ["1", 7])],
            fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [(WEST.asWkt(), [1]),
                                                    (EAST.asWkt(), [2])])
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits))
        self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(task.findings, [])
        self.assertEqual(task.notes(), ["O limită de sector nu cuprinde niciun imobil și nu a "
                                        "fost verificată."])

    def checked_against_outlines_without_a_sector(self, imobile, outlines=None):
        """The finished task on imobile, [(wkt, [id, sector])], and outlines, the west and
        the east one unless given."""
        imobile = helpers.polygon_layer("Imobile", imobile, fields="id:string,sector:integer")
        limits = helpers.polygon_layer("Sectoare", [
            (shape.asWkt(), [n]) for n, shape in enumerate(outlines or [WEST, EAST])])
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits))
        self.assertTrue(task.run(), task.error_msg)
        return task

    def test_an_outline_without_a_sector_that_the_roads_fill_is_the_roads_outline(self):
        # the road reaches 5 m past its outline
        task = self.checked_against_outlines_without_a_sector([
            (f"POLYGON({box(0, 0, 250, 100)})", ["A", 7]),
            (f"POLYGON({box(250, 0, 500, 105)})", ["R", 0])])
        self.assertEqual([f.detail for f in task.findings],
                         ["Imobilul R iese din limita sectorului 0 cu 1.250 mp."])
        self.assertEqual(task.notes(), [])

    def test_the_roads_outline_can_be_several_polygons_that_hold_every_road_together(self):
        # each half of the east outline is filled by one road; R2 reaches 5 m past its half
        lower = QgsGeometry.fromWkt(f"POLYGON({box(250, 0, 500, 50)})")
        upper = QgsGeometry.fromWkt(f"POLYGON({box(250, 50, 500, 100)})")
        task = self.checked_against_outlines_without_a_sector([
            (f"POLYGON({box(0, 0, 250, 100)})", ["A", 7]),
            (f"POLYGON({box(250, 0, 500, 50)})", ["R1", 0]),
            (f"POLYGON({box(250, 50, 500, 105)})", ["R2", 0])], [WEST, lower, upper])
        self.assertEqual([f.detail for f in task.findings],
                         ["Imobilul R2 iese din limita sectorului 0 cu 1.250 mp."])
        self.assertEqual(task.notes(), [])

    def test_an_outline_a_road_covers_only_partly_is_not_the_roads_outline(self):
        # road R1 covers 1.000 of the 25.000 mp of the west outline; road R2 lies north of
        # the east one
        inside = (f"POLYGON({box(100, 0, 110, 100)})", ["R1", 0])
        elsewhere = (f"POLYGON({box(250, 100, 500, 110)})", ["R2", 0])
        for roads in ([inside, elsewhere], [inside]):
            task = self.checked_against_outlines_without_a_sector(self.SECTOR_2 + roads)
            self.assertEqual([f.detail for f in task.findings], [], roads)
            self.assertEqual(task.notes(), self.NOT_CHECKED, roads)

    def test_an_outline_one_road_fills_is_not_the_roads_outline_when_another_road_lies_outside_it(self):
        # road R1 fills the east outline; road R2 lies north of the west one
        task = self.checked_against_outlines_without_a_sector([
            (f"POLYGON({box(0, 0, 250, 100)})", ["A", 7]),
            (f"POLYGON({box(250, 0, 500, 100)})", ["R1", 0]),
            (f"POLYGON({box(0, 100, 250, 110)})", ["R2", 0])])
        self.assertEqual([f.detail for f in task.findings], [])
        self.assertEqual(task.notes(), self.NOT_CHECKED)


class TopologyScaleTests(unittest.TestCase):
    """6,400 imobile in 400 sectors, each sector with its outline."""

    @classmethod
    def setUpClass(cls):
        cls.imobile, cls.outlines = straight_grid(80, 80, 4)

    def test_the_sector_limit_check_of_400_outlines_ends_in_seconds(self):
        # when the outlines or the imobile name no sector, each imobil has to be found in an
        # outline; trying every outline for every imobil takes 2,560,000 tests
        named = [parcel(number, wkt, str(sector)) for wkt, (number, sector) in self.imobile]
        unnamed = [parcel(number, wkt, None) for wkt, (number, _sector) in self.imobile]
        shapes = [(str(sector), QgsGeometry.fromWkt(wkt)) for wkt, (sector,) in self.outlines]
        for parcels, outlines in ((named, [(None, shape) for _sector, shape in shapes]),
                                  (unnamed, shapes)):
            started = time.perf_counter()
            found = sector_limits(parcels, outlines)
            self.assertLess(time.perf_counter() - started, 3.0)
            self.assertEqual(found, [])

    def layers(self, imobile, outlines):
        return (helpers.polygon_layer("Imobile", imobile, fields="id:string,sector:integer"),
                helpers.polygon_layer("Sectoare", outlines, fields="nr:integer"))

    def test_a_cancelled_check_ends_at_once(self):
        imobile, limits = self.layers(self.imobile, self.outlines)
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits))
        asked = []
        assign = tcs.assign_outlines

        def cancelled(*args, **kwargs):
            # cancelled as the work with the sector outlines begins
            asked.append(time.perf_counter())
            return assign(*args, **kwargs)

        task.stop_hook = lambda: bool(asked)
        with mock.patch.object(tcs, "assign_outlines", cancelled):
            self.assertFalse(task.run())
        self.assertTrue(asked)
        self.assertLess(time.perf_counter() - asked[0], 2.0)

    def test_progress_moves_while_the_sector_limits_are_checked(self):
        imobile, limits = self.layers(*straight_grid(20, 20, 4))
        task = tcs.TopologyCheckTask(imobile=[LayerSnapshot(imobile)], id_field="id",
                                     sector_field="sector", limits=LayerSnapshot(limits),
                                     limit_field="nr")
        seen, during, checking = [], [], []
        check = tcs.check_sector_limits

        def watched(*args, **kwargs):
            checking.append(True)
            try:
                return check(*args, **kwargs)
            finally:
                checking.clear()

        def progress(percent):
            seen.append(percent)
            if checking:
                during.append(percent)

        task.progress_hook = progress
        with mock.patch.object(tcs, "check_sector_limits", watched):
            self.assertTrue(task.run(), task.error_msg)
        self.assertEqual(seen, sorted(seen))
        self.assertGreaterEqual(len(set(during)), 3)


class TopologyBuildingCutTests(unittest.TestCase):
    """Topologie Construcții and Verificare Topologică on the same construcții. Imobil 1 lies
    from x 0 to 20, imobil 2 from x 20 to 40."""

    # an imobil with a slanted south limit, and a construcție whose south wall has its two
    # points computed on that limit
    SLANTED = ("POLYGON((500000 400000, 500030 400010, 500030 400040, 500000 400040, "
               "500000 400000))")
    ON_THE_LIMIT = (f"POLYGON((500007 {400000 + 7 / 3!r}, 500016 {400000 + 16 / 3!r}, "
                    f"500013 {400009 + 16 / 3!r}, 500004 {400009 + 7 / 3!r}, "
                    f"500007 {400000 + 7 / 3!r}))")

    def setUp(self):
        self.imobile = helpers.polygon_layer("Imobile", [
            (f"POLYGON({box(0, 0, 20, 20)})", ["1"]),
            (f"POLYGON({box(20, 0, 40, 20)})", ["2"])], fields="id:string")

    def both(self, rows, imobile=None, geometry="Polygon", check_tolerance=None, **cut_options):
        """(the cut's rows, the check's findings on construcții outside their imobil) for the
        construcții [(wkt, imobil)]."""
        imobile = self.imobile if imobile is None else imobile
        constructii = helpers.polygon_layer("Construcții", [(wkt, [link]) for wkt, link in rows],
                                            fields="imobil:string", geometry=geometry)
        cut = bts.BuildingTopologyTask(
            imobile=[LayerSnapshot(imobile)], constructii=[LayerSnapshot(constructii)],
            buffer_m=0.002, threshold=1.0, join_fields=[], link_field="imobil",
            key_field="id", **cut_options)
        self.assertTrue(cut.run(), cut.error_msg)
        options = {} if check_tolerance is None else {"building_tolerance": check_tolerance}
        check = tcs.TopologyCheckTask(
            imobile=[LayerSnapshot(imobile)], constructii=[LayerSnapshot(constructii)],
            id_field="id", link_field="imobil", **options)
        self.assertTrue(check.run(), check.error_msg)
        return cut.results[0], [f for f in check.findings
                                if f.kind in (tcs.BUILDING_OUTSIDE, tcs.BUILDING_ELSEWHERE)]

    def test_a_wall_on_a_slanted_limit_is_left_as_it_is(self):
        imobile = helpers.polygon_layer("Imobile", [(self.SLANTED, ["1"])], fields="id:string")
        # the points miss the limit by less than a thousandth of a millimetre, which is
        # nothing at the tolerance of 1 cm and nothing with no tolerance at all
        for options in ({}, {"check_tolerance": 0.0, "tolerance": 0.0}):
            rows, found = self.both([(self.ON_THE_LIMIT, "1")], imobile, **options)
            self.assertEqual(found, [], options)
            self.assertEqual((rows[0]["stare"], rows[0]["sup_pierduta"]), (bts.UNCHANGED, 0.0),
                             options)
            self.assertTrue(helpers.same_geometry(rows[0]["geometry"],
                                                  QgsGeometry.fromWkt(self.ON_THE_LIMIT)),
                            options)

    def test_an_overhang_under_the_tolerance_is_left_and_one_over_it_is_reported_and_cut(self):
        # 9 mm and 11 mm over the limit between the two imobile, at the tolerance of 1 cm
        rows, found = self.both([(f"POLYGON({box(15, 5, 20.009, 10)})", "1"),
                                 (f"POLYGON({box(15, 12, 20.011, 17)})", "1")])
        self.assertEqual([row["stare"] for row in rows], [bts.UNCHANGED, bts.FIXED])
        self.assertEqual([f.detail for f in found], [
            "Un punct al construcției Construcții #2 iese din imobilul 1 cu 1,1 cm "
            "(0,055 mp în afară)."])
        self.assertAlmostEqual(rows[1]["geometry"].boundingBox().xMaximum(), 19.998, places=6)

    def test_a_wall_across_a_notch_of_the_limit_is_reported_and_cut(self):
        # the north limit of the imobil has a notch 8 m wide and 10 m deep; the construcție
        # spans it, with 8 x 5 m in the notch and none of its points there
        notched = "POLYGON((0 0, 40 0, 40 40, 24 40, 24 30, 16 30, 16 40, 0 40, 0 0))"
        imobile = helpers.polygon_layer("Imobile", [(notched, ["1"])], fields="id:string")
        rows, found = self.both([(f"POLYGON({box(10, 25, 30, 35)})", "1")], imobile)
        self.assertEqual([(f.detail, f.value, f.unit) for f in found], [
            ("O latură a construcției Construcții #1 iese din imobilul 1 (40 mp în afară).",
             40.0, "mp")])
        self.assertEqual(rows[0]["stare"], bts.LOSS)
        # the ground in the notch and the 2 mm of the retreat along its three sides
        self.assertAlmostEqual(rows[0]["sup_pierduta"], 40.036, places=2)
        self.assertTrue(rows[0]["geometry"].within(QgsGeometry.fromWkt(notched)))

    def test_a_wall_across_a_notch_is_judged_by_the_width_of_the_ground_outside(self):
        notched = "POLYGON((0 0, 40 0, 40 40, 24 40, 24 30, 16 30, 16 40, 0 40, 0 0))"
        imobile = helpers.polygon_layer("Imobile", [(notched, ["1"])], fields="id:string")
        # the north wall lies 9 mm past the bottom of the notch, at the tolerance of 1 cm
        rows, found = self.both([(f"POLYGON({box(10, 25, 30, 30.009)})", "1")], imobile)
        self.assertEqual(found, [])
        self.assertEqual((rows[0]["stare"], rows[0]["sup_pierduta"]), (bts.UNCHANGED, 0.0))
        # and 11 mm past it
        rows, found = self.both([(f"POLYGON({box(10, 25, 30, 30.011)})", "1")], imobile)
        self.assertEqual([(f.detail, f.value, f.unit) for f in found], [
            ("O latură a construcției Construcții #1 iese din imobilul 1 (0,088 mp în afară).",
             0.088, "mp")])
        self.assertEqual(rows[0]["stare"], bts.FIXED)
        self.assertTrue(rows[0]["geometry"].within(QgsGeometry.fromWkt(notched)))

    def test_with_no_tolerance_every_point_outside_the_imobil_is_cut(self):
        rows, found = self.both([(f"POLYGON({box(15, 5, 20.005, 10)})", "1")],
                                check_tolerance=0.0, tolerance=0.0)
        self.assertEqual([row["stare"] for row in rows], [bts.FIXED])
        self.assertEqual([f.detail for f in found], [
            "Un punct al construcției Construcții #1 iese din imobilul 1 cu 0,5 cm "
            "(0,025 mp în afară)."])

    def test_a_part_the_cut_removes_entirely_is_named_as_removed(self):
        # one body inside imobil 1, one on imobil 2
        rows, _found = self.both([(f"MULTIPOLYGON(({box(2, 2, 12, 12)}), ({box(25, 5, 30, 10)}))",
                                   "1")], geometry="MultiPolygon")
        self.assertEqual(rows[0]["stare"], bts.LOSS)
        self.assertEqual(rows[0]["obs_topo"], "Pierde 25 mp (20%). O parte a construcției a "
                                              "fost eliminată: după tăiere nu a rămas nimic "
                                              "din ea.")
        self.assertAlmostEqual(rows[0]["geometry"].area(), 100.0, places=6)

    def test_a_neighbour_without_an_id_is_named_as_the_check_names_it(self):
        imobile = helpers.polygon_layer("Imobile", [
            (f"POLYGON({box(0, 0, 20, 20)})", ["1"]),
            (f"POLYGON({box(20, 0, 40, 20)})", [None])], fields="id:string")
        rows, found = self.both([(f"POLYGON({box(18, 5, 30, 15)})", "1")], imobile)
        self.assertEqual([f.other for f in found], ["imobil Imobile #2"])
        self.assertEqual(rows[0]["stare"], bts.ELSEWHERE)
        self.assertIn("pe imobilul Imobile #2 (100 mp din 120 mp)", rows[0]["obs_topo"])

    def test_a_distance_under_a_millimetre_is_shown_in_tenths_of_a_millimetre(self):
        # two tenths of a millimetre over the limit
        _rows, found = self.both([(f"POLYGON({box(15, 5, 20.0002, 10)})", "1")],
                                 check_tolerance=0.0)
        self.assertEqual([f.detail for f in found], [
            "Un punct al construcției Construcții #1 iese din imobilul 1 cu 0,2 mm "
            "(0,001 mp în afară)."])


if __name__ == "__main__":
    helpers.main()
