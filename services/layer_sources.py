# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer snapshots for background tasks, with geometries returned in Stereo 70.

A window takes LayerSnapshot(layer) on the main thread. A Processing algorithm reads the
layer's properties on the main thread into a LayerInfo (CadexAlgorithm.prepareAlgorithm) and
wraps its feature source with LayerSnapshot.of_source() on the algorithm's thread. Either way
the worker thread only reads features.
"""

from qgis.core import (  # type: ignore
    Qgis, QgsFeatureRequest, QgsField, QgsFields, QgsProcessingFeatureSource,
    QgsVectorLayerFeatureSource,
)

from . import roles, stereo70
from ..qt_compat import FIELD_DOUBLE, FIELD_INT, FIELD_LONGLONG, FIELD_STRING

# on Qgis from 3.36, on QgsProcessingFeatureSource in 3.34
if hasattr(Qgis, "ProcessingFeatureSourceFlag"):
    SKIP_VALIDITY_CHECKS = Qgis.ProcessingFeatureSourceFlag.SkipGeometryValidityChecks
else:
    SKIP_VALIDITY_CHECKS = QgsProcessingFeatureSource.Flag.FlagSkipGeometryValidityChecks

# the names GDAL gives the FID column it shows as a field; a primary key named otherwise, such
# as id from "ogr2ogr -lco FID=id", holds the user's data
_FID_NAMES = ("fid", "ogc_fid", "objectid")
# an export from GeoPackage to Shapefile keeps the row numbers as an ordinary column
_COPIED_ROW_NUMBERS = ("fid", "ogc_fid")


def row_number_fields(layer) -> list:
    """The columns that only number the rows: the FID column GDAL shows as a field (fid in a
    GeoPackage) and a copy of one. Two files written from different sets of features number
    their rows independently, so these columns are not data to compare or report."""
    if layer is None:
        return []
    fields = layer.fields()
    names = []
    if layer.providerType() == "ogr":
        names = [fields.at(i).name() for i in layer.primaryKeyAttributes()
                 if fields.at(i).name().lower() in _FID_NAMES]
    return names + [name for name in fields.names()
                    if name.lower() in _COPIED_ROW_NUMBERS and name not in names]


class LayerInfo:
    """What the tools read from a layer besides its features: name, row_number_fields,
    cgxml_columns ({field: CGXML column}), the Conversie CGXML batch and whether it holds a
    converted eTerra export. Built on the main thread, then used on a worker thread in place
    of the layer."""

    def __init__(self, layer):
        from .cgxml_compare import is_eterra_export
        self.name = layer.name()
        self.row_number_fields = row_number_fields(layer)
        self.cgxml_columns = roles.columns_of(layer)
        self.batch = roles.batch_of(layer)
        self.eterra = is_eterra_export(layer)

    def column_of(self, key):
        """The field holding a CGXML column, None for a layer without it (roles.column_of)."""
        return next((name for name, column in self.cgxml_columns.items() if column == key),
                    None)


class LayerSnapshot:
    """What a worker thread needs from one layer, read through a
    QgsVectorLayerFeatureSource. of_source() wraps a Processing feature source."""

    def __init__(self, layer, *, selected_only=False, context=None):
        self._setup(layer.name(), layer.fields(), layer.crs(), layer.wkbType(),
                    QgsVectorLayerFeatureSource(layer),
                    list(layer.selectedFeatureIds()) if selected_only else None,
                    layer.featureCount(), layer.extent(), context)
        self.row_number_fields = row_number_fields(layer)
        self.cgxml_columns = roles.columns_of(layer)
        self._processing = False

    @classmethod
    def of_source(cls, source, name, context=None, info=None):
        """info is the LayerInfo of the source's layer, when there is one; row_number_fields
        and cgxml_columns are taken from it."""
        snapshot = cls.__new__(cls)
        snapshot._setup(name, source.fields(), source.sourceCrs(), source.wkbType(),
                        source, None, source.featureCount(), source.sourceExtent(), context)
        snapshot.row_number_fields = list(info.row_number_fields) if info is not None else []
        snapshot.cgxml_columns = dict(info.cgxml_columns) if info is not None else {}
        snapshot._processing = True
        return snapshot

    def data_field_names(self) -> list:
        return [name for name in self.fields.names() if name not in self.row_number_fields]

    def _setup(self, name, fields, crs, wkb_type, source, fids, count, extent, context):
        self.name = name
        self.fields = QgsFields(fields)
        self.crs = crs
        self.wkb_type = wkb_type
        self.source = source
        self.fids = fids
        self.count = len(fids) if fids is not None else max(count, 0)
        self.transform, self.crs_state = stereo70.transform_for(crs, extent, context)

    def request(self):
        request = QgsFeatureRequest()
        if self.fids is not None:
            request.setFilterFids(self.fids)
        return request

    def features(self, with_empty=False):
        """Yield (feature, geometry in Stereo 70). with_empty also yields (feature, None) for
        features without geometry."""
        if self._processing:
            # the tools report or repair invalid geometries themselves, so Processing's
            # setting that stops a run at the first one must not apply
            iterator = self.source.getFeatures(self.request(), SKIP_VALIDITY_CHECKS)
        else:
            iterator = self.source.getFeatures(self.request())
        for feature in iterator:
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                if with_empty:
                    yield feature, None
                continue
            if self.transform is not None:
                geometry.transform(self.transform)
            yield feature, geometry


def _field_keys(layer, field):
    """({(UAT, key)}, whether the layer has a UAT column) of the values in field, None when
    the layer has no such field. The UAT is that of a Conversie CGXML layer, else None."""
    from .compare_service import normalize_key
    fields = layer.fields()
    index = fields.lookupField(field)
    if index < 0:
        return None
    uat = fields.indexOf(roles.column_of(layer, "uat") or "")
    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    request.setSubsetOfAttributes([i for i in (index, uat) if i >= 0])
    keys = set()
    for feature in layer.getFeatures(request):
        attributes = feature.attributes()
        key = normalize_key(attributes[index])
        if key is not None:
            keys.add((normalize_key(attributes[uat]) if uat >= 0 else None, key))
    return keys, uat >= 0


def _comparable(first, second):
    """The two key sets of _field_keys, with the UAT only when both layers have one."""
    (first_keys, first_uat), (second_keys, second_uat) = first, second
    if first_uat and second_uat:
        return first_keys, second_keys
    return ({(None, key) for _uat, key in first_keys},
            {(None, key) for _uat, key in second_keys})


def shared_keys(layers, field, sample=5):
    """(first layer, second layer, up to sample shared keys, number of keys shared) for the
    first two layers that look like versions of the same data by the rule of same_imobile, or
    None. Keys compare as the comparator compares them, 100001.0 from a Shapefile is 100001,
    and together with the UAT when both layers come from Conversie CGXML with a UAT column."""
    seen = []
    for layer in layers:
        keys = _field_keys(layer, field)
        if keys is None:
            continue
        for other, other_keys in seen:
            first, second = _comparable(other_keys, keys)
            if same_imobile(first, second):
                common = first & second
                ordered = sorted({key for _uat, key in common}, key=lambda k: (len(k), k))
                return other, layer, ordered[:sample], len(common)
        seen.append((layer, keys))
    return None


class LayerCache:
    """Values computed from layers, by layer id. A layer's value is dropped when its data,
    fields or source change and when the layer is deleted. Main thread only."""

    def __init__(self):
        self._values = {}
        self._watched = set()

    def __contains__(self, layer_id):
        return layer_id in self._values

    def get(self, layer_id):
        return self._values.get(layer_id)

    def put(self, layer, value):
        layer_id = layer.id()
        self._values[layer_id] = value
        if layer_id in self._watched:
            return
        self._watched.add(layer_id)

        def drop(*_args):
            self._values.pop(layer_id, None)

        def forget(*_args):
            self._values.pop(layer_id, None)
            self._watched.discard(layer_id)

        # layerModified: an edit not yet saved; editingStopped: saved or rolled back;
        # dataChanged: the provider's data, for example after a reload
        for signal in (layer.layerModified, layer.editingStopped, layer.dataChanged,
                       layer.updatedFields, layer.dataSourceChanged):
            signal.connect(drop)
        layer.willBeDeleted.connect(forget)


# imobil_keys of each layer, as ((batch, columns property), keys): the windows call
# imobil_keys again at every change in the project, and a converted layer's keys stay as
# written
_KEYS = LayerCache()


def imobil_keys(layers) -> set:
    """(column, UAT, value) of the IDs and IEs in the converted layers' records; both are
    unique within a UAT only."""
    keys = set()
    for layer in layers:
        keys |= _layer_keys(layer)
    return keys


def _layer_keys(layer) -> frozenset:
    stamp = (roles.batch_of(layer),
             str(layer.customProperty(roles.COLUMNS_PROPERTY, "") or ""))
    cached = _KEYS.get(layer.id())
    if cached is not None and cached[0] == stamp:
        return cached[1]
    keys = frozenset(_read_imobil_keys(layer))
    _KEYS.put(layer, (stamp, keys))
    return keys


def _read_imobil_keys(layer) -> set:
    from .compare_service import normalize_key
    keys = set()
    uat = layer.fields().indexOf(roles.column_of(layer, "uat") or "")
    for column in ("id", "ie"):
        index = layer.fields().indexOf(roles.column_of(layer, column) or "")
        if index < 0:
            continue
        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        request.setSubsetOfAttributes([i for i in (index, uat) if i >= 0])
        for feature in layer.getFeatures(request):
            attributes = feature.attributes()
            key = normalize_key(attributes[index])
            if key is not None:
                keys.add((column, normalize_key(attributes[uat]) if uat >= 0 else None, key))
    return keys


def same_imobile(first: set, second: set) -> bool:
    """Whether two sets of keys, such as two runs' imobil_keys(), belong to versions of the
    same imobile: they share at least a tenth of the smaller set. Different sectors share
    none, or a few cadastral numbers by chance."""
    shared = len(first & second)
    return shared > 0 and shared >= 0.1 * min(len(first), len(second))


def current_conversions(layers) -> list:
    """The layers without a conversion run, and of the converted ones those no later run
    holds a newer version of. Runs of different sectors are all current."""
    runs = {}
    for layer in layers:
        runs.setdefault(roles.batch_of(layer), []).append(layer)
    batches = sorted(batch for batch in runs if batch)
    if len(batches) < 2:
        return list(layers)
    keys = {batch: imobil_keys(runs[batch]) for batch in batches}
    older = {batch for i, batch in enumerate(batches)
             if any(same_imobile(keys[batch], keys[later]) for later in batches[i + 1:])}
    return [layer for layer in layers if roles.batch_of(layer) not in older]


def merged_fields(snapshots) -> QgsFields:
    """The union of the snapshots' fields without their row_number_fields, matched by name
    regardless of case, the first spelling kept. A name with different types in two layers
    takes the wider number type (Int and LongLong give LongLong, an integer and a real give
    Double), else becomes text. A field takes the largest length of the layers, 0 (no limit)
    when any layer sets none."""
    merged = []
    positions = {}
    for snapshot in snapshots:
        for field in snapshot.fields:
            if field.name() in snapshot.row_number_fields:
                continue
            key = field.name().lower()
            if key in positions:
                merged[positions[key]] = _widened(merged[positions[key]], field)
            else:
                positions[key] = len(merged)
                merged.append(QgsField(field))
    result = QgsFields()
    for field in merged:
        result.append(field)
    return result


def merged_value(attributes: dict, name):
    """attributes[name], else the value under a name that differs from it only in case:
    merged_fields() names a column with its first layer's spelling. None when absent."""
    if name in attributes:
        return attributes[name]
    folded = name.lower()
    return next((value for key, value in attributes.items() if key.lower() == folded), None)


