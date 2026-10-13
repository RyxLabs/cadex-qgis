# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base class of the CADex Processing algorithms. No widgets: qgis_process loads it
without a GUI."""

import os
from typing import Optional

from qgis.core import (  # type: ignore
    Qgis, QgsFeatureSink, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingFeatureSourceDefinition, QgsProcessingLayerPostProcessorInterface,
    QgsProcessingOutputLayerDefinition, QgsProcessingParameterDefinition,
    QgsProcessingParameterField, QgsProcessingParameterNumber, QgsVectorLayer,
)

from .. import features, help_texts, icons
from ..i18n import tr as _tr
from ..services import fmt, layer_style, roles, stereo70
from ..services.layer_sources import LayerInfo, LayerSnapshot

# QGIS 3.36 and later have the Processing enums on Qgis, 3.34 on QgsProcessing
if hasattr(Qgis, "ProcessingSourceType"):
    SOURCE_FILE = Qgis.ProcessingSourceType.File
    SOURCE_POLYGON = Qgis.ProcessingSourceType.VectorPolygon
    SOURCE_POINT = Qgis.ProcessingSourceType.VectorPoint
    SOURCE_VECTOR = Qgis.ProcessingSourceType.Vector
    NUMBER_DOUBLE = Qgis.ProcessingNumberParameterType.Double
    FIELD_ANY = Qgis.ProcessingFieldParameterDataType.Any
    FLAG_ADVANCED = Qgis.ProcessingParameterFlag.Advanced
else:
    SOURCE_FILE = QgsProcessing.SourceType.TypeFile
    SOURCE_POLYGON = QgsProcessing.SourceType.TypeVectorPolygon
    SOURCE_POINT = QgsProcessing.SourceType.TypeVectorPoint
    SOURCE_VECTOR = QgsProcessing.SourceType.TypeVector
    NUMBER_DOUBLE = QgsProcessingParameterNumber.Type.Double
    FIELD_ANY = QgsProcessingParameterField.DataType.Any
    FLAG_ADVANCED = QgsProcessingParameterDefinition.Flag.FlagAdvanced

FAST_INSERT = QgsFeatureSink.Flag.FastInsert

# the parameter types whose layer prepareAlgorithm reads
LAYER_PARAMETERS = ("source", "vector")

GROUPS = {"conversie": help_texts.PROCESSING["convert_cgxml"][0],
          "comparare": help_texts.PROCESSING["compare_layers"][0],
          "verificare": help_texts.PROCESSING["check_topology"][0],
          "analiza": help_texts.PROCESSING["intravilan_report"][0]}


def advanced(parameter):
    parameter.setFlags(parameter.flags() | FLAG_ADVANCED)
    return parameter


class CadexAlgorithm(QgsProcessingAlgorithm):
    """Subclasses set NAME, TITLE, SUMMARY (toolbox tooltip), GROUP (a GROUPS key) and TOOL
    (the catalog method whose icon they use). Their help is help_texts.ALGORITHMS[NAME]."""

    NAME = ""
    TITLE = ""
    SUMMARY = ""
    GROUP = ""
    TOOL = ""
    # loaded outputs are marked as reports, which the windows never preselect by name
    REPORT = True

    def __init__(self):
        super().__init__()
        self._pending_styles = []
        self._layer_info = {}

    def tr(self, text):
        return _tr(text)

    def createInstance(self):
        return type(self)()

    def name(self):
        return self.NAME

    def displayName(self):
        return self.tr(self.TITLE)

    def shortDescription(self):
        return self.tr(self.SUMMARY)

    def group(self):
        return self.tr(GROUPS[self.GROUP])

    def groupId(self):
        return self.GROUP

    def icon(self):
        feature = next((f for f in features.CATALOG if f.method == self.TOOL), None)
        return icons.icon(feature.icon if feature else "cadex.svg")

    def shortHelpString(self):
        parts = [self.tr(help_texts.ALGORITHMS.get(self.NAME, ""))]
        # the paragraph on coordinate systems describes layer inputs; CGXML files are always
        # in Stereo 70
        if any(d.type() in LAYER_PARAMETERS for d in self.parameterDefinitions()):
            parts.append("<p>" + self.tr(help_texts.STEREO70) + "</p>")
        # the help panel turns every line into a paragraph, and its style sheet gives <b> a
        # fixed dark grey, unreadable on a dark theme; <strong> is bold without that rule
        text = " ".join("".join(parts).split())
        return text.replace("<b>", "<strong>").replace("</b>", "</strong>")

    # --- inputs ---

    def prepareAlgorithm(self, parameters, context, feedback):
        """Read what processAlgorithm needs from each layer parameter into layer_info().
        QGIS calls this on the thread of the context, the main thread, while processAlgorithm
        may run on another thread, where reading a project layer's fields or custom
        properties races with the main thread changing them."""
        self._layer_info = {}
        for definition in self.parameterDefinitions():
            name = definition.name()
            if definition.type() not in LAYER_PARAMETERS or parameters.get(name) in (None, ""):
                continue
            value = parameters[name]
            if isinstance(value, QgsProcessingFeatureSourceDefinition):
                # a source with 'selected features only' ticked; parameterAsVectorLayer reads
                # no layer from the definition itself
                value = value.source
            layer = self.parameterAsVectorLayer({name: value}, name, context)
            if layer is not None:
                self._layer_info[name] = LayerInfo(layer)
        return True

    def layer_info(self, name) -> Optional[LayerInfo]:
        """The LayerInfo of the layer parameter name, read in prepareAlgorithm; None when the
        parameter is empty or is not a layer. processAlgorithm uses it in place of
        parameterAsVectorLayer."""
        return self._layer_info.get(name)

    def snapshot(self, parameters, name, context, feedback) -> LayerSnapshot:
        """A required source as a Stereo 70 snapshot."""
        snapshot = self.optional_snapshot(parameters, name, context, feedback)
        if snapshot is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, name))
        return snapshot

    def optional_snapshot(self, parameters, name, context, feedback) -> Optional[LayerSnapshot]:
        """The source as a Stereo 70 snapshot, None when left empty. Raises for a source that
        does not load or whose coordinates cannot be read as Stereo 70
        (stereo70.unusable_reason)."""
        source = self.parameterAsSource(parameters, name, context)
        if source is None:
            if parameters.get(name) in (None, ""):
                return None
            raise QgsProcessingException(self.invalidSourceError(parameters, name))
        info = self.layer_info(name)
        label = info.name if info is not None else self.parameterDefinition(name).description()
        reason = stereo70.unusable_reason(label, source.sourceCrs(), source.sourceExtent())
        if reason:
            raise QgsProcessingException(reason)
        snapshot = LayerSnapshot.of_source(source, label, context.transformContext(), info)
        for note in stereo70.notes([snapshot]):
            feedback.pushInfo(note)
        return snapshot

    def field_names(self, parameters, name, context):
        return list(self.parameterAsStrings(parameters, name, context))

    def field_name(self, parameters, name, context):
        values = self.field_names(parameters, name, context)
        return values[0] if values else None

    @staticmethod
    def refuse_row_number(field, snapshot, message):
        """field, unless it only numbers the snapshot's rows: every layer has rows 1, 2, 3...,
        so such a column pairs or joins the wrong features. message takes the field and the
        layer name. A wheel turn over an empty field in the parameter panel selects fid, the
        first field."""
        if field and field in snapshot.row_number_fields:
            raise QgsProcessingException(message.format(field, snapshot.name))
        return field

    def building_link(self, parameters, name, snapshot, context):
        """The construcții column naming each building's imobil, None when left empty."""
        return self.refuse_row_number(
            self.field_name(parameters, name, context), snapshot, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate lega o "
                "construcție de imobilul ei. Alegeți coloana cu ID-ul imobilului sau "
                "lăsați câmpul necompletat."))

    # --- running ---

    @staticmethod
    def run_task(task, feedback):
        """Run a CadexTask on the algorithm's thread, reporting to feedback."""
        task.stop_hook = feedback.isCanceled
        task.progress_hook = feedback.setProgress
        ok = task.run()
        if feedback.isCanceled():
            raise QgsProcessingException(_tr("Rularea a fost anulată."))
        if not ok:
            raise QgsProcessingException(task.error_msg or _tr("eroare necunoscută"))
        return task

    @staticmethod
    def report_output(task, results, key, feedback, path=None):
        """Put the task's report file (path, else task.excel_path) in results under key. When
        the task could not write it, report task.report_error as an error that does not stop
        the run, and leave key out."""
        if getattr(task, "report_error", None):
            feedback.reportError(task.report_error, fatalError=False)
            return
        path = path or getattr(task, "excel_path", None)
        if path:
            results[key] = path

    def sink(self, parameters, name, context, fields, wkb_type):
        """(sink, destination id) in Stereo 70, (None, None) for a skipped optional output. A
        file is written in UTF-8: Romanian text has ș and ț, which no Windows code page holds,
        and the Processing settings default to the code page ("System" on QGIS 4 writes no
        .cpg, so the text reads back wrong)."""
        value = parameters.get(name)
        if value not in (None, ""):
            definition = QgsProcessingOutputLayerDefinition(value)
            options = dict(definition.createOptions or {})
            # QGIS uses the fileEncoding of the output's own createOptions before the
            # context's default
            options["fileEncoding"] = "UTF-8"
            definition.createOptions = options
            parameters = dict(parameters, **{name: definition})
        return self.parameterAsSink(parameters, name, context, fields, wkb_type, stereo70.crs())

    @staticmethod
    def write(sink, features_, feedback=None) -> int:
        """Add the features to sink and return how many it took. With feedback, features the
        sink refused are reported as a warning with their number and the provider's error."""
        if sink is None:
            return 0
        offered = count = 0
        for feature in features_:
            offered += 1
            if sink.addFeature(feature, FAST_INSERT):
                count += 1
        if feedback is not None and count < offered:
            missing = offered - count
            message = _tr("{0} din {1} {2} în rezultat.").format(
                fmt.count(missing, _tr("element"), _tr("elemente")), fmt.number(offered, 0),
                fmt.plural(missing, _tr("nu a putut fi scris"), _tr("nu au putut fi scrise")))
            error = sink.lastError()
            feedback.pushWarning(f"{message} {error}" if error else message)
        return count

    def style(self, destination, apply):
        """Queue apply(layer) for when Processing loads the output. Attached only after a
        successful run: a failed run loads no layer and its styler would never be released."""
        if destination:
            self._pending_styles.append((destination, apply))

    def postProcessAlgorithm(self, context, feedback):
        # called on the main thread, only after processAlgorithm succeeded
        for destination, apply in self._pending_styles:
            if os.path.isfile(str(destination).split("|")[0]):
                # the file keeps the style and report marker for any later project, loaded or
                # not; saved before Processing opens the layer it loads, since saving switches
                # a GeoPackage to a write-ahead log and a layer opened earlier then fails to
                # count its features ("unable to open database file"). The style the file
                # held is not loaded: its CADex markers may belong to an earlier result.
                options = QgsVectorLayer.LayerOptions()
                options.loadDefaultStyle = False
                saved = QgsVectorLayer(destination, "", "ogr", options)
                if saved.isValid():
                    style_output(saved, apply, self.REPORT)
                    layer_style.store_with_file(saved)
                del saved
            Styler.attach(context, destination, apply, self.REPORT)
        self._pending_styles = []
        return {}


def style_output(layer, apply, report):
    """Style an output layer with apply(layer) and mark it as a report when report is true."""
    apply(layer)
    if report:
        roles.mark_report(layer)


class Styler(QgsProcessingLayerPostProcessorInterface):
    """Styles an output layer when Processing loads it. QGIS keeps only a pointer, so _alive
    holds the instances until they have run."""

    _alive = []

    def __init__(self, apply, report):
        super().__init__()
        self._apply = apply
        self._report = report

    def postProcessLayer(self, layer, context, feedback):
        if isinstance(layer, QgsVectorLayer):
            style_output(layer, self._apply, self._report)
        if self in Styler._alive:
            Styler._alive.remove(self)

    @classmethod
    def attach(cls, context, destination, apply, report):
        if not destination or not context.willLoadLayerOnCompletion(destination):
            return
        styler = cls(apply, report)
        cls._alive.append(styler)
        context.layerToLoadOnCompletionDetails(destination).setPostProcessor(styler)
