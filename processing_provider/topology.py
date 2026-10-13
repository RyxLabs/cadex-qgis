# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verificare Topologică and Topologie Construcții algorithms.

Left empty, the columns of a layer from Conversie CGXML default to the ones the windows
preselect: the imobil ID and sector, and the imobil of each construcție.
"""

from typing import Any, Dict

from qgis.core import (  # type: ignore
    Qgis, QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField, QgsProcessingParameterFileDestination,
    QgsProcessingParameterMultipleLayers, QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

from .base import (
    FIELD_ANY, NUMBER_DOUBLE, SOURCE_FILE, SOURCE_POINT, SOURCE_POLYGON, CadexAlgorithm,
    advanced,
)
from ..services import building_topology_service as bts
from ..services import cgxml_sources
from ..services import layer_style
from ..services import topology_check_service as tcs
from ..services.layer_sources import merged_fields
from ..services.roles import normalize

LINK_WITHOUT_KEY = "Alegeți și coloana din imobile la care trimite 'Imobilul construcției'."


def state_key(state) -> str:
    """Output name of a state's count: "Pierdere peste prag" -> "COUNT_PIERDERE_PESTE_PRAG"."""
    return "COUNT_" + normalize(state).upper()


def converted_column(info, key):
    """The field holding a CGXML column in a layer from Conversie CGXML, None for another
    layer or an empty parameter (info None)."""
    return info.column_of(key) if info is not None else None


class _TopologyBase(CadexAlgorithm):
    GROUP = "verificare"
    TOOL = "check_topology"

    def add_common(self):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "LIMITS", self.tr("Limitele sectoarelor"), [SOURCE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterField(
            "LIMIT_FIELD", self.tr("Coloana sectorului în limite"),
            parentLayerParameterName="LIMITS", type=FIELD_ANY, optional=True))
        for key, label, value, top in (
                ("MIN_AREA", "Suprafață minimă a unei suprapuneri sau a unui gol (mp)",
                 tcs.MIN_AREA, 100.0),
                ("SNAP_PRECISION", "Toleranță de racordare (m)", tcs.SNAP_PRECISION, 0.1),
                ("SNAP_DISTANCE", "Distanță de racordare (m)", tcs.SNAP_DISTANCE, 5.0),
                ("BUILDING_TOLERANCE", "Cât poate ieși un punct al construcției din imobil (m)",
                 tcs.BUILDING_TOLERANCE, 1.0)):
            self.addParameter(advanced(QgsProcessingParameterNumber(
                key, self.tr(label), NUMBER_DOUBLE, value, minValue=0.0, maxValue=top)))
        self.addParameter(advanced(QgsProcessingParameterString(
            "ROADS_SECTOR",
            self.tr("Sectorul drumurilor dintre sectoare (necompletat dacă nu există)"),
            tcs.ROADS, optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT_AREAS", self.tr("Probleme topologice (suprafețe)"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT_POINTS", self.tr("Probleme topologice (puncte)"), SOURCE_POINT))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Raport Excel"), self.tr("Excel (*.xlsx)"),
            optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputNumber("PROBLEMS", self.tr("Probleme găsite")))
        self.addOutput(QgsProcessingOutputNumber("ERRORS", self.tr("Dintre care erori")))

    def common_options(self, parameters, context, feedback) -> Dict[str, Any]:
        snap_precision = self.parameterAsDouble(parameters, "SNAP_PRECISION", context)
        snap_distance = self.parameterAsDouble(parameters, "SNAP_DISTANCE", context)
        if snap_precision >= snap_distance:
            raise QgsProcessingException(self.tr(
                "Toleranța de racordare trebuie să fie mai mică decât distanța de racordare."))
        limits = self.optional_snapshot(parameters, "LIMITS", context, feedback)
        limit_field = None
        if limits is not None:
            limit_field = self.refuse_row_number(
                self.field_name(parameters, "LIMIT_FIELD", context), limits, self.tr(
                    "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate da sectorul "
                    "unei limite. Alegeți coloana cu numărul sectorului sau lăsați câmpul "
                    "necompletat."))
        return dict(
            limits=limits, limit_field=limit_field,
            min_area=self.parameterAsDouble(parameters, "MIN_AREA", context),
            snap_precision=snap_precision, snap_distance=snap_distance,
            building_tolerance=self.parameterAsDouble(parameters, "BUILDING_TOLERANCE", context),
            roads_sector=self.parameterAsString(parameters, "ROADS_SECTOR", context) or None,
            excel_path=self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context) or None)

    def outputs(self, task, parameters, context, feedback):
        fields = tcs.findings_fields()
        results = {"PROBLEMS": len(task.findings),
                   "ERRORS": sum(1 for f in task.findings if f.severity == tcs.ERROR)}
        for key, findings, wkb in (
                ("OUTPUT_AREAS", [f for f in task.findings if f.is_area],
                 Qgis.WkbType.MultiPolygon),
                ("OUTPUT_POINTS", [f for f in task.findings if not f.is_area],
                 Qgis.WkbType.Point)):
            sink, destination = self.sink(parameters, key, context, fields, wkb)
            if sink is None:
                continue
            self.write(sink, tcs.findings_features(findings, fields, wkb), feedback)
            self.style(destination, lambda layer: layer_style.style_categories(
                layer, "tip", [(k, k, tcs.KIND_COLORS[k]) for k in tcs.KINDS]))
            results[key] = destination
        for kind, count in tcs.counts_by_kind(task.findings).items():
            if count:
                feedback.pushInfo(f"{kind}: {count}")
        for name, message in task.read_errors:
            feedback.reportError(f"{name}: {message}")
        for note in task.notes():
            feedback.pushWarning(note)
        for imobil, files in task.duplicates:
            feedback.pushInfo(self.tr("Imobilul {0} apare în: {1}. S-a verificat doar primul "
                                      "fișier.").format(imobil, ", ".join(files)))
        for imobil, files in task.reused_keys:
            feedback.pushInfo(self.tr("Imobilul {0} apare în locuri diferite, în fișierele: {1}. "
                                      "Fiecare fișier s-a verificat ca imobil separat.").format(
                                          imobil, ", ".join(files)))
        self.report_output(task, results, "OUTPUT_XLSX", feedback)
        return results


