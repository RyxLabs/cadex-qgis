# Running the tests

Most modules need QGIS's bundled Python, because plain system Python cannot
import `qgis.core`. Run each module as its own process: QGIS allows a
single `initQgis`/`exitQgis` cycle per process, so `unittest discover`
would crash.

On Windows, from the plugin folder, one module:

    "C:\Program Files\QGIS 3.40.6\bin\python-qgis-ltr.bat" test\test_cgxml_export.py

and every module, each in its own process, with one line per module and the
totals at the end:

    "C:\Program Files\QGIS 3.40.6\bin\python-qgis-ltr.bat" .github\scripts\run_tests.py .

(`python-qgis.bat` on a non-LTR install). On Linux the `qgis/qgis`
Docker images work the same way with plain `python3`. `run_tests.py` takes
module patterns (`test_topology*`) and `--pure`; the plugin folder has to
be named `cadex`.

The exit code is the module's result: 0 when every test passed,
non-zero otherwise. A module that starts QGIS ends through `helpers.main()`, which
exits with that code without running Python's shutdown, where QGIS 3.40
crashes after some Processing runs. Each process gets its own temporary
QGIS profile, so a run never reads the settings of your QGIS and several
modules can run at the same time.
`QT_QPA_PLATFORM` defaults to `offscreen`.

The pure modules start no QGIS (they never call `helpers.start_qgis()`) and
also run under plain Python 3.9 or newer; `test_conventions` skips its
theme check there, since `dialogs/theme.py` imports `qgis`.

`test_processing` runs the Processing Toolbox algorithms through
`processing.run`; it loads QGIS's own Processing plugin from the QGIS
installation, nothing to set up.

`openpyxl` is optional: the tests that read the Excel files back with it are
skipped without it. The DXF tests read the drawings back through GDAL.

## What a run leaves in TEMP

In a module that starts QGIS, a test removes its temporary folder in
`tearDown` with `helpers.remove_folder()`. On Windows QGIS keeps some files
open until its process ends: files of the profile, and some of
the GeoPackage, Shapefile, GeoJSON and CSV files OGR has read or written.
`helpers.main()` hands the folders that still hold such a file, the profile
included, to a short-lived Python process that removes them once the
module's process has ended. A module that ends this way leaves nothing in
TEMP: on QGIS 3.40.6 and 4.0.0 on Windows, at most eight folders are still
there when its process ends, and they are gone half a second later.

A script that calls `helpers.start_qgis()` and ends normally without
`helpers.main()` is cleaned up the same way when Python exits. What stays
behind is the profile (`cadex_tests_*`), the test folders (`cadex_*`) and
QGIS's own `CADexTests*` entries of a process that was killed, crashed or
timed out, and of a script that ends with `os._exit()`. Nothing removes
those later; delete them by hand.

## Test data

Every CGXML file the tests read is synthetic: the hand-written fixtures in
`test/data/` and the files `test/synthetic.py` builds, random shapes with
holes, islands and buildings in the eTerra and the delivery style. Real
deliveries and eTerra exports hold personal data and are never test input.
`.gitattributes` keeps the fixtures' bytes as committed on every platform.

## Continuous integration

`.github/workflows/tests.yml` runs on a push to `main`, on a pull request,
by hand, every Monday, and from `release.yml`. Its jobs:

- **release zip** builds `cadex.<version>.zip` with `scripts/build_zip.py`
  and keeps it as the artifact every other job uses.
- **tests** runs in the `qgis/qgis` images `3.34` (the minimum QGIS
  version), `3.40`, `ltr`, `stable` and `nightly`: every module on the
  checkout, every module again on the extracted zip with `test/`,
  `scripts/`, `.github/` and `CHANGELOG.md` copied beside it, then
  `.github/scripts/qgis_process_smoke.py`, which installs the zip into a
  throwaway profile, enables the plugin for `qgis_process` and checks that
  it lists the eight algorithms in the zip's version. A failure on `nightly`
  fails the Monday run and no other. `openpyxl` is installed.
- **tests (Windows, QGIS LTR)** runs on Mondays, for tags and by hand, not
  for pushes: `.github/scripts/install_qgis.ps1` installs `qgis-ltr` and
  `python3-openpyxl` from the OSGeo4W repository, and
  `.github/scripts/windows_tests.ps1` finds `bin\python-qgis*.bat` and runs
  the same three steps. The tests marked Windows-only run nowhere else in
  CI. `windows_tests.ps1 -Plugin . -Zip <zip> -QgisRoot <installation>`
  runs them on your PC.
- **upload checks and Python 3.9** runs the pure modules on Python 3.9, the
  oldest Python of a QGIS 3.34, and on the zip's contents the checks
  plugins.qgis.org runs on an upload: Bandit, detect-secrets, flake8 at
  120 columns and the Qt6 checker, `pyqt5_to_pyqt6.py --dry_run` in the
  `ghcr.io/qgis/pyqgis4-checker:main-ubuntu` image the site uses.

Dependabot proposes updates of the actions the workflows use.

## Release zip

    python scripts/build_zip.py [output folder]

builds `cadex.<version>.zip` (into `dist/` by default) from an allow-list:
the `.py` files, `metadata.txt`, `LICENSE`, `README.md`, the `.svg` icons,
`icons/ATTRIBUTION.md` and `data/siruta.db`. `test/`, `scripts/`, `docs/`,
`JOURNAL.md`, `CHANGELOG.md`, `SECURITY.md`, hidden files and folders and
Office lock files stay out. Any other file stops the build, which lists it.
A build into `dist/` removes the zips of older versions there; another
output folder keeps its files.

Text files go into the zip with LF line endings whatever the working tree
holds, every entry carries the time of the checked-out commit (1980-01-01
outside a git checkout), and no entry records the build machine. A Windows
checkout with CRLF files and an LF checkout of the same commit therefore
give the same zip; the build prints its SHA-256 to compare. The compressed
bytes come from the zlib of the Python that builds, so two machines agree
as long as theirs do.

## Releasing a version

1. `metadata.txt`: the version, and its entries in `changelog`.
   `CHANGELOG.md`: the same entries under `## <version> - <date>`, the
   newest section first. `test_conventions` fails while the two disagree.
2. Commit, push, wait for the tests.
3. `git tag v<version>` and `git push origin v<version>`.

`.github/workflows/release.yml` runs for that tag only. It stops when the
tag is not `v` + the version of `metadata.txt` or the section is still
headed `unreleased`; runs every job of `tests.yml`, the Windows job
included, on the tagged commit; creates the GitHub release with the
section of `CHANGELOG.md` as notes and the zip those jobs tested attached;
and uploads the same zip to plugins.qgis.org with
`.github/scripts/upload_plugin.py`.

The upload needs the repository secret `PLUGINS_QGIS_ORG_TOKEN`: on
plugins.qgis.org open the plugin's page, the *Tokens* tab, generate a
token (it is shown once) and store it under *Settings > Secrets and
variables > Actions* of the GitHub repository. Without the secret the
workflow still creates the GitHub release, and its run shows a notice that
the zip was not uploaded. The site publishes a version after its own
security scan.

## Screenshots

`CADEX_SCREENSHOTS=<folder>` makes `test_dialogs` save a picture of every
dialog. On Windows set `QT_QPA_PLATFORM=windows` as well, since the
offscreen platform has no fonts there. `CADEX_DARK=1` gives the pictures a
dark palette like QGIS's Night Mapping theme, to check the dark look.
