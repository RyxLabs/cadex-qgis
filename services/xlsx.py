# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Minimal .xlsx writer and .xlsx / .csv reader on the standard library.

Not openpyxl: on QGIS 3.40 for Windows its lxml shares libxml2 with GDAL, and after GDAL has
written a GeoPackage the next openpyxl workbook crashes QGIS.
"""

import csv
import io
import os
import re
# the files are the user's own; expat fetches no external entities and, from 2.4, limits
# entity expansion
import xml.etree.ElementTree as ET  # nosec B405
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from typing import NamedTuple

from . import fmt

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
SPREADSHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml"

# built-in number formats, no numFmt entry needed
BUILTIN_FORMATS = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "@": 49}

# what an XML 1.0 file cannot hold: the control characters other than tab, line feed and
# carriage return, the halves of a surrogate pair, U+FFFE and U+FFFF
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_LINE_SPACE = re.compile(r"[\t\r\n]")
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")

# Excel's limits; it counts the length of a text in UTF-16 units
MAX_SHEET_NAME = 31
MAX_CELL_TEXT = 32767
MAX_ROWS = 1048576
MAX_COLUMNS = 16384


def column_letter(index: int) -> str:
    """1 -> "A", 27 -> "AA"."""
    letters = ""
    while index > 0:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def column_index(letters: str) -> int:
    """"A" -> 1, "AA" -> 27."""
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index


def clean_text(value: str) -> str:
    """value with a space in place of every character an XML file cannot hold."""
    return _NOT_XML.sub(" ", value)


def utf16_cut(text: str, units: int) -> str:
    """text cut to at most units UTF-16 units; a character outside the basic plane counts as
    two and is not cut in half."""
    if len(text) * 2 <= units:
        return text
    count = 0
    for position, char in enumerate(text):
        count += 2 if char > "\uffff" else 1
        if count > units:
            return text[:position]
    return text


def sheet_name(text: str) -> str:
    """A valid worksheet name: none of []:*?/\\, one line, at most 31 UTF-16 units, no
    apostrophe at either end, not empty."""
    name = _SHEET_BAD.sub("-", _LINE_SPACE.sub(" ", clean_text(str(text))))
    return utf16_cut(name, MAX_SHEET_NAME).strip("'") or "Foaie"


def _xml_text(value: str) -> str:
    return escape(utf16_cut(clean_text(value), MAX_CELL_TEXT), quote=False)


def _attr(value: str) -> str:
    return escape(clean_text(str(value)))


class SheetTooLarge(OSError):
    """A sheet with more rows or columns than Excel holds. An OSError, so that a tool reports
    it as a report file it could not write."""


class _Styles:
    """Deduplicated fonts, fills, borders and cell formats for styles.xml."""

    def __init__(self):
        self.fonts: list = [("", False, False, 11.0)]
        self.fills: list = [None, "gray125"]  # the format reserves the first two fills
        self.borders: list = [None]
        self.formats: dict = {}
        self.xfs: list = [(0, 0, 0, 0, None)]
        # get()'s arguments -> cell format id; a report asks for the same few once per cell
        self._ids: dict = {}

    def _index(self, items, item):
        if item not in items:
            items.append(item)
        return items.index(item)

    def get(self, *, bold=False, italic=False, color="", size=11.0, fill=None,
            border=None, halign=None, valign=None, wrap=False, number=None):
        key = (bold, italic, color, size, fill, border, halign, valign, wrap, number)
        if key not in self._ids:
            self._ids[key] = self._add(*key)
        return self._ids[key]

    def _add(self, bold, italic, color, size, fill, border, halign, valign, wrap, number):
        font = self._index(self.fonts, (color or "", bool(bold), bool(italic), float(size)))
        fill_id = self._index(self.fills, fill) if fill else 0
        border_id = self._index(self.borders, border) if border else 0
        if number is None or number == "General":
            num = 0
        elif number in BUILTIN_FORMATS:
            num = BUILTIN_FORMATS[number]
        else:
            num = self.formats.setdefault(number, 164 + len(self.formats))
        align = (halign, valign, bool(wrap)) if (halign or valign or wrap) else None
        return self._index(self.xfs, (num, font, fill_id, border_id, align))

    def xml(self) -> str:
        out = [f'{XML_DECLARATION}<styleSheet xmlns="{MAIN_NS}">']
        if self.formats:
            out.append(f'<numFmts count="{len(self.formats)}">')
            for code, num in self.formats.items():
                out.append(f'<numFmt numFmtId="{num}" formatCode="{_attr(code)}"/>')
            out.append("</numFmts>")
        out.append(f'<fonts count="{len(self.fonts)}">')
        for color, bold, italic, size in self.fonts:
            parts = ["<b/>" if bold else "", "<i/>" if italic else "", f'<sz val="{size:g}"/>',
                     f'<color rgb="FF{color}"/>' if color else "",
                     '<name val="Calibri"/><family val="2"/>']
            out.append("<font>" + "".join(parts) + "</font>")
        out.append("</fonts>")
        out.append(f'<fills count="{len(self.fills)}">')
        for fill in self.fills:
            if fill is None:
                out.append('<fill><patternFill patternType="none"/></fill>')
            elif fill == "gray125":
                out.append('<fill><patternFill patternType="gray125"/></fill>')
            else:
                out.append(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{fill}"/>'
                           '<bgColor indexed="64"/></patternFill></fill>')
        out.append("</fills>")
        out.append(f'<borders count="{len(self.borders)}">')
        for border in self.borders:
            if border is None:
                out.append("<border><left/><right/><top/><bottom/><diagonal/></border>")
            else:
                side = f'style="thin"><color rgb="FF{border}"/>'
                out.append(f"<border><left {side}</left><right {side}</right><top {side}</top>"
                           f"<bottom {side}</bottom><diagonal/></border>")
        out.append("</borders>")
        out.append('<cellStyleXfs count="1">'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>')
        out.append(f'<cellXfs count="{len(self.xfs)}">')
        for num, font, fill, border, align in self.xfs:
            attrs = (f'numFmtId="{num}" fontId="{font}" fillId="{fill}" borderId="{border}"'
                     ' xfId="0"'
                     + (' applyNumberFormat="1"' if num else "")
                     + (' applyFont="1"' if font else "")
                     + (' applyFill="1"' if fill else "")
                     + (' applyBorder="1"' if border else ""))
            if align:
                halign, valign, wrap = align
                inner = ((f' horizontal="{halign}"' if halign else "")
                         + (f' vertical="{valign}"' if valign else "")
                         + (' wrapText="1"' if wrap else ""))
                out.append(f'<xf {attrs} applyAlignment="1"><alignment{inner}/></xf>')
            else:
                out.append(f"<xf {attrs}/>")
        out.append("</cellXfs>")
        out.append('<cellStyles count="1">'
                   '<cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>')
        out.append("</styleSheet>")
        return "".join(out)


class Sheet:

    def __init__(self, name, book):
        self.name = name
        self.book = book         # the Workbook that counts the cells cut short
        self.cells = {}          # (row, col) -> (value, style id)
        self.widths = {}         # col -> characters
        self.heights = {}        # row -> points
        self.merges = []
        self.freeze: tuple = ()  # first unfrozen (row, col), empty for no frozen pane
        self.filter = ""         # "A1:D10", empty for no filter
        self.grid = True
        # printed on A4 portrait, scaled to the page width, as many pages long as needed
        self.fit_width = False

    def set(self, row, col, value, style=0):
        """A text longer than a cell holds is cut to MAX_CELL_TEXT and counted in the
        workbook's cut_cells."""
        if isinstance(value, str) and len(value) * 2 > MAX_CELL_TEXT:
            whole, value = value, utf16_cut(value, MAX_CELL_TEXT)
            if len(value) < len(whole):
                self.book.cut_cells += 1
        self.cells[(row, col)] = (value, style)

    def merge(self, first_row, first_col, last_row, last_col):
        self.merges.append(f"{column_letter(first_col)}{first_row}:"
                           f"{column_letter(last_col)}{last_row}")

    @property
    def max_row(self):
        return max((r for r, _c in self.cells), default=0)

    @property
    def max_col(self):
        return max((c for _r, c in self.cells), default=0)

    def xml(self) -> str:
        out = [f'{XML_DECLARATION}<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">']
        # the schema orders the elements: sheetPr first, pageSetup after pageMargins
        if self.fit_width:
            out.append('<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>')
        if self.cells:
            out.append(f'<dimension ref="A1:{column_letter(self.max_col)}{self.max_row}"/>')
        view = '<sheetView workbookViewId="0"' + ("" if self.grid else ' showGridLines="0"')
        if self.freeze:
            row, col = self.freeze
            cell = f"{column_letter(col)}{row}"
            pane_attrs = ((f' xSplit="{col - 1}"' if col > 1 else "")
                          + (f' ySplit="{row - 1}"' if row > 1 else ""))
            pane = "bottomLeft" if col == 1 else ("topRight" if row == 1 else "bottomRight")
            out.append(f'<sheetViews>{view}><pane{pane_attrs} topLeftCell="{cell}" '
                       f'activePane="{pane}" state="frozen"/>'
                       f'<selection pane="{pane}" activeCell="{cell}" sqref="{cell}"/>'
                       "</sheetView></sheetViews>")
        else:
            out.append(f"<sheetViews>{view}/></sheetViews>")
        out.append('<sheetFormatPr defaultRowHeight="15"/>')
        if self.widths:
            out.append("<cols>")
            for col in sorted(self.widths):
                out.append(f'<col min="{col}" max="{col}" width="{self.widths[col]:.2f}"'
                           ' customWidth="1"/>')
            out.append("</cols>")
        out.append("<sheetData>")
        rows = {}
        for (row, col), cell in self.cells.items():
            rows.setdefault(row, {})[col] = cell
        for row in sorted(rows):
            height = self.heights.get(row)
            out.append(f'<row r="{row}"'
                       + (f' ht="{height:g}" customHeight="1"' if height else "") + ">")
            for col in sorted(rows[row]):
                value, style = rows[row][col]
                ref = f"{column_letter(col)}{row}"
                s = f' s="{style}"' if style else ""
                if value is None or value == "":
                    out.append(f'<c r="{ref}"{s}/>')
                elif isinstance(value, bool):
                    out.append(f'<c r="{ref}"{s} t="b"><v>{int(value)}</v></c>')
                elif isinstance(value, (int, float)):
                    if value != value or value in (float("inf"), float("-inf")):
                        out.append(f'<c r="{ref}"{s}/>')
                    else:
                        out.append(f'<c r="{ref}"{s}><v>{value!r}</v></c>')
                else:
                    text = str(value)
                    space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
                    out.append(f'<c r="{ref}"{s} t="inlineStr">'
                               f"<is><t{space}>{_xml_text(text)}</t></is></c>")
            out.append("</row>")
        out.append("</sheetData>")
        if self.filter:
            out.append(f'<autoFilter ref="{self.filter}"/>')
        if self.merges:
            out.append(f'<mergeCells count="{len(self.merges)}">')
            out.extend(f'<mergeCell ref="{ref}"/>' for ref in self.merges)
            out.append("</mergeCells>")
        out.append('<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3"'
                   ' footer="0.3"/>')
        if self.fit_width:
            # paper size 9 is A4; a fitToHeight of 0 leaves the number of pages free
            out.append('<pageSetup paperSize="9" orientation="portrait" fitToWidth="1"'
                       ' fitToHeight="0"/>')
        out.append("</worksheet>")
        return "".join(out)


