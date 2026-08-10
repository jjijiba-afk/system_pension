"""PC 에서 **전체 기능 화면** 을 띄운다.

이 프로그램에는 화면이 둘이다.

``연금계리산출.exe`` (tkinter)
    파일 세 칸을 채우고 [산출 실행] 을 누르는 창. 결산 실무의 본 작업이라
    한 번에 보이는 것이 낫다.

전체 기능 화면 (웹앱)
    분석 그래프·계리평가 보고서·산출 내역·단체 관리·사번 조회까지 다 있는
    화면. 원래 아이패드용으로 만들었지만 **브라우저면 어디서든 같다.**

두 번째를 tkinter 로 다시 만들지 않는 이유는 분명하다. 그림이 필요한 화면이고
(연차별 채무 곡선·직군별 막대·민감도), tkinter 로는 그걸 그릴 수단이 캔버스에
선을 긋는 것뿐이다. 인쇄→PDF 도 브라우저가 이미 해 준다. 같은 것을 두 번
만들면 **두 화면의 숫자가 언젠가 갈린다** — 이 프로젝트가 계속 경계해 온 일이다.

그래서 PC 에서는 배포 꾸러미에 이미 들어 있는 그 웹앱을 **로컬 서버로 띄워
기본 브라우저로 연다.** 계산은 브라우저 안 엔진(Pyodide)이 하므로 이 서버는
파일을 내어 줄 뿐이고, 바깥으로 나가는 통신은 없다.

주소는 ``127.0.0.1`` 에만 연다. 사내망에 열려면 :func:`pension.web.serve` 쪽
(``pension-cli web --host 0.0.0.0``)을 쓰되, 인증이 없다는 점을 알고 써야 한다.
"""

from __future__ import annotations

import functools
import http.server
import mimetypes
import os
import socketserver
import sys
import threading
from pathlib import Path

__all__ = ["MissingAppError", "app_root", "open_in_browser", "serve"]

#: 배포 꾸러미에서 웹앱이 놓이는 이름들. 사람이 폴더 이름을 바꿔도 웬만하면 찾는다.
_FOLDER_NAMES = ("아이패드웹앱", "웹앱", "dist", "webapp")

#: 이 파일이 있어야 웹앱 폴더로 인정한다.
_MARK = "index.html"

#: 이 중 하나는 있어야 **빌드된** 웹앱이다.
#:
#: ``index.html`` 만 보면 개발 트리의 원본 폴더(``webapp/app``)도 걸린다. 거기에는
#: 엔진(pyodide·휠)이 없어 브라우저에서 화면만 뜨고 아무것도 못 한다 — 찾은
#: 순간에 걸러 내는 편이 낫다.
_BUILT = ("pyodide", "wheels")

#: 환경변수로 직접 지정하는 길. 개발 중이거나 폴더를 딴 곳에 둔 경우.
_ENV = "PENSION_WEBAPP"


class MissingAppError(RuntimeError):
    """전체 기능 화면 폴더를 찾지 못했다."""


def _bases() -> list[Path]:
    """웹앱 폴더가 있을 만한 자리. 앞에 있는 것부터 본다."""
    found: list[Path] = []
    # PyInstaller 로 묶은 EXE 는 실행 파일 옆에 꾸러미가 풀려 있다.
    with_exe = Path(sys.executable).resolve().parent
    found.append(with_exe)
    if getattr(sys, "frozen", False):
        # 꾸러미 루트가 한 단계 위인 배치도 있다.
        found.append(with_exe.parent)
    if sys.argv and sys.argv[0]:
        found.append(Path(sys.argv[0]).resolve().parent)
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        found.append(Path(meipass))
    # 개발 트리: src/pension/localapp.py → 저장소 뿌리/webapp/dist
    found.append(Path(__file__).resolve().parents[2] / "webapp")
    return found


def _is_app(path: Path) -> bool:
    """빌드된 웹앱 폴더인지."""
    return ((path / _MARK).is_file()
            and any((path / name).is_dir() for name in _BUILT))