class TopologyCheckAlgorithm(_TopologyBase):
    NAME = "topologycheck"
    TITLE = "Verificare Topologică (layere)"
    SUMMARY = ("Verifică un layer de imobile și, opțional, unul de construcții: suprapuneri, "
               "goluri, puncte neracordate și construcții ieșite din imobil.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "IMOBILE", self.tr("Imobile"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            "ID_FIELD", self.tr("ID imobil"), parentLayerParameterName="IMOBILE",
            type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(
            "SECTOR_FIELD", self.tr("Sector cadastral"), parentLayerParameterName="IMOBILE",
            type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "CONSTRUCTII", self.tr("Construcții"), [SOURCE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterField(
            "LINK_FIELD", self.tr("Imobilul construcției (coloana din construcții)"),
            parentLayerParameterName="CONSTRUCTII", type=FIELD_ANY, optional=True))
        self.add_common()

    def processAlgorithm(self, parameters, context, feedback):
        imobile = self.snapshot(parameters, "IMOBILE", context, feedback)
        constructii = self.optional_snapshot(parameters, "CONSTRUCTII", context, feedback)
        imobile_info = self.layer_info("IMOBILE")
        buildings_info = self.layer_info("CONSTRUCTII")
        id_field = self.refuse_row_number(
            self.field_name(parameters, "ID_FIELD", context)
            or converted_column(imobile_info, "id"), imobile, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate numi imobilele. "
                "Alegeți coloana cu ID-ul imobilului sau lăsați câmpul necompletat."))
        sector_field = self.refuse_row_number(
            self.field_name(parameters, "SECTOR_FIELD", context)
            or converted_column(imobile_info, "sector"), imobile, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate fi sectorul "
                "cadastral. Alegeți coloana cu sectorul sau lăsați câmpul necompletat."))
        link_field = number_field = None
        if constructii is not None:
            link_field = self.building_link(parameters, "LINK_FIELD", constructii, context)
            if link_field and not id_field:
                raise QgsProcessingException(self.tr(LINK_WITHOUT_KEY))
            if not link_field and id_field:
                link_field = converted_column(buildings_info, "id")
            number_field = converted_column(buildings_info, "nr_constr")
        options = self.common_options(parameters, context, feedback)
        if options["roads_sector"] and not sector_field:
            feedback.pushWarning(self.tr(
                "Sectorul drumurilor '{0}' nu se poate recunoaște fără 'Sector cadastral': "
                "drumurile se verifică la fel ca celelalte imobile, iar suprapunerile cu ele se "
                "raportează ca erori.").format(options["roads_sector"]))
        task = self.run_task(tcs.TopologyCheckTask(
            imobile=[imobile], constructii=[constructii] if constructii else [],
            id_field=id_field, sector_field=sector_field, link_field=link_field,
            number_field=number_field,
            facts=[(self.tr("Layer de imobile"), imobile.name),
                   (self.tr("Layer de construcții"), constructii.name if constructii else "-")],
            **options), feedback)
        return self.outputs(task, parameters, context, feedback)


