"""아이패드·다른 PC 에서 쓰는 웹 화면.

tkinter GUI 는 윈도우 데스크톱에서만 돈다. 담당자가 아이패드나 다른 자리의
PC 에서 산출해야 할 때를 위해, 같은 파이프라인을 **브라우저 화면** 으로 연다.

    pension web                     # 이 PC 에서만 (http://127.0.0.1:8035)
    pension web --host 0.0.0.0      # 같은 네트워크의 아이패드에서 접속

서버는 표준 라이브러리 ``http.server`` 만 쓴다. EXE 하나로 돌아야 하는 배포
조건이 웹이라고 달라지지 않기 때문이다. 명부는 업로드 즉시 임시 폴더에서
산출되고, 결과는 메모리에만 잠시 들고 있다가 내려받으면 그만이다 — 서버에
개인정보가 파일로 남지 않는다.

**인터넷에 노출하지 말 것.** 인증이 없는 사내망용 화면이다. 명부에는
생년월일·임금이 들어 있으므로 같은 사무실 네트워크 밖으로 열면 안 된다.
"""

from __future__ import annotations

import html
import io
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import PensionDataError, Severity

__all__ = ["PensionWebServer", "serve"]


# ── multipart/form-data 해석 ──────────────────────────────────────
# 파이썬 3.13 에서 cgi 모듈이 빠졌다. 필요한 것은 "파일 두 개 + 텍스트 몇 칸"
# 뿐이므로 그만큼만 직접 푼다.

@dataclass(slots=True)
class _Part:
    name: str = ""
    filename: str = ""
    body: bytes = b""


def _parse_multipart(body: bytes, content_type: str) -> dict[str, _Part]:
    """폼 전송 본문을 이름 → 파트로 푼다. 형식이 어긋난 파트는 버린다."""
    marker = "boundary="
    index = content_type.find(marker)
    if index < 0:
        return {}
    boundary = content_type[index + len(marker):].split(";")[0].strip().strip('"')
    delimiter = b"--" + boundary.encode()

    parts: dict[str, _Part] = {}
    for chunk in body.split(delimiter):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, _, payload = chunk.partition(b"\r\n\r\n")
        part = _Part(body=payload)
        for line in head.decode("utf-8", "replace").splitlines():
            if not line.lower().startswith("content-disposition"):
                continue
            for token in line.split(";"):
                token = token.strip()
                if token.startswith("name="):
                    part.name = token[5:].strip('"')
                elif token.startswith("filename="):
                    part.filename = token[9:].strip('"')
        if part.name:
            parts[part.name] = part
    return parts


# ── 결과 보관함 ───────────────────────────────────────────────────

@dataclass(slots=True)
class _ResultStore:
    """내려받기 전까지 결과 파일을 메모리에 들고 있는 곳.

    디스크에 쓰지 않는 이유는 하나다 — 명부가 서버 PC 에 파일로 남으면 안 된다.
    항목 수를 제한해 오래된 것부터 버린다.
    """

    limit: int = 20
    _items: dict[str, tuple[str, bytes]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def put(self, filename: str, payload: bytes) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            while len(self._items) >= self.limit:
                self._items.pop(next(iter(self._items)))
            self._items[token] = (filename, payload)
        return token

    def get(self, token: str) -> tuple[str, bytes] | None:
        with self._lock:
            return self._items.get(token)


# ── 화면 ─────────────────────────────────────────────────────────

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
       max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; line-height: 1.55; }
