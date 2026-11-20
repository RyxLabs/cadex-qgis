# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparison of two layers matched on a key column, and the value and geometry rules
shared with cgxml_compare."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from qgis.core import (  # type: ignore
    Qgis, QgsExpression, QgsFeature, QgsFeatureRequest, QgsField, QgsFields, QgsGeometry,
    QgsVectorLayer, QgsWkbTypes,
)

from . import codes, fmt, overlay, roles, settings_service, stereo70, xlsx_report
from .layer_sources import LayerCache, LayerInfo, unique_name
from .roles import fold
from .tasks import CadexTask
from ..qt_compat import FIELD_DOUBLE, FIELD_STRING, is_null

# isExactlyEqual exists from QGIS 4.2, which deprecates equals
_exactly_equal = getattr(QgsGeometry, "isExactlyEqual", QgsGeometry.equals)

# the label of the geometry in the reports' lists of what changed
GEOMETRY = "Geometrie (suprafața, mp)"

# the colors of the result categories, in the windows and the Processing outputs
ADDED_COLOR = "#2E9E5B"
DELETED_COLOR = "#D64545"
CHANGED_COLOR = "#E3A008"

# what changed in a record found in both versions; the "Modificate" layers are styled by it
CHANGE_KIND_FIELD = "tip_modif"
BOUNDARY_ONLY = "Doar limita"
DATA_ONLY = "Doar datele"
BOUNDARY_AND_DATA = "Limita și datele"
SPLIT = "Dezmembrat"
# a side has no coordinates for the record, so only its data was compared
BOUNDARY_UNCOMPARED = "Limita necomparată"
CHANGE_COLORS = {BOUNDARY_ONLY: "#E3A008", DATA_ONLY: "#7C5CC4", BOUNDARY_AND_DATA: "#C2410C",
                 SPLIT: "#1C9AA6", BOUNDARY_UNCOMPARED: "#64748B"}
CHANGE_NOTES = {BOUNDARY_ONLY: "S-a mutat doar limita",
                DATA_ONLY: "Limita este aceeași, s-au schimbat datele",
                BOUNDARY_AND_DATA: "S-au schimbat limita și datele",
                SPLIT: "Imobilul din eTerra a fost dezmembrat",
                BOUNDARY_UNCOMPARED: "Limita nu s-a putut compara; s-au schimbat datele"}
# the CGXML column of the measured area, which is computed from the boundary
MEASURED_AREA = "sup_mas"


def change_kind(columns, moved, split=False, measured_area=MEASURED_AREA, uncompared=False):
    """The kind of change of a record whose columns differ and whose boundary moved or not.
    The measured area changes with a moved boundary, so its change then counts as a boundary
    change.
    uncompared: the boundary could not be compared, so every change, the measured area's too,
    is BOUNDARY_UNCOMPARED."""
    if split:
        return SPLIT
    if uncompared:
        return BOUNDARY_UNCOMPARED
    data = set(columns) - ({measured_area} if moved else set())
    if moved:
        return BOUNDARY_AND_DATA if data else BOUNDARY_ONLY
    return DATA_ONLY


def change_categories():
    """(value, label, color) of the change kinds, for layer_style.style_categories."""
    return [(kind, kind, color) for kind, color in CHANGE_COLORS.items()]


def kind_rows(kinds, with_split=False):
    """[("   Doar limita", count)] of the changed records' kinds, indented under their total;
    BOUNDARY_UNCOMPARED only when a record has it."""
    order = [BOUNDARY_ONLY, DATA_ONLY, BOUNDARY_AND_DATA] + ([SPLIT] if with_split else [])
    if BOUNDARY_UNCOMPARED in kinds:
        order.append(BOUNDARY_UNCOMPARED)
    return [("   " + kind, sum(1 for k in kinds if k == kind)) for kind in order]


def normalize_key(value):
    """The key as text, None when empty. 100001.0 and "100001" are the same key, since a
    Shapefile may store an integer column as real. A real that is no number (NaN) or is
    infinite is no key."""
    if is_null(value):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value == int(value):
            value = int(value)
    text = str(value).strip()
    return text or None


# s and t with a cedilla, which older keyboards and fonts write for the Romanian letters with
# a comma below: the same letters
_COMMA_BELOW = str.maketrans("\u015e\u015f\u0162\u0163", "\u0218\u0219\u021a\u021b")


def match_key(key: str) -> str:
    """The form under which two keys name the same record: letter case and the two spellings
    of ș and ț carry no meaning, leading zeros do ("0123" is not "123")."""
    return key.translate(_COMMA_BELOW).casefold()


