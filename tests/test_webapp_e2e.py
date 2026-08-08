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
def shared_dir(tmp_path_factory):
    """테스트끼리 주고받는 파일(보관함 zip)을 두는 곳."""
    return tmp_path_factory.mktemp("웹앱공유")


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(CHROMIUM))
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def page(browser, app_url):
    """엔진 부팅이 오래 걸리므로 한 번 띄운 페이지를 모듈 전체가 나눠 쓴다."""
    page = browser.new_page()
    page.goto(app_url)
    page.wait_for_selector("#run:not([disabled])", timeout=120_000)
    yield page
    page.close()


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


def test_saved_roster_and_prior_link_and_backup(page, tmp_path, shared_dir) -> None:
    """올렸던 명부 재사용 · 전기 DBO 연결 · 기기 밖 보관함까지."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    # 전기 산출을 하나 만들어 저장한다.
    page.click("#tab-calc")
    page.set_input_files("#roster", str(roster))
    page.check("#asrc-file")
    page.set_input_files("#assumptions", str(assumptions))

    # 명부를 목록에 저장 — prompt 로 이름을 묻는다.
    page.once("dialog", lambda dialog: dialog.accept("1번단체 명부"))
    page.click("#roster-save")
    page.wait_for_selector("text=목록에 저장했습니다", timeout=30_000)

    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    page.fill("#run-name", "2312 1번단체")
    page.click("#run-save")
    page.wait_for_selector("text=저장했습니다", timeout=30_000)

    # 저장된 명부만으로 (파일 재선택 없이) 당기를 돌린다.
    page.reload()
    page.wait_for_selector("#run:not([disabled])", timeout=120_000)
    page.select_option("#roster-saved", "1번단체 명부")
    page.set_input_files("#assumptions", str(assumptions))

    # 전기 산출을 고르면 DBO·할인율이 자동으로 채워진다.
    page.select_option("#prior-run", "2312 1번단체")
    assert page.input_value("#prior_dbo").replace(",", "").isdigit()
    assert page.input_value("#prior_rate").endswith("%")

    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    assert "보험수리적손익" in page.inner_text("#summary")

    # 보관함 내보내기 — 기기 밖에 둘 zip 이 실제로 떨어져야 한다.
    page.click("#tab-runs")
    with page.expect_download() as captured:
        page.click("#backup-export")
    archive = shared_dir / "보관함.zip"
    captured.value.save_as(str(archive))
    assert archive.read_bytes()[:2] == b"PK"


def test_backup_restores_on_a_clean_device(browser, app_url, shared_dir) -> None:
    """저장소가 빈 기기(=새 브라우저 컨텍스트)에서 보관함으로 되살린다."""
    archive = shared_dir / "보관함.zip"
    if not archive.exists():
        pytest.skip("앞 테스트에서 보관함을 만들지 못했다")

    context = browser.new_context()   # 저장소가 비어 있는 '다른 기기'
    fresh = context.new_page()
    fresh.goto(app_url)
    fresh.wait_for_selector("#run:not([disabled])", timeout=120_000)

    fresh.click("#tab-runs")
    assert "저장된 산출이 없습니다" in fresh.inner_text("#runs-list")

    fresh.on("dialog", lambda dialog: dialog.accept())
    fresh.set_input_files("#backup-file", str(archive))
    fresh.click("#backup-replace")
    fresh.wait_for_selector("#runs-list >> text=2312 1번단체", timeout=60_000)

    # 등록 자료(명부)도 함께 돌아와야 한다.
    fresh.click("#tab-calc")
    assert "1번단체 명부" in fresh.inner_text("#roster-saved")
    context.close()


def test_editor_survives_tab_switching(page) -> None:
    """가정을 입력하다 다른 탭에 다녀와도 값이 남아 있어야 한다."""
    page.click("#tab-edit")
    page.click("#ed-subtabs >> text=할인율")
    first = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input")
    first.nth(0).fill("3")
    first.nth(1).fill("4.44%")

    # 곧바로 다른 탭으로 — 디바운스가 끝나기 전에 옮긴다.
    page.click("#tab-lib")
    page.click("#tab-runs")
    page.click("#tab-edit")

    page.click("#ed-subtabs >> text=할인율")
    kept = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input")
    assert kept.nth(0).input_value() == "3"
    assert kept.nth(1).input_value() == "4.44%"

    # 새로고침해도 남아야 한다(브라우저에 임시 저장).
    page.reload()
    page.wait_for_selector("#run:not([disabled])", timeout=120_000)
    page.click("#tab-edit")
    page.click("#ed-subtabs >> text=할인율")
    after = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input")
    assert after.nth(1).input_value() == "4.44%"


def test_generator_tab_makes_and_runs_a_case(page) -> None:
    """시험명부 탭에서 만든 명부로 곧바로 산출까지."""
    page.click("#tab-gen")
    page.fill("#gen-seed", "777")
    page.click("#gen-run")
    page.wait_for_selector("#gen-cases fieldset", timeout=120_000)
    assert page.locator("#gen-cases fieldset").count() == 3

    # 특이사항 안내문이 실제 내용을 담고 있어야 한다.
    page.locator("#gen-cases button", has_text="특이사항 보기").first.click()
    page.wait_for_selector("#report-dialog[open]")
    assert "산출 특이사항" in page.inner_text("#report-body")
    page.click("#report-dialog >> text=닫기")

    with page.expect_download() as captured:
        page.click("#gen-download")
    assert Path(captured.value.path()).read_bytes()[:2] == b"PK"

    # '이 명부로 산출 준비' → 파일 선택 없이 그대로 산출된다.
    page.locator("#gen-cases button", has_text="이 명부로 산출 준비").first.click()
    page.wait_for_selector("#loaded-run-banner", state="visible")
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    assert "확정급여채무" in page.inner_text("#summary")


def test_preset_round_trip_in_the_browser(page) -> None:
    """가정세트를 저장하고 다시 불러온다."""
    page.click("#tab-edit")
    page.click("#ed-subtabs >> text=지급률")
    cells = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input")
    cells.nth(0).fill("10")
    cells.nth(1).fill("13")

    page.once("dialog", lambda dialog: dialog.accept("E2E 규정"))
    page.click("#ed-preset-save")
    page.wait_for_selector("#ed-status:has-text('저장했습니다')", timeout=60_000)

    # 값을 지운 뒤 되불러오면 돌아와야 한다.
    page.click("#ed-example")
    page.select_option("#ed-preset", "E2E 규정")
    page.click("#ed-preset-load")
    page.wait_for_selector("#ed-status:has-text('불러왔습니다')", timeout=60_000)
    page.click("#ed-subtabs >> text=지급률")
    back = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input")
    assert back.nth(0).input_value() == "10"
    assert back.nth(1).input_value() == "13"


def test_standard_rates_fill_the_grids(page) -> None:
    """내장 표준률 불러오기 — 15~70세 표가 채워져야 한다."""
    page.click("#tab-edit")
    page.select_option("#ed-rates", "__builtin__")
    page.click("#ed-rates-load")
    assert "불러왔습니다" in page.inner_text("#ed-status")

    page.click("#ed-subtabs >> text=사망률")
    first_age = page.locator("#ed-subpages .subpage.on tbody tr:nth-child(2) input").first
    assert first_age.input_value() == "15"
