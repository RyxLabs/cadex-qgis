# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""CGXML to plain records: one imobil per file, its construcții, rings as coordinate lists.
Pure Python, safe on a worker thread."""

# CGXML facts relied on here, for eTerra exports and deliveries alike:
# - a file holds exactly one Land
# - eTerra exports leave CADSECTOR empty; their CADGENNO may be filled, empty or 0 (ANCPI's
#   ERR49 treats 0 as missing), and an imobil without one is known by its E2IDENTIFIER
# - a construcție's E2IDENTIFIER looks like "100001-C1", so it stays text
# - Points are ordered by NO, then POINTID. eTerra closes every ring by repeating its first
#   point, deliveries leave a single ring open
# - a ring ends where its first point repeats; the next ring starts right after

import math
import re
# the files are the user's own; expat never fetches external entities and refuses entity
# expansion attacks
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict

from . import codes

MAX_FILE_BYTES = 50 * 1024 * 1024

# registrations naming the owner or possessor; part III burdens and notes never do
OWNER_REGISTRATIONS = frozenset(("INTAB", "POSESION_REGISTATION",
                                 "PROVISIONALENTRY"))

_NATURAL = re.compile(r"(\d+)")


class CgxmlError(Exception):
    """An unreadable CGXML file. The message, in Romanian, is shown to the user."""


def _natural_key(text):
    return [int(p) if p.isdigit() else p.lower()
            for p in _NATURAL.split(str(text))]


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _text(element, tag: str) -> str:
    if element is None:
        return ""
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _float(value: str):
    """The number in value, None when it is empty or not a finite number. float() also reads
    "nan" and "inf", which a script writes for an empty spreadsheet cell."""
    try:
        number = float(value) if value else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


def _int(value: str):
    """int for an integer string, "12.0" included, else None."""
    number = _float(value)
    if number is None:
        return None
    return int(number) if number == int(number) else None


def _yes_no(value: str) -> str:
    """Da / Nu for an XSD boolean, empty for a missing value."""
    value = value.lower()
    if value in ("true", "1"):
        return "Da"
    if value in ("false", "0"):
        return "Nu"
    return ""


def _unique_join(values, sep=", "):
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return sep.join(seen)


def _identity(code: str) -> str:
    """A person's IDCODE when it identifies someone, else "": deliveries write a run of 0 or
    of 9 for an unknown code."""
    code = code.strip()
    return "" if not code or set(code) in ({"0"}, {"9"}) else code


def _join_persons(entries, sep=", ") -> str:
    """The texts of (text, identity) entries, each once, except that a text shared by persons
    with different identities, two co-owners with the same name, is repeated for each."""
    identities = {}
    for text, identity in entries:
        if text:
            found = identities.setdefault(text, set())
            if identity:
                found.add(identity)
    return sep.join(text for text, found in identities.items()
                    for _person in range(max(1, len(found))))


def land_class(flags) -> str:
    """Intravilan, Extravilan or Mixt from the parcels' INTRAVILAN values, empty without
    parcels. A missing value counts as extravilan."""
    if not flags:
        return ""
    inside = [f.lower() in ("true", "1") for f in flags]
    if all(inside):
        return "Intravilan"
    if not any(inside):
        return "Extravilan"
    return "Mixt"


# --- rings ---

def _ring_area(ring) -> float:
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _point_in_ring(x, y, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def split_rings(points, eps=1e-6):
    """Open rings from points in NO order.

    A ring ends where its first point repeats; leftover points form a last ring. Repeated
    consecutive vertices are dropped, rings with fewer than 3 points discarded.
    """
    rings = []
    current = []
    for x, y in points:
        if current and abs(x - current[0][0]) < eps and abs(y - current[0][1]) < eps:
            rings.append(current)
            current = []
            continue
        if current and abs(x - current[-1][0]) < eps and abs(y - current[-1][1]) < eps:
            continue
        current.append((x, y))
    if current:
        rings.append(current)
    return [r for r in rings if len(r) >= 3]


def _ring_inside(ring, other) -> bool:
    # majority vote: a hole may touch its shell at a vertex, which tests either way
    inside = sum(1 for x, y in ring if _point_in_ring(x, y, other))
    return inside * 2 > len(ring)


def organize_rings(rings):
    """Polygon parts as [exterior, hole, ...] lists, largest part first.

    A ring nested in an even number of rings is an exterior, in an odd number a hole of the
    ring directly around it, so an island inside a hole is a part of its own. Zero-area rings
    are dropped.
    """
    ordered = sorted((r for r in rings if abs(_ring_area(r)) > 1e-6),
                     key=lambda r: abs(_ring_area(r)), reverse=True)
    parent = []
    depth = []
    for i, ring in enumerate(ordered):
        # walking back from the next larger ring finds the smallest one containing it
        holder = next((j for j in range(i - 1, -1, -1)
                       if _ring_inside(ring, ordered[j])), None)
        parent.append(holder)
        depth.append(0 if holder is None else depth[holder] + 1)
    parts = {i: [ring] for i, ring in enumerate(ordered) if depth[i] % 2 == 0}
    for i, ring in enumerate(ordered):
        if depth[i] % 2 == 1:
            parts[parent[i]].append(ring)
    return [parts[i] for i in sorted(parts)]


# --- reading ---

def _parse_error(error) -> str:
    """The expat error as a Romanian sentence, with the line number."""
    from xml.parsers.expat import errors
    line = error.position[0] if getattr(error, "position", None) else 0
    code = getattr(error, "code", 0)
    messages = {
        errors.codes[errors.XML_ERROR_NO_ELEMENTS]:
            "fișierul nu are conținut sau este incomplet",
        errors.codes[errors.XML_ERROR_INVALID_TOKEN]:
            "conține caractere nevalide, de obicei o codificare greșită "
            "(fișierul nu este salvat ca UTF-8)",
        errors.codes[errors.XML_ERROR_UNDEFINED_ENTITY]:
            "folosește o entitate XML nedefinită",
        errors.codes[errors.XML_ERROR_TAG_MISMATCH]:
            "are un element închis greșit",
        errors.codes[errors.XML_ERROR_UNCLOSED_TOKEN]:
            "se termină în mijlocul unui element",
    }
    text = messages.get(code, "nu este un XML valid")
    return f"{text} (rândul {line})" if line else text


def parse_bytes(data: bytes, source: str = ""):
    """(imobile, constructii, warnings) of one CGXML document. Raises CgxmlError for
    anything that is not a CGXML with a Land."""
    if not data.strip():
        raise CgxmlError("fișierul nu are conținut")
    # expat refuses a document with anything before the XML declaration but a BOM
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    data = bom + data[len(bom):].lstrip(b" \t\r\n")
    try:
        root = ET.fromstring(data)  # nosec B314
    except ET.ParseError as e:
        raise CgxmlError(_parse_error(e)) from None
    if _local(root.tag).lower() != "cgxml":
        raise CgxmlError("nu este un fișier CGXML")

    by_tag = defaultdict(list)
    for child in root:
        for element in child.iter():
            element.tag = _local(element.tag)
        by_tag[child.tag.lower()].append(child)

    lands = by_tag.get("land", [])
    if not lands:
        raise CgxmlError("fișierul nu conține niciun imobil (Land)")

    warnings = []
    addresses = {_text(a, "ADDRESSID"): a for a in by_tag.get("address", [])}
    parcels = defaultdict(list)
    for parcel in by_tag.get("parcel", []):
        parcels[_text(parcel, "LANDID")].append(parcel)
    buildings = defaultdict(list)
    for building in by_tag.get("building", []):
        buildings[_text(building, "LANDID")].append(building)

    points = defaultdict(list)
    # an entity with an unreadable coordinate gets no geometry: skipping the point would
    # build a wrong polygon
    unreadable = {}
    for point in by_tag.get("points", []):
        land_id = _text(point, "IMMOVABLEID")
        owner = ("L", land_id) if land_id else ("B", _text(point, "BUILDINGID"))
        x = _float(_text(point, "X"))
        y = _float(_text(point, "Y"))
        if x is None or y is None:
            unreadable.setdefault(owner, _text(point, "NO") or "?")
            continue
        points[owner].append((_natural_key(_text(point, "NO")),
                              _int(_text(point, "POINTID")) or 0, x, y))

    def has_points(kind, entity_id):
        return (kind, entity_id) in unreadable or bool(points.get((kind, entity_id)))

    def rings_of(kind, entity_id):
        what, its = ("imobilului", "lui") if kind == "L" else ("construcției", "ei")
        if (kind, entity_id) in unreadable:
            warnings.append(
                f"punctul {unreadable[(kind, entity_id)]} are coordonate care nu "
                f"sunt numere, deci geometria {what} nu a fost construită")
            return []
        pts = sorted(points.get((kind, entity_id), []),
                     key=lambda p: (p[0], p[1]))
        rings = organize_rings(split_rings([(p[2], p[3]) for p in pts]))
        if pts and not rings:
            # too few points, or an outline that crosses itself so that its signed area is 0
            warnings.append(f"punctele {what} nu închid o suprafață, deci geometria {its} "
                            "nu a fost construită")
        return rings

    owners, others = _persons(by_tag)

    imobile = []
    constructii = []
    for land in lands:
        land_id = _text(land, "LANDID")
        address = addresses.get(_text(land, "ADDRESSID"))
        land_parcels = parcels.get(land_id, [])
        cadgenno = _text(land, "CADGENNO")
        if cadgenno == "0":
            cadgenno = ""
        e2id = _text(land, "E2IDENTIFIER")
        sector_raw = _text(land, "CADSECTOR")
        tip_teren = land_class([_text(p, "INTRAVILAN") for p in land_parcels])

        land_id_num = _int(cadgenno)
        if cadgenno and land_id_num is None:
            warnings.append(f"ID nenumeric '{cadgenno}'")
        ie_num = _int(e2id)
        if e2id and ie_num is None:
            warnings.append(f"IE nenumeric '{e2id}'")

        sirsup = _text(address, "SIRSUP")
        siruta_code = _text(address, "SIRUTA")
        record = {
            "fisier": source,
            "id": land_id_num,
            "ie": ie_num,
            # as written, for pairing files: the numbers above lose leading zeros and letters
            "id_text": cadgenno,
            "ie_text": e2id,
            "is_new": {"Da": True, "Nu": False}.get(_yes_no(_text(land, "ISNEW"))),
            "sector": _int(sector_raw),
            "uat_siruta": sirsup,
            "loc_siruta": siruta_code,
            "adresa_parts": _address_parts(address),
            "desc_adresa": _text(address, "DESCRIPTION"),
            "nr_postal": _text(address, "POSTALNUMBER"),
            "tip_teren": tip_teren,
            "nr_tarla": _unique_join(_text(p, "LANDPLOTNO") for p in land_parcels),
            "nr_parcele": _unique_join(_text(p, "PARCELNO") for p in land_parcels),
            "categ_fol": _unique_join(_text(p, "USECATEGORY") for p in land_parcels),
            "nr_titlu": _unique_join(_text(p, "TITLENO") for p in land_parcels),
            "imprej": _yes_no(_text(land, "ENCLOSED")),
            "sup_mas": _float(_text(land, "MEASUREDAREA")),
            "sup_acte": _float(_text(land, "PARCELLEGALAREA")),
            "propr": _join_persons(owners.get(("L", land_id), [])),
            # "; " because a role can hold a comma: "(proprietate, C1)"
            "alte_pers": _join_persons(others.get(("L", land_id), []), "; "),
            "nr_cad_vechi": _text(land, "PAPERCADNO"),
            "nr_cf_vechi": _text(land, "PAPERLBNO"),
            "note": _text(land, "NOTES"),
            "rings": rings_of("L", land_id),
            # with empty rings: True when the file has points from which no geometry was built
            "_has_points": has_points("L", land_id),
        }
        imobile.append(record)

        for building in sorted(buildings.get(land_id, []),
                               key=lambda b: _natural_key(_text(b, "BUILDNO"))):
            building_id = _text(building, "BUILDINGID")
            buildno = _text(building, "BUILDNO")
            constructii.append({
                "fisier": source,
                "id": land_id_num,
                "ie_imobil": ie_num,
                "sector": record["sector"],
                "nr_constr": f"C{buildno}" if buildno else "",
                "ie": _text(building, "E2IDENTIFIER"),
                "tip_teren": tip_teren,
                "destinatie": _text(building, "BUILDINGDESTINATION"),
                "etaje": _int(_text(building, "LEVELSNO")),
                "sup_mas": _float(_text(building, "MEASUREDAREA")),
                "sup_tot": _float(_text(building, "TOTALAREA")),
                "are_acte": _yes_no(_text(building, "ISLEGAL")),
                "propr": _join_persons(owners.get(("B", building_id), [])),
                "alte_pers": _join_persons(others.get(("B", building_id), []), "; "),
                "note": _text(building, "NOTES"),
                "rings": rings_of("B", building_id),
                "_has_points": has_points("B", building_id),
            })
    return imobile, constructii, warnings


def _role(kind: str, right: str) -> str:
    """The role written after the names of a registration's persons: "ipotecă", "notare",
    "posesie", "concesiune, înscriere provizorie"..."""
    if kind == "NOTATION":
        return codes.label(codes.REGISTRATIONTYPE, kind)
    if kind in ("POSESION_REGISTATION", "PROVISIONALENTRY"):
        entry = codes.label(codes.REGISTRATIONTYPE, kind)
        if right in ("", "PROPRIETATE"):
            return entry
        return f"{codes.label(codes.RIGHTTYPE, right)}, {entry}"
    return (codes.label(codes.RIGHTTYPE, right) or codes.label(codes.REGISTRATIONTYPE, kind)
            or "înscriere")


def _persons(by_tag):
    """(owners, others) for the targets ("L", land id) and ("B", building id).

    owners: [(name, identity)] from property, possession and provisional registrations.
    others: every other person as ("name (role)", identity). A person linked only to a
    building or an individual unit is also listed on the land, with the place: "name
    (proprietate, C1)". A person without a registration, and a registration without links,
    belong to the file's Land. identity is the person's IDCODE when it is not a placeholder.
    """
    registrations = {}
    for reg in by_tag.get("registration", []):
        kind = _text(reg, "REGISTRATIONTYPE").upper()
        right = _text(reg, "RIGHTTYPE").upper()
        # part II also holds ADMINISTRARE and CONCESIUNE, and a mortgage creditor is an INTAB
        # too; neither is the owner
        owner = (kind in OWNER_REGISTRATIONS and right in ("", "PROPRIETATE")
                 and _text(reg, "LBPARTNO") != "3")
        registrations[_text(reg, "REGISTRATIONID")] = (owner, _role(kind, right))

    land_ids = [_text(land, "LANDID") for land in by_tag.get("land", [])]
    buildings = {_text(b, "BUILDINGID"): (_text(b, "LANDID"), _text(b, "BUILDNO"))
                 for b in by_tag.get("building", [])}
    units = {_text(u, "IUID"): (_text(u, "BUILDINGID"), _text(u, "IDENTIFIER"))
             for u in by_tag.get("iu", [])}

    links = defaultdict(set)
    for row in by_tag.get("registrationxentity", []):
        for tag, kind in (("LANDID", "L"), ("BUILDINGID", "B"), ("IUID", "U")):
            value = _text(row, tag)
            if value:
                links[_text(row, "REGISTRATIONID")].add((kind, value))

    def lands_of(building_id):
        land = buildings.get(building_id, ("", ""))[0]
        return [land] if land else land_ids

    owners, others = defaultdict(list), defaultdict(list)
    for person in by_tag.get("person", []):
        name = " ".join(filter(None, (_text(person, "LASTNAME"),
                                      _text(person, "FIRSTNAME"))))
        if not name:
            continue
        identity = _identity(_text(person, "IDCODE"))
        reg_id = _text(person, "REGISTRATIONID")
        if reg_id not in registrations:
            for land_id in land_ids:
                others[("L", land_id)].append((f"{name} (fără înscriere)", identity))
            continue
        owner, role = registrations[reg_id]
        targets = links.get(reg_id) or {("L", land_id) for land_id in land_ids}
        on_land = {value for kind, value in targets if kind == "L"}
        for kind, value in sorted(targets):
            if kind == "U":
                building_id, place = units.get(value, ("", ""))
                place = place or "unitate individuală"
                if building_id:
                    others[("B", building_id)].append((f"{name} ({role}, {place})", identity))
            else:
                if owner:
                    owners[(kind, value)].append((name, identity))
                else:
                    others[(kind, value)].append((f"{name} ({role})", identity))
                if kind == "L":
                    continue
                building_id = value
                buildno = buildings.get(value, ("", ""))[1]
                place = f"C{buildno}" if buildno else "construcție"
            for land_id in lands_of(building_id):
                if land_id not in on_land:
                    others[("L", land_id)].append((f"{name} ({role}, {place})", identity))
    return owners, others


def _address_parts(address):
    """The street part of an address as (element, text) pairs: ("STREETNAME", "Strada
    Mare"), ("POSTALNUMBER", "Nr. 10"). UAT and locality are added later from SIRUTA."""
    if address is None:
        return []
    parts = []
    district = _text(address, "DISTRICTNAME")
    if district:
        kind = codes.label(codes.DISTRICTTYPE, _text(address, "DISTRICTTYPE"))
        parts.append(("DISTRICTNAME", f"{kind} {district}".strip()))
    street = _text(address, "STREETNAME")
    if street:
        kind = codes.label(codes.STREETTYPE, _text(address, "STREETTYPE"))
        parts.append(("STREETNAME", f"{kind or 'Strada'} {street}"))
    for tag, prefix in (("POSTALNUMBER", "Nr."), ("BLOCK", "bl."),
                        ("ENTRY", "sc."), ("FLOOR", "et."), ("APNO", "ap.")):
        value = _text(address, tag)
        if value:
            parts.append((tag, f"{prefix} {value}"))
    return parts


def read_file(path: str, source: str = ""):
    """parse_bytes() of a file, refusing files over MAX_FILE_BYTES."""
    import os
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise CgxmlError(f"fișier prea mare ({size // (1024 * 1024)} MB)")
    with open(path, "rb") as fp:
        return parse_bytes(fp.read(), source or os.path.basename(path))