def app_root() -> Path:
    """전체 기능 화면(웹앱)이 들어 있는 폴더.

    :raises MissingAppError: 어디에서도 찾지 못했을 때. 어디를 뒤졌는지
        메시지에 적는다 — 폴더를 옮겨 둔 사람이 스스로 고칠 수 있어야 한다.
    """
    told = os.environ.get(_ENV, "").strip()
    if told:
        path = Path(told).expanduser()
        if _is_app(path):
            return path
        raise MissingAppError(
            f"{_ENV} 가 가리키는 곳에 빌드된 웹앱이 없습니다: {path}\n"
            f"({_MARK} 과 {' 또는 '.join(_BUILT)} 폴더가 있어야 합니다)")

    looked: list[Path] = []
    for base in _bases():
        for name in _FOLDER_NAMES:
            candidate = base / name
            looked.append(candidate)
            if _is_app(candidate):
                return candidate
        looked.append(base)
        if _is_app(base):
            return base

    raise MissingAppError(
        "전체 기능 화면(웹앱) 폴더를 찾지 못했습니다.\n\n"
        "배포 꾸러미의 '아이패드웹앱' 폴더가 실행 파일과 **같은 자리에** 있어야 합니다. "
        "zip 에서 실행 파일만 꺼내 쓰면 이 화면을 열 수 없습니다.\n\n"
        "찾아본 곳:\n  " + "\n  ".join(str(p) for p in dict.fromkeys(looked))
    )


class _Quiet(http.server.SimpleHTTPRequestHandler):
    """조용한 정적 핸들러.

    요청마다 콘솔에 한 줄씩 쏟으면 EXE 의 검은 창이 스크롤로 가득 찬다.
    파일을 내어 주는 것 말고는 하는 일이 없다.
    """

    def log_message(self, format: str, *args) -> None:
        pass

    def end_headers(self) -> None:
        # 웹앱을 새로 배포했는데 옛 화면이 뜨는 일을 막는다. 로컬 파일이라
        # 매번 읽어도 느리지 않다.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def _register_types() -> None:
    """브라우저가 알아들을 MIME 을 못 박는다.

    ``.wasm`` 을 모르는 채로 내어 주면 ``application/octet-stream`` 이 되고,
    브라우저가 스트리밍 컴파일을 거부해 엔진이 뜨지 않는다. 윈도우는 MIME 을
    레지스트리에서 읽어 와 PC 마다 답이 달라지므로 여기서 직접 정한다.
    """
    mimetypes.add_type("application/wasm", ".wasm")
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")
    mimetypes.add_type("application/manifest+json", ".webmanifest")


def serve(directory: str | Path | None = None, port: int = 0):
    """웹앱을 로컬 서버로 띄운다. 서버 객체를 돌려준다(아직 돌지 않는다).

    :param directory: 웹앱 폴더. 생략하면 :func:`app_root` 로 찾는다.
    :param port: 0 이면 비어 있는 포트를 운영체제가 골라 준다. 고정 포트를
        쓰면 이미 그 포트를 쓰는 프로그램이 있을 때 뜨지 않는다.
    """
    root = Path(directory) if directory is not None else app_root()
    _register_types()

    handler = functools.partial(_Quiet, directory=str(root))
    # 브라우저가 여러 파일을 동시에 받아 간다. 한 번에 하나만 처리하면
    # 14MB 짜리 런타임을 받는 동안 화면이 멈춘 것처럼 보인다.
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    return server


def open_in_browser(directory: str | Path | None = None, port: int = 0) -> tuple:
    """서버를 백그라운드로 띄우고 기본 브라우저로 연다.

    :returns: ``(서버, 주소)``. 서버는 데몬 스레드에서 돌고 있으므로,
        부른 쪽이 살아 있는 동안만 열려 있다.
    """
    import webbrowser

    server = serve(directory, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    webbrowser.open(url)
    return server, url
