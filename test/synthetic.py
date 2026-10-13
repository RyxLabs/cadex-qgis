# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic CGXML files; every name, number and coordinate is made up.

One imobil per file, in the eTerra style (every ring closed by repeating its first point) or
the delivery style (a single ring left open).
"""

import math
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

Ring = List[Tuple[float, float]]


@dataclass
class Building:
    number: int
    rings: List[Ring]
    destination: str = "CL"
    levels: Optional[int] = 1
    legal: Optional[bool] = True
    ie: str = ""
    total_area: Optional[float] = None


@dataclass
class Land:
    rings: List[Ring]
    cadgenno: str = ""
    ie: str = ""
    sector: str = ""
    sirsup: str = "40900"
    siruta: str = "40900"
    measured_area: Optional[float] = None      # None: the net area of the rings
    legal_area: Optional[float] = None
    enclosed: Optional[bool] = None
    is_new: Optional[bool] = None
    notes: str = ""
    street: str = ""
    postal_number: str = ""
    owners: Tuple[str, ...] = ("POPESCU ION",)
    creditor: str = ""                          # an ipoteca in part III, never an owner
    noted: str = ""                             # a person on a notation, never an owner
    parcels: Tuple[Tuple[str, bool], ...] = (("A", True),)   # (use category, intravilan)
    plot: str = ""
    buildings: List[Building] = field(default_factory=list)
    style: str = "livrare"                      # or "eterra"


def ring_area(ring: Ring) -> float:
    total = 0.0
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def square(x, y, size) -> Ring:
    return [(x, y), (x + size, y), (x + size, y + size), (x, y + size)]


def net_area(rings: List[Ring]) -> float:
    """Area of concentric rings nested as exterior, hole, island...: even levels add, odd
    levels subtract."""
    return sum(ring_area(r) * (1 if level % 2 == 0 else -1) for level, r in enumerate(rings))


def _point_rows(owner_tag, owner_id, rings, style, first_id):
    """Points elements for the rings, POINTID and NO counting up from first_id."""
    rows = []
    number = first_id
    closed = style == "eterra" or len(rings) > 1
    for ring in rings:
        points = list(ring) + ([ring[0]] if closed else [])
        for x, y in points:
            rows.append(f"<Points><POINTID>{number}</POINTID><{owner_tag}>{owner_id}</{owner_tag}>"
                        f"<NO>{number}</NO><X>{x:.3f}</X><Y>{y:.3f}</Y></Points>")
            number += 1
    return rows, number


def _flag(value):
    return "" if value is None else ("true" if value else "false")


def cgxml_bytes(land: Land) -> bytes:
    rows = ['<?xml version="1.0" encoding="UTF-8"?>', "<CGXML>"]
    rows.append(f"<Address><ADDRESSID>1</ADDRESSID><SIRSUP>{land.sirsup}</SIRSUP>"
                f"<SIRUTA>{land.siruta}</SIRUTA><INTRAVILAN>true</INTRAVILAN>"
                + (f"<STREETTYPE>STR</STREETTYPE><STREETNAME>{escape(land.street)}</STREETNAME>"
                   if land.street else "")
                + (f"<POSTALNUMBER>{escape(land.postal_number)}</POSTALNUMBER>"
                   if land.postal_number else "")
                + "</Address>")
    area = land.measured_area if land.measured_area is not None else round(net_area(land.rings), 2)
    parts = [f"<LANDID>1</LANDID><CADSECTOR>{land.sector}</CADSECTOR><ADDRESSID>1</ADDRESSID>",
             f"<MEASUREDAREA>{area}</MEASUREDAREA>"]
    if land.legal_area is not None:
        parts.append(f"<PARCELLEGALAREA>{land.legal_area}</PARCELLEGALAREA>")
    if land.is_new is not None:
        parts.append(f"<ISNEW>{_flag(land.is_new)}</ISNEW>")
    if land.enclosed is not None:
        parts.append(f"<ENCLOSED>{_flag(land.enclosed)}</ENCLOSED>")
    parts.append(f"<E2IDENTIFIER>{land.ie}</E2IDENTIFIER><CADGENNO>{land.cadgenno}</CADGENNO>")
    if land.notes:
        parts.append(f"<NOTES>{escape(land.notes)}</NOTES>")
    rows.append("<Land>" + "".join(parts) + "</Land>")
    for i, (category, intravilan) in enumerate(land.parcels, start=1):
        rows.append(f"<Parcel><PARCELID>{i}</PARCELID><LANDID>1</LANDID><NUMBER>{i}</NUMBER>"
                    f"<USECATEGORY>{category}</USECATEGORY>"
                    f"<INTRAVILAN>{_flag(intravilan)}</INTRAVILAN>"
                    f"<LANDPLOTNO>{escape(land.plot)}</LANDPLOTNO></Parcel>")
    points, next_id = _point_rows("IMMOVABLEID", 1, land.rings, land.style, 1)
    rows.extend(points)
    for building in land.buildings:
        building_id = 100 + building.number
        rows.append(
            f"<Building><BUILDINGID>{building_id}</BUILDINGID><LANDID>1</LANDID>"
            f"<BUILDNO>{building.number}</BUILDNO>"
            f"<MEASUREDAREA>{round(net_area(building.rings), 2)}</MEASUREDAREA>"
            + (f"<TOTALAREA>{building.total_area}</TOTALAREA>"
               if building.total_area is not None else "")
            + f"<BUILDINGDESTINATION>{building.destination}</BUILDINGDESTINATION>"
            + (f"<LEVELSNO>{building.levels}</LEVELSNO>" if building.levels is not None else "")
            + (f"<ISLEGAL>{_flag(building.legal)}</ISLEGAL>"
               if building.legal is not None else "")
            + f"<E2IDENTIFIER>{building.ie}</E2IDENTIFIER></Building>")
        points, next_id = _point_rows("BUILDINGID", building_id, building.rings, land.style,
                                      next_id)
        rows.extend(points)

    owner_kind = "INTAB" if land.style == "eterra" else "POSESION_REGISTATION"
    registrations = [(1, owner_kind, "PROPRIETATE", 2, land.owners)]
    if land.creditor:
        registrations.append((2, "INTAB", "IPOTECA", 3, (land.creditor,)))
    if land.noted:
        registrations.append((3, "NOTATION", "", 2, (land.noted,)))
    link = 1
    person = 1
    for reg_id, kind, right, part, names in registrations:
        rows.append(f"<Registration><REGISTRATIONID>{reg_id}</REGISTRATIONID>"
                    f"<REGISTRATIONTYPE>{kind}</REGISTRATIONTYPE><RIGHTTYPE>{right}</RIGHTTYPE>"
                    f"<DEEDID>{reg_id}</DEEDID><LBPARTNO>{part}</LBPARTNO></Registration>")
        targets = [("LANDID", 1)]
        if reg_id == 1:
            targets += [("BUILDINGID", 100 + b.number) for b in land.buildings]
        for tag, target in targets:
            rows.append("<RegistrationXEntity>"
                        f"<REGISTRATIONXENTITYID>{link}</REGISTRATIONXENTITYID>"
                        f"<REGISTRATIONID>{reg_id}</REGISTRATIONID><{tag}>{target}</{tag}>"
                        "</RegistrationXEntity>")
            link += 1
        for name in names:
            last, _, first = name.partition(" ")
            rows.append(f"<Person><PERSONID>{person}</PERSONID>"
                        f"<REGISTRATIONID>{reg_id}</REGISTRATIONID>"
                        f"<FIRSTNAME>{escape(first)}</FIRSTNAME><LASTNAME>{escape(last)}</LASTNAME>"
                        "<IDCODE>9999999999999</IDCODE></Person>")
            person += 1
    rows.append("</CGXML>")
    return "\n".join(rows).encode("utf-8")


def write(folder, name, land: Land) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as fp:
        fp.write(cgxml_bytes(land))
    return path


def star(rng, cx, cy, radius) -> Ring:
    """A random star-shaped ring around (cx, cy), vertices 0.6 to 1 radius out. With 8 or
    more spread angles, neighbours are at most 90 degrees apart, so every edge stays at least
    0.42 radius from the centre."""
    count = rng.randint(8, 16)
    step = 2 * math.pi / count
    points = []
    for i in range(count):
        angle = (i + rng.uniform(0.1, 0.9)) * step
        reach = radius * rng.uniform(0.6, 1.0)
        points.append((round(cx + math.cos(angle) * reach, 3),
                       round(cy + math.sin(angle) * reach, 3)))
    return points


def random_land(rng: random.Random, index: int, style: str) -> Land:
    """A random imobil: a ring with a building in the middle, a ring with a hole, or a ring
    with an island in its hole."""
    cx = 500000 + (index % 20) * 400
    cy = 400000 + (index // 20) * 400
    radius = rng.uniform(20, 150)
    rings = [star(rng, cx, cy, radius)]
    kind = rng.choice(("plain", "plain", "hole", "island"))
    if kind in ("hole", "island"):
        # every exterior edge is at least 0.42 radius out, a hole reaching 0.4 stays clear of it
        rings.append(star(rng, cx, cy, radius * 0.4))
    if kind == "island":
        rings.append(star(rng, cx, cy, radius * 0.15))
    buildings = []
    if kind == "plain":
        side = min(10.0, radius * 0.5)
        buildings.append(Building(1, [square(cx - side / 2, cy - side / 2, side)],
                                  ie=f"{100000 + index}-C1" if style == "eterra" else ""))
    return Land(
        rings=rings, style=style,
        cadgenno="" if style == "eterra" else str(10000 + index),
        ie=str(100000 + index) if style == "eterra" or index % 3 == 0 else "",
        sector="" if style == "eterra" else str(1 + index % 4),
        owners=(f"PROPRIETAR{index} ION",),
        creditor="BANCA EXEMPLU SA" if index % 5 == 0 else "",
        buildings=buildings)
