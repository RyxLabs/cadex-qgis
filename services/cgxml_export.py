# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversie CGXML: CGXML files to GeoPackage, Shapefile, DXF or Excel."""

import errno
import os
from collections import defaultdict
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime

from qgis.core import (  # type: ignore
    Qgis, QgsCoordinateTransformContext, QgsFeature, QgsField, QgsFields, QgsGeometry,
    QgsLineString, QgsMultiPolygon, QgsPolygon, QgsVectorFileWriter,
)

from . import (
    cgxml_columns, cgxml_sources, codes, dxf_writer, fmt, outputs, siruta, stereo70,
    xlsx_report,
)
from .roles import normalize
from .tasks import CadexTask, log
from ..qt_compat import FIELD_DOUBLE, FIELD_LONGLONG, FIELD_STRING

EXTENSIONS = {"gpkg": ".gpkg", "shp": ".shp", "dxf": ".dxf", "xlsx": ".xlsx"}

# default DXF label (prefix, suffix) per column key
LABEL_DEFAULTS = {
    "id": ("ID ", ""),
    "ie": ("IE ", ""),
    "ie_imobil": ("IE ", ""),
    "sector": ("S", ""),
    "nr_tarla": ("T", ""),
    "nr_titlu": ("TP ", ""),
    "etaje": ("", " niv."),
    "sup_mas": ("", " mp"),
    "sup_acte": ("", " mp"),
    "sup_tot": ("", " mp"),
}
DEFAULT_LABELS = {"imobile": ("id", "ie"), "constructii": ("nr_constr", "destinatie")}


def _never():
    return False


class Canceled(Exception):
    """Raised while an output is written when the run is canceled; the output is not put in
    place."""


def format_value(value) -> str:
    """Label text: 2628.0 -> "2628", 2628.5 -> "2628,5", None -> ""."""
    if value is None:
        return ""
    if isinstance(value, float):
        return fmt.number(value, 2, thousands=False)
    return str(value)


def table_name(text: str) -> str:
    """ASCII table or file name for a layer name; the QGIS layer keeps the original name."""
    return normalize(text) or "layer"


def stored_layer_name(fmt_key: str, text: str) -> str:
    """The name a layer gets inside an output of format fmt_key: its GeoPackage table or
    Shapefile name, its DXF layer or its Excel sheet. Two layers whose names are equal here,
    ignoring case, would be written to the same place: Windows file names, DXF layers and
    Excel sheets are all compared without case."""
    if fmt_key == "dxf":
        return dxf_writer.cad_layer_name(text)
    if fmt_key == "xlsx":
        return text.strip()
    return table_name(text)


# the default output file name is this prefix and the source's name
BASE_PREFIX = "cgxml_"


def _stem(path) -> str:
    return os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]


def source_stem(paths) -> str:
    """The name of a source: a folder's or archive's name, the common folder of several
    files, "" without paths. Paths on different drives have no common folder; the first
    one names them."""
    if len(paths) == 1:
        return _stem(paths[0])
    if paths:
        try:
            common = os.path.commonpath([os.path.abspath(p) for p in paths])
        except ValueError:
            return _stem(paths[0])
        return os.path.basename(os.path.dirname(common + os.sep))
    return ""


def display_name(layer_name: str, base_name: str) -> str:
    """The project name of a converted layer, "Imobile livrare_v2" for the output file
    cgxml_livrare_v2: two deliveries converted into one project differ in every layer list,
    while the tables and files keep the layer name."""
    prefixed = base_name.lower().startswith(BASE_PREFIX)
    label = base_name[len(BASE_PREFIX):] if prefixed else base_name
    return f"{layer_name} {label}" if label else layer_name


# --- records ---

