"""``python -m pension`` 및 EXE 진입점.

인자 없이 실행하면 GUI, 하위 명령을 주면 CLI 로 동작한다.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
