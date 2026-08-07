"""``python -m pension`` 및 EXE 진입점.

인자 없이 실행하면 GUI, 하위 명령을 주면 CLI 로 동작한다.

임포트를 상대 경로(``from .cli import``)가 아니라 절대 경로로 쓴 이유가 있다.
PyInstaller 는 이 파일을 패키지 맥락 없이 최상위 스크립트로 실행하므로, 상대
임포트를 쓰면 ``ImportError: attempted relative import with no known parent
package`` 로 죽는다. 절대 임포트는 ``python -m pension`` 과 EXE 양쪽에서 모두
동작한다.
"""

from __future__ import annotations

import sys

from pension.cli import main

if __name__ == "__main__":
    sys.exit(main())
