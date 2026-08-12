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
import os
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

# 윈도우 콘솔은 로캘에 따라 stdout 인코딩이 cp1252 등으로 잡혀, 진행 메시지의
# 한글을 print() 하는 순간 UnicodeEncodeError 로 죽는다(영어 로캘 CI 러너에서
# 실제로 이렇게 죽었다). UTF-8 로 못 박아 로캘과 무관하게 돌게 한다.
if sys.platform == "win32":  # pragma: no cover - 리눅스 CI 에서는 확인 못 한다
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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


def _png(size: int, *, safe: float = 1.0) -> bytes:
    """앱 아이콘. 남색 바탕에 오름차순 막대 셋 — 외부 라이브러리 없이 그린다.

    :param safe: 그림을 가운데로 줄이는 비율. 안드로이드는 홈 화면 아이콘을
        기기 모양(원·둥근 사각)대로 **잘라 낸다.** 가장자리까지 그린 그림을
        maskable 로 내놓으면 막대 끝이 잘린다. 0.6 쯤으로 줄여 안전지대
        (가운데 80%) 안에 들어오게 한다. 바탕은 꽉 채우므로 흰 테두리는 없다.
    """
    navy = (31, 56, 100)
    bar = (255, 255, 255)
    accent = (255, 217, 102)

    pixels = bytearray()
    unit = size / 10 * safe
    edge = (size - unit * 10) / 2      # 줄인 만큼 사방으로 민다
    bars = (  # (x0, x1, 높이) 비율 단위
        (2, 3.4, 3), (4.3, 5.7, 5), (6.6, 8, 7),
    )
    floor = size - edge                # 막대가 서 있는 바닥
    for y in range(size):
        pixels.append(0)  # 필터 없음
        for x in range(size):
            color = navy
            for index, (x0, x1, height) in enumerate(bars):
                # 아래쪽도 막아야 한다. 위쪽만 재면 막대가 그림 맨 밑까지
                # 내려가, 줄여 놓고도 안드로이드 마스크에 그대로 잘린다.
                if (edge + x0 * unit <= x < edge + x1 * unit
                        and floor - (height + 1.2) * unit <= y < floor):
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



def _markdown(text: str) -> str:
    """사용설명서를 화면에 띄울 조각으로. 이 문서가 쓰는 것만 다룬다.

    범용 변환기를 끌어오면 휠이 하나 더 늘고, 웹앱은 그것을 통째로 내려받아야
    한다. 문서는 우리가 쓰는 것이라 쓰이는 표기가 정해져 있으므로 여기서 그만큼
    만 옮긴다 — 제목·표·목록·인용·코드·굵게·코드조각.
    """
    import html as _html
    import re

    def inline(raw: str) -> str:
        out = _html.escape(raw)
        out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
        out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
        out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)   # 링크는 글자만
        return out

    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]

        if line.startswith("```"):                      # 코드 덩어리
            index += 1
            block = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(_html.escape(lines[index]))
                index += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            index += 1
            continue

        if line.startswith("|") and index + 1 < len(lines) and set(
                lines[index + 1].replace("|", "").strip()) <= set("-: "):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            head = cells(line)
            index += 2
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(cells(lines[index]))
                index += 1
            out.append("<table><thead><tr>"
                       + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row)
                                 + "</tr>" for row in rows)
                       + "</tbody></table>")
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if line.startswith(">"):
            block = []
            while index < len(lines) and lines[index].startswith(">"):
                block.append(lines[index].lstrip(">").strip())
                index += 1
            out.append("<blockquote>" + inline(" ".join(block)) + "</blockquote>")
            continue

        bullet = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", line)
        if bullet:
            ordered = bullet.group(1).endswith(".")
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                found = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[index])
                if found and found.group(1).endswith(".") == ordered:
                    items.append(found.group(2))
                    index += 1
                    continue
                # 이어지는 줄. 원문은 긴 항목을 다음 줄로 접어 쓰므로, 여기서
                # 붙이지 않으면 한 항목이 문단으로 떨어져 나온다.
                if (items and lines[index].strip()
                        and not re.match(r"^(#{1,4}\s|\||>|```)", lines[index])):
                    items[-1] += " " + lines[index].strip()
                    index += 1
                    continue
                break
            out.append(f"<{tag}>"
                       + "".join(f"<li>{inline(item)}</li>" for item in items)
                       + f"</{tag}>")
            continue

        if line.strip() in ("", "---"):
            index += 1
            continue

        block = []
        while index < len(lines) and lines[index].strip() and not re.match(
                r"^(#{1,4}\s|\||>|```|\s*([-*]|\d+\.)\s)", lines[index]):
            block.append(lines[index].strip())
            index += 1
        out.append("<p>" + inline(" ".join(block)) + "</p>")

    return "\n".join(out)


