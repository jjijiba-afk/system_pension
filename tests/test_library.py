"""시스템에 등록해 두고 여러 단체에 재사용하는 자료.

한 결산기에 여러 단체를 산출할 때 금리표는 모두 같다. 단체마다 파일을 다시
찾아 지정하면 그중 한 번만 다른 파일을 집어도 그 단체만 할인율이 달라진다.
"""

from __future__ import annotations

import pytest

from pension.library import (
    CURVE_KIND,
    RATES_KIND,
    entries,
    find_entry,
    library_dir,
    read_settings,
    register,
    remove,
    resolve_default,
    write_settings,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """등록 폴더를 시험용으로 갈아 끼운다. 사용자 폴더를 건드리면 안 된다."""
    monkeypatch.setenv("PENSION_HOME", str(tmp_path / "home"))
    return tmp_path


def _book(path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active["A1"] = "표시용"
    wb.save(path)
    return path


class TestRegister:
    def test_copies_rather_than_links(self, tmp_path) -> None:
        """원본을 참조만 하면 담당자가 파일을 옮겼을 때 조용히 실패한다."""
        source = _book(tmp_path / "원본.xlsx")
        entry = register(CURVE_KIND, source)

        source.unlink()
        assert entry.path.exists()
        assert find_entry(CURVE_KIND, entry.name) is not None

    def test_custom_name(self, tmp_path) -> None:
        entry = register(CURVE_KIND, _book(tmp_path / "x.xlsx"), name="KIS_20251231")
        assert entry.name == "KIS_20251231"
        assert [e.name for e in entries(CURVE_KIND)] == ["KIS_20251231"]

    def test_illegal_characters_are_stripped(self, tmp_path) -> None:
        entry = register(CURVE_KIND, _book(tmp_path / "x.xlsx"), name="2025/12:31")
        assert "/" not in entry.name and ":" not in entry.name
        assert entry.path.exists()

    def test_missing_source_is_reported(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            register(CURVE_KIND, tmp_path / "없는파일.xlsx")

    def test_unknown_kind_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="등록 종류"):
            register("아무거나", _book(tmp_path / "x.xlsx"))

    def test_kinds_are_separate(self, tmp_path) -> None:
        register(CURVE_KIND, _book(tmp_path / "a.xlsx"), name="같은이름")
        register(RATES_KIND, _book(tmp_path / "b.xlsx"), name="같은이름")
        assert len(entries(CURVE_KIND)) == 1
        assert len(entries(RATES_KIND)) == 1

    def test_remove(self, tmp_path) -> None:
        register(CURVE_KIND, _book(tmp_path / "a.xlsx"), name="지울것")
        assert remove(CURVE_KIND, "지울것") is True
        assert remove(CURVE_KIND, "지울것") is False
        assert entries(CURVE_KIND) == []


class TestDefault:
    def test_newest_wins_when_nothing_is_pinned(self, tmp_path) -> None:
        """결산기마다 새로 등록하므로, 지정을 안 해도 최신 것이 잡혀야 한다."""
        import os
        import time

        register(CURVE_KIND, _book(tmp_path / "a.xlsx"), name="2024")
        newer = register(CURVE_KIND, _book(tmp_path / "b.xlsx"), name="2025")
        # 같은 초에 등록되면 순서를 가릴 수 없으므로 시각을 벌린다.
        os.utime(newer.path, (time.time() + 10, time.time() + 10))

        assert resolve_default(CURVE_KIND).name == "2025"

    def test_pinned_choice_wins(self, tmp_path) -> None:
        register(CURVE_KIND, _book(tmp_path / "a.xlsx"), name="2024")
        register(CURVE_KIND, _book(tmp_path / "b.xlsx"), name="2025")
        write_settings({CURVE_KIND: "2024"})
        assert resolve_default(CURVE_KIND).name == "2024"

    def test_pinned_but_deleted_falls_back(self, tmp_path) -> None:
        register(CURVE_KIND, _book(tmp_path / "a.xlsx"), name="2024")
        write_settings({CURVE_KIND: "사라진것"})
        assert resolve_default(CURVE_KIND).name == "2024"

    def test_empty_library(self) -> None:
        assert resolve_default(CURVE_KIND) is None

    def test_broken_settings_file_is_not_fatal(self) -> None:
        (library_dir() / "설정.json").write_text("{망가진", encoding="utf-8")
        assert read_settings() == {}


class TestCurveFromLibrary:
    """등록한 금리표에서 곧바로 할인율 곡선을 뽑을 수 있어야 한다."""

    def test_round_trip(self, tmp_path) -> None:
        import openpyxl

        from pension.yieldcurve import pick_curve, read_yield_curves

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "KIS_NET금리"
        ws.append(["No", "기준일자", "구분", "등급", "1년", "5년", "20년"])
        ws.append([1, None, "공모 무보증회사채", "AA0", 3.122, 3.62, 5.26])
        source = tmp_path / "금리.xlsx"
        wb.save(source)

        entry = register(CURVE_KIND, source, name="KIS_20251231")
        curve = pick_curve(read_yield_curves(entry.path), "AA0")

        assert curve is not None
        assert len(curve.points) == 3
        assert curve.rate_at(5) == pytest.approx(0.0362)
