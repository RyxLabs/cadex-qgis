# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Comparator algorithms: two layers, or two sets of CGXML files."""

from qgis.core import (  # type: ignore
    Qgis, QgsProcessingException, QgsProcessingOutputNumber, QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterFileDestination, QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
)

from .base import (
    FIELD_ANY, NUMBER_DOUBLE, SOURCE_FILE, SOURCE_POLYGON, SOURCE_VECTOR, CadexAlgorithm,
    advanced,
)
from .convert import saved_layouts
from ..services import cgxml_compare, compare_service, fmt, layer_style
from ..services.compare_service import (
    ADDED, ADDED_COLOR, CHANGED, CHANGED_COLOR, DELETED, DELETED_COLOR,
)

# the imobil identifier written by Conversie CGXML
KEY_DEFAULT = "id"


def _style(color):
    return lambda layer: layer_style.style_fill(layer, color)


def _style_kinds(field):
    """The changed records by their kind of change, so the legend can hide each kind."""
    return lambda layer: layer_style.style_categories(layer, field,
                                                      compare_service.change_categories())


# the choices of FOLD_TEXT in the CGXML comparison
FOLD_NO, FOLD_YES, FOLD_ETERRA = 0, 1, 2


def add_criteria(algorithm, eterra_fold=False):
    """eterra_fold: FOLD_TEXT is a choice whose default ignores case only between an eTerra
    export and a delivery, whose texts are typed by different people."""
    algorithm.addParameter(QgsProcessingParameterBoolean(
        "COMPARE_GEOMETRY", algorithm.tr("Compară și geometria"), True))
    algorithm.addParameter(QgsProcessingParameterNumber(
        "TOLERANCE", algorithm.tr("Toleranță pentru geometrie (mp)"), NUMBER_DOUBLE, 0.1,
        minValue=0.0))
    if eterra_fold:
        algorithm.addParameter(advanced(QgsProcessingParameterEnum(
            "FOLD_TEXT", algorithm.tr("Ignoră majusculele și diacriticele"),
            [algorithm.tr("Nu"), algorithm.tr("Da"),
             algorithm.tr("Doar la potrivirea după IE")],
            defaultValue=FOLD_ETERRA)))
    else:
        algorithm.addParameter(advanced(QgsProcessingParameterBoolean(
            "FOLD_TEXT", algorithm.tr("Ignoră majusculele și diacriticele"), False)))


