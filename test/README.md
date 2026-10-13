# Running the tests

Most modules need QGIS's bundled Python, because plain system Python cannot
import `qgis.core`. Run each module as its own process: QGIS allows a
single `initQgis`/`exitQgis` cycle per process, so `unittest discover`
would crash.

On Windows, from the plugin folder:

    "C:\Program Files\QGIS 3.40.6\bin\python-qgis-ltr.bat" test\test_cgxml_export.py

(`python-qgis.bat` on a non-LTR install). On Linux the `qgis/qgis`
Docker images work the same way with plain `python3`; see
`.github/workflows/tests.yml`.

The exit code is the module's result: 0 when every test passed,
non-zero otherwise. A module that starts QGIS ends through `helpers.main()`, which
exits with that code without running Python's shutdown, where QGIS 3.40
crashes after some Processing runs. Each process gets its own temporary
QGIS profile, so a run never reads the settings of your QGIS and several
modules can run at the same time.
`QT_QPA_PLATFORM` defaults to `offscreen`.

In a module that starts QGIS, a test removes its temporary folder in
`tearDown` with `helpers.remove_folder()`. On Windows QGIS keeps some files
open until its process ends: the profile's `user-history.db`, and some of
the GeoPackage, Shapefile, GeoJSON and CSV files OGR has read or written.
`helpers.main()` hands the folders that still hold such a file, the profile
included, to a short-lived Python process that removes them once the
module's process has ended, so a run leaves no `cadex_*` folder in TEMP.

The pure modules (`test_roles`, `test_cgxml_reader`, `test_pure_services`,
`test_xlsx`, `test_conventions`, `test_build_scripts`) start no QGIS and
also run under plain Python 3.9 or newer; `test_conventions` skips its
theme check there, since `dialogs/theme.py` imports `qgis`.

`test_processing` runs the Processing Toolbox algorithms through
`processing.run`; it loads QGIS's own Processing plugin from the QGIS
installation, nothing to set up.

`ezdxf` and `openpyxl` are optional: the DXF test and the tests that read
the Excel files back with openpyxl are skipped without them.

## Test data

Every CGXML file the tests read is synthetic: the hand-written fixtures in
`test/data/` and the files `test/synthetic.py` builds, random shapes with
holes, islands and buildings in the eTerra and the delivery style. Real
deliveries and eTerra exports hold personal data and are never test input.

## Continuous integration

`.github/workflows/tests.yml` runs every module in the `qgis/qgis` images
`3.34` (the minimum QGIS version), `ltr`, `stable` and `nightly`; a failure
on `nightly` does not fail the workflow. It installs `ezdxf` in the versions
`services/deps.py` installs for users, and `openpyxl`. A second job builds
the release zip, keeps it as a build artifact, and runs on its contents the
checks plugins.qgis.org runs on an upload: Bandit, detect-secrets, flake8 at
120 columns and the Qt6 checker, `pyqt5_to_pyqt6.py --dry_run` in the
`ghcr.io/qgis/pyqgis4-checker:main-ubuntu` image the site uses.

## Release zip

    python scripts/build_zip.py [output folder]

builds `cadex.<version>.zip` (into `dist/` by default) from an allow-list:
the `.py` files, `metadata.txt`, `LICENSE`, `README.md`, the `.svg` icons,
`icons/ATTRIBUTION.md` and `data/siruta.db`. `test/`, `scripts/`, `docs/`,
`JOURNAL.md`, hidden files and Office lock files stay out. Any other file
stops the build, which lists it. A build into `dist/` removes the zips of
older versions there; another output folder keeps its files.

## Screenshots

`CADEX_SCREENSHOTS=<folder>` makes `test_dialogs` save a picture of every
dialog. On Windows set `QT_QPA_PLATFORM=windows` as well, since the
offscreen platform has no fonts there. `CADEX_DARK=1` gives the pictures a
dark palette like QGIS's Night Mapping theme, to check the dark look.
