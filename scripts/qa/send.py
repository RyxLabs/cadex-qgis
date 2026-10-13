# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Send Python code to the QGIS started by start_qa.ps1 -Harness and print what it printed.

    python scripts/qa/send.py <name> [timeout_s] < code.py

The code runs on the QGIS GUI thread with plugin(), click(), later(), modal(), pump(), shot()
and iface defined (see harness.py). A name starting with u_ also runs while another command
waits in a modal dialog; open dialogs with later(lambda: click(...)), never directly.
"""

import io
import os
import sys
import time
from typing import cast

CTL = os.path.join(os.environ["TEMP"], "cdxqa", "ctl")

name = sys.argv[1]
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 120
cast(io.TextIOWrapper, sys.stdin).reconfigure(encoding="utf-8")
# a PowerShell pipe starts the text with a byte order mark
code = sys.stdin.read().lstrip("﻿")
out = os.path.join(CTL, "out", name + ".txt")
if os.path.exists(out):
    os.remove(out)
tmp = os.path.join(CTL, "cmd", name + ".tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(code)
os.replace(tmp, os.path.join(CTL, "cmd", name + ".py"))
end = time.time() + timeout
while time.time() < end:
    if os.path.exists(out):
        cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8")
        with open(out, encoding="utf-8") as fh:
            print(fh.read())
        sys.exit(0)
    time.sleep(0.2)
print(f"TIMEOUT after {timeout:.0f}s; the command may still be queued or running")
sys.exit(1)
