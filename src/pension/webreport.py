"""K-IFRS 1019 평가보고서 — 인쇄해 PDF 로 저장하는 HTML.

계리법인이 회사에 주는 평가보고서 서식을 따른다: 표지 → 인사말 → 목차 →
평가개요 → 평가 결과 요약 → 공시사항 → 민감도 분석 → 주석 사항 → 첨부
자료(기초율 표 전체) → 용어 정리 → 지급률표. 퇴직급여와 장기종업원급여는
별도 보고서다(장기급여는 문단 153~158 의 간이 공시를 따른다).

PDF 를 파이썬으로 직접 쓰려면 한글 폰트 몇 MB 를 실어야 한다. 대신 인쇄용
HTML 을 만들고 브라우저의 인쇄(→ PDF 저장)를 쓴다 — 용량 0, 한글 깨짐 0,
아이패드 파일 앱에 바로 떨어진다. 외부 리소스는 하나도 참조하지 않으므로
오프라인·사내망에서도 그대로 열린다.

숫자는 산출 결과(:class:`pension.pipeline.PensionRun`)에서만 나온다 — 이
모듈은 **계산하지 않고 배치만** 한다. 보고서와 화면 요약이 다른 값을 보이는
사고를 막기 위해서다.
"""

from __future__ import annotations

import datetime as _dt
import html
from collections.abc import Iterable
from typing import Any

__all__ = ["REPORT_KINDS", "maturity_buckets", "render_html"]

REPORT_KINDS = ("severance", "longterm")

_STANDARD = "K-IFRS 제1019호"


# ── 서식 도우미 ──────────────────────────────────────────────────

def _won(value: float | None, blank: str = "0") -> str:
    """원 단위 표기. 차감 항목은 사람들이 익숙한 괄호로 싼다."""
    if value is None:
        return "—"
    if not value:
        return blank
    if value < 0:
        return f"({abs(value):,.0f})"
    return f"{value:,.0f}"


