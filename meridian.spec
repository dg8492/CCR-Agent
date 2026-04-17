# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Meridian — builds Windows EXE and macOS .app
# Run: pyinstaller meridian.spec --clean

import sys

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('ui', 'ui'),           # Bundle the entire UI folder (HTML, logo)
    ],
    hiddenimports=[
        # Flask + WSGI
        'flask', 'flask.json', 'jinja2', 'jinja2.ext', 'jinja2.utils',
        'werkzeug', 'werkzeug.utils', 'werkzeug.routing', 'werkzeug.exceptions',
        'werkzeug.middleware', 'werkzeug.middleware.proxy_fix',
        'click', 'itsdangerous', 'markupsafe',
        # Anthropic SDK
        'anthropic', 'anthropic.types', 'anthropic._client',
        'httpx', 'httpcore', 'httpcore._sync', 'httpcore._async',
        'anyio', 'anyio._backends', 'anyio._backends._asyncio',
        'sniffio', 'h11', 'h2', 'hpack', 'hyperframe',
        # dotenv
        'dotenv', 'python_dotenv',
        # Requests
        'requests', 'requests.adapters', 'requests.auth',
        'urllib3', 'urllib3.util', 'charset_normalizer', 'certifi', 'idna',
        # PDF parsing
        'pypdf', 'pypdf.filters', 'pypdf._reader', 'pypdf._page',
        'pypdf.generic', 'pypdf.constants',
        # Word documents
        'docx', 'docx.oxml', 'docx.oxml.ns', 'docx.opc', 'docx.opc.constants',
        'lxml', 'lxml.etree', 'lxml._elementpath',
        # BM25 search
        'rank_bm25',
        # DuckDuckGo search
        'duckduckgo_search', 'duckduckgo_search.DDGS',
        'primp',
        # App module
        'document_loader',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unused GUI frameworks (reduces size + AV suspicion)
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'wx', 'customtkinter', 'gi',
        # Exclude unused heavy libs
        'matplotlib', 'numpy', 'pandas', 'scipy', 'PIL',
        'reportlab', 'cv2',
        # Exclude unused stdlib
        'test', 'unittest', 'doctest', 'pdb',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── Windows / Linux Executable ─────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Meridian',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # IMPORTANT: Disable UPX — reduces antivirus false positives
    console=False,      # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if sys.platform == 'win32' and os.path.exists('assets/icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Meridian',
)

# ── macOS App Bundle ───────────────────────────────────────────────────────
if sys.platform == 'darwin':
    app_bundle = BUNDLE(
        coll,
        name='Meridian.app',
        icon='assets/icon.icns' if os.path.exists('assets/icon.icns') else None,
        bundle_identifier='com.catalystcapitalresearch.meridian',
        info_plist={
            'CFBundleDisplayName': 'Meridian',
            'CFBundleName': 'Meridian',
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleVersion': '1.0.0',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '11.0',
            'NSHumanReadableCopyright': 'Copyright 2026 Catalyst Capital Research',
        },
    )
