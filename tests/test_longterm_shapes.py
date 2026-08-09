"""장기급여의 지급 모양 — 복합 지급·지급시점·반복·누적.

지금까지는 '근속 N년에 닿으면 재직 중에 한 번 준다' 만 담을 수 있었다. 실제
규정은 그보다 다양하다. 스터디 자료에서 확인한 것들::

    1번 케이스   재직 포상(휴가 + refresh지원금)에 더해, 퇴직 시 현물 3~10돈,
                 정년퇴직 시 현물 10~20돈을 따로 준다.
    10번 케이스  3/6/9년 유급휴가 5·10·15일 — '휴가는 소멸기한 없음'.
                 안 쓰면 쌓였다가 나갈 때 정산된다.
    18번 케이스  10/20/30년 : 휴가 + 금 + 특별상여를 한꺼번에 준다.
                 지급일은 창립기념일(10/1)이라 도달해도 최대 1년 밀린다.
    22번 케이스  건강검진처럼 몇 년마다 되풀이하는 급여.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from pension.assumptions import (
    LT_AT_EXIT,
    LT_AT_MILESTONE,
    LT_AT_NRA,
    LT_CASH,
    LT_VACATION,
    Assumptions,
    BenefitScale,
    DiscountCurve,
    LongTermRule,
    MortalityTable,
    RateCurve,
    RateTable,
    load_assumptions,
    write_assumptions,
)
from pension.config import CalculationConfig, JobGroupRule
from pension.longterm import value_longterm_member
from pension.models import ActiveMember
from pension.normalize import BenefitPlan

BASE = _dt.date(2025, 12, 31)
RULE = "정규직"


def _config() -> CalculationConfig:
    return CalculationConfig(
        base_date=BASE,
        job_group_rules=[JobGroupRule(RULE, RULE, severance_nra=60, longterm_nra=60)],
    )


def _member(*, age: int = 35, service: float = 5.0) -> ActiveMember:
    member = ActiveMember(seq=1, row=26)
    member.employee_id = "A1"
    member.job_group = RULE
    member.job_group_index = 0
    member.birth_date = _dt.date(BASE.year - age, 1, 1)
    member.hire_date = BASE - _dt.timedelta(days=round(service * 365.25))
    member.settlement_date = member.hire_date
    member.monthly_wage = 3_000_000
    member.daily_base_pay = 100_000
    member.plan = BenefitPlan.DB
    member.longterm_target = "Y"
    member.age = age
    member.longterm_nra = 60
    return member


def _assumptions(
    curves: dict[str, dict[int, float]],
    items: list[LongTermRule],
    *,
    withdrawal: float = 0.0,
    mortality: float = 0.0,
    discount: float = 0.05,
) -> Assumptions:
    return Assumptions(
        discount=DiscountCurve(spot=RateCurve({1: discount}), flat=discount),
        withdrawal=RateTable(
            curves={"기본": RateCurve({0: withdrawal})}, default_rule="기본"
        ),
        mortality=MortalityTable(RateCurve({0: mortality}), RateCurve({0: mortality})),
        longterm_benefit=BenefitScale(
            curves={name: RateCurve(points) for name, points in curves.items()},
            statutory_when_missing=False,
        ),
        longterm_rules={RULE: items},
    )


def _dbo(assumptions, member=None) -> float:
    return value_longterm_member(member or _member(), _config(), assumptions).dbo


class TestCompoundItems:
    """18번 케이스 — '10년 : 휴가 3일, 금 10돈, 특별상여'."""

    def test_items_add_up(self) -> None:
        vacation = _assumptions(
            {RULE: {10: 3}}, [LongTermRule(kind=LT_VACATION)]
        )
        gold = _assumptions(
            {"금": {10: 5_000_000}},
            [LongTermRule(kind=LT_CASH, item="금")],
        )
        both = _assumptions(
            {RULE: {10: 3}, "금": {10: 5_000_000}},
            [LongTermRule(kind=LT_VACATION), LongTermRule(kind=LT_CASH, item="금")],
        )
        assert _dbo(both) == pytest.approx(_dbo(vacation) + _dbo(gold), rel=1e-12)

    def test_each_item_uses_its_own_column(self) -> None:
        """항목이 같은 열을 보면 규정이 통째로 어긋난다."""
        assumptions = _assumptions(
            {RULE: {10: 3}, "금": {10: 5_000_000}},
            [LongTermRule(kind=LT_VACATION), LongTermRule(kind=LT_CASH, item="금")],
        )
        result = value_longterm_member(_member(), _config(), assumptions)
        assert result.benefit_kind == "휴가 + 현금"

    def test_a_missing_column_does_not_kill_the_others(self) -> None:
        """항목 하나의 열이 비어도 나머지는 산출돼야 한다."""
        assumptions = _assumptions(
            {RULE: {10: 3}},
            [LongTermRule(kind=LT_VACATION), LongTermRule(kind=LT_CASH, item="없는열")],
        )
        assert _dbo(assumptions) > 0


class TestTiming:
    """1번 케이스 — 재직 포상 / 퇴직 시 예우 / 정년 시 예우."""

    def test_at_exit_pays_even_to_leavers(self) -> None:
        """퇴직 시 지급은 중도퇴직자도 받는다."""
        at_exit = _assumptions(
            {RULE: {10: 1_000_000}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)],
            withdrawal=0.10,
        )
        at_nra = _assumptions(
            {RULE: {10: 1_000_000}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_NRA)],
            withdrawal=0.10,
        )
        # 중도퇴직률이 높으면 정년까지 남는 사람이 적어 정년 지급이 훨씬 작다.
        assert _dbo(at_exit) > _dbo(at_nra)

    def test_at_nra_needs_nobody_to_leave(self) -> None:
        """탈퇴가 없으면 퇴직시와 정년시가 같아진다 — 어차피 정년에 나간다."""
        common = {"curves": {RULE: {10: 1_000_000}}, "withdrawal": 0.0}
        at_exit = _assumptions(
            common["curves"], [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)]
        )
        at_nra = _assumptions(
            common["curves"], [LongTermRule(kind=LT_CASH, timing=LT_AT_NRA)]
        )
        assert _dbo(at_exit) == pytest.approx(_dbo(at_nra), rel=1e-9)

    def test_at_exit_uses_a_step_lookup(self) -> None:
        """'20년 이상이면 8돈' — 계단이지 합계가 아니다."""
        member = _member(age=59, service=25.0)     # 정년까지 1년, 이미 25년
        assumptions = _assumptions(
            {RULE: {10: 3.0, 15: 5.0, 20: 8.0}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)],
        )
        # 탈퇴 없음 · 할인 0 → 1년 뒤 정년에 8 을 받고, 귀속은 지금 자격(8)/그때(8).
        flat = _assumptions(
            {RULE: {10: 3.0, 15: 5.0, 20: 8.0}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)],
            discount=0.0,
        )
        assert _dbo(flat, member) == pytest.approx(8.0, rel=1e-9)
        assert _dbo(assumptions, member) < 8.0     # 할인이 걸린다

    def test_milestone_timing_ignores_the_past(self) -> None:
        """재직 중 지급은 이미 지나간 시점을 다시 잡지 않는다."""
        member = _member(age=50, service=25.0)
        assumptions = _assumptions(
            {RULE: {10: 1_000_000}}, [LongTermRule(kind=LT_CASH)]
        )
        assert _dbo(assumptions, member) == 0.0

    def test_at_exit_still_counts_a_past_milestone(self) -> None:
        """퇴직 시 지급은 자격만 갖추면 되므로, 지나간 근속도 살아 있다."""
        member = _member(age=50, service=25.0)
        assumptions = _assumptions(
            {RULE: {10: 1_000_000}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)],
        )
        assert _dbo(assumptions, member) > 0.0


class TestAccumulate:
    """10번 케이스 — '유급휴가 소멸기한 없음'."""

    def test_unused_leave_piles_up(self) -> None:
        member = _member(age=50, service=12.0)
        points = {RULE: {3: 5.0, 6: 10.0, 9: 15.0}}
        stepped = _assumptions(
            points, [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT)], discount=0.0
        )
        piled = _assumptions(
            points,
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT, accumulate=True)],
            discount=0.0,
        )
        # 계단이면 15, 누적이면 5+10+15 = 30.
        assert _dbo(stepped, member) == pytest.approx(15.0, rel=1e-9)
        assert _dbo(piled, member) == pytest.approx(30.0, rel=1e-9)

    def test_it_only_counts_what_has_been_reached(self) -> None:
        member = _member(age=50, service=7.0)      # 3년·6년만 지났다
        piled = _assumptions(
            {RULE: {3: 5.0, 6: 10.0, 9: 15.0}},
            [LongTermRule(kind=LT_CASH, timing=LT_AT_EXIT, accumulate=True)],
            discount=0.0,
        )
        # 3년·6년분은 다 벌었고, 9년분은 7/9 만큼 쌓였다.
        assert _dbo(piled, member) == pytest.approx(5 + 10 + 15 * 7 / 9, rel=1e-3)


class TestRepeat:
    """22번 케이스 — 몇 년마다 되풀이하는 급여."""

    def test_a_cycle_adds_more_payments(self) -> None:
        once = _assumptions({RULE: {10: 500_000}}, [LongTermRule(kind=LT_CASH)])
        every_two = _assumptions(
            {RULE: {10: 500_000}}, [LongTermRule(kind=LT_CASH, every_years=2)]
        )
        assert _dbo(every_two) > _dbo(once)

    def test_the_cycle_stops_at_the_retirement_age(self) -> None:
        member = _member(age=58, service=20.0)     # 정년까지 2년
        assumptions = _assumptions(
            {RULE: {21: 500_000}},
            [LongTermRule(kind=LT_CASH, every_years=1)],
            discount=0.0,
        )
        result = value_longterm_member(member, _config(), assumptions)
        assert result.milestone_count == 2         # 21년·22년만 닿는다

    def test_zero_means_once(self) -> None:
        once = _assumptions({RULE: {10: 500_000}}, [LongTermRule(kind=LT_CASH)])
        explicit = _assumptions(
            {RULE: {10: 500_000}}, [LongTermRule(kind=LT_CASH, every_years=0)]
        )
        assert _dbo(explicit) == pytest.approx(_dbo(once), rel=1e-12)


class TestAnniversary:
    """18번 케이스 — 근속에 닿아도 창립기념일이 와야 준다."""

    def test_payment_slips_to_the_anniversary(self) -> None:
        plain = _assumptions({RULE: {10: 1_000_000}}, [LongTermRule(kind=LT_CASH)])
        delayed = _assumptions(
            {RULE: {10: 1_000_000}},
            [LongTermRule(kind=LT_CASH, anniversary="10-01")],
        )
        # 지급이 밀리면 그만큼 더 할인된다.
        assert _dbo(delayed) < _dbo(plain)

    def test_the_slip_is_never_more_than_a_year(self) -> None:
        member = _member(age=35, service=5.0)
        base = _assumptions({RULE: {10: 1_000_000}}, [LongTermRule(kind=LT_CASH)])
        delayed = _assumptions(
            {RULE: {10: 1_000_000}},
            [LongTermRule(kind=LT_CASH, anniversary="10-01")],
        )
        one_more_year = _assumptions(
            {RULE: {11: 1_000_000}}, [LongTermRule(kind=LT_CASH)]
        )
        assert _dbo(one_more_year, member) < _dbo(delayed, member) < _dbo(base, member)


class TestWorkbookRoundTrip:
    def _sheets(self) -> dict:
        return {
            "할인율": (["연차", "할인율"], [[1, 0.045]]),
            "지급률": (["근속연수", RULE], [[0, 1.0]]),
            "장기급여지급률": (["근속연수", RULE, "금"], [[10, 3, 5_000_000]]),
        }

    def test_multiple_items_survive(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [
                [RULE, LT_VACATION, None, "", "", LT_AT_MILESTONE, None, "", "10-01"],
                [RULE, LT_CASH, None, "현물 포상", "금", LT_AT_EXIT, 5, "Y", ""],
            ],
        )
        items = load_assumptions(path).longterm_items(RULE)
        assert len(items) == 2
        assert items[0].anniversary == "10-01"
        assert items[1].item == "금"
        assert items[1].timing == LT_AT_EXIT
        assert items[1].every_years == 5
        assert items[1].accumulate is True
        assert items[0].column(RULE) == RULE
        assert items[1].column(RULE) == "금"

    def test_an_excel_mangled_payday_is_still_read(self, tmp_path) -> None:
        """엑셀이 ``10-01`` 을 날짜로 바꿔 저장해도 월-일로 읽어야 한다."""
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_VACATION, None, "", "", LT_AT_MILESTONE, None, "",
              _dt.datetime(2025, 10, 1)]],
        )
        assert load_assumptions(path).longterm_items(RULE)[0].anniversary == "10-01"

    def test_an_unreadable_payday_is_refused(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_VACATION, None, "", "", LT_AT_MILESTONE, None, "", "창립기념일"]],
        )
        with pytest.raises(ValueError, match="창립기념일"):
            load_assumptions(path)

    def test_a_payday_on_an_exit_benefit_is_refused(self, tmp_path) -> None:
        """나갈 때 주는 급여에 창립기념일을 적으면 뜻이 서로 어긋난다."""
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_CASH, None, "", "", LT_AT_EXIT, None, "", "10-01"]],
        )
        with pytest.raises(ValueError, match="지급일은"):
            load_assumptions(path)

    def test_an_unknown_timing_is_refused(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_CASH, None, "", "", "아무때나", None, "", ""]],
        )
        with pytest.raises(ValueError, match="아무때나"):
            load_assumptions(path)

    def test_the_same_item_twice_is_refused(self, tmp_path) -> None:
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [
                [RULE, LT_CASH, None, "", "금", LT_AT_EXIT, None, "", ""],
                [RULE, LT_VACATION, None, "", "금", LT_AT_EXIT, None, "", ""],
            ],
        )
        with pytest.raises(ValueError, match="두 번"):
            load_assumptions(path)

    def test_an_old_four_column_sheet_still_loads(self, tmp_path) -> None:
        """새 열이 없던 파일도 그대로 읽혀야 한다."""
        path = write_assumptions(
            tmp_path / "기초율.xlsx", self._sheets(), None,
            [[RULE, LT_VACATION, None, ""]],
        )
        items = load_assumptions(path).longterm_items(RULE)
        assert len(items) == 1
        assert items[0].timing == LT_AT_MILESTONE
        assert items[0].every_years == 0.0
        assert items[0].accumulate is False
