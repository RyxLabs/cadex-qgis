# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diminuare Suprafețe: scales deed areas so they add up to the measured contour area.

Parcels with a CF/IE keep their area:

    A = sum of the deed areas, B = sum over the CF/IE parcels, C = measured contour area
    factor = (C - B) / (A - B), new area = deed area x factor for every other parcel
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from . import fmt
from .roles import fold

_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^[+-]?\d{1,3}(,\d{3})+$")
_NUMERIC_TEXT = re.compile(r"^[+-]?[\d.,]+$")
# a row that sums others up, matched on folded text: "TOTAL", "Subtotal tarla 12", "Sumă"
_TOTAL_WORD = re.compile(r"\b(?:sub)?total(?:uri)?\b|\bsuma\b")
# CF/IE cell texts that mean no CF/IE, folded and in upper case
_NO_CF_IE = ("", "NULL", "NONE", "-", "NU", "NO", "FALSE", "FARA")
# row numbers listed in a note before "..."
LISTED_ROWS = 15


def _number_text(value) -> str:
    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    return re.sub(r"(?i)(mp|m2|m²)$", "", text)


def decimal_mark(values):
    """"," or "." when the text cells of a column show their decimal separator, None when
    none does or they disagree. "1250.5" and "1,234.5" show "."; "2,5" and "1.234,5" show ","."""
    marks = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = _number_text(value)
        if not _NUMERIC_TEXT.match(text):
            continue
        if "," in text and "." in text:
            marks.add("," if text.rfind(",") > text.rfind(".") else ".")
        elif "," in text and not _THOUSANDS_COMMA.match(text):
            marks.add(",")
        elif "." in text and not _THOUSANDS.match(text):
            marks.add(".")
    return marks.pop() if len(marks) == 1 else None


def parse_column(values):
    """parse_number for the cells of one column, with the decimal separator the column
    shows, so "125.375" next to "1250.5" is 125.375 and next to "2,5" is 125375."""
    mark = decimal_mark(values)
    return [parse_number(value, mark) for value in values]


def parse_number(value, decimal=None):
    """A float from a cell, None when it holds no number. Text may use Romanian or English
    separators: "1234,56", "1.234,56", "1234.56". decimal is the column's separator when
    known (see decimal_mark); without it "12.000" is twelve thousand, as in a Romanian
    sheet."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    text = _number_text(value)
    if not text or text.upper() in ("NULL", "NONE", "-"):
        return None
    if decimal == ".":
        text = text.replace(",", "")
    elif decimal == ",":
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." in text:
        # the last separator is the decimal one
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif _THOUSANDS.match(text):
        # "12.000" is twelve thousand in a Romanian sheet
        text = text.replace(".", "")
    if not _NUMBER.match(text):
        return None
    return float(text)


def has_value(value) -> bool:
    """True when a CF/IE cell is filled in. Empty, 0 in any notation ("0", "0,00"), NU, NO,
    FALSE and FĂRĂ count as no CF/IE."""
    if value is None:
        return False
    if isinstance(value, (int, float)):
        # an .xlsx gives numbers back as float; NaN is not equal to itself
        return value == value and value != 0
    text = str(value).strip()
    if fold(text).upper() in _NO_CF_IE:
        return False
    return parse_number(text) != 0


def cell_text(value) -> str:
    """A cell as the table holds it: the .xlsx reader gives 50124 back as 50124.0."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Da" if value else "Nu"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else fmt.number(value, 6, thousands=False)
    return str(value)


def row_list(numbers) -> str:
    """Row numbers for a note: "5, 9, 12", the first LISTED_ROWS of them."""
    shown = ", ".join(str(n) for n in numbers[:LISTED_ROWS])
    return shown + ("..." if len(numbers) > LISTED_ROWS else "")


def round_half_up(value, decimals=0) -> float:
    """value rounded to decimals with halves away from zero, as areas are rounded by hand:
    2500.5 is 2501, where round() gives the even 2500."""
    step = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(float(value))).quantize(step, rounding=ROUND_HALF_UP))


def distribute(values, total, decimals):
    """values rounded to decimals so they add up to total, itself rounded half up: each value
    is rounded down, and the units still missing go to the largest remainders."""
    step = 10 ** decimals
    # the rounded total times step is a whole number up to float noise
    target = round(round_half_up(total, decimals) * step)
    scaled = [v * step for v in values]
    floors = [math.floor(s) for s in scaled]
    short = target - sum(floors)
    order = sorted(range(len(values)), key=lambda i: scaled[i] - floors[i],
                   reverse=True)
    if short >= 0:
        for i in order[:short]:
            floors[i] += 1
    else:
        # total below the sum of the floors: subtract one unit from the smallest remainders
        for i in order[short:]:
            floors[i] -= 1
    return [f / step for f in floors]