def address_text(record: dict, every_locality=False) -> str:
    """"UAT Bod, loc. Colonia Bod, Strada Morii, Nr. 7". A locality with the UAT's name is
    left out; with every_locality it is named too, and only an address holding the UAT's own
    SIRUTA code has none."""
    parts = []
    if record.get("uat"):
        parts.append(f"UAT {record['uat']}")
    locality = record.get("localitate")
    if every_locality:
        named = locality and record.get("loc_siruta") != record.get("uat_siruta")
    else:
        named = locality and locality != record.get("uat")
    if named:
        parts.append(f"loc. {locality}")
    parts.extend(text for _element, text in record.get("adresa_parts") or [])
    return ", ".join(parts)


def finalize(record: dict, which: str):
    """Add the columns that come from SIRUTA and the code tables."""
    if which == "imobile":
        record["uat"] = siruta.uat_name(record.get("uat_siruta"))
        record["judet"] = siruta.county_name(record.get("uat_siruta"))
        record["localitate"] = siruta.locality_name(record.get("loc_siruta"))
        record["adresa"] = address_text(record)
    else:
        record["destinatie_text"] = codes.label(codes.BUILDINGDESTINATION,
                                                record.get("destinatie", ""))


@dataclass
class ReadResult:
    # the .cgxml files found and those read; an archive or folder that cannot be listed is
    # only among the errors
    file_count: int = 0
    read_count: int = 0
    imobile: list = field(default_factory=list)
    constructii: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # (file, message), unreadable files
    warnings: list = field(default_factory=list)   # (file, message), read with remarks


def read_all(paths, recursive=True, stop=_never, progress=None) -> ReadResult:
    """Read and finalize every record of the sources. progress receives 0 to 1."""
    siruta.retry()
    sources = cgxml_sources.list_sources(paths, recursive)
    result = ReadResult(file_count=sum(
        1 for source in sources if not isinstance(source, cgxml_sources.Unreadable)))
    for source, parsed, problem in cgxml_sources.read_each(sources, stop, progress):
        if problem is not None:
            result.errors.append((source[0], problem))
            continue
        result.read_count += 1
        imobile, constructii, warnings = parsed
        result.warnings.extend((source[0], w) for w in warnings)
        result.imobile.extend(imobile)
        result.constructii.extend(constructii)
    for record in result.imobile:
        finalize(record, "imobile")
    for record in result.constructii:
        finalize(record, "constructii")

    # the same imobil in two files, e.g. two deliveries in one folder, puts its polygon in the
    # output twice. An imobil is named by its ID; the IE only when there is no ID, since the
    # imobile split from one old imobil all carry its IE. Both are unique only within a UAT,
    # so two UATs converted together share numbers without sharing an imobil.
    for label, key in (("ID", "id"), ("IE", "ie")):
        files_of = defaultdict(list)
        for r in result.imobile:
            if r.get(key) is not None and (key == "id" or r.get("id") is None):
                files_of[(r.get("uat_siruta") or "", r[key])].append(r["fisier"])
        for (_uat, value), files in sorted(files_of.items()):
            if len(files) > 1:
                message = (f"{label} {value} apare în "
                           f"{fmt.count(len(files), 'fișier', 'fișiere')}")
                result.warnings.extend((f, message) for f in files)
    return result


def nothing_read(errors) -> str:
    """The reason a run whose sources hold no readable file writes nothing, with the first
    problems."""
    shown = 10
    lines = [f"{name}: {problem}" for name, problem in errors[:shown]]
    if len(errors) > shown:
        lines.append("... și încă " + fmt.count(len(errors) - shown, "fișier", "fișiere"))
    return ("Niciun fișier nu a putut fi citit, deci nu s-a scris niciun rezultat:\n\n"
            + "\n".join(lines))


