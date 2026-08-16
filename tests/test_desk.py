"""본 화면(PC) — 실제 창을 띄워 끝까지 돌린다.

이 창이 곧 프로그램이다. 산출을 돌리고, 그 결과로 분석·보고서·사번 조회가
채워지고, 이름을 붙여 남긴 뒤 다음 회차에 전기로 끌어오는 것까지 **한 창
안에서** 돼야 한다. 어느 한 탭이라도 다른 화면으로 넘기면 이 시험이 깨진다.

tkinter 와 화면(DISPLAY)이 있어야 돌아간다 — 없으면 건너뛴다.
"""

from __future__ import annotations

import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    """이 시험만 쓰는 보관 폴더와 표본 자료."""
    from pension.samples import write_sample_pack

    home = tmp_path_factory.mktemp("보관")
    work = tmp_path_factory.mktemp("작업")
    files = write_sample_pack(work)
    return {
        "home": home,
        "work": work,
        "roster": next(p for p in files if p.name == "명부_양식.xlsx"),
        "assumptions": next(p for p in files if p.name == "기초율_기본값.xlsx"),
    }


@pytest.fixture
def app(workspace, monkeypatch):
    """창 하나. 보관 폴더를 시험용으로 돌려 놓아 진짜 자료를 건드리지 않는다."""
    monkeypatch.setenv("PENSION_HOME", str(workspace["home"]))
    from pension import clients
    from pension.desk.app import DeskApp

    try:
        window = DeskApp()
    except tk.TclError as exc:                        # pragma: no cover
        pytest.skip(f"화면 없음: {exc}")
    window.withdraw()
    window.update()
    assert clients.current()          # 단체가 하나는 있어야 한다
    yield window
    window.destroy()


def run_once(app, workspace, name: str = "결과.xlsx"):
    """산출 탭에서 한 회차를 끝까지 돌린다."""
    calc = app.calc
    calc.roster_path.set(str(workspace["roster"]))
    calc.assumptions_path.set(str(workspace["assumptions"]))
    calc.output_path.set(str(workspace["work"] / name))
    calc.start()
    deadline = time.monotonic() + 180
    while calc._worker is not None and time.monotonic() < deadline:
        app.update()
        time.sleep(0.02)
    app.update()
    assert calc._worker is None, "산출이 끝나지 않았습니다"
    return app.hub.run


class TestTheWindowItself:

    def test_every_screen_is_a_tab_in_this_one_window(self, app) -> None:
        """'전체 기능 화면 열기' 같은 것이 없어야 한다 — 여기가 전체다."""
        titles = [app.book.tab(index, "text").strip()
                  for index in range(len(app.book.tabs()))]
        assert titles == ["산출", "산출가정 입력", "분석", "계리평가 보고서",
                          "사번 조회", "산출 내역", "자료실"]

    def test_switching_tabs_does_not_break_anything(self, app) -> None:
        for index in range(len(app.book.tabs())):
            app.book.select(index)
            app.update()

    def test_the_assumptions_editor_lives_inside_the_notebook(self, app) -> None:
        """가정 입력이 별도 창으로 뜨면 안 된다."""
        assert app.editor.window is None
        assert str(app.editor).startswith(str(app.book))

    def test_no_screen_is_missing_before_a_run(self, app) -> None:
        """산출 전에도 탭은 다 있고, 안내만 다르다."""
        assert app.hub.run is None
        assert "산출" in app.analysis._empty.cget("text")
        assert "산출" in app.report.status.cget("text")


class TestARunFillsEveryScreen:

    def test_a_run_reaches_analysis_report_and_lookup(self, app, workspace) -> None:
        run = run_once(app, workspace)
        assert run is not None

        # 분석 — 지표·직군·만기·민감도가 실제로 채워진다.
        assert app.analysis.metrics.winfo_children()
        assert app.analysis.group_table.tree.get_children()
        assert len(app.analysis.maturity_chart._rows) == 17
        assert app.analysis.sens_table.tree.get_children()

        # 보고서 — 같은 회차로 본문이 만들어진다.
        app.report.refresh()
        assert "확정급여채무" in app.report._html
        assert len(app.report.reader.text.get("1.0", "end")) > 2000

        # 사번 조회 — 그 명부의 사람을 다시 산출한다.
        first = run.valuation.members[0]
        app.member.employee.set(str(first.employee_id))
        app.member.look_up()
        assert "찾지 못했" not in app.member.status.cget("text")
        assert app.member.total_table.tree.get_children()

    def test_the_summary_matches_the_engine(self, app, workspace) -> None:
        """로그에 적은 숫자가 산출 결과 그대로여야 한다."""
        run = run_once(app, workspace)
        written = app.calc.log.get("1.0", "end")
        assert f"{run.valuation.dbo:,.0f}" in written
        assert f"{run.valuation.headcount:,}명" in written

    def test_the_report_refuses_longterm_without_it(self, app, workspace) -> None:
        app.calc.include_longterm.set(False)
        run_once(app, workspace, name="장기없음.xlsx")
        app.report.kind.set("longterm")
        app.report.refresh()
        assert app.report._html == ""
        assert "장기급여" in app.report.status.cget("text")


