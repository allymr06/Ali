from pathlib import Path

root = Path(SPEC).resolve().parent.parent
entrypoint = root / "installer" / "entrypoint.py"
# Nova (pywebview/WebView2) shell assets. They land below
# _internal/app/ui/nova/web so app.ui.nova.shell.resolve_web_root()
# finds them through sys._MEIPASS exactly as in a source checkout.
nova_web = root / "app" / "ui" / "nova" / "web"

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
        (str(nova_web / "index.html"), "app/ui/nova/web"),
        (str(nova_web / "nova.css"), "app/ui/nova/web"),
        (str(nova_web / "nova.js"), "app/ui/nova/web"),
    ],
    hiddenimports=[
        "app.ui.nova",
        "app.ui.nova.shell",
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