def unmapped_notes(which: str, records) -> list:
    """Sentences about the records that a map output leaves out, for the result window and
    Processing: those without coordinates in their file, and those whose points build no
    geometry (the reason of each is among the problems)."""
    without = sum(1 for r in records if not r.get("rings") and not r.get("_has_points"))
    unbuilt = sum(1 for r in records if not r.get("rings") and r.get("_has_points"))
    nouns = ("imobil", "imobile") if which == "imobile" else ("construcție", "construcții")
    notes = []
    if without:
        notes.append(fmt.plural(
            without, "{0} nu are coordonate în fișier și nu apare în rezultat.",
            "{0} nu au coordonate în fișier și nu apar în rezultat.",
        ).format(fmt.count(without, *nouns)))
    if unbuilt:
        notes.append(fmt.plural(
            unbuilt,
            "{0} are puncte din care nu s-a putut construi geometria și nu apare în rezultat.",
            "{0} au puncte din care nu s-a putut construi geometria și nu apar în rezultat.",
        ).format(fmt.count(unbuilt, *nouns)))
    return notes


def truncated_note(count: int) -> str:
    """The sentence for the texts cut to the Shapefile limit."""
    return fmt.plural(
        count,
        "{0} a fost scurtată la 254 de octeți, limita formatului Shapefile (o literă cu "
        "diacritice ocupă doi octeți).",
        "{0} au fost scurtate la 254 de octeți, limita formatului Shapefile (o literă cu "
        "diacritice ocupă doi octeți).",
    ).format(fmt.count(count, "valoare", "valori"))


def unwritten_note(layer_name: str, missing: int, offered: int, error: str) -> str:
    """The sentence for features the writer refused, with the writer's own error."""
    note = fmt.plural(
        missing, "{0} din {1} nu a putut fi scris în layerul {2}.",
        "{0} din {1} nu au putut fi scrise în layerul {2}.",
    ).format(fmt.count(missing, "element", "elemente"), fmt.number(offered, 0),
             f"'{layer_name}'")
    return f"{note} {error}" if error else note


def build_geometry(parts):
    """A MultiPolygon from organize_rings() parts, None without parts."""
    if not parts:
        return None
    multi = QgsMultiPolygon()
    for part in parts:
        polygon = QgsPolygon()
        for i, ring in enumerate(part):
            closed = list(ring) + [ring[0]]
            line = QgsLineString([p[0] for p in closed], [p[1] for p in closed])
            if i == 0:
                polygon.setExteriorRing(line)
            else:
                polygon.addInteriorRing(line)
        multi.addGeometry(polygon)
    return QgsGeometry(multi)


