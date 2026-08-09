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


#: 산출가정 화면의 구획이 어느 묶음 탭 아래에 있는지. 파이썬 쪽
#: ``EDITOR_GROUPS`` 와 짝이 맞아야 한다(test_webui 가 배치를 따로 검사한다).
_SECTION_GROUP = {
    "할인율": "기초율", "승급률": "기초율", "퇴직률": "기초율", "사망률": "기초율",
    "임금상승률": "직군", "지급률": "퇴직급여", "장기급여지급률": "장기급여",
}


def section(page, sheet: str):
    """묶음 탭을 열고 그 안의 구획을 펼친다.

    탭이 열세 개일 때는 시트 이름이 곧 탭 이름이었다. 네 묶음으로 접은 뒤로는
    묶음을 먼저 열고 구획을 골라야 한다 — 사람이 하는 것과 같은 순서다.
    """
    page.click(f"#ed-subtabs >> text={_SECTION_GROUP[sheet]}")
    box = page.locator(f'#ed-subpages .subpage.on details[data-section="{sheet}"]')
    if not box.evaluate("node => node.open"):
        box.locator("summary").click()
    return box


def grid(page, sheet: str):
    """그 구획 안의 표."""
    return section(page, sheet).locator("tbody")


def grid_row(page, sheet: str, row: int = 2):
    """그 표의 ``row`` 번째 줄 입력칸들(1행은 머리글)."""
    return grid(page, sheet).locator(f"tr:nth-child({row}) input")


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


def test_roster_fills_the_asset_boxes(page, tmp_path) -> None:
    """명부를 고르면 '1)일반사항' 의 사외적립자산이 입력칸에 들어가야 한다.

    엔진이 알아서 읽는 것만으로는 부족하다. 칸이 비어 있으면 담당자는
    아무것도 읽히지 않은 줄 알고 신탁 명세서를 보고 손으로 다시 적는다.
    """
    from pension.rostergen import CASES, write_case_roster

    roster = write_case_roster(CASES[0], tmp_path / "일반사항명부.xlsx")

    page.click("#tab-calc")
    page.set_input_files("#roster", str(roster))
    page.wait_for_selector("#general-filled", state="visible", timeout=60_000)

    assert page.input_value("#base_date")
    assert page.input_value("#period_start") < page.input_value("#base_date")
    assert float(page.input_value("#asset_opening")) > 0
    assert float(page.input_value("#asset_closing")) > 0
    assert "사외적립자산" in page.inner_text("#general-filled")

    # 사람이 손댄 칸은 덮어쓰지 않는다 — 명부보다 나중 자료일 수 있다.
    page.fill("#asset_opening", "1")
    page.set_input_files("#roster", str(roster))
    page.wait_for_selector("#general-filled", state="visible", timeout=60_000)
    assert page.input_value("#asset_opening") == "1"

    # 다른 단체로 바꾸면 **우리가 채운 칸은** 그 명부 값으로 다시 채운다.
    # 앞 단체 숫자를 남겨 두면 남의 회사 자산으로 산출된다.
    other = write_case_roster(CASES[1], tmp_path / "다른단체.xlsx")
    before = page.input_value("#asset_closing")
    page.set_input_files("#roster", str(other))
    page.wait_for_selector("#general-filled", state="visible", timeout=60_000)
    assert page.input_value("#asset_closing") != before
    assert page.input_value("#asset_opening") == "1"   # 손댄 칸은 그대로

    for box in ("base_date", "period_start", "asset_opening", "asset_contributions",
                "asset_paid", "asset_closing"):
        page.fill(f"#{box}", "")
    page.set_input_files("#roster", [])


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
    assert grid_row(page, "할인율").first.input_value() == "0.25"


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
    first = grid_row(page, "할인율")
    first.nth(0).fill("3")
    first.nth(1).fill("4.44%")

    # 곧바로 다른 탭으로 — 디바운스가 끝나기 전에 옮긴다.
    page.click("#tab-lib")
    page.click("#tab-runs")
    page.click("#tab-edit")

    kept = grid_row(page, "할인율")
    assert kept.nth(0).input_value() == "3"
    assert kept.nth(1).input_value() == "4.44%"

    # 새로고침해도 남아야 한다(브라우저에 임시 저장).
    page.reload()
    page.wait_for_selector("#run:not([disabled])", timeout=120_000)
    page.click("#tab-edit")
    assert grid_row(page, "할인율").nth(1).input_value() == "4.44%"

    # 접어 둔 구획도 채워졌는지는 제목 옆 숫자로 보여야 한다.
    page.click("#ed-subtabs >> text=기초율")
    summary = page.locator(
        '#ed-subpages .subpage.on details[data-section="사망률"] summary .count'
    )
    assert summary.inner_text().strip() in {"비어 있음", "3줄"}


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
    cells = grid_row(page, "지급률")
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
    back = grid_row(page, "지급률")
    assert back.nth(0).input_value() == "10"
    assert back.nth(1).input_value() == "13"


def test_standard_rates_fill_the_grids(page) -> None:
    """내장 표준률 불러오기 — 15~70세 표가 채워져야 한다."""
    page.click("#tab-edit")
    page.select_option("#ed-rates", "__builtin__")
    page.click("#ed-rates-load")
    assert "불러왔습니다" in page.inner_text("#ed-status")

    assert grid_row(page, "사망률").first.input_value() == "15"
