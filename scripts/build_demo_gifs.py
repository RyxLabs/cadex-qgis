# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turn the OBS screen recordings of each tool into the README GIFs in docs/media.

Record on the demo data from scripts/build_demo_data.py only: real deliveries hold real
names and CNPs, and a GIF in the README is public.

Each clip is cut into segments with a playback speed. What carries the demo - a window
opening, the result layers landing - stays at 1x; cursor travel and form filling run at 2-3x.
A clip without segments yet is rendered whole at 1x. --motion prints how much each second
of a recording changes, the measure the 2-3x ranges are picked from.

Clicks get a translucent dot under the cursor, like the live click highlighter of the Vernier
GIFs; their times and places are listed per clip. Parts of the screen that show the recording
PC - folder names, the Office account - are blurred.

    python scripts/build_demo_gifs.py <folder with the .mp4 recordings>
    python scripts/build_demo_gifs.py <folder> --detect     # QGIS window rect per recording
    python scripts/build_demo_gifs.py <folder> --motion     # per-second motion profile
    python scripts/build_demo_gifs.py <folder> --only comparator

Needs numpy and Pillow. ffmpeg and ffprobe have to be on PATH, or CADEX_FFMPEG has to point
at ffmpeg with ffprobe next to it.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import IO, cast

import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "media")

# 1000px and 128 colours read the same as 1100px and 192 on the ~880px README column, at a
# quarter less weight. 20fps because GIF frame delays are whole hundredths of a second: 20fps
# is exactly 5, 15fps alternates 6 and 7.
GIF_WIDTH = 1000
GIF_FPS = 20
GIF_COLORS = 128

# the QGIS window in the 1920x1080 recordings, without its 1px border
QGIS_WINDOW = (1, 1, 1194, 1030)

# the click dot, sized in recording pixels and timed in GIF seconds, so a click in a 3x
# segment shows as long as one at 1x
DOT_COLOR = (0, 245, 240)
DOT_OPACITY = 0.6
DOT_DIAMETER = 32
DOT_HOLD = 0.35
DOT_FADE = 0.25
BLUR_RADIUS = 10

