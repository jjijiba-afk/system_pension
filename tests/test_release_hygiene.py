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


class TestReleaseCheckScript:
    """빌드가 부르는 개인정보 확인 스크립트.

    윈도우 잡에서만 도는 검사라 손으로 확인하기 어렵다. 검사 자체는 평범한
    파이썬이므로 여기서 못 박아 둔다 — **잡아야 할 것을 못 잡으면 검사가
    있으나 마나** 다.
    """

    def _check(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_release", ROOT / "tools" / "check_release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_clean_package_passes(self, tmp_path) -> None:
        (tmp_path / "연금계리산출.exe").write_bytes(b"MZ" + b"\0" * 100)
        (tmp_path / "기본자료").mkdir()
        (tmp_path / "기본자료" / "명부_기본.xlsx").write_bytes(b"PK\x03\x04")
        assert self._check().problems(tmp_path) == []

    def test_a_stray_csv_is_caught(self, tmp_path) -> None:
        (tmp_path / "기본자료").mkdir()
        (tmp_path / "기본자료" / "명부.csv").write_text("사번,생년월일\n", encoding="utf-8")
        found = self._check().problems(tmp_path)
        assert len(found) == 1 and "명부.csv" in found[0]

    def test_raw_data_inside_the_exe_is_caught(self, tmp_path) -> None:
        """원자료를 다시 실어 나르면 실행 파일 안에 파일명이 남는다."""
        module = self._check()
        (tmp_path / "연금계리산출.exe").write_bytes(
            b"MZ" + b"\0" * 50 + module.ROSTER_MARK + "재직자.csv".encode())
        found = module.problems(tmp_path)
        assert len(found) == 1 and "명부 원자료" in found[0]

    def test_a_missing_folder_is_not_silently_a_pass(self, tmp_path) -> None:
        """폴더 이름이 바뀌었는데 조용히 통과하면 검사가 사라진 줄도 모른다."""
        assert self._check().main([str(tmp_path / "없는폴더")]) == 2

    def test_exit_codes(self, tmp_path) -> None:
        module = self._check()
        (tmp_path / "연금계리산출.exe").write_bytes(b"MZ")
        assert module.main([str(tmp_path)]) == 0
        (tmp_path / "명부.csv").write_text("x", encoding="utf-8")
        assert module.main([str(tmp_path)]) == 1


class TestSelfContainedExe:
    """전체 기능 화면이 실행 파일 **안** 에 들어가야 한다.

    옆 폴더에 두면, 실행 파일만 바탕화면에 복사한 순간 그 화면이 안 열린다.
    쓰는 사람은 그것이 왜인지 알 길이 없다 — 구조로 막는다.
    """

    def test_the_spec_bundles_the_webapp(self) -> None:
        spec = (ROOT / "pension.spec").read_text(encoding="utf-8")
        assert '("webapp/dist", "webapp")' in spec

    def test_the_webapp_is_built_before_the_exe(self) -> None:
        """PyInstaller 가 묶을 때 이미 있어야 한다. 순서가 뒤집히면 빈 채로 나간다."""
        flow = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
        assert flow.index("webapp/build.py") < flow.index("PyInstaller pension.spec")

    def test_the_package_no_longer_carries_a_loose_copy(self) -> None:
        """EXE 안에 있는데 옆에도 두면 어느 쪽이 쓰이는지 알 수 없다."""
        flow = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
        assert "Copy-Item -Recurse webapp/dist" not in flow

    def test_the_build_verifies_it_can_find_the_webapp(self) -> None:
        """묶이지 않았으면 사용자가 버튼을 눌러야 알게 된다. 빌드가 먼저 본다."""
        flow = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
        assert "app --no-browser --check" in flow

    def test_meipass_is_searched(self) -> None:
        """PyInstaller 는 묶은 자료를 _MEIPASS 아래에 푼다."""
        from pension import localapp

        source = (ROOT / "src/pension/localapp.py").read_text(encoding="utf-8")
        assert "_MEIPASS" in source
        assert "webapp" in localapp._FOLDER_NAMES


class TestInstaller:
    """설치 프로그램 — 회사 PC 에서 실제로 설치될 수 있어야 한다."""

    def _script(self) -> str:
        return (ROOT / "packaging/설치.iss").read_text(encoding="utf-8")

    def test_needs_no_admin_rights(self) -> None:
        """회사 PC 는 관리자 권한이 막혀 있는 경우가 흔하다."""
        assert "PrivilegesRequired=lowest" in self._script()

    def test_user_data_survives_uninstall(self) -> None:
        """프로그램을 지웠다고 산출 내역까지 없애면 안 된다."""
        script = self._script()
        assert "{userappdata}" not in script
        assert "연금계리산출\"" not in script.split("[UninstallDelete]")[1]

    def test_makes_a_start_menu_entry_and_uninstaller(self) -> None:
        script = self._script()
        assert "[Icons]" in script and "{uninstallexe}" in script

    def test_the_workflow_builds_and_ships_it(self) -> None:
        flow = (ROOT / ".github/workflows/build-exe.yml").read_text(encoding="utf-8")
        assert "packaging\\설치.iss" in flow
        assert "연금계리산출_설치.exe" in flow