h1 { font-size: 1.5rem; color: #1F3864; }
h1 small { font-weight: normal; font-size: 0.9rem; color: #5B6478; display: block; }
fieldset { border: 1px solid #C4CBD8; border-radius: 10px; margin: 0 0 16px; padding: 14px 16px; }
legend { font-weight: bold; color: #1F3864; padding: 0 6px; }
label { display: block; margin: 10px 0 4px; font-weight: 600; }
input[type=file] { width: 100%; padding: 12px; border: 1px dashed #8894AB;
                   border-radius: 8px; font-size: 1rem; }
input[type=text] { width: 100%; padding: 12px; border: 1px solid #C4CBD8;
                   border-radius: 8px; font-size: 1.05rem; }
.hint { color: #5B6478; font-size: 0.85rem; margin-top: 2px; }
.check { display: flex; align-items: center; gap: 10px; margin: 10px 0; font-weight: 600; }
.check input { width: 22px; height: 22px; }
button, a.button { display: block; width: 100%; padding: 16px; margin-top: 18px;
  background: #1F3864; color: #fff; border: 0; border-radius: 10px;
  font-size: 1.15rem; font-weight: bold; text-align: center; text-decoration: none; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
td, th { border: 1px solid #C4CBD8; padding: 9px 12px; text-align: left; font-size: 0.95rem; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.error { background: #FCE4E4; border-radius: 8px; padding: 10px 14px; margin: 6px 0; }
.warn-box { background: #FFF6E0; border-radius: 8px; padding: 10px 14px; margin: 6px 0;
            font-size: 0.9rem; }
.notice { color: #5B6478; font-size: 0.9rem; }
"""

_FORM_PAGE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>연금계리 산출</title><style>{style}</style></head><body>
<h1>연금계리 산출<small>K-IFRS 1019 확정급여채무 (PUC) — 사내망 전용</small></h1>
<form method="post" action="/calc" enctype="multipart/form-data">
<fieldset><legend>1. 입력 파일</legend>
  <label>명부 파일 (.xls / .xlsx / .xlsm)</label>
  <input type="file" name="roster" accept=".xls,.xlsx,.xlsm" required>
  <div class="hint">Input · 재직자명부 · 퇴직자명부 시트가 들어 있는 통합문서</div>
  <label>기초율 파일 (.xlsx)</label>
  <input type="file" name="assumptions" accept=".xlsx,.xlsm" required>
  <div class="hint">할인율 · 승급률 · 퇴직률 · 사망률 · 지급률 · 지급규정</div>
</fieldset>
<fieldset><legend>2. 산출 옵션</legend>
  <div class="check"><input type="checkbox" name="force" id="force">
    <label for="force" style="margin:0">검증 오류가 있어도 산출 강행 (검토용)</label></div>
  <div class="check"><input type="checkbox" name="sensitivity" id="s" checked>
    <label for="s" style="margin:0">민감도분석 포함</label></div>
  <div class="check"><input type="checkbox" name="longterm" id="l" checked>
    <label for="l" style="margin:0">기타장기종업원급여 별도 산출</label></div>
</fieldset>
<fieldset><legend>3. 전기 산출 결과 (증감분석용 · 최초 평가면 비워 두세요)</legend>
  <label>전기말 확정급여채무 (원)</label>
  <input type="text" name="prior_dbo" inputmode="numeric" placeholder="예: 18,500,000,000">
  <label>전기말 할인율</label>
  <input type="text" name="prior_rate" inputmode="decimal" placeholder="예: 4.1%">
</fieldset>
<button type="submit">산출 실행</button>
<p class="notice">산출은 서버 PC 에서 이루어지며, 명부는 산출 즉시 지워지고
파일로 남지 않습니다. 이 화면은 인증이 없으므로 사내 네트워크 밖으로 열지 마세요.</p>
</form></body></html>"""


def _money(value: float) -> str:
    return f"{value:,.0f}"


def _result_page(run, token: str, members_token: str) -> str:
    v = run.valuation
    if run.assumptions.discount.flat is None:
        rate_row = f"{v.single_discount_rate():.3%} <span class='notice'>(수익률곡선기법 단일할인율)</span>"
    else:
        rate_row = f"{run.assumptions.discount.level_rate:.3%}"

    rows = [
        ("산출기준일", str(run.config.base_date)),
        ("적용 할인율", rate_row),
        ("산출대상 인원", f"{v.headcount:,}명"),
        ("확정급여채무 (DBO)", _money(v.dbo) + " 원"),
        ("당기근무원가", _money(v.service_cost) + " 원"),
        ("이자원가 (차기)", _money(v.interest_cost) + " 원"),
        ("듀레이션", f"{v.duration:.1f} 년"),
    ]
    if run.longterm is not None:
        rows.append(("장기급여채무", _money(run.longterm.dbo) + " 원"))
    if run.rollforward is not None:
        rows.append(("보험수리적손익", _money(run.rollforward.actuarial_gain_loss) + " 원"))

    body = "".join(
        f"<tr><th>{html.escape(label)}</th><td class='num'>{value}</td></tr>"
        for label, value in rows
    )

    group_rows = "".join(
        f"<tr><td>{html.escape(group)}</td><td class='num'>{count:,}</td>"
        f"<td class='num'>{_money(dbo)}</td><td class='num'>{_money(sc)}</td></tr>"
        for group, (count, dbo, sc) in v.by_job_group().items()
    )

    issues = run.issues
    verdict = (
        f"오류 {len(issues.errors)}건 / 경고 {len(issues.warnings)}건 — "
        "상세는 결과 파일의 검증리포트 시트"
    )
    excluded = "".join(
        f"<li>{count:,}명 — {html.escape(reason)}</li>"
        for reason, count in v.exclusion_summary().items()
    )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>산출 결과</title><style>{_STYLE}</style></head><body>
<h1>산출 결과</h1>
<table>{body}</table>
<h1 style="font-size:1.1rem">직군별</h1>
<table><tr><th>직군</th><th>인원</th><th>확정급여채무</th><th>당기근무원가</th></tr>
{group_rows}</table>
<div class="warn-box">{verdict}</div>
{f"<div class='warn-box'>산출 제외<ul>{excluded}</ul></div>" if excluded else ""}
<a class="button" href="/download/{token}">결과 엑셀 내려받기</a>
<a class="button" style="background:#44546A" href="/download/{members_token}">개인별 결과 내려받기</a>
<a class="button" style="background:#8894AB" href="/">새 산출</a>
</body></html>"""


def _error_page(title: str, items: list[str], *, hint: str = "") -> str:
    listing = "".join(f"<div class='error'>{html.escape(item)}</div>" for item in items[:40])
    more = f"<p class='notice'>… 외 {len(items) - 40}건</p>" if len(items) > 40 else ""
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>
<h1>{html.escape(title)}</h1>{listing}{more}
{f"<p>{html.escape(hint)}</p>" if hint else ""}
<a class="button" href="/">돌아가기</a></body></html>"""


# ── 서버 ─────────────────────────────────────────────────────────

class PensionWebServer(ThreadingHTTPServer):
    """산출 웹서버. 표준 핸들러에 결과 보관함만 얹는다."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int]):
        super().__init__(address, _Handler)
        self.results = _ResultStore()


class _Handler(BaseHTTPRequestHandler):
    server: PensionWebServer

    # 로그는 한 줄이면 된다. 기본 구현은 요청마다 stderr 에 쏟는다.
    def log_message(self, format: str, *args) -> None:
        print(f"  {self.address_string()} {format % args}")

    def _send_html(self, page: str, status: int = 200) -> None:
        payload = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self._send_html(_FORM_PAGE.format(style=_STYLE))
            return
        if self.path.startswith("/download/"):
            token = self.path.rsplit("/", 1)[-1]
            found = self.server.results.get(token)
            if found is None:
                self._send_html(_error_page(
                    "내려받기 만료", ["결과가 만료되었습니다. 다시 산출해 주세요."]), 404)
                return
            filename, payload = found
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            from urllib.parse import quote

            # 헤더는 latin-1 만 허용된다. 한글 파일명은 RFC 5987 대로 퍼센트 인코딩.
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(filename)}",
            )
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._send_html(_error_page("없는 주소", [self.path]), 404)

    def do_POST(self) -> None:
        if self.path != "/calc":
            self._send_html(_error_page("없는 주소", [self.path]), 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        parts = _parse_multipart(
            self.rfile.read(length), self.headers.get("Content-Type", "")
        )
        try:
            page = self._run(parts)
        except PensionDataError as exc:
            messages = [str(i) for i in exc.issues if i.severity is Severity.ERROR]
            page = _error_page(
                "명부 검증 오류로 산출을 중단했습니다", messages,
                hint="명부를 고치거나, '검증 오류가 있어도 산출 강행' 을 켜고 다시 실행하세요.",
            )
        except Exception as exc:  # 화면 없는 서버이므로 어떤 실패든 페이지로 알린다
            page = _error_page("산출하지 못했습니다", [f"{type(exc).__name__}: {exc}"])
        self._send_html(page)

    def _run(self, parts: dict[str, _Part]) -> str:
        from .cli import _percent
        from .members import write_member_export
        from .pipeline import PriorPeriod, RunOptions, run_valuation
        from .report import write_report

        roster = parts.get("roster")
        assumptions = parts.get("assumptions")
        if roster is None or not roster.filename or assumptions is None:
            return _error_page("파일이 없습니다", ["명부와 기초율 파일을 모두 골라 주세요."])

        def number(name: str, default: float = 0.0) -> float:
            token = parts[name].body.decode("utf-8", "replace").strip() if name in parts else ""
            token = token.replace(",", "")
            if not token:
                return default
            return _percent(token) if name.endswith("rate") else float(token)

        with TemporaryDirectory(prefix="pension-web-") as workdir:
            base = Path(workdir)
            # 확장자에 따라 xls/xlsx 리더가 갈리므로 원래 확장자를 지킨다.
            roster_path = base / ("명부" + Path(roster.filename).suffix.lower())
            roster_path.write_bytes(roster.body)
            assumptions_path = base / "기초율.xlsx"
            assumptions_path.write_bytes(assumptions.body)

            output = base / "산출결과.xlsx"
            run = run_valuation(RunOptions(
                roster_path=roster_path,
                assumptions_path=assumptions_path,
                output_path=output,
                include_sensitivity="sensitivity" in parts,
                include_longterm="longterm" in parts,
                allow_errors="force" in parts,
                prior=PriorPeriod(
                    dbo=number("prior_dbo"),
                    discount_rate=number("prior_rate"),
                ),
            ))
            write_report(run, output)
            members = base / "개인별.xlsx"
            write_member_export(run, members)

            token = self.server.results.put("산출결과.xlsx", output.read_bytes())
            members_token = self.server.results.put("개인별결과.xlsx", members.read_bytes())
        # TemporaryDirectory 가 여기서 지워진다 — 명부가 디스크에 남지 않는다.
        return _result_page(run, token, members_token)


def _lan_addresses() -> list[str]:
    """이 PC 의 사내망 주소들. 아이패드에 입력할 주소를 알려 주기 위한 것."""
    import socket

    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    return found


def serve(host: str = "127.0.0.1", port: int = 8035) -> int:
    """웹서버를 돌린다. Ctrl+C 로 멈출 때까지 반환하지 않는다."""
    server = PensionWebServer((host, port))
    shown_port = server.server_address[1]

    print("연금계리 산출 웹서버")
    print(f"  이 PC 에서:      http://127.0.0.1:{shown_port}")
    if host not in ("127.0.0.1", "localhost"):
        for address in _lan_addresses():
            print(f"  아이패드에서:    http://{address}:{shown_port}")
        print("\n  ※ 인증이 없는 사내망용입니다. 인터넷(공유기 포트포워딩 등)에 노출하지 마세요.")
    else:
        print("\n  아이패드 등 다른 기기에서 접속하려면:  pension web --host 0.0.0.0")
    print("  멈추려면 Ctrl+C\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 멈췄습니다.")
    finally:
        server.server_close()
    return 0


def _run_in_memory(server: PensionWebServer) -> None:
    """시험용 — 백그라운드 스레드에서 서버를 돌린다."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


# 시험에서 io 를 직접 참조하지는 않지만, 향후 스트리밍 응답을 위해 남겨 둔다.
_ = io
