# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the test modules of a CADex folder, each in a process of its own, with the Python that
runs this script: QGIS allows one initQgis/exitQgis cycle per process.

Usage: python .github/scripts/run_tests.py <plugin folder> [--pure] [module pattern ...]

  <plugin folder>  a folder named cadex that holds the plugin and its test/ folder
  --pure           only the modules that start no QGIS, which also run under plain Python
  module pattern   e.g. test_topology*; without one, every test_*.py

The exit code is 1 when a module fails, crashes, runs no test or runs longer than 15 minutes.
"""

import fnmatch
import glob
import os
import re
import subprocess
import sys
import threading
import time

TIMEOUT = 900
RAN = re.compile(rb"^Ran (\d+) tests? in", re.MULTILINE)
SKIPPED = re.compile(rb"skipped=(\d+)")
ON_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"


def qgis_version() -> str:
    try:
        from qgis.core import Qgis  # type: ignore
    except ImportError:
        return "no QGIS"
    return f"QGIS {Qgis.version()}"


def modules(plugin: str, pure: bool, patterns) -> list:
    found = []
    for path in sorted(glob.glob(os.path.join(plugin, "test", "test_*.py"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if patterns and not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
            continue
        if pure:
            with open(path, encoding="utf-8") as fp:
                if "start_qgis(" in fp.read():
                    continue
        found.append(path)
    return found


def run(path: str, plugin: str) -> tuple:
    """(exit code or "timeout", tests run, tests skipped, seconds) of one module, its output
    passed on as it comes."""
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, path], cwd=plugin, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    timed_out = []

    def stop():
        timed_out.append(True)
        process.kill()

    timer = threading.Timer(TIMEOUT, stop)
    timer.start()
    output = []
    try:
        for line in process.stdout:
            output.append(line)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        code = process.wait()
    finally:
        timer.cancel()
    text = b"".join(output)
    ran = RAN.findall(text)
    skipped = SKIPPED.findall(text)
    return ("timeout" if timed_out else code, int(ran[-1]) if ran else 0,
            int(skipped[-1]) if skipped else 0, time.monotonic() - started)


def main(arguments) -> int:
    pure = "--pure" in arguments
    arguments = [a for a in arguments if a != "--pure"]
    if not arguments:
        sys.exit(__doc__)
    plugin = os.path.abspath(arguments[0])
    if os.path.basename(plugin) != "cadex":
        sys.exit(f"{plugin}: the folder has to be named cadex for 'import cadex' to work")
    paths = modules(plugin, pure, arguments[1:])
    if not paths:
        sys.exit(f"no test module found in {os.path.join(plugin, 'test')}")
    print(f"Python {sys.version.split()[0]}, {qgis_version()}, {len(paths)} modules of {plugin}",
          flush=True)
    rows = []
    for path in paths:
        name = os.path.basename(path)
        print(f"::group::{name}" if ON_GITHUB else f"=== {name}", flush=True)
        code, ran, skipped, seconds = run(path, plugin)
        if ON_GITHUB:
            print("::endgroup::", flush=True)
        failed = code != 0 or ran == 0
        if failed:
            reason = (f"ran longer than {TIMEOUT // 60} minutes" if code == "timeout" else
                      f"ended with exit code {code}" if code != 0 else "ran no test")
            print(f"::error file=test/{name}::{name} {reason}" if ON_GITHUB
                  else f"FAILED: {name} {reason}", flush=True)
        rows.append((name, ran, skipped, seconds, failed))
    total = sum(row[1] for row in rows)
    skipped = sum(row[2] for row in rows)
    failures = [row[0] for row in rows if row[4]]
    for name, ran, skip, seconds, failed in rows:
        print(f"{name:34s} {ran:4d} tests {skip:3d} skipped {seconds:6.1f} s  "
              f"{'FAILED' if failed else 'ok'}")
    print(f"{len(rows)} modules, {total} tests, {skipped} skipped, "
          f"{len(failures)} failed{': ' + ', '.join(failures) if failures else ''}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
