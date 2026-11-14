# PyInstaller build of Tuckvault for Windows.
#
#     .venv\Scripts\python -m PyInstaller --noconfirm --clean packaging\tuckvault.spec
#
# Produces dist\Tuckvault\ with two programs that share one set of files:
#   Tuckvault.exe      the window; also what the scheduled tasks start (no console flash)
#   tuckvault-cli.exe  the same commands with console output
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "tuckvault" / "__init__.py").read_text()).group(1)
ICON = str(ROOT / "tuckvault" / "assets" / "tuckvault.ico")

# Windows "Details" tab of the .exe
numbers = tuple(int(x) for x in (VERSION.split(".") + ["0"])[:4])
version_file = Path(workpath) / "version_info.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Tuckvault'),
      StringStruct('FileDescription', 'Tuckvault backup'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'Tuckvault'),
      StringStruct('OriginalFilename', 'Tuckvault.exe'),
      StringStruct('ProductName', 'Tuckvault'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=[
        # sent to the backup computer as source, so it must ship as a readable file
        (str(ROOT / "tuckvault" / "agent.py"), "tuckvault"),
        (ICON, "tuckvault/assets"),
    ],
    excludes=["PIL", "pytest", "unittest", "pydoc", "doctest", "tkinter.test", "lib2to3"],
)
pyz = PYZ(a.pure)

common = dict(exclude_binaries=True, icon=ICON, version=str(version_file), upx=False)
gui = EXE(pyz, a.scripts, [], name="Tuckvault", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="tuckvault-cli", console=True, **common)
COLLECT(gui, cli, a.binaries, a.datas, name="Tuckvault", upx=False)
