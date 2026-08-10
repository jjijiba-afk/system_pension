"""배포물에 무엇이 실려 나가는지.

프로그램을 남에게 건네면 **같이 들어 있는 것도 전부 건네진다.** 개인정보와
설계 메모 둘 다 그렇다. 여기서는 빌드가 그것들을 실제로 걷어내는지 본다.

코드 자체는 가릴 수 없다 — 브라우저가 파이썬을 실행해야 하므로 소스가 함께
간다. 가릴 수 있는 것은 **주석과 독스트링** 이고, 그것이 이 시스템이 무엇을
보고 만들어졌는지 말해 주는 부분이다.
"""

from __future__ import annotations

import ast
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "webapp"))

build = pytest.importorskip("build", reason="webapp/build.py 가 있어야 한다")


class TestStripping:
    """주석·독스트링만 걷어내고 계산은 한 글자도 건드리지 않는다."""

    def _strip(self, source: str) -> str:
        """빌드가 쓰는 것과 같은 변환. 휠을 만들지 않고 함수만 부른다."""
        wheel_source = {}

        def fake_zip(path):
            raise AssertionError("휠을 건드리면 안 된다")

        # _strip_engine_source 안의 지역 함수를 그대로 쓰기 위해, 같은 방식으로
        # 다시 구현하지 않고 모듈이 만든 휠 하나를 통째로 돌려 본다.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "pension_actuarial-0.0.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("pension/샘플.py", source)
                archive.writestr("pension_actuarial-0.0.0.dist-info/METADATA", "x")
            build._strip_engine_source(wheel)
            with zipfile.ZipFile(wheel) as archive:
                wheel_source = archive.read("pension/샘플.py").decode("utf-8")
        return wheel_source

    def test_docstrings_and_comments_are_gone(self) -> None:
        stripped = self._strip(
            '"""모듈 설명 — 종전 규칙를 옮긴 것이다."""\n'
            "\n"
            "LIMIT = 25\n"
            '"""원본 배열이 1 To 25 로 선언되어 있다."""\n'
            "\n"
            "\n"
            "def add(a, b):\n"
            '    """더한다. 원본 시트 C3 을 읽던 자리다."""\n'
            "    # 원본은 여기서 메시지 를 띄웠다\n"
            "    return a + b\n"
        )
        assert "원본" not in stripped
        assert "메시지" not in stripped
        assert '"""' not in stripped
        assert "#" not in stripped

    def test_the_code_still_does_the_same_thing(self) -> None:
        """설명을 지우다 계산을 건드리면 채무가 틀린다."""
        source = (
            '"""설명."""\n'
            "def multiple(years):\n"
            '    """지급배수."""\n'
            "    if years < 5:\n"
            "        return years * 1.0\n"
            "    return 5 + (years - 5) * 1.5\n"
        )
        original, stripped = {}, {}
        exec(compile(source, "<원본>", "exec"), original)
        exec(compile(self._strip(source), "<정리본>", "exec"), stripped)
        for years in (0, 3, 5, 12, 30.5):
            assert stripped["multiple"](years) == original["multiple"](years)

    def test_a_body_that_was_only_a_docstring_still_parses(self) -> None:
        """본문이 설명뿐인 함수를 비워 버리면 문법 오류가 난다."""
        stripped = self._strip('def 나중에():\n    """아직 안 만들었다."""\n')
        ast.parse(stripped)
        assert "pass" in stripped

    def test_data_files_do_not_ride_along(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "pension_actuarial-0.0.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("pension/data/명부.csv", "사번,생년월일\n1,1969-02-23\n")
                archive.writestr("pension/메모.md", "종전 규칙 대응표")
                archive.writestr("pension/engine.py", "X = 1\n")
            build._strip_engine_source(wheel)
            with zipfile.ZipFile(wheel) as archive:
                assert archive.namelist() == ["pension/engine.py"]


class TestPackagingLeavesNoTrail:
    """꾸러미 설정이 출처를 실어 나르지 않는지."""

    def test_metadata_carries_no_readme(self) -> None:
        """휠 METADATA 는 README 본문을 통째로 담는다. 개발용 문서다."""
        toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "readme" not in toml
        assert "종전 규칙" not in toml

    def test_the_exe_drops_docstrings(self) -> None:
        spec = (ROOT / "pension.spec").read_text(encoding="utf-8")
        assert "optimize=2" in spec

    def test_only_user_facing_docs_are_shipped(self) -> None:
        """개발 메모는 종전 규칙의 파일명과 구조가 적힌 것이다."""
        flow = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
        copied = "".join(
            line for line in flow.splitlines() if "Copy-Item" in line or "docs/" in line)
        assert "docs/*.md" not in copied
        assert "docs/vba-mapping.md" not in copied
        for name in ("사용설명서", "계리방법론", "지급률규정-작성법"):
            assert f"docs/{name}.md" in copied


@pytest.mark.skipif(not (ROOT / "webapp/dist").exists(),
                    reason="webapp/build.py 를 먼저 실행")
class TestTheBuiltWheel:
    """실제로 빌드된 것을 열어 본다."""

    def _wheel(self):
        return next((ROOT / "webapp/dist/wheels").glob("pension_actuarial-*.whl"))

    def test_no_personal_data(self) -> None:
        with zipfile.ZipFile(self._wheel()) as archive:
            assert not [n for n in archive.namelist() if n.endswith(".csv")]

    def test_no_design_notes(self) -> None:
        """독스트링은 구문 트리로 본다. 삼중따옴표는 HTML 조각에도 쓰인다."""
        with zipfile.ZipFile(self._wheel()) as archive:
            for name in archive.namelist():
                if not name.endswith(".py"):
                    continue
                source = archive.read(name).decode("utf-8")
                assert "종전 규칙" not in source, name
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                        assert ast.get_docstring(node) is None, f"{name}: {node}"
