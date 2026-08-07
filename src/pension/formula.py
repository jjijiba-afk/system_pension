"""지급률 규정 수식.

퇴직급여 규정 대부분은 근속 구간별 배수표로 표현되지만(``방식 = 누적/누진``),
"정년까지 3년 미만이면 배수를 깎는다" 같은 조건부 규정은 표로 담기 어렵다.
그런 규정을 위한 **엑셀 문법의 작은 수식 언어** 다.

    =IF(t < 10, t * 1.0, 10 + (t - 10) * 2.0)

문법은 엑셀 수식과 같게 두었다. 계리 담당자가 이미 쓰는 문법이라 따로 배울 것이
없고, 엑셀에 그대로 붙여 넣어 검산할 수 있기 때문이다.

**왜 종전 규칙·파이썬 코드를 그대로 받지 않는가**

명부 파일에 적힌 코드를 실행하는 방식은 세 가지 이유로 쓰지 않았다.

1. 실행 파일에는 종전 규칙 런타임이 없다. 엑셀 없이 도는 프로그램에서 종전 규칙 는 애초에
   동작하지 않는다.
2. 임의 코드 실행은 파일 하나로 시스템 전체를 장악할 수 있는 통로가 된다.
   기초율 파일은 여러 사람 손을 거쳐 메일로 오간다.
3. 감사 대응이 어렵다. "이 채무가 왜 이 값인가" 를 설명하려면 규정이 **선언적**
   이어야 한다. 임의 코드는 부작용·전역상태·무한루프가 가능해 재현성을 보장할 수
   없다.

그래서 이 수식은 **부작용이 없는 순수 계산식만** 허용한다. 변수 할당, 반복문,
속성 접근, 함수 정의, 임포트가 모두 문법 단계에서 거부된다. 파서는 파이썬
``ast`` 로 구문만 빌려 쓰고 평가는 직접 하며, ``eval``/``exec`` 는 쓰지 않는다.

사용 가능한 변수
    ``t``   근속연수(중간정산 반영, 소수 가능)
    ``x``   산출 시점 연령
    ``N``   정년연령
    ``S``   30일 평균임금
    ``직군`` 변환 직군명(문자열)
    ``제도`` ``"DB"`` / ``"DC"`` / ``"퇴직금제도"``
    ``임직원`` ``"임원"`` / ``"직원"``
    ``배수`` 명부의 퇴직금 지급배수(비어 있으면 1)

사용 가능한 함수
    ``IF`` ``AND`` ``OR`` ``NOT`` ``MIN`` ``MAX`` ``ABS``
    ``ROUND`` ``ROUNDDOWN`` ``ROUNDUP`` ``FLOOR`` ``CEILING`` ``TRUNC``
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = ["FUNCTIONS", "VARIABLES", "Formula", "FormulaError"]


class FormulaError(ValueError):
    """수식을 해석하거나 계산할 수 없을 때."""

    def __init__(self, message: str, source: str = "") -> None:
        self.source = source
        super().__init__(f"{message} — 수식: {source}" if source else message)


VARIABLES: Final[dict[str, str]] = {
    "t": "근속연수 (중간정산 반영)",
    "x": "연령 (산출 시점)",
    "N": "정년연령",
    "S": "30일 평균임금",
    "직군": "변환 직군명",
    "제도": "퇴직급여 제도구분 (DB / DC / 퇴직금제도)",
    "임직원": "임직원구분 (임원 / 직원)",
    "배수": "명부의 퇴직금 지급배수 (비어 있으면 1)",
}

#: 값이 문자열인 변수. 나머지는 숫자로 본다.
_TEXT_VARIABLES: Final[frozenset[str]] = frozenset({"직군", "제도", "임직원"})

#: 엑셀 이름 → 구현. 인자를 모두 계산한 뒤 호출하는 일반 함수들.
_EAGER: Final[dict[str, Any]] = {
    "MIN": lambda *a: min(a),
    "MAX": lambda *a: max(a),
    "ABS": abs,
    "ROUND": lambda v, n=0: _round_half_up(v, int(n)),
    "ROUNDDOWN": lambda v, n=0: _truncate(v, int(n)),
    "ROUNDUP": lambda v, n=0: _round_away(v, int(n)),
    "TRUNC": lambda v, n=0: _truncate(v, int(n)),
    "FLOOR": lambda v, step=1.0: math.floor(v / step) * step if step else 0.0,
    "CEILING": lambda v, step=1.0: math.ceil(v / step) * step if step else 0.0,
    "NOT": lambda v: not _truthy(v),
}

#: 인자를 늦게 계산해야 하는 함수. ``IF`` 는 선택되지 않은 가지를 계산하지 않는다.
_LAZY: Final[frozenset[str]] = frozenset({"IF", "AND", "OR"})

FUNCTIONS: Final[frozenset[str]] = frozenset(_EAGER) | _LAZY


def _round_half_up(value: float, digits: int = 0) -> float:
    """엑셀 ``ROUND``. 파이썬 기본 반올림은 은행가 반올림이라 결과가 다르다."""
    factor = 10.0**digits
    scaled = value * factor
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / factor


def _truncate(value: float, digits: int = 0) -> float:
    factor = 10.0**digits
    return math.trunc(value * factor) / factor


def _round_away(value: float, digits: int = 0) -> float:
    factor = 10.0**digits
    scaled = value * factor
    rounded = math.ceil(scaled) if scaled >= 0 else math.floor(scaled)
    return rounded / factor


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value != ""
    return bool(value)


# ────────────────────────────────────────────────────────────────
# 엑셀 표기 → 파이썬 구문
# ────────────────────────────────────────────────────────────────

#: ``<>`` 는 파이썬에 없으므로 먼저 바꾼다.
_NOT_EQUAL = re.compile(r"<>")

#: 비교의 ``=`` 를 ``==`` 로. ``<=`` ``>=`` ``==`` ``!=`` 는 건드리지 않는다.
_SINGLE_EQUAL = re.compile(r"(?<![<>=!])=(?!=)")

#: 거듭제곱 ``^``. 파이썬에서는 XOR 이라 의미가 달라진다.
_CARET = re.compile(r"\^")


def _to_python(source: str) -> str:
    """엑셀 수식 표기를 파이썬 식 구문으로 옮긴다(의미는 그대로)."""
    text = source.strip()
    if text.startswith("="):
        text = text[1:]
    # 문자열 리터럴 안의 기호는 건드리면 안 되므로 따옴표 구간을 분리해 처리한다.
    parts = re.split(r'("[^"]*")', text)
    for index, part in enumerate(parts):
        if part.startswith('"'):
            continue
        part = _NOT_EQUAL.sub("!=", part)
        part = _SINGLE_EQUAL.sub("==", part)
        part = _CARET.sub("**", part)
        parts[index] = part
    return "".join(parts)


# ────────────────────────────────────────────────────────────────
# 평가
# ────────────────────────────────────────────────────────────────

MAX_EXPONENT: Final = 64
"""거듭제곱 지수 상한.

