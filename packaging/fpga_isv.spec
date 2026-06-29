# PyInstaller spec for fpga-isv: a single self-contained binary that bundles its own Python,
# Tcl/Tk (for tkinter), Pillow, and the example panels -- so it runs with zero prerequisites.
#
# Build (one runner per OS/arch -- PyInstaller cannot cross-compile):
#   pip install pyinstaller pillow
#   pyinstaller packaging/fpga_isv.spec
# Output: dist/fpga-isv  (dist/fpga-isv.exe on Windows)
#
# console=True is intentional: fpga-isv has a real CLI surface (--list-examples, --version,
# --help) and prints calibration coordinates / listen status to the terminal.

import os

# SPECPATH is the directory containing this spec (…/packaging); the repo root is its parent.
ROOT = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(ROOT, "packaging", "fpga_isv_entry.py")],
    pathex=[ROOT],
    binaries=[],
    # Bundle the example panels at the same package-relative path examples.py looks for
    # under sys._MEIPASS (i.e. <_MEIPASS>/fpga_isv/examples).
    datas=[(os.path.join(ROOT, "fpga_isv", "examples"), "fpga_isv/examples")],
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="fpga-isv",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,       # native arch of the runner (no cross-compile)
    codesign_identity=None,
    entitlements_file=None,
)