class Workbook:

    def __init__(self):
        self.sheets = []
        self.styles = _Styles()
        # how many cells hold a text cut to MAX_CELL_TEXT, for the caller to report
        self.cut_cells = 0

    def style(self, **kwargs) -> int:
        return self.styles.get(**kwargs)

    def add_sheet(self, name, index=None) -> Sheet:
        base = sheet_name(name)
        taken = {s.name.lower() for s in self.sheets}
        unique, n = base, 2
        while unique.lower() in taken:
            suffix = f" ({n})"
            unique = utf16_cut(base, MAX_SHEET_NAME - len(suffix)) + suffix
            n += 1
        sheet = Sheet(unique, self)
        if index is None:
            self.sheets.append(sheet)
        else:
            self.sheets.insert(index, sheet)
        return sheet

    def save(self, path):
        """Write the workbook to a temporary file in path's folder, then move it onto path:
        when writing fails, the file already at path stays whole. An error names path. A
        sheet larger than Excel holds raises SheetTooLarge before anything is written."""
        if not self.sheets:
            self.add_sheet("Foaie")
        self._refuse_too_large()
        temporary = f"{path}.{os.getpid()}.tmp"
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
                self._write_parts(zf)
            os.replace(temporary, path)
        except OSError as error:
            _remove(temporary)
            if error.errno is None:
                raise
            # OSError(errno, ...) returns PermissionError or FileNotFoundError for these errno
            # values
            raise OSError(error.errno, error.strerror, path) from error
        except BaseException:
            _remove(temporary)
            raise

    def _refuse_too_large(self):
        # the text ends outputs.write_error's sentence "Fișierul '...' nu poate fi scris: "
        for sheet in self.sheets:
            for size, limit, one, many in ((sheet.max_row, MAX_ROWS, "rând", "rânduri"),
                                           (sheet.max_col, MAX_COLUMNS, "coloană", "coloane")):
                if size > limit:
                    raise SheetTooLarge(
                        f"foaia '{sheet.name}' are {fmt.count(size, one, many)}, iar o foaie "
                        f"Excel cuprinde cel mult {fmt.number(limit, 0)}")

    def _write_parts(self, zf):
        count = len(self.sheets)
        zf.writestr("[Content_Types].xml", self._content_types(count))
        zf.writestr("_rels/.rels", (
            f'{XML_DECLARATION}<Relationships xmlns="{PKG_REL_NS}">'
            f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument"'
            ' Target="xl/workbook.xml"/></Relationships>'))
        zf.writestr("xl/workbook.xml", self._workbook())
        rels = [f'<Relationship Id="rId{i}" Type="{REL_NS}/worksheet"'
                f' Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1)]
        rels.append(f'<Relationship Id="rId{count + 1}" Type="{REL_NS}/styles"'
                    ' Target="styles.xml"/>')
        zf.writestr("xl/_rels/workbook.xml.rels", (
            f'{XML_DECLARATION}<Relationships xmlns="{PKG_REL_NS}">'
            + "".join(rels) + "</Relationships>"))
        zf.writestr("xl/styles.xml", self.styles.xml())
        for i, sheet in enumerate(self.sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet.xml())

    @staticmethod
    def _content_types(count):
        sheets = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml"'
            f' ContentType="{SPREADSHEET_TYPE}.worksheet+xml"/>' for i in range(1, count + 1))
        return (f'{XML_DECLARATION}'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels"'
                ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml"'
                f' ContentType="{SPREADSHEET_TYPE}.sheet.main+xml"/>'
                '<Override PartName="/xl/styles.xml"'
                f' ContentType="{SPREADSHEET_TYPE}.styles+xml"/>'
                + sheets + "</Types>")

    def _workbook(self):
        sheets = "".join(f'<sheet name="{_attr(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
                         for i, s in enumerate(self.sheets, start=1))
        # Excel "repairs" a file whose filter lacks this hidden defined name
        names = "".join(
            f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
            f"{_xml_text(_quoted(s.name))}!{_absolute(s.filter)}</definedName>"
            for i, s in enumerate(self.sheets) if s.filter)
        return (f'{XML_DECLARATION}<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
                '<bookViews><workbookView activeTab="0"/></bookViews>'
                f"<sheets>{sheets}</sheets>"
                + (f"<definedNames>{names}</definedNames>" if names else "")
                + "</workbook>")


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _quoted(name):
    return "'" + name.replace("'", "''") + "'"


