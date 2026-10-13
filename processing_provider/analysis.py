# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Raport Intravilan / Extravilan and Diminuare Suprafețe algorithms."""

from datetime import datetime

from qgis.core import (  # type: ignore
    Qgis, QgsFeature, QgsFeatureRequest, QgsField, QgsFields, QgsGeometry, QgsProcessingException,
    QgsProcessingFeatureSourceDefinition, QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField, QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber, QgsProviderRegistry,
)

from .base import FIELD_ANY, NUMBER_DOUBLE, SOURCE_POLYGON, SOURCE_VECTOR, CadexAlgorithm
from ..qt_compat import FIELD_DOUBLE, is_null
from ..services import diminuare_service, fmt, intravilan_service, layer_style, stereo70, xlsx
from ..services.layer_sources import SKIP_VALIDITY_CHECKS, unique_name
from ..services.tasks import CadexTask


class IntravilanAlgorithm(CadexAlgorithm):
    NAME = "intravilanreport"
    TITLE = "Raport Intravilan / Extravilan"
    SUMMARY = ("Calculează pentru fiecare imobil suprafața din intravilan și din extravilan și "
               "îl încadrează integral în intravilan, parțial în intravilan sau integral în "
               "extravilan.")
    GROUP = "analiza"
    TOOL = "intravilan_report"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "IMOBILE", self.tr("Imobile"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "INTRAVILAN", self.tr("Contur intravilan"), [SOURCE_POLYGON]))
        self.addParameter(QgsProcessingParameterField(
            "ID_FIELD", self.tr("ID imobil"), parentLayerParameterName="IMOBILE",
            type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(
            "OWNER_FIELD", self.tr("Proprietar, pentru statisticile de proprietari"),
            parentLayerParameterName="IMOBILE", type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            "TOLERANCE", self.tr("Toleranță (mp)"), NUMBER_DOUBLE, 0.5, minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            "ONLY_INTEGRAL", self.tr("Doar imobilele integral în intravilan"), False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", self.tr("Raport intravilan"), SOURCE_POLYGON))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Raport Excel"), self.tr("Excel (*.xlsx)"),
            optional=True, createByDefault=False))
        for status, key in ((intravilan_service.INTEGRAL_INTRA, "INTRAVILAN_COUNT"),
                            (intravilan_service.PARTIAL, "PARTIAL_COUNT"),
                            (intravilan_service.INTEGRAL_EXTRA, "EXTRAVILAN_COUNT")):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(status)))

    def processAlgorithm(self, parameters, context, feedback):
        imobile = self.snapshot(parameters, "IMOBILE", context, feedback)
        intravilan = self.snapshot(parameters, "INTRAVILAN", context, feedback)
        id_field = self.refuse_row_number(
            self.field_name(parameters, "ID_FIELD", context), imobile, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu identifică imobilele. "
                "Alegeți coloana cu ID-ul imobilului sau lăsați câmpul necompletat."))
        owner_field = self.refuse_row_number(
            self.field_name(parameters, "OWNER_FIELD", context), imobile, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu conține "
                "proprietarii. Alegeți coloana cu proprietarul sau lăsați câmpul necompletat."))
        task = self.run_task(intravilan_service.IntravilanTask(
            imobile=[imobile], intravilan=[intravilan],
            excel_fields=imobile.data_field_names(), id_field=id_field,
            owner_field=owner_field,
            tolerance=self.parameterAsDouble(parameters, "TOLERANCE", context),
            excel_path=self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context) or None,
            only_integral=self.parameterAsBoolean(parameters, "ONLY_INTEGRAL", context)),
            feedback)
        sink, destination = self.sink(parameters, "OUTPUT", context, task.output_fields,
                                      intravilan_service.WKB_TYPE)
        self.write(sink, task.output_features(), feedback)
        field = task.status_field
        self.style(destination, lambda layer: layer_style.style_categories(
            layer, field, [(s, s, intravilan_service.STATUS_COLORS[s])
                           for s in intravilan_service.STATUSES]))
        if task.skipped:
            feedback.pushWarning(fmt.plural(
                task.skipped, self.tr("{0} fără geometrie sau cu suprafața 0 a fost ignorat."),
                self.tr("{0} fără geometrie sau cu suprafața 0 au fost ignorate.")).format(
                    fmt.count(task.skipped, self.tr("imobil"), self.tr("imobile"))))
        results = {"OUTPUT": destination}
        self.report_output(task, results, "OUTPUT_XLSX", feedback)
        for status, key in ((intravilan_service.INTEGRAL_INTRA, "INTRAVILAN_COUNT"),
                            (intravilan_service.PARTIAL, "PARTIAL_COUNT"),
                            (intravilan_service.INTEGRAL_EXTRA, "EXTRAVILAN_COUNT")):
            results[key] = sum(1 for r in task.rows if r["situatie"] == status)
        return results


