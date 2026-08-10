"""고해상도 화면에서 창이 흐릿하지 않게.

윈도우는 "나는 DPI 를 모른다" 고 선언한 프로그램의 창을 **다 그린 다음 비트맵째
확대한다.** 배율 150~200% 로 쓰는 요즘 노트북에서 글자와 선이 뭉개져 보이는
것이 그래서다. 늘리기 전에 "내가 알아서 그리겠다" 고 말해 두면 처음부터 원래
해상도로 그려진다.

**선언은 첫 창을 만들기 전에 해야 한다.** Tk 가 창을 띄운 뒤에는 이미 늘리기
모드가 정해져 있어 아무 효과가 없다. 그래서 :func:`declare_dpi_aware` 는
``tkinter.Tk()`` 보다 먼저 불러야 하고, :func:`apply` 는 그 뒤에 부른다.

선언하고 나면 이번에는 **좌표가 진짜 픽셀** 이 된다. 배율 200% 화면에서
``900x840`` 짜리 창은 종전의 절반 크기로 보인다. 그래서 픽셀로 적은 값
(창 크기·캔버스 높이·줄바꿈 폭)은 :func:`px` 로 함께 키운다. 글꼴은 포인트
단위라 ``tk scaling`` 하나로 따라온다.

이 모듈은 화면이 없거나 윈도우가 아니면 **아무것도 하지 않는다.** 실패해도
프로그램이 죽지 않아야 한다 — 흐린 창은 불편이지만, 안 뜨는 창은 못 쓰는
프로그램이다.
"""

from __future__ import annotations

import sys

__all__ = ["apply", "declare_dpi_aware", "px", "scale_geometry", "scaling"]

#: 기준 해상도. 이 값이면 배율 100% 라 아무것도 키우지 않는다.
BASE_DPI = 96.0

#: 글꼴 크기의 단위. 1포인트 = 1/72인치.
POINT_DPI = 72.0

#: 너무 큰 배율은 오히려 화면 밖으로 밀어낸다. 4K 노트북이 보통 2.0 이다.
MAX_SCALE = 3.0


def declare_dpi_aware() -> bool:
    """윈도우에 "화면 배율은 내가 처리한다" 고 알린다.

    **첫 창을 만들기 전에** 불러야 한다. 윈도우가 아니거나 실패하면 조용히
    ``False`` 를 돌려준다 — 흐릴 뿐 못 쓰지는 않는다.
    """
    if sys.platform != "win32":
        return False

    import ctypes

    # 모니터마다 배율이 다를 수 있다(노트북 + 외부 모니터). 창을 옮길 때마다
    # 따라가는 PER_MONITOR_AWARE 가 가장 낫고, 구형 윈도우면 한 단계 낮춘다.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def scaling(root) -> float:
    """이 화면이 기준(96dpi)의 몇 배인지. 배율 150% 면 1.5.

    화면을 못 읽으면 1.0 — 키우지도 줄이지도 않는다.
    """
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        return 1.0
    if not dpi or dpi <= 0:
        return 1.0
    return min(max(dpi / BASE_DPI, 1.0), MAX_SCALE)


def px(root, pixels: float) -> int:
    """픽셀로 적은 길이를 이 화면 배율에 맞춘다."""
    return int(round(pixels * scaling(root)))


def scale_geometry(root, geometry: str) -> str:
    """``"900x840"`` 같은 창 크기를 배율에 맞춰 키운다.

    ``+x+y`` 로 위치가 붙어 있으면 위치는 건드리지 않는다 — 크기만 화면
    해상도의 문제이고, 위치는 사용자가 둔 자리다.
    """
    factor = scaling(root)
    if factor == 1.0:
        return geometry

    size, _, where = geometry.partition("+")
    wide, _, high = size.partition("x")
    try:
        scaled = f"{int(round(int(wide) * factor))}x{int(round(int(high) * factor))}"
    except ValueError:
        return geometry
    return f"{scaled}+{where}" if where else scaled


def apply(root) -> float:
    """창 하나에 배율을 먹인다. 쓴 배율을 돌려준다.

    글꼴은 포인트 단위라 ``tk scaling`` 만 맞추면 따라온다. 픽셀로 적은
    값들은 :func:`px` 로 각자 키워야 한다.
    """
    factor = scaling(root)
    try:
        root.tk.call("tk", "scaling", factor * BASE_DPI / POINT_DPI)
    except Exception:
        return 1.0
    return factor