class CompareLayersAlgorithm(CadexAlgorithm):
    NAME = "comparelayers"
    TITLE = "Comparator (layere)"
    SUMMARY = ("Compară două layere după o coloană cheie și scrie înregistrările apărute, "
               "dispărute și modificate.")
    GROUP = "comparare"
    TOOL = "compare_layers"

    def initAlgorithm(self, config=None):
        # without a default the key would start on the first field, often fid, which only
        # numbers the rows and pairs the wrong imobile once one is removed
        self.addParameter(QgsProcessingParameterFeatureSource(
            "OLD", self.tr("Versiunea veche"), [SOURCE_VECTOR]))
        self.addParameter(QgsProcessingParameterField(
            "OLD_KEY", self.tr("Coloana cheie în versiunea veche"), defaultValue=KEY_DEFAULT,
            parentLayerParameterName="OLD", type=FIELD_ANY))
        self.addParameter(QgsProcessingParameterFeatureSource(
            "NEW", self.tr("Versiunea nouă"), [SOURCE_VECTOR]))
        self.addParameter(QgsProcessingParameterField(
            "NEW_KEY", self.tr("Coloana cheie în versiunea nouă"), defaultValue=KEY_DEFAULT,
            parentLayerParameterName="NEW", type=FIELD_ANY))
        self.addParameter(QgsProcessingParameterField(
            "FIELDS", self.tr("Coloanele de comparat"), parentLayerParameterName="OLD",
            type=FIELD_ANY, allowMultiple=True, optional=True))
        for side, label in (("OLD", "veche"), ("NEW", "nouă")):
            self.addParameter(QgsProcessingParameterFeatureSource(
                f"{side}_BUILDINGS", self.tr(f"Construcții, versiunea {label}"),
                [SOURCE_POLYGON], optional=True))
            self.addParameter(QgsProcessingParameterField(
                f"{side}_BUILDING_IMOBIL",
                self.tr(f"Imobilul construcției, versiunea {label}"),
                parentLayerParameterName=f"{side}_BUILDINGS", type=FIELD_ANY, optional=True))
            self.addParameter(QgsProcessingParameterField(
                f"{side}_BUILDING_NUMBER",
                self.tr(f"Numărul construcției, versiunea {label}"),
                parentLayerParameterName=f"{side}_BUILDINGS", type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(
            "BUILDING_FIELDS", self.tr("Coloanele construcțiilor de comparat"),
            parentLayerParameterName="OLD_BUILDINGS", type=FIELD_ANY, allowMultiple=True,
            optional=True))
        add_criteria(self)
        for key, label in (("OUTPUT_ADDED", "Apărute"), ("OUTPUT_DELETED", "Dispărute"),
                           ("OUTPUT_CHANGED", "Modificate")):
            self.addParameter(QgsProcessingParameterFeatureSink(
                key, self.tr(label), SOURCE_VECTOR, optional=True))
        for key, label in (("OUTPUT_BUILDINGS_ADDED", "Construcții apărute"),
                           ("OUTPUT_BUILDINGS_DELETED", "Construcții dispărute"),
                           ("OUTPUT_BUILDINGS_CHANGED", "Construcții modificate")):
            self.addParameter(QgsProcessingParameterFeatureSink(
                key, self.tr(label), SOURCE_POLYGON, optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Raport Excel"), self.tr("Excel (*.xlsx)"),
            optional=True, createByDefault=False))
        for key, label in (("ADDED", "Apărute"), ("DELETED", "Dispărute"),
                           ("CHANGED", "Modificate"), ("UNCHANGED", "Nemodificate"),
                           ("BUILDINGS_ADDED", "Construcții apărute"),
                           ("BUILDINGS_DELETED", "Construcții dispărute"),
                           ("BUILDINGS_CHANGED", "Construcții modificate"),
                           ("BUILDINGS_UNCHANGED", "Construcții nemodificate")):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(label)))

    def processAlgorithm(self, parameters, context, feedback):
        old = self.snapshot(parameters, "OLD", context, feedback)
        new = self.snapshot(parameters, "NEW", context, feedback)
        infos = (self.layer_info("OLD"), self.layer_info("NEW"))
        key_old, key_new = self._keys(parameters, context, old, new, infos)
        for snapshot, key in ((old, key_old), (new, key_new)):
            if snapshot.fields.indexOf(key) < 0:
                raise QgsProcessingException(self.tr(
                    "Layerul '{0}' nu are coloana '{1}'. Alegeți coloana cheie.").format(
                        snapshot.name, key))
            self.refuse_row_number(key, snapshot, self.tr(
                "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate potrivi "
                "înregistrările celor două versiuni. Alegeți coloana cheie, de exemplu 'id'."))
        self._eterra_warning(infos, (key_old, key_new), feedback)
        fields = self.field_names(parameters, "FIELDS", context)
        if not fields:
            fields = _common_fields(old, new, (key_old, key_new), cgxml_compare.converted_columns(
                *infos, cgxml_compare.DEFAULT_COLUMNS))
        wants_geometry = self.parameterAsBoolean(parameters, "COMPARE_GEOMETRY", context)
        compare_geometry = wants_geometry and _spatial(old, new)
        if not fields and not compare_geometry:
            raise QgsProcessingException(self.tr(
                "Layerele nu au coloane comune de comparat, iar geometria nu se compară."))
        tolerance = self.parameterAsDouble(parameters, "TOLERANCE", context)
        fold_text = self.parameterAsBoolean(parameters, "FOLD_TEXT", context)
        buildings = self._buildings_task(parameters, context, feedback, infos, wants_geometry,
                                         tolerance, fold_text)
        task = self.run_task(compare_service.CompareTask(
            old=old, new=new, key_old=key_old, key_new=key_new, fields=fields,
            compare_geometry=compare_geometry, tolerance=tolerance,
            excel_path=self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context) or None,
            fold_text=fold_text, buildings=buildings), feedback)

        results = {}
        self._report(task, "", ("OUTPUT_ADDED", "OUTPUT_DELETED", "OUTPUT_CHANGED"),
                     parameters, context, feedback, results)
        if buildings is not None:
            self._report(buildings, "BUILDINGS_",
                         ("OUTPUT_BUILDINGS_ADDED", "OUTPUT_BUILDINGS_DELETED",
                          "OUTPUT_BUILDINGS_CHANGED"),
                         parameters, context, feedback, results)
        self.report_output(task, results, "OUTPUT_XLSX", feedback)
        return results

    def _keys(self, parameters, context, old, new, infos):
        """The key columns chosen. Both left at 'id' for two Conversie CGXML layers, the
        columns the window pairs them on (compare_service.key_columns)."""
        keys = tuple(self.field_name(parameters, name, context) or KEY_DEFAULT
                     for name in ("OLD_KEY", "NEW_KEY"))
        if keys == (KEY_DEFAULT, KEY_DEFAULT) and all(info is not None and info.batch
                                                      for info in infos):
            resolved = compare_service.key_columns(old.source, new.source, *infos)
            if all(resolved):
                return resolved
        return keys

    def _eterra_warning(self, infos, keys, feedback):
        """Advice for a converted eTerra export compared with a delivery."""
        names = tuple(self.tr("Layerul '{0}'").format(info.name) if info is not None else ""
                      for info in infos)
        advice = cgxml_compare.eterra_advice(*infos, names)
        if not advice:
            return
        id_columns = {info.column_of("id") for info in infos} - {None}
        if id_columns & set(keys):
            advice += self.tr(" Potriviți imobilele după 'ie': numerele cadastrale din "
                              "exportul eTerra nu sunt ID-urile din livrare.")
        feedback.pushWarning(self.tr(advice))

    def _buildings_task(self, parameters, context, feedback, infos, wants_geometry, tolerance,
                        fold_text):
        """The CompareTask of the construcții layers, None without them. infos are the
        LayerInfo of the imobile layers."""
        chosen = [bool(parameters.get(f"{side}_BUILDINGS")) for side in ("OLD", "NEW")]
        if not any(chosen):
            return None
        if not all(chosen):
            raise QgsProcessingException(self.tr(
                "Alegeți ambele layere de construcții sau niciunul."))
        # the cadastral numbers of an eTerra export are not the IDs of a delivery
        eterra = bool(cgxml_compare.eterra_advice(*infos))
        snapshots, keys, building_infos = [], [], []
        for side in ("OLD", "NEW"):
            snapshot = self.snapshot(parameters, f"{side}_BUILDINGS", context, feedback)
            snapshots.append(snapshot)
            info = self.layer_info(f"{side}_BUILDINGS")
            building_infos.append(info)
            imobil = self.field_name(parameters, f"{side}_BUILDING_IMOBIL", context)
            if not imobil and info is not None:
                imobil = info.column_of("ie_imobil" if eterra else "id")
                if eterra and not imobil:
                    raise QgsProcessingException(self.tr(
                        "Construcțiile unui export eTerra se potrivesc cu cele ale livrării "
                        "după IE-ul imobilului, pe care layerul '{0}' nu îl are. Alegeți coloana "
                        "imobilului în ambele layere de construcții sau comparați fișierele "
                        "CGXML.").format(info.name))
            number = (self.field_name(parameters, f"{side}_BUILDING_NUMBER", context)
                      or (info.column_of("nr_constr") if info is not None else None))
            if not number:
                raise QgsProcessingException(self.tr(
                    "Alegeți coloana cu numărul construcției pentru ambele layere de "
                    "construcții."))
            for name in (imobil, number):
                self.refuse_row_number(name, snapshot, self.tr(
                    "Coloana '{0}' din '{1}' doar numerotează rândurile și nu poate lega "
                    "construcțiile celor două versiuni. Alegeți coloana imobilului și a "
                    "numărului construcției sau lăsați-le necompletate pentru layerele create "
                    "cu Conversie CGXML."))
            keys.append([k for k in (imobil, number) if k])
        if len(keys[0]) != len(keys[1]):
            raise QgsProcessingException(self.tr(
                "Alegeți coloana imobilului în ambele layere de construcții sau în niciunul."))
        old, new = snapshots
        fields = self.field_names(parameters, "BUILDING_FIELDS", context)
        if not fields:
            fields = _common_fields(old, new, keys[0] + keys[1], cgxml_compare.converted_columns(
                *building_infos, cgxml_compare.BUILDING_COLUMNS))
        return compare_service.CompareTask(
            old=old, new=new, key_old=keys[0], key_new=keys[1], fields=fields,
            compare_geometry=wants_geometry and _spatial(old, new), tolerance=tolerance,
            excel_path=None, fold_text=fold_text)

    def _report(self, task, prefix, sinks, parameters, context, feedback, results):
        """Warnings, counts and output layers of task; prefix starts the count names."""
        lead = self.tr("Construcții: ") if prefix else ""
        missing = task.old_missing + task.new_missing
        if missing:
            feedback.pushWarning(lead + fmt.plural(
                missing, self.tr("{0} fără valoare în coloana cheie a fost ignorat."),
                self.tr("{0} fără valoare în coloana cheie au fost ignorate.")).format(
                    fmt.count(missing, self.tr("rând"), self.tr("rânduri"))))
        for side, dups in ((self.tr("vechi"), task.old_dups), (self.tr("nou"), task.new_dups)):
            for key, count in sorted(dups.items()):
                # "apare de 3 ori", "apare de 20 de ori"
                feedback.pushWarning(lead + self.tr("Cheia {0} apare de {1} în layerul {2}; "
                                                    "s-a comparat primul rând.").format(
                    key, fmt.count(count, self.tr("dată"), self.tr("ori")), side))

        results.update({prefix + "ADDED": len(task.added), prefix + "DELETED": len(task.deleted),
                        prefix + "CHANGED": len(task.changed_keys()),
                        prefix + "UNCHANGED": task.unchanged_count()})
        for key, category, color in zip(sinks, (ADDED, DELETED, CHANGED),
                                        (ADDED_COLOR, DELETED_COLOR, CHANGED_COLOR)):
            result = task.result_features(category)
            sink, destination = self.sink(parameters, key, context, result.fields,
                                          result.wkb_type)
            if sink is None:
                continue
            self.write(sink, result.features, feedback)
            if _has_geometry(result.wkb_type):
                self.style(destination, _style_kinds(result.kind_field) if result.kind_field
                           else _style(color))
            results[key] = destination


