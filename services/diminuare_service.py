# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Diminuare Suprafețe: reduces deed areas so they add up to the measured contour area.

Parcels with a CF/IE keep their area:

    A = sum of the deed areas, B = sum over the CF/IE parcels, C = measured contour area
    factor = (C - B) / (A - B), new area = deed area x factor for every other parcel

Areas are never scaled up: with C above A every area stays and the result holds the surplus,
C - A, and rounding gives no parcel more than its deed area.
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
# the word of a row that sums others up, matched on folded text: "TOTAL", "Subtotal tarla 12",
# "Sumă"
_TOTAL_WORD = re.compile(r"\b(?:sub)?total(?:uri)?\b|\bsuma\b")
# every text _TOTAL_WORD matches holds one of these, which is tested before the text is folded
_TOTAL_HINT = re.compile(r"total|sum", re.IGNORECASE)
# what the name of a total row holds besides the word, folded: "Total general", "Subtotal
# tarla nr. 12", "Total T 12", "Total sector cadastral 3", "Total suprafață", "Total parcele"
_NAME_WORDS = frozenset((
    "general", "generala", "tarla", "tarlaua", "t", "sector", "sectorul", "cadastral", "nr",
    "suprafata", "suprafete", "parcele"))
# a number in such a name, alone or with a letter: "12", "T12", "12a"
_NAME_NUMBER = re.compile(r"[a-z]?\d+[a-z]?")
# a unit written in a cell of its own, or in the name of a total row, is no data
_UNITS = frozenset(("mp", "m2", "m²", "ha"))
# why a row counts as a total row: a cell of it is the name of a total row and nothing more;
# or, for the word inside a longer text, its area is the sum of the parcel rows above it, or it
# holds only that text and numbers
TOTAL_BY_WORD = "word"
TOTAL_BY_SUM = "sum"
TOTAL_BY_LABEL = "label"
# how far, in mp, a total may lie from the sum of its rows, which a table can show rounded
SUM_SLACK = 1.0
# CF/IE cell texts that mean no CF/IE, folded and in upper case
_NO_CF_IE = ("", "NULL", "NONE", "NU", "NO", "FALSE", "FARA", "N/A", "NU ARE", "LIPSA", "?")
# a cell of dashes and spaces alone means no CF/IE too
_DASHES = "-–—− "
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
    FALSE, FĂRĂ, LIPSĂ, NU ARE, N/A, a question mark and dashes count as no CF/IE."""
    if value is None:
        return False
    if isinstance(value, (int, float)):
        # an .xlsx gives numbers back as float; NaN is not equal to itself
        return value == value and value != 0
    text = str(value).strip()
    if fold(text).upper() in _NO_CF_IE or not text.strip(_DASHES):
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


def distribute(values, total, decimals, caps=None):
    """values rounded to decimals so they add up to total, itself rounded half up: each value
    is rounded down, and the units still missing go to the largest remainders, one each. With
    caps, one for each value, no unit goes to a value that it would lift above its cap, so the
    result can add up to less than total."""
    step = 10 ** decimals
    # the rounded total times step is a whole number up to float noise
    target = round(round_half_up(total, decimals) * step)
    scaled = [v * step for v in values]
    floors = [math.floor(s) for s in scaled]
    short = target - sum(floors)
    order = sorted(range(len(values)), key=lambda i: scaled[i] - floors[i],
                   reverse=True)
    if short >= 0:
        if caps is not None:
            # the float noise of a cap times step is far below a millionth of a unit
            order = [i for i in order if floors[i] + 1 <= caps[i] * step + 1e-6]
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
    surplus: float = 0.0        # C - A when the contour is larger than the deeds, else 0
    decimals: int = 2           # the rounding of the new areas
    unassigned: float = 0.0     # mp of the rounded contour that no parcel could take
    error: str = ""


def compute(areas, fixed_flags, contour, decimals=2, left_out=()) -> Result:
    """The reduction for the parsed areas (None when unreadable) and CF/IE flags. A row with
    a negative area is a bad row, like one without a number, and is also listed in
    negative_rows; the rows in left_out, such as total rows, take no part. With a contour
    larger than the sum of the deed areas no area changes, whatever the rounding: the factor
    is 1 and Result.surplus holds the difference. Rounding lifts no parcel above its deed
    area either: the units that would are left out of the sum, and Result.unassigned holds
    them (see sum_note()). Result.error is a Romanian sentence for the user, empty on
    success."""
    result = Result(contour=contour, decimals=decimals)
    result.left_out = sorted(set(left_out))
    skipped = set(result.left_out)
    result.negative_rows = [i for i, a in enumerate(areas)
                            if i not in skipped and a is not None and a < 0]
    areas = [None if i in skipped or a is None or a < 0 else a for i, a in enumerate(areas)]
    result.bad_rows = [i for i, a in enumerate(areas) if a is None and i not in skipped]
    result.fixed_rows = [i for i, flag in enumerate(fixed_flags)
                         if flag and areas[i] is not None]
    readable = [i for i, a in enumerate(areas) if a is not None]
    # fsum: a running sum of thousands of areas drifts from the sum a user types as the contour
    result.total_deed = math.fsum(areas[i] for i in readable)
    result.total_fixed = math.fsum(areas[i] for i in result.fixed_rows)
    free = result.total_deed - result.total_fixed

    if not readable:
        result.error = "Coloana aleasă nu conține nicio suprafață numerică."
        return result
    if result.total_deed <= 0:
        result.error = "Suma suprafețelor din acte este 0."
        return result
    # as floats, 1000.3 + 2000.1 is less than 3000.4: a difference that rounds to nothing is no
    # surplus, so a contour typed as the sum of the deed areas is an ordinary reduction
    surplus = round(contour - result.total_deed, 6)
    if surplus > 0:
        result.surplus = surplus
        result.new_areas = list(areas)
        result.total_new = round(result.total_deed, 6)
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

    # at most 1: a contour above the sum of the deed areas by a difference that rounds to 0 is
    # no surplus, and it must not scale the areas up
    result.factor = min(1.0, (contour - result.total_fixed) / free)
    result.reduction_pct = (1.0 - result.factor) * 100.0

    fixed = set(result.fixed_rows)
    free_rows = [i for i in readable if i not in fixed]
    scaled = distribute([areas[i] * result.factor for i in free_rows], target, decimals,
                        caps=[areas[i] for i in free_rows])
    new_areas = [None] * len(areas)
    for i in result.fixed_rows:
        new_areas[i] = areas[i]
    for i, value in zip(free_rows, scaled):
        new_areas[i] = value
    result.new_areas = new_areas
    result.total_new = round(sum(a for a in new_areas if a is not None), 6)
    result.unassigned = round(max(0.0, round_half_up(target, decimals) - sum(scaled)), 6)
    return result


def sum_note(result) -> str:
    """The sentence on the sum of the new areas: on a surplus, that the areas stay the deed
    areas; otherwise why the new areas and the CF/IE areas do not add up to the contour
    rounded to result.decimals, "" when they do. "" on an error. The cause is units that no
    parcel could take without going over its deed area (Result.unassigned), CF/IE areas with
    more decimals than the rounding, or both."""
    if result.error:
        return ""
    if result.surplus > 0:
        return (f"Conturul depășește suma suprafețelor din acte cu {fmt.area(result.surplus)}, "
                "deci suprafețele rămân cele din acte.")
    target = round_half_up(result.contour, result.decimals)
    if abs(result.total_new - target) <= 1e-6:
        return ""
    if result.unassigned <= 0:
        cause = "Parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă"
    else:
        cause = ("La rotunjire, nicio parcelă nu primește o suprafață mai mare decât cea din "
                 "acte")
        if abs(result.total_new + result.unassigned - target) > 1e-6:
            cause += ", iar parcelele cu CF/IE au mai multe zecimale decât rotunjirea aleasă"
    return (f"{cause}, deci suma nouă este {fmt.area(result.total_new, 4)}, "
            f"nu {fmt.area(target)}.")


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


def _is_number(value) -> bool:
    if isinstance(value, str):
        return parse_number(value) is not None
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_total_name(text) -> bool:
    """Whether a text with the word total, subtotal or sumă is the name of a total row and
    nothing more: the word, alone or with what such a name holds besides it (_NAME_WORDS,
    numbers, a unit, punctuation). An owner's name ("SC TOTAL AGRO SRL") or a remark ("din
    total 5000 mp arabil") is a longer text."""
    rest = _TOTAL_WORD.sub(" ", fold(text))
    return all(token in _NAME_WORDS or token in _UNITS or _NAME_NUMBER.fullmatch(token)
               for token in re.findall(r"[^\W_]+", rest))