class TestKeepingAndBringingBack:

    def test_a_run_can_be_kept_and_loaded_again(self, app, workspace) -> None:
        run = run_once(app, workspace)
        app.runs.run_name.set("2512 시험")
        app.runs.save()
        assert "저장했습니다" in app.runs.save_status.cget("text")
        assert "2512 시험" in app.runs.table.tree.get_children()

        # 다른 회차를 돌려 화면을 갈아 끼운 뒤,
        app.hub.publish(None)
        assert app.hub.run is None

        # 저장본을 고르면 그때 그 숫자로 되살아난다.
        app.runs.table.tree.selection_set("2512 시험")
        app.runs.load()
        assert app.hub.run is not None
        assert app.hub.loaded.name == "2512 시험"
        assert app.hub.run.valuation.dbo == pytest.approx(run.valuation.dbo)

    def test_a_kept_run_becomes_the_prior_period(self, app, workspace) -> None:
        run = run_once(app, workspace)
        app.runs.run_name.set("2412 전기")
        app.runs.save()

        app.calc._refresh_prior_runs()
        app.calc.prior_run.set("2412 전기")
        app.calc._use_prior_run()

        assert app.calc.prior_dbo.get().replace(",", "") == f"{run.valuation.dbo:.0f}"
        assert app.calc.prior_rate.get().endswith("%")
        assert str(app.calc._check_button["state"]) == "normal"

        # 전기 기초율까지 딸려 와야 손익이 경험조정과 가정변경으로 나뉜다.
        assert app.calc.prior_assumptions.get().endswith("기초율.xlsx")

    def test_the_prior_roster_comparison_runs_in_this_window(
        self, app, workspace
    ) -> None:
        run_once(app, workspace)
        app.runs.run_name.set("2412 맞대기")
        app.runs.save()
        app.calc._refresh_prior_runs()
        app.calc.prior_run.set("2412 맞대기")
        app.calc._use_prior_run()
        app.calc._compare_rosters()

        # 같은 명부끼리 맞댔으므로 걸릴 것이 없다.
        assert "이상 없" in app.calc._check_status.cget("text")

    def test_deleting_a_kept_run_takes_it_off_the_prior_list(
        self, app, workspace, monkeypatch
    ) -> None:
        from tkinter import messagebox

        run_once(app, workspace)
        app.runs.run_name.set("지울것")
        app.runs.save()
        monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
        app.runs.table.tree.selection_set("지울것")
        app.runs.delete()
        assert "지울것" not in app.runs.table.tree.get_children()
        app.calc._refresh_prior_runs()
        assert "지울것" not in app.calc._prior_box.cget("values")