def _absolute(ref):
    """"A1:D10" -> "$A$1:$D$10"."""
    return ":".join(_CELL_REF.sub(r"$\1$\2", part) for part in ref.split(":"))


# --- reading ---

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _shared_strings(zf):
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    strings = []
    for item in ET.fromstring(data):  # nosec B314
        if _local(item.tag) != "si":
            continue
        # plain text is one <t>, rich text <r><t> runs; phonetic <rPh> guides are not text
        parts = []
        for child in item:
            name = _local(child.tag)
            if name == "t":
                parts.append(child.text or "")
            elif name == "r":
                parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
        strings.append("".join(parts))
    return strings


class Percent(float):
    """A number that its cell shows as a percentage: 0.25 with number_format "0%" is 25%."""

    def __new__(cls, value, number_format="0%"):
        number = super().__new__(cls, value)
        number.number_format = number_format
        return number


class _Sheet(NamedTuple):
    name: str
    path: str      # of its part inside the zip
    shown: bool    # a worksheet Excel shows: not hidden, and not a chart or macro sheet


class _Book(NamedTuple):
    sheets: list     # of _Sheet, in tab order
    styles: str      # the path of the styles part
    date1904: bool   # days counted from 1904, as in a workbook begun on a Mac


def _book(zf) -> _Book:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))  # nosec B314
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))  # nosec B314
    targets, styles = {}, "xl/styles.xml"
    for rel in rels:
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        targets[rel.get("Id")] = (target, kind)
        if kind == "styles":
            styles = target
    sheets, date1904 = [], False
    for element in workbook.iter():
        name = _local(element.tag)
        if name == "workbookPr":
            date1904 = element.get("date1904") in ("1", "true")
        elif name == "sheet":
            rid = next((v for k, v in element.attrib.items() if _local(k) == "id"), None)
            if rid in targets:
                target, kind = targets[rid]
                shown = (kind == "worksheet"
                         and element.get("state") not in ("hidden", "veryHidden"))
                sheets.append(_Sheet(element.get("name"), target, shown))
    return _Book(sheets, styles, date1904)


