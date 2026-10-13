# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Demo data for the README recordings: a made-up village sector. Every person, number and
boundary is invented; only the SIRUTA code (UAT Cristian, Brașov) is real.

    "C:/Program Files/QGIS 3.40.6/bin/python-qgis-ltr.bat" scripts/build_demo_data.py <folder>

Writes into <folder>:
    livrare_v1/             CGXML delivery: a street with lots on both sides, a tarla behind
    livrare_v2/             the same delivery after a sale, a moved boundary, a new annex,
                            one imobil removed and one added, for the Comparator
    context.gpkg            Intravilan, Limite sectoare, Contur tarla 12
    imobile.gpkg            livrare_v1 converted, for the tools that work on layers
    parcele_tarla_12.xlsx   the parcel table for Diminuare Suprafețe
    demo_gol.qgz            the outlines only, for recording Conversie CGXML
    demo.qgz                the outlines and the converted layers, for the other tools

livrare_v1 carries the problems Verificare Topologică and Topologie Construcții find: an
overlap, a gap, an unsnapped corner, two buildings over their limit, an annex standing mostly
on the neighbouring imobil and a self-intersecting parcel.
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "test"))
import helpers  # noqa: E402  (puts the plugins folder on sys.path)
import synthetic  # noqa: E402
from synthetic import Building, Land  # noqa: E402

SIRUTA = "40900"
STREET = "Principală"
# the street axis, in Stereo 70; open ground west of Cristian
E0, N0 = 532400.0, 455300.0
NORTH_WIDTHS = (22, 25, 20, 24, 23, 21, 26, 22)
SOUTH_WIDTHS = (24, 21, 23, 25, 20, 22, 24, 23, 22)    # the last lot only exists in v2
NORTH_DEPTH, SOUTH_DEPTH = 65.0, 55.0
NORTH_SKEW, SOUTH_SKEW = 0.08, -0.05                   # side lines lean, metres per metre
HALF_ROAD = 4.0
TARLA_GAP, TARLA_DEPTH = 30.0, 180.0
TARLA_WIDTHS = (18, 32, 25, 15, 28, 22, 35, 20, 26, 19)

NORTH_OWNERS = (("POPESCU ION", "POPESCU MARIA"), ("IONESCU VASILE",), ("STAN ANA",),
                ("DUMITRESCU GHEORGHE",), ("MARIN CONSTANTIN", "MARIN ELENA"),
                ("RADU IOANA",), ("NEIDENTIFICAT",), ("GHEORGHE MIHAI",))
SOUTH_OWNERS = (("CONSTANTIN ELENA",), ("TUDOR ADRIAN",), ("POPA NICOLAE", "POPA RODICA"),
                ("LUPU DANIEL",), ("NEAGU FLORIN",), ("ENACHE CRISTINA",),
                ("BARBU LIVIU",), ("SIMION IULIA",), ("DOBRE ANDREI",))
TARLA_OWNERS = ("VOICU PETRE", "OPREA MARIN", "MOCANU LUCIA", "ZAHARIA DORIN", "PAVEL ANA",
                "STOICA VIOREL", "ANGHEL MIRCEA", "COMAN ILEANA", "TOMA GRIGORE", "PREDA SORIN")


def edges(widths):
    xs = [0.0]
    for width in widths:
        xs.append(xs[-1] + width)
    return xs


NX, SX, TX = edges(NORTH_WIDTHS), edges(SOUTH_WIDTHS), edges(TARLA_WIDTHS)


def north(x, v):
    """A point at x along the street front, v metres back into the north row."""
    return (round(E0 + x + NORTH_SKEW * v, 3), round(N0 + HALF_ROAD + v, 3))


def south(x, v):
    return (round(E0 + x + SOUTH_SKEW * v, 3), round(N0 - HALF_ROAD - v, 3))