def _is_blank(value) -> bool:
    """Whether a cell holds no data: empty, dashes alone, or a unit alone ("mp", "m2", "ha")."""
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return not text.strip(_DASHES) or fold(text).strip(" .()") in _UNITS


def total_candidates(rows) -> dict:
    """{row index: what makes it a total row whatever the area column} for the rows with the
    word total, subtotal, totaluri or sumă in a text cell, in any case, with or without
    diacritics: TOTAL_BY_WORD, TOTAL_BY_LABEL, or None for a row that only its area can make a
    total row (see total_rows()).

    TOTAL_BY_WORD: a cell is the name of a total row and nothing more. The other rows hold the
    word inside a longer text, and the word alone does not make them total rows. TOTAL_BY_LABEL:
    the row's other cells with data are numbers, and it differs from the parcel rows, the rows
    without the word: a column that most of them fill is empty in it, or its word stands in a
    column where most of them hold a number. None of this depends on the area column, so it is
    computed once for a table."""
    words = {}
    for i, row in enumerate(rows):
        columns = [j for j, value in enumerate(row) if isinstance(value, str)
                   and _TOTAL_HINT.search(value) and _TOTAL_WORD.search(fold(value))]
        if columns:
            words[i] = columns
    found = {i: TOTAL_BY_WORD if any(_is_total_name(rows[i][j]) for j in columns) else None
             for i, columns in words.items()}
    if all(found.values()):
        return found
    parcels = [row for i, row in enumerate(rows) if i not in words]
    width = max(len(row) for row in rows)
    half = len(parcels) / 2.0
    filled = [sum(1 for row in parcels if j < len(row) and not _is_blank(row[j])) > half
              for j in range(width)]
    numeric = [sum(1 for row in parcels if j < len(row) and _is_number(row[j])) > half
               for j in range(width)]
    for i, columns in words.items():
        if found[i]:
            continue
        row = rows[i]
        others = [j for j, value in enumerate(row) if j not in columns and not _is_blank(value)]
        unlike_a_parcel = (any(numeric[j] for j in columns)
                           or any(filled[j] and _is_blank(row[j]) for j in range(len(row))))
        if unlike_a_parcel and all(_is_number(row[j]) for j in others):
            found[i] = TOTAL_BY_LABEL
    return found