@dataclass
class Result:
    new_areas: list = field(default_factory=list)   # None for skipped rows
    total_deed: float = 0.0     # A
    total_fixed: float = 0.0    # B
    contour: float = 0.0        # C
    factor: float = 1.0
    reduction_pct: float = 0.0
    fixed_rows: list = field(default_factory=list)    # indexes of CF/IE rows
    bad_rows: list = field(default_factory=list)      # indexes without a usable area
    negative_rows: list = field(default_factory=list)  # the bad rows with an area below 0
    left_out: list = field(default_factory=list)      # indexes of total rows
    total_new: float = 0.0      # new areas and CF/IE areas together
    error: str = ""


def compute(areas, fixed_flags, contour, decimals=2, left_out=()) -> Result:
    """The reduction for the parsed areas (None when unreadable) and CF/IE flags. A row with
    a negative area is a bad row, like one without a number, and is also listed in
    negative_rows; the rows in left_out, such as total rows, take no part. Result.error is a
    Romanian sentence for the user, empty on success."""
    result = Result(contour=contour)
    result.left_out = sorted(set(left_out))
    skipped = set(result.left_out)
    result.negative_rows = [i for i, a in enumerate(areas)
                            if i not in skipped and a is not None and a < 0]
    areas = [None if i in skipped or a is None or a < 0 else a for i, a in enumerate(areas)]
    result.bad_rows = [i for i, a in enumerate(areas) if a is None and i not in skipped]
    result.fixed_rows = [i for i, flag in enumerate(fixed_flags)
                         if flag and areas[i] is not None]
    readable = [i for i, a in enumerate(areas) if a is not None]
    result.total_deed = sum(areas[i] for i in readable)
    result.total_fixed = sum(areas[i] for i in result.fixed_rows)
    free = result.total_deed - result.total_fixed

    if not readable:
        result.error = "Coloana aleasă nu conține nicio suprafață numerică."
        return result
    if result.total_deed <= 0:
        result.error = "Suma suprafețelor din acte este 0."
        return result
    # below about 1 mp the division turns floating-point noise into huge factors
    if free <= 1.0:
        if len(result.fixed_rows) == len(readable):
            result.error = ("Toate parcelele au CF/IE, deci nu există suprafețe "
                            "de diminuat.")
        else:
            result.error = (f"Parcelele fără CF/IE au împreună {fmt.area(free)}, prea puțin "
                            "pentru diminuare.")
        return result
    if contour < result.total_fixed:
        result.error = (
            f"Suprafața conturului ({fmt.area(contour)}) este mai mică decât "
            f"suma parcelelor cu CF/IE ({fmt.area(result.total_fixed)}), care "
            "nu se modifică. Verificați conturul sau coloana CF/IE.")
        return result
    # the parcels without CF/IE share what the CF/IE parcels leave of the rounded contour
    target = round_half_up(contour, decimals) - result.total_fixed
    if round_half_up(target, decimals) <= 0:
        result.error = (
            f"Suprafața conturului ({fmt.area(contour)}) nu depășește, la rotunjirea aleasă, "
            f"suma parcelelor cu CF/IE ({fmt.area(result.total_fixed)}), care nu se modifică, "
            "deci parcelelor fără CF/IE nu le rămâne nicio suprafață. Verificați conturul sau "
            "coloana CF/IE.")
        return result

    result.factor = (contour - result.total_fixed) / free
    result.reduction_pct = (1.0 - result.factor) * 100.0

    fixed = set(result.fixed_rows)
    free_rows = [i for i in readable if i not in fixed]
    scaled = distribute([areas[i] * result.factor for i in free_rows], target, decimals)
    new_areas = [None] * len(areas)
    for i in result.fixed_rows:
        new_areas[i] = areas[i]
    for i, value in zip(free_rows, scaled):
        new_areas[i] = value
    result.new_areas = new_areas
    result.total_new = round(sum(a for a in new_areas if a is not None), 6)
    return result


def contour_area(geometries, crs, extent=None) -> float:
    """Total area of the geometries in Stereo 70 square metres."""
    # imported here so the rest of the module loads without QGIS
    from qgis.core import QgsGeometry  # type: ignore
    from . import stereo70
    transform, _state = stereo70.transform_for(crs, extent)
    total = 0.0
    for geometry in geometries:
        if geometry is None or geometry.isEmpty():
            continue
        if transform is not None:
            geometry = QgsGeometry(geometry)
            geometry.transform(transform)
        total += geometry.area()
    return total


def _filled(values) -> int:
    return sum(1 for v in values if v not in (None, ""))