class TestTheLibraryTab:

    def test_the_random_roster_generator_is_in_this_window(self, app) -> None:
        """난수 명부를 이 창 안에서 만들고 그대로 목록에 넣는다.

        잠긴 사례(교육용 문제지가 딸린 명부)는 만들지도 등록하지도 않는다 —
        비밀번호를 풀기 전에는 없는 것과 같아야 한다.
        """
        from pension.rostergen import case_specs

        open_cases = {spec.title for spec in case_specs(specials=False)}
        locked = {spec.title for spec in case_specs()} - open_cases
        assert locked, "잠긴 사례가 하나는 있어야 이 시험이 뜻을 가진다"

        app.library.seed.set("4242")
        app.library.generate()
        assert "만들었습니다" in app.library.gen_status.cget("text")

        app.library.register_cases()
        app.library.kind.set("명부")
        app.library.refresh()
        registered = set(app.library.asset_table.tree.get_children())
        assert open_cases <= registered
        assert not (locked & registered), "잠긴 사례가 목록에 나오면 안 된다"

        # 산출 탭의 [저장된 명부] 목록에도 바로 떠야 한다.
        app.calc._refresh_library()
        assert set(app.calc._roster_box.cget("values")) >= open_cases

    def test_a_generated_roster_actually_runs(self, app, workspace) -> None:
        """만든 명부로 산출까지 돌아야 '만들었다' 고 할 수 있다."""
        from pension.library import ROSTER_KIND, find_entry

        app.library.seed.set("4242")
        app.library.generate()
        app.library.register_cases()

        entry = find_entry(ROSTER_KIND, "시험명부1_표준")
        assert entry is not None
        app.calc.roster_path.set(str(entry.path))
        app.calc.assumptions_path.set(
            str(app.library._gen_folder() / "시험명부1_표준_기초율.xlsx"))
        app.calc.output_path.set(str(workspace["work"] / "난수산출.xlsx"))
        app.calc.start()
        deadline = time.monotonic() + 300
        while app.calc._worker is not None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.02)
        app.update()
        assert app.hub.run is not None
        assert app.hub.run.valuation.headcount > 100

    def test_clients_can_be_made_and_chosen_here(self, app) -> None:
        app.library.client_table.tree.selection_remove(
            *app.library.client_table.tree.get_children())
        app.hub.select_client("2번단체")
        app.update()
        assert app.hub.client() == "2번단체"
        assert app.client.get() == "2번단체"
        # 단체를 옮기면 산출 내역 목록도 그 단체 것으로 바뀐다.
        assert "2번단체" in app.runs.status.cget("text")

    def test_the_backup_carries_the_library_out(self, app, workspace) -> None:
        import zipfile
        from tkinter import filedialog

        run_once(app, workspace)
        app.runs.run_name.set("보관시험")
        app.runs.save()

        target = workspace["work"] / "보관함.zip"
        app_dialog = filedialog.asksaveasfilename
        try:
            filedialog.asksaveasfilename = lambda **_kw: str(target)
            app.library.export()
        finally:
            filedialog.asksaveasfilename = app_dialog

        assert target.exists()
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        assert "연금계리보관함.json" in names
        assert any("보관시험" in name for name in names)


class TestNumbersOnScreen:
    """화면에 찍히는 숫자 서식. 조서로 그대로 옮겨 적는 자리다."""

    def test_money_is_in_won_with_brackets_for_minus(self) -> None:
        from pension.desk import theme

        assert theme.money(1234000) == "1,234,000"
        assert theme.money(-1234000) == "(1,234,000)"
        assert theme.money(0) == "0"
        assert theme.money(None) == "-"

    def test_changes_keep_their_sign(self) -> None:
        from pension.desk import theme

        assert theme.signed(1200) == "+1,200"
        assert theme.signed(-1200) == "-1,200"

    def test_axis_tops_are_round_numbers(self) -> None:
        from pension.desk.charts import nice_ceiling

        assert nice_ceiling(0) == 1.0
        assert nice_ceiling(93) == 100.0
        assert nice_ceiling(1_234_567) == 2_000_000.0
        assert nice_ceiling(0.0043) == 0.005


class TestTheReportReader:
    """보고서 HTML 을 글자로 옮겨 그리는 부분."""

    def test_tables_keep_their_columns(self) -> None:
        from pension.desk.htmlview import to_blocks

        blocks = to_blocks(
            "<h2>1. 요약</h2><p>본문</p>"
            '<table class="t"><thead><tr><th>구 분</th><th>금액</th></tr></thead>'
            "<tbody><tr><td>확정급여채무</td><td>1,000</td></tr></tbody></table>")
        kinds = [kind for kind, _ in blocks]
        assert "h2" in kinds and "table" in kinds
        table = next(payload for kind, payload in blocks if kind == "table")
        assert table[0] == ["구 분", "금액"]
        assert table[1] == ["확정급여채무", "1,000"]

    def test_styles_and_scripts_never_reach_the_screen(self) -> None:
        from pension.desk.htmlview import to_blocks

        blocks = to_blocks("<style>body { color: red }</style><p>본문</p>")
        assert [payload for _kind, payload in blocks] == ["본문"]

    def test_korean_columns_line_up(self) -> None:
        """한글은 두 칸을 먹는다. 그걸 세지 않으면 표가 어긋난다."""
        from pension.desk.htmlview import _width

        assert _width("가나") == 4
        assert _width("ab") == 2
