"""난수로 만드는 시험용 명부 세 가지.

시험 자료가 시험 대상보다 먼저 틀리면 곤란하다. 세 사례가 **의도한 성격대로**
나오는지를 여기서 못박는다 — 표준은 오류 없이 돌고, 복합제도는 특수 경로를
실제로 지나가고, 자료불량은 검증이 잡아낼 거리를 실제로 담고 있어야 한다.
"""

from __future__ import annotations

import pytest

from pension.errors import PensionDataError, Severity
from pension.pipeline import RunOptions, run_valuation
from pension.rostergen import CASES, write_case_pack


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> dict[str, tuple]:
    """세 사례를 한 번만 만들어 나눠 쓴다. 만드는 데 몇 초 걸린다."""
    directory = tmp_path_factory.mktemp("사례")
    write_case_pack(directory)
    return {
        spec.key: (
            directory / f"{spec.title}.xlsx",
            directory / f"{spec.title}_기초율.xlsx",
        )
        for spec in CASES
    }


def _run(pack, key, tmp_path, *, force=False):
    roster, assumptions = pack[key]
    return run_valuation(RunOptions(
        roster_path=roster, assumptions_path=assumptions,
        output_path=tmp_path / "결과.xlsx",
        include_sensitivity=False, include_longterm=True, allow_errors=force,
    ))


class TestStandardCase:
    def test_runs_without_a_single_error(self, pack, tmp_path) -> None:
        """기준선이 되는 사례다. 여기서 오류가 나면 시험 자체가 흔들린다."""
        run = _run(pack, "표준", tmp_path)
        assert run.issues.errors == []
        assert run.issues.warnings == []

    def test_headcount_and_shape(self, pack, tmp_path) -> None:
        run = _run(pack, "표준", tmp_path)
        valuation = run.valuation
        assert valuation.headcount == 290
        assert len(run.roster.retired) == 28
        assert set(valuation.by_job_group()) == {"정규직", "계약직", "임원"}
        # 채무·듀레이션이 상식적인 범위에 있어야 한다.
        assert 5e9 < valuation.dbo < 1e11
        assert 3 < valuation.duration < 15
        assert valuation.service_cost > 0

    def test_longterm_benefit_is_not_empty(self, pack, tmp_path) -> None:
        """장기급여 표가 비면 기타장기종업원급여를 눌러도 볼 것이 없다."""
        run = _run(pack, "표준", tmp_path)
        assert run.longterm is not None
        assert run.longterm.dbo > 0


class TestComplexCase:
    def test_runs_clean_despite_complex_structure(self, pack, tmp_path) -> None:
        """자료는 옳은 사례다 — 구조가 복잡하다고 오류가 나면 안 된다."""
        run = _run(pack, "복합제도", tmp_path)
        assert run.issues.errors == []

    def test_exercises_the_special_paths(self, pack, tmp_path) -> None:
        run = _run(pack, "복합제도", tmp_path)
        active = run.roster.active

        assert any(m.settlement_date for m in active), "중간정산자가 있어야 한다"
        assert any(m.wage_peak_age for m in active), "임금피크 대상이 있어야 한다"
        assert any(m.transfer_in_date for m in active), "전입자가 있어야 한다"
        assert any(m.payout_multiple for m in active), "개별 지급배수가 있어야 한다"
        assert any(m.age > 60 for m in active), "정년 초과 재고용자가 있어야 한다"
        # 임금피크는 정년연령을 실제로 당긴다 — 값이 흘러들어갔는지 본다.
        peak = next(m for m in active if m.wage_peak_age)
        assert peak.severance_nra == peak.wage_peak_age

        excluded = run.valuation.exclusion_summary()
        assert any("DC" in reason for reason in excluded), "DC 가입자가 빠져야 한다"

    def test_raw_job_groups_are_mapped(self, pack, tmp_path) -> None:
        """명부에는 '정사원·촉탁사원' 이 오지만 산출은 묶음 단위로 나와야 한다."""
        run = _run(pack, "복합제도", tmp_path)
        assert set(run.valuation.by_job_group()) <= {"정규직", "계약직", "임원"}

    def test_retirement_reasons_are_varied(self, pack, tmp_path) -> None:
        run = _run(pack, "복합제도", tmp_path)
        reasons = {m.reason for m in run.roster.retired if m.reason}
        assert len(reasons) >= 4, "정년·사망·전출 등이 섞여 있어야 한다"


class TestDirtyCase:
    def test_stops_without_force(self, pack, tmp_path) -> None:
        with pytest.raises(PensionDataError) as caught:
            _run(pack, "자료불량", tmp_path)
        errors = [i for i in caught.value.issues if i.severity is Severity.ERROR]
        assert len(errors) > 30

    def test_catches_the_planted_problems(self, pack, tmp_path) -> None:
        """심어 둔 문제를 검증이 실제로 잡는지 — 종류별로 확인한다."""
        run = _run(pack, "자료불량", tmp_path, force=True)
        found = {issue.code for issue in run.issues.errors}
        for code in (
            "JAE_DUP_ID",            # 사번 중복
            "JAE_PLAN_MISSING",      # 제도구분 누락
            "JAE_WAGE_MISSING",      # 임금 0
            "JAE_WAGE_BELOW_CHECK",  # 체크금액 미만
            "JAE_BIRTH_AFTER_HIRE",  # 생년월일·입사일 역전
            "JAE_HIRE_AFTER_BASE",   # 입사일이 기준일보다 늦음
            "JAE_JOB_GROUP_UNKNOWN", # 규정에 없는 직군
        ):
            assert code in found, f"{code} 를 잡아내지 못했다"

    def test_still_produces_a_valuation_when_forced(self, pack, tmp_path) -> None:
        """강행하면 읽을 수 있는 사람만으로 끝까지 돌아야 한다."""
        run = _run(pack, "자료불량", tmp_path, force=True)
        assert 0 < run.valuation.headcount < 290
        assert run.valuation.dbo > 0
        assert run.valuation.exclusion_summary(), "읽지 못한 사람은 제외 사유가 남아야 한다"

    def test_mixed_date_formats_are_still_parsed(self, pack, tmp_path) -> None:
        """서식이 뒤섞여도 상당수는 읽혀야 한다. 전부 실패하면 파서 문제다."""
        run = _run(pack, "자료불량", tmp_path, force=True)
        parsed = sum(1 for m in run.roster.active if m.birth_date)
        assert parsed > 250


def test_same_seed_gives_the_same_file(tmp_path) -> None:
    """산출 결과를 비교하려면 명부가 매번 같아야 한다."""
    first = write_case_pack(tmp_path / "a")[0]
    second = write_case_pack(tmp_path / "b")[0]
    third = write_case_pack(tmp_path / "c", seed=7)[0]

    import openpyxl

    def rows(path):
        ws = openpyxl.load_workbook(path)["재직자명부"]
        return [
            [ws.cell(r, c).value for c in range(3, 12)]
            for r in range(25, 40)
        ]

    assert rows(first) == rows(second)
    assert rows(first) != rows(third)
