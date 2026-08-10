"""전기 명부와 맞대어 보기.

당기 명부만 보면 멀쩡한데 전기와 나란히 놓아야 드러나는 잘못이 있다. 결산이
끝난 뒤에 발견하면 다시 산출해야 하므로, 산출 전에 잡는 것이 요점이다.

**놓치면 안 되는 것** 과 **괜히 겁주면 안 되는 것** 을 함께 고정한다. 실제로
사람이 바뀐 경우도 많아서(개명·주민번호 정정·승진), 전부 빨간 글씨로 내면
아무도 안 읽는다.
"""

from __future__ import annotations

import datetime as _dt

from pension.models import ActiveMember, BenefitPlan, RetiredMember, Roster
from pension.priorcheck import compare_rosters


def active(employee_id, *, birth="1985-05-01", hire="2010-03-02",
           wage=5_000_000, plan=BenefitPlan.DB, seq=1) -> ActiveMember:
    return ActiveMember(
        seq=seq, row=seq + 25, employee_id=employee_id,
        birth_date=_dt.date.fromisoformat(birth),
        hire_date=_dt.date.fromisoformat(hire),
        monthly_wage=wage, plan=plan,
    )


def retired(employee_id, seq=1) -> RetiredMember:
    return RetiredMember(seq=seq, row=seq + 21, employee_id=employee_id)


def roster(actives=(), retireds=()) -> Roster:
    return Roster(active=list(actives), retired=list(retireds))


def codes(result) -> list[str]:
    return [f.code for f in result.findings]


class TestNothingWrong:
    def test_identical_rosters_are_quiet(self) -> None:
        people = [active(f"A{n:03d}", seq=n) for n in range(1, 11)]
        result = compare_rosters(roster(people), roster(people))
        assert result.findings == []
        assert "이상 없습니다" in result.summary()
        assert result.matched == 10

    def test_a_normal_raise_is_not_flagged(self) -> None:
        """호봉 인상은 해마다 있는 일이다. 이것까지 잡으면 아무도 안 읽는다."""
        before = [active("A001", wage=5_000_000)]
        after = [active("A001", wage=5_300_000)]
        assert compare_rosters(roster(after), roster(before)).findings == []

    def test_a_leaver_moved_to_the_retired_sheet_is_fine(self) -> None:
        before = roster([active("A001"), active("A002", seq=2)])
        after = roster([active("A001")], [retired("A002")])
        assert compare_rosters(after, before).findings == []


class TestThingsThatBreakTheNumbers:
    """산출 결과를 틀리게 만드는 것 — 반드시 잡아야 한다."""

    def test_a_changed_birth_date(self) -> None:
        before = roster([active("A001", birth="1985-05-01")])
        after = roster([active("A001", birth="1958-05-01")])

        result = compare_rosters(after, before)
        assert codes(result) == ["생년월일 변경"]
        assert result.serious
        assert "1985-05-01" in result.findings[0].message
        assert "1958-05-01" in result.findings[0].message

    def test_a_changed_hire_date(self) -> None:
        before = roster([active("A001", hire="2010-03-02")])
        after = roster([active("A001", hire="2020-03-02")])

        result = compare_rosters(after, before)
        assert codes(result) == ["입사일 변경"]
        assert result.serious

    def test_someone_who_vanished(self) -> None:
        """퇴직자로 옮기지 않고 재직자에서 지우면 채무가 조용히 줄어든다."""
        before = roster([active("A001"), active("A002", seq=2)])
        after = roster([active("A001")])

        result = compare_rosters(after, before)
        assert codes(result) == ["행방불명"]
        assert result.findings[0].employee_id == "A002"
        assert result.serious

    def test_a_thousandfold_wage_is_serious(self) -> None:
        """천원 단위를 원 단위로 옮기면 정확히 1000배가 된다."""
        before = roster([active("A001", wage=5_000)])
        after = roster([active("A001", wage=5_000_000)])

        result = compare_rosters(after, before)
        assert codes(result) == ["임금 급등"]
        assert result.serious

    def test_a_big_headcount_shift(self) -> None:
        before = roster([active(f"A{n:03d}", seq=n) for n in range(1, 101)])
        after = roster([active(f"A{n:03d}", seq=n) for n in range(1, 51)])

        result = compare_rosters(after, before)
        assert "인원 급변" in codes(result)
        assert "100" in result.findings[0].message and "50" in result.findings[0].message

    def test_completely_different_ids_stop_the_comparison(self) -> None:
        """사번 체계가 바뀌면 뒤의 비교가 전부 헛것이다. 거기서 멈춘다."""
        before = roster([active(f"A{n:03d}", seq=n) for n in range(1, 11)])
        after = roster([active(f"B{n:03d}", seq=n) for n in range(1, 11)])

        result = compare_rosters(after, before)
        assert codes(result) == ["사번 불일치"]      # 행방불명 10건이 쏟아지지 않는다
        assert result.matched == 0


