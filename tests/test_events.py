"""기중 제도 변동 — 축소·정산·사업결합·분할.

결산일 명부에는 없는 사람들이다. 어디에도 안 잡히면 기초에서 기말까지의
증감표가 그 금액만큼 통째로 어긋나고, 그 차이는 설명 없는 '경험조정' 으로
나타난다. 담당자는 무엇이 틀렸는지 알 수 없다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from pension.config import CalculationConfig, JobGroupRule
from pension.errors import IssueLog
from pension.events import (
    CURTAILMENT,
    DISPOSAL,
    EVENT_KINDS,
    MERGER,
    SETTLEMENT,
    measure_events,
)
from pension.models import ActiveMember, RateRules
from pension.normalize import BenefitPlan, Gender
from pension.rostertemplate import FIRST_DATA_ROW, HEADER_ROW

BASE_DATE = dt.date(2025, 12, 31)


def config_of() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE_DATE,
        job_group_rules=[JobGroupRule("정규직", "정규직", severance_nra=60,
                                      longterm_nra=60, over_nra_add_age=2)],
    )


def assumptions_of():
    from tests.test_valuation import make_assumptions

    return make_assumptions(discount=0.05, salary=0.0)


@pytest.fixture
def config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE_DATE,
        job_group_rules=[JobGroupRule("정규직", "정규직", severance_nra=60,
                                      longterm_nra=60, over_nra_add_age=2)],
    )


@pytest.fixture
def assumptions():
    from tests.test_valuation import make_assumptions

    return make_assumptions(discount=0.05, salary=0.0)


def _member(kind: str, when: dt.date, *, paid: float = 0.0,
            wage: float = 5_000_000, seq: int = 1) -> ActiveMember:
    member = ActiveMember(seq=seq, row=FIRST_DATA_ROW + seq - 1)
    member.employee_id = f"E{seq:03d}"
    member.gender = Gender.MALE
    member.birth_date = dt.date(1975, 4, 1)
    member.hire_date = dt.date(2005, 4, 1)
    member.settlement_date = member.hire_date
    member.monthly_wage = wage
    member.plan = BenefitPlan.DB
    member.job_group = "정규직"
    member.job_group_raw = "정규직"
    member.job_group_index = 0
    member.rules = RateRules(severance_withdrawal="기본",
                             severance_salary_increase="기본")
    member.event_kind = kind
    member.event_date = when
    member.event_payment = paid
    return member


class TestMeasuringAtTheEventDate:
    def test_the_obligation_is_measured_on_the_event_date(
        self, config, assumptions
    ) -> None:
        """7월에 정산한 사람을 12월 가정으로 재면 반년치 이자가 섞여 든다."""
        july = measure_events([_member(SETTLEMENT, dt.date(2025, 7, 1))],
                              config, assumptions)
        december = measure_events([_member(SETTLEMENT, dt.date(2025, 12, 31))],
                                  config, assumptions)
        assert july.of(SETTLEMENT).obligation != pytest.approx(
            december.of(SETTLEMENT).obligation)

    def test_each_event_date_is_measured_on_its_own(self, config, assumptions) -> None:
        """7월 매각과 11월 정산을 한 시점으로 뭉뚱그리면 둘 다 틀린다."""
        both = measure_events(
            [_member(SETTLEMENT, dt.date(2025, 7, 1), seq=1),
             _member(SETTLEMENT, dt.date(2025, 11, 30), seq=2)],
            config, assumptions)
        alone = [
            measure_events([_member(SETTLEMENT, dt.date(2025, 7, 1))],
                           config, assumptions).of(SETTLEMENT).obligation,
            measure_events([_member(SETTLEMENT, dt.date(2025, 11, 30))],
                           config, assumptions).of(SETTLEMENT).obligation,
        ]
        assert both.of(SETTLEMENT).obligation == pytest.approx(sum(alone))
        assert both.of(SETTLEMENT).headcount == 2

    def test_the_gain_is_payment_less_the_obligation(self, config, assumptions) -> None:
        """문단 109 — 없앤 채무보다 적게 주고 끝냈으면 그만큼 이익이다."""
        outcome = measure_events(
            [_member(SETTLEMENT, dt.date(2025, 7, 1), paid=100_000_000)],
            config, assumptions)
        effect = outcome.of(SETTLEMENT)
        assert effect.gain == pytest.approx(100_000_000 - effect.obligation)

    def test_an_incoming_group_books_no_gain(self, config, assumptions) -> None:
        """사업결합으로 넘겨받은 채무는 대가가 따로 오간다 — 여기서 손익이 아니다."""
        outcome = measure_events(
            [_member(MERGER, dt.date(2025, 9, 30), paid=0)], config, assumptions)
        assert outcome.of(MERGER).obligation > 0
        assert outcome.of(MERGER).gain == 0.0

    def test_incoming_and_outgoing_land_on_opposite_sides(
        self, config, assumptions
    ) -> None:
        outcome = measure_events(
            [_member(MERGER, dt.date(2025, 9, 30), seq=1),
             _member(DISPOSAL, dt.date(2025, 9, 30), seq=2)],
            config, assumptions)
        assert outcome.transfers_in > 0
        assert outcome.transfers_out > 0
        assert outcome.settled_obligation == 0.0

    def test_curtailment_counts_as_extinguished(self, config, assumptions) -> None:
        outcome = measure_events(
            [_member(CURTAILMENT, dt.date(2025, 3, 1))], config, assumptions)
        assert outcome.settled_obligation == pytest.approx(
            outcome.of(CURTAILMENT).obligation)


class TestReadingWhatCompaniesActuallyWrite:
    @pytest.mark.parametrize(
        ("written", "expect"),
        [("정산", SETTLEMENT), ("제도 축소", CURTAILMENT), ("사업 결합", MERGER),
         ("분할", DISPOSAL), ("사업부 매각", DISPOSAL), ("합병", MERGER),
         ("중간정산", SETTLEMENT)],
    )
    def test_common_wordings_are_understood(
        self, written, expect, config, assumptions
    ) -> None:
        outcome = measure_events([_member(written, dt.date(2025, 6, 1))],
                                 config, assumptions)
        assert outcome.of(expect).headcount == 1

    def test_a_row_with_no_date_is_skipped_and_said_out_loud(
        self, config, assumptions
    ) -> None:
        """조용히 빼면 인원이 왜 안 맞는지 알 방법이 없다."""
        member = _member(SETTLEMENT, dt.date(2025, 6, 1))
        member.event_date = None
        log = IssueLog()
        outcome = measure_events([member], config, assumptions, log)
        assert outcome.is_empty and outcome.skipped == 1
        assert any(i.code == "JAE_EVENT_INCOMPLETE" for i in log.warnings)

    def test_an_unreadable_kind_is_skipped(self, config, assumptions) -> None:
        member = _member("???", dt.date(2025, 6, 1))
        outcome = measure_events([member], config, assumptions)
        assert outcome.is_empty and outcome.skipped == 1


class TestTheSheetAndTheEngineAgree:
    def test_the_words_on_the_form_are_the_words_we_read(self) -> None:
        from pension.rostertemplate import EVENT_KINDS as ON_FORM

        assert ON_FORM == EVENT_KINDS

    def test_the_blank_form_shows_one_row_per_event_kind(self, tmp_path) -> None:
        """무엇을 적는 칸인지 예시 없이는 알 수 없다."""
        from pension.rostertemplate import write_roster_template

        path = write_roster_template(tmp_path / "양식.xlsx")
        ws = openpyxl.load_workbook(path)["추가명부"]
        head = {str(c.value).strip(): c.column for c in ws[HEADER_ROW] if c.value}
        kinds = [ws.cell(r, head["사건 구분"]).value
                 for r in range(FIRST_DATA_ROW, FIRST_DATA_ROW + 3)]
        assert kinds == [SETTLEMENT, MERGER, DISPOSAL]

    def test_an_example_row_left_in_place_is_not_counted(self, tmp_path) -> None:
        """이 시트는 비어 있는 것이 정상이라 예시가 남은 채 돌아오기 쉽다.

        그대로 세면 있지도 않은 정산이 잡혀 증감표가 그 금액만큼 틀린다 —
        오류 없이 그럴듯한 숫자가 나오는 쪽이다. 사번의 표시로 걸러낸다.
        """
        from pension.rostertemplate import EXAMPLE_MARK

        member = _member(SETTLEMENT, dt.date(2025, 7, 1), paid=90_000_000)
        member.employee_id = f"{EXAMPLE_MARK}A0007"
        log = IssueLog()
        outcome = measure_events([member], config_of(), assumptions_of(), log)
        assert outcome.is_empty
        assert outcome.skipped == 0, "예시 줄은 경고 없이 조용히 빠져야 한다"
        assert log.warnings == []

    def test_a_workbook_without_the_sheet_reads_as_no_events(self, tmp_path) -> None:
        """대부분의 회사·대부분의 해에는 이런 일이 없다. 없는 것이 정상이다."""
        from pension.readers import read_extra_roster

        book = openpyxl.Workbook()
        book.create_sheet("재직자명부")
        path = Path(tmp_path) / "추가명부없음.xlsx"
        book.save(path)

        reopened = openpyxl.load_workbook(path)
        assert read_extra_roster(reopened, CalculationConfig(base_date=BASE_DATE),
                                 IssueLog()) == []
        reopened.close()


class TestItReachesTheRollforward:
    """[추가명부] 를 채워 보내면 손으로 적어 넣던 칸을 대신해야 한다.

    지금까지는 담당자가 소멸 채무를 따로 계산해 한 칸에 넣었다. 그 숫자가
    어떻게 나왔는지는 어디에도 남지 않는다.
    """

    def _pack(self, tmp_path, rows):
        from pension.rostergen import CASES, write_case_assumptions, write_case_roster

        tmp_path = Path(tmp_path)
        tmp_path.mkdir(parents=True, exist_ok=True)
        spec = CASES[0]
        roster = write_case_roster(spec, tmp_path / "명부.xlsx")
        assumptions = write_case_assumptions(spec, tmp_path / "기초율.xlsx")

        book = openpyxl.load_workbook(roster)
        ws = book["추가명부"]
        head = {str(c.value).strip(): c.column for c in ws[HEADER_ROW] if c.value}
        for offset, record in enumerate(rows):
            for label, value in record.items():
                ws.cell(FIRST_DATA_ROW + offset, head[label], value)
        book.save(roster)
        return roster, assumptions

    def _row(self, kind, *, paid=0):
        return {
            "순번": 1, "사건 구분": kind, "사건일": "2025-07-01",
            "사번": "X001", "성명": "정산자", "생년월일": "1975-04-01",
            "성별": "남", "입사일자": "2005-04-01", "직군": "정규직",
            "30일 평균임금": 5_000_000, "퇴직급여 제도구분": "DB", "지급액": paid,
        }

    def _run(self, tmp_path, rows, **prior):
        from pension.pipeline import PriorPeriod, RunOptions, run_valuation

        roster, assumptions = self._pack(tmp_path, rows)
        return run_valuation(RunOptions(
            roster_path=roster, assumptions_path=assumptions,
            output_path=tmp_path / "결과.xlsx", allow_errors=True,
            include_sensitivity=False, include_longterm=False,
            prior=PriorPeriod(dbo=15_000_000_000, discount_rate=0.045, **prior),
        ))

    def test_a_settlement_row_produces_a_gain(self, tmp_path) -> None:
        run = self._run(tmp_path, [self._row("정산", paid=90_000_000)])
        effect = run.events.of(SETTLEMENT)
        assert effect.headcount == 1
        assert effect.obligation > 0
        assert run.rollforward.settlement_gain == pytest.approx(
            run.rollforward.settlement_paid - effect.obligation)

    def test_the_sheet_beats_a_hand_typed_number(self, tmp_path) -> None:
        """사건 시점에 실제로 잰 채무가 손으로 적은 한 칸보다 근거가 낫다."""
        run = self._run(tmp_path, [self._row("정산", paid=90_000_000)],
                        settlement_obligation=999_000_000)
        assert run.events.settled_obligation != pytest.approx(999_000_000)
        assert run.rollforward.settlement_gain == pytest.approx(
            run.rollforward.settlement_paid - run.events.settled_obligation)

    def test_a_merger_row_shows_up_as_an_inflow(self, tmp_path) -> None:
        run = self._run(tmp_path, [self._row("사업결합")])
        assert run.events.transfers_in > 0
        assert run.rollforward.transfers_in >= run.events.transfers_in

    def test_an_untouched_sheet_changes_nothing(self, tmp_path) -> None:
        """대부분의 해에는 이 시트가 비어 있다. 그때 결과가 흔들리면 안 된다."""
        plain = self._run(tmp_path / "a", [])
        assert plain.events.is_empty
        assert plain.rollforward.settlement_gain == 0.0

    def test_the_report_shows_what_happened(self, tmp_path) -> None:
        from pension.webreport import render_html

        run = self._run(tmp_path, [self._row("정산", paid=90_000_000)])
        page = render_html(run, kind="severance")
        assert "기중 제도변동" in page
        assert "정산 — 사건시점 채무" in page