def _chosen(sheets, name=None):
    """The sheet called name, else the first one Excel shows, since a hidden sheet or a chart
    can come first, else the first; None for a workbook without sheets."""
    return (next((s for s in sheets if s.name == name), None)
            or next((s for s in sheets if s.shown), None)
            or (sheets[0] if sheets else None))


def sheet_names(path):
    if path.lower().endswith(".csv"):
        return ["CSV"]
    with zipfile.ZipFile(path) as zf:
        return [sheet.name for sheet in _book(zf).sheets]


def default_sheet(path):
    """The name of the sheet read_table() reads when none is named, None for a workbook
    without sheets."""
    if path.lower().endswith(".csv"):
        return "CSV"
    with zipfile.ZipFile(path) as zf:
        sheet = _chosen(_book(zf).sheets)
    return sheet.name if sheet else None


def read_rows(path, sheet=None):
    """(sheet names, rows) of an .xlsx or .csv. Rows are lists of cell values, None for an
    empty cell, all as wide as the widest row of values; the empty rows above the last value
    are kept, so row numbers match Excel's. A value is a str, a bool, a float, or what the
    cell's format shows of its number: a date, a datetime or a time, or a Percent."""
    names, rows, _hidden = read_table(path, sheet)
    return names, rows


def read_table(path, sheet=None):
    """(sheet names, rows, hidden row numbers): read_rows() and the Excel numbers (from 1) of
    the rows hidden in the sheet, by a filter or by hand. A CSV has no hidden rows. Without
    sheet, or with a name the workbook lacks, the sheet read is default_sheet()."""
    if path.lower().endswith(".csv"):
        return ["CSV"], _read_csv(path), []
    with zipfile.ZipFile(path) as zf:
        book = _book(zf)
        chosen = _chosen(book.sheets, sheet)
        if chosen is None:
            return [], [], []
        names = [s.name for s in book.sheets]
        shared = _shared_strings(zf)
        formats = _cell_formats(zf, book.styles)
        rows = {}
        hidden = []
        with zf.open(chosen.path) as fp:
            next_row = 0
            for _event, element in ET.iterparse(fp):  # nosec B314
                if _local(element.tag) != "row":
                    continue
                next_row = int(element.get("r") or next_row + 1)
                if element.get("hidden") in ("1", "true"):
                    hidden.append(next_row)
                values = {}
                next_col = 0
                for cell in element:
                    if _local(cell.tag) != "c":
                        continue
                    ref = cell.get("r")
                    match = _CELL_REF.match(ref or "")
                    next_col = column_index(match.group(1)) if match else next_col + 1
                    value = _cell_value(cell, shared, formats, book.date1904)
                    # a cell that holds only a format is not part of the table: one such
                    # cell in the last column would make every row 16,384 cells wide
                    if value is not None and value != "":
                        values[next_col] = value
                if values:
                    rows[next_row] = values
                element.clear()
    width = max((max(v) for v in rows.values()), default=0)
    last = max(rows, default=0)
    return names, [[rows.get(r, {}).get(c) for c in range(1, width + 1)]
                   for r in range(1, last + 1)], hidden