# the black Windows arrow of the recordings, tip at (0, 0): filled columns per row. Its pixels
# are copied back over the dot, so the cursor stays on top as with the live highlighter.
ARROW = ((0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (0, 6), (0, 7), (0, 4), (0, 1, 3, 4),
         (0, 0, 4, 5), (4, 5), (5, 6), (5, 6), (6, 7), (6, 7))

# GIF name -> recording file (None: not recorded yet), crop rect (x, y, w, h), None to detect
# it, and (start, end, speed) segments, None for the whole clip at 1x. A speed under 1 holds a
# result window long enough to read; a gap between two segments is cut.
# clicks: (time, x, y) of each press, in recording seconds and pixels; a list of them is a drag,
# held from the first point to the last. scripts/find_clicks.py proposes them. Windows has Snap
# To on in the recordings, so the cursor jumping onto a new window's default button is not a
# click.
# blur: (start, end, (x, y, w, h)) screen areas to blur.
CLIPS = {
    "conversie-cgxml": {
        "file": "conversie-cgxml.mp4",
        "crop": QGIS_WINDOW,
        "segments": [
            (0.8, 2.7, 1.0),     # the window opens
            (2.7, 6.3, 2.5),     # folder picker
            (6.3, 8.8, 2.0),     # 27 CGXML files found
            (8.8, 11.8, 1.0),    # Coloane tab
            (11.8, 13.0, 1.0),   # Convertește
            (13.0, 14.1, 0.45),  # result window
            (14.1, 15.6, 1.0),   # the layers on the map
            (15.6, 18.2, 2.0),   # opening the attribute table
            (18.2, 23.2, 1.25),  # imobile, scrolled to the persons
            (23.2, 31.0, 3.0),   # construcții
            (31.0, 32.2, 1.0),
        ],
        "clicks": [
            (1.57, 28, 166),     # CADex toolbar
            (3.70, 651, 393),    # Folder...
            (5.25, 556, 397),    # livrare_v1
            (6.17, 666, 784),    # Select Folder
            (9.10, 358, 335),    # Coloane
            (11.67, 312, 333),   # General
            (12.57, 697, 772),   # Convertește
            (14.23, 778, 389),   # closing the result window
            (16.40, 109, 572),   # Imobile
            (16.90, 109, 572),   # right click
            (18.20, 189, 792),   # Open Attribute Table
            [(19.45, 489, 631), (19.63, 517, 638), (19.80, 606, 643), (20.00, 669, 648),
             (20.23, 734, 648), (20.50, 776, 651), (20.73, 814, 652), (21.00, 842, 650),
             (21.43, 864, 649), (21.83, 895, 648), (22.13, 908, 648), (22.40, 912, 648)],
            (23.50, 964, 223),   # closing the table
            (24.43, 206, 592),   # Construcții
            (24.85, 206, 592),   # right click
            (26.03, 298, 861),   # Open Attribute Table
            [(27.50, 488, 629), (27.73, 544, 626), (28.00, 587, 626), (28.27, 620, 628),
             (28.53, 664, 632), (28.80, 723, 629), (29.07, 791, 624), (29.33, 843, 621),
             (29.60, 883, 620), (29.73, 894, 621)],
            (30.95, 969, 228),   # closing the table
        ],
    },
    "verificare-topologica": {
        "file": "validare-topo.mp4",
        "crop": QGIS_WINDOW,
        "segments": [
            (1.2, 2.7, 1.0),     # the window opens
            (2.7, 9.6, 2.5),     # sector outlines chosen
            (9.6, 10.2, 1.0),    # Verifică
            (10.2, 11.7, 0.5),   # result window
            (11.7, 13.4, 1.25),  # the problem layers land
            (13.4, 19.6, 3.0),   # legend clicks
            (19.6, 28.15, 1.5),  # zooming to problems
            (28.9, 30.4, 1.0),   # overview; the zoom-out and back before it is cut
        ],
        "clicks": [
            (1.57, 192, 172),    # CADex toolbar
            (4.30, 284, 594),    # Limitele Sectoarelor, expanded
            (4.93, 299, 588),    # and ticked
            (5.70, 403, 659),    # Layer cu limitele
            (7.13, 434, 705),    # limita-sectoare
            (9.63, 788, 870),    # Verifică
            (11.60, 812, 353),   # closing the result window
            (12.57, 912, 72),    # closing the window
            (13.73, 228, 590),   # the legend, one problem type at a time
            (16.03, 233, 617),
            (16.65, 233, 635),
            (17.33, 231, 656),
            (18.53, 196, 695),
            (19.37, 204, 717),
        ],
    },
    "raport-intravilan": {
        "file": "intra-extra.mp4",
        "crop": QGIS_WINDOW,
        "segments": [
            (1.3, 2.4, 1.0),     # the window opens, layers preselected
            (2.4, 7.8, 3.0),
            (7.8, 8.45, 1.0),    # Generează
            (8.45, 9.8, 0.5),    # result window
            (9.8, 11.2, 1.25),   # Arată pe hartă
            (11.2, 16.5, 1.0),   # the classified layer
        ],
        "clicks": [
            (1.48, 71, 169),     # CADex toolbar
            (8.08, 750, 955),    # Generează
            (9.71, 681, 638),    # Arată pe hartă
            (10.85, 893, 112),   # closing the window
        ],
    },
    "comparator": {"file": None, "crop": QGIS_WINDOW, "segments": None},
    "topologie-constructii": {
        "file": "topo-constructii.mp4",
        "crop": QGIS_WINDOW,
        "segments": [
            (1.2, 2.4, 1.0),     # the window opens
            (2.4, 6.4, 2.0),     # link column chosen
            (6.4, 7.4, 1.0),     # Corectează
            (7.4, 8.7, 0.5),     # result window
            (8.7, 9.6, 1.25),
            (9.6, 19.2, 1.25),   # the states on the map
        ],
        "clicks": [
            (1.45, 232, 172),    # CADex toolbar
            (3.97, 544, 475),    # Imobilul construcției
            (4.97, 539, 516),    # id
            (7.13, 802, 760),    # Corectează
            (8.53, 807, 546),    # Închide
            (9.33, 950, 115),    # closing the window
        ],
    },
    "diminuare-suprafete": {
        "file": "diminuare.mp4",
        "crop": QGIS_WINDOW,
        "segments": [
            (1.2, 2.3, 1.0),     # the window opens
            (2.3, 5.4, 3.0),     # table picked
            (5.4, 9.3, 1.5),     # columns and contour
            (9.3, 10.1, 1.0),    # Preia din geometrie
            (10.1, 11.9, 0.75),  # the reduced areas
            (11.9, 13.27, 1.5),  # Save dialog
            (13.27, 14.4, 0.6),  # saved, Deschide fișierul
            (14.4, 15.6, 3.0),   # Excel starting
            (15.6, 18.6, 1.25),  # Sumar sheet
            (18.6, 21.3, 1.0),   # Diminuare sheet
        ],
        "clicks": [
            (1.50, 125, 174),    # CADex toolbar
            (3.05, 933, 210),    # table browse button
            (4.30, 581, 297),    # parcele_t12.xlsx
            (5.20, 734, 640),    # Open
            (6.85, 657, 297),    # CF / IE column, opened
            (7.45, 657, 297),    # and closed
            (7.90, 669, 268),    # area column, opened
            (8.70, 669, 268),    # and closed
            (9.75, 895, 447),    # Preia din geometrie
            (11.60, 815, 960),   # Salvează Excel...
            (12.97, 750, 641),   # Save
            (13.98, 718, 589),   # Deschide fișierul
            (18.77, 262, 884),   # Diminuare sheet
        ],
        "blur": [
            # the Save dialog's side pane lists the PC's folders until it has faded out
            (11.96, 13.235, (306, 184, 170, 328)),
            (15.40, 21.40, (832, 146, 52, 36)),      # Excel account badge
        ],
    },
}


def tool(name: str) -> str:
    configured = os.environ.get("CADEX_FFMPEG")
    if configured:
        path = os.path.join(os.path.dirname(configured), name + os.path.splitext(configured)[1])
        if os.path.exists(path):
            return path
    found = shutil.which(name)
    if not found:
        sys.exit(f"{name} not found - put it on PATH or set CADEX_FFMPEG")
    return found


def probe(src: str) -> tuple:
    """(width, height, duration in seconds) of a recording."""
    out = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "csv=p=0:s=,", src],
        capture_output=True, text=True, check=True).stdout.split()
    width, height = (int(v) for v in out[0].split(",")[:2])
    return width, height, float(out[-1].strip(","))