def _pct(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}%}"


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _table(headers: Iterable[Any], rows: Iterable[Iterable[Any]],
           *, cls: str = "t", unit: str = "(단위 : 원)") -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = [
            # 첫 칸은 항목명(왼쪽 정렬), 나머지는 숫자(오른쪽 정렬)로 본다.
            # 항목명의 들여쓰기( &nbsp; )만 이스케이프에서 되살린다.
            (f"<td>{_esc(cell).replace('&amp;nbsp;', '&nbsp;')}</td>" if i == 0
             else f'<td class="n">{_esc(cell)}</td>')
            for i, cell in enumerate(row)
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    note = f'<div class="unit">{_esc(unit)}</div>' if unit else ""
    return (f'{note}<table class="{cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _kv_table(rows: list[tuple[str, Any]], **kw) -> str:
    return _table(["구 분", "금액"], rows, **kw)


def maturity_buckets(flows: dict[float, float]) -> list[tuple[str, float]]:
    """만기분석 구간(문단 147(c)). 1년 단위로 15년, 이후 15~20년 · 20년 이상."""
    buckets: list[tuple[str, float]] = []
    for k in range(15):
        label = "1년미만" if k == 0 else f"{k}년이상~{k + 1}년미만"
        buckets.append((label, sum(v for t, v in flows.items() if k <= t < k + 1)))
    buckets.append(("15년이상~20년미만",
                    sum(v for t, v in flows.items() if 15 <= t < 20)))
    buckets.append(("20년이상", sum(v for t, v in flows.items() if t >= 20)))
    return buckets


def _curve_rows(curves: dict[str, Any], fmt=lambda v: f"{v:.4%}") -> tuple[list, list]:
    """규정명별 곡선 묶음 → (머리글, 줄들). 값이 없는 규정은 뺀다."""
    names = [n for n, c in curves.items() if c and getattr(c, "points", None)]
    keys = sorted({k for n in names for k in curves[n].points})
    rows = [[key] + [fmt(curves[n].points[key]) if key in curves[n].points else ""
                     for n in names] for key in keys]
    return names, rows


# ── 본문 조립 ────────────────────────────────────────────────────

def render_html(
    run: Any,
    *,
    kind: str = "severance",
    client: str = "",
    period_start: _dt.date | None = None,
) -> str:
    """평가보고서 한 부를 통짜 HTML 로 만든다.

    :param kind: ``severance`` 퇴직급여 / ``longterm`` 장기종업원급여.
    :param client: 표지에 올릴 단체 이름.
    :param period_start: 산출 기간의 시작(직전 결산일 다음 날). 명부의
        ``1)일반사항`` 회계기간이 있으면 그쪽을 먼저 쓴다.
    """
    if kind not in REPORT_KINDS:
        raise ValueError(f"보고서 종류는 {' / '.join(REPORT_KINDS)} 입니다: {kind}")
    if kind == "longterm" and run.longterm is None:
        raise ValueError("장기급여를 산출하지 않았습니다. 산출 옵션을 확인하세요")

    base_date = run.config.base_date
    info = run.general_info
    start = period_start
    if start is None and info is not None and info.period_start:
        start = info.period_start
    period = (f"{start} 부터 {base_date} 까지" if start else f"{base_date} 기준")
    today = _dt.date.today()

    benefit_word = "퇴직급여" if kind == "severance" else "장기종업원급여"
    title = (f"{_STANDARD} 퇴직급여 확정급여부채 평가보고서" if kind == "severance"
             else f"{_STANDARD} 장기종업원급여부채 평가보고서")

    sections: list[tuple[str, str]] = []   # (제목, 본문 html)

    if kind == "severance":
        _severance_sections(run, sections, period)
    else:
        _longterm_sections(run, sections, period)
    _shared_sections(run, sections, kind)

    toc = "".join(
        f"<li>{i + 1}. {_esc(name)}</li>" for i, (name, _) in enumerate(sections)
    )
    body = "".join(
        f'<section class="chapter"><h2>{i + 1}. {_esc(name)}</h2>{content}</section>'
        for i, (name, content) in enumerate(sections)
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
@page {{ size: A4; margin: 18mm 16mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
       color: #1A1D23; line-height: 1.6; font-size: 10.5pt; margin: 0; }}
.cover {{ text-align: center; padding-top: 30vh; page-break-after: always; }}
.cover h1 {{ font-size: 20pt; color: #1F3864; margin-bottom: 40px; }}
.cover .meta {{ font-size: 12pt; line-height: 2.2; }}
.cover .client {{ font-size: 16pt; font-weight: bold; margin-top: 60px; }}
.letter, .toc {{ page-break-after: always; }}
.letter p {{ margin: 12px 0; }}
h2 {{ color: #1F3864; border-bottom: 2px solid #1F3864; padding-bottom: 4px;
     font-size: 13pt; margin: 26px 0 12px; }}
h3 {{ color: #44546A; font-size: 11pt; margin: 18px 0 6px; }}
.chapter {{ page-break-before: always; }}
.chapter:first-of-type {{ page-break-before: auto; }}
table.t {{ border-collapse: collapse; width: 100%; margin: 6px 0 14px;
          font-size: 9.5pt; page-break-inside: avoid; }}
.t th, .t td {{ border: 1px solid #8894AB; padding: 4px 8px; }}
.t th {{ background: #EEF3F8; color: #1F3864; }}
.t td.n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.t.wide {{ font-size: 8.5pt; }}
.unit {{ text-align: right; color: #5B6478; font-size: 8.5pt; }}
.note {{ color: #5B6478; font-size: 9pt; margin: 4px 0 12px; }}
ol.toc-list {{ font-size: 12pt; line-height: 2.4; list-style: none; }}
dl dt {{ font-weight: bold; margin-top: 10px; }}
dl dd {{ margin: 2px 0 8px 12px; color: #333; }}
@media screen {{ body {{ max-width: 800px; margin: 0 auto; padding: 24px; }} }}
</style>
</head>
<body>
<div class="cover">
  <h1>{_esc(title)}</h1>
  <div class="meta">기준일 : {base_date}<br>작성일 : {today}</div>
  <div class="client">{_esc(client) or "&nbsp;"}</div>
  <div class="meta" style="margin-top:40px">연금계리 산출 시스템</div>
</div>

<div class="letter">
<h2>{_esc(benefit_word)} 확정급여부채 평가보고서</h2>
<p><b>{_esc(client) or '회사'}</b> 귀중</p>
<p>{_esc(client) or '회사'}(이하 '회사')의 {benefit_word} 채무에 대하여
{base_date} 기준으로 PUC(Projected Unit Credit) 방식에 의한 평가를 실시하였으며,
그 결과는 본 보고서 본문에 수록되어 있습니다.</p>
<p>본 보고서는 한국채택국제회계기준 {_STANDARD} 종업원급여를 기준으로 하여
필요한 정보를 제공하기 위하여 작성되었습니다. 그 이외의 목적을 위한 평가를
위해서는 추가적인 자료가 필요하거나 평가결과가 달라질 수 있습니다.</p>
<p>본 보고서를 작성하는 데 필요한 종업원급여정보, 보험수리적 가정 및 회계정책
사항 등은 회사가 제시한 정보를 근거로 하여 작성되었으며, 이에 대한 감사 절차는
수행하지 아니하였습니다.</p>
<p>본 보고서는 회계처리·재무제표 공시 목적으로만 사용할 수 있으며, 감사 목적
이외에 제3자에게 배포될 수 없습니다.</p>
</div>

<div class="toc">
<h2>목 차</h2>
<ol class="toc-list">{toc}</ol>
</div>

{body}
</body>
</html>"""


# ── 퇴직급여 장 ──────────────────────────────────────────────────

def _severance_sections(run: Any, sections: list, period: str) -> None:
    val = run.valuation
    roll = run.rollforward
    assets = run.plan_assets
    info = run.general_info
    # 전기 연결 없이 돌리면 파이프라인이 '최초 인식' 증감표(기초 0)를 만든다.
    # 그 표를 공시 서식에 그대로 실으면 기초채무가 0 인 회사처럼 보이므로,
    # 전기가 실제로 연결됐을 때만 변동표를 싣는다.
    if roll is not None and not roll.opening_dbo and not roll.interest_cost:
        roll = None
    single = (run.assumptions.discount.flat
              if run.assumptions.discount.flat is not None
              else val.single_discount_rate())

    # 1. 평가개요
    sections.append(("평가개요", f"""
<p>본 퇴직급여 보고서는 {_esc(period)} 회사의 퇴직급여에 대한 보고서로서
{_STANDARD}에 따라 수행되었습니다.</p>
<ul>
<li>평가는 {_STANDARD}가 정한 예측단위적립방식(PUC)에 따랐습니다.</li>
<li>본 보고서는 회사가 제시한 인원현황 및 회계장부상 기재내역을 기준으로
작성되었습니다.</li>
<li>사용한 가정은 회사가 제시한 최선의 추정치와 {_STANDARD}의 요구사항을
반영하고 있습니다.</li>
<li>본 보고서는 재무상태표의 확정급여부채, 재무제표·포괄손익계산서 공시정보,
주석 공시사항을 제공합니다.</li>
<li>본 보고서는 회계처리 및 재무제표 공시 목적 이외에는 사용할 수 없습니다.</li>
</ul>"""))

    # 2. 평가 결과 요약
    national = info.assets.national_pension if info is not None else 0.0
    net_rows: list[tuple[str, Any]] = [
        ("1. 기말 확정급여채무의 현재가치", _won(val.dbo))]
    if assets is not None:
        net_rows.append(("2. 사외적립자산의 공정가치", _won(-assets.closing_fair_value)))
        if national:
            net_rows.append(("3. 국민연금 전환금", _won(-national)))
        if assets.unpaid_benefits:
            net_rows.append(("4. 미지급 퇴직급여", _won(assets.unpaid_benefits)))
        before = (val.dbo + assets.unpaid_benefits
                  - assets.closing_fair_value - national)
        if assets.asset_ceiling is not None:
            # 문단 64 — 초과적립일 때만 자산을 깎는다.
            net_rows += [
                ("5. 자산인식상한기준 적용 전 순확정급여부채(자산)", _won(before)),
                ("6. 자산인식상한 적용에 따른 자산차감액", _won(assets.ceiling_effect)),
                ("7. 자산인식상한기준 적용 후 순확정급여부채(자산)",
                 _won(before + assets.ceiling_effect)),
            ]
        else:
            net_rows.append(("5. 순확정급여부채(자산)", _won(before)))
    summary = _kv_table(net_rows)

    pl_rows: list[tuple[str, Any]] = [("1. 당기근무원가", _won(
        roll.service_cost if roll is not None else val.service_cost))]
    if roll is not None:
        pl_rows += [
            ("2. 확정급여채무의 이자비용", _won(roll.interest_cost)),
            ("3. 과거근무원가", _won(roll.past_service_cost)),
            ("4. 축소·정산 손실(이익)", _won(-roll.settlement_gain)),
        ]
        fees = (info.assets.management_fee + info.assets.custody_fee
                if info is not None else 0.0)
        if fees:
            pl_rows.append(("5. 관리비용(운용관리수수료)", _won(fees)))
        if assets is not None:
            pl_rows.append(("6. 사외적립자산의 이자수익", _won(-assets.interest_income)))
        pnl = (roll.service_cost + roll.interest_cost + roll.past_service_cost
               - roll.settlement_gain
               - (assets.interest_income if assets is not None else 0.0))
        oci = roll.actuarial_gain_loss - (
            assets.remeasurement if assets is not None else 0.0)
        pl_rows += [
            ("7. 당기손익으로 인식할 금액", _won(pnl)),
            ("8. 기타포괄손익으로 인식할 재측정요소 손실(이익)", _won(oci)),
            ("9. 포괄손익계산서 인식 금액", _won(pnl + oci)),
        ]
        pl_note = ""
    else:
        pl_rows += [("2. 이자원가 (차기)", _won(val.interest_cost))]
        pl_note = ('<p class="note">전기 산출 결과를 연결하지 않아 당기 발생액 '
                   '분해(이자비용·재측정요소)는 제공되지 않습니다. 산출 화면의 '
                   '[전기 산출 결과] 를 채우면 완성됩니다.</p>')

    sections.append(("평가 결과 요약", f"""
<h3>2.1 재무상태표의 순확정급여부채(자산) 현황</h3>{summary}
<h3>2.2 손익계산서</h3>{_kv_table(pl_rows)}{pl_note}"""))

    # 3. 공시사항
    parts = []
    parts.append(f"<h3>3.1 확정급여채무의 변동내역</h3>"
                 f'<p class="note">확정급여채무의 가중평균만기(듀레이션)는 '
                 f"{val.duration:.2f}년 입니다.</p>")
    if roll is not None:
        parts.append(_kv_table([(label, _won(amount))
                                for label, amount in roll.as_rows()]))
    else:
        parts.append(_kv_table([("기말 확정급여채무의 현재가치", _won(val.dbo))]))
        parts.append('<p class="note">전기 연결이 없어 당기 변동 분해는 생략'
                     '되었습니다.</p>')

    parts.append("<h3>3.2 사외적립자산의 변동내역</h3>")
    if assets is not None:
        parts.append(_kv_table([(label, _won(amount))
                                for label, amount in assets.as_rows()]))
        breakdown = info.assets.breakdown if info is not None else {}
        if breakdown:
            parts.append("<h3>※ 사외적립자산 구성내역 (문단 142)</h3>")
            parts.append(_kv_table(
                [(name, _won(amount)) for name, amount in breakdown.items()]
                + [("사외적립자산 합계", _won(sum(breakdown.values())))]))
    else:
        parts.append('<p class="note">사외적립자산 입력이 없어 전액 미적립으로 '
                     '보았습니다.</p>')

    parts.append("<h3>3.3 순확정급여부채(자산)</h3>")
    if assets is not None:
        parts.append(_kv_table([(label, _won(amount))
                                for label, amount in assets.net_rows()]))
        parts.append(f'<p class="note">적립비율은 {assets.funded_ratio:.1%} 입니다. '
                     "당기 변동의 세부는 3.1과 3.2로 갈음합니다.</p>")

    parts.append("<h3>3.4 재측정요소 분석 (기타포괄손익 인식 금액)</h3>")
    if roll is not None:
        re_rows = [
            ("1. 확정급여채무의 재측정요소 손실(이익)", _won(roll.actuarial_gain_loss)),
            ("&nbsp;&nbsp;1) 가정변경에 의한 손실(이익)", _won(roll.assumption_change)),
        ]
        for label, amount in roll.assumption_steps:
            re_rows.append((f"&nbsp;&nbsp;&nbsp;&nbsp;· {label}", _won(amount)))
        re_rows.append(
            ("&nbsp;&nbsp;2) 경험조정 손실(이익)", _won(roll.experience_adjustment)))
        if assets is not None:
            re_rows.append(("2. 사외적립자산의 재측정 손실(이익)",
                            _won(-assets.remeasurement)))
        re_rows.append(("3. 총 재측정요소 손실(이익)",
                        _won(roll.actuarial_gain_loss
                             - (assets.remeasurement if assets is not None else 0.0))))
        parts.append(_kv_table(re_rows))
        if roll.assumption_steps:
            parts.append('<p class="note">가정별 몫은 전기 가정에서 당기 가정으로 '
                         '사망률 → 퇴직률 → 임금상승률 → 할인율 차례로 하나씩 갈아 '
                         '끼우며 잰 값이며, 합계는 가정변경효과와 일치합니다. '
                         '차례를 바꾸면 교차효과가 붙는 자리가 달라집니다.</p>')
        else:
            parts.append('<p class="note">가정변경 효과는 전기 기초율을 당기 명부에 '
                         '적용해 재평가한 차이로 산출했습니다. 가정별 세부 분해는 '
                         '산출 옵션에서 [재측정요소 가정별 분해] 를 켜면 나옵니다.</p>')
    else:
        parts.append('<p class="note">전기 연결이 없어 재측정요소가 산출되지 '
                     '않았습니다.</p>')

    projection = run.projection
    parts.append("<h3>3.5 차년도 예상 퇴직급여 비용</h3>")
    if projection is not None:
        parts.append(_kv_table([(label, _won(amount))
                                for label, amount in projection.expense_rows()]))
        parts.append("<h3>3.6 차년도 확정급여채무 예측</h3>")
        parts.append(_kv_table([(label, _won(amount))
                                for label, amount in projection.dbo_rows()]))
        if projection.has_assets:
            parts.append("<h3>3.7 차년도 사외적립자산 예측</h3>")
            parts.append(_kv_table([(label, _won(amount))
                                    for label, amount in projection.asset_rows()]))
        parts.append('<p class="note">예측이므로 보험수리적손익은 0 으로 두었습니다 '
                     '— 가정이 그대로 실현된다고 본 값입니다.</p>')
    sections.append(("공시사항", "".join(parts)))

    # 4. 민감도 분석
    parts = []
    if run.sensitivity is not None:
        rows = [[case.name, _won(case.dbo), _won(case.change),
                 f"{case.change_ratio:+.2%}"] for case in run.sensitivity.cases]
        parts.append(_table(["가정 변동", "확정급여채무", "증감액", "변화율"], rows))
        parts.append(
            f'<p class="note">기준 확정급여채무는 {_won(run.sensitivity.base_dbo)}원, '
            f"가중평균만기는 {val.duration:.2f}년입니다. 문단 145에 따라 다른 가정을 "
            "고정한 채 유의적 가정 하나씩을 변동했습니다.</p>")
    else:
        parts.append('<p class="note">민감도분석을 끄고 산출했습니다. 산출 옵션에서 '
                     '켠 뒤 다시 실행하십시오.</p>')
    sections.append(("민감도 분석 (Sensitivity Test)", "".join(parts)))

    # 5. 주석 사항
    sections.append(("주석 사항", _notes_section(run, single)))

    # 6. 첨부 자료
    sections.append(("첨부 자료", _attachment_section(run)))


def _notes_section(run: Any, single: float) -> str:
    val = run.valuation
    a = run.assumptions
    parts = ["""
<h3>5.1 제도의 일반사항과 위험 (문단 139)</h3>
<p>회사의 규정에 정한 바에 따라 퇴직급여는 퇴직 당시 평균임금과 근속기간을
기초로 산출되며, 지급액은 일시금으로 지급됩니다. 제도는 확정급여형(DB)이며,
회사는 할인율 변동 위험(우량회사채 수익률 하락 시 채무 증가), 임금 상승 위험,
종업원의 근속·수명에 관한 보험수리적 위험에 노출되어 있습니다.</p>"""]

    base_up = sorted(a.salary.base_up.points.items())
    base_up_text = ", ".join(f"{k}년차 {v:.3%}" for k, v in base_up) or "0%"
    parts.append(f"""
<h3>5.2 기본적인 보험수리적 가정 (문단 144)</h3>
{_table(["구분", "값"], [
    ["1. 할인율", _pct(single)],
    ["2. 임금인상률 (Base-up)", base_up_text],
    ["3. 승급률", f"기초율 표 적용 (기준: {a.salary.promotion.basis}) — 첨부 6.3"],
    ["4. 사망률", "기초율 표 적용 — 첨부 6.5"],
    ["5. 중도퇴직률", f"기초율 표 적용 (기준: {a.withdrawal.basis}) — 첨부 6.4"],
], unit="")}
<h3>5.3 평가방법</h3>
<p>종업원급여의 확정급여채무 및 당기근무원가의 현재가치는 {_STANDARD} 기준에서
정한 예측단위적립방식(Projected Unit Credit Method)에 의거하여 산출하였습니다.
급여는 급여산정식에 따라 근무기간에 귀속했으며(문단 70), 추가 근무가 유의적인
급여 증가를 낳지 않는 시점 이후로는 귀속하지 않았습니다.</p>""")

    rules = run.config.job_group_rules
    if rules:
        parts.append("<h3>5.4 퇴직급여 규정 요약</h3>")
        parts.append(_table(
            ["직군", "정년", "가입자격(년)", "근속 산정", "단수처리",
             "지급률 규정 방식"],
            [[r.mapped_name or r.source_name,
              f"{r.severance_nra}세" + (f" (임원 {r.executive_nra}세)"
                                        if r.executive_nra else ""),
              r.min_service_years or "없음", r.service_basis, r.service_fraction,
              a.severance_benefit.mode(r.mapped_name or r.source_name)]
             for r in rules if not r.excluded], unit=""))

    attributed = maturity_buckets(val.cash_flows())
    paid = dict(maturity_buckets(val.benefit_cash_flows()))
    parts.append("""
<h3>5.5 경과기간별 예상 확정급여채무 및 퇴직급여 지급 예상액 (문단 147(c))</h3>""")
    parts.append(_table(
        ["구분", "확정급여채무 (가득반영, 할인 미적용)", "퇴직급여 지급 예상액 (할인 미적용)"],
        [[label, _won(amount), _won(paid.get(label, 0.0))]
         for label, amount in attributed]))
    parts.append('<p class="note">※ 현재가치 할인 전 명목금액입니다.</p>')

    contributions = (run.plan_assets.contributions
                     if run.plan_assets is not None else 0.0)
    parts.append(f"""
<h3>5.6 차년도 예상 기여금 (문단 147(b))</h3>
<p>회사의 적립정책에 따라 결정될 사항입니다. 참고로 당기 부담금 납입액은
{_won(contributions)}원입니다.</p>""")
    return "".join(parts)


def _attachment_section(run: Any) -> str:
    val = run.valuation
    a = run.assumptions
    info = run.general_info
    base_date = run.config.base_date

    included = [m for m in val.members if not m.excluded_reason]
    count = len(included)
    wage_total = sum(m.monthly_wage for m in included)
    ages = [m.age for m in included]
    hire_years = [
        (base_date - m.hire_date).days / 365.2425
        for m in included if m.hire_date is not None
    ]
    services = [m.past_service for m in included]

    parts = ["<h3>6.1 임직원 분포 현황</h3>"]
    parts.append(_table(["구분", "값"], [
        ["1) 임직원 인원수", f"{count:,}명"],
        ["2) 기준임금 합계", f"{_won(wage_total)}원"],
        ["3) 평균 기준임금", f"{_won(wage_total / count if count else 0)}원"],
        ["4) 평균 연령", f"{sum(ages) / count if count else 0:.1f}세"],
        ["5) 평균 근속연수 (입사일 기준)",
         f"{sum(hire_years) / len(hire_years) if hire_years else 0:.1f}년"],
        ["6) 퇴직금 평균 근속연수 (기산일 기준)",
         f"{sum(services) / count if count else 0:.1f}년"],
    ], unit=""))
    excluded = val.exclusion_summary()
    if excluded:
        parts.append(_table(["산출 제외 사유", "인원"],
                            [[reason, f"{n:,}명"] for reason, n in excluded.items()],
                            unit=""))

    grade = info.credit_grade if info is not None else ""
    if a.discount.flat is not None:
        discount_note = f"단일 할인율 {_pct(a.discount.flat)} (회사 제시)"
    else:
        discount_note = (
            f"수익률곡선방식 — 우량회사채 기간구조로 만기별 할인 후 단일할인율 "
            f"{_pct(val.single_discount_rate())} 역산"
            + (f" (신용등급 {grade})" if grade else ""))
    parts.append(f"""
<h3>6.2 보험수리적 가정 산출 정보</h3>
<p>1) 할인율 — {_esc(discount_note)}. 문단 83에 따라 보고기간 말 현재 우량회사채의
시장수익률을 참조하였습니다.</p>
<p>2) 총 임금상승률 — 임금인상률(Base-up)과 승급률을 결합한 동태적 임금상승률을
사용하였습니다.</p>""")

    # 있는 표만 싣고 번호는 이어 붙인다 — 6.3부터.
    number = 3

    def attach(title: str, headers: list, rows: list) -> None:
        nonlocal number
        parts.append(f"<h3>6.{number} {_esc(title)}</h3>")
        parts.append(_table(headers, rows, cls="t wide", unit=""))
        number += 1

    if a.discount.flat is None and a.discount.spot.points:
        attach("할인율 기간구조", ["만기(년)", "현물이자율"],
               [[k, _pct(v)] for k, v in sorted(a.discount.spot.points.items())])

    for title, curves, basis in (
        ("승급률", a.salary.promotion.curves, a.salary.promotion.basis),
        ("중도퇴직률", a.withdrawal.curves, a.withdrawal.basis),
    ):
        names, rows = _curve_rows(curves)
        if names:
            attach(f"{title} (기준: {basis})", [basis, *names], rows)

    names, rows = _curve_rows({"남자": a.mortality.male, "여자": a.mortality.female})
    if names:
        attach("사망률 qx", ["연령", *names], rows)

    names, rows = _curve_rows(a.severance_benefit.curves, fmt=lambda v: f"{v:g}")
    if names:
        attach("퇴직급여 지급률", ["근속연수", *names], rows)
    modes = [[name, a.severance_benefit.mode(name),
              getattr(a.severance_benefit.formulas.get(name), "source", "")]
             for name in a.severance_benefit.curves]
    if modes:
        parts.append(_table(["규정명", "방식", "수식"], modes, unit=""))
    if not a.exit_causes.is_empty():
        parts.append("<h3>※ 퇴직사유별 지급 차등</h3>")
        parts.append(_table(
            ["지급률 규정", "퇴직사유", "대체 규정", "가산 규정", "가산액(원)",
             "근속 하한", "가산 귀속"],
            [[rule, cause, entry.benefit_rule, entry.extra_rule,
              _won(entry.extra_amount, blank=""), entry.min_service or "",
              entry.attribution_basis(cause)]
             for (rule, cause), entry in sorted(a.exit_causes.rules.items())],
            unit=""))
    return "".join(parts)


# ── 장기급여 장 ──────────────────────────────────────────────────

def _longterm_sections(run: Any, sections: list, period: str) -> None:
    lt = run.longterm
    a = run.assumptions
    info = run.general_info
    single = (a.discount.flat if a.discount.flat is not None
              else run.valuation.single_discount_rate())

    sections.append(("평가개요", f"""
<p>본 장기급여 보고서는 {_esc(period)} 회사의 기타장기종업원급여에 대한
보고서로서 {_STANDARD}에 따라 수행되었습니다.</p>
<ul>
<li>평가는 예측단위적립방식(PUC)에 따랐습니다 (문단 153~158).</li>
<li>재측정요소는 문단 154에 의거 전액 당기손익으로 인식합니다.</li>
<li>본 보고서는 회사가 제시한 인원현황 및 회계장부상 기재내역을 기준으로
작성되었습니다.</li>
</ul>"""))

    paid = info.longterm_paid if info is not None else 0.0
    sections.append(("평가 결과 요약", f"""
<h3>2.1 재무상태표의 부채 현황</h3>
{_kv_table([("1. 기말 확정급여채무의 현재가치", _won(lt.dbo)),
            ("2. 사외적립자산의 공정가치", "0"),
            ("3. 재무상태표에 인식된 순부채", _won(lt.dbo))])}
<h3>2.2 손익계산서</h3>
{_kv_table([("1. 당기근무원가", _won(lt.service_cost)),
            ("2. 확정급여채무의 이자비용",
             _won(run.longterm_rollforward.interest_cost
                  if run.longterm_rollforward else lt.interest_cost)),
            ("3. 재측정요소 손실(이익)",
             _won(run.longterm_rollforward.remeasurement)
             if run.longterm_rollforward else "—"),
            ("4. 당기손익으로 인식할 금액",
             _won(run.longterm_rollforward.profit_or_loss)
             if run.longterm_rollforward
             else _won(lt.service_cost + lt.interest_cost))])}
<p class="note">* 재측정요소는 {_STANDARD} 문단 154에 의거 당기손익으로
처리합니다. 전기 장기급여채무를 연결하면 재측정 금액이 산출됩니다.</p>"""))

    roll = run.longterm_rollforward
    if roll is not None:
        move_rows = [(label, _won(amount)) for label, amount in roll.as_rows()]
        move_note = ("재측정요소는 문단 154 에 따라 전액 당기손익으로 인식합니다. "
                     f"당기손익 인식액은 {_won(roll.profit_or_loss)}원입니다.")
    else:
        move_rows = [
            ("1. 기시 확정급여채무의 현재가치", "—"),
            ("2. 당기근무원가", _won(lt.service_cost)),
            ("3. 확정급여채무의 이자비용", _won(lt.interest_cost)),
            ("4. 확정급여채무 장기급여 지급액", _won(-paid) if paid else "—"),
            ("5. 기말 확정급여채무의 현재가치", _won(lt.dbo)),
        ]
        move_note = ("기시 채무는 산출 화면에 전기 장기급여채무를 넣으면 채워집니다. "
                     "장기급여 지급액은 명부의 1)일반사항에서 읽었습니다.")
    sections.append(("공시사항", f"""
<h3>3.1 확정급여채무의 변동내역</h3>
{_kv_table(move_rows)}
<p class="note">{move_note}</p>
<h3>3.2 차년도 예상 장기급여 비용</h3>
{_kv_table([("1. 당기근무원가", _won(lt.service_cost)),
            ("2. 확정급여채무의 이자비용", _won(lt.interest_cost)),
            ("3. 합계", _won(lt.service_cost + lt.interest_cost))])}"""))

    sections.append(("민감도 분석", f"""
<p class="note">{_STANDARD} 문단 158은 기타장기종업원급여에 대하여 별도의
공시를 요구하지 않습니다. 민감도분석은 퇴직급여 보고서를 참조하십시오.</p>"""))

    included = [m for m in lt.members if not m.excluded_reason]
    count = len(included)
    kinds = sorted({m.benefit_kind for m in included if m.benefit_kind})
    daily = [m.daily_base_pay for m in included if m.daily_base_pay]
    parts = [f"""
<h3>5.1 제도의 일반사항</h3>
<p>회사의 규정에 따라 장기근속 종업원에게 {"、".join(kinds) or "장기근속 급여"}
를 지급합니다. 산출 대상 인원은 {count:,}명입니다.</p>
<h3>5.2 기본적인 보험수리적 가정</h3>
{_table(["구분", "값"], [
    ["1. 할인율", _pct(single)],
    ["2. 임금인상률 (Base-up)",
     ", ".join(f"{k}년차 {v:.3%}" for k, v in sorted(a.salary.base_up.points.items())) or "0%"],
    ["3. 평균 1일 통상임금",
     f"{_won(sum(daily) / len(daily) if daily else 0)}원"],
], unit="")}"""]

    if a.longterm_rules:
        parts.append("<h3>5.3 장기급여 지급 항목</h3>")
        parts.append(_table(
            ["규정명", "항목", "지급유형", "현물 상승률", "지급시점",
             "반복 주기(년)", "누적", "지급일"],
            [[name, entry.item, entry.kind,
              _pct(entry.escalation, 2) if entry.escalation else "",
              entry.timing, entry.every_years or "",
              "Y" if entry.accumulate else "", entry.anniversary]
             for name, entries in sorted(a.longterm_rules.items())
             for entry in entries], unit=""))

    names, rows = _curve_rows(a.longterm_benefit.curves, fmt=lambda v: f"{v:g}")
    if names:
        parts.append("<h3>5.4 장기급여 지급률</h3>")
        parts.append(_table(["근속연수", *names], rows, cls="t wide", unit=""))
    sections.append(("주석 사항", "".join(parts)))


# ── 공통 꼬리 ────────────────────────────────────────────────────

_GLOSSARY = (
    ("퇴직급여 (Severance Benefit)",
     "종업원이 퇴직한 이후에 지급하는 종업원급여. 퇴직 일시금·퇴직연금과 그 밖의 "
     "퇴직후급여를 말한다."),
    ("확정급여채무 (Defined Benefit Obligation)",
     "예측단위적립방식으로 평가한 종업원의 퇴직급여 채무의 현재가치."),
    ("당기근무원가 (Service Cost)",
     "당기에 종업원이 근무용역을 제공함에 따라 늘어나는 확정급여채무의 현재가치 "
     "증가액."),
    ("이자비용 (Interest Cost)",
     "확정급여채무가 기초에서 기말로 이동하면서 발생하는 시간가치의 증가분."),
    ("재측정요소 / 보험수리적손익 (Remeasurements / Actuarial Gain·Loss)",
     "가정으로 추정한 채무(또는 자산)가 실제와 다르거나 가정 자체를 바꿀 때 "
     "생기는 차액. 확정급여채무·사외적립자산의 재측정은 기타포괄손익으로, "
     "기타장기종업원급여의 재측정은 당기손익으로 인식한다."),
    ("과거근무원가 (Past Service Cost)",
     "제도를 새로 도입하거나 개정할 때 과거 근무용역분 채무의 현재가치가 변동하는 "
     "금액. 당기손익으로 즉시 인식한다 (문단 103)."),
    ("예측단위적립방식 (Projected Unit Credit)",
     "장래급여를 근무기간의 단위로 분할·귀속하고 그 단위의 현재가치를 쌓아 "
     "채무를 재는 방식 (문단 67~68)."),
    ("정산 (Settlement)",
     "확정급여제도에 따라 생긴 급여의 전부나 일부에 대한 의무를 더 이상 부담하지 "
     "않기로 하는 거래 (문단 109~112)."),
    ("듀레이션 (가중평균만기)",
     "채무 현금흐름의 현재가치로 가중한 평균 지급시점. 할인율의 회사채 만기 선택 "
     "근거가 된다."),
)


def _shared_sections(run: Any, sections: list, kind: str) -> None:
    glossary = "".join(f"<dt>■ {_esc(term)}</dt><dd>{_esc(desc)}</dd>"
                       for term, desc in _GLOSSARY)
    issues = run.issues
    footer = (f'<p class="note">검증 결과: 오류 {len(issues.errors)}건, '
              f"경고 {len(issues.warnings)}건 — 상세는 산출 결과 파일의 "
              "검증리포트 시트를 참조하십시오. 본 보고서의 수치는 산출 엔진 "
              "결과를 그대로 옮긴 것으로, 화면 요약·결과 엑셀과 원 단위까지 "
              "일치합니다.</p>")
    sections.append(("용어 정리", f"<dl>{glossary}</dl>{footer}"))