def _cell_value(cell, shared, formats, date1904):
    kind = cell.get("t", "n")
    value = None
    inline = None
    for child in cell:
        name = _local(child.tag)
        if name == "v":
            value = child.text
        elif name == "is":
            inline = "".join(t.text or "" for t in child.iter() if _local(t.tag) == "t")
    if kind == "inlineStr":
        return inline
    if value is None:
        return None
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return None
    if kind in ("str", "e"):
        return value
    if kind == "b":
        return value == "1"
    try:
        number = float(value)
    except ValueError:
        return value
    try:
        shows, code = formats[int(cell.get("s") or 0)]
    except (ValueError, IndexError):
        return number
    if shows == "percent":
        return Percent(number, code)
    return _moment(number, shows, date1904) if shows else number


# the codes of the built-in number formats that show a percentage, a date or a time
_BUILTIN_CODES = {9: "0%", 10: "0.00%", 14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm",
                  17: "mmm-yy", 18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm",
                  21: "h:mm:ss", 22: "m/d/yy h:mm", 45: "mm:ss", 46: "[h]:mm:ss",
                  47: "mmss.0"}
# what a format code holds besides its own letters: text in quotes, a character after a
# backslash, an underscore or an asterisk, and a colour, condition or locale in brackets;
# [h], [m] and [s] are hours, minutes and seconds counted past a day
_FORMAT_LITERALS = re.compile(r'"[^"]*"|\\.|_.|\*.|\[(?![hms]+\])[^\]]*\]', re.IGNORECASE)
_EPOCH_1900 = datetime(1899, 12, 30)
_EPOCH_1904 = datetime(1904, 1, 1)


