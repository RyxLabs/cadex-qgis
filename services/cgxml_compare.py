# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparison of two sets of CGXML files.

Files pair by name or by IE, with the same rules as cadex.ro. Paired imobile are compared by
column and boundary, their construcții by number.
"""

# pairing rules:
# - by name, an unpaired file is looked up again by CADGENNO, then by IE, so a renamed file
#   is not counted as one removed and one added; a delivery's CADGENNO is unique per UAT, its
#   IE is not: the pieces of a split eTerra imobil share it, so the IE pass uses only the IEs
#   that a single leftover file holds on each side
# - IE and CADGENNO are unique per UAT only, so with several UATs on either side a key pairs
#   within its own UAT
# - a key repeated on one side keeps the first file by name; the others are listed as ignored
# - by IE, the eTerra export may be either source: the comparison runs with it as the old
#   source and its outcomes are turned back to the order chosen (_turn_around)
# - by IE, a delivery imobil without an IE exists only in the delivery
# - by IE, an IE repeated in the delivery is an eTerra imobil split by the systematic work:
#   the eTerra imobil is compared with all its pieces together
# - by IE, an eTerra export fills other columns than a delivery and leaves some imobile and
#   construcții without coordinates; a value only one side has is no change, and eTerra
#   writes 0 for an area or a number of levels it does not have

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional

from qgis.core import Qgis, QgsFeature, QgsField, QgsGeometry  # type: ignore

from . import cgxml_columns, cgxml_export, cgxml_sources, fmt, roles, siruta, stereo70, xlsx_report
from .compare_service import (
    CHANGE_KIND_FIELD, GEOMETRY, ResultFeatures, change_kind, comparable, geometry_change,
    kind_rows, normalize_key, normalized_differ, values_differ,
)
from .layer_sources import LayerInfo, unique_name
from .tasks import CadexTask
from ..qt_compat import FIELD_DOUBLE, FIELD_STRING

FILENAME = "filename"
IE = "ie"
MODES = (FILENAME, IE)
MODE_NAMES = {FILENAME: "După numele fișierului",
              IE: "După identificatorul electronic (IE)"}
MODE_HINTS = {
    FILENAME: "Fișierele se potrivesc după nume. Un fișier fără pereche se caută apoi după "
              "numărul cadastral și după IE, deci un fișier redenumit nu apare ca imobil "
              "dispărut și apărut.",
    IE: "Fișierele se potrivesc după IE, oricum s-ar numi. Când una dintre surse este un export "
        "eTerra, un imobil din eTerra dezmembrat se compară cu toate imobilele din cealaltă "
        "sursă care îi păstrează IE-ul, iar imobilele fără IE apar doar în sursa lor.",
}

OLD_SIDE = "Sursa veche"
NEW_SIDE = "Sursa nouă"
WKB_TYPE = Qgis.WkbType.MultiPolygon
ONLY_OLD = "Doar în sursa veche"
ONLY_NEW = "Doar în sursa nouă"
# the result categories as the result window, the Excel report and Processing name them
IMOBILE_ADDED = "Imobile apărute"
IMOBILE_DELETED = "Imobile dispărute"
IMOBILE_CHANGED = "Imobile modificate"
IMOBILE_UNCHANGED = "Imobile nemodificate"
BUILDINGS_ADDED = "Construcții apărute"
BUILDINGS_DELETED = "Construcții dispărute"
BUILDINGS_CHANGED = "Construcții modificate"
BUILDINGS_UNCHANGED = "Construcții nemodificate"

# checked by default; the SIRUTA names repeat the address and the notes are free text
DEFAULT_COLUMNS = ("sector", "adresa", "tip_teren", "nr_tarla", "nr_parcele", "categ_fol",
                   "nr_titlu", "imprej", "sup_mas", "sup_acte", "propr", "nr_cad_vechi",
                   "nr_cf_vechi")
# the pairing keys
NOT_COMPARED = ("id", "ie")
# checked by default for construcții
BUILDING_COLUMNS = ("destinatie", "etaje", "sup_mas", "sup_tot", "are_acte")
# a construcție pairs by its number within its imobil, whose columns it repeats
BUILDING_NOT_COMPARED = ("id", "ie_imobil", "nr_constr", "sector", "tip_teren")


def comparable_columns():
    """(key, label) of the comparable imobil columns, in catalog order."""
    return [(c.key, c.label) for c in cgxml_columns.IMOBILE if c.key not in NOT_COMPARED]


def _converted(source):
    """(Conversie CGXML batch, {field: CGXML column}) of a layer or of its LayerInfo."""
    if isinstance(source, LayerInfo):
        return source.batch, source.cgxml_columns
    return roles.batch_of(source), roles.columns_of(source)


def converted_columns(old, new, keys):
    """For two layers from Conversie CGXML, their columns of keys, e.g. DEFAULT_COLUMNS, so
    that comparing them as layers gives what comparing the files gives; None for other
    layers. old and new are layers or their LayerInfo."""
    if old is None or new is None:
        return None
    sides = [_converted(source) for source in (old, new)]
    if not all(batch for batch, _columns in sides):
        return None
    return {name for _batch, columns in sides for name, column in columns.items()
            if column in keys}


def is_eterra_export(layer) -> bool:
    """Whether a layer from Conversie CGXML holds an eTerra export: every imobil of a delivery
    has its cadastral sector, those of an eTerra export none. The cadastral numbers some of its
    imobile have are not the IDs of a delivery. layer may be its LayerInfo."""
    if isinstance(layer, LayerInfo):
        return layer.eterra
    if layer is None or not roles.batch_of(layer):
        return False
    index = layer.fields().indexOf(roles.column_of(layer, "sector") or "")
    return index >= 0 and all(normalize_key(v) is None for v in layer.uniqueValues(index, 2))


def eterra_advice(old, new, names=("Layerul vechi", "Layerul nou")):
    """For an eTerra export compared as a layer with a delivery, the sentence pointing to the
    file comparison; "" for other layers. old and new are layers or their LayerInfo; names:
    what to call each layer."""
    if old is None or new is None or is_eterra_export(old) == is_eterra_export(new):
        return ""
    which = names[0] if is_eterra_export(old) else names[1]
    return (f"{which} este un export eTerra convertit. Pentru un export eTerra și o livrare, "
            "comparați fișierele CGXML, potrivite după IE: acolo contează doar valorile "
            "completate în ambele surse, iar imobilele care nu au coordonate în eTerra nu apar "
            "ca apărute sau dispărute.")


def comparable_building_columns():
    """(key, label) of the comparable construcție columns, in catalog order."""
    return [(c.key, c.label) for c in cgxml_columns.CONSTRUCTII
            if c.key not in BUILDING_NOT_COMPARED]


# --- pairing ---

@dataclass(eq=False)
class Entry:
    """One file of a source. Compared by identity: two files can hold the same values."""
    file_name: str
    display: str
    record: dict
    buildings: list
    cadgenno: str
    ie: str
    uat: str
    is_new: bool
    key: str = ""
    # the other pieces of an eTerra imobil split in the delivery, which share its IE
    pieces: list = field(default_factory=list)
    # the reader's remarks on the file
    warnings: list = field(default_factory=list)
    # [(key, record)] of the construcții and {number: times} of the numbers repeated
    # (numbered())
    numbered: list = field(default_factory=list)
    repeated: dict = field(default_factory=dict)


def entry_of(source, imobile, constructii, warnings=()) -> Entry:
    record = imobile[0]
    keyed, repeated = numbered(constructii)
    return Entry(file_name=cgxml_sources.file_name(source), display=source[0], record=record,
                 buildings=constructii, cadgenno=record.get("id_text") or "",
                 ie=record.get("ie_text") or "", uat=record.get("uat_siruta") or "",
                 is_new=record.get("is_new") is True, warnings=list(warnings),
                 numbered=keyed, repeated=repeated)


# the end of each remark of the reader on an imobil or a construcție it builds no geometry for,
# whatever the cause: a point it cannot read, points that close no surface
# (cgxml_reader.parse_bytes)
_UNBUILT_END = "nu a fost construită"


def _unbuilt_kind(remark):
    """"constructii" or "imobile" for a reader's remark that a construcție or the imobil has
    no geometry, None for any other remark."""
    if not remark.rstrip(" .").endswith(_UNBUILT_END):
        return None
    return "constructii" if "construcți" in remark else "imobile"


def unbuilt(record) -> bool:
    """Whether the reader left an imobil or construcție record without the geometry its
    points describe: a point that is not a number, or points that close no surface. A record
    without any point is not unbuilt."""
    return not record.get("rings") and bool(record.get("_has_points"))


@dataclass
class Ignored:
    side: str
    file_name: str     # with its folder or archive, as list_sources() names it
    key: str
    reason: str


class Problem(NamedTuple):
    """A remark on a file that was compared: the reader's, or a repeated construcție number."""
    side: str
    file_name: str
    text: str