def _write_help(target: Path) -> None:
    """사용설명서를 앱 안에서 읽을 수 있게 넣는다.

    다른 창으로 나가 읽게 하면 입력하던 것을 잃는다. 산출 도중에 물어볼 것이
    생기므로 화면을 떠나지 않고 볼 수 있어야 한다.

    **설치판 설명서를 그대로 쓰면 안 된다.** 그 문서는 '설치 파일을
    더블클릭하라' 로 시작하고 명령행과 ``%APPDATA%`` 를 말한다 — 브라우저로
    쓰는 사람에게는 하나도 해당되지 않는다. 웹앱용 문서를 따로 둔다.
    """
    source = ROOT / "docs" / "웹앱-사용설명서.md"
    if not source.exists():                       # 없으면 아예 넣지 않는다.
        raise FileNotFoundError(f"{source} 가 없습니다")
    target.write_text(_markdown(source.read_text(encoding="utf-8")), encoding="utf-8")


#: 자료실에서 내려받는 문서. (배포본 파일명, 원본)
_DOCS: Final = (
    ("사용설명서.md", "웹앱-사용설명서.md"),
    ("계리방법론.md", "계리방법론.md"),
)


def _copy_docs(target: Path) -> None:
    """자료실에서 내려받을 문서를 배포본에 넣는다.

    화면 안(물음표)에서 읽는 것과 **같은 원본** 을 쓴다. 파일을 따로 만들어
    두면 화면에 보이는 설명과 받아 간 파일이 서서히 갈라진다.

    받는 사람이 그대로 남에게 보낼 수 있어야 하므로 주소는 적지 않는다 —
    문서만 돌아다녀도 주소까지 같이 돌지는 않게 한다.
    """
    for name, origin in _DOCS:
        source = ROOT / "docs" / origin
        if not source.exists():
            raise FileNotFoundError(f"{source} 가 없습니다")
        (target / name).write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
    print(f"  문서 {len(_DOCS)}개")



def _write_cname(target: Path) -> None:
    """맞춤 도메인을 배포본에 넣는다.

    GitHub Pages 는 배포된 파일 안의 ``CNAME`` 을 보고 도메인을 잡는다. 설정
    화면에서 한 번 넣어 두어도, 워크플로가 올린 판에 이 파일이 없으면 배포할
    때마다 도메인이 풀린다.

    도메인은 **환경변수 ``PAGES_DOMAIN`` 을 먼저 본다.** 저장소에 적어 두면
    누구나 도메인으로 이 저장소를 찾을 수 있기 때문이다 — 공개 저장소는 코드
    검색이 되므로, 주소를 아는 사람이 검색창에 그대로 쳐 넣으면 걸린다. 주소와
    저장소를 잇는 끈은 소스에 두지 않는다.

    ``webapp/CNAME`` 은 그 다음이다. 손으로 빌드해 볼 때 환경변수를 매번
    넘기지 않아도 되게 남겨 둔 자리이며, 이 파일은 저장소에 커밋하지 않는다.

    둘 다 없으면 아무것도 하지 않는다 — 도메인을 안 쓰는 동안에는 ``github.io``
    주소로 그냥 열린다.

    **도메인은 찍지 않는다.** 공개 저장소의 실행 기록은 누구나 읽을 수 있어,
    로그에 한 줄 남기면 소스에서 뺀 의미가 없어진다.
    """
    domain = os.environ.get("PAGES_DOMAIN", "").strip()
    if not domain:
        source = Path(__file__).parent / "CNAME"
        if not source.exists():
            return
        domain = source.read_text(encoding="utf-8").strip()
    if not domain:
        return
    target.write_text(domain + "\n", encoding="utf-8")
    print("  맞춤 도메인 설정됨")


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
    _write_help(DIST / "help.html")
    _copy_docs(DIST)
    _write_cname(DIST / "CNAME")
    (DIST / "icon-180.png").write_bytes(_png(180))
    (DIST / "icon-512.png").write_bytes(_png(512))
    # 안드로이드는 192 를 먼저 찾고, maskable 이 없으면 아이콘을 흰 판에
    # 얹어 letterbox 로 보여 준다 — 남의 앱들과 나란히 두면 그것만 튄다.
    (DIST / "icon-192.png").write_bytes(_png(192))
    (DIST / "icon-maskable-512.png").write_bytes(_png(512, safe=0.62))

    # index.html 에 휠 목록을 심는다. 파일명이 버전을 담고 있으므로 하드코딩하면
    # 버전을 올릴 때마다 손으로 고쳐야 한다.
    page = (DIST / "index.html").read_text(encoding="utf-8")
    page = page.replace("__WHEELS__", json.dumps(wheels, ensure_ascii=False))
    (DIST / "index.html").write_text(page, encoding="utf-8")

    # 서비스워커 — 내용 해시를 캐시 이름에 넣어 앱을 고치면 캐시가 자연히 갈린다.
    #
    # 사전 캐시 목록에는 **화면 파일만** 넣는다. 런타임(파이오다이드)과 휠은
    # 합쳐 14MB 인데, 설치 때 그것까지 받게 하면 그중 하나만 실패해도 새 일꾼이
    # 설치되지 않고 옛 판이 계속 돈다. 무거운 것은 처음 쓸 때 받아 캐시에
    # 넣으므로(서비스워커의 fetch 처리), 오프라인 동작은 그대로다.
    stamp = _hash_dir(DIST)
    heavy = ("pyodide/", "wheels/")
    files = sorted(
        "./" + p.relative_to(DIST).as_posix()
        for p in DIST.rglob("*")
        if p.is_file()
        and p.name != "sw.js"
        and not p.relative_to(DIST).as_posix().startswith(heavy)
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

