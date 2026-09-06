from pathlib import Path, PurePosixPath

root = Path(SPEC).resolve().parent.parent
entrypoint = root / "installer" / "entrypoint.py"
# Nova (pywebview/WebView2) shell assets: index.html plus the css/ and js/
# directories. Every file below the web root is collected, keeping its
# relative directory, so it lands below _internal/app/ui/nova/web and
# app.ui.nova.shell.resolve_web_root() finds it through sys._MEIPASS
# exactly as in a source checkout.
# The Medical Academy's curated data (curriculum, anatomy, concepts). The
# study layer cannot start without it, so it ships with the executable.
medical_data = root / "app" / "medical" / "data"
medical_datas = [
    (str(path), "app/medical/data")
    for path in sorted(medical_data.glob("*.json"))
]

nova_web = root / "app" / "ui" / "nova" / "web"
nova_datas = [
    (
        str(path),
        str(PurePosixPath("app/ui/nova/web") / path.relative_to(nova_web).parent.as_posix()),
    )
    for path in sorted(nova_web.rglob("*"))
    if path.is_file()
]

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
        *nova_datas,
        *medical_datas,
    ],
    hiddenimports=[
        "app.ui.nova",
        "app.ui.nova.shell",
        "app.medical",
        "app.medical.academy",
        "pypdfium2",
        "webview",
        "app.voice.audio",
        "app.voice.gemini",
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
