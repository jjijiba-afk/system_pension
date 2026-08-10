# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 사양.

실행 파일 두 개를 만든다. 둘 다 같은 코드에서 나오며 차이는 콘솔 창뿐이다.

``연금계리산출.exe``
    더블클릭용. 콘솔 창 없이 GUI 만 뜬다(``console=False``).
``pension-cli.exe``
    배치·자동화용. 콘솔에 진행상황과 결과를 출력한다(``console=True``).
    윈도우에서는 ``console=False`` 인 실행 파일이 표준출력을 낼 수 없어
    별도로 만든다.

빌드::

    pip install -e ".[build]"
    pyinstaller pension.spec --noconfirm
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = [
    "openpyxl",
    "openpyxl.cell._writer",   # onefile 에서 종종 누락되는 확장 모듈
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
    *collect_submodules("pension"),
]

analysis = Analysis(
    ["src/pension/__main__.py"],
    pathex=["src"],
    binaries=[],
    # 실을 자료가 없다. 기본 명부는 난수로 만들므로 원자료 파일이 필요 없고,
    # **실제 개인정보를 실행 파일에 넣지 않는다** 는 뜻이기도 하다.
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # 쓰지 않는 무거운 의존성을 빼서 실행 파일 크기를 줄인다.
    excludes=[
        "numpy", "pandas", "matplotlib", "scipy",
        "PIL", "pytest", "setuptools", "pip",
    ],
    noarchive=False,
    # 독스트링과 주석을 뺀 바이트코드로 묶는다. 실행 파일을 풀어 보아도
    # 설계 메모가 그대로 읽히지 않는다.
    optimize=2,
)

pyz = PYZ(analysis.pure)

def build(name: str, *, console: bool) -> EXE:
    return EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


gui_exe = build("연금계리산출", console=False)
cli_exe = build("pension-cli", console=True)
