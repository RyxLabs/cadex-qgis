# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conversie CGXML algorithm. Uses the column layout and DXF labels saved by the Conversie
CGXML window."""

import json
import re

from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsProcessingException, QgsProcessingOutputLayerDefinition,
    QgsProcessingOutputNumber, QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination, QgsProcessingParameterMultipleLayers, QgsProperty,
)

from .base import SOURCE_FILE, SOURCE_POLYGON, CadexAlgorithm
from ..i18n import tr as _tr
from ..services import (
    cgxml_columns, cgxml_export, deps, dialog_memory, layer_style, outputs, roles,
    settings_service, siruta,
)
from ..services.tasks import log

_FILE_FORMAT = re.compile(r"\.(shp|gpkg)(?=$|[|'\"\s])", re.IGNORECASE)


def saved_layouts():
    return {which: cgxml_columns.layout_from_json(
        which, settings_service.get(f"cgxml/columns_{which}"))
        for which in ("imobile", "constructii")}


def saved_names():
    return {"imobile": settings_service.get("cgxml/layer_imobile") or _tr("Imobile"),
            "constructii": settings_service.get("cgxml/layer_constructii") or _tr("Construcții")}


def saved_labels():
    """The DXF labels saved by the Conversie CGXML window, else the defaults."""
    try:
        stored = json.loads(dialog_memory.load("CgxmlDialog", "labels", "") or "{}")
    except (TypeError, ValueError):
        stored = {}
    labels = {}
    for which in ("imobile", "constructii"):
        config = stored.get(which) if isinstance(stored, dict) else None
        if not isinstance(config, list):
            config = []
            for key in cgxml_export.DEFAULT_LABELS[which]:
                prefix, suffix = cgxml_export.LABEL_DEFAULTS.get(key, ("", ""))
                config.append({"key": key, "prefix": prefix, "suffix": suffix, "on": True})
        labels[which] = config
    return labels


def destination_text(value, context) -> str:
    """The destination a sink parameter's value names. Not parameterAsOutputLayer: it also
    registers a temporary output for loading under a name the sink does not use, and loading
    it at the end of the run fails."""
    if isinstance(value, QgsProcessingOutputLayerDefinition):
        value = value.sink
    if isinstance(value, QgsProperty):
        text, _ok = value.valueAsString(context.expressionContext(), "")
        return text
    return str(value or "")


def destination_format(destination) -> str:
    """"shp" or "gpkg" for a destination written to such a file, "" for any other, such as a
    temporary layer."""
    match = _FILE_FORMAT.search(str(destination or ""))
    return match.group(1).lower() if match else ""


class ConvertCgxmlAlgorithm(CadexAlgorithm):
    NAME = "cgxmlconvert"
    TITLE = "Conversie CGXML"
    REPORT = False
    SUMMARY = ("Convertește fișiere CGXML, arhive ZIP sau foldere într-un layer de imobile și "
               "unul de construcții, cu tabel Excel și desen DXF opționale.")
    GROUP = "conversie"
    TOOL = "convert_cgxml"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "INPUT", self.tr("Fișiere CGXML, arhive ZIP sau foldere"), SOURCE_FILE))
        self.addParameter(QgsProcessingParameterBoolean(
            "RECURSIVE", self.tr("Include și subfolderele"), defaultValue=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT_IMOBILE", self.tr("Imobile"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT_CONSTRUCTII", self.tr("Construcții"), SOURCE_POLYGON, optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Tabel Excel cu toate înregistrările"),
            self.tr("Excel (*.xlsx)"), optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_DXF", self.tr("Desen DXF cu etichete"), self.tr("DXF (*.dxf)"),
            optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputNumber("FILES", self.tr("Fișiere CGXML găsite")))
        self.addOutput(QgsProcessingOutputNumber("UNREAD", self.tr("Fișiere necitite")))
        self.addOutput(QgsProcessingOutputNumber("IMOBILE", self.tr("Imobile citite")))
        self.addOutput(QgsProcessingOutputNumber("CONSTRUCTII", self.tr("Construcții citite")))

    def _canceled(self):
        return QgsProcessingException(self.tr("Rularea a fost anulată."))

    def _layer_formats(self, parameters, context, layouts):
        """{which: "shp", "gpkg" or ""} of the layer outputs that are set. Raises when the
        saved column layout cannot be written in a destination's format, before anything is
        read."""
        formats = {}
        for which, key, title in (("imobile", "OUTPUT_IMOBILE", self.tr("imobile")),
                                  ("constructii", "OUTPUT_CONSTRUCTII", self.tr("construcții"))):
            destination = destination_text(parameters.get(key), context)
            if not destination:
                continue
            fmt_key = destination_format(destination)
            problems = cgxml_columns.layout_problems(
                layouts[which], fmt_key == "shp", which, geopackage=fmt_key == "gpkg",
                any_name=not fmt_key)
            if problems:
                raise QgsProcessingException(self.tr(
                    "Coloanele pentru {0} nu pot fi scrise: {1} Coloanele se aleg în fila "
                    "Coloane a ferestrei Conversie CGXML.").format(title, " ".join(problems)))
            formats[which] = fmt_key
        return formats

    def processAlgorithm(self, parameters, context, feedback):
        paths = self.parameterAsFileList(parameters, "INPUT", context)
        recursive = self.parameterAsBoolean(parameters, "RECURSIVE", context)
        dxf_path = self.parameterAsFileOutput(parameters, "OUTPUT_DXF", context)
        if dxf_path and not deps.is_installed("ezdxf"):
            raise QgsProcessingException(self.tr(
                "Formatul DXF are nevoie de pachetul Python 'ezdxf'. Instalați-l o dată din "
                "fereastra Conversie CGXML, alegând formatul DXF, sau din OSGeo4W Shell cu "
                "comanda: python -m pip install ezdxf"))
        layouts = saved_layouts()
        formats = self._layer_formats(parameters, context, layouts)
        read = cgxml_export.read_all(paths, recursive, feedback.isCanceled,
                                     lambda share: feedback.setProgress(70.0 * share))
        if feedback.isCanceled():
            raise self._canceled()
        if not read.errors and read.file_count == 0:
            raise QgsProcessingException(self.tr("Nu s-a găsit niciun fișier .cgxml în "
                                                 "sursele alese."))
        if read.read_count == 0:
            raise QgsProcessingException(cgxml_export.nothing_read(read.errors))
        for name, message in read.errors:
            feedback.reportError(f"{name}: {message}")
        for name, message in read.warnings:
            feedback.pushWarning(f"{name}: {message}")
        if siruta.problem():
            feedback.pushWarning(siruta.problem())

        results = {"FILES": read.file_count, "UNREAD": len(read.errors),
                   "IMOBILE": len(read.imobile), "CONSTRUCTII": len(read.constructii)}
        batch = roles.new_batch()
        stem = cgxml_export.source_stem(paths)
        layer_names = saved_names()
        mapped = sum(1 for r in read.imobile + read.constructii if r.get("rings"))
        step = cgxml_export.percent_steps(feedback.setProgress, 70, 85, mapped)
        for which, records, key in (("imobile", read.imobile, "OUTPUT_IMOBILE"),
                                    ("constructii", read.constructii, "OUTPUT_CONSTRUCTII")):
            shapefile = formats.get(which) == "shp"
            fields, names = cgxml_export.output_fields(which, layouts[which], shapefile)
            sink, destination = self.sink(parameters, key, context, fields,
                                          Qgis.WkbType.MultiPolygon)
            if sink is None:
                continue
            truncated = []
            self.write(sink, self._features(which, records, fields, names, shapefile,
                                            truncated, feedback, step), feedback)
            if feedback.isCanceled():
                raise self._canceled()
            for note in cgxml_export.unmapped_notes(which, records):
                feedback.pushWarning(note)
            if truncated:
                feedback.pushWarning(cgxml_export.truncated_note(sum(truncated)))

            def apply(layer, w=which, n=names):
                layer.setName(cgxml_export.display_name(layer_names[w], stem))
                layer_style.style_cadastral(layer, w, layer_style.cadastral_label_parts(w, n))
                roles.mark_batch(layer, batch)
                roles.mark_columns(layer, n)
            self.style(destination, apply)
            results[key] = destination
        feedback.setProgress(85)

        xlsx_path = self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context)
        if xlsx_path:
            self._file_output(lambda: cgxml_export.write_xlsx(
                xlsx_path, layouts, saved_names(), read.imobile, read.constructii,
                cgxml_export.conversion_facts(paths, read.file_count, len(read.errors)),
                [[f, m] for f, m in read.errors + read.warnings]),
                xlsx_path, "OUTPUT_XLSX", results, feedback)
        if dxf_path:
            dxf_step = cgxml_export.percent_steps(
                feedback.setProgress, 85, 99, len(read.imobile) + len(read.constructii))
            try:
                self._file_output(lambda: cgxml_export.write_dxf(
                    dxf_path, saved_names(), saved_labels(), read.imobile, read.constructii,
                    feedback.isCanceled, lambda _share: dxf_step()),
                    dxf_path, "OUTPUT_DXF", results, feedback)
            except cgxml_export.Canceled:
                raise self._canceled() from None
        feedback.setProgress(100)
        return results

    def _features(self, which, records, fields, names, shapefile, truncated, feedback, step):
        """The records with geometry as features; texts cut to the Shapefile limit are counted
        in truncated. Stops when the run is canceled."""
        title = self.tr("Imobile") if which == "imobile" else self.tr("Construcții")
        for record in records:
            geometry = cgxml_export.build_geometry(record.get("rings"))
            if geometry is None:
                continue
            if feedback.isCanceled():
                return
            if not geometry.isGeosValid():
                feedback.pushWarning(self.tr(
                    "{0}: geometrie invalidă în layerul {1}, păstrată așa cum este în "
                    "fișier").format(record["fisier"], title))
            feature = QgsFeature(fields)
            feature.setGeometry(geometry)
            values, cut = cgxml_export.record_values(record, names, shapefile)
            if cut:
                truncated.append(cut)
            feature.setAttributes(values)
            step()
            yield feature

    def _file_output(self, write, path, key, results, feedback):
        """Write an optional file output. One that cannot be written is reported as an error
        that does not stop the run, and is left out of results."""
        try:
            write()
        except OSError as error:
            feedback.reportError(outputs.write_error(path, error), fatalError=False)
            log(f"{path}: {error}", Qgis.MessageLevel.Warning)
            return
        results[key] = path