class TestThingsWorthALook:
    """틀렸다고 단정할 수 없는 것 — 알리되 겁주지 않는다."""

    def test_a_wage_drop_is_only_a_note(self) -> None:
        """임금피크가 실제로 있다."""
        before = roster([active("A001", wage=10_000_000)])
        after = roster([active("A001", wage=6_000_000)])

        result = compare_rosters(after, before)
        assert codes(result) == ["임금 급감"]
        assert not result.serious          # 빨간 글씨로 내지 않는다
        assert result.notes

    def test_a_plan_switch_is_only_a_note(self) -> None:
        before = roster([active("A001", plan=BenefitPlan.DB)])
        after = roster([active("A001", plan=BenefitPlan.DC)])

        result = compare_rosters(after, before)
        assert codes(result) == ["제도 전환"]
        assert not result.serious
        assert "정산손익" in result.findings[0].message

    def test_a_rehire_is_only_a_note(self) -> None:
        before = roster([active("A001")], [retired("A002")])
        after = roster([active("A001"), active("A002", seq=2)])

        result = compare_rosters(after, before)
        assert codes(result) == ["퇴직자 재등장"]
        assert not result.serious


class TestShape:
    def test_findings_are_counted_by_kind(self) -> None:
        """같은 성격이 수십 건씩 나온다. 화면은 묶어서 보여 준다."""
        before = roster([active(f"A{n:03d}", seq=n, wage=5_000) for n in range(1, 6)])
        after = roster([active(f"A{n:03d}", seq=n, wage=5_000_000) for n in range(1, 6)])

        result = compare_rosters(after, before)
        assert result.counts() == {"임금 급등": 5}
        assert "5건" in result.summary() or "확인" in result.summary()

    def test_duplicate_ids_do_not_multiply_findings(self) -> None:
        """임원 세법한도로 같은 사번이 두 줄인 명부가 있다."""
        before = roster([active("A001", birth="1985-05-01", seq=1),
                         active("A001", birth="1985-05-01", seq=2)])
        after = roster([active("A001", birth="1958-05-01", seq=1),
                        active("A001", birth="1958-05-01", seq=2)])

        assert codes(compare_rosters(after, before)) == ["생년월일 변경"]

    def test_an_empty_prior_roster_says_nothing(self) -> None:
        """전기가 없으면 비교할 것이 없다 — 최초 평가다."""
        assert compare_rosters(roster([active("A001")]), roster()).findings == []


class TestSmallGroups:
    """작은 단체에서 비율 검사가 오탐을 내면 안 된다.

    8명짜리 단체에서 두 명이 나가면 -25% 다. 정상인데도 매번 걸리면 사람이
    경고를 무시하기 시작하고, 그때부터 검사가 없는 것과 같아진다.
    """

    def test_a_small_group_is_not_flagged_for_headcount(self) -> None:
        before = roster([active(f"A{n:03d}", seq=n) for n in range(1, 9)])
        after = roster([active(f"A{n:03d}", seq=n) for n in range(1, 7)])

        result = compare_rosters(after, before)
        assert "인원 급변" not in codes(result)

    def test_individual_checks_still_run_for_small_groups(self) -> None:
        """인원 검사만 쉬는 것이지, 작은 단체가 검사에서 빠지는 것이 아니다."""
        before = roster([active("A001", birth="1985-05-01")])
        after = roster([active("A001", birth="1958-05-01")])
        assert codes(compare_rosters(after, before)) == ["생년월일 변경"]

    def test_a_big_group_is_still_flagged(self) -> None:
        before = roster([active(f"A{n:03d}", seq=n) for n in range(1, 41)])
        after = roster([active(f"A{n:03d}", seq=n) for n in range(1, 21)])
        assert "인원 급변" in codes(compare_rosters(after, before))
