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


def grid_row(page, sheet: str, row: int = 1):
    """그 표의 ``row`` 번째 **값** 줄. 머리글과 열 머리 패널은 건너뛴다."""
    return grid(page, sheet).locator("tr[data-row]").nth(row - 1).locator("input")


def open_section(page, selector: str):
    """접어 둔 구획을 펼친다. 이미 펼쳐져 있으면 그대로 둔다."""
    box = page.locator(selector)
    if not box.evaluate("node => node.open"):
        box.locator("summary").click()


def open_calc(page, section_id: str):
    """산출 탭에서 접어 둔 구획을 펼친다. 사람이 하는 것과 같은 순서다."""
    page.click("#tab-calc")
    open_section(page, f"#{section_id}")


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

    # 채워 넣은 구획은 저절로 펼쳐져야 한다 — 접힌 채로 값만 들어가면
    # 담당자가 확인할 기회 없이 그대로 산출된다.
    assert page.locator("#sec-assets").evaluate("node => node.open")
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
    open_section(page, "#sec-lib-curve")
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
    open_calc(page, "sec-prior")
    page.select_option("#prior-run", "2312 1번단체")
    assert page.input_value("#prior_dbo").replace(",", "").isdigit()
    assert page.input_value("#prior_rate").endswith("%")

    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    assert "보험수리적손익" in page.inner_text("#summary")

    # 보관함 내보내기 — 기기 밖에 둘 zip 이 실제로 떨어져야 한다.
    page.click("#tab-lib")
    open_section(page, "#lib-backup")
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
    fresh.click("#tab-lib")
    open_section(fresh, "#lib-backup")
    fresh.set_input_files("#backup-file", str(archive))
    fresh.click("#backup-replace")
    fresh.click("#tab-runs")
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
    """자료실의 시험 명부 구획에서 만든 명부로 곧바로 산출까지."""
    page.click("#tab-lib")
    open_section(page, "#lib-gen")
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


def test_workplace_size_switches_the_standard_table(page) -> None:
    """300인 미만/이상은 다른 표다 — 고른 쪽이 실제로 표에 들어와야 한다."""
    page.click("#tab-edit")
    page.select_option("#ed-rates", "__builtin__")

    def first_withdrawal(size: str) -> str:
        page.select_option("#ed-size", size)
        page.click("#ed-rates-load")
        assert size in page.inner_text("#ed-status")
        return grid_row(page, "퇴직률").nth(1).input_value()

    small = first_withdrawal("300인 미만")
    large = first_withdrawal("300인 이상")
    assert small != large

    # 사망률은 규모로 갈리지 않으므로 그대로여야 한다.
    assert grid_row(page, "사망률").first.input_value() == "15"


def test_benefit_column_splits_into_three_causes(page) -> None:
    """[사유별 차등] 한 번으로 정년·중도·사망 열이 생기고 연결까지 끝나야 한다."""
    def split_box():
        return section(page, "지급률").locator(
            'tr.panel:has(td.name:text-is("사유별 차등")) input[type=checkbox]'
        ).first

    def headers():
        return section(page, "지급률").locator("tbody tr").first.inner_text()

    split_box().check()
    assert "정규직·정년" in headers()

    # 퇴직사유 표까지 자동으로 채워져야 손댈 곳이 없다.
    causes = page.locator('#ed-subpages .subpage.on details[data-section="cause"]')
    if not causes.evaluate("node => node.open"):
        causes.locator("summary").click()
    assert "정규직·정년" in causes.inner_text()

    # 도로 접으면 열도 연결도 사라진다.
    page.once("dialog", lambda dialog: dialog.accept())
    split_box().uncheck()
    assert "정규직·정년" not in headers()
    assert "정규직·정년" not in causes.inner_text()