def total_rows(rows) -> list:
    """The indexes of the rows that sum others up: a text cell holding the word total,
    subtotal, totaluri or sumă, in any case, with or without diacritics."""
    return [i for i, row in enumerate(rows)
            if any(isinstance(v, str) and _TOTAL_WORD.search(fold(v)) for v in row)]


class Table(NamedTuple):
    names: list           # the sheets of the file
    header: list          # column names, unique
    rows: list
    numbers: list         # the Excel row number of each row
    header_number: int    # the Excel row number of the header
    hidden: list          # the Excel row numbers of the filled rows hidden in Excel


def _column_names(values) -> list:
    """The header cells as column names: an empty cell is "Coloana N", and a name used by
    several columns gets each one's letter, "Suprafata (C)" and "Suprafata (E)"."""
    from .xlsx import column_letter
    names = [str(v).strip() if v not in (None, "") else f"Coloana {i + 1}"
             for i, v in enumerate(values)]
    counts = Counter(names)
    return [f"{name} ({column_letter(i + 1)})" if counts[name] > 1 else name
            for i, name in enumerate(names)]


def read_sheet(path, sheet_name=None) -> Table:
    """The parcel table of an .xlsx or .csv.

    The header is the first row with more than half as many filled cells as the fullest row,
    so a title above the table is skipped. Rows hidden in Excel, by a filter or by hand, are
    left out. Formulas give their last computed value.
    """
    from . import xlsx
    names, raw, hidden_numbers = xlsx.read_table(path, sheet_name)
    concealed = set(hidden_numbers)
    widest = max((_filled(values) for number, values in enumerate(raw, start=1)
                  if number not in concealed), default=0)
    header, header_number = None, 0
    rows, numbers, hidden = [], [], []
    for number, values in enumerate(raw, start=1):
        filled = _filled(values)
        if number in concealed:
            if header is not None and filled:
                hidden.append(number)
            continue
        if header is None:
            if filled and filled > widest // 2:
                header = _column_names(values)
                header_number = number
            continue
        if not filled:
            continue
        rows.append(values)
        numbers.append(number)
    width = len(header or [])
    rows = [(list(r) + [None] * width)[:width] for r in rows]
    return Table(names, header or [], rows, numbers, header_number, hidden)


def write_excel(path, header, rows, result, meta, area_col, fixed_col):
    """The source table with the reduced area and the difference, and a Sumar sheet."""
    from . import xlsx_report

    new_header = list(header) + ["Suprafață diminuată (mp)", "Diferență (mp)"]
    out_rows, colors = [], []
    fixed = set(result.fixed_rows)
    bad = set(result.bad_rows) | set(result.left_out)
    olds = parse_column([row[area_col] for row in rows])
    for i, row in enumerate(rows):
        new = result.new_areas[i] if i < len(result.new_areas) else None
        old = olds[i]
        diff = round(new - old, 4) if new is not None and old is not None else None
        values = list(row)
        if fixed_col is not None and values[fixed_col] is not None:
            # written as text, as the table holds it
            values[fixed_col] = cell_text(values[fixed_col])
        out_rows.append(values + [new, diff])
        colors.append(xlsx_report.BAD if i in bad else
                      xlsx_report.NOTE if i in fixed else None)
    wb = xlsx_report.new_workbook()
    summary = xlsx_report.Summary(wb, "Diminuare Suprafețe")
    summary.section("Informații")
    summary.facts(meta)
    summary.section("Calcul")
    summary.table(
        ["Mărime", "Valoare", "", "Explicație"],
        [["A", round(result.total_deed, 4), "", "Suma suprafețelor din acte"],
         ["B", round(result.total_fixed, 4), "",
          "Suma suprafețelor parcelelor cu CF/IE, care nu se modifică"],
         ["C", round(result.contour, 4), "", "Suprafața măsurată a conturului"],
         ["Factor", round(result.factor, 8), "", "(C - B) / (A - B)"],
         ["Diminuare", xlsx_report.percent(result.reduction_pct, 4), "",
          "Cât pierde fiecare parcelă fără CF/IE; negativ înseamnă majorare"],
         ["Suma nouă", round(result.total_new, 4), "",
          "Suma suprafețelor diminuate și a parcelelor cu CF/IE"]],
        number_format="General")
    summary.note("Suprafețele noi sunt rotunjite astfel încât suma lor, împreună cu parcelele "
                 "cu CF/IE, să fie egală cu suprafața conturului rotunjită la aceeași precizie. "
                 "Dacă parcelele cu CF/IE au mai multe zecimale decât rotunjirea, suma poate "
                 "diferi cu mai puțin de o unitate de rotunjire.")
    xlsx_report.write_table(wb, "Diminuare", new_header, out_rows, row_colors=colors,
                            text_columns=[header[fixed_col]] if fixed_col is not None else ())
    xlsx_report.save(wb, path)
