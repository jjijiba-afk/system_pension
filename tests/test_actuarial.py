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
        """생일 당일은 지난 것으로 센다. 통상 만나이와 갈리는 유일한 지점이다."""
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


class TestContractExpiry:
    """잔여계약기간은 근무기간의 **상한** 이다.

    정년이 아니라 계약 만료로 나가는 사람이라, 정년도 임금피크도 그 뒤의
    이야기다. 여기서 정년 쪽이 이기면 계약직이 정년까지 일하는 것으로 잡혀
    채무가 몇 배로 부푼다.
    """

    def test_it_sets_the_exit_at_age_plus_the_years(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(40, rule, contract_years=1) == 41
        assert normal_retirement_age(40, rule, contract_years=3) == 43

    def test_it_beats_the_rule_and_the_wage_peak(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(
            50, rule, wage_peak_age=57, declared_nra=63, contract_years=2
        ) == 52

    def test_a_part_year_still_runs_to_the_end_of_that_year(
        self, rule: JobGroupRule
    ) -> None:
        """0.5년 남았어도 그 해에는 일한다. 0 년으로 깎으면 근무기간이 사라진다."""
        assert normal_retirement_age(40, rule, contract_years=0.5) == 41

    def test_empty_means_the_rule_still_decides(self, rule: JobGroupRule) -> None:
        assert normal_retirement_age(40, rule, contract_years=0) == 60

    def test_longterm_ends_with_the_contract_too(self, rule: JobGroupRule) -> None:
        """계약이 끝난 뒤의 근속포상은 받을 수 없다."""
        assert longterm_retirement_age(40, rule, contract_years=2) == 42


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


class TestResidentNumber:
    """주민등록번호 앞 7자리에서 생년월일과 성별을 읽는다.

    여기가 틀리면 연령이 100년 어긋나고, 그 사람은 이미 죽었거나 아직 태어나지
    않은 것으로 잡힌다. 검증에 걸리기는 하지만 그 전에 맞게 읽어야 한다.
    """

    @pytest.mark.parametrize("raw", ["850305-1", "8503051", "850305 1", " 850305-1 "])
    def test_it_reads_however_it_is_typed(self, raw: str) -> None:
        from pension.normalize import from_resident_number

        born, sex = from_resident_number(raw)
        assert born == dt.date(1985, 3, 5)
        assert sex is Gender.MALE

    @pytest.mark.parametrize(
        ("marker", "year", "sex"),
        [
            ("1", 1985, Gender.MALE), ("2", 1985, Gender.FEMALE),
            ("3", 2085, Gender.MALE), ("4", 2085, Gender.FEMALE),
        ],
    )
    def test_the_marker_says_the_century_and_the_sex(
        self, marker: str, year: int, sex: Gender
    ) -> None:
        from pension.normalize import from_resident_number

        born, read = from_resident_number(f"850305-{marker}")
        # 2000년대 표기는 아직 오지 않은 날이라 100년 당겨진다.
        expected = year if year <= dt.date.today().year else year - 100
        assert born is not None and born.year == expected
        assert read is sex

    def test_six_digits_give_the_date_but_not_the_sex(self) -> None:
        """성별 자리가 없으면 성별은 모르는 것이다. 남자로 넘겨짚지 않는다."""
        from pension.normalize import from_resident_number

        born, sex = from_resident_number("850305")
        assert born == dt.date(1985, 3, 5)
        assert sex is None

    @pytest.mark.parametrize("raw", ["", None, "12345", "859905-1", "abc"])
    def test_what_cannot_be_read_is_not_guessed(self, raw: object) -> None:
        from pension.normalize import from_resident_number

        assert from_resident_number(raw) == (None, None)

    def test_it_never_returns_a_future_birth_date(self) -> None:
        """`051231-1` 처럼 세기 자리를 잘못 적어 와도 연령이 음수가 되지 않는다."""
        from pension.normalize import from_resident_number

        born, _sex = from_resident_number("051231-3")
        assert born is not None and born < dt.date.today()


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
        """'임금피크제도에 따른 중간정산' 은 정년으로도 중간정산으로도 읽힌다."""
        raw = "임금피크제도에 따른 중간정산"
        assert normalize_retirement_reason(raw) is RetirementReason.NORMAL
        assert is_ambiguous_reason(raw)
        assert not is_ambiguous_reason("정년퇴직")


def test_yes_no_flag() -> None:
    assert normalize_yes_no("Y") == "Y"
    assert normalize_yes_no("n") == "N"
    assert normalize_yes_no("") == ""
    assert normalize_yes_no("", default="Y") == "Y"
