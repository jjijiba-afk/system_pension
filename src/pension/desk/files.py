"""파일을 이 컴퓨터의 탐색기·기본 프로그램으로 넘기는 자리.

플랫폼마다 다르고, 어느 탭에서나 쓴다. 한군데 모아 두지 않으면 탭마다
``sys.platform`` 을 다시 보게 된다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = ["open_with_default", "print_file", "reveal"]


def _hand_over(target: Path) -> bool:
    """탐색기·기본 프로그램에 넘긴다. 넘길 것이 없으면 거짓.

    넘길 프로그램이 없다고 해서 하던 일까지 실패하면 안 된다. 파일은 이미
    만들어졌고, 열어 보여 주는 것은 곁다리다.
    """
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(target))
        except OSError:
            return False
        return True

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, str(target)])
    except OSError:
        return False
    return True


def reveal(path: Path | str) -> bool:
    """폴더를 연다. 파일을 주면 그 파일이 든 폴더."""
    target = Path(path)
    if target.is_file():
        target = target.parent
    if not target.exists():
        return False
    return _hand_over(target)


def open_with_default(path: Path | str) -> bool:
    """이 컴퓨터가 그 확장자에 물려 둔 프로그램으로 연다."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    return _hand_over(target)


def print_file(path: Path | str) -> bool:
    """인쇄로 넘긴다. 넘길 방법이 없으면 거짓 — 부르는 쪽이 대신 안내한다.

    인쇄는 운영체제가 하는 일이다. 프로그램이 종이에 직접 그릴 수는 없으므로
    윈도우에서는 등록된 인쇄 동사를, 그 밖에서는 기본 프로그램을 쓴다.
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(target), "print")
        except OSError:
            return False
        return True
    return False