def lot_ring(place, left, right, depth, left_shift=0.0, right_shift=0.0, short_corner=0.0):
    """A lot between two side lines; a shift moves a whole side, short_corner pulls the back
    left corner towards the street."""
    return [place(left + left_shift, 0), place(right + right_shift, 0),
            place(right + right_shift, depth), place(left + left_shift, depth - short_corner)]


def block(place, left, u, v, width, depth):
    """A building drawn in the lot's own frame: u along the front from the left side, v back."""
    return [place(left + u, v), place(left + u + width, v), place(left + u + width, v + depth),
            place(left + u, v + depth)]


def village(version):
    lands = []
    for i, owners in enumerate(NORTH_OWNERS):
        if version == 2 and i == 7:
            continue                                    # removed in v2
        ring = lot_ring(north, NX[i], NX[i + 1], NORTH_DEPTH,
                        right_shift=0.35 if i == 3 else 0.0,          # overlaps lot 4
                        short_corner=0.08 if i == 6 else 0.0)         # unsnapped corner
        house_u = {1: -0.7, 5: -0.06}.get(i, 1.5)                    # lots 1 and 5 cross the side
        buildings = [Building(1, [block(north, NX[i], house_u, 2.0, 10.0, 9.0)], levels=2),
                     Building(2, [block(north, NX[i], 4.0, 34.0, 5.0, 4.0)], destination="CA")]
        if version == 2 and i == 5:
            buildings.append(Building(3, [block(north, NX[i], 9.0, 44.0, 6.0, 4.0)],
                                      destination="CA"))
        mixed = i in (2, 3)                              # their back part is extravilan
        lands.append(Land(
            rings=[ring], cadgenno=str(1101 + i), ie=str(50101 + i) if i % 2 == 0 else "",
            sector="1", sirsup=SIRUTA, siruta=SIRUTA, street=STREET,
            postal_number=str(1 + 2 * i), legal_area=round(synthetic.net_area([ring]) * 1.02),
            enclosed=True,
            owners=("ALEXE VALENTIN",) if version == 2 and i == 2 else owners,
            creditor="BANCA EXEMPLU SA" if i == 4 else "",
            parcels=(("CC", True), ("A", False)) if mixed else (("CC", True),),
            buildings=buildings))

    for i, owners in enumerate(SOUTH_OWNERS):
        if version == 1 and i == 8:
            continue                                    # added in v2
        depth = SOUTH_DEPTH + (1.5 if version == 2 and i == 1 else 0.0)   # moved in v2
        ring = lot_ring(south, SX[i], SX[i + 1], depth,
                        left_shift=0.3 if i == 5 else 0.0)            # gap next to lot 4
        width = SOUTH_WIDTHS[i]
        buildings = [Building(1, [block(south, SX[i], 1.5, 2.0, 11.0, 8.0)])]
        if i == 2:
            # the annex stands mostly on lot 3
            buildings.append(Building(2, [block(south, SX[i], width - 1.5, 30.0, 5.0, 4.0)],
                                      destination="CA"))
        lands.append(Land(
            rings=[ring], cadgenno=str(1201 + i), ie=str(50201 + i) if i % 3 == 0 else "",
            sector="2", sirsup=SIRUTA, siruta=SIRUTA, street=STREET,
            postal_number=str(2 + 2 * i), legal_area=round(synthetic.net_area([ring]) * 0.99),
            owners=owners, noted="TUDOR ADRIAN" if i == 6 else "",
            buildings=buildings))

    road = [(E0 - 10, N0 - HALF_ROAD), (E0 + max(NX[-1], SX[-1]) + 10, N0 - HALF_ROAD),
            (E0 + max(NX[-1], SX[-1]) + 10, N0 + HALF_ROAD), (E0 - 10, N0 + HALF_ROAD)]
    lands.append(Land(rings=[road], cadgenno="1001", sector="0", sirsup=SIRUTA, siruta=SIRUTA,
                      street=STREET, owners=("COMUNA CRISTIAN",), parcels=(("DR", True),)))

    for i, owner in enumerate(TARLA_OWNERS):
        back = NORTH_DEPTH + TARLA_GAP + TARLA_DEPTH
        front = NORTH_DEPTH + TARLA_GAP
        ring = [north(TX[i], front), north(TX[i + 1], front), north(TX[i + 1], back),
                north(TX[i], back)]
        if i == 6:
            # a stray last vertex in front of the parcel: the closing edges cross its front
            ring.append(north(TX[i] + 0.2 * TARLA_WIDTHS[i], front - 3.0))
        lands.append(Land(
            rings=[ring], cadgenno=str(1301 + i), sector="3", sirsup=SIRUTA, siruta=SIRUTA,
            plot="12", owners=(owner,), parcels=(("A", False),),
            legal_area=round(synthetic.net_area([ring]) * 1.04, -1)))
    return lands


