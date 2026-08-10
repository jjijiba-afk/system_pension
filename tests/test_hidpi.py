"""고해상도 화면 대응 — 배율 계산.

윈도우 DPI 선언 자체는 여기서 확인할 수 없다(윈도우가 있어야 한다). 대신
**화면이 몇 배인지 재고, 픽셀로 적은 값을 그만큼 키우는 계산** 을 고정한다.
여기가 틀리면 4K 노트북에서 창이 손톱만 하게 뜨거나 화면 밖으로 나간다.
"""

from __future__ import annotations

import pytest

from pension import hidpi


class FakeScreen:
    """``winfo_fpixels('1i')`` 만 흉내 내는 가짜 위젯."""

    def __init__(self, dpi):
        self._dpi = dpi

    def winfo_fpixels(self, _spec):
        if self._dpi is None:
            raise RuntimeError("화면 없음")
        return self._dpi


class TestScaling:
    @pytest.mark.parametrize(("dpi", "expected"), [
        (96, 1.0),      # 100%
        (120, 1.25),    # 125%
        (144, 1.5),     # 150%
        (192, 2.0),     # 200% — 4K 노트북
    ])
    def test_reads_the_screen(self, dpi, expected) -> None:
        assert hidpi.scaling(FakeScreen(dpi)) == pytest.approx(expected)

    def test_never_shrinks(self) -> None:
        """96 보다 낮게 보고하는 화면이 있다. 줄이면 글자를 못 읽는다."""
        assert hidpi.scaling(FakeScreen(72)) == 1.0

    def test_absurd_scaling_is_capped(self) -> None:
        """배율이 터무니없으면 창이 화면 밖으로 나간다."""
        assert hidpi.scaling(FakeScreen(9600)) == hidpi.MAX_SCALE

    def test_a_screen_it_cannot_read_changes_nothing(self) -> None:
        assert hidpi.scaling(FakeScreen(None)) == 1.0
        assert hidpi.scaling(FakeScreen(0)) == 1.0


class TestPixels:
    def test_scales_lengths(self) -> None:
        assert hidpi.px(FakeScreen(144), 260) == 390
        assert hidpi.px(FakeScreen(96), 260) == 260

    def test_geometry_grows_with_the_screen(self) -> None:
        assert hidpi.scale_geometry(FakeScreen(192), "900x840") == "1800x1680"
        assert hidpi.scale_geometry(FakeScreen(96), "900x840") == "900x840"

    def test_position_is_left_alone(self) -> None:
        """크기는 화면 문제지만, 위치는 사용자가 둔 자리다."""
        assert hidpi.scale_geometry(FakeScreen(144), "900x840+10+20") == "1350x1260+10+20"

    def test_a_geometry_it_cannot_parse_passes_through(self) -> None:
        assert hidpi.scale_geometry(FakeScreen(144), "이상한값") == "이상한값"


class TestDeclare:
    def test_does_nothing_off_windows(self, monkeypatch) -> None:
        """리눅스·맥에서는 건드릴 것이 없다. 죽지만 않으면 된다."""
        monkeypatch.setattr(hidpi.sys, "platform", "linux")
        assert hidpi.declare_dpi_aware() is False
