# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Excel report layout shared by the tools: data tables and the Sumar sheet."""

import math
import re
from datetime import date, datetime, time, timedelta

from . import fmt, xlsx

# ACCENT is 6.6:1 on white
ACCENT = "0052D8"
HEADING = "161A22"
BODY = "3D4451"
META = "687181"
STRIPE = "F4F7FA"
BORDER = "E3E7ED"

# row tints, light enough for the body text to keep its contrast
GOOD = "DDF3E6"
BAD = "FBE0DE"
MIXED = "FDF1CC"
NOTE = "E8E1F3"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

DATE_FORMAT = "dd.mm.yyyy"
DATETIME_FORMAT = "dd.mm.yyyy hh:mm"
# day 0 of Excel's date numbers; they include 29 February 1900, which did not exist, so they
# are right only from 1 March 1900 and earlier dates are written as text
_EXCEL_EPOCH = datetime(1899, 12, 30)
_FIRST_EXCEL_DATE = datetime(1900, 3, 1)


class Formatted:
    """A cell value with its own number format."""

    def __init__(self, value, number_format):
        self.value = value
        self.number_format = number_format


def cell_value(value):
    """A storable cell value: NULL becomes empty, numbers stay numbers, dates become Excel
    dates (Formatted), so a filter groups them by year and month and a sort is chronological,
    times become hh:mm:ss text, anything else text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "Da" if value else "Nu"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return value
    if isinstance(value, datetime):
        return _excel_date(value.replace(tzinfo=None), DATETIME_FORMAT)
    if isinstance(value, date):
        return _excel_date(datetime(value.year, value.month, value.day), DATE_FORMAT)
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    type_name = type(value).__name__
    if type_name == "QVariant":
        return None if value.isNull() else cell_value(value.value())
    if type_name in ("QDate", "QDateTime", "QTime"):
        if value.isNull() or not value.isValid():
            return None
        if type_name == "QDate":
            return cell_value(value.toPyDate())
        if type_name == "QDateTime":
            return cell_value(value.toPyDateTime())
        return value.toString("HH:mm:ss")
    return _CONTROL.sub(" ", str(value))


def _excel_date(moment: datetime, number_format):
    if moment < _FIRST_EXCEL_DATE:
        text = f"{moment.day:02d}.{moment.month:02d}.{moment.year:04d}"
        if number_format == DATETIME_FORMAT:
            text += f" {moment.hour:02d}:{moment.minute:02d}"
        return text
    days = (moment - _EXCEL_EPOCH).total_seconds() / 86400.0
    return Formatted(int(days) if days.is_integer() else days, number_format)


def _split(value):
    """(cell value, number format or None) of a value given to a sheet."""
    if isinstance(value, Formatted):
        inner = cell_value(value.value)
        if isinstance(inner, Formatted):
            return inner.value, value.number_format
        return inner, value.number_format
    stored = cell_value(value)
    if isinstance(stored, Formatted):
        return stored.value, stored.number_format
    return stored, None


def _display_text(value, number_format):
    """The text a cell shows, for the column width."""
    if number_format in (DATE_FORMAT, DATETIME_FORMAT) and isinstance(value, (int, float)):
        return number_format
    return "" if value is None else str(value)


def percent(value, decimals=1) -> Formatted:
    """A percentage such as 55.6 as a number, shown as 55,6% or 55.6% depending on the
    computer's regional settings."""
    code = "0." + "0" * decimals + "%" if decimals else "0%"
    return Formatted(None if value is None else value / 100.0, code)


def _as_text(value, number_format=None) -> str:
    """A number in a text column as a Romanian writes it: 1155.0 is "1155", 1186.5 is
    "1186,5"; a date as 01.03.2025."""
    if number_format in (DATE_FORMAT, DATETIME_FORMAT):
        moment = _EXCEL_EPOCH + timedelta(days=value)
        return moment.strftime("%d.%m.%Y %H:%M" if number_format == DATETIME_FORMAT
                               else "%d.%m.%Y")
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else fmt.number(value, 6, thousands=False)
    return str(value)


def new_workbook():
    return xlsx.Workbook()


def _header(wb):
    return wb.style(bold=True, color="FFFFFF", fill=ACCENT, border=BORDER,
                    halign="center", valign="center", wrap=True)


def _body(wb, fill=None, text=False, number=None):
    return wb.style(color=BODY, fill=fill, border=BORDER, valign="top", wrap=True,
                    number="@" if text else number)