def normalize_value(value, fold_text=False):
    """The comparable form of a value.

    NULL equals empty text, a number stays a number, a date or a time becomes Python's,
    outer and repeated spaces are ignored, ș and ț are the same letters with a comma or with
    a cedilla, and text stays text ("0123" differs from "123"). fold_text also ignores case
    and diacritics.
    """
    if is_null(value):
        return None
    if isinstance(value, (bool, int, float, date, time)):
        return value
    type_name = type(value).__name__
    if type_name in ("QDate", "QDateTime", "QTime"):
        if value.isNull() or not value.isValid():
            return None
        reader = {"QDate": "toPyDate", "QDateTime": "toPyDateTime", "QTime": "toPyTime"}
        return getattr(value, reader[type_name])()
    text = " ".join(str(value).translate(_COMMA_BELOW).split())
    if fold_text:
        text = fold(text)
    return text or None


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_number(value):
    """value as an int or a float, None for anything that is no number. Text reads with a
    decimal point or comma."""
    if is_number(value):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", ".")
    for read in (int, float):
        try:
            return read(text)
        except ValueError:
            continue
    return None


def _whole(number) -> bool:
    return isinstance(number, int) or (math.isfinite(number) and number.is_integer())


# how text columns write a date and a time: ISO, the Shapefile driver's
# "2024/03/01 10:30:00.000" and the Romanian "01.03.2024"
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y")
_TIME_FORMATS = ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M")