@dataclass
class SourceIndex:
    side: str
    mode: str
    by_key: dict
    unkeyed: list
    ignored: list
    files: int
    uats: list


@dataclass
class Pair:
    key: str
    older: Entry
    newer: Entry
    matched_by: str   # "key", or "ie" / "cadgenno" for a renamed file


def stem_of(file_name: str) -> str:
    dot = file_name.rfind(".")
    return file_name if dot <= 0 else file_name[:dot]


def key_of(entry: Entry, mode: str) -> str:
    return stem_of(entry.file_name).strip() if mode == FILENAME else entry.ie


def match_text(mode: str, key: str) -> str:
    """The pairing key as the report shows it: "IE 70000" or "numele 5429"."""
    if not key:
        return ""
    return f"IE {key}" if mode == IE else f"numele {key}"


def _missing_key_reason(mode):
    return "Fără nume de fișier" if mode == FILENAME else "Fără IE"


def _duplicate_reason(mode, kept):
    what = "nume" if mode == FILENAME else "IE"
    return f"Același {what} ca '{kept}'; s-a comparat doar acela."


def count_uats(entries) -> list:
    """[(SIRSUP, file count)], most frequent first."""
    counts = {}
    for entry in entries:
        if entry.uat:
            counts[entry.uat] = counts.get(entry.uat, 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def _scoped_key(scoped, uat, key):
    return f"{uat}|{key.upper()}" if scoped else key.upper()


def index_source(entries, mode, side, delivery=None) -> SourceIndex:
    """Index one side's files by key, in file name order, so a repeated key keeps its first
    file. A file without SIRSUP takes the side's UAT when there is only one. side names the
    source in the report; delivery, by default for the new source, is whether by IE an entry
    without an IE is a new imobil and a repeated IE a piece of a split imobil."""
    if delivery is None:
        delivery = side == NEW_SIDE
    uats = count_uats(entries)
    only = uats[0][0] if len(uats) == 1 else ""
    scoped = len(uats) > 1
    by_key, unkeyed, ignored = {}, [], []
    for entry in sorted(entries, key=lambda e: e.file_name.upper()):
        if not entry.uat and only:
            entry.uat = only
        key = key_of(entry, mode)
        if not key:
            if mode == IE and delivery:
                unkeyed.append(entry)
            else:
                ignored.append(Ignored(side, entry.display, "", _missing_key_reason(mode)))
            continue
        lookup = _scoped_key(scoped, entry.uat, key)
        kept = by_key.get(lookup)
        if kept is not None:
            if mode == IE and delivery:
                kept.pieces.append(entry)
            else:
                ignored.append(Ignored(side, entry.display, match_text(mode, key),
                                       _duplicate_reason(mode, kept.display)))
            continue
        entry.key = key
        by_key[lookup] = entry
    return SourceIndex(side, mode, by_key, unkeyed, ignored, len(entries), uats)


def _by_ie(entry):
    return entry.ie or None


def _by_cadgenno(entry):
    return entry.cadgenno or None


def pair_sources(older: SourceIndex, newer: SourceIndex):
    """(pairs, missing), missing being [(ONLY_OLD or ONLY_NEW, match text, entry)]."""
    scoped = bool(older.uats) and bool(newer.uats) and (len(older.uats) > 1 or len(newer.uats) > 1)

    def lookup(entry, key):
        return _scoped_key(scoped, entry.uat, key)

    pairs = []
    old_by_key = {}
    for entry in older.by_key.values():
        old_by_key[lookup(entry, entry.key)] = entry
    # dict as an ordered set; entries hash by identity
    leftover_old = dict.fromkeys(older.by_key.values())
    leftover_new = []
    for entry in newer.by_key.values():
        counterpart = old_by_key.get(lookup(entry, entry.key))
        if counterpart is not None and counterpart in leftover_old:
            pairs.append(Pair(entry.key, counterpart, entry, "key"))
            del leftover_old[counterpart]
        else:
            leftover_new.append(entry)

    # (matched_by, key of an entry, whether only keys that one leftover file holds on each
    # side count)
    passes = ([("cadgenno", _by_cadgenno, False), ("ie", _by_ie, True)]
              if newer.mode == FILENAME else [])
    for matched_by, key_fn, single_only in passes:
        candidates = {}
        for entry in leftover_old:
            key = key_fn(entry)
            if key is not None:
                candidates.setdefault(lookup(entry, key), []).append(entry)
        new_counts = {}
        for entry in leftover_new:
            key = key_fn(entry)
            if key is not None:
                new_counts[lookup(entry, key)] = new_counts.get(lookup(entry, key), 0) + 1
        still_new = []
        for entry in leftover_new:
            key = key_fn(entry)
            found = candidates.get(lookup(entry, key), []) if key is not None else []
            if single_only and (len(found) != 1 or new_counts[lookup(entry, key)] != 1):
                found = []
            counterpart = found[0] if found else None
            if counterpart is None or counterpart not in leftover_old:
                still_new.append(entry)
                continue
            pairs.append(Pair(entry.key, counterpart, entry, matched_by))
            del leftover_old[counterpart]
        leftover_new = still_new

    missing = [(ONLY_OLD, match_text(older.mode, e.key), e) for e in leftover_old]
    missing += [(ONLY_NEW, match_text(newer.mode, e.key), e) for e in leftover_new]
    missing += [(ONLY_NEW, "fără IE (imobil nou)" if e.is_new else "fără IE", e)
                for e in newer.unkeyed]
    return pairs, missing


def is_eterra_side(entries) -> bool:
    """Whether a source's files are an eTerra export: every imobil of a delivery has its
    cadastral sector, those of an eTerra export none."""
    return bool(entries) and all(e.record.get("sector") is None for e in entries)


# --- comparing ---

@dataclass
class Outcome:
    """One imobil or construcție in a result category."""
    key: str                         # pairing key as the report shows it
    record: dict                     # the new record, the old one when it disappeared
    file_old: str = ""
    file_new: str = ""
    changes: list = field(default_factory=list)   # [(column key, old value, new value)]
    moved: Optional[tuple] = None                 # (area before, area after, moved area)
    matched_by: str = "key"
    imobil: str = ""                 # a construcție's imobil, as the report shows it
    pieces: list = field(default_factory=list)    # the other files of a split imobil
    # a side has no geometry for it, so its boundary was not compared
    uncompared: bool = False
    # of a changed imobil or construcție, the old record
    other_record: Optional[dict] = None
    # the pieces of a split imobil are in the old source, with file_old
    pieces_old: bool = False


def kind_of(outcome) -> str:
    """compare_service.change_kind() of a changed imobil or construcție."""
    return change_kind([column for column, _old, _new in outcome.changes],
                       outcome.moved is not None, bool(outcome.pieces),
                       uncompared=outcome.uncompared)


def building_key(record, index) -> str:
    """"C1", else the IE, else the position in the file."""
    return record.get("nr_constr") or record.get("ie") or f"#{index + 1}"


def numbered(buildings):
    """([(key, record)], {key: times}) of a file's construcții: building_key(), the second
    construcție with a number already used in the file as "C1 (2)", and the numbers repeated
    with how often they occur."""
    keyed, seen = [], {}
    for i, record in enumerate(buildings):
        key = building_key(record, i)
        seen[key] = seen.get(key, 0) + 1
        keyed.append((key if seen[key] == 1 else f"{key} ({seen[key]})", record))
    return keyed, {key: times for key, times in seen.items() if times > 1}


def repeated_text(number, times) -> str:
    """The remark on a construcție number repeated in a file."""
    names = [f"'{number}'"] + [f"'{number} ({n})'" for n in range(2, times + 1)]
    return (f"Construcția {number} apare de {fmt.count(times, 'dată', 'ori')} în fișier; "
            f"s-au comparat ca {', '.join(names[:-1])} și {names[-1]}.")


def _blank(value) -> bool:
    return value is None or str(value).strip() == ""


AREA_COLUMNS = ("sup_mas", "sup_acte")
DEED_AREA = "sup_acte"
# the 0 eTerra writes for an area or a number of levels it does not have
ETERRA_ZERO = ("sup_mas", "sup_acte", "sup_tot", "etaje")
# eTerra keeps the decimals of an area, a delivery writes whole square metres
ROUNDED_AREAS = ("sup_mas", "sup_acte", "sup_tot")

# how an eTerra value and a delivery value differ
CHANGED = "changed"
ONE_SIDED = "one-sided"
ROUNDED = "rounded"


def eterra_value(column, value):
    """A value of an eTerra export, None for the 0 it writes for no value."""
    if column in ETERRA_ZERO and isinstance(value, (int, float)) and value == 0:
        return None
    return value


def whole_metres(a: float, b: float) -> bool:
    """Two areas that differ only by the rounding of one of them to whole square metres."""
    return abs(a - b) <= 0.5 and (a.is_integer() or b.is_integer())


# the street number of a plot that has none: "FN", fără număr
_WITHOUT_NUMBER = re.compile(r"f\.?\s*n\.?|-", re.IGNORECASE)


def address_parts(record) -> dict:
    """{part: value} of an imobil's address: the UAT and the locality by SIRUTA code, the
    locality only when it is not the UAT's own code, then the street parts."""
    uat = record.get("uat_siruta") or ""
    locality = record.get("loc_siruta") or ""
    parts = {"uat": uat, "localitate": locality if locality != uat else ""}
    parts.update(record.get("adresa_parts") or [])
    if _WITHOUT_NUMBER.fullmatch((record.get("nr_postal") or "").strip()):
        parts.pop("POSTALNUMBER", None)
    return {part: value for part, value in parts.items() if value}


def named_addresses(records) -> str:
    """The addresses of records, each with its locality named, joined when they differ."""
    texts = []
    for record in records:
        text = cgxml_export.address_text(record, every_locality=True)
        if text not in texts:
            texts.append(text)
    return "; ".join(texts)


def address_difference(old, news, fold_text):
    """How an eTerra address differs from the addresses of the delivery imobile that keep its
    IE: ONE_SIDED when only by parts one side leaves out, such as the street eTerra does not
    hold or the village a delivery does not name; CHANGED when a part both name differs."""
    found = None
    before = address_parts(old)
    for new in news:
        if not values_differ(old.get("adresa"), new.get("adresa"), fold_text):
            continue
        after = address_parts(new)
        if any(values_differ(before[part], after[part], fold_text)
               for part in before.keys() & after.keys()):
            return CHANGED
        if before.keys() != after.keys():
            found = ONE_SIDED
    return found


# an int or real field cannot hold the joined values of the pieces, "501; 502"
_NUMERIC_COLUMNS = {c.key for c in cgxml_columns.IMOBILE if c.kind in ("int", "real")}


def merged_record(entries, old=None) -> dict:
    """One record for an imobil split into the entries: every outline, the areas added up,
    and for the other columns the common value, or the different values joined; a number
    the pieces do not share is left empty. old is the imobil before the split: when every
    piece repeats its deed area, that is the deed area of the whole."""
    merged = dict(entries[0].record)
    records = [e.record for e in entries]
    for key in merged:
        if key in ("rings", "fisier", "adresa_parts"):
            continue
        values = [r.get(key) for r in records]
        if key in AREA_COLUMNS:
            merged[key] = (sum(values) if all(isinstance(v, (int, float)) for v in values)
                           else None)
            if key == DEED_AREA and _repeats_deed_area(values, old):
                merged[key] = values[0]
        elif len({str(v) for v in values}) > 1 and key in _NUMERIC_COLUMNS:
            merged[key] = None
        elif len({str(v) for v in values}) > 1:
            distinct = []
            for v in values:
                if not _blank(v) and str(v) not in distinct:
                    distinct.append(str(v))
            merged[key] = "; ".join(distinct) or None
    merged["rings"] = [part for r in records for part in (r.get("rings") or [])]
    return merged


def _repeats_deed_area(values, old) -> bool:
    """Whether the pieces all carry the deed area of old, the imobil before the split, as
    pieces that cite the deed of the whole do."""
    if old is None or len(set(values)) != 1 or not isinstance(values[0], (int, float)):
        return False
    before = eterra_value(DEED_AREA, old.get(DEED_AREA))
    if not isinstance(before, (int, float)):
        return False
    return values[0] == before or whole_metres(float(before), float(values[0]))


def files_text(outcome):
    """(old files, new files) of a changed imobil, the pieces of a split imobil with the file
    of their source."""
    old, new = [outcome.file_old], [outcome.file_new]
    (old if outcome.pieces_old else new).extend(outcome.pieces)
    return ", ".join(old), ", ".join(new)


# metres; eTerra rounds coordinates to the millimetre and a delivery does not, so a boundary
# with no point farther than this from the other outline counts as unmoved
COORDINATE_PRECISION = 0.01


def compare_shapes(old, new, tolerance):
    return geometry_change(cgxml_export.build_geometry(old.get("rings")),
                           cgxml_export.build_geometry(new.get("rings")), tolerance)


def _outline(rings):
    """The geometry of rings with the parts that touch dissolved into one, None without
    rings: the line between the pieces of a split imobil lies inside the old outline."""
    geometry = cgxml_export.build_geometry(rings)
    if geometry is None:
        return None
    parts = geometry.asGeometryCollection()
    if len(parts) < 2:
        return geometry
    union = QgsGeometry.unaryUnion(parts)
    return union if union is not None and not union.isEmpty() else geometry


def moved_within(old, new, distance) -> bool:
    """Whether no point of either outline is farther than distance metres from the other."""
    before = _outline(old.get("rings"))
    after = _outline(new.get("rings"))
    if before is None or after is None:
        return False
    # the Hausdorff distance; negative when GEOS fails
    return 0.0 <= before.hausdorffDistance(after) <= distance


class CgxmlCompareTask(CadexTask):
    """Reads, pairs and compares both sets in the background. The outcomes hold plain
    records; result_features() turns them into layer features."""

    def __init__(self, *, old_paths, new_paths, recursive=True, old_recursive=None,
                 new_recursive=None, mode=FILENAME, columns=None, compare_geometry=True,
                 tolerance=0.1, buildings=True, fold_text=False, excel_path=None,
                 building_columns=None, finished_cb=None):
        super().__init__("CADex: Comparator CGXML", finished_cb)
        self.old_paths = _paths(old_paths)
        self.new_paths = _paths(new_paths)
        self.old_recursive = recursive if old_recursive is None else old_recursive
        self.new_recursive = recursive if new_recursive is None else new_recursive
        self.mode = mode if mode in MODES else FILENAME
        self.columns = list(DEFAULT_COLUMNS if columns is None else columns)
        self.building_columns = list(BUILDING_COLUMNS if building_columns is None
                                     else building_columns)
        self.compare_geometry = compare_geometry
        self.tolerance = tolerance
        self.buildings = buildings
        self.fold_text = fold_text
        self.excel_path = excel_path or None

        self.old_files = self.new_files = 0
        self.added: List[Outcome] = []
        self.deleted: List[Outcome] = []
        self.changed: List[Outcome] = []
        self.unchanged = 0
        self.renamed = 0
        self.matched = 0
        self.ignored: List[Ignored] = []
        # remarks on files that were compared, shown with the result and in the report
        self.problems: List[Problem] = []
        self._repeated: List[Problem] = []
        self.buildings_added: List[Outcome] = []
        self.buildings_deleted: List[Outcome] = []
        self.buildings_changed: List[Outcome] = []
        self.buildings_unchanged = 0
        self.split = 0                  # eTerra imobile split in the delivery
        self.split_files = 0            # their delivery files besides the first of each
        # eTerra imobile and construcții found in both, without coordinates
        self.old_without_geometry = 0
        self.old_buildings_without_geometry = 0
        self.skipped_columns = []       # chosen columns the eTerra export leaves empty
        # by IE, the eTerra export is the new source and the delivery the old one
        self.reversed = False
        # imobile and construcții with a value only one side has, by column, in IE mode
        self.one_sided: Dict[str, int] = {}
        self.one_sided_buildings: Dict[str, int] = {}
        # and those whose area differs only by its rounding to whole square metres
        self.rounded: Dict[str, int] = {}
        self.rounded_buildings: Dict[str, int] = {}
        # imobile and construcții whose boundary moved within COORDINATE_PRECISION, in IE mode
        self.within_precision = {"imobile": 0, "constructii": 0}

    def _read(self, sources, side, start):
        entries = []
        for source, parsed, problem in cgxml_sources.read_each(
                sources, self.stopped, lambda share: self.report(start + 40.0 * share)):
            if problem is not None:
                self.ignored.append(Ignored(side, source[0], "",
                                            f"Fișierul nu poate fi citit: {problem}."))
                continue
            imobile, constructii, warnings = parsed
            for record in imobile:
                cgxml_export.finalize(record, "imobile")
            for record in constructii:
                cgxml_export.finalize(record, "constructii")
            entry = entry_of(source, imobile, constructii, warnings)
            self.problems += [Problem(side, entry.display, text) for text in warnings]
            if self.buildings:
                repeated = [Problem(side, entry.display, repeated_text(number, times))
                            for number, times in entry.repeated.items()]
                self.problems += repeated
                self._repeated += repeated
            entries.append(entry)
        return entries

    def run(self):
        try:
            siruta.retry()
            old_sources = cgxml_sources.list_sources(self.old_paths, self.old_recursive)
            new_sources = cgxml_sources.list_sources(self.new_paths, self.new_recursive)
            self.old_files, self.new_files = len(old_sources), len(new_sources)
            if not old_sources or not new_sources:
                self.error_msg = "Nu s-a găsit niciun fișier .cgxml în sursa {0}.".format(
                    "veche" if not old_sources else "nouă")
                return False
            for which, sources, reported in (("veche", old_sources, "apărute"),
                                             ("nouă", new_sources, "dispărute")):
                unread = cgxml_sources.unreadable_text(sources)
                if unread:
                    self.error_msg = (
                        f"Sursa {which} nu poate fi citită în întregime: {unread}. Comparația "
                        f"nu s-a făcut: imobilele din partea necitită ar fi apărut ca {reported}.")
                    return False
            old_entries = self._read(old_sources, OLD_SIDE, 0.0)
            if self.stopped():
                return False
            new_entries = self._read(new_sources, NEW_SIDE, 40.0)
            if self.stopped():
                return False
            self.reversed = (self.mode == IE and is_eterra_side(new_entries)
                             and not is_eterra_side(old_entries))
            if self.reversed:
                older = index_source(new_entries, self.mode, NEW_SIDE, delivery=False)
                newer = index_source(old_entries, self.mode, OLD_SIDE, delivery=True)
                eterra = new_entries
            else:
                older = index_source(old_entries, self.mode, OLD_SIDE)
                newer = index_source(new_entries, self.mode, NEW_SIDE)
                eterra = old_entries
            self.ignored += older.ignored + newer.ignored
            pairs, missing = pair_sources(older, newer)
            self.matched = len(pairs)
            self.renamed = sum(1 for p in pairs if p.matched_by != "key")
            if self.mode == IE and eterra:
                self.skipped_columns = [
                    c for c in self.columns
                    if all(comparable(eterra_value(c, e.record.get(c)), self.fold_text, c) is None
                           for e in eterra)]

            for state, key, first in missing:
                for entry in [first] + first.pieces:
                    outcome = Outcome(key or entry.file_name, entry.record,
                                      file_old=entry.display if state == ONLY_OLD else "",
                                      file_new=entry.display if state == ONLY_NEW else "")
                    (self.deleted if state == ONLY_OLD else self.added).append(outcome)
                    if self.buildings:
                        target = (self.buildings_deleted if state == ONLY_OLD
                                  else self.buildings_added)
                        owner = self._owner(entry)
                        target.extend(Outcome(number, b, imobil=owner)
                                      for number, b in entry.numbered)

            for n, pair in enumerate(pairs):
                if self.stopped():
                    return False
                self._compare(pair)
                if n % 50 == 0:
                    self.report(80.0 + 15.0 * n / max(len(pairs), 1))
            if self.reversed:
                self._turn_around()
            if self.excel_path:
                self.write_report(self._write_excel, self.excel_path)
            self.report(100.0)
            return True
        except Exception as e:
            return self.failed("Comparator CGXML", e)

    def _turn_around(self):
        """Put the outcomes of a comparison run with the eTerra export as the old source back
        in the order chosen, where it is the new one: what only one source holds changes
        sides, and every change reads from the old source to the new one."""
        self.added, self.deleted = self.deleted, self.added
        self.buildings_added, self.buildings_deleted = (self.buildings_deleted,
                                                        self.buildings_added)
        for outcome in (self.added + self.deleted + self.buildings_added
                        + self.buildings_deleted):
            outcome.file_old, outcome.file_new = outcome.file_new, outcome.file_old
        for outcome in self.changed + self.buildings_changed:
            outcome.file_old, outcome.file_new = outcome.file_new, outcome.file_old
            outcome.changes = [(column, after, before)
                               for column, before, after in outcome.changes]
            if outcome.moved is not None:
                before, after, moved = outcome.moved
                outcome.moved = (after, before, moved)
            if outcome.other_record is not None:
                outcome.record, outcome.other_record = outcome.other_record, outcome.record
            outcome.pieces_old = not outcome.pieces_old

    def _owner(self, entry) -> str:
        """entry's imobil as the report shows it next to its construcții: its pairing key;
        with an eTerra export, whose IEs would mix with a delivery's IDs, "IE 70001" or
        "ID 9001"."""
        if self.mode != IE:
            return entry.key or entry.cadgenno or stem_of(entry.file_name)
        if entry.ie:
            return f"IE {entry.ie}"
        if entry.cadgenno:
            return f"ID {entry.cadgenno}"
        return stem_of(entry.file_name)

    def _compare(self, pair):
        newer = pair.newer
        pieces = [newer] + newer.pieces
        old = pair.older.record
        new = merged_record(pieces, old) if newer.pieces else newer.record
        columns = [c for c in self.columns if c not in self.skipped_columns]
        changes = self._changes(old, new, columns, self.one_sided, self.rounded,
                                [p.record for p in pieces])
        moved, uncompared = None, False
        if self.compare_geometry:
            if unbuilt(old) or any(unbuilt(p.record) for p in pieces):
                uncompared = True
            elif self.mode == IE and not old.get("rings"):
                self.old_without_geometry += 1
                uncompared = True
            else:
                moved = self._moved(old, new, "imobile")
        if newer.pieces:
            self.split += 1
            self.split_files += len(newer.pieces)
        if changes or moved or newer.pieces:
            self.changed.append(Outcome(pair.key, new, pair.older.display, newer.display,
                                        changes, moved, pair.matched_by,
                                        pieces=[p.display for p in newer.pieces],
                                        uncompared=uncompared, other_record=old))
        else:
            self.unchanged += 1
        if not self.buildings:
            return
        owner = self._owner(newer)
        old_by = dict(pair.older.numbered)
        new_by = {}
        for piece in pieces:
            for key, b in piece.numbered:
                # the pieces of a split imobil number their construcții each from C1
                if key in new_by:
                    key = f"{key} ({piece.cadgenno or stem_of(piece.file_name)})"
                if key not in new_by:
                    new_by[key] = b
        for key, b in new_by.items():
            if key not in old_by:
                self.buildings_added.append(Outcome(key, b, imobil=owner))
                continue
            before = old_by[key]
            b_changes = self._changes(before, b, self.building_columns,
                                      self.one_sided_buildings, self.rounded_buildings)
            b_moved, b_uncompared = None, False
            if self.compare_geometry:
                if unbuilt(before) or unbuilt(b):
                    b_uncompared = True
                elif self.mode == IE and not before.get("rings"):
                    self.old_buildings_without_geometry += 1
                    b_uncompared = True
                else:
                    b_moved = self._moved(before, b, "constructii")
            if b_changes or b_moved:
                self.buildings_changed.append(Outcome(key, b, changes=b_changes, moved=b_moved,
                                                      imobil=owner, uncompared=b_uncompared,
                                                      other_record=before))
            else:
                self.buildings_unchanged += 1
        for key, b in old_by.items():
            if key not in new_by:
                self.buildings_deleted.append(Outcome(key, b, imobil=owner))

    def _moved(self, old, new, which):
        """compare_shapes(); in IE mode a boundary that moved within COORDINATE_PRECISION is
        no move, counted in within_precision."""
        moved = compare_shapes(old, new, self.tolerance)
        if moved is not None and self.mode == IE and moved_within(old, new, COORDINATE_PRECISION):
            self.within_precision[which] += 1
            return None
        return moved

    def _changes(self, old, new, columns, one_sided, rounded, pieces=()):
        """[(column, old value, new value)] of the columns that differ. pieces are the new
        records of an imobil, more than one when it was split.

        In IE mode an eTerra export and a delivery fill different columns, e.g. the deed area
        or the fence, so a value only one side has is no change; nor is an area that differs
        only by its rounding. They are counted in one_sided and rounded."""
        changes = []
        for column in columns:
            before, after = old.get(column), new.get(column)
            if self.mode != IE:
                if values_differ(before, after, self.fold_text, column):
                    changes.append((column, before, after))
                continue
            difference = self._eterra_difference(column, old, new, pieces)
            if difference == CHANGED:
                if column == "adresa" and pieces:
                    # the village named like its UAT is left out of the address, so a change
                    # of village would read as a value only one side has
                    before, after = named_addresses([old]), named_addresses(pieces)
                changes.append((column, before, after))
            elif difference is not None:
                counts = one_sided if difference == ONE_SIDED else rounded
                counts[column] = counts.get(column, 0) + 1
        return changes

    def _eterra_difference(self, column, old, new, pieces):
        """CHANGED, ONE_SIDED, ROUNDED or None for one column of an eTerra record and a
        delivery record."""
        if column == "adresa" and pieces:
            return address_difference(old, pieces, self.fold_text)
        before = comparable(eterra_value(column, old.get(column)), self.fold_text, column)
        after = comparable(new.get(column), self.fold_text, column)
        if before is None or after is None:
            return None if before is after else ONE_SIDED
        if not normalized_differ(before, after):
            return None
        if (column in ROUNDED_AREAS and isinstance(before, float) and isinstance(after, float)
                and whole_metres(before, after)):
            return ROUNDED
        return CHANGED

    def one_sided_text(self):
        """"Împrejmuit la 75 de imobile, ..." for the values only one side has."""
        return _counts_text(self.one_sided, self.one_sided_buildings)

    def rounded_text(self):
        return _counts_text(self.rounded, self.rounded_buildings)

    def within_precision_text(self):
        """"3 imobile, 1 construcție" for the boundaries that moved within the precision."""
        counts = self.within_precision
        imobile, constructii = counts["imobile"], counts["constructii"]
        return ", ".join(text for count, text in (
            (imobile, fmt.count(imobile, "imobil", "imobile")),
            (constructii, fmt.count(constructii, "construcție", "construcții"))) if count)

    # --- report ---

    def summary_rows(self):
        """[(label, count)] of the imobile for the result window and the Sumar sheet."""
        rows = [("Fișiere din sursa veche", self.old_files),
                ("Fișiere din sursa nouă", self.new_files),
                ("Imobile în ambele surse", self.matched),
                (IMOBILE_CHANGED, len(self.changed))]
        rows += self.kind_rows(self.changed, with_split=bool(self.split))
        rows.append((IMOBILE_UNCHANGED, self.unchanged))
        if self.renamed:
            rows.append(("Imobile cu alt nume de fișier", self.renamed))
        if self.split and not self.compare_geometry:
            rows.append(("Imobile din eTerra dezmembrate", self.split))
        if self.split_files:
            # the delivery files of split imobile besides the one counted with the imobile in
            # both sources
            side = "sursa veche" if self.reversed else "sursa nouă"
            rows.append((f"Fișiere din {side} în plus, la imobilele dezmembrate",
                         self.split_files))
        rows += [(IMOBILE_ADDED, len(self.added)), (IMOBILE_DELETED, len(self.deleted)),
                 ("Fișiere ignorate", len(self.ignored))]
        return rows

    def building_summary_rows(self):
        """[(label, count)] of the construcții for the result window; [] when they were not
        compared."""
        if not self.buildings:
            return []
        return ([(BUILDINGS_ADDED, len(self.buildings_added)),
                 (BUILDINGS_DELETED, len(self.buildings_deleted)),
                 (BUILDINGS_CHANGED, len(self.buildings_changed))]
                + self.kind_rows(self.buildings_changed)
                + [(BUILDINGS_UNCHANGED, self.buildings_unchanged)])

    def kind_rows(self, outcomes, with_split=False):
        """compare_service.kind_rows() of outcomes; [] when the geometry was not compared,
        since then a moved boundary would read as a change in the data only."""
        if not self.compare_geometry:
            return []
        return kind_rows([kind_of(o) for o in outcomes], with_split)

    def notes(self):
        """Sentences about the sources and what was not compared, for the result window and
        Processing."""
        notes = []
        if self.skipped_columns:
            labels = ", ".join(cgxml_columns.label_of("imobile", c) for c in self.skipped_columns)
            notes.append(fmt.plural(
                len(self.skipped_columns),
                "Coloana {0} este necompletată în tot exportul eTerra și nu s-a comparat.",
                "Coloanele {0} sunt necompletate în tot exportul eTerra și nu s-au comparat.",
            ).format(labels))
        if self.buildings and not self.building_columns and not self.compare_geometry:
            notes.append("Pentru construcții nu s-a ales nicio coloană și nu s-a comparat "
                         "geometria, deci s-au căutat doar construcțiile apărute și dispărute.")
        if self.one_sided or self.one_sided_buildings:
            note = ("Valorile completate doar într-una dintre surse nu s-au considerat "
                    f"modificări: {self.one_sided_text()}.")
            if (set(self.one_sided) | set(self.one_sided_buildings)) & set(ETERRA_ZERO):
                note += (" În exportul eTerra, 0 la o suprafață sau la numărul de niveluri "
                         "înseamnă că valoarea lipsește.")
            notes.append(note)
        if self.rounded or self.rounded_buildings:
            notes.append("Suprafețele care diferă doar prin rotunjirea la metru pătrat nu s-au "
                         f"considerat modificări: {self.rounded_text()}.")
        if any(self.within_precision.values()):
            notes.append("Limitele mutate cu cel mult 1 cm, cât este precizia coordonatelor, nu "
                         f"s-au considerat modificări: {self.within_precision_text()}.")
        imobile, constructii = self.old_without_geometry, self.old_buildings_without_geometry
        if imobile or constructii:
            what = " și ".join(text for count, text in (
                (imobile, fmt.count(imobile, "imobil", "imobile")),
                (constructii, fmt.count(constructii, "construcție", "construcții"))) if count)
            if imobile + constructii == 1:
                notes.append(f"{what} din exportul eTerra nu are coordonate, deci limita "
                             f"{'lui' if imobile else 'ei'} nu s-a comparat.")
            else:
                notes.append(f"{what} din exportul eTerra nu au coordonate, deci limita lor nu "
                             "s-a comparat.")
        unbuilt_in = _files_text([p for p in self.problems if _unbuilt_kind(p.text)])
        if unbuilt_in:
            notes.append("Imobile sau construcții fără geometrie, care nu a putut fi construită "
                         f"din punctele lor, în {unbuilt_in}. Limita lor nu s-a comparat.")
        repeated = _files_text(self._repeated)
        if repeated:
            notes.append(f"Numere de construcție repetate în {repeated}. "
                         "Construcțiile cu același număr s-au comparat ca 'C1', 'C1 (2)', "
                         "'C1 (3)'.")
        if self.split:
            notes.append(fmt.plural(
                self.split,
                "{0} din eTerra a fost dezmembrat și s-a comparat cu toate imobilele din "
                "cealaltă sursă care îi păstrează IE-ul.",
                "{0} din eTerra au fost dezmembrate și s-au comparat cu toate imobilele din "
                "cealaltă sursă care le păstrează IE-ul.",
            ).format(fmt.count(self.split, "imobil", "imobile")))
        if siruta.problem():
            notes.append(siruta.problem())
        return notes

    def _buildings_fact(self) -> str:
        """The Sumar fact on what was compared of the construcții."""
        if not self.buildings:
            return "Nu"
        labels = ", ".join(cgxml_columns.label_of("constructii", c)
                           for c in self.building_columns)
        if labels:
            return "Da: " + labels
        if self.compare_geometry:
            return "Da: doar geometria"
        return "Da: doar construcțiile apărute și dispărute"

    def _write_excel(self):
        wb = xlsx_report.new_workbook()
        summary = xlsx_report.Summary(wb, "Comparator CGXML")
        summary.section("Informații")
        summary.facts([fact for fact in [
            ("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
            (OLD_SIDE, "; ".join(self.old_paths)),
            (NEW_SIDE, "; ".join(self.new_paths)),
            ("Potrivirea fișierelor", MODE_NAMES[self.mode]),
            ("Coloane comparate", ", ".join(cgxml_columns.label_of("imobile", c)
                                            for c in self.columns
                                            if c not in self.skipped_columns) or "-"),
            ("Valori completate doar într-o sursă, neconsiderate modificări",
             self.one_sided_text() or "-") if self.mode == IE else None,
            ("Suprafețe diferite doar prin rotunjire, neconsiderate modificări",
             self.rounded_text() or "-") if self.mode == IE else None,
            ("Limite mutate cu cel mult 1 cm, neconsiderate modificări",
             self.within_precision_text() or "-") if self.mode == IE else None,
            ("Geometrie comparată", f"Da, toleranță {fmt.area(self.tolerance)}"
             if self.compare_geometry else "Nu"),
            ("Construcții comparate", self._buildings_fact()),
            ("Se ignoră majusculele și diacriticele", "Da" if self.fold_text else "Nu"),
            ("Sistem de coordonate", stereo70.NAME),
        ] if fact is not None])
        summary.section("Imobile")
        explained = {IMOBILE_ADDED: ONLY_NEW, IMOBILE_DELETED: ONLY_OLD}
        summary.table(["Categorie", "Nr.", "", ""],
                      [[label, count, "", explained.get(label, "")]
                       for label, count in self.summary_rows()])
        if self.buildings:
            kinds = self.kind_rows(self.buildings_changed)
            summary.section("Construcții")
            summary.table(["Categorie", "Nr.", "", ""], [
                ["Apărute", len(self.buildings_added), "", ONLY_NEW],
                ["Dispărute", len(self.buildings_deleted), "", ONLY_OLD],
                ["Modificate", len(self.buildings_changed), "", ""]]
                + [[label, count, "", ""] for label, count in kinds]
                + [["Nemodificate", self.buildings_unchanged, "", ""]],
                row_colors=[xlsx_report.GOOD, xlsx_report.BAD, xlsx_report.MIXED]
                + [None] * len(kinds) + [None])
        summary.note(
            "Imobilele au fost căutate în ambele surse după IE." if self.mode == IE else
            "Imobilele au fost căutate în ambele surse după numele fișierului, iar cele al "
            "căror fișier are alt nume în sursa nouă, după numărul cadastral sau după IE.")
        for note in self.notes():
            summary.note(note)

        # with the geometry compared, the kind of change after the key, to filter on
        kind_column = ["Tip modificare"] if self.compare_geometry else []

        def kind(outcome):
            return [kind_of(outcome)] if self.compare_geometry else []

        detail = []
        for o in self.changed:
            files_old, files_new = files_text(o)
            lead = [o.key] + kind(o) + [files_old, files_new]
            if o.pieces:
                pieces = [files_old, None] if o.pieces_old else [None, files_new]
                detail.append(lead + ["Dezmembrat în"] + pieces + [None])
            for column, before, after in o.changes:
                detail.append(lead + [cgxml_columns.label_of("imobile", column), before, after,
                                      None])
            if o.moved:
                before, after, moved = o.moved
                detail.append(lead + [GEOMETRY, round(before, 2), round(after, 2),
                                      round(moved, 2)])
        if detail:
            xlsx_report.write_table(
                wb, "Modificate",
                ["Imobil"] + kind_column + ["Fișier vechi", "Fișier nou", "Ce s-a modificat",
                                            "Valoare veche", "Valoare nouă",
                                            "Suprafață mutată (mp)"],
                detail, row_colors=[xlsx_report.MIXED] * len(detail),
                text_columns=["Imobil", "Valoare veche", "Valoare nouă"])
        for title, outcomes, color in (("Apărute", self.added, xlsx_report.GOOD),
                                       ("Dispărute", self.deleted, xlsx_report.BAD)):
            if outcomes:
                xlsx_report.write_table(
                    wb, title, ["Căutat după", "ID", "IE", "Fișier", "UAT",
                                cgxml_columns.label_of("imobile", "sup_mas"),
                                cgxml_columns.label_of("imobile", "propr")],
                    [[o.key, o.record.get("id"), o.record.get("ie_text"),
                      o.file_new or o.file_old, o.record.get("uat"), o.record.get("sup_mas"),
                      o.record.get("propr")] for o in outcomes],
                    row_colors=[color] * len(outcomes), text_columns=["Căutat după", "IE"])
        if self.buildings:
            rows, colors = [], []
            no_kind = [""] if self.compare_geometry else []
            for state, outcomes, color in (("Apărută", self.buildings_added, xlsx_report.GOOD),
                                           ("Dispărută", self.buildings_deleted, xlsx_report.BAD)):
                for o in outcomes:
                    rows.append([o.imobil, o.key, state] + no_kind + ["", None, None, None])
                    colors.append(color)
            for o in self.buildings_changed:
                lead = [o.imobil, o.key, "Modificată"] + kind(o)
                for column, before, after in o.changes:
                    rows.append(lead + [cgxml_columns.label_of("constructii", column),
                                        before, after, None])
                    colors.append(xlsx_report.MIXED)
                if o.moved:
                    before, after, moved = o.moved
                    rows.append(lead + [GEOMETRY, round(before, 2), round(after, 2),
                                        round(moved, 2)])
                    colors.append(xlsx_report.MIXED)
            if rows:
                xlsx_report.write_table(
                    wb, "Construcții",
                    ["Imobil", "Construcție", "Stare"] + kind_column
                    + ["Ce s-a modificat", "Valoare veche", "Valoare nouă",
                       "Suprafață mutată (mp)"],
                    rows, row_colors=colors,
                    text_columns=["Imobil", "Valoare veche", "Valoare nouă"])
        if self.ignored:
            xlsx_report.write_table(
                wb, "Fișiere ignorate", ["Sursa", "Fișier", "Căutat după", "Motiv"],
                [[i.side, i.file_name, i.key, i.reason] for i in self.ignored],
                row_colors=[xlsx_report.NOTE] * len(self.ignored))
        if self.problems:
            xlsx_report.write_table(
                wb, "Observații", ["Sursa", "Fișier", "Observație"],
                [list(problem) for problem in self.problems],
                row_colors=[xlsx_report.NOTE] * len(self.problems))
        xlsx_report.save(wb, self.excel_path)


def _paths(paths):
    return cgxml_sources.split_paths(paths) if isinstance(paths, str) else list(paths or [])


def _files_text(problems, shown=5) -> str:
    """"2 fișiere: 'a.cgxml' din sursa veche, ..." for the files of problems; "" for none."""
    files = []
    for problem in problems:
        name = f"'{problem.file_name}' din {problem.side.lower()}"
        if name not in files:
            files.append(name)
    if not files:
        return ""
    listed = ", ".join(files[:shown])
    if len(files) > shown:
        listed += f" și încă {fmt.number(len(files) - shown, 0)}"
    return f"{fmt.count(len(files), 'fișier', 'fișiere')}: {listed}"


def _counts_text(imobile, constructii):
    """"Împrejmuit la 75 de imobile, Nr. niveluri la 3 construcții" from counts by column."""
    parts = [f"{cgxml_columns.label_of('imobile', c)} la {fmt.count(n, 'imobil', 'imobile')}"
             for c, n in sorted(imobile.items(), key=lambda kv: -kv[1])]
    parts += [f"{cgxml_columns.label_of('constructii', c)} la "
              f"{fmt.count(n, 'construcție', 'construcții')}"
              for c, n in sorted(constructii.items(), key=lambda kv: -kv[1])]
    return ", ".join(parts)


# --- result layers ---

def result_features(outcomes, which, layout, with_changes=False, kinds=False) -> ResultFeatures:
    """The Stereo 70 features of one result category, in the Conversie CGXML column layout.
    with_changes adds the changed columns and the moved area, kinds the kind of change
    (kind_of), for a comparison of the geometry too. Records without coordinates have no
    feature and are counted in left_out."""
    fields, names = cgxml_export.output_fields(which, layout)
    extra = {}
    if with_changes:
        if kinds:
            extra["kind"] = unique_name(fields, CHANGE_KIND_FIELD)
            fields.append(QgsField(extra["kind"], FIELD_STRING))
        extra["what"] = unique_name(fields, "modificari")
        fields.append(QgsField(extra["what"], FIELD_STRING))
        extra["moved"] = unique_name(fields, "sup_mutata")
        fields.append(QgsField(extra["moved"], FIELD_DOUBLE, "double", 20, 2))
    if which == "constructii":
        extra["imobil"] = unique_name(fields, "imobil")
        fields.append(QgsField(extra["imobil"], FIELD_STRING))
    shown = dict(names)
    features = []
    left_out = 0
    for o in outcomes:
        geometry = cgxml_export.build_geometry(o.record.get("rings"))
        if geometry is None:
            left_out += 1
            continue
        feature = QgsFeature(fields)
        feature.setGeometry(geometry)
        values, _cut = cgxml_export.record_values(o.record, names)
        if with_changes:
            if kinds:
                values.append(kind_of(o))
            what = []
            if o.pieces:
                # the IDs of the pieces, which the id field cannot hold together
                pieces = o.other_record if o.pieces_old else o.record
                ids = (pieces or {}).get("id_text")
                what.append(f"dezmembrat în {len(o.pieces) + 1} imobile"
                            + (f": {ids}" if ids else ""))
            what += [shown.get(column, column) for column, _old, _new in o.changes]
            if o.moved:
                what.append("geometrie")
            values += [", ".join(what), round(o.moved[2], 2) if o.moved else None]
        if which == "constructii":
            values.append(o.imobil)
        feature.setAttributes(values)
        features.append(feature)
    return ResultFeatures(fields, features, WKB_TYPE, extra.get("kind"), left_out)


def left_out_note(left_out, in_report=False) -> str:
    """The sentence for the records result_features() left out, "" for none. left_out is
    [(result layer name, count)]; in_report: the Excel report, which lists every record, was
    written."""
    parts = [f"{fmt.number(count, 0)} la '{name}'" for name, count in left_out if count]
    if not parts:
        return ""
    note = "Înregistrările fără coordonate nu apar în layerele rezultat: " + ", ".join(parts) + "."
    return note + " Sunt listate în raportul Excel." if in_report else note
