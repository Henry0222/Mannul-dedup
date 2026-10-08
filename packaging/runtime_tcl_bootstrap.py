"""Prime Tcl in PyInstaller one-file builds before tkinter creates the UI.

Some Windows Python 3.12/Tcl 8.6.15 installations fail their first Tcl_Init
after PyInstaller extracts the bundled libraries, even though init.tcl exists.
Creating and disposing a minimal interpreter once refreshes Tcl's internal
library-path state; tkinter's real interpreter can then initialize normally.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _prime_bundled_tcl() -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return

    bundle_root_value = getattr(sys, "_MEIPASS", None)
    if not bundle_root_value:
        return

    bundle_root = Path(bundle_root_value)
    tcl_library = bundle_root / "_tcl_data"
    tk_library = bundle_root / "_tk_data"
    tcl_dll = bundle_root / "tcl86t.dll"
    if not (tcl_library / "init.tcl").is_file() or not tcl_dll.is_file():
        return

    os.environ["TCL_LIBRARY"] = str(tcl_library)
    if (tk_library / "tk.tcl").is_file():
        os.environ["TK_LIBRARY"] = str(tk_library)

    try:
        library = ctypes.WinDLL(str(tcl_dll))
        library.Tcl_CreateInterp.argtypes = []
        library.Tcl_CreateInterp.restype = ctypes.c_void_p
        library.Tcl_Init.argtypes = [ctypes.c_void_p]
        library.Tcl_Init.restype = ctypes.c_int
        library.Tcl_DeleteInterp.argtypes = [ctypes.c_void_p]
        library.Tcl_DeleteInterp.restype = None

        interpreter = library.Tcl_CreateInterp()
        if interpreter:
            try:
                library.Tcl_Init(interpreter)
            finally:
                library.Tcl_DeleteInterp(interpreter)
    except (AttributeError, OSError):
        # Let tkinter report the original initialization error if priming is
        # unavailable on a different runtime.
        return


_prime_bundled_tcl()
