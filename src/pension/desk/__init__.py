"""본 화면(PC).

``pension.desk.main()`` 이 창을 띄운다. 산출·산출가정·분석·보고서·사번 조회·
산출 내역·자료실이 한 창 안에 탭으로 들어 있고, 계산은 모두 이 프로그램 안에서
돈다 — 명부가 이 컴퓨터 밖으로 나가지 않는다.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    """본 화면을 띄운다. tkinter 가 없으면 그 사실을 알린다."""
    try:
        from .app import main as run
    except ImportError as exc:  # pragma: no cover - tkinter 없는 파이썬
        raise SystemExit(
            "이 화면은 tkinter 가 필요합니다. Windows 공식 파이썬에는 기본 포함되어 "
            "있고, 리눅스에서는 python3-tk 패키지를 설치하세요."
        ) from exc
    return run()