파이썬 정수 거듭제곱은 임의 정밀도라 ``5 ** 99999999`` 같은 식이 메모리와 시간을
모두 삼켜 프로그램을 멈춘다. 기초율 파일은 외부에서 받아 오는 것이므로, 실수로든
고의로든 그런 식이 들어오면 산출이 끝나지 않는다. 지급률 계산에 큰 지수가 필요할
일은 없으니 상한을 두고 부동소수점으로 계산한다.
"""

MAX_NODES: Final = 500
"""수식 하나가 가질 수 있는 최대 구문 요소 수. 과도한 중첩으로 인한 재귀 폭주 방지."""


def _power(base: float, exponent: float) -> float:
    if abs(exponent) > MAX_EXPONENT:
        raise FormulaError(f"거듭제곱 지수는 ±{MAX_EXPONENT} 이내여야 합니다 (입력: {exponent})")
    try:
        return float(base) ** float(exponent)
    except OverflowError:
        return math.inf


_ALLOWED_BINOPS: Final = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b if b else 0.0,
    ast.Pow: _power,
    ast.Mod: lambda a, b: a % b if b else 0.0,
    ast.FloorDiv: lambda a, b: a // b if b else 0.0,
}

_ALLOWED_COMPARE: Final = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


@dataclass(slots=True)
class Formula:
    """검증을 마친 지급률 수식.

    생성 시점에 구문을 검사하므로, 객체가 만들어졌다면 계산 중 구문 오류는 나지
    않는다. 담당자가 입력한 즉시 오류를 알려 줄 수 있다.
    """

    source: str
    """담당자가 입력한 원문. 리포트와 감사 추적에 그대로 남긴다."""

    _tree: ast.expr = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.source or not self.source.strip():
            raise FormulaError("수식이 비어 있습니다")
        try:
            parsed = ast.parse(_to_python(self.source), mode="eval")
        except SyntaxError as exc:
            raise FormulaError(f"수식 문법이 잘못되었습니다 ({exc.msg})", self.source) from exc
        self._tree = parsed.body

        node_count = sum(1 for _ in ast.walk(self._tree))
        if node_count > MAX_NODES:
            raise FormulaError(
                f"수식이 너무 복잡합니다 (구문 요소 {node_count}개, 상한 {MAX_NODES}개). "
                "구간표(누진 방식)로 나누어 입력하세요",
                self.source,
            )
        self._check(self._tree)

    # ── 구문 검사 ────────────────────────────────────────────────
    def _check(self, node: ast.AST) -> None:
        """허용하지 않은 구문이 있으면 즉시 거부한다."""
        if isinstance(node, ast.Name):
            if node.id not in VARIABLES and node.id not in FUNCTIONS:
                known = ", ".join(sorted(VARIABLES))
                raise FormulaError(
                    f"알 수 없는 이름 '{node.id}' 입니다 (사용 가능한 변수: {known})",
                    self.source,
                )
            return
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool)):
                raise FormulaError("숫자와 문자열만 쓸 수 있습니다", self.source)
            return
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                name = getattr(node.func, "id", "?")
                raise FormulaError(
                    f"사용할 수 없는 함수 '{name}' 입니다 "
                    f"(사용 가능: {', '.join(sorted(FUNCTIONS))})",
                    self.source,
                )
            if node.keywords:
                raise FormulaError("함수에 이름 붙은 인자는 쓸 수 없습니다", self.source)
            for arg in node.args:
                self._check(arg)
            return
        if isinstance(node, ast.BinOp):
            if type(node.op) not in _ALLOWED_BINOPS:
                raise FormulaError("사용할 수 없는 연산자입니다", self.source)
            self._check(node.left)
            self._check(node.right)
            return
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
                raise FormulaError("사용할 수 없는 연산자입니다", self.source)
            self._check(node.operand)
            return
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if type(op) not in _ALLOWED_COMPARE:
                    raise FormulaError("사용할 수 없는 비교입니다", self.source)
            self._check(node.left)
            for comparator in node.comparators:
                self._check(comparator)
            return
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                self._check(value)
            return
        if isinstance(node, ast.IfExp):
            self._check(node.test)
            self._check(node.body)
            self._check(node.orelse)
            return

        raise FormulaError(
            f"수식에 쓸 수 없는 구문입니다 ({type(node).__name__}). "
            "계산식만 입력하세요",
            self.source,
        )

    # ── 계산 ─────────────────────────────────────────────────────
    def evaluate(self, **variables: Any) -> float:
        """수식을 계산해 숫자로 돌려준다.

        :param variables: :data:`VARIABLES` 의 이름들. 주지 않은 변수는 0(문자열
            변수는 빈 문자열)으로 본다.
        :raises FormulaError: 계산 중 오류가 났을 때.
        """
        env = {
            name: ("" if name in _TEXT_VARIABLES else 0.0)
            for name in VARIABLES
        }
        env.update({k: v for k, v in variables.items() if k in VARIABLES})
        try:
            value = self._eval(self._tree, env)
        except FormulaError:
            raise
        except Exception as exc:  # 0 나누기 외의 예기치 못한 계산 오류
            raise FormulaError(f"수식 계산에 실패했습니다 ({exc})", self.source) from exc

        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        raise FormulaError(f"수식 결과가 숫자가 아닙니다 ({value!r})", self.source)

    def _eval(self, node: ast.AST, env: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env.get(node.id, 0.0)
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, env)
            right = self._eval(node.right, env)
            return _ALLOWED_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            operand = self._eval(node.operand, env)
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            return not _truthy(operand)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            for op, right_node in zip(node.ops, node.comparators, strict=True):
                right = self._eval(right_node, env)
                if not _ALLOWED_COMPARE[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            results = [self._eval(v, env) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(_truthy(r) for r in results)
            return any(_truthy(r) for r in results)
        if isinstance(node, ast.IfExp):
            test = self._eval(node.test, env)
            return self._eval(node.body if _truthy(test) else node.orelse, env)
        if isinstance(node, ast.Call):
            return self._call(node, env)

        raise FormulaError(f"계산할 수 없는 구문입니다 ({type(node).__name__})", self.source)

    def _call(self, node: ast.Call, env: dict[str, Any]) -> Any:
        name = node.func.id  # type: ignore[union-attr]

        if name == "IF":
            if len(node.args) not in (2, 3):
                raise FormulaError("IF 는 인자가 2개 또는 3개여야 합니다", self.source)
            test = self._eval(node.args[0], env)
            if _truthy(test):
                return self._eval(node.args[1], env)
            return self._eval(node.args[2], env) if len(node.args) == 3 else 0.0
        if name == "AND":
            return all(_truthy(self._eval(arg, env)) for arg in node.args)
        if name == "OR":
            return any(_truthy(self._eval(arg, env)) for arg in node.args)

        args = [self._eval(arg, env) for arg in node.args]
        if not args:
            raise FormulaError(f"{name} 에 인자가 없습니다", self.source)
        try:
            return _EAGER[name](*args)
        except TypeError as exc:
            raise FormulaError(f"{name} 의 인자 개수가 맞지 않습니다", self.source) from exc

    def preview(self, services: list[float], **variables: Any) -> list[tuple[float, float]]:
        """근속연수별 계산 결과. 입력 즉시 확인용 미리보기에 쓴다."""
        return [(t, self.evaluate(t=t, **variables)) for t in services]

    def __str__(self) -> str:
        return self.source
