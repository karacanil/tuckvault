"""Double-click this file to open Tuckvault.

It hands over to the project's own Python environment (.venv), which has
the one library Tuckvault needs, so it works whichever Python Windows
associates with .pyw files.
"""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
venv_python = root / ".venv" / "Scripts" / "pythonw.exe"

if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
    subprocess.Popen([str(venv_python), "-m", "tuckvault"], cwd=str(root))
else:
    sys.path.insert(0, str(root))
    from tuckvault.gui import main
    main()
