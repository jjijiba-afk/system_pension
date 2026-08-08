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


@pytest.fixture(scope="module")
def app_url():
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(DIST)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def page(app_url):
    """엔진 부팅이 오래 걸리므로 한 번 띄운 페이지를 모듈 전체가 나눠 쓴다."""
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROMIUM))
        page = browser.new_page()
        page.goto(app_url)
        page.wait_for_selector("#run:not([disabled])", timeout=120_000)
        yield page
        browser.close()


def test_upload_run_download(page, tmp_path) -> None:
    """업로드 → 브라우저 안 산출 → 진짜 엑셀 내려받기까지."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    page.set_input_files("#roster", str(roster))
    page.check("#asrc-file")
    page.set_input_files("#assumptions", str(assumptions))
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)

    summary = page.inner_text("#summary")
    assert "확정급여채무" in summary

    with page.expect_download() as captured:
        page.click("#dl-result")
    payload = Path(captured.value.path()).read_bytes()
    assert payload[:2] == b"PK"


def test_editor_tab_builds_assumptions_and_runs(page, tmp_path) -> None:
    """산출가정 입력 탭에서 만든 가정만으로 산출까지 이어져야 한다."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    page.set_input_files("#roster", str(roster))

    page.click("#tab-edit")
    page.click("#ed-example")  # 예시 값 채우기 → 할인율이 생겨 편집기 소스가 열린다

    # 저장하면 산출가정.xlsx 가 내려오고, 산출 탭의 소스가 편집기로 바뀐다.
    with page.expect_download() as captured:
        page.click("#ed-save")
    assert Path(captured.value.path()).read_bytes()[:2] == b"PK"

    page.click("#tab-calc")
    assert page.is_checked("#asrc-editor")
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    assert "확정급여채무" in page.inner_text("#summary")


def test_library_registers_curve_into_editor(page, tmp_path) -> None:
    """기본가정 관리에 금리표를 등록하면 편집기 할인율에 적용할 수 있어야 한다."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "KIS_NET금리"
    ws.append(["No", "기준일자", "구분", "등급", "3월", "1년", "5년", "20년"])
    ws.append([1, None, "공모 무보증회사채", "AA0", 3.01, 3.122, 3.62, 5.26])
    source = tmp_path / "금리.xlsx"
    wb.save(source)

    page.click("#tab-lib")
    page.set_input_files("#lib-curve-file", str(source))
    page.fill("#lib-curve-name", "KIS_E2E")
    page.click("#lib-curve-add")
    page.wait_for_selector("#lib-curve-list table", timeout=30_000)
    assert "KIS_E2E" in page.inner_text("#lib-curve-list")

    page.click("#tab-edit")
    page.select_option("#ed-curve", "KIS_E2E")
    page.select_option("#ed-grade", "AA0")
    page.click("#ed-curve-apply")
    assert "할인율에 넣었습니다" in page.inner_text("#ed-status")

    # 3월 만기(0.25년)가 살아 있어야 한다 — 내림하면 안 되는 값.
    page.click("#ed-subtabs >> text=할인율")
    first_key = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input").first
    assert first_key.input_value() == "0.25"


def test_run_history_save_and_restore(page, tmp_path) -> None:
    """산출 → 이름 붙여 저장 → 목록 → 입력 불러와 재산출까지."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    page.click("#tab-calc")
    page.set_input_files("#roster", str(roster))
    page.check("#asrc-file")
    page.set_input_files("#assumptions", str(assumptions))
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)

    page.fill("#run-name", "2412 1번단체")
    page.click("#run-save")
    page.wait_for_selector("text=저장했습니다", timeout=30_000)

    page.click("#tab-runs")
    listing = page.inner_text("#runs-list")
    assert "2412 1번단체" in listing and "확정급여채무" not in listing

    # 입력을 불러오면 산출 탭으로 돌아오고, 파일을 고르지 않아도 재산출된다.
    page.click("#runs-list >> text=입력 불러오기")
    page.wait_for_selector("#loaded-run-banner", state="visible")
    assert page.is_checked("#asrc-saved")
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    assert "확정급여채무" in page.inner_text("#summary")

    # 저장된 결과 보기 대화상자.
    page.click("#tab-runs")
    page.click("#runs-list >> text=결과 보기")
    page.wait_for_selector("#run-dialog[open]")
    assert "확정급여채무" in page.inner_text("#run-dialog-summary")
    page.click("#run-dialog >> text=닫기")


def test_standard_rates_fill_the_grids(page) -> None:
    """내장 표준률 불러오기 — 15~70세 표가 채워져야 한다."""
    page.click("#tab-edit")
    page.select_option("#ed-rates", "__builtin__")
    page.click("#ed-rates-load")
    assert "불러왔습니다" in page.inner_text("#ed-status")

    page.click("#ed-subtabs >> text=사망률")
    first_age = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input").first
    assert first_age.input_value() == "15"
