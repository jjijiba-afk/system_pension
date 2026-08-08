"""아이패드 웹앱(브라우저 안 엔진) 끝까지 검증.

Playwright 와 Chromium, 그리고 빌드된 dist 가 있을 때만 돈다 — CI 기본
잡에서는 건너뛰고, 로컬·전용 잡에서 실제 브라우저로 확인한다.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

DIST = Path(__file__).resolve().parent.parent / "webapp" / "dist"
CHROMIUM = Path("/opt/pw-browsers/chromium")

pytestmark = [
    pytest.mark.skipif(not DIST.exists(), reason="webapp/build.py 를 먼저 실행"),
    pytest.mark.skipif(not CHROMIUM.exists(), reason="Chromium 없음"),
]


@pytest.fixture
def app_url():
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(DIST)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
    server.server_close()


def test_engine_runs_in_the_browser(app_url, tmp_path) -> None:
    """업로드 → 브라우저 안 산출 → 진짜 엑셀 내려받기까지."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROMIUM))
        page = browser.new_page()
        page.goto(app_url)
        page.wait_for_selector("#run:not([disabled])", timeout=120_000)

        page.set_input_files("#roster", str(roster))
        page.set_input_files("#assumptions", str(assumptions))
        page.click("#run")
        page.wait_for_selector("#result", state="visible", timeout=180_000)

        summary = page.inner_text("#summary")
        assert "확정급여채무" in summary

        with page.expect_download() as captured:
            page.click("#dl-result")
        payload = Path(captured.value.path()).read_bytes()
        assert payload[:2] == b"PK"
        browser.close()