def test_client_bar_keeps_run_history_apart(page, tmp_path) -> None:
    """단체를 갈아 끼우면 산출 내역과 전기 산출 목록이 그 단체 것만 남는다."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    # 앞 테스트가 기본 단체에 "2412 1번단체" 를 저장해 두었다.
    page.click("#tab-runs")
    assert "2412 1번단체" in page.inner_text("#runs-list")

    page.once("dialog", lambda dialog: dialog.accept("나단체"))
    page.click("#client-add")
    assert page.input_value("#client-pick") == "나단체"
    assert "아직 저장된 산출이 없습니다" in page.inner_text("#runs-list")

    # 전기 산출 목록에도 앞 단체 것이 남으면 안 된다.
    open_calc(page, "sec-prior")
    assert "2412 1번단체" not in page.inner_text("#prior-run")

    # 이 단체에 한 건 저장하면 여기에만 쌓인다.
    page.set_input_files("#roster", str(roster))
    page.check("#asrc-file")
    page.set_input_files("#assumptions", str(assumptions))
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    page.fill("#run-name", "2412")
    page.click("#run-save")
    page.wait_for_selector("text=저장했습니다", timeout=30_000)

    page.click("#tab-runs")
    listing = page.inner_text("#runs-list")
    assert "2412" in listing and "1번단체" not in listing
    assert page.inner_text("#client-runs") == "1건"

    # 이름을 바꿔도 산출은 따라간다.
    page.once("dialog", lambda dialog: dialog.accept("나단체㈜"))
    page.click("#client-rename")
    assert page.input_value("#client-pick") == "나단체㈜"
    assert "2412" in page.inner_text("#runs-list")

    # 되돌아가면 앞 단체 것이 그대로 있다.
    page.select_option("#client-pick", "기본 단체")
    assert "2412 1번단체" in page.inner_text("#runs-list")

    # 산출이 남은 단체는 한 번 더 물어본 뒤에야 지워진다.
    page.select_option("#client-pick", "나단체㈜")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.click("#client-remove")
    assert page.input_value("#client-pick") == "나단체㈜"

    page.once("dialog", lambda dialog: dialog.accept())
    page.click("#client-remove")
    assert page.input_value("#client-pick") == "기본 단체"
    assert "2412 1번단체" in page.inner_text("#runs-list")


def test_member_lookup_and_reports(page, tmp_path) -> None:
    """산출 → 사번 조회(연차별 근거) → 계리평가 보고서 미리보기까지."""
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

    # 사번 조회 — 그 사람만 재산출한 연차별 근거가 떠야 한다.
    page.fill("#lookup-id", "A0001")
    page.click("#lookup-run")
    page.wait_for_selector("#member-dialog[open]", timeout=120_000)
    body = page.inner_text("#member-body")
    assert "연차별 계산 근거" in body
    assert "확정급여채무 (DBO)" in body
    page.click("#member-dialog >> text=닫기")

    # 퇴직급여 보고서 — 미리보기 iframe 안에 표지 제목이 있어야 한다.
    page.click("#report-sev")
    page.wait_for_selector("#print-dialog[open]", timeout=120_000)
    frame = page.frame_locator("#print-frame")
    assert "확정급여부채 평가보고서" in frame.locator("body").inner_text()
    assert "민감도 분석" in frame.locator("body").inner_text()
    page.click("#print-dialog >> text=닫기")


def test_build_stamp_is_visible_and_matches_the_cache(page) -> None:
    """화면의 빌드 값 = 서비스워커 캐시 이름.

    새 빌드를 올렸는데 옛 캐시가 도는 것을 눈으로 가려낼 수단이다. 치환이
    빠지면 화면에 ``__BUILD__`` 가 그대로 뜨므로 그것도 함께 막는다.
    """
    import re

    stamp = page.inner_text("#build-stamp").strip()
    assert re.fullmatch(r"[0-9a-f]{12}", stamp), f"빌드 값이 이상하다: {stamp}"
    assert f'"pension-{stamp}"' in (DIST / "sw.js").read_text(encoding="utf-8")


def test_dashboard_tab_follows_each_run(page, tmp_path) -> None:
    """분석 탭 — 산출할 때마다 그 회차로 다시 그려져야 한다."""
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

    page.click("#tab-dash")
    page.wait_for_selector("#dash-body", state="visible", timeout=60_000)
    assert page.locator("#dash-strip .stat").count() >= 6
    assert page.locator("#dash-chart rect").count() > 0
    assert page.locator("#dash-trace tr").count() > 2
    first = page.inner_text("#dash-strip")

    # 막대를 누르면 그 해의 계산식이 펼쳐진다.
    page.locator("#dash-chart rect.hit").nth(1).click()
    assert "귀속액" in page.inner_text("#dash-readout")

    # 가정을 흔들면 채무가 움직인다.
    base = page.inner_text("#dash-readout .big")
    page.locator("#dash-chips .chip").nth(1).click()
    assert page.inner_text("#dash-readout .big") != base

    # 사번을 지정해 다른 사람을 해부한다.
    page.fill("#dash-emp", "A0002")
    page.click("#dash-emp-go")
    assert "A0002" in page.inner_text("#dash-who")

    # 기준일을 옮겨 다시 산출하면 분석 값도 그 회차로 갈린다.
    # (앞으로 당기면 퇴직자 퇴사일이 기준일보다 늦어 검증에 걸린다 — 뒤로 민다.)
    open_calc(page, "sec-dates")
    page.fill("#base_date", "2026-06-30")
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    page.click("#tab-dash")
    assert page.inner_text("#dash-when").strip() == "기준일 2026-06-30"
    assert page.inner_text("#dash-strip") != first


def test_new_disclosures_flow_into_the_dashboard(page, tmp_path) -> None:
    """재측정 분해 · 자산인식상한 · 차년도 예측 · 장기급여 증감표까지 한 바퀴."""
    from pension.samples import write_sample_pack

    files = write_sample_pack(tmp_path)
    roster = next(p for p in files if p.name == "명부_양식.xlsx")
    assumptions = next(p for p in files if p.name == "기초율_기본값.xlsx")

    page.click("#tab-calc")
    page.set_input_files("#roster", str(roster))
    page.check("#asrc-file")
    page.set_input_files("#assumptions", str(assumptions))

    # 가정별 분해는 전기 **기초율** 이 있어야 돈다. 한 번 산출해 저장한 뒤
    # 그것을 전기로 연결한다 — 실제 사용 순서와 같다.
    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    page.fill("#run-name", "전기연결시험")
    page.click("#run-save")
    page.wait_for_selector("text=저장했습니다", timeout=30_000)

    open_calc(page, "sec-options")
    page.check("#split_remeasurement")
    open_calc(page, "sec-prior")
    page.select_option("#prior-run", "전기연결시험")
    page.fill("#prior_longterm_dbo", "40000000")
    open_calc(page, "sec-assets")
    page.fill("#asset_opening", "200000000")
    page.fill("#asset_contributions", "50000000")
    page.fill("#asset_closing", "900000000")     # 초과적립으로 상한을 물린다
    page.fill("#asset_ceiling", "50000000")
    page.fill("#expected_contributions", "60000000")

    page.click("#run")
    page.wait_for_selector("#result", state="visible", timeout=180_000)
    summary = page.inner_text("#summary")
    assert "자산인식상한 차감액" in summary
    assert "장기급여 재측정(당기손익)" in summary

    page.click("#tab-dash")
    page.wait_for_selector("#dash-body", state="visible", timeout=60_000)

    open_section(page, "#sec-dash-roll")
    roll = page.inner_text("#dash-roll")
    assert "가정변경효과 분해" in roll
    for name in ("사망률", "퇴직률", "임금상승률", "할인율"):
        assert name in roll
    assert "자산인식상한" in roll

    open_section(page, "#sec-dash-next")
    nxt = page.inner_text("#dash-next")
    assert "차년도 예상 퇴직급여 비용" in nxt
    assert "차년도 확정급여채무 예측" in nxt
    assert "예상 부담금 납입액" in nxt

    open_section(page, "#sec-dash-lt")
    assert "재측정요소 (당기손익)" in page.inner_text("#dash-lt")

    # 인쇄를 누르면 접힌 구획이 모두 펼쳐진다(인쇄 대화상자는 막아 둔다).
    page.evaluate("window.print = () => { window.__printed = true; }")
    page.click("#dash-print")
    page.wait_for_timeout(400)
    assert page.evaluate("window.__printed") is True
    assert page.evaluate(
        "[...document.querySelectorAll('#page-dash details.section')]"
        ".every((d) => d.open)")
    assert "기준일" in page.inner_text("#dash-print-title")


def test_the_pc_local_server_boots_the_engine(browser) -> None:
    """PC 의 [전체 기능 화면] — 로컬 서버로 낸 웹앱이 실제로 뜨는지.

    ``.wasm`` 의 MIME 이나 폴더 배치가 틀리면 화면만 뜨고 엔진이 죽는다.
    파일을 그대로 복사한 것이라 조용히 어긋나기 쉬운 대목이다.
    """
    import threading

    from pension import localapp

    server = localapp.serve(DIST)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/index.html")
        # 엔진이 다 뜨면 [산출 실행] 의 잠금이 풀린다.
        page.wait_for_selector("#run:not([disabled])", timeout=120_000)
        assert page.locator("#tab-dash").count() == 1
    finally:
        page.close()
        server.shutdown()
        server.server_close()


def test_storage_persistence_is_requested_and_reported(page) -> None:
    """자료가 지워질 수 있는지를 화면이 말해 줘야 한다.

    이 앱의 산출 내역은 브라우저 저장소에 있고, 브라우저는 공간이 모자라면
    **말없이 지운다.** 영구 저장을 요청해 두면 그 대상에서 빠지는데, 받아들여
    졌는지는 기기가 정한다. 보장되지 않는데 보장된 줄 알고 내보내기를 건너
    뛰는 것이 제일 나쁘므로, 결과를 그대로 적어 둔다.
    """
    page.click("#tab-lib")
    open_section(page, "#lib-backup")
    note = page.locator("#storage-note")
    page.wait_for_function(
        "() => document.querySelector('#storage-note').textContent.trim().length > 0",
        timeout=30_000)

    # 허용됐든 아니든 **내보내기를 권해야** 한다. 허용은 이 기기에서만이고,
    # 기기를 바꾸면 어차피 파일이 있어야 되살릴 수 있다.
    text = note.inner_text()
    assert "내보내" in text, text
    assert note.get_attribute("class") in ("hint ok-text", "hint bad-text", "hint")

    # 허용되지 않았으면 눈에 띄어야 한다. 회색 안내문에 섞이면 아무도 안 읽는다.
    if "허용되지 않" in text:
        assert note.get_attribute("class") == "hint bad-text"
