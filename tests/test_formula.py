"""지급률 규정 수식."""

from __future__ import annotations

import time

import pytest

from pension.formula import MAX_EXPONENT, MAX_NODES, Formula, FormulaError


class TestExcelSyntax:
    """계리 담당자가 엑셀에서 쓰던 문법이 그대로 통해야 한다."""

    @pytest.mark.parametrize(
        ("source", "variables", "expected"),
        [
            ("=t", {"t": 12}, 12.0),
            ("t * 1.5", {"t": 10}, 15.0),
            ("=IF(t < 10, t, 10 + (t - 10) * 2)", {"t": 15}, 20.0),
            ("=IF(t < 10, t, 10 + (t - 10) * 2)", {"t": 5}, 5.0),
            ("=MIN(t, 30)", {"t": 40}, 30.0),
            ("=MAX(t, 5)", {"t": 2}, 5.0),
            ("=t^2", {"t": 4}, 16.0),  # ^ 는 거듭제곱 (파이썬 XOR 아님)
            ("=ROUND(t * 1.234, 2)", {"t": 10}, 12.34),
            ("=ROUNDDOWN(t * 1.99, 0)", {"t": 10}, 19.0),
            ("=ROUNDUP(t * 1.01, 0)", {"t": 10}, 11.0),
            ("=ABS(0 - t)", {"t": 7}, 7.0),
            ("=FLOOR(t, 5)", {"t": 12}, 10.0),
            ("=CEILING(t, 5)", {"t": 12}, 15.0),
        ],
    )
    def test_evaluates(self, source, variables, expected) -> None:
        assert Formula(source).evaluate(**variables) == pytest.approx(expected)

    def test_equality_uses_single_equals_like_excel(self) -> None:
        formula = Formula('=IF(제도="DB", t * 2, t)')
        assert formula.evaluate(t=10, 제도="DB") == 20.0
        assert formula.evaluate(t=10, 제도="DC") == 10.0

    def test_not_equal_uses_angle_brackets(self) -> None:
        formula = Formula('=IF(제도<>"DC", t, 0)')
        assert formula.evaluate(t=10, 제도="DB") == 10.0
        assert formula.evaluate(t=10, 제도="DC") == 0.0

    def test_and_or_combine_conditions(self) -> None:
        formula = Formula("=IF(AND(t >= 10, x < 55), t * 2, t)")
        assert formula.evaluate(t=12, x=50) == 24.0
        assert formula.evaluate(t=12, x=57) == 12.0

    def test_retirement_age_proximity_rule(self) -> None:
        """정년까지 3년 미만이면 배수를 깎는 규정 — 표로는 못 담는 형태."""
        formula = Formula("=IF(N - x < 3, t * 0.5, t * 1.5)")
        assert formula.evaluate(t=20, x=50, N=60) == 30.0
        assert formula.evaluate(t=20, x=58, N=60) == 10.0

    def test_round_is_half_up_not_bankers(self) -> None:
        """엑셀 ROUND 는 사사오입. 파이썬 기본 round 는 은행가 반올림이라 다르다."""
        assert Formula("=ROUND(t, 0)").evaluate(t=2.5) == 3.0
        assert Formula("=ROUND(t, 0)").evaluate(t=0.5) == 1.0
        assert round(2.5) == 2  # 파이썬 기본 동작 대조

    def test_missing_variable_defaults_to_zero(self) -> None:
        assert Formula("=t + x").evaluate(t=5) == 5.0

    def test_division_by_zero_yields_zero_not_crash(self) -> None:
        assert Formula("=t / x").evaluate(t=5, x=0) == 0.0

    def test_if_does_not_evaluate_unused_branch(self) -> None:
        """쓰이지 않는 가지에서 나눗셈 오류가 나도 결과에 영향이 없어야 한다."""
        assert Formula("=IF(x > 0, t / x, 0)").evaluate(t=10, x=0) == 0.0


class TestRejection:
    """기초율 파일은 메일로 오간다. 계산식이 아닌 것은 전부 거부한다."""

    @pytest.mark.parametrize(
        "source",
        [
            '__import__("os").system("echo hi")',
            'open("/etc/passwd").read()',
            "(1).__class__.__bases__[0].__subclasses__()",
            "t.__class__",
            "t.real",
            "[x for x in range(10)]",
            "{1: 2}[1]",
            "{1, 2}",
            "lambda: 1",
            'exec("a=1")',
            "globals()",
            'getattr(t, "real")',
            'f"{t}"',
            "MIN(*[1, 2])",
            "t << 2",
            "~t",
            "1 @ 2",
        ],
    )
    def test_rejects_non_arithmetic(self, source) -> None:
        with pytest.raises(FormulaError):
            Formula(source)

    def test_rejects_unknown_variable_with_helpful_message(self) -> None:
        with pytest.raises(FormulaError, match="사용 가능한 변수"):
            Formula("=근속연수 * 2")

    def test_rejects_unknown_function_with_helpful_message(self) -> None:
        with pytest.raises(FormulaError, match="사용 가능"):
            Formula("=VLOOKUP(t, A, 2)")

    def test_rejects_empty(self) -> None:
        with pytest.raises(FormulaError, match="비어 있"):
            Formula("   ")

    def test_rejects_syntax_error(self) -> None:
        with pytest.raises(FormulaError, match="문법"):
            Formula("=IF(t < 5,")

    def test_rejects_non_numeric_result(self) -> None:
        with pytest.raises(FormulaError, match="숫자가 아닙니다"):
            Formula('="문자열"').evaluate()


class TestResourceLimits:
    """파일 한 줄로 산출 프로그램을 멈출 수 없어야 한다."""

    def test_huge_exponent_is_refused_immediately(self) -> None:
        """정수 거듭제곱은 임의 정밀도라 그냥 두면 프로세스가 멈춘다."""
        started = time.perf_counter()
        with pytest.raises(FormulaError, match="지수"):
            Formula("=t ^ 99999999").evaluate(t=5)
        assert time.perf_counter() - started < 1.0

    def test_exponent_at_the_limit_still_works(self) -> None:
        assert Formula(f"=2 ^ {MAX_EXPONENT}").evaluate() == pytest.approx(2.0**MAX_EXPONENT)

    def test_overly_complex_formula_is_refused(self) -> None:
        with pytest.raises(FormulaError, match="너무 복잡"):
            Formula("+".join(["t"] * (MAX_NODES + 10)))


class TestPreview:
    def test_preview_returns_pairs(self) -> None:
        pairs = Formula("=t * 2").preview([1, 5, 10])
        assert pairs == [(1, 2.0), (5, 10.0), (10, 20.0)]

    def test_source_is_kept_verbatim_for_audit(self) -> None:
        source = "=IF(t<10, t*1.0, 10 + (t-10)*2.0)"
        assert str(Formula(source)) == source