def _widened(kept, other) -> QgsField:
    """kept, under its own name, made able to hold other's values too."""
    if kept.type() == other.type():
        field = QgsField(kept)
        lengths = (kept.length(), other.length())
        # a length of 0 or less sets no limit
        field.setLength(0 if min(lengths) <= 0 else max(lengths))
        field.setPrecision(0 if field.length() == 0 else max(kept.precision(), other.precision()))
        return field
    integers = (FIELD_INT, FIELD_LONGLONG)
    numbers = integers + (FIELD_DOUBLE,)
    if kept.type() in integers and other.type() in integers:
        wider = kept if kept.type() == FIELD_LONGLONG else other
    elif kept.type() in numbers and other.type() in numbers:
        wider = kept if kept.type() == FIELD_DOUBLE else other
    else:
        return QgsField(kept.name(), FIELD_STRING, "string", 0)
    field = QgsField(wider)
    field.setName(kept.name())
    field.setLength(0)
    field.setPrecision(0)
    return field


def unique_name(fields: QgsFields, wanted: str) -> str:
    """wanted, or wanted_2, wanted_3... when taken (case-insensitive)."""
    existing = {fields.at(i).name().lower() for i in range(fields.count())}
    if wanted.lower() not in existing:
        return wanted
    n = 2
    while f"{wanted}_{n}".lower() in existing:
        n += 1
    return f"{wanted}_{n}"