class TopologyCheckCgxmlAlgorithm(_TopologyBase):
    NAME = "topologycheckcgxml"
    TITLE = "Verificare Topologică (fișiere CGXML)"
    SUMMARY = ("Verifică imobilele și construcțiile din fișiere CGXML: suprapuneri, goluri, "
               "puncte neracordate și construcții ieșite din imobil.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "INPUT", self.tr("Fișiere CGXML, arhive ZIP sau foldere"), SOURCE_FILE))
        self.addParameter(QgsProcessingParameterBoolean(
            "RECURSIVE", self.tr("Include și subfolderele"), True))
        self.add_common()

    def processAlgorithm(self, parameters, context, feedback):
        paths = self.parameterAsFileList(parameters, "INPUT", context)
        recursive = self.parameterAsBoolean(parameters, "RECURSIVE", context)
        try:
            sources = cgxml_sources.list_sources(paths, recursive)
        except Exception as e:
            raise QgsProcessingException(self.tr("Sursa nu poate fi citită: {0}").format(e))
        if not sources:
            raise QgsProcessingException(self.tr("Nu s-a găsit niciun fișier .cgxml în "
                                                 "sursele alese."))
        if all(isinstance(s, cgxml_sources.Unreadable) for s in sources):
            raise QgsProcessingException(self.tr("Sursa aleasă nu poate fi citită: {0}.").format(
                cgxml_sources.unreadable_text(sources)))
        # a model that goes on with the problem count would take 0 for a clean delivery
        task = self.run_task(tcs.TopologyCheckTask(
            cgxml_paths=paths,
            recursive=recursive,
            facts=[(self.tr("Sursă"), "; ".join(paths))],
            stop_if_nothing_read=True,
            **self.common_options(parameters, context, feedback)), feedback)
        return self.outputs(task, parameters, context, feedback)


class BuildingTopologyAlgorithm(CadexAlgorithm):
    NAME = "buildingtopology"
    TITLE = "Topologie Construcții"
    SUMMARY = ("Taie construcțiile care depășesc limita imobilului lor și scrie un layer nou, "
               "cu suprafața pierdută de fiecare construcție.")
    GROUP = "verificare"
    TOOL = "building_topology"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "IMOBILE", self.tr("Imobile"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "CONSTRUCTII", self.tr("Construcții"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            "LINK_FIELD", self.tr("Imobilul construcției (coloana din construcții)"),
            parentLayerParameterName="CONSTRUCTII", type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(
            "KEY_FIELD", self.tr("ID imobil (coloana din imobile)"),
            parentLayerParameterName="IMOBILE", type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            "BUFFER", self.tr("Retragere față de limită (m)"), NUMBER_DOUBLE, 0.002,
            minValue=0.0, maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            "THRESHOLD", self.tr("Prag de verificare (mp)"), NUMBER_DOUBLE, 1.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterField(
            "JOIN_FIELDS", self.tr("Coloane preluate din imobil"),
            parentLayerParameterName="IMOBILE", type=FIELD_ANY, allowMultiple=True,
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", self.tr("Construcții corectate"), SOURCE_POLYGON))
        for state in bts.STATES:
            self.addOutput(QgsProcessingOutputNumber(state_key(state), self.tr(state)))

    def processAlgorithm(self, parameters, context, feedback):
        imobile = self.snapshot(parameters, "IMOBILE", context, feedback)
        constructii = self.snapshot(parameters, "CONSTRUCTII", context, feedback)
        merged = merged_fields([imobile])
        join_fields = [merged.field(name) for name in
                       self.field_names(parameters, "JOIN_FIELDS", context)
                       if merged.indexOf(name) >= 0]
        key_field = self.refuse_row_number(
            self.field_name(parameters, "KEY_FIELD", context)
            or converted_column(self.layer_info("IMOBILE"), "id"), imobile, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate lega "
                "construcțiile de imobile. Alegeți coloana cu ID-ul imobilului."))
        link_field = self.building_link(parameters, "LINK_FIELD", constructii, context)
        if link_field and not key_field:
            raise QgsProcessingException(self.tr(LINK_WITHOUT_KEY))
        if not link_field and key_field:
            link_field = converted_column(self.layer_info("CONSTRUCTII"), "id")
        task = self.run_task(bts.BuildingTopologyTask(
            imobile=[imobile], constructii=[constructii],
            buffer_m=self.parameterAsDouble(parameters, "BUFFER", context),
            threshold=self.parameterAsDouble(parameters, "THRESHOLD", context),
            join_fields=join_fields, link_field=link_field, key_field=key_field), feedback)

        fields, names, _join_names = bts.output_fields(constructii.fields, task.join_fields)
        sink, destination = self.sink(parameters, "OUTPUT", context, fields,
                                      constructii.wkb_type)
        self.write(sink, bts.result_features(task.results[0], fields, task.join_fields,
                                             constructii.wkb_type), feedback)
        state_field = names["stare"]
        self.style(destination, lambda layer: layer_style.style_categories(
            layer, state_field, [(s, s, bts.STATE_COLORS[s]) for s in bts.STATES]))
        results = {"OUTPUT": destination}
        for state in bts.STATES:
            results[state_key(state)] = task.counts[state]
        return results