def _format_kind(code):
    """What a number format code shows of a number: "date", "datetime", "time", "percent",
    or None for the number itself. Only the section for positive numbers is read."""
    bare = _FORMAT_LITERALS.sub("", code).split(";")[0].lower()
    if bare == "general":
        return None
    has_time = "h" in bare or "s" in bare
    # m is the minutes next to hours or seconds, the month otherwise
    has_date = "d" in bare or "y" in bare or ("m" in bare and not has_time)
    if has_date:
        return "datetime" if has_time else "date"
    if has_time:
        return "time"
    return "percent" if "%" in bare else None


def _cell_formats(zf, path) -> list:
    """(what the format shows, as _format_kind() names it, format code) of each cell style
    of the workbook, by its index; empty for a workbook without a styles part."""
    try:
        styles = ET.fromstring(zf.read(path))  # nosec B314
    except KeyError:
        return []
    codes = dict(_BUILTIN_CODES)
    numbers = []
    for section in styles:
        name = _local(section.tag)
        if name == "numFmts":
            for item in section:
                codes[_whole(item.get("numFmtId"))] = item.get("formatCode") or ""
        elif name == "cellXfs":
            numbers = [_whole(xf.get("numFmtId")) for xf in section
                       if _local(xf.tag) == "xf"]
    return [(_format_kind(codes.get(number, "")), codes.get(number, "")) for number in numbers]


