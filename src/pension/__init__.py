"""연금계리 산출 시스템.

K-IFRS 1019호 '종업원급여' 에 따른 확정급여채무(DBO) 산출 시스템이다.
기존 엑셀 VBA 매크로(명부 정규화·검증)의 로직을 이식하고, 그 위에 예측단위적립
방식(PUC) 계리 엔진을 얹었다.

구성
----
======================  ==========================================================
:mod:`~pension.dates`   명부 날짜 문자열 정규화(길이 기반 형식 추론)
:mod:`~pension.config`  ``Input`` 시트의 산출기준 · 직군 규칙
:mod:`~pension.readers` 재직자/퇴직자 명부 읽기
:mod:`~pension.validation` 명부 검증(오류 일괄 수집)
:mod:`~pension.upload`  ``UpLoad_Jae``/``UpLoad_Toi`` 업로드 명부 생성
:mod:`~pension.assumptions` 기초율(할인율·퇴직률·사망률·승급률·지급률)
:mod:`~pension.valuation`   PUC 확정급여채무 산출
:mod:`~pension.longterm`    기타장기종업원급여 산출
:mod:`~pension.sensitivity` 민감도분석
:mod:`~pension.rollforward` 증감분석 · 보험수리적손익 분해
:mod:`~pension.pipeline`    전 과정 실행
:mod:`~pension.report`      엑셀 결과 리포트
======================  ==========================================================

사용 예::

    from pension.pipeline import RunOptions, run_valuation
    from pension.report import write_report

    run = run_valuation(RunOptions(
        roster_path="명부.xlsm",
        assumptions_path="기초율.xlsx",
        output_path="산출결과.xlsx",
    ))
    write_report(run, "산출결과.xlsx")
    print(f"확정급여채무: {run.valuation.dbo:,.0f}원")
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["__version__"]
