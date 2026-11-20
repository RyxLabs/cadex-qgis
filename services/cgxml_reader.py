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
# - the Points of an imobil or a construcție follow one another in POINTID order, the order
#   ANCPI's own tool reads them in. NO is the name of a point and may start again at each
#   ring. eTerra closes every ring by repeating its first point, deliveries leave a single
#   ring open
# - a ring ends where its first point repeats; the next ring starts right after

import math
import re
# the files are the user's own; expat never fetches external entities, and _parse_xml
# refuses any DOCTYPE, so no entity declared in a file can expand
import xml.etree.ElementTree as ET  # nosec B405
from collections import defaultdict

from . import codes, fmt

MAX_FILE_BYTES = 50 * 1024 * 1024

# Stereo 70 x and y of a point in Romania, land or sea, lie between about 116,000 and
# 1,019,000 m. A coordinate left at 0, or written in degrees or millimetres, falls outside.
POINT_RANGE = (100000.0, 1100000.0)

# the most rings an imobil or construcție may have for its geometry to be built, a hundred
# times what a real one has; organize_rings compares each ring with the larger ones
MAX_RINGS = 1000

# square metres between the area of an outline and the measured area its file declares
# before the two are reported as different. A delivery declares the area of the outline
# rounded to the square metre, at most half a square metre away.
AREA_TOLERANCE = 1.0

# registrations naming the owner or possessor; part III burdens and notes never do
OWNER_REGISTRATIONS = frozenset(("INTAB", "POSESION_REGISTATION",
                                 "PROVISIONALENTRY"))

_NATURAL = re.compile(r"(\d+)")
_WHOLE = re.compile(r"[+-]?\d+(?:\.0*)?")
# the first whole number a 64-bit integer field does not hold
_INT_LIMIT = 2 ** 63

# the warnings for the elements that do not carry the LANDID of a Land of the file, each as
# (nouns, sentences when the file's only imobil takes them, sentences when they are left out)
_UNLINKED = {
    "parcel": (
        ("parcelă", "parcele"),
        ("{0} nu are LANDID-ul imobilului și a fost citită ca parcelă a lui",
         "{0} nu au LANDID-ul imobilului și au fost citite ca parcele ale lui"),
        ("{0} nu are LANDID-ul unui imobil din fișier și nu a fost citită",
         "{0} nu au LANDID-ul unui imobil din fișier și nu au fost citite")),
    "building": (
        ("construcție", "construcții"),
        ("{0} nu are LANDID-ul imobilului și a fost citită ca a lui",
         "{0} nu au LANDID-ul imobilului și au fost citite ca ale lui"),
        ("{0} nu are LANDID-ul unui imobil din fișier și nu a fost citită",
         "{0} nu au LANDID-ul unui imobil din fișier și nu au fost citite")),
    "point": (
        ("punct", "puncte"),
        ("{0} nu are IMMOVABLEID-ul imobilului și a fost citit ca punct al lui",
         "{0} nu au IMMOVABLEID-ul imobilului și au fost citite ca puncte ale lui"),
        ("{0} nu are IMMOVABLEID-ul unui imobil din fișier și nu a fost folosit",
         "{0} nu au IMMOVABLEID-ul unui imobil din fișier și nu au fost folosite")),
    # a point with the BUILDINGID of no Building has no outline to join
    "building point": (
        ("punct", "puncte"), None,
        ("{0} are BUILDINGID-ul unei construcții care nu este în fișier și nu a fost folosit",
         "{0} au BUILDINGID-ul unei construcții care nu este în fișier și nu au fost "
         "folosite")),
}


class CgxmlError(Exception):
    """An unreadable CGXML file. The message, in Romanian, is shown to the user."""


