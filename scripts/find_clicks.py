# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""List the likely clicks of a screen recording, as entries for CLIPS in build_demo_gifs.py.

The black arrow cursor is found in every frame by template matching. A click is the cursor
holding still while the screen changes right next to it: a button pressed, a list opening, a
tab switching. Windows' Snap To moves the cursor onto the default button of a new dialog;
that jump is not a click and is skipped. Hover highlights and wheel scrolling also change the
screen next to a still cursor, so check every candidate on the sheet before using it, and
find drags (several candidates along a moving cursor) by eye.

    python scripts/find_clicks.py <recording.mp4> --sheet clicks.png
    python scripts/find_clicks.py <recording.mp4> --from 1.2 --to 16.5

Needs numpy, Pillow and OpenCV (opencv-python-headless); ffmpeg as for build_demo_gifs.py.
"""
import argparse
import subprocess
import sys
from typing import IO, cast

import cv2
import numpy as np
from PIL import Image, ImageDraw

from build_demo_gifs import ARROW, QGIS_WINDOW, frame_rate, tool

# a change next to the cursor counts above this share of the box around it, in percent
LOCAL_CHANGE = 0.3
SNAP_JUMP = 250


def arrow_template():
    """(template, mask, pad): the black arrow and its 1px white outline, tip at (pad, pad)."""
    pad = 2
    fill = np.zeros((len(ARROW) + 2 * pad, 8 + 2 * pad), bool)
    for row, spans in enumerate(ARROW):
        for a, b in zip(spans[::2], spans[1::2]):
            fill[row + pad, a + pad:b + pad + 1] = True
    grown = fill.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(fill, dy, 0), dx, 1)
    template = np.where(fill, 0, 255).astype(np.float32)
    return template, grown.astype(np.float32), pad


def track(src: str, crop: tuple):
    """Per frame: (cursor tip or None, percent changed next to the cursor)."""
    x0, y0, w, h = crop
    template, mask, pad = arrow_template()
    n_mask = mask.sum()
    # converted before the crop: on yuv420p, crop rounds an odd x or y down
    reader = subprocess.Popen(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", f"format=gray,crop={w}:{h}:{x0}:{y0}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE)
    stdout = cast(IO[bytes], reader.stdout)
    rows, previous, previous_tip = [], None, None
    while True:
        raw = stdout.read(w * h)
        if len(raw) < w * h:
            break
        frame = np.frombuffer(raw, np.uint8).reshape(h, w)
        res = cv2.matchTemplate(frame.astype(np.float32), template, cv2.TM_SQDIFF, mask=mask)
        best, _worst, loc, _ = cv2.minMaxLoc(res)
        rms = (max(best, 0) / n_mask) ** 0.5
        found = rms < 45
        if not found and rms < 100:
            # on dark backgrounds only the dim 1px white outline shows: take the best match
            # when it clearly beats every other place
            others = res.copy()
            others[max(loc[1] - 12, 0):loc[1] + 12, max(loc[0] - 12, 0):loc[0] + 12] = np.inf
            found = rms < 0.8 * (max(others.min(), 0) / n_mask) ** 0.5
        tip = (loc[0] + pad, loc[1] + pad) if found else None
        local = 0.0
        if previous is not None and tip:
            changed = cv2.absdiff(frame, previous) > 30
            for p in (tip, previous_tip):
                if p:
                    changed[max(p[1] - 4, 0):p[1] + 22, max(p[0] - 4, 0):p[0] + 14] = False
            box = changed[max(tip[1] - 60, 0):tip[1] + 60, max(tip[0] - 150, 0):tip[0] + 150]
            local = box.mean() * 100 if box.size else 0.0
        rows.append((tip, local))
        previous, previous_tip = frame, tip
    reader.wait()
    return rows


def candidates(rows, fps: float, t_from: float, t_to: float):
    """(press frame, tip) for every still cursor with a change next to it."""
    found, i, last = [], 0, None
    while i < len(rows):
        tip = rows[i][0]
        if tip is None:
            i += 1
            continue
        snapped = last is not None and i - last[0] <= 2 and \
            abs(last[1][0] - tip[0]) + abs(last[1][1] - tip[1]) > SNAP_JUMP
        j = i
        while j + 1 < len(rows) and rows[j + 1][0] and \
                abs(rows[j + 1][0][0] - tip[0]) <= 1 and abs(rows[j + 1][0][1] - tip[1]) <= 1:
            j += 1
        last = (j, rows[j][0])
        if j > i and t_from <= i / fps <= t_to:
            changes = [k for k in range(i + 1, min(j + 2, len(rows))) if rows[k][1] > LOCAL_CHANGE]
            # a snapped cursor lands on a dialog that is still painting around it
            if changes and not (snapped and changes[0] <= i + 3):
                found.append((changes[0] - 1, tip))
        i = j + 1
    return found


def sheet(src: str, crop: tuple, presses, fps: float, out: str) -> None:
    """Close-ups of each candidate, 2 frames before the press and 5 after."""
    x0, y0, w, h = crop
    wanted = {f + d for f, _tip in presses for d in (-2, 5)}
    reader = subprocess.Popen(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", f"format=rgb24,crop={w}:{h}:{x0}:{y0}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE)
    stdout = cast(IO[bytes], reader.stdout)
    frames, n = {}, 0
    while wanted - frames.keys():
        raw = stdout.read(w * h * 3)
        if len(raw) < w * h * 3:
            break
        if n in wanted:
            frames[n] = Image.frombytes("RGB", (w, h), raw)
        n += 1
    reader.kill()
    cw, ch = 360, 180
    page = Image.new("RGB", (2 * cw + 4, len(presses) * (ch + 22)), "#222")
    draw = ImageDraw.Draw(page)
    for k, (f, (x, y)) in enumerate(presses):
        top = k * (ch + 22)
        draw.text((4, top + 4), f"#{k}  {f / fps:.2f}s  ({x + x0}, {y + y0})", fill="yellow")
        left = min(max(x - cw // 2, 0), w - cw)
        upper = min(max(y - ch // 2, 0), h - ch)
        for c, d in enumerate((-2, 5)):
            if f + d in frames:
                close = frames[f + d].crop((left, upper, left + cw, upper + ch))
                ImageDraw.Draw(close).ellipse([x - left - 12, y - upper - 12,
                                               x - left + 12, y - upper + 12], outline="red")
                page.paste(close, (c * (cw + 4), top + 20))
    page.save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording")
    ap.add_argument("--crop", default=",".join(map(str, QGIS_WINDOW)),
                    help="x,y,w,h of the area to search, the QGIS window by default")
    ap.add_argument("--from", dest="t_from", type=float, default=0.0, metavar="SECONDS")
    ap.add_argument("--to", dest="t_to", type=float, default=float("inf"), metavar="SECONDS")
    ap.add_argument("--sheet", metavar="PNG", help="write the before / after close-ups here")
    args = ap.parse_args()
    crop = tuple(int(v) for v in args.crop.split(","))
    if len(crop) != 4:
        sys.exit("--crop takes x,y,w,h")
    fps = frame_rate(args.recording)
    presses = candidates(track(args.recording, crop), fps, args.t_from, args.t_to)
    for k, (f, (x, y)) in enumerate(presses):
        print(f"            ({f / fps:.2f}, {x + crop[0]}, {y + crop[1]}),  # #{k}")
    if args.sheet and presses:
        sheet(args.recording, crop, presses, fps, args.sheet)
        print(f"{len(presses)} candidates, close-ups in {args.sheet}")


if __name__ == "__main__":
    main()
