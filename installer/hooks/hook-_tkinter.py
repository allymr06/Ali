from __future__ import annotations

import sys
from pathlib import Path


base = Path(sys.base_prefix)
datas = [
    (str(base / "tcl" / "tcl8.6"), "_tcl_data"),
    (str(base / "tcl" / "tk8.6"), "_tk_data"),
]
binaries = [
    (str(base / "DLLs" / "tcl86t.dll"), "."),
    (str(base / "DLLs" / "tk86t.dll"), "."),
]
