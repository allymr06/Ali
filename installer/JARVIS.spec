from pathlib import Path

root = Path(SPEC).resolve().parent.parent
entrypoint = root / "installer" / "entrypoint.py"

a = Analysis(
    [str(entrypoint)],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "README.md"), "."),
        (str(root / "docs" / "SECURITY.md"), "docs"),
        (str(root / "docs" / "CONFIGURATION.md"), "docs"),
        (str(root / "docs" / "ACCEPTANCE.md"), "docs"),
        (str(root / "assets" / "branding" / "jarvis.ico"), "assets"),
    ],
    hiddenimports=[
        "app.voice.audio",
        "app.voice.providers",
        "app.vision.capture",
        "app.vision.consent",
        "app.vision.models",
    ],
    hookspath=[str(root / "installer" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=str(root / "installer" / "version_info.txt"),
    manifest=str(root / "installer" / "jarvis.manifest"),
    icon=str(root / "assets" / "branding" / "jarvis.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JARVIS",
)