def frame(src: str, at: float, width: int, height: int) -> bytes:
    raw = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", str(at), "-i", src,
         "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    if len(raw) < width * height:
        sys.exit(f"could not read a frame from {src} at {at}s")
    return raw


def detect_window(src: str) -> tuple:
    """The QGIS window in the middle frame, found by its long runs of light pixels.

    The Qt chrome is near-white and the desktop behind it is dark, so a row inside the window
    always holds one light run at least as wide as a panel; bright text outside never does.
    Record with QGIS in its light theme for this to work.
    """
    width, height, duration = probe(src)
    raw = frame(src, duration / 2, width, height)
    light, min_run = 170, 80

    def longest_run(values):
        best = run = 0
        for v in values:
            run = run + 1 if v > light else 0
            best = max(best, run)
        return best

    rows = [y for y in range(height)
            if longest_run(raw[y * width:(y + 1) * width]) >= min_run]
    if not rows:
        sys.exit(f"no QGIS window found in {src}")
    y0, y1 = rows[0], rows[-1]
    cols = [x for x in range(width)
            if longest_run([raw[y * width + x] for y in range(y0, y1 + 1)]) >= min_run]
    # even sizes, which the GIF scaler needs
    return cols[0], y0, (cols[-1] - cols[0] + 1) // 2 * 2, (y1 - y0 + 1) // 2 * 2