def total_rows(rows, areas, candidates) -> dict:
    """{row index: TOTAL_BY_WORD, TOTAL_BY_SUM or TOTAL_BY_LABEL} for the rows that sum others
    up and take no part in the reduction, among candidates, what total_candidates(rows)
    returns. A row with a cell that is the name of a total row is one
    whatever its area: a total can count rows hidden in Excel, be typed by hand or stand above
    its rows. A row with the word inside a longer text needs evidence beyond the word.
    TOTAL_BY_SUM: its area, within SUM_SLACK, is the sum of at least two parcel rows above it,
    either all of them or those below the previous total row. TOTAL_BY_LABEL: it holds only
    that text and numbers. A parcel with the word in its owner's name or in a remark is none
    of these, and stays a parcel. areas holds the rows' parsed areas, None for an unreadable
    one."""
    found = {}
    # the sum and the number of the parcel rows above: below the previous total, and all
    part, part_count, whole, whole_count = 0.0, 0, 0.0, 0
    for i, area in enumerate(areas):
        if i in candidates:
            if candidates[i] == TOTAL_BY_WORD:
                found[i] = TOTAL_BY_WORD
            elif area is not None and (
                    (part_count > 1 and abs(area - part) <= SUM_SLACK)
                    or (whole_count > 1 and abs(area - whole) <= SUM_SLACK)):
                found[i] = TOTAL_BY_SUM
            elif candidates[i]:
                found[i] = TOTAL_BY_LABEL
            if i in found:
                part, part_count = 0.0, 0
                continue
        if area is not None and area >= 0:
            part, part_count = part + area, part_count + 1
            whole, whole_count = whole + area, whole_count + 1
    return found