def _has_geometry(wkb_type):
    return wkb_type not in (Qgis.WkbType.NoGeometry, Qgis.WkbType.Unknown)


def _spatial(old, new):
    return all(_has_geometry(s.wkb_type) for s in (old, new))


def _common_fields(old, new, keys, chosen=None):
    """The data columns both snapshots have, keys left out; with chosen, only those."""
    new_names = set(new.data_field_names())
    return [n for n in old.data_field_names() if n in new_names and n not in keys
            and (chosen is None or n in chosen)]


def _columns_parameter(name, description, columns, defaults):
    """A choice of CGXML columns kept by their keys, which stay valid in a saved model when
    the catalog gains or loses a column; the help lists each key's label."""
    keys = [key for key, _label in columns]
    parameter = QgsProcessingParameterEnum(
        name, description, keys, allowMultiple=True,
        defaultValue=[key for key in keys if key in defaults], optional=True,
        usesStaticStrings=True)
    parameter.setHelp(", ".join(f"{key}: {label}" for key, label in columns))
    return parameter


class CompareCgxmlAlgorithm(CadexAlgorithm):
    NAME = "comparecgxml"
    TITLE = "Comparator (fișiere CGXML)"
    SUMMARY = ("Compară două seturi de fișiere CGXML, potrivite ca pe cadex.ro, și scrie "
               "imobilele și construcțiile apărute, dispărute și modificate.")
    GROUP = "comparare"
    TOOL = "compare_layers"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "OLD", self.tr("Sursa veche (înainte): fișiere CGXML, arhive ZIP sau foldere"),
            SOURCE_FILE))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "NEW", self.tr("Sursa nouă (după): fișiere CGXML, arhive ZIP sau foldere"),
            SOURCE_FILE))
        self.addParameter(QgsProcessingParameterBoolean(
            "RECURSIVE", self.tr("Include și subfolderele"), True))
        self.addParameter(QgsProcessingParameterEnum(
            "MODE", self.tr("Potrivirea fișierelor"),
            [self.tr(cgxml_compare.MODE_NAMES[m]) for m in cgxml_compare.MODES], defaultValue=0))
        self.addParameter(_columns_parameter(
            "COLUMNS", self.tr("Coloanele imobilului de comparat"),
            cgxml_compare.comparable_columns(), cgxml_compare.DEFAULT_COLUMNS))
        add_criteria(self, eterra_fold=True)
        self.addParameter(QgsProcessingParameterBoolean(
            "BUILDINGS", self.tr("Compară și construcțiile, după numărul lor"), True))
        self.addParameter(_columns_parameter(
            "BUILDING_COLUMNS", self.tr("Coloanele construcției de comparat"),
            cgxml_compare.comparable_building_columns(), cgxml_compare.BUILDING_COLUMNS))
        for key, label in (("OUTPUT_ADDED", cgxml_compare.IMOBILE_ADDED),
                           ("OUTPUT_DELETED", cgxml_compare.IMOBILE_DELETED),
                           ("OUTPUT_CHANGED", cgxml_compare.IMOBILE_CHANGED),
                           ("OUTPUT_BUILDINGS_ADDED", cgxml_compare.BUILDINGS_ADDED),
                           ("OUTPUT_BUILDINGS_DELETED", cgxml_compare.BUILDINGS_DELETED),
                           ("OUTPUT_BUILDINGS_CHANGED", cgxml_compare.BUILDINGS_CHANGED)):
            self.addParameter(QgsProcessingParameterFeatureSink(
                key, self.tr(label), SOURCE_POLYGON, optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_XLSX", self.tr("Raport Excel"), self.tr("Excel (*.xlsx)"),
            optional=True, createByDefault=False))
        for key, label in (("MATCHED", "Imobile în ambele surse"),
                           ("CHANGED", cgxml_compare.IMOBILE_CHANGED),
                           ("UNCHANGED", cgxml_compare.IMOBILE_UNCHANGED),
                           ("ONLY_OLD", cgxml_compare.IMOBILE_DELETED),
                           ("ONLY_NEW", cgxml_compare.IMOBILE_ADDED),
                           ("IGNORED", "Fișiere ignorate"),
                           ("BUILDINGS_ADDED", cgxml_compare.BUILDINGS_ADDED),
                           ("BUILDINGS_DELETED", cgxml_compare.BUILDINGS_DELETED),
                           ("BUILDINGS_CHANGED", cgxml_compare.BUILDINGS_CHANGED),
                           ("BUILDINGS_UNCHANGED", cgxml_compare.BUILDINGS_UNCHANGED)):
            self.addOutput(QgsProcessingOutputNumber(key, self.tr(label)))

    def _chosen_columns(self, parameters, name, context):
        """The column keys chosen; an empty choice is no column, where QGIS would take the
        default."""
        value = parameters.get(name)
        if isinstance(value, (list, tuple)) and not value:
            return []
        return list(self.parameterAsEnumStrings(parameters, name, context))

    def processAlgorithm(self, parameters, context, feedback):
        old_paths = self.parameterAsFileList(parameters, "OLD", context)
        new_paths = self.parameterAsFileList(parameters, "NEW", context)
        if not old_paths or not new_paths:
            raise QgsProcessingException(self.tr("Alegeți ambele surse."))
        columns = self._chosen_columns(parameters, "COLUMNS", context)
        compare_geometry = self.parameterAsBoolean(parameters, "COMPARE_GEOMETRY", context)
        if not columns and not compare_geometry:
            raise QgsProcessingException(self.tr(
                "Alegeți cel puțin o coloană a imobilului de comparat sau comparați și "
                "geometria."))
        mode = cgxml_compare.MODES[self.parameterAsEnum(parameters, "MODE", context)]
        fold = self.parameterAsEnum(parameters, "FOLD_TEXT", context)
        task = self.run_task(cgxml_compare.CgxmlCompareTask(
            old_paths=old_paths, new_paths=new_paths,
            recursive=self.parameterAsBoolean(parameters, "RECURSIVE", context),
            mode=mode, columns=columns, compare_geometry=compare_geometry,
            tolerance=self.parameterAsDouble(parameters, "TOLERANCE", context),
            buildings=self.parameterAsBoolean(parameters, "BUILDINGS", context),
            building_columns=self._chosen_columns(parameters, "BUILDING_COLUMNS", context),
            fold_text=fold == FOLD_YES or (fold == FOLD_ETERRA and mode == cgxml_compare.IE),
            excel_path=self.parameterAsFileOutput(parameters, "OUTPUT_XLSX", context) or None),
            feedback)
        for ignored in task.ignored:
            feedback.pushWarning(f"{ignored.side}, {ignored.file_name}: {ignored.reason}")
        for problem in task.problems:
            feedback.pushWarning(f"{problem.side}, {problem.file_name}: {problem.text}")
        for note in task.notes():
            feedback.pushWarning(note)
        for label, count in task.summary_rows() + task.building_summary_rows():
            feedback.pushInfo(f"{label}: {count}")

        layouts = saved_layouts()
        results = {"MATCHED": task.matched, "CHANGED": len(task.changed),
                   "UNCHANGED": task.unchanged, "ONLY_OLD": len(task.deleted),
                   "ONLY_NEW": len(task.added), "IGNORED": len(task.ignored),
                   "BUILDINGS_ADDED": len(task.buildings_added),
                   "BUILDINGS_DELETED": len(task.buildings_deleted),
                   "BUILDINGS_CHANGED": len(task.buildings_changed),
                   "BUILDINGS_UNCHANGED": task.buildings_unchanged}
        outputs = [("OUTPUT_ADDED", task.added, "imobile", cgxml_compare.IMOBILE_ADDED,
                    ADDED_COLOR, False),
                   ("OUTPUT_DELETED", task.deleted, "imobile", cgxml_compare.IMOBILE_DELETED,
                    DELETED_COLOR, False),
                   ("OUTPUT_CHANGED", task.changed, "imobile", cgxml_compare.IMOBILE_CHANGED,
                    CHANGED_COLOR, True)]
        if task.buildings:
            outputs += [("OUTPUT_BUILDINGS_ADDED", task.buildings_added, "constructii",
                         cgxml_compare.BUILDINGS_ADDED, ADDED_COLOR, False),
                        ("OUTPUT_BUILDINGS_DELETED", task.buildings_deleted, "constructii",
                         cgxml_compare.BUILDINGS_DELETED, DELETED_COLOR, False),
                        ("OUTPUT_BUILDINGS_CHANGED", task.buildings_changed, "constructii",
                         cgxml_compare.BUILDINGS_CHANGED, CHANGED_COLOR, True)]
        left_out = []
        for key, outcomes, which, label, color, with_changes in outputs:
            result = cgxml_compare.result_features(outcomes, which, layouts[which],
                                                   with_changes, task.compare_geometry)
            sink, destination = self.sink(parameters, key, context, result.fields,
                                          result.wkb_type)
            if sink is None:
                continue
            self.write(sink, result.features, feedback)
            left_out.append((self.tr(label), result.left_out))
            self.style(destination, _style_kinds(result.kind_field) if result.kind_field
                       else _style(color))
            results[key] = destination
        self.report_output(task, results, "OUTPUT_XLSX", feedback)
        note = cgxml_compare.left_out_note(left_out, in_report="OUTPUT_XLSX" in results)
        if note:
            feedback.pushWarning(note)
        return results
