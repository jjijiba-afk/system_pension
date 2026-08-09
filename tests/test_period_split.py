"""기간별 지급률 분할.

한 사람의 급여가 근속 구간마다 다른 지급률로 갈리는 회사들이 있다. 스터디
자료에서 확인한 두 가지 모양::

    2번 케이스   호봉제 → 연봉제 전환. 전환 전 근속분은 누진 배수를 보전한다.
                 명부에 '연봉제 전환 추계일 / 누진적용 근속연수 / 누진적용 율'
                 세 열이 따로 있다.
    9번 케이스   임원 퇴직소득 세법한도. 같은 사번이 두 줄로 나뉘어 온다.
                 '19.12.31이전기간만3배수' / '20.1.1이후기간만 2배수'.
                 줄마다 배수도 기준임금도 다르다.

앞의 것은 배수만 갈리고 근속은 이어진다. 뒤의 것은 줄 자체가 구간이라 근속도
쪼개진다. 둘을 섞으면 급여가 두 번 잡히거나 통째로 빠진다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from test_valuation import BASE_DATE, make_assumptions, make_member

from pension.config import CalculationConfig, JobGroupRule
from pension.errors import IssueLog
from pension.valuation import value_member, value_roster


@pytest.fixture
def config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE_DATE,
        job_group_rules=[
            JobGroupRule(
                "정규직", "정규직", severance_nra=60, longterm_nra=60, over_nra_add_age=2
            )
        ],
    )


# ── 누진 구간 보전 (2번 케이스) ──────────────────────────────────

class TestProgressiveFreeze:
    def test_frozen_segment_keeps_its_higher_multiple(
        self, config: CalculationConfig
    ) -> None:
        """전환 전 7년은 1.5배, 그 뒤는 법정 1배.

        정년 하나만 남겨 두면 손으로 검산할 수 있다.
        """
        member = make_member(age=59, past_service=10.0, wage=1_000_000, nra=60)
        member.progressive_service = 7.0
        member.progressive_rate = 1.5
        assumptions = make_assumptions(discount=0.0, withdrawal=0.0, mortality=0.0)

        result = value_member(member, config, assumptions)

        past = result.past_service
        total = past + 1                       # 정년까지 1년
        # 총 배수 = 7×1.5 + (총근속−7)×1
        total_multiple = 7 * 1.5 + (total - 7)
        earned = 7 * 1.5 + (past - 7)
        assert result.dbo == pytest.approx(
            total_multiple * 1_000_000 * (earned / total_multiple), rel=1e-9
        )

    def test_it_raises_the_obligation_against_the_flat_scale(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=45, past_service=20.0, wage=5_000_000, nra=60)
        assumptions = make_assumptions(discount=0.045, salary=0.02, withdrawal=0.05)
        flat = value_member(member, config, assumptions).dbo

        member.progressive_service = 10.0
        member.progressive_rate = 1.4
        frozen = value_member(member, config, assumptions).dbo
        assert frozen > flat

    def test_a_rate_of_one_changes_nothing(self, config: CalculationConfig) -> None:
        """누진율 1배는 법정과 같다 — 구간을 나눠도 값이 흔들리면 안 된다."""
        member = make_member(age=45, past_service=20.0, wage=5_000_000, nra=60)
        assumptions = make_assumptions(discount=0.045, salary=0.02, withdrawal=0.05)
        flat = value_member(member, config, assumptions).dbo

        member.progressive_service = 10.0
        member.progressive_rate = 1.0
        assert value_member(member, config, assumptions).dbo == pytest.approx(
            flat, rel=1e-12
        )

    def test_only_one_of_the_two_columns_does_nothing(
        self, config: CalculationConfig
    ) -> None:
        """근속만 있고 율이 없으면 구간을 나누지 않는다(검증이 따로 알린다)."""
        member = make_member(age=45, past_service=20.0, wage=5_000_000, nra=60)
        assumptions = make_assumptions(discount=0.045, withdrawal=0.05)
        flat = value_member(member, config, assumptions).dbo

        member.progressive_service = 10.0
        assert value_member(member, config, assumptions).dbo == pytest.approx(
            flat, rel=1e-12
        )

    def test_someone_still_inside_the_frozen_window(
        self, config: CalculationConfig
    ) -> None:
        """근속이 아직 누진 구간 안이면 전 구간이 누진 배수다."""
        member = make_member(age=59, past_service=3.0, wage=1_000_000, nra=60)
        member.progressive_service = 20.0
        member.progressive_rate = 2.0
        assumptions = make_assumptions(discount=0.0, withdrawal=0.0, mortality=0.0)

        result = value_member(member, config, assumptions)
        past = result.past_service
        total = past + 1
        # 배수가 근속에 정비례하므로 귀속비율은 근속비와 같다.
        assert result.dbo == pytest.approx(
            total * 2.0 * 1_000_000 * (past / total), rel=1e-9
        )

    def test_it_shows_up_in_the_member_result(self, config: CalculationConfig) -> None:
        member = make_member(age=45, past_service=20.0, nra=60)
        member.progressive_service = 10.0
        member.progressive_rate = 1.4
        result = value_member(member, config, make_assumptions())
        assert result.progressive_service == 10.0
        assert result.progressive_rate == 1.4


# ── 줄로 나눈 지급 구간 (9번 케이스) ─────────────────────────────

def _segment(member, start: dt.date | None, end: dt.date | None, multiple: float,
             wage: float):
    """같은 사람의 한 구간을 담당하는 명부 줄."""
    member.period_start = start
    member.period_end = end
    member.payout_multiple = multiple
    member.monthly_wage = wage
    return member


class TestPeriodSegments:
    def test_two_segments_add_up_to_the_whole(self, config: CalculationConfig) -> None:
        """구간을 쪼개도 총 근속은 그대로여야 한다.

        배수와 임금을 두 줄에 똑같이 두면, 쪼개기 전 한 줄과 값이 같아야 한다.
        """
        whole = make_member(age=59, past_service=20.0, wage=1_000_000, nra=60)
        assumptions = make_assumptions(discount=0.045, withdrawal=0.03, mortality=0.001)
        one = value_member(whole, config, assumptions)

        start = whole.settlement_date
        cut = start + dt.timedelta(days=round(8 * 365.25))
        first = _segment(
            make_member(age=59, past_service=20.0, wage=1_000_000, nra=60),
            None, cut, 1.0, 1_000_000,
        )
        second = _segment(
            make_member(age=59, past_service=20.0, wage=1_000_000, nra=60),
            cut + dt.timedelta(days=1), None, 1.0, 1_000_000,
        )
        # 끝난 구간은 임금을 올리지 않으므로 임금상승률을 0 으로 둔 비교다.
        split = (
            value_member(first, config, assumptions).dbo
            + value_member(second, config, assumptions).dbo
        )
        assert split == pytest.approx(one.dbo, rel=1e-6)

    def test_each_segment_only_counts_its_own_service(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=59, past_service=20.0, wage=1_000_000, nra=60)
        cut = member.settlement_date + dt.timedelta(days=round(8 * 365.25))
        _segment(member, None, cut, 1.0, 1_000_000)

        result = value_member(member, config, make_assumptions())
        assert result.past_service == pytest.approx(8.0, abs=0.02)

    def test_a_closed_segment_does_not_grow_its_wage(
        self, config: CalculationConfig
    ) -> None:
        """끝난 구간의 임금은 그 시점의 기준임금이다 — 다시 올리면 안 된다."""
        cut = BASE_DATE - dt.timedelta(days=365)
        closed = _segment(
            make_member(age=50, past_service=20.0, wage=1_000_000, nra=60),
            None, cut, 1.0, 1_000_000,
        )
        no_growth = value_member(
            closed, config, make_assumptions(discount=0.03, salary=0.0)
        ).dbo
        with_growth = value_member(
            closed, config, make_assumptions(discount=0.03, salary=0.05)
        ).dbo
        assert with_growth == pytest.approx(no_growth, rel=1e-12)

    def test_an_open_segment_still_grows(self, config: CalculationConfig) -> None:
        member = make_member(age=50, past_service=20.0, wage=1_000_000, nra=60)
        member.period_start = member.settlement_date + dt.timedelta(days=365)
        no_growth = value_member(
            member, config, make_assumptions(discount=0.03, salary=0.0)
        ).dbo
        with_growth = value_member(
            member, config, make_assumptions(discount=0.03, salary=0.05)
        ).dbo
        assert with_growth > no_growth

    def test_different_multiples_per_segment(self, config: CalculationConfig) -> None:
        """세법한도처럼 앞 구간 3배 · 뒤 구간 2배."""
        from pension.assumptions import BenefitScale
        from pension.formula import Formula
        from pension.models import Roster

        assumptions = make_assumptions(discount=0.0, withdrawal=0.0, mortality=0.0)
        assumptions.severance_benefit = BenefitScale(
            formulas={"세법한도": Formula("t * 배수")}, statutory_when_missing=True
        )

        base = make_member(age=59, past_service=20.0, wage=1_000_000, nra=60)
        cut = base.settlement_date + dt.timedelta(days=round(15 * 365.25))

        rows = []
        for start, end, multiple, wage in (
            (None, cut, 3.0, 1_000_000),
            (cut + dt.timedelta(days=1), None, 2.0, 2_000_000),
        ):
            member = make_member(age=59, past_service=20.0, wage=wage, nra=60)
            member.rules.severance_benefit = "세법한도"
            rows.append(_segment(member, start, end, multiple, wage))

        result = value_roster(Roster(active=rows), config, assumptions)
        # 앞 구간 15년 × 3배 × 100만 + 뒤 구간 (총근속−15)년 × 2배 × 200만.
        # 배수가 근속에 정비례하므로 귀속비율은 각 구간의 근속비다.
        first, second = result.members
        assert first.past_service == pytest.approx(15.0, abs=0.02)
        assert second.past_service == pytest.approx(5.0, abs=0.02)
        assert first.dbo == pytest.approx(15.0 * 3.0 * 1_000_000, rel=1e-3)


# ── 검증 ─────────────────────────────────────────────────────────

class TestValidation:
    def _validate(self, members, config):
        from pension.validation import validate_active

        log = IssueLog()
        validate_active(members, config, log)
        return log

    def test_progressive_rate_without_service_is_flagged(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=45, past_service=20.0)
        member.employee_id = "A1"
        member.progressive_rate = 1.4
        codes = [i.code for i in self._validate([member], config)]
        assert "JAE_PROGRESSIVE_SERVICE_MISSING" in codes

    def test_progressive_service_without_rate_is_flagged(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=45, past_service=20.0)
        member.employee_id = "A1"
        member.progressive_service = 10.0
        codes = [i.code for i in self._validate([member], config)]
        assert "JAE_PROGRESSIVE_RATE_MISSING" in codes

    def test_a_frozen_window_longer_than_service_is_an_error(
        self, config: CalculationConfig
    ) -> None:
        member = make_member(age=45, past_service=10.0)
        member.employee_id = "A1"
        member.progressive_service = 25.0
        member.progressive_rate = 1.4
        log = self._validate([member], config)
        assert any(i.code == "JAE_PROGRESSIVE_TOO_LONG" for i in log.errors)

    def test_a_consistent_pair_raises_nothing(self, config: CalculationConfig) -> None:
        member = make_member(age=45, past_service=20.0)
        member.employee_id = "A1"
        member.progressive_service = 10.0
        member.progressive_rate = 1.4
        codes = [i.code for i in self._validate([member], config)]
        assert not any(c.startswith("JAE_PROGRESSIVE") for c in codes)

    def test_duplicate_ids_with_periods_are_not_an_error(
        self, config: CalculationConfig
    ) -> None:
        """기간을 적어 주면 중복이 아니라 한 사람을 쪼갠 것이다."""
        cut = dt.date(2019, 12, 31)
        rows = []
        for start, end in ((None, cut), (cut + dt.timedelta(days=1), None)):
            member = make_member(age=59, past_service=20.0)
            member.employee_id = "ER1"
            member.period_start, member.period_end = start, end
            rows.append(member)
        log = self._validate(rows, config)
        assert not any(i.code == "JAE_DUP_ID" for i in log.errors)

    def test_overlapping_periods_are_reported(self, config: CalculationConfig) -> None:
        rows = []
        for start, end in (
            (dt.date(2010, 1, 1), dt.date(2019, 12, 31)),
            (dt.date(2019, 1, 1), None),
        ):
            member = make_member(age=59, past_service=20.0)
            member.employee_id = "ER1"
            member.period_start, member.period_end = start, end
            rows.append(member)
        codes = [i.code for i in self._validate(rows, config)]
        assert "JAE_PERIOD_OVERLAP" in codes

    def test_a_gap_between_periods_is_reported(self, config: CalculationConfig) -> None:
        rows = []
        for start, end in (
            (dt.date(2010, 1, 1), dt.date(2018, 12, 31)),
            (dt.date(2020, 1, 1), None),
        ):
            member = make_member(age=59, past_service=20.0)
            member.employee_id = "ER1"
            member.period_start, member.period_end = start, end
            rows.append(member)
        codes = [i.code for i in self._validate(rows, config)]
        assert "JAE_PERIOD_GAP" in codes

    def test_duplicates_with_different_multiples_get_a_hint(
        self, config: CalculationConfig
    ) -> None:
        """기간 열 없이 배수만 다르면, 왜 중복인지 짐작해 알려 준다."""
        rows = []
        for multiple in (3.0, 2.0):
            member = make_member(age=59, past_service=20.0)
            member.employee_id = "ER1"
            member.payout_multiple = multiple
            rows.append(member)
        issues = [i for i in self._validate(rows, config).errors if i.code == "JAE_DUP_ID"]
        assert issues
        assert "지급률 기산일" in issues[0].message

    def test_reader_picks_up_the_new_columns(self, tmp_path) -> None:
        """머리글만 붙여 주면 열 위치가 어디든 읽혀야 한다."""
        import openpyxl

        from pension.config import read_config
        from pension.readers import read_roster

        wb = openpyxl.Workbook()
        del wb["Sheet"]
        setup = wb.create_sheet("Input")
        setup["B3"], setup["C3"] = "산출기준일", BASE_DATE
        setup.cell(11, 2, "명부직군")
        setup.cell(11, 3, "변환직군명")
        setup.cell(12, 2, "정규직")
        setup.cell(12, 3, "정규직")
        setup.cell(12, 4, 60)

        ws = wb.create_sheet("재직자명부")
        headers = [
            "사번", "임직원구분", "직군", "성명", "성별", "생년월일", "입사일자",
            "30일 평균임금", "퇴직급여 제도구분", "퇴직금 지급배수",
            "연봉제 전환 추계일", "누진적용 근속연수", "누진적용 율",
            "지급률 기산일", "지급률 종료일",
        ]
        for col, title in enumerate(headers, start=3):
            ws.cell(23, col, title)
        values = [
            "ER1", "임원", "정규직", "이이이", "남자", dt.date(1965, 3, 1),
            dt.date(2005, 1, 1), 13_922_406, "DB", 3.0,
            dt.date(2016, 1, 1), 7.5, 1.3,
            dt.date(2005, 1, 1), dt.date(2019, 12, 31),
        ]
        for col, value in enumerate(values, start=3):
            ws.cell(26, col, value)

        retired = wb.create_sheet("퇴직자명부")
        for col, title in enumerate(["사번", "성명", "퇴사일"], start=3):
            retired.cell(19, col, title)

        path = tmp_path / "구간명부.xlsx"
        wb.save(path)

        book = openpyxl.load_workbook(path, data_only=True)
        try:
            cfg = read_config(book)
            roster = read_roster(book, cfg, IssueLog())
        finally:
            book.close()

        member = roster.active[0]
        assert member.progressive_service == 7.5
        assert member.progressive_rate == 1.3
        assert member.annual_salary_date == dt.date(2016, 1, 1)
        assert member.period_start == dt.date(2005, 1, 1)
        assert member.period_end == dt.date(2019, 12, 31)
        assert member.has_period is True

    def test_plain_duplicates_stay_a_plain_error(self, config: CalculationConfig) -> None:
        rows = []
        for _ in range(2):
            member = make_member(age=59, past_service=20.0)
            member.employee_id = "A1"
            rows.append(member)
        issues = [i for i in self._validate(rows, config).errors if i.code == "JAE_DUP_ID"]
        assert issues
        assert "지급률 기산일" not in issues[0].message