def motion(src: str) -> list:
    """Mean absolute change between frames one second apart, per second, on a small grey
    copy: 0 is a still screen, a dialog opening reads in the tens."""
    small_w, small_h = 160, 90
    raw = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src, "-vf",
         f"fps=1,scale={small_w}:{small_h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    size = small_w * small_h
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    return [sum(abs(a - b) for a, b in zip(previous, current)) / size
            for previous, current in zip(frames, frames[1:])]


class Timeline:
    """Maps between recording time and GIF time through the segments."""

    def __init__(self, segments):
        self.parts = []
        t = 0.0
        for start, end, speed in segments:
            self.parts.append((t, start, end, speed))
            t += (end - start) / speed
        self.length = t

    def source(self, t_gif: float) -> float:
        for gif_start, start, end, speed in self.parts:
            if t_gif < gif_start + (end - start) / speed:
                return start + (t_gif - gif_start) * speed
        return self.parts[-1][2]

    def gif(self, t_source: float):
        """None for a moment that is cut."""
        for gif_start, start, end, speed in self.parts:
            if start <= t_source <= end:
                return gif_start + (t_source - start) / speed
        return None


def _arrow_masks():
    fill = np.zeros((len(ARROW) + 2, 10), bool)
    for row, spans in enumerate(ARROW):
        for a, b in zip(spans[::2], spans[1::2]):
            fill[row + 1, a + 1:b + 2] = True
    grown = fill.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(fill, dy, 0), dx, 1)
    # offsets from the tip, which sits at (1, 1) in the masks
    return np.argwhere(fill) - 1, np.argwhere(grown & ~fill) - 1


FILL, OUTLINE = _arrow_masks()


def _dot_alpha() -> np.ndarray:
    r = DOT_DIAMETER / 2
    yy, xx = np.mgrid[0:DOT_DIAMETER, 0:DOT_DIAMETER] + 0.5
    edge = np.clip(r - np.hypot(xx - r, yy - r) + 0.5, 0, 1)
    return (edge * DOT_OPACITY)[..., None]


DOT = _dot_alpha()


class Press:
    """One click or drag: where the dot is and how strong, at a GIF time."""

    def __init__(self, entry, timeline, crop):
        points = [entry] if isinstance(entry, tuple) else list(entry)
        self.points = [(t, x - crop[0], y - crop[1]) for t, x, y in points]
        self.timeline = timeline
        self.down = timeline.gif(points[0][0])
        self.up = timeline.gif(points[-1][0])
        if self.down is None or self.up is None:
            raise ValueError(f"the click at {points[0][0]}s falls in a cut")
        # the next press ends the dot early: in a 3x segment several clicks would show at once
        self.until = float("inf")

    def at(self, t_gif: float):
        """(x, y, strength) or None."""
        if t_gif < self.down or t_gif >= self.until:
            return None
        if t_gif <= self.up:
            t, (x, y) = self.timeline.source(t_gif), self.points[0][1:]
            for (t0, x0, y0), (t1, x1, y1) in zip(self.points, self.points[1:]):
                if t0 <= t <= t1:
                    k = (t - t0) / (t1 - t0)
                    x, y = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
            return round(x), round(y), 1.0
        age = t_gif - self.up - DOT_HOLD
        if age > DOT_FADE:
            return None
        return self.points[-1][1], self.points[-1][2], min(1.0, 1 - age / DOT_FADE)


def _arrow_tip(img: np.ndarray, x: int, y: int):
    """The cursor's tip within 4px of (x, y), if the arrow is drawn there: dark fill inside a
    light outline, which holds on light and on dark backgrounds."""
    h, w = img.shape[:2]
    best = None
    for ty in range(y - 4, y + 5):
        for tx in range(x - 4, x + 5):
            fy, fx = FILL[:, 0] + ty, FILL[:, 1] + tx
            oy, ox = OUTLINE[:, 0] + ty, OUTLINE[:, 1] + tx
            if min(fy.min(), oy.min(), fx.min(), ox.min()) < 0 or \
                    max(fy.max(), oy.max()) >= h or max(fx.max(), ox.max()) >= w:
                continue
            fill = img[fy, fx].mean()
            if fill < 70 and img[oy, ox].mean() > 140 and (best is None or fill < best[0]):
                best = (fill, tx, ty)
    return best and best[1:]


