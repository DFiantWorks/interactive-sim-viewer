"""Locate the bundled example panels.

Each example is a subdirectory of ``examples/`` holding ``<name>.json`` (the board/panel
config) plus any assets it references (e.g. a board photo). The directory resolves three ways:

  * frozen by PyInstaller     -> ``<_MEIPASS>/fpga_isv/examples`` (see packaging/fpga_isv.spec)
  * installed / run from src  -> ``<this package dir>/examples``
"""

import os
import sys


def examples_dir():
    """Absolute path to the bundled examples directory."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "fpga_isv", "examples")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def list_examples():
    """Sorted names of bundled examples (subdirs that contain ``<name>.json``)."""
    root = examples_dir()
    if not os.path.isdir(root):
        return []
    names = []
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, name + ".json")):
            names.append(name)
    return names


def example_config_path(name):
    """Path to ``examples/<name>/<name>.json``; raises ValueError if it doesn't exist."""
    path = os.path.join(examples_dir(), name, name + ".json")
    if not os.path.isfile(path):
        available = ", ".join(list_examples()) or "(none bundled)"
        raise ValueError(f"unknown example {name!r}; available: {available}")
    return path