def truncate_utf8(text: str, limit: int) -> str:
    """Cut text to at most limit UTF-8 bytes. dBase limits bytes, and a Romanian diacritic
    takes two."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _blank_to_null(value):
    # a missing CGXML value is NULL, not an empty string
    return None if value == "" else value


def output_fields(which, layout, shapefile=False):
    """(QgsFields, [(key, output name)]) for a column layout."""
    names = cgxml_columns.output_names(layout, shapefile)
    fields = QgsFields()
    for key, name in names:
        kind = cgxml_columns.kind_of(which, key)
        if kind == "int":
            # dBase reads a number wider than 18 digits back as a real, and GDAL's default
            # width for a 64-bit integer is 20; a Processing output can be a Shapefile too
            fields.append(QgsField(name, FIELD_LONGLONG, "integer64", 18))
        elif kind == "real":
            fields.append(QgsField(name, FIELD_DOUBLE, "double", 20, 2))
        else:
            fields.append(QgsField(name, FIELD_STRING, "string",
                                   cgxml_columns.SHP_TEXT_LIMIT if shapefile else 0))
    return fields, names


def record_values(record, names, shapefile=False):
    """(attribute values in layout order, count of texts cut to the Shapefile limit)."""
    values = []
    truncated = 0
    for key, _name in names:
        value = _blank_to_null(record.get(key))
        if shapefile and isinstance(value, str):
            cut = truncate_utf8(value, cgxml_columns.SHP_TEXT_LIMIT)
            if cut != value:
                value = cut
                truncated += 1
        values.append(value)
    return values, truncated


def write_vector(path, driver, table, which, layout, records, append=False, stop=_never,
                 step=None):
    """Write the records with geometry to a GeoPackage table or a Shapefile, calling step()
    after each. Returns (features written, features offered, texts truncated, the writer's
    error for the features it refused). Raises Canceled when stop() turns true."""
    shapefile = driver == "ESRI Shapefile"
    fields, names = output_fields(which, layout, shapefile)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.fileEncoding = "UTF-8"
    if not shapefile:
        options.layerName = table
        if append:
            options.actionOnExistingFile = (
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
    writer = QgsVectorFileWriter.create(
        path, fields, Qgis.WkbType.MultiPolygon, stereo70.crs(),
        QgsCoordinateTransformContext(), options)
    if writer.hasError() != QgsVectorFileWriter.WriterError.NoError:
        message = writer.errorMessage()
        del writer
        raise OSError(None, message, path)

    written = offered = truncated = 0
    error = ""
    try:
        for record in records:
            geometry = record.get("_geometry")
            if geometry is None:
                continue
            if stop():
                raise Canceled()
            feature = QgsFeature(fields)
            feature.setGeometry(geometry)
            values, cut = record_values(record, names, shapefile)
            truncated += cut
            feature.setAttributes(values)
            offered += 1
            if writer.addFeature(feature):
                written += 1
            else:
                error = writer.errorMessage() or error
            if step is not None:
                step()
    finally:
        del writer
    return written, offered, truncated, error


def write_xlsx(path, layouts, layer_names, imobile, constructii, meta, problems):
    """Sumar sheet, one sheet per layer with every record, and Probleme when there are any."""
    wb = xlsx_report.new_workbook()
    summary = xlsx_report.Summary(wb, "Conversie CGXML")
    summary.section("Informații")
    summary.facts(meta)
    summary.section("Rezultat")
    summary.table(
        ["Categorie", "Total", "Cu geometrie", "Fără geometrie"],
        [[layer_names["imobile"], len(imobile),
          sum(1 for r in imobile if r.get("rings")),
          sum(1 for r in imobile if not r.get("rings"))],
         [layer_names["constructii"], len(constructii),
          sum(1 for r in constructii if r.get("rings")),
          sum(1 for r in constructii if not r.get("rings"))]])

    for which, records in (("imobile", imobile), ("constructii", constructii)):
        keys = [key for key, _name in cgxml_columns.output_names(layouts[which], False)]
        headers = [cgxml_columns.label_of(which, key) for key in keys] + ["Are geometrie"]
        rows = [[record.get(key) for key in keys] + ["Da" if record.get("rings") else "Nu"]
                for record in records]
        text_columns = [cgxml_columns.label_of(which, key) for key in keys
                        if cgxml_columns.kind_of(which, key) == "text"]
        xlsx_report.write_table(wb, layer_names[which], headers, rows,
                                text_columns=text_columns)
    if problems:
        xlsx_report.write_table(wb, "Probleme", ["Fișier", "Problemă"],
                                problems, row_colors=[xlsx_report.MIXED] * len(problems))
    xlsx_report.save(wb, path)


def conversion_facts(paths, file_count, error_count):
    return [
        ("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
        ("Sursa", "; ".join(cgxml_sources.split_paths(paths) if isinstance(paths, str)
                            else paths)),
        ("Fișiere CGXML găsite", file_count),
        ("Fișiere necitite", error_count),
        ("Sistem de coordonate", stereo70.NAME),
    ]


def label_lines(record, config):
    lines = []
    for item in config:
        if not item.get("on"):
            continue
        text = format_value(record.get(item["key"]))
        if text:
            lines.append(f"{item.get('prefix', '')}{text}{item.get('suffix', '')}")
    return lines


def _same_file(first, second) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def temporary_path(path) -> str:
    """The name path is written under before it is put in place, in the same folder so that
    os.replace moves it without copying."""
    stem, extension = os.path.splitext(path)
    return f"{stem}.{os.getpid()}.tmp{extension}"


def _aside_path(path) -> str:
    """The name a file of the previous output keeps while the new output is put in place."""
    return f"{path}.{os.getpid()}.old"


# files other programs add beside a Shapefile, each describing the file it was made for:
# indexes (.qix of GDAL and QGIS, .sbn/.sbx of ArcGIS), the projection file of older QGIS
# versions and ArcGIS metadata. QgsVectorFileWriter.deleteShapeFile removes the same ones,
# except the metadata.
_SHAPEFILE_EXTRAS = (".qix", ".sbn", ".sbx", ".idm", ".ind", ".qpj", ".shp.xml")
# SQLite applies a journal it finds beside a GeoPackage to that file
_GEOPACKAGE_JOURNALS = ("-wal", "-shm", "-journal")


def _files_of(path) -> list:
    """Every file an output at path consists of: outputs.parts(path), with the files of
    _SHAPEFILE_EXTRAS for a Shapefile and the SQLite journals for a GeoPackage."""
    stem, extension = os.path.splitext(path)
    if extension.lower() == ".shp":
        return outputs.parts(path) + [stem + extra for extra in _SHAPEFILE_EXTRAS]
    if extension.lower() == ".gpkg":
        return [path] + [path + journal for journal in _GEOPACKAGE_JOURNALS]
    return outputs.parts(path)


def _move(source, target, named):
    """os.replace; when it fails, raises an OSError with named as the file name."""
    try:
        os.replace(source, target)
    except OSError as error:
        raise OSError(error.errno, error.strerror or str(error), named) from error


def _move_back(moves):
    """Undo the (source, target) moves, last first. A file that cannot be moved back keeps
    its new name, which the log names."""
    for source, target in reversed(moves):
        try:
            os.replace(target, source)
        except OSError as error:
            log(f"Fișierul '{target}' nu a putut primi din nou numele '{source}': {error}",
                Qgis.MessageLevel.Warning)


def _put_in_place(written, removed=()):
    """Move each temporary output of written, [(temporary, path)], onto its path and delete
    the outputs at the paths of removed, so that either every output is new or every previous
    output is as it was. The files of the previous outputs are moved aside first, then the new
    ones are moved in; when a move fails, the files already moved go back and the OSError
    names the file that could not be moved. A file of a previous output that the new one
    lacks, such as its saved style or a spatial index, is deleted. A write-protected file of
    a previous output raises PermissionError before anything moves: Windows renames such a
    file but does not delete it."""
    previous = []
    incoming = []
    for temporary, path in written:
        for source, target in zip(_files_of(temporary), _files_of(path)):
            if os.path.exists(source):
                incoming.append((source, target))
            if os.path.exists(target):
                previous.append(target)
    for path in removed:
        previous.extend(part for part in _files_of(path) if os.path.exists(part))
    for part in previous:
        if not os.access(part, os.W_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), part)

    aside = []
    try:
        for part in previous:
            _move(part, _aside_path(part), part)
            aside.append((part, _aside_path(part)))
        moved = []
        try:
            for source, target in incoming:
                _move(source, target, target)
                moved.append((source, target))
        except OSError:
            _move_back(moved)
            raise
    except OSError:
        _move_back(aside)
        raise
    for _part, kept in aside:
        try:
            os.remove(kept)
        except OSError as error:
            log(f"Fișierul '{kept}' al rezultatului anterior nu a putut fi șters: {error}",
                Qgis.MessageLevel.Warning)


@contextmanager
def replacing_all(paths, removed=()):
    """Write paths through temporary files: yields their names, in the order of paths, and,
    when the block ends without an exception, puts them in place together and deletes the
    outputs at the paths of removed (_put_in_place). An exception, Canceled included, keeps
    the previous files and removes the temporary ones; an OSError on a temporary file names
    its path."""
    temporaries = [temporary_path(path) for path in paths]
    try:
        try:
            yield temporaries
        except OSError as error:
            for temporary, path in zip(temporaries, paths):
                parts = outputs.parts(temporary)
                if error.filename and any(_same_file(error.filename, p) for p in parts):
                    raise OSError(error.errno, error.strerror or str(error), path) from error
            raise
        _put_in_place(list(zip(temporaries, paths)), removed)
    finally:
        for temporary in temporaries:
            for part in _files_of(temporary):
                with suppress(OSError):
                    os.remove(part)


@contextmanager
def replacing(path):
    """replacing_all() for one output: yields the name of its temporary file."""
    with replacing_all([path]) as (temporary,):
        yield temporary


def percent_steps(report, start, end, total):
    """A function to call once per item of total: it reports progress from start to end,
    only when the whole percent changes, since each report crosses to the main thread."""
    state = {"done": 0, "shown": None}

    def step():
        state["done"] += 1
        percent = int(start + (end - start) * state["done"] / max(total, 1))
        if percent != state["shown"]:
            state["shown"] = percent
            report(float(percent))
    return step


def write_dxf(path, layer_names, label_config, imobile, constructii, stop=_never,
              progress=None):
    """Write the DXF through a temporary file. progress receives 0 to 1. Raises Canceled,
    leaving the previous file, when stop() turns true."""
    from . import layer_style
    groups = []
    # the QGIS style colors, with the nearest AutoCAD index for readers without true color
    for which, records, index in (("imobile", imobile, 6), ("constructii", constructii, 4)):
        config = label_config.get(which, [])
        groups.append((dxf_writer.cad_layer_name(layer_names[which]),
                       (index, layer_style.CADASTRAL_COLORS[which]), records,
                       lambda r, c=config: label_lines(r, c)))
    with replacing(path) as temporary:
        if not dxf_writer.write(temporary, groups, stop, progress):
            raise Canceled()


# --- the task ---

class ConversionTask(CadexTask):
    """The conversion as a background task. Inputs are plain values; the dialog loads the
    result layers on the main thread."""

    def __init__(self, *, input_paths, recursive, output_dir, base_name, fmt,
                 layouts, layer_names, label_config, finished_cb=None):
        super().__init__("CADex: Conversie CGXML", finished_cb)
        self.input_paths = (cgxml_sources.split_paths(input_paths)
                            if isinstance(input_paths, str) else list(input_paths))
        self.recursive = recursive
        self.output_dir = output_dir
        self.base_name = base_name
        self.fmt = fmt
        self.layouts = layouts
        self.layer_names = layer_names
        self.label_config = label_config

        self.file_count = 0
        self.read_count = 0
        self.imobile = []
        self.constructii = []
        self.errors = []        # (file, message), unreadable files
        self.warnings = []      # (file, message), read with remarks
        self.outputs = {}       # "imobile"/"constructii" -> layer URI, "file" -> path
        self.truncated = 0
        self.invalid = 0
        # Romanian sentences on the features a writer refused
        self.write_notes = []

    def output_path(self) -> str:
        """The output file, or the output folder for Shapefile (one file per layer)."""
        if self.fmt == "shp":
            return self.output_dir
        return os.path.join(self.output_dir, self.base_name + EXTENSIONS[self.fmt])

    def shapefile_paths(self):
        return {which: os.path.join(
            self.output_dir,
            f"{self.base_name}_{table_name(self.layer_names[which])}.shp")
            for which in ("imobile", "constructii")}

    def output_paths(self) -> list:
        if self.fmt == "shp":
            paths = list(self.shapefile_paths().values())
        else:
            paths = [self.output_path()]
        # a file that cannot be moved aside, such as a spatial index, is named in the error
        return [part for path in paths for part in _files_of(path)]

    def run(self):
        try:
            read = read_all(self.input_paths, self.recursive, self.stopped,
                            lambda share: self.report(80.0 * share))
            self.file_count, self.read_count = read.file_count, read.read_count
            self.imobile, self.constructii = read.imobile, read.constructii
            self.errors, self.warnings = read.errors, read.warnings
            if self.stopped():
                return False
            if not self.errors and self.file_count == 0:
                self.error_msg = "Nu s-a găsit niciun fișier .cgxml în sursa aleasă."
                return False
            if self.read_count == 0:
                self.error_msg = nothing_read(self.errors)
                return False
            # once the outputs are in place the run is complete, even if canceled just then
            self._write_outputs()
            return True
        except Canceled:
            return False
        except Exception as e:
            return self.failed("Conversie CGXML", e)

    def _records(self, which):
        return self.imobile if which == "imobile" else self.constructii

    def _write_outputs(self):
        if self.fmt in ("gpkg", "shp"):
            for which in ("imobile", "constructii"):
                for record in self._records(which):
                    geometry = build_geometry(record.get("rings"))
                    record["_geometry"] = geometry
                    if geometry is not None and not geometry.isGeosValid():
                        self.invalid += 1
                        name = self.layer_names[which]
                        self.warnings.append(
                            (record["fisier"],
                             f"geometrie invalidă în layerul {name}, "
                             "păstrată așa cum este în fișier"))

        os.makedirs(self.output_dir, exist_ok=True)
        # reading took the progress to 80
        if self.fmt in ("gpkg", "shp"):
            self._write_layers()
        elif self.fmt == "dxf":
            path = self.output_path()
            step = percent_steps(self.report, 80, 99, len(self.imobile) + len(self.constructii))
            write_dxf(path, self.layer_names, self.label_config, self.imobile, self.constructii,
                      self.stopped, lambda _share: step())
            self.outputs["file"] = path
        else:
            if self.stopped():
                raise Canceled()
            path = self.output_path()
            problems = [[f, m] for f, m in self.errors + self.warnings]
            write_xlsx(path, self.layouts, self.layer_names, self.imobile, self.constructii,
                       conversion_facts(self.input_paths, self.file_count, len(self.errors)),
                       problems)
            self.outputs["file"] = path
        self.report(100.0)

    def _write_layers(self):
        """The GeoPackage, or the Shapefiles, written through temporary files and put in place
        together once both layers are complete. Without construcții with coordinates there is
        no construcții layer, and a construcții Shapefile of an earlier run is removed."""
        layers = [w for w in ("imobile", "constructii")
                  if w == "imobile" or any(r.get("_geometry") for r in self._records(w))]
        total = sum(1 for w in layers for r in self._records(w) if r.get("_geometry"))
        step = percent_steps(self.report, 80, 99, total)
        written = {}
        if self.fmt == "gpkg":
            path = self.output_path()
            with replacing(path) as temporary:
                for i, which in enumerate(layers):
                    table = table_name(self.layer_names[which])
                    self._write_layer(temporary, "GPKG", table, which, i > 0, step)
                    written[which] = f"{path}|layername={table}"
                if self.stopped():
                    raise Canceled()
        else:
            shapefiles = self.shapefile_paths()
            removed = [] if "constructii" in layers else [shapefiles["constructii"]]
            with replacing_all([shapefiles[w] for w in layers], removed) as temporaries:
                for which, temporary in zip(layers, temporaries):
                    self._write_layer(temporary, "ESRI Shapefile", "", which, False, step)
                    written[which] = shapefiles[which]
                if self.stopped():
                    raise Canceled()
        self.outputs.update(written)
        self.outputs["file"] = self.output_path()

    def _write_layer(self, path, driver, table, which, append, step):
        written, offered, truncated, error = write_vector(
            path, driver, table, which, self.layouts[which], self._records(which),
            append=append, stop=self.stopped, step=step)
        self.truncated += truncated
        if written < offered:
            self.write_notes.append(
                unwritten_note(self.layer_names[which], offered - written, offered, error))
