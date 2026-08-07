"""연령·정년연령 계산과 코드값 정규화."""

from __future__ import annotations

import datetime as dt

import pytest

from pension.actuarial import attained_age, longterm_retirement_age, normal_retirement_age
from pension.config import JobGroupRule
from pension.normalize import (
    BenefitPlan,
    EmployeeType,
    Gender,
    RetirementReason,
    is_ambiguous_reason,
    normalize_benefit_plan,
    normalize_employee_type,
    normalize_gender,
    normalize_retirement_reason,
    normalize_yes_no,
)


class TestAttainedAge:
    def test_before_birthday_in_the_year(self) -> None:
        assert attained_age(dt.date(1980, 12, 31), dt.date(2025, 6, 30)) == 44

    def test_after_birthday_in_the_year(self) -> None:
        assert attained_age(dt.date(1980, 3, 15), dt.date(2025, 12, 31)) == 45

    def test_birthday_itself_counts_as_passed(self) -> None:
        """VBA 의 ``dd >= dd2`` 규칙. 통상 만나이와 갈리는 유일한 지점이다."""
        assert attained_age(dt.date(1980, 12, 31), dt.date(2025, 12, 31)) == 45

    def test_day_before_birthday(self) -> None:
        assert attained_age(dt.date(1980, 12, 31), dt.date(2025, 12, 30)) == 44


@pytest.fixture
def rule() -> JobGroupRule:
    return JobGroupRule(
        source_name="정규직", mapped_name="1정규직",
        severance_nra=60, longterm_nra=58, over_nra_add_age=2,
    )


class TestRetirementAge:
    def test_under_nra_uses_the_rule(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(45, rule) == 60

    def test_over_nra_gets_the_add_on(self, rule: JobGroupRule) -> None:
        """이미 정년을 넘긴 사람은 현재 연령 + 가산연수를 정년으로 본다."""
        assert normal_retirement_age(62, rule) == 64

    def test_exactly_at_nra_gets_the_add_on(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(60, rule) == 62

    def test_wage_peak_age_wins_when_it_is_ahead(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(50, rule, wage_peak_age=57) == 57

    def test_wage_peak_age_ignored_when_already_passed(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(58, rule, wage_peak_age=57) == 60

    def test_longterm_ignores_wage_peak(self, rule: JobGroupRule) -> None:
        assert longterm_retirement_age(45, rule) == 58
        assert longterm_retirement_age(59, rule) == 61


class TestNormalizeEmployeeType:
    @pytest.mark.parametrize("raw", ["임원", "임", "Y", "y", 2, "2"])
    def test_executive_tokens(self, raw: object) -> None:
        assert normalize_employee_type(raw) is EmployeeType.EXECUTIVE

    @pytest.mark.parametrize("raw", ["직원", "N", 1, "", None, "사원"])
    def test_everything_else_is_staff(self, raw: object) -> None:
        assert normalize_employee_type(raw) is EmployeeType.STAFF


class TestNormalizeGender:
    @pytest.mark.parametrize("raw", ["여자", "여", "녀", "여성", 2, 4, 6, 8])
    def test_female_tokens(self, raw: object) -> None:
        assert normalize_gender(raw) is Gender.FEMALE

    @pytest.mark.parametrize("raw", ["남자", "남", 1, 3, "", None])
    def test_everything_else_is_male(self, raw: object) -> None:
        assert normalize_gender(raw) is Gender.MALE


class TestNormalizeBenefitPlan:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("DB", BenefitPlan.DB),
            ("혼합형", BenefitPlan.DB),      # 시트 M14 수식 규칙
            ("임원혼합형", BenefitPlan.DB),
            ("DC", BenefitPlan.DC),
            ("DC전환", BenefitPlan.DC),
            ("퇴직금제도", BenefitPlan.LEGACY),
            ("퇴직금", BenefitPlan.LEGACY),
            ("미가입", BenefitPlan.LEGACY),
        ],
    )
    def test_known_values(self, raw: str, expected: BenefitPlan) -> None:
        assert normalize_benefit_plan(raw) is expected

    def test_blank_and_unknown_are_none(self) -> None:
        assert normalize_benefit_plan("") is None
        assert normalize_benefit_plan("알수없음") is None


class TestNormalizeRetirementReason:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (1, RetirementReason.VOLUNTARY),
            ("중도퇴직", RetirementReason.VOLUNTARY),
            ("계약만료", RetirementReason.VOLUNTARY),
            ("사망퇴직", RetirementReason.DEATH),
            ("DC전환", RetirementReason.DC_CONVERSION),
            ("정년퇴직", RetirementReason.NORMAL),
            ("계열사전출", RetirementReason.TRANSFER_OUT),
            ("사업처분/분할", RetirementReason.DISPOSAL),
        ],
    )
    def test_known_values(self, raw: object, expected: RetirementReason) -> None:
        assert normalize_retirement_reason(raw) is expected

    def test_blank_is_none(self) -> None:
        assert normalize_retirement_reason("") is None

    def test_wage_peak_follows_vba_but_is_flagged(self) -> None:
        """VBA 는 4(정년), 시트 수식은 3(중간정산)으로 갈린다."""
        raw = "임금피크제도에 따른 중간정산"
        assert normalize_retirement_reason(raw) is RetirementReason.NORMAL
        assert is_ambiguous_reason(raw)
        assert not is_ambiguous_reason("정년퇴직")


def test_yes_no_flag() -> None:
    assert normalize_yes_no("Y") == "Y"
    assert normalize_yes_no("n") == "N"
    assert normalize_yes_no("") == ""
    assert normalize_yes_no("", default="Y") == "Y"