def _as_moment(value):
    """value as a datetime (a date is its midnight) or a time, None for anything else. Text
    is read in _DATE_FORMATS and _TIME_FORMATS."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("T", " ").split())
    for day in _DATE_FORMATS:
        for pattern in [day] + [f"{day} {hour}" for hour in _TIME_FORMATS]:
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    for hour in _TIME_FORMATS:
        try:
            return datetime.strptime(text, hour).time()
        except ValueError:
            continue
    return None


# CGXML columns that join the values of several parcels or persons, with their separators:
# the order carries no meaning, and one parcel can hold several numbers itself. A number
# written twice is one number; a name written twice is two persons with that name
# (cgxml_reader._join_persons). A split imobil joins the different values of its pieces with
# "; ".
LIST_COLUMNS = {"nr_tarla": ",;", "nr_parcele": ",;", "categ_fol": ",;", "nr_titlu": ",;",
                "propr": ",;", "alte_pers": ";", "nr_cad_vechi": ";", "nr_cf_vechi": ";"}
# CGXML columns of numbers and codes typed by hand, where case and spaces carry no meaning
CODE_COLUMNS = ("nr_tarla", "nr_parcele", "categ_fol", "nr_titlu", "nr_cad_vechi",
                "nr_cf_vechi")
# what is written before the number: T7 for tarla 7, CF 4417 for CF number 4417, and before a parcel
# number its use category, A 88/12/3, sometimes after "din"
_CATEGORIES = "|".join(sorted((fold(code) for code in codes.USECATEGORY), key=len, reverse=True))
_PREFIX = {
    "nr_tarla": re.compile(r"t(?=\d)"),
    "nr_parcele": re.compile(f"(?:din)?(?:{_CATEGORIES})(?=\\d)"),
    "nr_cf_vechi": re.compile(r"cf(?=\d)"),
}
# written for no number: "-", ".", "0", "N/A"; never for a use category, where NA is the code
# of Nedeterminată
_NO_NUMBER = re.compile(r"[-./_*]*|0+|n/?a")
_CATEGORY_COLUMN = "categ_fol"
# the electronic CF number eTerra writes after an old one: "560/N(e:50311)"
_E_NUMBER = re.compile(r"\(e:[^)]*\)")


def _code(text, column):
    """A number or code typed by hand as it is compared; "" when it stands for no number."""
    text = _E_NUMBER.sub("", "".join(fold(text).split())).strip(".,:;-")
    prefix = _PREFIX.get(column)
    if prefix is not None:
        text = prefix.sub("", text, count=1) if prefix.match(text) else text
    if column == _CATEGORY_COLUMN:
        return text
    return "" if _NO_NUMBER.fullmatch(text) else text


def counted(value, fold_text=False, column="") -> Counter:
    """{value: times} of the values in a CGXML list or code column: a number or a code once,
    a person as often as the column lists the name."""
    if is_null(value):
        return Counter()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    separators = LIST_COLUMNS.get(column)
    pieces = re.split(f"[{separators}]", str(value)) if separators else [str(value)]
    if column in CODE_COLUMNS:
        found = Counter({_code(piece, column) for piece in pieces})
    else:
        found = Counter(normalize_value(piece, fold_text) for piece in pieces)
    for empty in ("", None):
        found.pop(empty, None)
    return found


def comparable(value, fold_text=False, column=""):
    """normalize_value(); for a CGXML list or code column, its values with how often each
    occurs (counted())."""
    if column not in LIST_COLUMNS and column not in CODE_COLUMNS:
        return normalize_value(value, fold_text)
    return frozenset(counted(value, fold_text, column).items()) or None


def normalized_differ(a, b) -> bool:
    """Whether two comparable() values differ. Whole numbers compare exactly, so two long
    codes that differ in their last digit differ; a real with decimals compares within the
    precision of a float."""
    # a column that is numeric in one layer and text in the other compares as numbers
    if is_number(a) or is_number(b):
        na, nb = _as_number(a), _as_number(b)
        if na is not None and nb is not None:
            if na != na or nb != nb:
                return (na != na) != (nb != nb)
            if _whole(na) and _whole(nb):
                return int(na) != int(nb)
            return abs(na - nb) > 1e-9 * max(1.0, abs(na), abs(nb))
    # and one that is a date in one layer and text in the other as dates
    if isinstance(a, (date, time)) or isinstance(b, (date, time)):
        ma, mb = _as_moment(a), _as_moment(b)
        if ma is not None and mb is not None:
            return type(ma) is not type(mb) or ma != mb
    return a != b


def values_differ(old, new, fold_text=False, column="") -> bool:
    """column, the CGXML column the values come from, applies its rules; see comparable()."""
    return normalized_differ(comparable(old, fold_text, column),
                             comparable(new, fold_text, column))


def index_rows(rows):
    """({key: first row}, {duplicated key: count}, rows without a key) from (key, row)
    pairs."""
    indexed = {}
    duplicates = {}
    missing = 0
    for key, row in rows:
        if key is None:
            missing += 1
            continue
        if key in indexed:
            duplicates[key] = duplicates.get(key, 1) + 1
            continue
        indexed[key] = row
    return indexed, duplicates, missing


def sort_key(key):
    """Numbers in their order, also inside a key: "1203 C2" before "1203 C10"."""
    # isdecimal, as \d: isdigit also accepts "²", which int() refuses
    return [(0, int(part), "") if part.isdecimal() else (1, 0, part)
            for part in re.split(r"(\d+)", key) if part]


def diff(old, new, fields, fold_text=False, columns=None):
    """(added keys, deleted keys, {key: [(field, old, new)]}) of two {key: {field: value}}
    maps, comparing only fields. columns maps a field to the CGXML column it holds."""
    columns = columns or {}
    added = sorted(set(new) - set(old), key=sort_key)
    deleted = sorted(set(old) - set(new), key=sort_key)
    changed = {}
    for key in sorted(set(old) & set(new), key=sort_key):
        differences = [(f, old[key].get(f), new[key].get(f)) for f in fields
                       if values_differ(old[key].get(f), new[key].get(f), fold_text,
                                        columns.get(f, ""))]
        if differences:
            changed[key] = differences
    return added, deleted, changed


def _usable(geometry):
    # symDifference returns an empty geometry for invalid input, which would read as unchanged
    if geometry is None or geometry.isEmpty():
        return None
    if geometry.isGeosValid():
        return geometry
    fixed = geometry.makeValid()
    return fixed if fixed is not None and not fixed.isEmpty() else geometry


def geometry_change(old_geom, new_geom, tolerance):
    """(area before, area after, moved area) when the boundary moved by more than tolerance
    mp, else None. The moved area is the symmetric difference, so a shift without a change in
    size counts."""
    old_geom, new_geom = _usable(old_geom), _usable(new_geom)
    if old_geom is None or new_geom is None:
        if (old_geom is None) != (new_geom is None):
            before = old_geom.area() if old_geom is not None else 0.0
            after = new_geom.area() if new_geom is not None else 0.0
            return before, after, max(before, after)
        return None
    before, after = old_geom.area(), new_geom.area()
    if _exactly_equal(old_geom, new_geom):
        return None
    moved = overlay.sym_difference(old_geom, new_geom)
    moved_area = moved.area() if moved is not None and not moved.isEmpty() else 0.0
    if moved_area <= tolerance and abs(after - before) <= tolerance:
        return None
    return before, after, moved_area


# metres: a point or a line has moved when one of its points lies farther than this from the
# other version, the millimetre cadastral coordinates are written to
DISTANCE_TOLERANCE = 0.001
# the label of a point's or a line's geometry in the reports' lists of what changed
GEOMETRY_DISTANCE = "Geometrie (distanța, m)"


def has_area(wkb_type) -> bool:
    """Whether a layer of this geometry type holds polygons, whose change is measured as an
    area; points and lines have none."""
    return QgsWkbTypes.geometryType(wkb_type) == Qgis.GeometryType.Polygon


def distance_change(old_geom, new_geom, tolerance=DISTANCE_TOLERANCE):
    """(None, None, distance moved in metres) for a point or a line that moved by more than
    tolerance, else None. The distance is the largest one from a point of either version to
    the other version; it is None when only one version has a geometry."""
    old_geom, new_geom = _usable(old_geom), _usable(new_geom)
    if old_geom is None or new_geom is None:
        return None if old_geom is new_geom else (None, None, None)
    if _exactly_equal(old_geom, new_geom):
        return None
    distance = old_geom.hausdorffDistance(new_geom)
    # negative when GEOS cannot measure it; the two versions still differ
    if distance < 0:
        return None, None, None
    return (None, None, distance) if distance > tolerance else None


def key_fields(key) -> list:
    """A key column, or several whose values together make the key."""
    return [key] if isinstance(key, str) else list(key)


def key_text(key) -> str:
    return " + ".join(key_fields(key))


def key_of(feature, fields):
    """The key of a feature, the values of several key columns joined by a space ("1203 C2"),
    None when one is empty."""
    parts = [normalize_key(feature[name]) for name in fields]
    return None if any(part is None for part in parts) else " ".join(parts)


def key_columns(old, new, old_info=None, new_info=None):
    """(old key column, new key column) of two imobile layers, as the comparator pairs them,
    or (None, None) when no column fits.

    Two Conversie CGXML layers pair as their files do: two deliveries by the imobil ID, which
    a new imobil has long before its IE, unless a layer holds no ID at all; with an eTerra
    export, whose cadastral numbers are not the IDs of a delivery, by the IE. Other layers
    pair on the columns named like an ID ('fields/id' in Setări) or like an IE ('fields/ie'),
    whichever leaves fewer rows without a key, the ID when equal.

    old and new are layers, or Processing feature sources with their LayerInfo in old_info
    and new_info; a layer without one gets it read from the layer, on the main thread."""
    sources = (old, new)
    infos = [info if info is not None else LayerInfo(layer)
             for layer, info in zip(sources, (old_info, new_info))]
    if all(info.batch for info in infos):
        if any(info.eterra for info in infos):
            key = "ie"
        else:
            key = "id" if all(_has_keys(source, info.column_of("id"))
                              for source, info in zip(sources, infos)) else "ie"
        columns = tuple(info.column_of(key) for info in infos)
        return columns if all(columns) else (None, None)
    pairs = []
    for role in ("fields/id", "fields/ie"):
        patterns = settings_service.patterns(role)
        columns = tuple(roles.pick_field([name for name in source.fields().names()
                                          if name not in info.row_number_fields], patterns)
                        for source, info in zip(sources, infos))
        if all(columns) and columns not in pairs:
            pairs.append(columns)
    if not pairs:
        return None, None
    if len(pairs) == 1:
        return pairs[0]
    best, fewest = None, None
    for columns in pairs:
        # zero rows without a key is the best any pair can give
        if fewest == 0:
            break
        count = sum(_rows_without_key(source, column)
                    for source, column in zip(sources, columns))
        if fewest is None or count < fewest:
            best, fewest = columns, count
    return best


def _has_keys(source, column) -> bool:
    index = source.fields().indexOf(column or "")
    return index >= 0 and any(normalize_key(v) is not None
                              for v in source.uniqueValues(index, 2))


# _rows_without_key of a layer's columns, as {column: count}: the window resolves the key
# columns again at every change of its layers or of their fields
_EMPTY_KEYS = LayerCache()


def _rows_without_key(source, column) -> int:
    """The rows of a layer or a Processing feature source with no value in column."""
    layer = source if isinstance(source, QgsVectorLayer) else None
    counts = _EMPTY_KEYS.get(layer.id()) if layer is not None else None
    if counts is not None and column in counts:
        return counts[column]
    quoted = QgsExpression.quotedColumnRef(column)
    request = QgsFeatureRequest()
    # NULL or blank text; GeoPackage and database layers run this filter as SQL
    request.setFilterExpression(f"{quoted} IS NULL OR trim({quoted}) = ''")
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setNoAttributes()
    count = sum(1 for _feature in source.getFeatures(request))
    if layer is not None:
        counts = dict(counts or {})
        counts[column] = count
        _EMPTY_KEYS.put(layer, counts)
    return count


@dataclass
class LayerPlan:
    """How two imobile layers are compared where nothing is chosen by hand: what the window
    preselects and what Processing takes for a parameter left empty."""
    key_old: Optional[str]
    key_new: Optional[str]
    # the columns both layers have, the keys and the row numbers left out
    columns: list
    # of columns, those compared unless others are chosen
    ticked: list
    # cgxml_compare.eterra_advice()
    advice: str
    # the CGXML column that names a construcție's imobil in the construcții layers converted
    # with these imobile: "ie_imobil" when the imobile pair on their IE, else "id"
    imobil_column: str


@dataclass
class BuildingPlan:
    """How two construcții layers are compared where nothing is chosen by hand."""
    # [imobil column, number column] of each layer, None for a column that is not known
    key_old: list
    key_new: list
    columns: list
    ticked: list


def _info(source, info):
    """The LayerInfo given, else read from a layer, on the main thread; None for a Processing
    feature source without one."""
    if info is not None:
        return info
    return LayerInfo(source) if isinstance(source, QgsVectorLayer) else None


def _data_fields(source, info) -> list:
    """The columns of a layer or feature source that hold data; a name spelled twice in
    different letter case counts once, as the first spelling."""
    skip = set(info.row_number_fields) if info is not None else set()
    names, seen = [], set()
    for name in source.fields().names():
        if name not in skip and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def _compared(old, new, infos, keys, defaults):
    """(columns, ticked) of a plan. defaults are the CGXML columns two Conversie CGXML layers
    compare unless others are chosen; other layers compare every column they share."""
    from .cgxml_compare import converted_columns
    new_names = set(_data_fields(new, infos[1]))
    columns = [name for name in _data_fields(old, infos[0])
               if name in new_names and name not in keys]
    chosen = converted_columns(*infos, defaults)
    return columns, [name for name in columns if chosen is None or name in chosen]


def layer_plan(old, new, old_info=None, new_info=None, keys=(None, None),
               names=("Layerul vechi", "Layerul nou")) -> LayerPlan:
    """The LayerPlan of two imobile layers, or of two Processing feature sources with their
    LayerInfo. keys are the key columns chosen by hand, None for a side left to
    key_columns(); names is what the advice calls each layer."""
    from .cgxml_compare import DEFAULT_COLUMNS, eterra_advice
    infos = (_info(old, old_info), _info(new, new_info))
    found = key_columns(old, new, *infos) if all(infos) and not all(keys) else (None, None)
    listed = [source.fields().names() for source in (old, new)]
    # a key chosen for one layer only is the other layer's too when it has that column
    key_old, key_new = (keys[side] or (keys[1 - side] if keys[1 - side] in listed[side]
                                       else found[side]) for side in (0, 1))
    columns, ticked = _compared(old, new, infos, (key_old, key_new), DEFAULT_COLUMNS)
    ie_columns = tuple(info.column_of("ie") if info is not None else None for info in infos)
    by_ie = all(ie_columns) and (key_old, key_new) == ie_columns
    return LayerPlan(key_old, key_new, columns, ticked, eterra_advice(*infos, names),
                     "ie_imobil" if by_ie else "id")


def building_plan(old, new, old_info=None, new_info=None, imobil_column="id",
                  keys=((None, None), (None, None))) -> BuildingPlan:
    """The BuildingPlan of two construcții layers or feature sources. imobil_column comes
    from the LayerPlan of their imobile; keys are the (imobil column, number column) chosen
    by hand for each layer, None for one left to the columns a conversion wrote. An imobil
    column of "" is the choice of none: the construcții pair by their number alone, and the
    column a conversion wrote for the imobil is one of the columns to compare."""
    from .cgxml_compare import BUILDING_COLUMNS
    infos = (_info(old, old_info), _info(new, new_info))
    sides = []
    for info, (imobil, number) in zip(infos, keys):
        if info is not None:
            if imobil is None:
                imobil = info.column_of(imobil_column)
            number = number or info.column_of("nr_constr")
        sides.append([imobil or None, number or None])
    chosen = [name for side in sides for name in side if name]
    columns, ticked = _compared(old, new, infos, chosen, BUILDING_COLUMNS)
    return BuildingPlan(sides[0], sides[1], columns, ticked)


# the result categories of a comparison
ADDED, DELETED, CHANGED = "added", "deleted", "changed"


@dataclass
class ResultFeatures:
    """The features of one result category, for a memory layer or a Processing sink."""
    fields: QgsFields
    features: list
    wkb_type: object
    # the field the changed records are styled by, None for the other categories or with the
    # geometry not compared
    kind_field: Optional[str] = None
    # records of the category left without a feature: in a CGXML comparison, those without
    # coordinates
    left_out: int = 0


class CompareTask(CadexTask):
    """The layer comparison as a background task. The features kept for the result layers
    are in Stereo 70.

    key_old and key_new are a column or a list of them. buildings, a CompareTask of the
    construcții layers, is run within this one; it is never added to the task manager.
    """

    def __init__(self, *, old, new, key_old, key_new, fields, compare_geometry,
                 tolerance, excel_path, fold_text=False, buildings=None, finished_cb=None):
        super().__init__("CADex: Comparator", finished_cb)
        self.old = old
        self.new = new
        self.key_old = key_old
        self.key_new = key_new
        self.fields = list(fields)
        self.compare_geometry = compare_geometry
        self.tolerance = tolerance
        self.excel_path = excel_path
        self.fold_text = fold_text
        self.buildings = buildings
        # the field of a converted layer that holds the measured area
        self._measured_area = None
        # the rows read from each layer, those without a key and the repeats included
        self.old_read = self.new_read = 0
        # {match_key(key): the key as first read}, under which both layers' rows are kept
        self._spelling = {}
        # a layer holds points or lines, so the geometry is compared by distance, not by area
        self.by_distance = compare_geometry and not (has_area(old.wkb_type)
                                                     and has_area(new.wkb_type))

    def missing_columns(self) -> str:
        """The Romanian sentence for the first layer that lacks a key or compared column, ""
        when both have them all: a column renamed or deleted after it was chosen, or chosen
        from one layer's list only."""
        for snapshot, key in ((self.old, self.key_old), (self.new, self.key_new)):
            names = snapshot.fields.names()
            missing = []
            for name in key_fields(key) + self.fields:
                if name not in names and name not in missing:
                    missing.append(name)
            if missing:
                return fmt.plural(len(missing), "Layerul '{0}' nu are coloana {1}.",
                                  "Layerul '{0}' nu are coloanele {1}.").format(
                    snapshot.name, ", ".join(f"'{name}'" for name in missing))
        return ""

    def _read(self, snapshot, key, keep_geometry, report):
        """(rows by key, repeated keys, rows without a key, features by key, rows read)."""
        rows = []
        features = {}
        total = max(snapshot.count, 1)
        names = snapshot.fields.names()
        fields = key_fields(key)
        for i, (feature, geometry) in enumerate(snapshot.features(with_empty=True)):
            if self.stopped():
                return {}, {}, 0, {}, 0
            row_key = key_of(feature, fields)
            if row_key is not None:
                row_key = self._spelling.setdefault(match_key(row_key), row_key)
            values = {name: feature[name] for name in names}
            rows.append((row_key, {"values": values,
                                   "geometry": QgsGeometry(geometry)
                                   if keep_geometry and geometry is not None else None}))
            if row_key is not None and row_key not in features:
                copy = QgsFeature(feature)
                if geometry is not None:
                    copy.setGeometry(geometry)
                features[row_key] = copy
            if i % 200 == 0:
                report(i / total)
        indexed, duplicates, missing = index_rows(rows)
        return indexed, duplicates, missing, features, len(rows)

    def compare(self, start=0.0, end=80.0) -> bool:
        """Read and compare both layers, reporting progress from start to end; False when
        stopped."""
        span = end - start
        (self.old_rows, self.old_dups, self.old_missing, self.old_features,
         self.old_read) = self._read(self.old, self.key_old, self.compare_geometry,
                                     lambda share: self.report(start + 0.4 * span * share))
        (self.new_rows, self.new_dups, self.new_missing, self.new_features,
         self.new_read) = self._read(self.new, self.key_new, self.compare_geometry,
                                     lambda share: self.report(start + 0.4 * span * (1 + share)))
        if self.stopped():
            return False
        old_values = {k: r["values"] for k, r in self.old_rows.items()}
        new_values = {k: r["values"] for k, r in self.new_rows.items()}
        # two Conversie CGXML layers compare as their files do
        columns = {name: column for name, column in self.old.cgxml_columns.items()
                   if self.new.cgxml_columns.get(name) == column}
        self._measured_area = next(
            (name for name, column in columns.items() if column == MEASURED_AREA), None)
        self.added, self.deleted, self.changed = diff(old_values, new_values,
                                                      self.fields, self.fold_text, columns)
        self.geometry_changes = {}
        if self.compare_geometry:
            for key in sorted(set(self.old_rows) & set(self.new_rows), key=sort_key):
                if self.stopped():
                    return False
                old_geom, new_geom = self.old_rows[key]["geometry"], self.new_rows[key]["geometry"]
                if self.by_distance:
                    change = distance_change(old_geom, new_geom)
                else:
                    change = geometry_change(old_geom, new_geom, self.tolerance)
                if change is not None:
                    self.geometry_changes[key] = change
        self.report(end)
        return True

    def run(self):
        try:
            problem = self.missing_columns() or (
                self.buildings.missing_columns() if self.buildings is not None else "")
            if problem:
                self.error_msg = problem
                return False
            if not self.compare(0.0, 50.0 if self.buildings is not None else 80.0):
                return False
            if self.buildings is not None:
                # the inner task reports through this one
                self.buildings.stop_hook = self.stopped
                self.buildings.report = self.report
                if not self.buildings.compare(50.0, 90.0):
                    return False
            if self.excel_path:
                self.write_report(self._write_excel, self.excel_path)
            self.report(100.0)
            return True
        except Exception as e:
            return self.failed("Comparator", e)

    def result_features(self, category) -> ResultFeatures:
        """The features of the ADDED, DELETED or CHANGED records with their layer's fields and
        geometry type, in Stereo 70. The changed ones also hold the kind of change (with the
        geometry compared), the columns that changed and the moved area."""
        if category == DELETED:
            snapshot, by_key, keys = self.old, self.old_features, self.deleted
        else:
            snapshot, by_key = self.new, self.new_features
            keys = self.added if category == ADDED else self.changed_keys()
        changed = category == CHANGED
        fields = QgsFields(snapshot.fields)
        kind_field = None
        if changed:
            if self.compare_geometry:
                kind_field = unique_name(fields, CHANGE_KIND_FIELD)
                fields.append(QgsField(kind_field, FIELD_STRING))
            fields.append(QgsField(unique_name(fields, "modificari"), FIELD_STRING))
            if self.compare_geometry:
                # the moved area of a polygon in mp, the distance a point or a line moved in m
                name, decimals = ("dist_mutata", 3) if self.by_distance else ("sup_mutata", 2)
                fields.append(QgsField(unique_name(fields, name), FIELD_DOUBLE, "double", 20,
                                       decimals))
        features, left_out = [], 0
        for key in keys:
            source = by_key.get(key)
            if source is None:
                left_out += 1
                continue
            feature = QgsFeature(fields)
            if source.hasGeometry():
                feature.setGeometry(source.geometry())
            values = list(source.attributes())
            if changed:
                if kind_field:
                    values.append(self.change_kind(key))
                names = [name for name, _old, _new in self.changed.get(key, [])]
                if key in self.geometry_changes:
                    names.append("geometrie")
                values.append(", ".join(names))
                if self.compare_geometry:
                    values.append(self._moved(key))
            feature.setAttributes(values)
            features.append(feature)
        return ResultFeatures(fields, features, snapshot.wkb_type, kind_field, left_out)

    def _moved(self, key):
        """The moved area of a key's polygon in mp, or the distance its point or line moved
        in m; None when its geometry did not change or the distance is not known."""
        moved = self.geometry_changes.get(key, (None, None, None))[2]
        return None if moved is None else round(moved, 3 if self.by_distance else 2)

    def geometry_fact(self) -> str:
        """What was compared of the geometry, for the report and the result."""
        if not self.compare_geometry:
            return "Nu"
        if self.by_distance:
            return ("Da, după distanță, cu toleranța "
                    f"{fmt.number(DISTANCE_TOLERANCE * 1000, 0)} mm (puncte și linii)")
        return f"Da, toleranță {fmt.area(self.tolerance)}"

    def geometry_note(self) -> str:
        """The sentence on a geometry compared by distance, "" for one compared by area or
        not compared."""
        if not self.by_distance:
            return ""
        return (f"Layerele '{self.old.name}' și '{self.new.name}' nu sunt amândouă de "
                "poligoane, deci geometria s-a comparat după distanță: este mutată când un "
                f"punct al ei se află la peste {fmt.number(DISTANCE_TOLERANCE * 1000, 0)} mm "
                "de cealaltă versiune.")

    def changed_keys(self):
        return sorted(set(self.changed) | set(self.geometry_changes), key=sort_key)

    def unchanged_count(self):
        return len(set(self.old_rows) & set(self.new_rows)) - len(self.changed_keys())

    def change_kind(self, key):
        """BOUNDARY_ONLY, DATA_ONLY or BOUNDARY_AND_DATA for a changed key; with the geometry
        not compared, every change is in the data."""
        return change_kind([f for f, _old, _new in self.changed.get(key, [])],
                           key in self.geometry_changes, measured_area=self._measured_area)

    def kind_rows(self):
        """kind_rows() of the changed keys, [] when the geometry was not compared."""
        if not self.compare_geometry:
            return []
        return kind_rows([self.change_kind(k) for k in self.changed_keys()])

    def _write_excel(self):
        wb = xlsx_report.new_workbook()
        summary = xlsx_report.Summary(wb, "Comparator")
        summary.section("Informații")
        facts = [
            ("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
            ("Layer vechi", f"{self.old.name} (cheie '{key_text(self.key_old)}')"),
            ("Layer nou", f"{self.new.name} (cheie '{key_text(self.key_new)}')"),
            ("Coloane comparate", ", ".join(self.fields) or "-"),
        ]
        b = self.buildings
        if b is not None:
            facts += [
                ("Construcții, layer vechi", f"{b.old.name} (cheie '{key_text(b.key_old)}')"),
                ("Construcții, layer nou", f"{b.new.name} (cheie '{key_text(b.key_new)}')"),
                ("Coloane comparate ale construcțiilor", ", ".join(b.fields) or "-"),
            ]
        facts += [
            ("Geometrie comparată", self.geometry_fact()),
            ("Se ignoră majusculele și diacriticele", "Da" if self.fold_text else "Nu"),
            ("Sistem de coordonate", stereo70.NAME),
        ]
        summary.facts(facts)
        summary.section("Imobile" if b is not None else "Rezultat")
        self._summary_table(summary)
        if b is not None:
            summary.section("Construcții")
            b._summary_table(summary)
        self._write_sheets(wb, "")
        if b is not None:
            b._write_sheets(wb, "Construcții ")
        xlsx_report.save(wb, self.excel_path)

    def _summary_table(self, summary):
        kinds = [[label, count, "", CHANGE_NOTES[label.strip()]]
                 for label, count in self.kind_rows()]
        summary.table(
            ["Categorie", "Nr.", "", ""],
            [["Înregistrări în layerul vechi", self.old_read, "", ""],
             ["Înregistrări în layerul nou", self.new_read, "", ""],
             ["Apărute", len(self.added), "", "Există doar în layerul nou"],
             ["Dispărute", len(self.deleted), "", "Există doar în layerul vechi"],
             ["Modificate", len(self.changed_keys()), "",
              "Cel puțin o coloană comparată sau geometria diferă"]]
            + kinds
            + [["Nemodificate", self.unchanged_count(), "", ""],
               ["Rânduri fără cheie, ignorate", self.old_missing + self.new_missing, "",
                f"{self.old_missing} în layerul vechi, {self.new_missing} în cel nou"],
               ["Chei duplicate", len(self.old_dups) + len(self.new_dups), "",
                "Se compară doar primul rând al fiecărei chei"]],
            row_colors=[None, None, xlsx_report.GOOD, xlsx_report.BAD, xlsx_report.MIXED]
            + [None] * len(kinds)
            + [None, None, xlsx_report.NOTE if (self.old_dups or self.new_dups) else None])

    def _write_sheets(self, wb, prefix):
        """The sheets of added, removed and changed rows and of repeated keys; prefix starts
        their names, e.g. "Construcții "."""
        def title(name):
            return prefix + (name.lower() if prefix else name)

        new_names = self.new.data_field_names()
        old_names = self.old.data_field_names()
        if self.added:
            xlsx_report.write_table(
                wb, title("Apărute"), new_names,
                [[self.new_rows[k]["values"].get(n) for n in new_names] for k in self.added],
                row_colors=[xlsx_report.GOOD] * len(self.added))
        if self.deleted:
            xlsx_report.write_table(
                wb, title("Dispărute"), old_names,
                [[self.old_rows[k]["values"].get(n) for n in old_names] for k in self.deleted],
                row_colors=[xlsx_report.BAD] * len(self.deleted))
        detail = []
        for key in self.changed_keys():
            # with the geometry compared, the kind of change after the key, to filter on
            lead = [key, self.change_kind(key)] if self.compare_geometry else [key]
            for field, before, after in self.changed.get(key, []):
                detail.append(lead + [field, before, after, ""])
            if key in self.geometry_changes and self.by_distance:
                detail.append(lead + [GEOMETRY_DISTANCE, None, None, self._moved(key)])
            elif key in self.geometry_changes:
                before, after, _moved = self.geometry_changes[key]
                detail.append(lead + [GEOMETRY, round(before, 2), round(after, 2),
                                      self._moved(key)])
        if detail:
            xlsx_report.write_table(
                wb, title("Modificate"),
                ["Cheie"] + (["Tip modificare"] if self.compare_geometry else [])
                + ["Coloana", "Valoare veche", "Valoare nouă",
                   "Distanță (m)" if self.by_distance else "Suprafață mutată (mp)"],
                detail, row_colors=[xlsx_report.MIXED] * len(detail),
                text_columns=["Cheie"])
        duplicates = ([["vechi", k, n] for k, n in sorted(self.old_dups.items())]
                      + [["nou", k, n] for k, n in sorted(self.new_dups.items())])
        if duplicates:
            xlsx_report.write_table(wb, title("Chei duplicate"),
                                    ["Layer", "Cheie", "Nr. rânduri"], duplicates,
                                    row_colors=[xlsx_report.NOTE] * len(duplicates),
                                    text_columns=["Cheie"])
