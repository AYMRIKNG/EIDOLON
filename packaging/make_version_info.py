"""Writes packaging/version_info.txt (Windows file properties of EIDOLON.exe)
from core/branding.py, so the version lives in one place. Prints the version."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "core" / "branding.py").read_text(encoding="utf-8")
get = lambda name: re.search(rf'^{name}\s*=\s*"([^"]*)"', src, re.M).group(1)
version, name, author = get("APP_VERSION"), get("APP_NAME"), get("APP_AUTHOR")
nums = [int(x) for x in re.findall(r"\d+", version)][:3]
nums += [0] * (4 - len(nums))
t = tuple(nums)

(ROOT / "packaging" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', '{author}'),
      StringStruct('FileDescription', '{name} — assistant vocal'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', '© 2026 {author} — dérivé de MARK LIV (FatihMakes), CC BY-NC 4.0'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', '{name}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
""", encoding="utf-8")
print(version)