def write_deliveries(folder):
    for version in (1, 2):
        target = os.path.join(folder, f"livrare_v{version}")
        shutil.rmtree(target, ignore_errors=True)
        for land in village(version):
            synthetic.write(target, f"{land.cadgenno}.cgxml", land)


def outlines():
    """{layer name: (fields, [(values, ring)])} of the context layers."""
    # past the east end of the road, which the lot added in v2 lengthens
    end = max(NX[-1], SX[-1]) + 15
    top = NORTH_DEPTH + 2
    bottom = N0 - HALF_ROAD - SOUTH_DEPTH - 5
    # the notch follows the side lines of north lots 2 and 3, so only those two are partial
    intravilan = [(E0 - 15, bottom), (E0 + end, bottom), (E0 + end, N0 + HALF_ROAD + top),
                  north(NX[4], top), north(NX[4], 43.0), north(NX[2], 43.0),
                  north(NX[2], top), (E0 - 15, N0 + HALF_ROAD + top)]
    # sector 1 reaches 3 m past its last lot, ground the delivery leaves uncovered
    sector1 = lot_ring(north, NX[0], NX[-1] + 3.0, NORTH_DEPTH)
    sector2 = lot_ring(south, SX[0], SX[-2], SOUTH_DEPTH)
    road_end = E0 + max(NX[-1], SX[-1]) + 10
    sector0 = [(E0 - 10, N0 - HALF_ROAD), (road_end, N0 - HALF_ROAD),
               (road_end, N0 + HALF_ROAD), (E0 - 10, N0 + HALF_ROAD)]
    front, back = NORTH_DEPTH + TARLA_GAP, NORTH_DEPTH + TARLA_GAP + TARLA_DEPTH
    tarla = [north(TX[0], front), north(TX[-1], front), north(TX[-1], back),
             north(TX[0], back)]
    return {
        "Intravilan": ("nume:string", [(["Intravilan Cristian"], intravilan)]),
        "Limite sectoare": ("sector:integer", [([0], sector0), ([1], sector1), ([2], sector2),
                                               ([3], tarla)]),
        "Contur tarla 12": ("tarla:string", [(["12"], tarla)]),
    }