def draw_dot(img: np.ndarray, x: int, y: int, strength: float) -> None:
    h, w = img.shape[:2]
    tip = _arrow_tip(img, x, y)
    cursor = None
    if tip:
        cy, cx = np.concatenate([FILL, OUTLINE]).T
        cy, cx = cy + tip[1], cx + tip[0]
        cursor = (cy, cx, img[cy, cx].copy())
    r = DOT_DIAMETER // 2
    x0, y0 = x - r, y - r
    a0, b0 = max(x0, 0), max(y0, 0)
    a1, b1 = min(x0 + DOT_DIAMETER, w), min(y0 + DOT_DIAMETER, h)
    if a0 >= a1 or b0 >= b1:
        return
    alpha = DOT[b0 - y0:b1 - y0, a0 - x0:a1 - x0] * strength
    region = img[b0:b1, a0:a1].astype(np.float32)
    img[b0:b1, a0:a1] = (region * (1 - alpha) + np.array(DOT_COLOR) * alpha).astype(np.uint8)
    if cursor:
        img[cursor[0], cursor[1]] = cursor[2]


def blur(img: np.ndarray, rect: tuple) -> None:
    x, y, w, h = rect
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, img.shape[1]), min(y + h, img.shape[0])
    if x0 < x1 and y0 < y1:
        part = Image.fromarray(img[y0:y1, x0:x1]).filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
        img[y0:y1, x0:x1] = np.asarray(part)


def _dot_shades() -> list:
    """The dot over white, over the grey of the Qt panels, over the dark file dialogs, and half
    faded. A palette made from the frames alone rounds the dot to a dull grey-green: it covers
    too few pixels to get colours of its own."""
    shades = []
    for background, strength in ((250, 1.0), (225, 1.0), (30, 1.0), (250, 0.5)):
        a = DOT_OPACITY * strength
        shades.append(tuple(round(c * a + background * (1 - a)) for c in DOT_COLOR))
    return shades


DOT_SHADES = _dot_shades()


def _add_shades(palette: str) -> None:
    """Write the dot shades over the duplicate entries palettegen pads its 256 slots with."""
    colors = np.array(Image.open(palette).convert("RGBA")).reshape(-1, 4)
    seen, shades = set(), list(DOT_SHADES)
    for i in range(len(colors)):
        color = tuple(colors[i])
        if color in seen and shades:
            colors[i] = shades.pop(0) + (255,)
        seen.add(color)
    if shades:
        sys.exit(f"no room for the dot colours in {palette}")
    Image.fromarray(colors.reshape(16, 16, 4), "RGBA").save(palette)