def write_table(wb, title, headers, rows, *, row_colors=None, text_columns=(),
                max_width=60):
    """A sheet with a frozen, filterable header row.

    row_colors holds a hex color or None per row, replacing the stripes. Cells of the
    text_columns headers are stored as text, so identifiers keep their leading zeros.
    """
    sheet = wb.add_sheet(title)
    header_style = _header(wb)
    for col, name in enumerate(headers, start=1):
        sheet.set(1, col, str(name), header_style)
    text_idx = {i for i, h in enumerate(headers) if h in set(text_columns)}
    widths = [len(str(h)) for h in headers]
    for r, row in enumerate(rows):
        color = row_colors[r] if row_colors else None
        fill = color or (STRIPE if r % 2 else None)
        for i, value in enumerate(row):
            v, number = _split(value)
            if i in text_idx and v is not None:
                v, number = _as_text(v, number), None
            sheet.set(r + 2, i + 1, v, _body(wb, fill, text=i in text_idx, number=number))
            if v is not None and i < len(widths):
                shown = _display_text(v, number)
                widths[i] = max(widths[i], max(len(line) for line in shown.split("\n")))
    for i, width in enumerate(widths, start=1):
        sheet.widths[i] = min(width + 3, max_width)
    sheet.freeze = (2, 1)
    if headers:
        sheet.filter = f"A1:{xlsx.column_letter(len(headers))}{max(len(rows) + 1, 1)}"
    return sheet


# Sumar column widths in characters; a fact's value spans the last three, a note all four
SUMMARY_WIDTHS = (44, 22, 18, 44)
# characters per line and points per line: Excel does not fit a merged cell's height to its
# text, so the rows of long facts and notes get their height from their length
FACT_LINE = (76, 15)
NOTE_LINE = (170, 12)


def _row_height(text, characters, points):
    """The height of a row whose wrapped text has lines of about characters each, None for
    a single line."""
    lines = sum(max(1, math.ceil(len(part) / characters)) for part in str(text).split("\n"))
    return lines * points + 3 if lines > 1 else None


class Summary:
    """The Sumar sheet, four columns wide, built top to bottom: title, facts, tables. Printed
    one page wide on A4."""

    def __init__(self, wb, title, sheet="Sumar"):
        self.wb = wb
        self.sheet = wb.add_sheet(sheet, index=0)
        self.sheet.grid = False
        self.sheet.fit_width = True
        for col, width in zip(range(1, 5), SUMMARY_WIDTHS):
            self.sheet.widths[col] = width
        self.sheet.set(1, 1, title, wb.style(bold=True, color=HEADING, size=15))
        self.sheet.merge(1, 1, 1, 4)
        self.row = 3

    def section(self, title):
        self.sheet.set(self.row, 1, title, self.wb.style(bold=True, color=ACCENT, size=12))
        self.row += 1

    def facts(self, pairs):
        label = self.wb.style(color=META, valign="top", wrap=True)
        for name, value in pairs:
            v, number = _split(value)
            self.sheet.set(self.row, 1, name, label)
            self.sheet.set(self.row, 2, v, self.wb.style(
                color=BODY, halign="left", valign="top", wrap=True, number=number))
            self.sheet.merge(self.row, 2, self.row, 4)
            height = _row_height(_display_text(v, number), *FACT_LINE)
            if height:
                self.sheet.heights[self.row] = height
            self.row += 1
        self.row += 1

    def table(self, headers, rows, row_colors=None, number_format="#,##0"):
        header_style = _header(self.wb)
        for col, name in enumerate(headers, start=1):
            self.sheet.set(self.row, col, name, header_style)
        self.row += 1
        for r, row in enumerate(rows):
            color = row_colors[r] if row_colors else None
            for col, value in enumerate(row, start=1):
                v, number = _split(value)
                numeric = isinstance(v, (int, float)) and not isinstance(v, bool)
                if number is None and numeric:
                    number = number_format
                self.sheet.set(self.row, col, v, _body(self.wb, color, number=number))
            self.row += 1
        self.row += 1

    def note(self, text):
        self.sheet.set(self.row, 1, text, self.wb.style(italic=True, color=META, size=9,
                                                        valign="top", wrap=True))
        self.sheet.merge(self.row, 1, self.row, 4)
        self.sheet.heights[self.row] = _row_height(text, *NOTE_LINE) or 15
        self.row += 1


def save(wb, path: str):
    wb.save(path)