def write_parcel_table(path):
    from cadex.services import xlsx
    wb = xlsx.Workbook()
    sheet = wb.add_sheet("Tarla 12")
    bold = wb.style(bold=True)
    for col, title in enumerate(("Nr. crt.", "Proprietar", "Nr. parcelă",
                                 "Suprafața din acte (mp)", "CF/IE"), start=1):
        sheet.set(1, col, title, bold)
    for i, owner in enumerate(TARLA_OWNERS):
        ring = [(TX[i], 0), (TX[i + 1], 0), (TX[i + 1], TARLA_DEPTH), (TX[i], TARLA_DEPTH)]
        values = (i + 1, owner, f"A 12/{i + 1}", round(synthetic.net_area([ring]) * 1.04, -1),
                  f"{50301 + i}" if i in (1, 7) else None)
        for col, value in enumerate(values, start=1):
            if value is not None:
                sheet.set(i + 2, col, value)
    for col, width in zip(range(1, 6), (9, 20, 12, 24, 10)):
        sheet.widths[col] = width
    wb.save(path)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = os.path.abspath(sys.argv[1])
    os.makedirs(folder, exist_ok=True)
    write_deliveries(folder)
    write_parcel_table(os.path.join(folder, "parcele_tarla_12.xlsx"))

    helpers.start_qgis(gui=False)
    from qgis.core import (  # type: ignore
        QgsCoordinateTransformContext, QgsFeature, QgsFillSymbol, QgsGeometry, QgsPointXY,
        QgsProject, QgsReferencedRectangle, QgsSingleSymbolRenderer, QgsVectorFileWriter,
        QgsVectorLayer,
    )
    from cadex.services import cgxml_columns, cgxml_export, layer_style, stereo70

    context = os.path.join(folder, "context.gpkg")
    if os.path.exists(context):
        os.remove(context)
    for i, (name, (fields, rows)) in enumerate(outlines().items()):
        layer = QgsVectorLayer(f"Polygon?crs=EPSG:3844&field={fields}", name, "memory")
        provider = layer.dataProvider()
        for values, ring in rows:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]]))
            feature.setAttributes(values)
            provider.addFeatures([feature])
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = name
        if i:
            options.actionOnExistingFile = (
                QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer)
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, context, QgsCoordinateTransformContext(), options)[0]
        assert error == QgsVectorFileWriter.WriterError.NoError, name

    names = {"imobile": "Imobile", "constructii": "Construcții"}
    layouts = {w: cgxml_columns.default_layout(w) for w in names}
    task = cgxml_export.ConversionTask(
        input_paths=os.path.join(folder, "livrare_v1"), recursive=False, output_dir=folder,
        base_name="imobile", fmt="gpkg", layouts=layouts, layer_names=names, label_config={})
    assert task.run(), task.error_msg

    def outline_style(layer, color, width, dash=False):
        props = {"color": "0,0,0,0", "outline_color": color, "outline_width": str(width),
                 "outline_width_unit": "MM"}
        if dash:
            props["outline_style"] = "dash"
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(props)))

    project = QgsProject.instance()
    for name, with_converted in (("demo_gol.qgz", False), ("demo.qgz", True)):
        project.clear()
        project.setCrs(stereo70.crs())
        layers = []
        for layer_name, color, width, dash in (("Contur tarla 12", "#8A5A00", 0.6, False),
                                               ("Limite sectoare", "#6B7280", 0.4, True),
                                               ("Intravilan", "#D64545", 0.7, True)):
            layer = QgsVectorLayer(f"{context}|layername={layer_name}", layer_name, "ogr")
            assert layer.isValid(), layer_name
            outline_style(layer, color, width, dash)
            layers.append(layer)
        if with_converted:
            for which in ("imobile", "constructii"):
                layer = QgsVectorLayer(task.outputs[which], names[which], "ogr")
                assert layer.isValid(), which
                layer_style.style_cadastral(layer, which, layer_style.cadastral_label_parts(
                    which, cgxml_columns.output_names(layouts[which], False)))
                layers.append(layer)
        project.addMapLayers(layers)
        root = project.layerTreeRoot()
        # each one moved to the top in turn: construcții, added last, ends up first
        for layer in layers:
            node = root.findLayer(layer.id())
            root.insertChildNode(0, node.clone())
            root.removeChildNode(node)
        # the village, where most of the tools have something to show
        extent = next(lyr for lyr in layers if lyr.name() == "Intravilan").extent()
        extent.grow(15)
        project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, project.crs()))
        assert project.write(os.path.join(folder, name)), name
    project.clear()
    print(f"demo data written to {folder}")
    helpers.stop_qgis()


if __name__ == "__main__":
    main()