def frame_rate(src: str) -> float:
    rate = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", src],
        capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def _frames(src, crop, timeline, presses, areas):
    """The cut, cropped frames at GIF timing, with the blurs and the dots, as raw RGB.

    Each GIF frame takes the recording frame the timeline names. ffmpeg's trim and concat
    drift by a frame or two at every speed change, which puts a dot beside a moving cursor."""
    x, y, w, h = crop
    fps = frame_rate(src)
    # RGB before the crop: on the recording's yuv420p, crop rounds an odd x or y down
    reader = subprocess.Popen(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", src,
         "-vf", f"format=rgb24,crop={w}:{h}:{x}:{y}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE)
    stdout = cast(IO[bytes], reader.stdout)
    size = w * h * 3
    current, index = b"", -1
    for n in range(round(timeline.length * GIF_FPS)):
        t_gif = n / GIF_FPS
        t_source = timeline.source(t_gif)
        while index < round(t_source * fps):
            raw = stdout.read(size)
            if len(raw) < size:
                break
            current, index = raw, index + 1
        if not current:
            sys.exit(f"ffmpeg could not read {src}")
        img = np.frombuffer(current, np.uint8).reshape(h, w, 3).copy()
        for t0, t1, rect in areas:
            if t0 <= t_source <= t1:
                blur(img, rect)
        for press in presses:
            dot = press.at(t_gif)
            if dot:
                draw_dot(img, *dot)
        yield img.tobytes()
    reader.kill()
    reader.wait()


def _encode(frames, size: tuple, args: list) -> None:
    """ffmpeg reading the raw frames from stdin, then args."""
    w, h = size
    proc = subprocess.Popen(
        [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(GIF_FPS), "-i", "-"] + args,
        stdin=subprocess.PIPE)
    stdin = cast(IO[bytes], proc.stdin)
    for data in frames:
        stdin.write(data)
    stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed on the GIF frames")


def render(src: str, dst: str, crop: tuple, segments: list, clicks=(), blurs=()) -> None:
    """Cut, crop and slow or speed up with ffmpeg, draw the dots and blurs here, and let ffmpeg
    scale the frames and make the GIF: one pass for the palette, one for the GIF."""
    x, y, w, h = crop
    timeline = Timeline(segments)
    presses = sorted((Press(entry, timeline, crop) for entry in clicks), key=lambda p: p.down)
    for press, following in zip(presses, presses[1:]):
        press.until = following.down
    areas = [(t0, t1, (bx - x, by - y, bw, bh)) for t0, t1, (bx, by, bw, bh) in blurs]
    scale = f"scale={GIF_WIDTH}:{round(GIF_WIDTH * h / w / 2) * 2}:flags=lanczos"
    colors = GIF_COLORS - (len(DOT_SHADES) if presses else 0)
    with tempfile.TemporaryDirectory() as tmp:
        palette = os.path.join(tmp, "palette.png")
        _encode(_frames(src, crop, timeline, presses, areas), (w, h),
                ["-vf", f"{scale},palettegen=max_colors={colors}:stats_mode=diff", "-y", palette])
        if presses:
            _add_shades(palette)
        # diff_mode=rectangle only re-encodes the part of the frame that moved, most of the
        # win on screen captures
        _encode(_frames(src, crop, timeline, presses, areas), (w, h),
                ["-i", palette, "-filter_complex",
                 f"[0:v]{scale}[s];[s][1:v]paletteuse=dither=bayer:bayer_scale=5:"
                 f"diff_mode=rectangle", "-loop", "0", "-y", dst])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="folder holding the .mp4 recordings")
    ap.add_argument("--detect", action="store_true",
                    help="print the detected QGIS window rect of each recording and exit")
    ap.add_argument("--motion", action="store_true",
                    help="print the per-second motion profile of each recording and exit")
    ap.add_argument("--only", metavar="NAME", help="just this clip")
    args = ap.parse_args()

    total = 0.0
    for name, clip in CLIPS.items():
        if args.only and name != args.only:
            continue
        if not clip["file"]:
            print(f"{name:24s} SKIPPED - not recorded yet")
            continue
        src = os.path.join(args.source, clip["file"])
        if not os.path.exists(src):
            print(f"{name:24s} SKIPPED - {src} not found")
            continue
        if args.detect:
            print(f"{name:24s} {detect_window(src)}")
            continue
        if args.motion:
            profile = motion(src)
            print(f"{name}:")
            for second, value in enumerate(profile):
                print(f"  {second:3d}-{second + 1:<3d} {value:6.1f} {'#' * min(int(value), 60)}")
            continue
        crop = clip["crop"] or detect_window(src)
        segments = clip["segments"] or [(0, probe(src)[2], 1.0)]
        os.makedirs(OUT_DIR, exist_ok=True)
        dst = os.path.join(OUT_DIR, f"{name}.gif")
        render(src, dst, crop, segments, clip.get("clicks", ()), clip.get("blur", ()))
        length = sum((end - start) / speed for start, end, speed in segments)
        size = os.path.getsize(dst) / (1024 * 1024)
        total += size
        print(f"{name:24s} {length:5.1f}s  {size:5.1f} MB")
    if not (args.detect or args.motion):
        print(f"{'':24s} {'':5s}   {total:5.1f} MB total")


if __name__ == "__main__":
    main()