class AreaReductionAlgorithm(CadexAlgorithm):
    NAME = "areareduction"
    TITLE = "Diminuare Suprafețe"
    SUMMARY = ("Reduce proporțional suprafețele din acte ale parcelelor, astfel încât suma lor "
               "să fie egală cu suprafața conturului.")
    GROUP = "analiza"
    TOOL = "area_reduction"
    ROUNDING = (0, 2)

    def __init__(self):
        super().__init__()
        # (path, sheet name, sheet index) of an .xlsx table, set by prepareAlgorithm
        self._sheet = None

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            "TABLE", self.tr("Tabelul cu parcele"), [SOURCE_VECTOR]))
        self.addParameter(QgsProcessingParameterField(
            "AREA_FIELD", self.tr("Coloana suprafeței din acte"),
            parentLayerParameterName="TABLE", type=FIELD_ANY))
        self.addParameter(QgsProcessingParameterField(
            "FIXED_FIELD", self.tr("Coloana CF/IE (parcelele cu valoare își păstrează "
                                   "suprafața)"),
            parentLayerParameterName="TABLE", type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "CONTOUR", self.tr("Layer contur"), [SOURCE_POLYGON], optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            "CONTOUR_AREA",
            self.tr("Suprafața conturului (mp), dacă nu alegeți un layer contur"),
            NUMBER_DOUBLE, 0.0, optional=True, minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            "ROUNDING", self.tr("Rotunjire"),
            [self.tr("la metru pătrat"), self.tr("la două zecimale")], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            "OUTPUT", self.tr("Parcele diminuate"), SOURCE_VECTOR))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Raport Excel"), self.tr("Excel (*.xlsx)"),
            optional=True, createByDefault=False))
        self.addOutput(QgsProcessingOutputNumber("FACTOR", self.tr("Factor")))
        self.addOutput(QgsProcessingOutputNumber("REDUCTION_PCT", self.tr("Diminuare (%)")))

    def prepareAlgorithm(self, parameters, context, feedback):
        # the .xlsx file and sheet the table reads, for the rows hidden in Excel
        self._sheet = None
        value = parameters.get("TABLE")
        if value not in (None, ""):
            if isinstance(value, QgsProcessingFeatureSourceDefinition):
                value = value.source
            layer = self.parameterAsVectorLayer({"TABLE": value}, "TABLE", context)
            if layer is not None and layer.providerType() == "ogr":
                parts = QgsProviderRegistry.instance().decodeUri("ogr", layer.source())
                path = parts.get("path") or ""
                if path.lower().endswith(".xlsx"):
                    self._sheet = (path, parts.get("layerName") or None, parts.get("layerId"))
        return super().prepareAlgorithm(parameters, context, feedback)

    def _hidden_rows(self, feedback) -> set:
        """The Excel numbers of the rows hidden in the table's .xlsx sheet, by a filter or by
        hand. OGR gives each row of a sheet its Excel row number as fid."""
        if not self._sheet:
            return set()
        path, sheet, index = self._sheet
        try:
            if sheet is None and index not in (None, ""):
                names = xlsx.sheet_names(path)
                sheet = names[int(index)] if 0 <= int(index) < len(names) else None
            return set(xlsx.read_table(path, sheet)[2])
        except Exception:
            # OGR could open the table, so a failure here only loses the hidden-row check
            feedback.pushWarning(self.tr(
                "Rândurile ascunse în Excel nu au putut fi citite, deci toate rândurile "
                "tabelului intră în diminuare."))
            return set()

    def _contour(self, parameters, context, feedback):
        """(area, where it came from for the Sumar) of the contour layer, None for none."""
        contour = self.parameterAsSource(parameters, "CONTOUR", context)
        if contour is None:
            return None
        info = self.layer_info("CONTOUR")
        name = info.name if info is not None else self.tr("contur")
        definition = parameters.get("CONTOUR")
        selected = (isinstance(definition, QgsProcessingFeatureSourceDefinition)
                    and definition.selectedFeaturesOnly)
        # read as the window reads it: a self-intersecting contour still has its area
        features = list(contour.getFeatures(QgsFeatureRequest(), SKIP_VALIDITY_CHECKS))
        if selected and not features:
            raise QgsProcessingException(self.tr(
                "Nu este selectat niciun element în layerul '{0}'. Selectați conturul pe hartă "
                "sau debifați la 'Layer contur' opțiunea 'Doar entitățile selectate'.").format(
                    name))
        reason = stereo70.unusable_reason(name, contour.sourceCrs(), contour.sourceExtent())
        if reason:
            raise QgsProcessingException(reason)
        geometries = [f.geometry() for f in features if f.hasGeometry()]
        if len(geometries) > 1 and not selected:
            # every tarla of a sector, added up, would scale the parcels to the whole sector
            raise QgsProcessingException(self.tr(
                "Layerul contur '{0}' are {1}, iar suprafața lor adunată nu este suprafața "
                "unui singur contur. Selectați pe hartă conturul de folosit (sau toate "
                "elementele, pentru suma lor) și bifați la 'Layer contur' opțiunea 'Doar "
                "entitățile selectate'.").format(
                    name, fmt.count(len(geometries), self.tr("element"), self.tr("elemente"))))
        area = diminuare_service.contour_area(geometries, contour.sourceCrs(),
                                              contour.sourceExtent())
        if area <= 0:
            raise QgsProcessingException(self.tr(
                "Geometria conturului din layerul '{0}' are suprafața 0.").format(name))
        feedback.pushInfo(self.tr("Suprafața conturului, din geometrie: {0}.")
                          .format(fmt.area(area)))
        if selected:
            used = fmt.count(len(geometries), "element selectat", "elemente selectate")
        else:
            used = "tot layerul ({0})".format(fmt.count(len(geometries), "element", "elemente"))
        return area, f"{name}, {used}"

    def processAlgorithm(self, parameters, context, feedback):
        table = self.parameterAsSource(parameters, "TABLE", context)
        if table is None:
            raise QgsProcessingException(self.invalidSourceError(parameters, "TABLE"))
        info = self.layer_info("TABLE")
        table_name = info.name if info is not None else table.sourceName()
        area_field = self.field_name(parameters, "AREA_FIELD", context)
        fixed_field = self.field_name(parameters, "FIXED_FIELD", context)
        if info is not None:
            self.refuse_row_number(area_field, info, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu conține suprafețele "
                "din acte. Alegeți coloana cu suprafața din acte."))
            self.refuse_row_number(fixed_field, info, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile, deci fiecare parcelă ar "
                "avea CF/IE. Alegeți coloana CF/IE sau lăsați câmpul necompletat."))
        contour = self._contour(parameters, context, feedback)
        if contour is not None:
            area, origin = contour
        else:
            area, origin = self.parameterAsDouble(parameters, "CONTOUR_AREA", context), ""
        if area <= 0:
            raise QgsProcessingException(self.tr(
                "Alegeți layerul contur sau scrieți suprafața conturului."))

        header = table.fields().names()
        hidden = self._hidden_rows(feedback)
        rows, geometries, fids, left_hidden = [], [], [], []
        # every row counts in the reduction, whatever its geometry; the geometry is only copied
        for feature in table.getFeatures(QgsFeatureRequest(), SKIP_VALIDITY_CHECKS):
            # Qt5 builds give an empty attribute as a NULL QVariant, whose text is 'NULL'
            values = [None if is_null(v) else v for v in feature.attributes()]
            if feature.id() in hidden:
                # as in the window, only a hidden row that holds data is counted
                if any(v not in (None, "") for v in values):
                    left_hidden.append(feature.id())
                continue
            rows.append(values)
            geometries.append(QgsGeometry(feature.geometry()) if feature.hasGeometry() else None)
            fids.append(feature.id())
        if left_hidden:
            feedback.pushWarning(fmt.plural(
                len(left_hidden), self.tr("{0} ascuns în Excel a fost lăsat deoparte: {1}."),
                self.tr("{0} ascunse în Excel au fost lăsate deoparte: {1}.")).format(
                    fmt.count(len(left_hidden), self.tr("rând"), self.tr("rânduri")),
                    diminuare_service.row_list(left_hidden)))
        area_col = header.index(area_field)
        fixed_col = header.index(fixed_field) if fixed_field else None
        areas = diminuare_service.parse_column([r[area_col] for r in rows])
        flags = [diminuare_service.has_value(r[fixed_col]) if fixed_col is not None else False
                 for r in rows]
        decimals = self.ROUNDING[self.parameterAsEnum(parameters, "ROUNDING", context)]
        result = diminuare_service.compute(areas, flags, area, decimals,
                                           diminuare_service.total_rows(rows))
        if result.error:
            raise QgsProcessingException(result.error)
        # the fid, since row numbers depend on the source format
        for i in result.left_out:
            feedback.pushWarning(self.tr("Elementul cu fid {0} este un rând de total sau "
                                         "subtotal și nu intră în diminuare.").format(fids[i]))
        negative = set(result.negative_rows)
        for i in result.bad_rows:
            if i in negative:
                message = self.tr("Elementul cu fid {0} are suprafața negativă și rămâne "
                                  "neschimbat.")
            else:
                message = self.tr("Elementul cu fid {0} nu are o suprafață numerică și rămâne "
                                  "neschimbat.")
            feedback.pushWarning(message.format(fids[i]))

        fields = QgsFields(table.fields())
        new_name = unique_name(fields, "sup_diminuata")
        fields.append(QgsField(new_name, FIELD_DOUBLE, "double", 20, 2))
        fields.append(QgsField(unique_name(fields, "diferenta"), FIELD_DOUBLE, "double", 20, 4))
        # a table without geometry stays one; geometries are written in Stereo 70
        spatial = table.wkbType() not in (Qgis.WkbType.NoGeometry, Qgis.WkbType.Unknown)
        transform = None
        if spatial:
            transform, _state = stereo70.transform_for(table.sourceCrs(), table.sourceExtent(),
                                                       context.transformContext())
        sink, destination = self.sink(parameters, "OUTPUT", context, fields,
                                      table.wkbType() if spatial else Qgis.WkbType.NoGeometry)
        out = []
        for i, values in enumerate(rows):
            feature = QgsFeature(fields)
            if spatial and geometries[i] is not None:
                geometry = geometries[i]
                if transform is not None:
                    geometry.transform(transform)
                feature.setGeometry(geometry)
            new = result.new_areas[i]
            old = areas[i]
            feature.setAttributes(values + [new, round(new - old, 4)
                                            if new is not None and old is not None else None])
            out.append(feature)
        self.write(sink, out, feedback)

        results = {"OUTPUT": destination, "FACTOR": result.factor,
                   "REDUCTION_PCT": result.reduction_pct}
        excel = self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context)
        if excel:
            meta = [("Data", datetime.now().strftime("%d.%m.%Y %H:%M")),
                    ("Tabel", table_name),
                    ("Coloana suprafeței din acte", area_field),
                    ("Coloana CF/IE", fixed_field or "-"),
                    ("Contur", origin or "introdus manual"),
                    ("Rotunjire", "la metru pătrat" if decimals == 0 else "la două zecimale")]
            if left_hidden:
                meta.append(("Rânduri ascunse în Excel, lăsate deoparte",
                             fmt.count(len(left_hidden), "rând", "rânduri") + ": "
                             + diminuare_service.row_list(left_hidden)))
            report = CadexTask("CADex: Diminuare Suprafețe")
            report.excel_path = excel
            report.write_report(lambda: diminuare_service.write_excel(
                excel, header, rows, result, meta, area_col, fixed_col), excel)
            self.report_output(report, results, "OUTPUT_XLSX", feedback)
        return results