def _natural_key(text):
    # isdecimal, as \d: isdigit also accepts "²", which int() refuses
    return [int(p) if p.isdecimal() else p.lower()
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
    """int for a whole number, "12.0" included, that a 64-bit integer field holds, else None.
    The digits are read as they are: a float keeps only the first 15 to 17 of them."""
    if not _WHOLE.fullmatch(value):
        return None
    number = int(value.partition(".")[0])
    return number if -_INT_LIMIT <= number < _INT_LIMIT else None


def _number(value: str, parse, name, warnings):
    """The number in value, read by parse (_int or _float); None when value is empty or not a
    number. A value that is not a number adds a warning such as "sector nenumeric '12A'", or
    "ID prea lung '...'" for a whole number too large for a 64-bit field. name is (the
    field's noun, the form of "nenumeric" that agrees with it)."""
    number = parse(value)
    if value and number is None:
        noun, not_a_number = name
        too_long = parse is _int and _WHOLE.fullmatch(value)
        warnings.append(f"{noun} {'prea lung' if too_long else not_a_number} '{value}'")
    return number


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
    """Open rings from the points of an outline, in their order.

    A ring ends where its first point repeats; leftover points form a last ring. A point
    repeated right after itself is read once. When the repeat follows the point that closed
    a ring, it starts the next ring, as in two parts that share a corner, unless that ring
    closes on the point after the repeat: then the repeat was a closing point written twice
    and is dropped.
    Rings with fewer than 3 points are discarded.
    """
    def same(p, q):
        return abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps

    rings = []
    current = []
    previous = None
    # current[0] repeats the point the ring before closed on
    starts_on_closing = False
    for x, y in points:
        point = (x, y)
        if previous is not None and same(point, previous):
            if not current:
                current = [point]
                starts_on_closing = True
            continue
        previous = point
        if current and same(point, current[0]):
            rings.append(current)
        elif starts_on_closing and len(current) > 1 and same(point, current[1]):
            rings.append(current[1:])
        else:
            current.append(point)
            continue
        current = []
        starts_on_closing = False
    if current:
        rings.append(current)
    return [r for r in rings if len(r) >= 3]


def _ring_inside(ring, other) -> bool:
    # majority vote: a hole may touch its shell at a vertex, which tests either way
    inside = sum(1 for x, y in ring if _point_in_ring(x, y, other))
    return inside * 2 > len(ring)


def _bounds(ring):
    """(min x, min y, max x, max y) of a ring."""
    xs = [x for x, _y in ring]
    ys = [y for _x, y in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(box, other) -> bool:
    return (box[0] <= other[2] and other[0] <= box[2]
            and box[1] <= other[3] and other[1] <= box[3])


def organize_rings(rings):
    """Polygon parts as [exterior, hole, ...] lists, largest part first.

    A ring nested in an even number of rings is an exterior, in an odd number a hole of the
    ring directly around it, so an island inside a hole is a part of its own. Zero-area rings
    are dropped.
    """
    ordered = sorted((r for r in rings if abs(_ring_area(r)) > 1e-6),
                     key=lambda r: abs(_ring_area(r)), reverse=True)
    boxes = [_bounds(r) for r in ordered]
    parent = []
    depth = []
    for i, ring in enumerate(ordered):
        # walking back from the next larger ring finds the smallest one containing it; a ring
        # whose bounding box lies apart has no point inside
        holder = next((j for j in range(i - 1, -1, -1)
                       if _overlap(boxes[i], boxes[j]) and _ring_inside(ring, ordered[j])),
                      None)
        parent.append(holder)
        depth.append(0 if holder is None else depth[holder] + 1)
    parts = {i: [ring] for i, ring in enumerate(ordered) if depth[i] % 2 == 0}
    for i, ring in enumerate(ordered):
        if depth[i] % 2 == 1:
            parts[parent[i]].append(ring)
    return [parts[i] for i in sorted(parts)]


def _check_area(record, whose, warnings):
    """Add a warning when the record's outline does not have the measured area its file
    declares. A record without geometry or without a declared area, which a file also writes
    as 0, is not compared. whose is "imobilului" or "construcției C1"."""
    declared = record["sup_mas"]
    if not record["rings"] or not declared or declared < 0:
        return
    built = sum(abs(_ring_area(part[0])) - sum(abs(_ring_area(hole)) for hole in part[1:])
                for part in record["rings"])
    if abs(built - declared) > AREA_TOLERANCE:
        warnings.append(f"suprafața {whose} calculată din coordonate este {fmt.area(built)}, "
                        f"iar cea măsurată din fișier este {fmt.area(declared)}")


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


class _NoDoctype(ET.TreeBuilder):
    """Builds the tree of a document that may declare a document type, and refuses the
    declaration before any entity it defines is expanded."""

    def doctype(self, name, pubid, system):
        raise CgxmlError("conține o declarație DOCTYPE, pe care fișierele CGXML nu o au")


def _parse_xml(data: bytes):
    """The root element of the document. Raises CgxmlError when it declares a document
    type."""
    # a declaration holds the word as it is, here in an ASCII-based encoding or in UTF-16 of
    # either byte order; a document without the word is read by the parser's own tree
    # builder, which is faster than a subclass of it
    if b"DOCTYPE" not in data and b"D\x00O\x00C\x00T\x00Y\x00P\x00E" not in data:
        return ET.fromstring(data)  # nosec B314
    # no entity is read: the builder raises at the declaration
    parser = ET.XMLParser(target=_NoDoctype())  # nosec B314
    parser.feed(data)
    return parser.close()


def parse_bytes(data: bytes, source: str = ""):
    """(imobile, constructii, warnings) of one CGXML document. Raises CgxmlError for
    anything that is not a CGXML with a Land."""
    if not data.strip():
        raise CgxmlError("fișierul nu are conținut")
    # expat refuses a document with anything before the XML declaration but a BOM
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    data = bom + data[len(bom):].lstrip(b" \t\r\n")
    try:
        root = _parse_xml(data)
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
    land_ids = {_text(land, "LANDID") for land in lands}
    only_land = _text(lands[0], "LANDID") if len(lands) == 1 else None
    unlinked = defaultdict(int)

    def land_of(value, what):
        """The LANDID an element is read under: its own when a Land of the file has it, else
        that of the file's only Land. what counts the element for the warning."""
        if value in land_ids:
            return value
        unlinked[what] += 1
        return value if only_land is None else only_land

    addresses = {_text(a, "ADDRESSID"): a for a in by_tag.get("address", [])}
    parcels = defaultdict(list)
    for parcel in by_tag.get("parcel", []):
        parcels[land_of(_text(parcel, "LANDID"), "parcel")].append(parcel)
    buildings = defaultdict(list)
    for building in by_tag.get("building", []):
        buildings[land_of(_text(building, "LANDID"), "building")].append(building)
    building_ids = {_text(b, "BUILDINGID") for b in by_tag.get("building", [])}

    points = defaultdict(list)
    # an entity with a coordinate that is not a number, or not in Romania, gets no geometry:
    # skipping the point would build a wrong polygon
    unreadable = {}
    outside = {}
    low, high = POINT_RANGE
    for point in by_tag.get("points", []):
        # a construcție's point may name its imobil too, and a point of the imobil may carry
        # 0 where it has no construcție to name
        building_id = _text(point, "BUILDINGID")
        if building_id == "0" and "0" not in building_ids:
            building_id = ""
        if building_id and building_id not in building_ids:
            unlinked["building point"] += 1
            continue
        owner = (("B", building_id) if building_id
                 else ("L", land_of(_text(point, "IMMOVABLEID"), "point")))
        x = _float(_text(point, "X"))
        y = _float(_text(point, "Y"))
        if x is None or y is None:
            unreadable.setdefault(owner, _text(point, "NO") or "?")
            continue
        if not (low <= x <= high and low <= y <= high):
            outside.setdefault(owner, _text(point, "NO") or "?")
            continue
        points[owner].append((_int(_text(point, "POINTID")), x, y))
    for what, count in unlinked.items():
        nouns, kept, left_out = _UNLINKED[what]
        one, many = left_out if only_land is None or what == "building point" else kept
        warnings.append(fmt.plural(count, one, many).format(fmt.count(count, *nouns)))

    def has_points(kind, entity_id):
        key = (kind, entity_id)
        return key in unreadable or key in outside or bool(points.get(key))

    def rings_of(kind, entity_id):
        what, its = ("imobilului", "lui") if kind == "L" else ("construcției", "ei")
        for refused, reason in ((unreadable, "care nu sunt numere"),
                                (outside, "în afara României")):
            if (kind, entity_id) in refused:
                warnings.append(
                    f"punctul {refused[(kind, entity_id)]} are coordonate {reason}, deci "
                    f"geometria {what} nu a fost construită")
                return []
        pts = points.get((kind, entity_id), [])
        ids = [point_id for point_id, _x, _y in pts]
        # without a distinct whole POINTID on every point, the order of the file
        if None not in ids and len(set(ids)) == len(ids):
            pts = sorted(pts, key=lambda p: p[0])
        rings = split_rings([(x, y) for _id, x, y in pts])
        if len(rings) > MAX_RINGS:
            subject = "imobilul" if kind == "L" else "construcția"
            warnings.append(
                f"{subject} are {fmt.count(len(rings), 'contur', 'contururi')}, peste limita "
                f"de {fmt.number(MAX_RINGS, 0)}, deci geometria {its} nu a fost construită")
            return []
        rings = organize_rings(rings)
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
        tip_teren = land_class([_text(p, "INTRAVILAN") for p in land_parcels])

        land_id_num = _number(cadgenno, _int, ("ID", "nenumeric"), warnings)
        ie_num = _number(e2id, _int, ("IE", "nenumeric"), warnings)

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
            "sector": _number(_text(land, "CADSECTOR"), _int, ("sector", "nenumeric"),
                              warnings),
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
            "sup_mas": _number(_text(land, "MEASUREDAREA"), _float,
                               ("suprafață măsurată", "nenumerică"), warnings),
            "sup_acte": _number(_text(land, "PARCELLEGALAREA"), _float,
                                ("suprafață din acte", "nenumerică"), warnings),
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
        _check_area(record, "imobilului", warnings)

        for building in sorted(buildings.get(land_id, []),
                               key=lambda b: _natural_key(_text(b, "BUILDNO"))):
            building_id = _text(building, "BUILDINGID")
            buildno = _text(building, "BUILDNO")
            construction = {
                "fisier": source,
                "id": land_id_num,
                "ie_imobil": ie_num,
                "sector": record["sector"],
                "nr_constr": f"C{buildno}" if buildno else "",
                "ie": _text(building, "E2IDENTIFIER"),
                "tip_teren": tip_teren,
                "destinatie": _text(building, "BUILDINGDESTINATION"),
                "etaje": _number(_text(building, "LEVELSNO"), _int,
                                 ("număr de niveluri", "nenumeric"), warnings),
                "sup_mas": _number(_text(building, "MEASUREDAREA"), _float,
                                   ("suprafață construită la sol", "nenumerică"), warnings),
                "sup_tot": _number(_text(building, "TOTALAREA"), _float,
                                   ("suprafață construită desfășurată", "nenumerică"),
                                   warnings),
                "are_acte": _yes_no(_text(building, "ISLEGAL")),
                "propr": _join_persons(owners.get(("B", building_id), [])),
                "alte_pers": _join_persons(others.get(("B", building_id), []), "; "),
                "note": _text(building, "NOTES"),
                "rings": rings_of("B", building_id),
                "_has_points": has_points("B", building_id),
            }
            constructii.append(construction)
            _check_area(construction, f"construcției {construction['nr_constr']}".strip(),
                        warnings)
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
        return [land] if land in land_ids else land_ids

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