def _whole(text) -> int:
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def _moment(number, shows, date1904):
    """The date, date with time or time a cell shows for its number; the number itself when
    it stands for none, as a negative one or, counted from 1900, one below 1."""
    try:
        if shows == "time":
            if not 0 <= number < 1:
                return number
            return (datetime.min + timedelta(seconds=round(number * 86400))).time()
        days = int(number)
        if date1904:
            epoch = _EPOCH_1904
            if number < 0:
                return number
        else:
            epoch = _EPOCH_1900
            if number < 1:
                return number
            if number < 60:
                # Excel counts a 29 February 1900, which did not exist
                days += 1
        if shows == "date":
            return (epoch + timedelta(days=days)).date()
        return epoch + timedelta(days=days, seconds=round((number - int(number)) * 86400))
    except (OverflowError, ValueError):
        return number


# the column separators a CSV is tried with, the earlier one chosen when two fit as well
_SEPARATORS = (";", "\t", ",")
# how much of a CSV is read to find its separator, in characters
_SAMPLE = 65536


def _read_csv(path):
    with open(path, "rb") as fp:
        raw = fp.read()
    text = _decoded(raw)
    separator = _separator(text[:_SAMPLE], complete=len(text) <= _SAMPLE)
    # newline="": the csv module finds the line ends itself, a carriage return alone
    # included, and keeps the ones inside a quoted cell
    lines = csv.reader(io.StringIO(text, newline=""), delimiter=separator)
    return [[cell if cell != "" else None for cell in row] for row in lines]


def _decoded(raw: bytes) -> str:
    # Excel writes "Unicode Text" in UTF-16 with a byte order mark, "CSV UTF-8" with a BOM
    # and plain "CSV" in the Windows code page; latin-1 decodes any byte, so the loop always
    # ends with text
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _separator(sample, complete) -> str:
    """The column separator of a CSV text: of ';', tab and ',', the one that the largest share
    of its lines hold the same number of times, outside quoted cells. A comma between two
    digits can be a decimal comma: such commas do not count when ';' or tab fit at least half
    of the lines, and a text whose every comma is one has no ',' columns. An incomplete
    sample leaves out its cut last line."""
    lines = []
    # of ';', of tab, of ',', and of ',' not between two digits
    counts = [0, 0, 0, 0]
    quoted = filled = False
    for position, char in enumerate(sample):
        if char == '"':
            quoted = not quoted
            filled = True
        elif quoted:
            continue
        elif char in "\r\n":
            if filled:
                lines.append(counts)
            counts, filled = [0, 0, 0, 0], False
        else:
            filled = True
            if char == ";":
                counts[0] += 1
            elif char == "\t":
                counts[1] += 1
            elif char == ",":
                counts[2] += 1
                if not (sample[position - 1:position].isdigit()
                        and sample[position + 1:position + 2].isdigit()):
                    counts[3] += 1
    if filled and complete:
        lines.append(counts)
    fits = [_share([line[i] for line in lines]) for i in range(4)]
    candidates = [(fits[0], 0), (fits[1], -1)]
    if fits[3][0]:
        others_fit = max(fits[0][0], fits[1][0]) >= 0.5
        candidates.append((fits[3] if others_fit else fits[2], -2))
    share, order = max((fit[0], order) for fit, order in candidates)
    return _SEPARATORS[-order] if share else _SEPARATORS[0]


def _share(counts):
    """(the share of counts equal to their most frequent value above 0, that value); (0, 0)
    when none is above 0."""
    positive = [count for count in counts if count > 0]
    if not positive:
        return 0.0, 0
    value = Counter(positive).most_common(1)[0][0]
    return positive.count(value) / len(counts), value
