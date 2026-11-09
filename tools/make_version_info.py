"""Tworzy build/version_info.txt – metadane pliku .exe (nazwa, wersja, opis).

Plik .exe bez tych danych antywirusy i SmartScreen oceniają surowiej. Wywoływane przy budowaniu.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "version_info.txt"


def make() -> Path:
    ver = re.search(r'^VERSION = "([^"]*)"', (ROOT / "version.py").read_text(encoding="utf-8"), re.M).group(1)
    nums = (list(int(x) for x in re.findall(r"\d+", ver)) + [0, 0, 0, 0])[:4]
    t = tuple(nums)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041504B0', [
      StringStruct('CompanyName', 'Tibia Apps Companion'),
      StringStruct('FileDescription', 'Tibia Apps Companion'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'TibiaAppsCompanion'),
      StringStruct('LegalCopyright', 'Projekt fanowski, niezwiązany z CipSoft GmbH'),
      StringStruct('OriginalFilename', 'TibiaAppsCompanion.exe'),
      StringStruct('ProductName', 'Tibia Apps Companion'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [0x0415, 1200])])
  ]
)
""", encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(make())