# a total row's reason, for a list such as "5 (suma rândurilor de deasupra)"
TOTAL_REASONS = {TOTAL_BY_WORD: "denumire de total",
                 TOTAL_BY_SUM: "suma rândurilor de deasupra",
                 TOTAL_BY_LABEL: "doar denumirea și numere"}


def total_list(totals, name) -> str:
    """The total rows of total_rows() with the reason of each, for a note: "5 (suma rândurilor
    de deasupra), 9 (doar denumirea și numere)", the first LISTED_ROWS of them. name gives
    the text shown for a row index."""
    shown = ", ".join(f"{name(i)} ({TOTAL_REASONS[totals[i]]})"
                      for i in sorted(totals)[:LISTED_ROWS])
    return shown + ("..." if len(totals) > LISTED_ROWS else "")


class Table(NamedTuple):
    names: list           # the sheets of the file
    header: list          # column names, unique
    rows: list
    numbers: list         # the Excel row number of each row
    header_number: int    # the Excel row number of the header
    hidden: list          # the Excel row numbers of the filled rows hidden in Excel
    sheet: str            # the sheet the table was read from


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
    left out. Formulas give their last computed value. Without sheet_name, or with a name
    the file lacks, the sheet is xlsx.default_sheet(); Table.sheet names the one read.
    """
    from . import xlsx
    names, raw, hidden_numbers = xlsx.read_table(path, sheet_name)
    sheet = sheet_name if sheet_name in names else xlsx.default_sheet(path)
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
    return Table(names, header or [], rows, numbers, header_number, hidden, sheet or "")


def write_excel(path, header, rows, result, meta, area_col, fixed_col) -> int:
    """The source table with the reduced area and the difference, and a Sumar sheet. Returns
    the number of cells whose text was cut to the length an Excel cell holds."""
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
    calculation = [
        ["A", round(result.total_deed, 4), "", "Suma suprafețelor din acte"],
        ["B", round(result.total_fixed, 4), "",
         "Suma suprafețelor parcelelor cu CF/IE, care nu se modifică"],
        ["C", round(result.contour, 4), "", "Suprafața măsurată a conturului"]]
    if result.surplus > 0:
        calculation += [
            ["C - A", round(result.surplus, 4), "",
             "Cu cât depășește conturul suma suprafețelor din acte"],
            ["Factor", round(result.factor, 8), "",
             "Conturul este mai mare decât suma din acte, deci suprafețele nu se modifică"]]
        note = ("Conturul este mai mare decât suma suprafețelor din acte, deci suprafețele au "
                "rămas cele din acte, nerotunjite.")
    else:
        calculation.append(["Factor", round(result.factor, 8), "", "(C - B) / (A - B)"])
        difference = sum_note(result)
        if difference:
            rounded = fmt.area(round_half_up(result.contour, result.decimals))
            note = ("Suprafața conturului, rotunjită la aceeași precizie ca suprafețele noi, "
                    f"este {rounded}. {difference}")
        else:
            note = ("Suprafețele noi sunt rotunjite astfel încât suma lor, împreună cu "
                    "parcelele cu CF/IE, să fie egală cu suprafața conturului rotunjită la "
                    "aceeași precizie.")
    calculation += [
        ["Diminuare", xlsx_report.percent(result.reduction_pct, 4), "",
         "Cât pierde fiecare parcelă fără CF/IE"],
        ["Suma nouă", round(result.total_new, 4), "",
         "Suma suprafețelor diminuate și a parcelelor cu CF/IE"]]
    summary.table(["Mărime", "Valoare", "", "Explicație"], calculation,
                  number_format="General")
    summary.note(note)
    xlsx_report.write_table(wb, "Diminuare", new_header, out_rows, row_colors=colors,
                            text_columns=[header[fixed_col]] if fixed_col is not None else ())
    xlsx_report.save(wb, path)
    return wb.cut_cells
