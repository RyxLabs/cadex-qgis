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
from html import escape

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
SPREADSHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml"

# built-in number formats, no numFmt entry needed
BUILTIN_FORMATS = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "@": 49}

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")

MAX_SHEET_NAME = 31
MAX_CELL_TEXT = 32767  # Excel's limit per cell


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


def sheet_name(text: str) -> str:
    """A valid worksheet name: none of []:*?/\\, at most 31 characters, not empty."""
    return _SHEET_BAD.sub("-", str(text))[:MAX_SHEET_NAME].strip("'") or "Foaie"


def _xml_text(value: str) -> str:
    return escape(_CONTROL.sub(" ", value)[:MAX_CELL_TEXT], quote=False)


def _attr(value: str) -> str:
    return escape(str(value))


class _Styles:
    """Deduplicated fonts, fills, borders and cell formats for styles.xml."""

    def __init__(self):
        self.fonts: list = [("", False, False, 11.0)]
        self.fills: list = [None, "gray125"]  # the format reserves the first two fills
        self.borders: list = [None]
        self.formats: dict = {}
        self.xfs: list = [(0, 0, 0, 0, None)]

    def _index(self, items, item):
        if item not in items:
            items.append(item)
        return items.index(item)

    def get(self, *, bold=False, italic=False, color="", size=11.0, fill=None,
            border=None, halign=None, valign=None, wrap=False, number=None):
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

    def __init__(self, name):
        self.name = name
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

    def style(self, **kwargs) -> int:
        return self.styles.get(**kwargs)

    def add_sheet(self, name, index=None) -> Sheet:
        base = sheet_name(name)
        taken = {s.name.lower() for s in self.sheets}
        unique, n = base, 2
        while unique.lower() in taken:
            suffix = f" ({n})"
            unique = base[:MAX_SHEET_NAME - len(suffix)] + suffix
            n += 1
        sheet = Sheet(unique)
        if index is None:
            self.sheets.append(sheet)
        else:
            self.sheets.insert(index, sheet)
        return sheet

    def save(self, path):
        """Write the workbook to a temporary file in path's folder, then move it onto path:
        when writing fails, the file already at path stays whole. An error names path."""
        if not self.sheets:
            self.add_sheet("Foaie")
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


def _sheet_paths(zf):
    """[(sheet name, path inside the zip)] in workbook order."""
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))  # nosec B314
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))  # nosec B314
    targets = {}
    for rel in rels:
        target = rel.get("Target", "")
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        targets[rel.get("Id")] = target
    result = []
    for element in workbook.iter():
        if _local(element.tag) == "sheet":
            rid = next((v for k, v in element.attrib.items() if _local(k) == "id"), None)
            if rid in targets:
                result.append((element.get("name"), targets[rid]))
    return result


def sheet_names(path):
    if path.lower().endswith(".csv"):
        return ["CSV"]
    with zipfile.ZipFile(path) as zf:
        return [name for name, _p in _sheet_paths(zf)]


def read_rows(path, sheet=None):
    """(sheet names, rows) of an .xlsx or .csv. Rows are lists of str, float or None, all as
    wide as the widest; empty rows are kept, so row numbers match Excel's."""
    names, rows, _hidden = read_table(path, sheet)
    return names, rows


def read_table(path, sheet=None):
    """(sheet names, rows, hidden row numbers): read_rows() and the Excel numbers (from 1) of
    the rows hidden in the sheet, by a filter or by hand. A CSV has no hidden rows."""
    if path.lower().endswith(".csv"):
        return ["CSV"], _read_csv(path), []
    with zipfile.ZipFile(path) as zf:
        sheets = _sheet_paths(zf)
        if not sheets:
            return [], [], []
        names = [name for name, _p in sheets]
        target = dict(sheets).get(sheet) or sheets[0][1]
        shared = _shared_strings(zf)
        rows = {}
        hidden = []
        with zf.open(target) as fp:
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
                    values[next_col] = _cell_value(cell, shared)
                rows[next_row] = values
                element.clear()
    width = max((max(v) for v in rows.values() if v), default=0)
    last = max(rows, default=0)
    return names, [[rows.get(r, {}).get(c) for c in range(1, width + 1)]
                   for r in range(1, last + 1)], hidden


def _cell_value(cell, shared):
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
        return float(value)
    except ValueError:
        return value


class _Semicolon(csv.excel):
    delimiter = ";"


def _read_csv(path):
    with open(path, "rb") as fp:
        raw = fp.read()
    # Excel writes "CSV UTF-8" with a BOM and plain "CSV" in the Windows code page; latin-1
    # decodes any byte, so the loop always ends with text
    text = ""
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    if _semicolon_columns(sample, complete=len(text) <= len(sample)):
        dialect = _Semicolon
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            dialect = _Semicolon if sample.count(";") >= sample.count(",") else csv.excel
    return [[cell if cell != "" else None for cell in row]
            for row in csv.reader(io.StringIO(text), dialect)]


def _semicolon_columns(sample, complete) -> bool:
    """Whether every line of sample holds the same number of ";", at least one: the layout a
    Romanian Excel exports, where "," is the decimal separator. csv.Sniffer returns ","
    when both occur once per line. An incomplete sample leaves out its cut last line."""
    lines = [line for line in sample.splitlines() if line.strip()]
    if not complete:
        lines = lines[:-1]
    counts = {line.count(";") for line in lines}
    return len(counts) == 1 and counts.pop() > 0
