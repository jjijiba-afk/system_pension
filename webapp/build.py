"""아이패드용 웹앱(PWA) 빌드.

산출 엔진이 순수 파이썬이라는 사실 덕에, 서버 없이 **브라우저 안에서** 엔진을
그대로 돌릴 수 있다(Pyodide/WebAssembly). 아이패드 Safari 로 열어 홈 화면에
추가하면 앱 아이콘이 생기고, 서비스워커가 전부 캐시하므로 이후에는 오프라인
에서도 돈다. 명부는 기기 밖으로 한 바이트도 나가지 않는다.

CDN 을 참조하지 않는다. pyodide 런타임(npm 배포판)과 필요한 휠 전부를 dist 에
동봉한다 — 사내망처럼 외부 CDN 이 막힌 곳에서도 돌아야 하고, "어느 날 CDN 이
바뀌어 앱이 죽는" 일이 없어야 하기 때문이다.

빌드::

    python webapp/build.py            # → webapp/dist/

dist 폴더를 아무 정적 웹서버(HTTPS)에나 올리면 된다. 홈 화면 설치와 오프라인
캐시는 HTTPS(또는 localhost)에서만 동작한다 — 브라우저 규약이다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zlib
from pathlib import Path

PYODIDE_VERSION = "0.26.4"
NPM_TARBALL = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz"

#: npm 배포판에서 실제로 쓰는 파일. 전부 복사하면 40MB 가 넘는 node 용 파일까지
#: 딸려 온다.
PYODIDE_FILES = (
    "pyodide.js",
    "pyodide.asm.js",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
)

ROOT = Path(__file__).resolve().parent.parent
APP = Path(__file__).resolve().parent / "app"
DIST = Path(__file__).resolve().parent / "dist"
CACHE = Path(__file__).resolve().parent / ".cache"


def _download_pyodide(target: Path) -> None:
    """npm 배포판에서 pyodide 런타임을 꺼낸다. 받은 tgz 는 캐시한다."""
    CACHE.mkdir(exist_ok=True)
    tarball = CACHE / f"pyodide-{PYODIDE_VERSION}.tgz"
    if not tarball.exists():
        print(f"  pyodide {PYODIDE_VERSION} 내려받는 중 (npm)…")
        with urllib.request.urlopen(NPM_TARBALL) as response:
            tarball.write_bytes(response.read())

    target.mkdir(parents=True, exist_ok=True)
    wanted = {f"package/{name}" for name in PYODIDE_FILES}
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive.getmembers():
            if member.name in wanted:
                payload = archive.extractfile(member)
                assert payload is not None
                (target / Path(member.name).name).write_bytes(payload.read())
    missing = [n for n in PYODIDE_FILES if not (target / n).exists()]
    if missing:
        raise SystemExit(f"pyodide 배포판에 없는 파일: {missing}")


def _build_wheels(target: Path) -> list[str]:
    """엔진 휠 + 순수 파이썬 의존성 휠을 모은다."""
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
         "-w", str(target), "-q"],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "openpyxl", "et_xmlfile", "xlrd",
         "--no-deps", "-d", str(target), "-q"],
        check=True,
    )
    _strip_engine_source(next(target.glob("pension_actuarial-*.whl")))

    names = sorted(p.name for p in target.glob("*.whl"))
    impure = [n for n in names if not n.endswith(("py3-none-any.whl", "py2.py3-none-any.whl"))]
    if impure:
        raise SystemExit(f"순수 파이썬 휠이 아니라 브라우저에서 못 쓴다: {impure}")
    return names


def _strip_engine_source(wheel: Path) -> None:
    """엔진 휠에서 **읽을거리** 를 걷어낸다 — 자료 파일과 독스트링·주석.

    이 휠은 웹앱과 함께 그대로 배포된다. 브라우저가 파이썬을 실행해야 하므로
    소스를 빼고 보낼 수는 없는데, 압축을 풀면 누구나 파일을 열어 볼 수 있다.
    그 안에 이 시스템이 무엇을 옮긴 것인지, 원본 시트가 어떻게 생겼는지 적은
    설계 메모가 통째로 들어 있었다.

    코드는 그대로 두고 **주석과 독스트링만** 없앤다. 구문 트리를 다시 찍어
    내므로 동작은 한 글자도 달라지지 않고, 주석은 애초에 트리에 없다.
    """
    import ast
    import zipfile

    def is_text(statement: ast.stmt) -> bool:
        """그 줄이 글만 적어 둔 문장인지 — 독스트링과 속성 설명."""
        return (isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str))

    def strip(source: str) -> str:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if body is None or not isinstance(body, list):
                continue
            # 값에 붙여 쓴 설명(``SERVICE_DAILY = "일할"`` 다음 줄의 문자열)도
            # 글이다. 파이썬이 실행할 때 버리는 문장이라 빼도 동작이 같다.
            kept = [item for item in body if not is_text(item)]
            node.body = kept or [ast.Pass()]
        return ast.unparse(ast.fix_missing_locations(tree))

    kept = []
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith((".csv", ".txt", ".md")) and "dist-info" not in name:
                continue
            payload = archive.read(info)
            if name.endswith(".py"):
                payload = strip(payload.decode("utf-8")).encode("utf-8")
            kept.append((info, payload))
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, payload in kept:
            archive.writestr(info, payload)


def _png(size: int) -> bytes:
    """앱 아이콘. 남색 바탕에 오름차순 막대 셋 — 외부 라이브러리 없이 그린다."""
    navy = (31, 56, 100)
    bar = (255, 255, 255)
    accent = (255, 217, 102)

    pixels = bytearray()
    unit = size // 10
    bars = (  # (x0, x1, 높이) 비율 단위
        (2, 3.4, 3), (4.3, 5.7, 5), (6.6, 8, 7),
    )
    for y in range(size):
        pixels.append(0)  # 필터 없음
        for x in range(size):
            color = navy
            for index, (x0, x1, height) in enumerate(bars):
                if x0 * unit <= x < x1 * unit and y >= size - (height + 1.2) * unit:
                    color = accent if index == 2 else bar
            pixels.extend(color)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        raw = kind + payload
        return struct.pack(">I", len(payload)) + raw + struct.pack(">I", zlib.crc32(raw))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
        + chunk(b"IEND", b"")
    )


def _hash_dir(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "sw.js":
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build() -> Path:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    print("아이패드 웹앱 빌드")
    _download_pyodide(DIST / "pyodide")
    wheels = _build_wheels(DIST / "wheels")
    print(f"  휠 {len(wheels)}개: {', '.join(wheels)}")

    for name in ("index.html", "app.css", "app.js", "manifest.webmanifest"):
        shutil.copy2(APP / name, DIST / name)
    (DIST / "icon-180.png").write_bytes(_png(180))
    (DIST / "icon-512.png").write_bytes(_png(512))

    # index.html 에 휠 목록을 심는다. 파일명이 버전을 담고 있으므로 하드코딩하면
    # 버전을 올릴 때마다 손으로 고쳐야 한다.
    page = (DIST / "index.html").read_text(encoding="utf-8")
    page = page.replace("__WHEELS__", json.dumps(wheels, ensure_ascii=False))
    (DIST / "index.html").write_text(page, encoding="utf-8")

    # 서비스워커 — dist 의 모든 파일을 사전 캐시 목록으로 심고, 내용 해시를
    # 캐시 이름에 넣어 앱을 고치면 캐시가 자연히 갈리게 한다.
    stamp = _hash_dir(DIST)
    files = sorted(
        "./" + p.relative_to(DIST).as_posix()
        for p in DIST.rglob("*") if p.is_file()
    )
    worker = (APP / "sw.js").read_text(encoding="utf-8")
    worker = worker.replace("__VERSION__", stamp)
    worker = worker.replace("__PRECACHE__", json.dumps(files, ensure_ascii=False))
    (DIST / "sw.js").write_text(worker, encoding="utf-8")

    # 화면 아래에도 같은 값을 박아 둔다. 새 빌드를 올렸는데 옛 캐시가 도는지
    # 눈으로 가려낼 수단이 없으면, 고친 것이 안 고쳐진 것처럼 보인다.
    # 해시를 낸 **뒤** 에 끼워 넣는다 — 넣고 나서 재면 값이 자기를 바꾼다.
    page = (DIST / "index.html").read_text(encoding="utf-8")
    (DIST / "index.html").write_text(
        page.replace("__BUILD__", stamp), encoding="utf-8"
    )

    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"  완료: {DIST}  ({total / 1e6:.1f} MB, 캐시 버전 {stamp})")
    return DIST


if __name__ == "__main__":
    build()

