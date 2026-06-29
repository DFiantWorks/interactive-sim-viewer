"""PyInstaller entry point. Kept outside the package so the frozen __main__ is a plain
top-level script importing the package (avoids intra-package __main__ import quirks)."""

from fpga_isv.viewer import main

if __name__ == "__main__":
    main()
