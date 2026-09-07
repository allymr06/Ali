"""Office presentations → PDF through the PowerPoint the student already has.

The academy reads PDFs. A lecture that arrives as ``.ppt`` or ``.pptx`` is
exported to PDF once by PowerPoint itself (COM automation driven from a
PowerShell script, exactly like the local voice is), and the PDF is cached
under the source file's SHA-256 so the same deck is never converted twice.
PowerPoint opens the deck read-only and saves it as PDF; nothing inside the
document is executed, and a deck that cannot be opened fails loudly with a
Turkish message instead of an empty document.

No third-party renderer is bundled: when PowerPoint is absent the converter
reports itself unavailable and the pipeline says so at import time.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

OFFICE_SUFFIXES = frozenset({".ppt", ".pptx"})
CONVERSION_TIMEOUT_SECONDS = 300.0

# Where recent Office builds put PowerPoint when the registry does not say.
_KNOWN_LOCATIONS = (
    r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
    r"C:\Program Files (x86)\Microsoft Office\root\Office16\POWERPNT.EXE",
    r"C:\Program Files\Microsoft Office\Office16\POWERPNT.EXE",
    r"C:\Program Files (x86)\Microsoft Office\Office16\POWERPNT.EXE",
    r"C:\Program Files\Microsoft Office\Office15\POWERPNT.EXE",
    r"C:\Program Files (x86)\Microsoft Office\Office15\POWERPNT.EXE",
)
_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\POWERPNT.EXE"

# ppSaveAsPDF = 32. Open(FileName, ReadOnly, Untitled, WithWindow): read-only,
# keep the name, no window. DisplayAlerts = ppAlertsNone so a damaged deck
# fails the call instead of parking a dialog nobody can see.
_CONVERT_SCRIPT = r"""
param([string]$Source, [string]$Target)
$ErrorActionPreference = "Stop"
$app = New-Object -ComObject PowerPoint.Application
try {
  try { $app.DisplayAlerts = 1 } catch {}
  $presentation = $app.Presentations.Open($Source, $true, $false, $false)
  try {
    $presentation.SaveAs($Target, 32)
  } finally {
    $presentation.Close()
  }
} finally {
  try { $app.Quit() } catch {}
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
}
"""


class ConversionError(RuntimeError):
    """The presentation could not be turned into a PDF."""


def find_powerpoint() -> Path | None:
    """The installed PowerPoint executable, or None when there is none."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    key = winreg.OpenKey(root, _APP_PATHS_KEY, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with key:
                    try:
                        value, _kind = winreg.QueryValueEx(key, "")
                    except OSError:
                        continue
                path = Path(str(value).strip().strip('"'))
                if path.is_file():
                    return path
    except Exception:
        pass
    for candidate in _KNOWN_LOCATIONS:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class OfficeConverter:
    """Converts ``.ppt``/``.pptx`` to PDF, caching by content hash.

    ``runner`` replaces PowerPoint in tests: it is called with the source
    and the target path and must write the PDF. Without a runner the
    converter is available only when PowerPoint was found.
    """

    def __init__(
        self,
        cache_directory: Path | None,
        *,
        executable: Path | None = None,
        runner: Callable[[Path, Path], None] | None = None,
        timeout_seconds: float = CONVERSION_TIMEOUT_SECONDS,
        detect: bool = True,
    ) -> None:
        self._cache = Path(cache_directory) if cache_directory is not None else None
        self._runner = runner
        if executable is None and runner is None and detect:
            executable = find_powerpoint()
        self._executable = executable
        self._timeout = float(timeout_seconds)
        self.conversions = 0

    @property
    def available(self) -> bool:
        return self._runner is not None or self._executable is not None

    @property
    def executable(self) -> Path | None:
        return self._executable

    def describe(self) -> str:
        if self._runner is not None:
            return "test runner"
        if self._executable is not None:
            return f"PowerPoint ({self._executable})"
        return "PowerPoint bulunamadı"

    @staticmethod
    def supports(path: Path) -> bool:
        return Path(path).suffix.lower() in OFFICE_SUFFIXES

    def cached_pdf(self, source: Path) -> Path | None:
        """The PDF an earlier conversion of these bytes left, if any."""
        if self._cache is None:
            return None
        target = self._cache / f"{sha256_of_file(Path(source))}.pdf"
        return target if target.is_file() and target.stat().st_size > 0 else None

    def to_pdf(self, source: Path) -> Path:
        source = Path(source)
        if not source.is_file():
            raise ConversionError("Sunum dosyası bulunamadı.")
        if not self.supports(source):
            raise ConversionError("Yalnızca .ppt ve .pptx sunumları PDF'e çevrilir.")
        if not self.available:
            raise ConversionError("Sunumu PDF'e çevirecek PowerPoint bulunamadı; sunumu PDF olarak kaydedip ekle.")
        cached = self.cached_pdf(source)
        if cached is not None:
            return cached
        if self._cache is not None:
            self._cache.mkdir(parents=True, exist_ok=True)
            target = self._cache / f"{sha256_of_file(source)}.pdf"
        else:
            target = Path(tempfile.mkdtemp(prefix="jarvis_office_")) / f"{source.stem}.pdf"
        partial = target.with_name(target.name + ".part.pdf")
        try:
            if self._runner is not None:
                self._runner(source, partial)
            else:
                self._run_powerpoint(source, partial)
            if not partial.is_file() or partial.stat().st_size == 0:
                raise ConversionError("PowerPoint sunumu PDF'e çeviremedi (boş çıktı).")
            os.replace(partial, target)
        finally:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
        self.conversions += 1
        return target

    def _run_powerpoint(self, source: Path, target: Path) -> None:
        script_dir = tempfile.mkdtemp(prefix="jarvis_office_")
        script_path = Path(script_dir) / "convert.ps1"
        try:
            script_path.write_text(_CONVERT_SCRIPT, encoding="utf-8")
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script_path),
                        "-Source",
                        str(source),
                        "-Target",
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise ConversionError("PowerPoint dönüştürmesi zaman aşımına uğradı.") from exc
            except OSError as exc:
                raise ConversionError(f"PowerPoint başlatılamadı ({type(exc).__name__}).") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip().splitlines()
                last = detail[-1][:160] if detail else f"çıkış kodu {completed.returncode}"
                raise ConversionError(f"PowerPoint sunumu PDF'e çeviremedi: {last}")
        finally:
            shutil.rmtree(script_dir, ignore_errors=True)


__all__ = ["OFFICE_SUFFIXES", "ConversionError", "OfficeConverter", "find_powerpoint", "sha256_of_file"]
