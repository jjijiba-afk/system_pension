"""회사에 보내는 명부 양식.

받는 사람은 계리를 모르는 인사·회계 담당자다. 그래서 이 파일 하나로 **무엇을
어디에 적는지** 가 끝나야 한다.

* 색이 진한 앞쪽 열만 채우면 산출된다. 나머지는 해당자만.
* 열은 ``퇴직급여``·``기타장기`` 두 파트로 갈리고, 파트 안에서 다시 묶음으로
  나뉜다. 해당 없는 파트·묶음은 통째로 지우고 보내도 된다.
* 머리글 이름으로 열을 찾으므로 순서를 바꿔도 되고, 옛 이름으로 적어 보내도
  읽힌다(:mod:`pension.layout` 의 별칭).

명부만으로는 산출이 되지 않으므로 같은 파일에 ``기본정보``·``퇴직급여규정``·
``장기급여규정``·``사외적립자산`` 을 함께 둔다. 따로 보내면 그중 하나가 빠진
채로 온다.
"""

from __future__ import annotations

import datetime as _dt

from .workbook import save_workbook
from pathlib import Path

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FACE = "맑은 고딕"

# 명부를 크게 두 부분으로 가른다. 시트는 한 장이되, 퇴직급여로 사는 열과
# 기타장기(근속포상·장기근속휴가)로 사는 열이 섞여 있으면 안 된다 — 기타장기가
# 없는 회사가 대부분인데, 섞여 있으면 "우리는 이 칸을 채워야 하나" 를 열마다
# 되묻게 된다. 기타장기 파트는 언제나 **오른쪽 끝** 이라, 통째로 지우고 보내도
# 왼쪽 파트의 열 자리가 흔들리지 않는다.
#
# 시트를 아예 둘로 가르지 않는 이유는 한 사람이 두 줄이 되기 때문이다. 사번을
# 두 시트에 맞춰 두어야 하고, 한쪽에만 있는 사람이 생긴다.
PARTS = {
    "퇴직급여": ("1F3864",
             "퇴직급여채무를 산출하는 데 쓰는 부분입니다. 여기까지는 모든 회사가 채웁니다."),
    "기타장기": ("7A5470",
             "근속포상·장기근속휴가가 있는 회사만. 없으면 여기부터 오른쪽 끝까지 "
             "통째로 비우거나 지우고 보내셔도 됩니다."),
}

# 블록별 색 — (파트, 채움색, 글자색, 안내). 필수인지 아닌지가 열 색만 보고
# 판단되게 한다.
BLOCKS = {
    "필수": ("퇴직급여", "1F3864", "FFFFFF", "이 열이 비면 그 사람은 산출되지 않습니다."),
    "제도·근속": ("퇴직급여", "2E6F6A", "FFFFFF",
              "해당자만 기입하십시오. 비우면 아래 '비우면' 대로 봅니다."),
    "개인 예외": ("퇴직급여", "8F6318", "FFFFFF",
              "이 사람만 직군 규칙과 달라야 할 때. 대부분 비웁니다 — 블록째 지워도 됩니다."),
    "기타": ("퇴직급여", "5B6478", "FFFFFF", "드물게 쓰는 항목과 관리용. 블록째 지워도 됩니다."),
    "장기급여": ("기타장기", "7A5470", "FFFFFF",
              "근속포상 대상자만. 대상이 아닌 사람은 '장기급여 대상' 에 N 만 적으면 됩니다."),
    "장기급여 예외": ("기타장기", "9C7799", "FFFFFF",
                 "이 사람만 장기급여 규칙이 직군과 달라야 할 때. 대부분 비웁니다."),
}

#: 시트의 고정 줄. 파트 → 블록 → 열 이름 → 자료. 읽을 때는 머리글 이름으로
#: 찾으므로 여기가 바뀌어도 산출은 흔들리지 않는다.
PART_ROW = 2
BLOCK_ROW = 3
HEADER_ROW = 4
FIRST_DATA_ROW = 5

# (블록, 열이름, 뜻, 예시, 비우면)
ACTIVE = [
    ("필수", "순번", "1부터 이어지는 번호", 1, "무시합니다"),
    ("필수", "사번", "회사 사번. 전기와 맞대어 볼 때 이것으로 짝지웁니다",
     "A0001", "주민번호+성명으로 임시 사번을 만듭니다"),
    ("필수", "성명", "동명이인이 있어도 그대로", "홍길동", "사번만으로 봅니다"),
    ("필수", "주민등록번호 앞7자리",
     "생년월일과 성별을 여기서 읽습니다. 뒷 여섯 자리는 적지 마세요 — "
     "받는 순간 개인정보 등급이 올라갑니다", "850501-1",
     "생년월일·성별 칸을 따로 기입하십시오"),
    ("필수", "생년월일", "주민번호를 적었으면 비워도 됩니다", "", "주민번호에서 읽습니다"),
    ("필수", "성별", "남 / 여 (M/F, 1/2 도 읽습니다). 주민번호를 적었으면 비워도 "
     "됩니다 — 주민번호를 안 적으면 여기를 채워야 합니다. 사망률이 성별로 "
     "갈립니다", "", "주민번호에서 읽습니다. 둘 다 없으면 남자로 보고 알려 드립니다"),
    ("필수", "입사일자", "근속 기산일", "2010-03-02", "근속을 못 정해 산출 제외"),
    ("필수", "중간정산일", "중간정산을 했으면 그 날. 근속이 이 날부터 다시 셉니다",
     "", "중간정산 없음 — 입사일부터 셉니다"),
    ("필수", "직군", "지급률·정년이 갈리는 묶음. 임원도 여기에 적습니다 "
     "('임원'·'상무'·'등기이사'). [기본정보] 의 직군 규칙에서 묶어 줍니다",
     "정규직", "첫 직군으로 봅니다"),
    ("필수", "규정명", "이 사람에게 걸 지급률 규정. 경험률을 규정별로 볼 때도 씁니다",
     "규정A", "직군에 걸린 규정"),
    ("필수", "30일 평균임금", "근로기준법상 평균임금 30일분(원)", 5_000_000,
     "임금이 0이라 채무도 0"),
    # 예시 금액은 이 줄의 사람·임금·근속으로 실제로 계산한 값이다. 어림수를
    # 적어 두면 양식을 그대로 돌렸을 때 우리 검산이 곧바로 어긋난다고 짚어,
    # 받는 사람이 "이 프로그램은 원래 경고가 뜨는구나" 로 배우게 된다.
    ("필수", "추계액", "회사가 계산해 둔 퇴직급여추계액(원). 우리 값과 맞대어 봅니다",
     79_219_178, "검산을 건너뜁니다"),
    ("제도·근속", "차년도 추계액",
     "1년 뒤 추계액(원). 있으면 임금상승 가정이 회사 생각과 같은지까지 "
     "맞대어 볼 수 있습니다 — 당기 추계액만으로는 그 축이 안 보입니다",
     88_356_529, "차년도 검산을 건너뜁니다"),

    ("제도·근속", "퇴직급여 제도구분", "DB / DC / 퇴직금제도. DC 는 산출에서 빠집니다",
     "DB", "DB 로 봅니다"),
    ("제도·근속", "DB비율", "혼합형이면 DB 비중. `DC 1% / DB 99%` 면 0.99 (99 로 적어도 됩니다)",
     "", "1 (전액 DB)"),
    ("제도·근속", "혼합형 가입일",
     "혼합형으로 바꾼 날. 그 전 근속은 전액 DB 로 남습니다 — 비우면 입사 때부터 "
     "혼합형이었던 것으로 보아 전 근속에 DB비율이 걸립니다", "",
     "입사 때부터 혼합형"),
    ("제도·근속", "중간정산 지급금액", "중간정산으로 이미 지급한 금액(원)", "", "0"),
    ("제도·근속", "휴직차감일수",
     "근속에서 빼는 휴직 일수. 기산일을 그만큼 뒤로 밉니다 — 연수로 환산하지 "
     "마세요", "", "0"),
    ("제도·근속", "가산근속연수",
     "군경력 등으로 지급률 근속에만 더할 연수. 급여 배수를 이 근속으로 "
     "찾습니다 — 귀속(할당)은 실제 근속 그대로입니다", "", "0"),
    ("제도·근속", "차감근속연수",
     "지급률 근속에서만 뺄 연수. '연단위 절사 지급' 같은 만근속 규정을 "
     "여기로 설계합니다", "", "0"),
    ("제도·근속", "계약종료일",
     "정년이 아니라 계약 만료 로 나가는 사람. 날짜로 적어 주십시오 — "
     "남은 햇수는 기준일이 바뀌면 틀립니다", "2027-06-30", "직군 규칙의 정년까지 근무"),
    ("제도·근속", "잔여계약기간",
     "계약종료일을 못 적을 때만. 둘 다 적으면 날짜 쪽을 씁니다",
     "", "계약종료일 또는 직군 규칙의 정년"),
    ("제도·근속", "원가코드", "제조원가 / 판관비 등 배분 코드", "판관비", "배분표를 안 만듭니다"),

    ("개인 예외", "정년연령", "이 사람만 정년이 다를 때", "", "직군 규칙의 정년"),
    ("개인 예외", "임금피크 연령", "임금피크가 시작되는 연령", "", "적용 안 함"),
    ("개인 예외", "임원지급배수",
     "임원 2·3배수처럼 이 사람만 배수가 다를 때. 적어도 저절로 곱해지지는 "
     "않습니다 — 지급률 규정을 수식 방식으로 두고 식에서 `배수` 를 불러 쓰세요",
     "", "쓰지 않습니다"),
    ("개인 예외", "퇴직률 규정", "중도·사망퇴직률을 다르게 걸 때", "", "직군에 걸린 규정"),
    ("개인 예외", "승급률 규정", "승급률을 다르게 걸 때", "", "직군에 걸린 규정"),

    ("기타", "명예퇴직 기준임금", "명예퇴직 급여를 다른 임금으로 계산할 때(원)",
     "", "30일 평균임금을 씁니다"),
    ("기타", "전입일", "계열사에서 옮겨 온 날", "", "해당 없음"),
    ("기타", "전입 인수액", "함께 넘겨받은 채무액(원)", "", "0"),
    ("기타", "추가지급 기준일", "이 날 이후 근속분에 지급률이 달라질 때 그 기준일",
     "", "구간을 나누지 않습니다"),
    ("기타", "추가지급 기본급",
     "사망 위로금·명퇴 가산금처럼 따로 얹는 금액(원). 적어도 저절로 더해지지는 "
     "않습니다 — 어느 사유에 붙는지는 [퇴직사유] 표의 가산액에 기재하십시오",
     "", "쓰지 않습니다"),
    ("기타", "비고", "자유 기재. 산출에는 쓰지 않습니다", "", ""),

    # ── 여기부터 오른쪽 끝까지가 기타장기 파트다. 통째로 지워도 된다. ──
    ("장기급여", "장기급여 대상", "Y / N", "N", "N (대상 아님)"),
    ("장기급여", "장기급여 기산일",
     "근속포상 근속을 세기 시작하는 날. 중간정산과 무관합니다 — 퇴직금을 "
     "중간정산했다고 근속포상 시계가 0 으로 돌아가지는 않습니다", "", "입사일"),
    ("장기급여", "1일 통상임금", "장기근속휴가를 금액으로 환산할 때 씁니다(원)", "", "0"),
    ("장기급여", "장기급여 기지급액", "이미 지급한 근속포상 금액(원)", "", "0"),

    ("장기급여 예외", "장기급여 지급률 규정", "장기급여만 다른 지급률을 걸 때", "",
     "직군에 걸린 규정"),
    ("장기급여 예외", "장기급여 퇴직률 규정", "장기급여만 다른 퇴직률을 걸 때", "",
     "직군에 걸린 규정"),
    ("장기급여 예외", "장기급여 승급률 규정", "장기급여만 다른 승급률을 걸 때", "",
     "직군에 걸린 규정"),
]

RETIRED = [
    ("필수", "순번", "1부터 이어지는 번호", 1, "무시합니다"),
    ("필수", "사번", "회사 사번", "T0001", "주민번호+성명으로 임시 사번을 만듭니다"),
    ("필수", "성명", "", "이퇴직", "사번만으로 봅니다"),
    ("필수", "주민등록번호 앞7자리", "생년월일과 성별. 뒷 여섯 자리는 적지 마세요",
     "800210-1", "생년월일 칸을 기입하십시오"),
    ("필수", "생년월일", "주민번호를 적었으면 비워도 됩니다", "", "주민번호에서 읽습니다"),
    ("필수", "성별", "남 / 여. 주민번호를 적었으면 비워도 됩니다 — 경험사망률을 "
     "낼 때 씁니다", "", "주민번호에서 읽습니다. 둘 다 없으면 남자로 셉니다"),
    ("필수", "입사일자", "근속 기산일", "2012-04-01", "근속을 못 정합니다"),
    ("필수", "퇴사일", "실제 퇴직일. DC 전환일·전출일도 여기에 적습니다",
     "2025-06-30", "퇴직자로 세지 않습니다"),
    ("필수", "퇴직사유",
     "중도 / 사망 / DC전환 / 정년 / 전출 / 사업처분 (숫자 1~6 으로 적어도 됩니다)",
     "중도", "사유 미상으로 봅니다"),
    ("필수", "직군", "재직자명부와 같은 표기로. 임원도 여기에", "정규직", "첫 직군으로 봅니다"),

    ("제도·근속", "퇴직급여 제도구분", "DB / DC / 퇴직금제도", "DB", "DB 로 봅니다"),
    ("제도·근속", "퇴직급여 총지급액",
     "채무 전표처리 기준 지급액(원). 퇴직으로 채무가 줄어든 금액이며 "
     "증감표의 급여지급액이 이 값입니다", 45_000_000,
     "지급액 0 으로 봅니다"),
    ("제도·근속", "실지급 기준 지급액",
     "실제로 계좌에서 나간 금액(원). 12월 퇴직자의 돈이 1월에 나가면 전표는 "
     "당기, 현금은 차기라 두 값이 갈립니다", "", "전표 기준과 같은 것으로 봅니다"),
    ("제도·근속", "사외자산 지급액", "그중 사외적립자산에서 나간 금액(원)", 40_000_000, "0"),
    ("제도·근속", "사외자산 지급일", "자산에서 실제로 나간 날", "", "퇴사일로 봅니다"),
    ("제도·근속", "국민연금 전환금", "국민연금 전환금 지급액(원)", "", "0"),
    ("제도·근속", "원가코드", "제조원가 / 판관비 등 배분 코드", "제조원가", "배분표를 안 만듭니다"),

    ("개인 예외", "퇴직률 규정", "이 사람만 다른 퇴직률을 걸 때", "", "직군에 걸린 규정"),

    ("기타", "퇴직위로금 등", "퇴직급여 외에 지급한 금액(원)", "", "0"),
    ("기타", "전출 지급액", "계열사 전출·사업처분으로 넘긴 금액(원)", "", "0"),
    ("기타", "비고", "자유 기재. 산출에는 쓰지 않습니다", "", ""),

    # ── 여기부터 오른쪽 끝까지가 기타장기 파트다. 통째로 지워도 된다. ──
    ("장기급여", "장기급여 대상", "Y / N", "N", "N (대상 아님)"),
    ("장기급여", "장기급여 지급액", "퇴직하며 지급한 장기급여(원)", "", "0"),

    ("장기급여 예외", "장기급여 퇴직률 규정", "장기급여만 다른 퇴직률을 걸 때", "",
     "직군에 걸린 규정"),
]

#: 사건 구분 낱말. :mod:`pension.events` 가 아는 말과 같아야 한다.
EVENT_KINDS: tuple[str, ...] = ("축소", "정산", "사업결합", "분할")

#: 축소·정산·사업결합·분할로 기중에 드나든 사람들.
#:
#: 결산일 명부에는 없다 — 이미 나갔거나, 아직 안 들어왔거나, 제도가 바뀌어
#: 종전 조건으로는 더 이상 세지 않는 사람들이다. **사건 시점 기준으로** 다시
#: 재야 소멸·인수 채무가 나온다. 결산일 가정으로 재면 그 사이의 이자와
#: 임금상승이 섞여 정산손익이 그만큼 틀린다.
EXTRA = [
    ("필수", "순번", "1부터 이어지는 번호", 1, "무시합니다"),
    ("필수", "사건 구분", " / ".join(EVENT_KINDS), "정산",
     "사건을 알 수 없어 이 줄을 건너뜁니다"),
    ("필수", "사건일", "그 일이 일어난 날. 이 날 기준으로 다시 계산합니다",
     "2025-07-01", "이 줄을 건너뜁니다"),
    ("필수", "사번", "재직자명부와 같은 사번으로", "A0007",
     "주민번호+성명으로 임시 사번을 만듭니다"),
    ("필수", "성명", "", "최정산", "사번만으로 봅니다"),
    ("필수", "주민등록번호 앞7자리", "생년월일과 성별. 뒷 여섯 자리는 적지 마세요",
     "780315-1", "생년월일·성별 칸을 기입하십시오"),
    ("필수", "생년월일", "주민번호를 적었으면 비워도 됩니다", "", "주민번호에서 읽습니다"),
    ("필수", "성별", "남 / 여", "", "주민번호에서 읽습니다"),
    ("필수", "입사일자", "근속 기산일", "2008-02-01", "이 줄을 건너뜁니다"),
    ("필수", "중간정산일", "중간정산을 했으면 그 날", "", "입사일부터 셉니다"),
    ("필수", "직군", "재직자명부와 같은 표기로", "정규직", "첫 직군으로 봅니다"),
    ("필수", "규정명", "사건 시점에 걸려 있던 지급률 규정", "규정A", "직군에 걸린 규정"),
    ("필수", "30일 평균임금", "사건 시점의 평균임금(원)", 6_200_000, "채무가 0 이 됩니다"),

    ("제도·근속", "퇴직급여 제도구분", "DB / DC / 퇴직금제도", "DB", "DB 로 봅니다"),
    ("제도·근속", "지급액", "그 사건으로 실제 지급한 금액(원). 정산손익이 "
     "여기서 나옵니다 — 소멸한 채무와의 차이입니다", 95_000_000, "0"),
    ("제도·근속", "휴직차감일수", "근속에서 빼는 휴직 일수", "", "0"),
    ("제도·근속", "가산근속연수", "지급률 근속에만 더할 연수", "", "0"),
    ("제도·근속", "차감근속연수", "지급률 근속에서만 뺄 연수", "", "0"),

    ("개인 예외", "정년연령", "이 사람만 정년이 다를 때", "", "직군 규칙의 정년"),
    ("개인 예외", "퇴직률 규정", "다른 퇴직률을 걸 때", "", "직군에 걸린 규정"),
    ("개인 예외", "승급률 규정", "다른 승급률을 걸 때", "", "직군에 걸린 규정"),

    ("기타", "비고", "무슨 일이었는지 한 줄로. 근거자료에 그대로 남습니다",
     "○○사업부 매각", ""),
]


#: 추가명부 예시 줄의 사번 앞머리. 이 표시로 시작하는 줄은 **산출에서 뺀다.**
#:
#: 이 시트는 통째로 비어 있는 것이 정상이라, 예시가 남은 채 돌아오기 쉽다.
#: 그대로 읽으면 있지도 않은 정산이 잡혀 증감표가 그 금액만큼 틀린다 —
#: 아무 말 없이 그럴듯한 숫자가 나오는 쪽이다. 그래서 예시를 보여 주되,
#: 지우지 않고 보내도 조용히 빠지도록 표시를 심어 둔다.
EXAMPLE_MARK: str = "(예시)"

#: 추가명부 작성 예시. 사건 네 가지 중 자주 쓰는 셋을 한 줄씩 보여 준다.
EXTRA_EXAMPLES = [
    {"순번": 1, "사건 구분": "정산", "사건일": "2025-07-01",
     "사번": f"{EXAMPLE_MARK}A0007", "성명": "최정산", "주민등록번호 앞7자리": "780315-1",
     "입사일자": "2008-02-01", "직군": "정규직", "규정명": "규정A",
     "30일 평균임금": 6_200_000, "퇴직급여 제도구분": "DB", "지급액": 95_000_000,
     "비고": "희망퇴직 정산 — 지급액과 이 시점 채무의 차이가 정산손익이 됩니다"},
    {"순번": 2, "사건 구분": "사업결합", "사건일": "2025-09-30",
     "사번": f"{EXAMPLE_MARK}A0008", "성명": "인수직원", "주민등록번호 앞7자리": "880920-2",
     "입사일자": "2015-05-04", "직군": "정규직", "규정명": "규정A",
     "30일 평균임금": 4_100_000, "퇴직급여 제도구분": "DB", "지급액": 0,
     "비고": "△△사 합병으로 넘겨받음 — 채무가 그만큼 늘어납니다"},
    {"순번": 3, "사건 구분": "분할", "사건일": "2025-11-01",
     "사번": f"{EXAMPLE_MARK}A0009", "성명": "이관직원", "주민등록번호 앞7자리": "820704-1",
     "입사일자": "2011-03-01", "직군": "정규직", "규정명": "규정A",
     "30일 평균임금": 5_300_000, "퇴직급여 제도구분": "DB", "지급액": 61_000_000,
     "비고": "○○사업부 매각으로 넘김 — 채무가 그만큼 줄어듭니다"},
]

#: 작성 예시 두 줄이 쓰는 규정명. 기초율 양식이 이 이름으로 지급률 열을 함께
#: 만들어야 한다 — 양식 한 벌을 그대로 돌렸을 때 '기초율에 없는 규정' 경고가
#: 뜨면, 우리가 보낸 두 파일이 서로 안 맞는다는 뜻이다.
EXAMPLE_RULES: tuple[str, ...] = ("규정A", "임원규정")

THIN = Side(style="thin", color="B8C0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

#: 금액 칸에 거는 표시 서식. 값은 건드리지 않고 **보이는 모양만** 바꾼다 —
#: 셀 안에는 그대로 숫자가 들어 있으므로 우리가 읽을 때도, 회사가 계산식을
#: 걸 때도 달라지는 것이 없다.
MONEY_FORMAT: str = "#,##0"

#: 금액을 적는 열. 세 자리마다 끊어 주지 않으면 자릿수를 눈으로 셀 수 없다 —
#: ``5000000`` 과 ``50000000`` 은 한 번에 구분되지 않고, 0 하나가 더 붙은
#: 임금은 그 사람의 채무를 열 배로 만든다. 대조하는 사람이 가장 먼저 보는
#: 것이 자릿수라, 여기서 막지 못하면 뒤에서는 못 잡는다.
MONEY_COLUMNS: frozenset = frozenset({
    # 재직자명부
    "30일 평균임금", "추계액", "차년도 추계액", "중간정산 지급금액",
    "명예퇴직 기준임금", "전입 인수액", "추가지급 기본급",
    "1일 통상임금", "장기급여 기지급액",
    # 퇴직자명부
    "퇴직급여 총지급액", "사외자산 지급액", "국민연금 전환금",
    "퇴직위로금 등", "전출 지급액", "장기급여 지급액",
    # 추가명부
    "지급액",
})

#: 퇴직사유 코드 → 말. 기존 명부는 숫자로 오므로 제안 서식에서는 말로 보여 준다.
REASON_WORD = {
    "1": "중도", "2": "사망", "3": "DC전환",
    "4": "정년", "5": "전출", "6": "사업처분",
}


#: 두 번째 작성 예시. 한 줄만 보이면 "이 줄만 고치면 되나" 로 읽혀, 사람이
#: 늘어날 때 어디에 이어 적어야 할지 알기 어렵다. 성격이 다른 사람으로 둔다.
SECOND_ACTIVE = {
    "순번": 2, "사번": "A0002", "성명": "김임원",
    "주민등록번호 앞7자리": "720815-2", "입사일자": "2005-01-02",
    "직군": "임원", "규정명": "임원규정", "30일 평균임금": 9_000_000,
    "추계액": 189_073_973, "차년도 추계액": 205_016_069,
    "퇴직급여 제도구분": "DB", "임원지급배수": 2,
    "원가코드": "판관비", "장기급여 대상": "N",
}
SECOND_RETIRED = {
    "순번": 2, "사번": "T0002", "성명": "박정년",
    "주민등록번호 앞7자리": "651120-2",
    "입사일자": "1998-03-02", "퇴사일": "2025-11-20",
    "퇴직사유": "정년", "직군": "정규직",
    "퇴직급여 제도구분": "DB", "퇴직급여 총지급액": 120_000_000,
    "사외자산 지급액": 118_000_000, "원가코드": "제조원가", "장기급여 대상": "N",
}


#: 우리 양식에 없는, 회사가 원래 두고 있던 열. 파트를 정할 수 없으므로 따로 센다.
EXTRA_BLOCK = "회사 열"
EXTRA_PART = "회사 열"


def _as_money(cell, label: str) -> None:
    """금액 열이면 세 자리 끊어 보이게 한다. 아니면 아무것도 하지 않는다."""
    if label in MONEY_COLUMNS:
        cell.number_format = MONEY_FORMAT


def _look(block: str) -> tuple[str, str, str]:
    """블록 이름 → (채움색, 글자색, 안내)."""
    if block == EXTRA_BLOCK:
        return ("8A8F9C", "FFFFFF",
                "우리 양식에 없어 원래 이름 그대로 오른쪽에 옮겨 둔 열입니다.")
    _part, fill, ink, note = BLOCKS[block]
    return fill, ink, note


def _part_of(block: str) -> str:
    return EXTRA_PART if block == EXTRA_BLOCK else BLOCKS[block][0]


def _sheet(wb, name: str, columns: list, first_row: int = FIRST_DATA_ROW,
           second: dict | None = None,
           rows: list[dict] | None = None, extras: tuple[str, ...] = (),
           examples: list[dict] | None = None):
    """명부 시트 한 장.

    줄 차례는 파트(2) → 블록(3) → 열 이름(4) → 자료(5). 파트 줄이 있어야
    "우리 회사는 근속포상이 없는데 이 칸도 채워야 하나" 를 열마다 되묻지 않는다.

    :param rows: 채워 넣을 자료. 주면 작성 예시 두 줄 대신 이것을 적는다.
        열쇠는 **열 이름** 이다(:data:`ACTIVE` 의 두 번째 항목).
    :param extras: 고정 서식에 없는 열. 오른쪽에 덧붙인다 — 회사가 누진 보전·
        지급구간처럼 자기네 열을 더해 보내는 모양 그대로다. 기타장기 파트보다
        더 오른쪽에 붙으므로, 파트를 통째로 지우는 손질과 부딪히지 않는다.
    :param examples: 노란 작성 예시 줄들. 열 예시 두 줄 대신 이것을 쓴다.
    """
    columns = list(columns) + [(EXTRA_BLOCK, label, "", "", "") for label in extras]

    ws = wb.create_sheet(name)
    ws.cell(1, 1, f"{name} — 색이 진한 앞쪽 열이 필수입니다."
                  + ("" if rows else " 노란 줄은 작성 예시이니 지우고 쓰세요."))
    ws.cell(1, 1).font = Font(name=FACE, size=9, italic=True, color="5B6478")

    def band(row: int, name_of, colors) -> None:
        """같은 값이 이어지는 구간마다 이름을 얹는다.

        병합 대신 **'선택 영역의 가운데로'** 정렬을 쓴다. 고정(freeze)된
        머리글 행에 병합 칸이 있으면 엑셀 제한된 보기에서 그리기가 깨져,
        멀쩡한 파일인데 글자가 군데군데 사라져 보였다. 이 정렬은 병합 없이
        같은 모양을 낸다 — 값은 구간 첫 칸에만 있고, 빈 칸들이 이어지는
        동안 가운데로 그려진다.
        """
        start = 1
        for index in range(1, len(columns) + 1):
            here = name_of(columns[index - 1][0])
            last = index == len(columns)
            if last or name_of(columns[index][0]) != here:
                fill, ink = colors(here)
                cell = ws.cell(row, start, here)
                cell.font = Font(name=FACE, size=9, bold=True, color=ink)
                for span in range(start, index + 1):
                    target = ws.cell(row, span)
                    target.fill = PatternFill("solid", fgColor=fill)
                    target.border = BORDER
                    target.alignment = Alignment(horizontal="centerContinuous",
                                                 vertical="center")
                start = index + 1

    # 2행: 파트. 3행: 블록. 어디까지가 필수이고 어디부터 지워도 되는지 한눈에.
    band(PART_ROW, _part_of,
         lambda part: (PARTS[part][0] if part in PARTS else "8A8F9C", "FFFFFF"))
    band(BLOCK_ROW, lambda block: block, lambda block: _look(block)[:2])

    for index, (block, label, _mean, sample, _blank) in enumerate(columns, start=1):
        fill, ink = _look(block)[:2]
        cell = ws.cell(HEADER_ROW, index, label)
        cell.font = Font(name=FACE, size=9, bold=True, color=ink)
        cell.fill = PatternFill("solid", fgColor=fill)
        # 자동 줄바꿈은 끈다. 고정된 머리글 행에서 줄바꿈 켜진 칸이 엑셀
        # 제한된 보기에서 글자를 안 그리는 일이 있었다 — 끄니 잘 보인다.
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=False)
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(index)].width = max(11, min(18, len(label) + 5))

        if rows is None and examples is None:
            sample_cell = ws.cell(first_row, index, sample)
            sample_cell.font = Font(name=FACE, size=9, color="9C6500")
            sample_cell.fill = PatternFill("solid", fgColor="FFF2CC")
            sample_cell.border = BORDER
            _as_money(sample_cell, label)

    if rows is None and examples is not None:
        where = {label: index for index, (_b, label, *_r) in enumerate(columns, start=1)}
        for offset, record in enumerate(examples):
            for column in range(1, len(columns) + 1):
                cell = ws.cell(first_row + offset, column,
                               record.get(columns[column - 1][1]))
                cell.font = Font(name=FACE, size=9, color="9C6500")
                cell.fill = PatternFill("solid", fgColor="FFF2CC")
                cell.border = BORDER
                _as_money(cell, columns[column - 1][1])
    elif rows is None:
        for index, (block, label, _mean, _sample, _blank) in enumerate(columns, start=1):
            value = (second or {}).get(label)
            cell = ws.cell(first_row + 1, index, value)
            cell.font = Font(name=FACE, size=9, color="9C6500")
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
            cell.border = BORDER
            _as_money(cell, label)
    else:
        where = {label: index for index, (_b, label, *_r) in enumerate(columns, start=1)}
        body = Font(name=FACE, size=9)
        for offset, record in enumerate(rows):
            row = first_row + offset
            for label, value in record.items():
                index = where.get(label)
                if index is None or value == "":
                    continue
                cell = ws.cell(row, index, value)
                cell.font = body
                _as_money(cell, label)

    # 이어 적을 빈 줄을 미리 그어 둔다. 선이 예시 줄에서 끊기면 그 아래가
    # 표 밖처럼 보여, 사람이 늘어날 때 어디에 적어야 할지 되묻게 된다.
    #
    # 금액 서식도 그 빈 줄에 미리 걸어 둔다. 없으면 회사가 이어 적는 순간부터
    # 콤마가 사라져, 위 줄은 5,000,000 인데 아래 줄은 5000000 으로 보인다 —
    # 그 상태로 검토하면 자릿수를 눈이 먼저 속는다.
    written = (len(rows) if rows is not None
               else (len(examples) if examples is not None else 2))
    for offset in range(written, written + 4):
        for column, (_block, label, *_rest) in enumerate(columns, start=1):
            cell = ws.cell(first_row + offset, column)
            cell.border = BORDER
            _as_money(cell, label)

    ws.row_dimensions[HEADER_ROW].height = 22
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 1)
    return ws


def _guide(wb) -> None:
    ws = wb.create_sheet("작성요령", 0)
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 13
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 56
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 34

    ws["A1"] = "명부 작성요령"
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color="1F3864")
    ws["A2"] = ("필수(남색) 열만 채우면 산출됩니다. 나머지는 해당자만 기입하십시오 — "
                "비워 두면 아래 '비우면' 대로 처리합니다.")
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")
    ws["A3"] = ("우리 회사에 해당 없는 블록은 열째 지우고 보내도 됩니다. "
                "머리글 이름으로 열을 찾으므로 순서를 바꿔도 됩니다.")
    ws["A3"].font = Font(name=FACE, size=9, color="5B6478")
    ws["A4"] = ("명부 시트는 한 장이지만 열이 [퇴직급여] 와 [기타장기] 두 파트로 "
                "갈려 있습니다(맨 윗줄). 근속포상·장기근속휴가가 없으면 "
                "[기타장기] 는 통째로 비우거나 지우고 보내시면 됩니다. "
                "명부와 표는 줄을 얼마든지 늘려 쓰셔도 됩니다.")
    ws["A4"].font = Font(name=FACE, size=9, color="5B6478")

    row = 6
    ws.cell(row, 1, "시트").font = Font(name=FACE, size=11, bold=True, color="1F3864")
    row += 1
    for name, what in (
        ("기초자료", "회사·기간, 직군 규칙, 퇴직급여 지급규정, 특이사항, "
                  "장기급여 규정을 한 장에 모았습니다"),
        ("예치금", "신탁·보험 명세서의 증감표와 구성내역. 순확정급여부채가 여기서 나옵니다"),
        ("재직자명부", "기준일 현재 재직 중인 사람"),
        ("퇴직자명부", "기중에 퇴직·전출·DC전환한 사람"),
        ("추가명부", "축소·정산·사업결합·분할이 있었으면 그 사람들. 없으면 비워 두세요"),
        ("전년명부", "전기말 재직자. 열은 재직자명부와 같습니다. 넣어 주시면 "
                  "신규·퇴사 인원이 제대로 반영됐는지 맞대어 봅니다"),
    ):
        ws.cell(row, 1, name).font = Font(name=FACE, size=9, bold=True)
        ws.cell(row, 2, what).font = Font(name=FACE, size=9, color="5B6478")
        row += 1
    row += 1

    ws.cell(row, 1, "명부 열 파트").font = Font(name=FACE, size=11, bold=True, color="1F3864")
    row += 1
    for part, (fill, note) in PARTS.items():
        cell = ws.cell(row, 1, part)
        cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.border = BORDER
        _note(ws, row, 2, note)
        row += 1
    row += 1

    ws.cell(row, 1, "명부 열 블록").font = Font(name=FACE, size=11, bold=True, color="1F3864")
    row += 1
    for block, (part, fill, ink, note) in BLOCKS.items():
        cell = ws.cell(row, 1, block)
        cell.font = Font(name=FACE, size=9, bold=True, color=ink)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.border = BORDER
        _note(ws, row, 2, f"[{part}] {note}")
        row += 1
    row += 1

    for title, columns in (("재직자명부", ACTIVE), ("퇴직자명부", RETIRED),
                           ("추가명부", EXTRA)):
        ws.cell(row, 1, f"{title} ({len(columns)}열)").font = Font(
            name=FACE, size=11, bold=True, color="1F3864")
        row += 1
        for index, head in enumerate(("파트", "구분", "열 이름", "뜻", "예시", "비우면")):
            cell = ws.cell(row, index + 1, head)
            cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="44546A")
            cell.border = BORDER
        row += 1
        for block, label, meaning, sample, blank in columns:
            part = _part_of(block)
            ws.cell(row, 1, part).font = Font(name=FACE, size=9, color=PARTS[part][0])
            ws.cell(row, 2, block).font = Font(name=FACE, size=9, color=_look(block)[0])
            ws.cell(row, 3, label).font = Font(name=FACE, size=9, bold=block == "필수")
            ws.cell(row, 4, meaning).font = Font(name=FACE, size=9)
            ws.cell(row, 5, sample).font = Font(name=FACE, size=9, color="9C6500")
            _as_money(ws.cell(row, 5), label)
            ws.cell(row, 6, blank).font = Font(name=FACE, size=9, color="5B6478")
            for column in range(1, 7):
                ws.cell(row, column).border = BORDER
                ws.cell(row, column).alignment = Alignment(vertical="top", wrap_text=True)
            row += 1
        row += 2



# ── 입력 시트의 공통 어법 ────────────────────────────────────────
# 명부와 같은 말투로 그린다. 색 띠 하나에 블록 하나, 그 아래 표. 명부에서는
# 블록이 가로(열)로 늘어서고 입력 시트에서는 세로(행)로 쌓일 뿐이다.

#: 입력 시트의 공통 열 너비. 세 블록이 한 장에 얹히므로 하나로 맞춘다.
INPUT_WIDTHS = (("A", 24), ("B", 30), ("C", 22), ("D", 20), ("E", 22), ("F", 46))

#: 블록 띠 색. 명부 블록과 같은 값을 쓴다 — 두 파일이 한 벌로 보여야 한다.
BAND_COLOURS = ("1F3864", "2E6F6A", "8F6318", "5B6478", "7A5470")


def _prepare(ws, title: str, note: str, *, ink: str = "1F3864") -> int:
    """시트 머리(제목·안내)를 얹고 첫 블록이 시작할 행을 돌려준다."""
    for column, width in INPUT_WIDTHS:
        ws.column_dimensions[column].width = width
    ws["A1"] = title
    ws["A1"].font = Font(name=FACE, size=14, bold=True, color=ink)
    ws["A2"] = note
    ws["A2"].font = Font(name=FACE, size=9, color="5B6478")
    return 4


def _wrapped_height(note: str) -> float:
    """병합된 안내 칸의 줄 높이.

    엑셀은 **병합된 칸의 높이를 자동으로 맞추지 않는다.** 자동 줄바꿈을 켜
    두어도 줄 수만 늘 뿐 높이는 그대로라, 안내가 한 줄을 넘어가면 뒷부분이
    보이지 않는다 — 파일을 여는 사람은 잘렸다는 사실조차 모른다. 그래서
    글자 수로 줄 수를 세어 높이를 직접 잡는다.

    한글은 엑셀 폭 단위로 영문 두 배를 차지하므로 그렇게 센다. 넉넉히 잡는
    쪽으로 어림한다 — 남는 여백은 눈에 거슬리지 않지만 잘린 글자는 사고다.
    """
    span = sum(width for _column, width in INPUT_WIDTHS)
    each = sum(2 if ord(letter) > 0x2000 else 1 for letter in note)
    lines = max(1, -(-each // max(1, int(span * 0.95))))
    return max(15.0, lines * 14.5)


def _band(ws, row: int, title: str, colour: str, note: str = "") -> int:
    """블록 띠 한 줄. 명부의 블록 머리와 같은 모양이다."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    cell = ws.cell(row, 1, title)
    cell.font = Font(name=FACE, size=10, bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor=colour)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[row].height = 20
    row += 1
    if note:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        cell = ws.cell(row, 1, note)
        cell.font = Font(name=FACE, size=9, color="5B6478")
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[row].height = _wrapped_height(note)
        row += 1
    return row


def _heads(ws, row: int, columns: tuple[tuple[int, str], ...],
           colour: str = "44546A", spans: dict[int, int] | None = None) -> int:
    """표 머리글 한 줄."""
    for column, label in columns:
        end = (spans or {}).get(column)
        if end:
            ws.merge_cells(start_row=row, start_column=column,
                           end_row=row, end_column=end)
        cell = ws.cell(row, column, label)
        cell.font = Font(name=FACE, size=9, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=colour)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
        for span in range(column, (end or column) + 1):
            ws.cell(row, span).border = BORDER
            ws.cell(row, span).fill = PatternFill("solid", fgColor=colour)
    return row + 1


def _write(ws, row: int, column: int, value, *, span: int = 0, bold: bool = False,
           filled: bool = True, money: bool = False, centre: bool = False):
    """자료 칸 하나. 빈 양식이면 노랗게 칠해 '여기를 기입하십시오' 로 보이게 한다."""
    if span:
        ws.merge_cells(start_row=row, start_column=column, end_row=row, end_column=span)
    cell = ws.cell(row, column, value)
    cell.font = Font(name=FACE, size=9, bold=bold,
                     color="000000" if filled else "9C6500")
    for index in range(column, (span or column) + 1):
        target = ws.cell(row, index)
        target.border = BORDER
        if not filled:
            target.fill = PatternFill("solid", fgColor="FFF2CC")
    if money:
        cell.number_format = "#,##0"
    cell.alignment = Alignment(
        horizontal="center" if centre else "general", vertical="top", wrap_text=True)
    return cell


def _note(ws, row: int, column: int, text_: str, span: int = 6, *,
          boxed: bool = True) -> None:
    """표 오른쪽의 '적는 법' 칸.

    :param boxed: 표 밖의 안내 줄이면 끈다. 표 안이면 테두리를 둘러야 한다 —
        한 줄만 선이 끊겨 있으면 그 줄이 표 밖인지 안인지 알 수 없다.
    """
    if span > column:
        ws.merge_cells(start_row=row, start_column=column, end_row=row, end_column=span)
    ws.cell(row, column, text_).font = Font(name=FACE, size=9, color="5B6478")
    ws.cell(row, column).alignment = Alignment(vertical="top", wrap_text=True)
    if boxed:
        for index in range(column, max(span, column) + 1):
            ws.cell(row, index).border = BORDER


def _basics(ws, row: int, *, values: dict | None = None,
            groups: list | None = None) -> int:
    """① 회사·기간 과 ② 직군 규칙.

    예전 ``Input`` 시트다. 'C3 에 넣으세요' 대신 이름을 붙인다.

    :param values: 항목 이름 → 값. 주면 예시 대신 이 값을 적는다.
    :param groups: 직군 규칙 표의 줄들. ``(명부 직군, 산출 직군, 정년, 장기급여
        정년, 가산연수)``.
    """
    filled = values is not None

    row = _band(ws, row, "① 회사와 회계기간", BAND_COLOURS[0],
                "결산일과 직전 결산일 다음 날을 적어 주세요. 이 기간으로 이자원가를 환산합니다.")
    row = _heads(ws, row, ((1, "항목"), (2, "값"), (3, "적는 법")), spans={3: 6})
    rows = [
        ("단체명", "○○주식회사", "보고서 표지에 올라갑니다"),
        ("산출기준일", "2025-12-31", "결산일. 비우면 프로그램 화면에서 넣어도 됩니다"),
        ("산출 시작일", "2025-01-01", "직전 결산일 다음 날"),
        ("상시근로자 수", 275, "표준률의 300인 미만/이상을 고르는 데 씁니다"),
        ("회사채 신용등급", "AA-", "할인율로 쓸 회사채 등급. AAA·AA+·AA0·AA-·A+·A0·A- 중"),
        ("평균임금 하한 점검액", 0, "이보다 낮은 평균임금을 오류로 봅니다. 0 이면 점검 안 함"),
    ]
    if filled:
        rows = [(label, values.get(label, value), hint) for label, value, hint in rows]
    # 인원과 금액은 세 자리로 끊는다. 여기 적는 값은 날짜·등급과 섞여 있어
    # 열 단위로 걸 수가 없으므로 항목 이름으로 가른다.
    grouped = {"상시근로자 수", "평균임금 하한 점검액"}
    for label, value, hint in rows:
        _write(ws, row, 1, label, bold=True)
        _write(ws, row, 2, value, filled=filled, money=label in grouped)
        _note(ws, row, 3, hint)
        row += 1

    row += 1
    # 열 설명은 **표 위 안내줄에 모아** 둔다. 예전에는 열마다 한 문장씩 떼어
    # 첫째·둘째·셋째 줄의 [적는 법] 칸에 하나씩 넣었는데, 그러면 '정규직' 줄
    # 옆에 "명부의 직군 칸에 적힌 그대로" 가 붙는다 — 그 줄에 대한 설명처럼
    # 읽히지만 사실은 첫 열에 대한 설명이라, 읽는 사람이 매번 되짚어야 했다.
    # 줄마다 다른 말이 나오는 것도 규칙이 있는 것처럼 보여 더 헷갈렸다.
    row = _band(ws, row, "② 직군 규칙", BAND_COLOURS[1],
                "명부에 적은 직군을 산출에 쓸 묶음으로 배정합니다. "
                "[명부 직군] 은 명부의 직군 칸에 적힌 그대로, [산출 직군] 은 "
                "그 이름으로 묶어 산출합니다. 정년이 다르면 여기서 나눕니다 — "
                "[정년연령] 을 비우면 정년이 없다는 뜻이므로 현재 연령에 "
                "[정년초과 가산연수] 를 더합니다. 아래 직군 이름은 예시이며, "
                "생산직·관리직 등 회사에 있는 직군으로 줄을 늘려 기재하십시오.")
    row = _heads(ws, row, (
        (1, "명부 직군"), (2, "산출 직군"), (3, "정년연령"),
        (4, "장기급여 정년"), (5, "정년초과 가산연수"), (6, "적는 법")))
    table = groups or [
        ("정규직", "정규직", 60, 60, 2),
        ("계약직", "계약직", 60, 60, 2),
        # 임원 정년은 회사마다 다르다. 60 을 미리 적어 두면 그 값이 사실인 것처럼
        # 굳어 버리므로 **비워 둔다** — 비우면 정년이 없다는 뜻이 된다.
        ("임원", "임원", None, None, 2),
    ]
    for line in table:
        for index, value in enumerate(line, start=1):
            _write(ws, row, index, value, filled=filled,
                   centre=index >= 3, money=index >= 3)
        # [적는 법] 은 회사가 필요하면 적는 빈 칸이다. 테두리만 둘러 표 안임을
        # 보이고 내용은 넣지 않는다.
        _note(ws, row, 6, "")
        row += 1
    # 이어 적을 빈 줄. 표가 어디까지인지 보이지 않으면 어디에 쓸지 모른다.
    for _ in range(3):
        for column in range(1, 6):
            ws.cell(row, column).border = BORDER
        row += 1
    return row + 1
# ── 퇴직급여규정 · 특이사항 ──────────────────────────────────────

#: 지급규정 항목. (라벨, 예시, 설명)
RULE_ROWS = [
    ("가입자격", "전 임직원", "근속 1년 이상 등 제한이 있으면 그대로 기재하십시오"),
    ("근속기간 산정", "월할 계산, 1년 미만 단수개월 절사",
     "일할 / 월할 / 분기할 / 반기할 / 연할 중 어느 것인지"),
    ("계산구조", "ROUND(평균임금 × 근속연수 × 지급률, -1)",
     "수식 그대로 적어 주시면 됩니다"),
    ("기준임금", "퇴직 직전 3개월 평균임금", "무엇을 급여 기준으로 삼는지"),
    ("정년 (직원)", "만 60세", ""),
    ("정년 (임원)", "없음", "정년이 없으면 '없음' 이라고 기재하십시오"),
    ("정년 퇴직일", "정년에 이른 날이 속한 사업연도 말일",
     "'만 60세가 되는 날' 인지 '그 해 연말' 인지. 상반기 생일자가 많으면 "
     "둘의 차이가 채무에 그대로 나옵니다"),
    ("중도퇴직 지급률", "근속연수 × 1.0", "사유별로 배수가 다르면 각각 기재하십시오"),
    ("사망퇴직 지급률", "근속연수 × 1.0", ""),
    ("정년퇴직 지급률", "근속연수 × 1.0", ""),
    ("지급방법", "일시금", "일시금 / 연금 / 선택"),
]

#: 특이사항. (구분, 예시)
SPECIAL_ROWS = [
    ("제도 변경", "2023-01-01 부터 누진제 폐지, 그 전 근속분은 종전 지급률 유지"),
    ("명예퇴직", "만 55세 이상·근속 20년 이상 대상, 잔여 정년 개월수 × 기본급"),
    ("임금피크", "만 57세부터 매년 10% 감액"),
    ("중간정산", "2016년 전원 중간정산, 근속을 그 날부터 다시 셈"),
    ("임원", "임원 퇴직금 지급규정 별도. 세법 한도 초과분 동결"),
    ("DC 전환", "2024년 신입부터 DC. 전환자는 전환일 기준으로 정산 완료"),
    ("장기급여", "근속 10·20·30년에 포상금, 20년에 장기근속휴가 10일"),
    ("그 밖에", "산출에 영향을 줄 만한 것은 모두 적어 주세요"),
]


def _rules(ws, row: int, *, filled: bool = False,
           specials: dict[str, str] | None = None) -> int:
    """③ 퇴직급여 지급규정 과 ④ 특이사항.

    :param specials: 특이사항 구분 → 내용. 주면 그 줄을 채운 채로 낸다.
    """
    row = _band(ws, row, "③ 퇴직급여 지급규정", BAND_COLOURS[2],
                "규정집을 그대로 옮길 필요는 없습니다. 아래 항목만 한 줄씩 적어 주세요 — "
                "여기 적힌 대로 산출가정을 세우고 보고서에 근거로 남깁니다.")
    row = _heads(ws, row, ((1, "항목"), (2, "내용"), (4, "적는 법")),
                 spans={2: 3, 4: 6})
    for label, sample, hint in RULE_ROWS:
        _write(ws, row, 1, label, bold=True)
        _write(ws, row, 2, sample, span=3, filled=filled)
        _note(ws, row, 4, hint)
        row += 1

    row += 1
    row = _band(ws, row, "④ 특이사항", BAND_COLOURS[3],
                "해당 없으면 비워 두세요. 오른쪽은 예시일 뿐 회사 사실이 아닙니다.")
    row = _heads(ws, row, ((1, "구분"), (2, "내용"), (4, "이런 것을 적습니다")),
                 spans={2: 3, 4: 6})
    for label, sample in SPECIAL_ROWS:
        _write(ws, row, 1, label, bold=True)
        written = (specials or {}).get(label, "") if specials is not None else ""
        _write(ws, row, 2, written, span=3, filled=True)
        _note(ws, row, 4, sample)
        ws.row_dimensions[row].height = 22
        row += 1
    # 고정 구분에 없는 특이사항은 이어서 적는다. 특이케이스 명부의 문제지가
    # 여기로 실린다 — 회사가 제 구분을 만들어 보내도 같은 길로 들어온다.
    known = {label for label, _sample in SPECIAL_ROWS}
    for label, written in (specials or {}).items():
        if label in known:
            continue
        _write(ws, row, 1, label, bold=True)
        _write(ws, row, 2, written, span=6, filled=True)
        ws.row_dimensions[row].height = 22
        row += 1
    return row + 1
# ── 장기급여(근속포상) 규정 ─────────────────────────────────────

#: 규정 개요. (항목, 예시, 적는 법)
LONGTERM_RULE_ROWS = [
    ("대상", "근속 10년 이상 전 직원",
     "직군마다 다르면 각각 기재하십시오. 명부의 '장기급여 대상' 칸과 맞아야 합니다"),
    ("근속 기산일", "입사일",
     "중간정산과 무관합니다. 다른 날부터 센다면 명부의 '장기급여 기산일' 을 기입하십시오"),
    ("지급시점", "근속 도달", f"{' / '.join(('근속도달시', '퇴직시', '정년시'))} 중"),
    ("지급일", "창립기념일 (10-01)",
     "도달 즉시가 아니라 정해진 날에 몰아 준다면 그 날짜. 없으면 비우세요"),
    ("반복 지급", "30년 넘으면 5년마다 한 번 더", "되풀이가 없으면 '없음'"),
    ("이월", "받은 휴가는 그해 소멸, 이월 없음",
     "안 쓰고 모아 두었다가 퇴직할 때 정산하면 '이월' 이라고 기재하십시오"),
    ("기준임금", "일 기본급 (통상임금 ÷ 209 × 8)",
     "휴가·포상을 금액으로 바꿀 때 무엇을 쓰는지"),
    ("현물 시세", "금 1돈 450,000원 (2025-12-31 기준)",
     "금·물품으로 준다면 평가시점 시세와 그 기준일. 없으면 비우세요"),
    ("기중 지급액", "38,000,000원",
     "이번 회계기간에 실제로 나간 근속포상 총액. [사외적립자산] 시트에도 있습니다"),
]

#: 근속별 지급 내용. (규정명, 근속년수, 지급 내용, 지급방법, 지급기준)
#: 한 근속에 성격이 다른 급여가 둘 이상 걸리면 **줄을 나눠** 적는다 — 휴가와
#: 현물은 금액으로 바꾸는 방법도, 해마다 올리는 방법도 다르다.
LONGTERM_SCALE_ROWS = [
    ("정규직포상", 10, "유급휴가 5일", "휴가", "일 기본급 × 5"),
    ("정규직포상", 20, "유급휴가 10일", "휴가", "일 기본급 × 10"),
    ("정규직포상", 20, "순금 10돈", "현물", "평가시점 시세 4,500,000원"),
    ("정규직포상", 30, "30일 평균임금의 300%", "평균임금", "30일 평균임금 × 3"),
    ("임원포상", 10, "기념패 및 300만원", "현금", "정액 3,000,000원"),
]

#: 지급방법 낱말. 산출가정의 ``장기급여규정`` 시트가 그대로 받는 말이라,
#: 여기서 다른 말로 적어 받으면 우리가 손으로 옮기며 뜻을 바꾸게 된다.
LONGTERM_KINDS: tuple[str, ...] = ("휴가", "평균임금", "현물", "현금")


def _longterm(ws, row: int, *, filled: bool = False, rows: list | None = None,
              overview: dict[str, str] | None = None) -> int:
    """⑤ 장기급여(근속포상·장기근속휴가) 규정.

    퇴직급여와 성격이 달라 블록을 나눴다. 근속포상이 없는 회사가 대부분인데,
    퇴직급여규정 안에 한 칸으로 끼워 두면 "우리는 없는데 뭘 적으라는 건지" 로
    읽히고, 있는 회사는 한 칸에 다 못 적어 규정집을 첨부로 보냈다. 어느 쪽이든
    우리가 되묻게 된다.

    :param rows: 근속별 지급 내용 줄들. 주면 예시 대신 이것을 적는다.
    :param overview: 규정 개요 항목 → 내용.
    """
    filled = filled or rows is not None or overview is not None

    row = _band(ws, row, "⑤ 장기급여(근속포상) 규정  ※ 기타장기", BAND_COLOURS[4],
                "근속포상·장기근속휴가가 없으면 이 블록은 통째로 비워 두시면 됩니다. "
                "명부의 [기타장기] 파트도 함께 비우세요.")
    row = _heads(ws, row, ((1, "항목"), (2, "내용"), (4, "적는 법")),
                 colour=BAND_COLOURS[4], spans={2: 3, 4: 6})
    for label, sample, hint in LONGTERM_RULE_ROWS:
        _write(ws, row, 1, label, bold=True)
        # 빈 양식에도 예시를 노랗게 남긴다. 항목 이름만 있으면 '대상' 칸에
        # 무엇을 몇 줄로 적어야 하는지 알 수 없어, 대개 비워서 돌아온다.
        _write(ws, row, 2, (overview or {}).get(label, sample), span=3, filled=filled)
        _note(ws, row, 4, hint)
        row += 1

    row += 1
    _note(ws, row, 1, "근속별 지급 내용 — 한 근속에 휴가와 현물이 함께 걸리면 "
                      "줄을 나눠 적어 주세요. 줄은 얼마든지 늘려도 됩니다.",
          boxed=False)
    row += 1
    row = _heads(ws, row, (
        (1, "규정명"), (2, "근속년수"), (3, "지급 내용"),
        (4, f"지급방법 ({' / '.join(LONGTERM_KINDS)})"), (5, "지급기준 (금액 환산)")),
        colour=BAND_COLOURS[4], spans={5: 6})
    ws.row_dimensions[row - 1].height = 26

    given = rows if rows is not None else LONGTERM_SCALE_ROWS
    for rule, service, what, kind, basis in given:
        for column, value in ((1, rule), (2, service), (3, what), (4, kind)):
            _write(ws, row, column, value, filled=filled, centre=column in (2, 4))
        _write(ws, row, 5, basis, span=6, filled=filled)
        row += 1
    for _ in range(4):
        for column in range(1, 7):
            ws.cell(row, column).border = BORDER
        row += 1

    _note(ws, row, 1, "'규정명' 은 명부의 '장기급여 지급률 규정' 또는 직군과 이어집니다. "
                      "규정이 하나뿐이면 아무 이름이나 한 가지로 통일해 주세요.",
          boxed=False)
    return row + 2
# ── 사외적립자산 ────────────────────────────────────────────────

#: 퇴직급여추계액 변동내역. (부호, 항목, 예시금액)
#: 증감 한 표. (부호, 항목, 추계액에 해당?, 예치금에 해당?, 예시추계액, 예시예치금)
#:
#: 종전에는 추계액 증감과 예치금 증감이 표 두 개였는데, 항목 대부분(지급액·
#: 중간정산금·DC전환·전입·합병…)이 양쪽에 똑같이 들어가 담당자가 같은
#: 숫자를 두 번 적었다. 한 표로 합치고 열만 갈랐다 — 해당 없는 칸은 회색으로
#: 막아 두어 "여기는 적는 칸이 아니다" 가 보이게 한다.
MOVEMENT_ROWS = [
    ("(+)", "부담금 납입액",          False, True,  0,             1_500_000_000),
    ("(+)", "이자수익",              False, True,  0,             420_000_000),
    # 추계액은 그 해에 **자라기도** 한다. 이 줄이 없으면 기초에서 지급액만 빼게
    # 되어 기말과 절대 맞지 않고, 검산 자체가 성립하지 않는다.
    ("(+)", "당기 추계액 증가",        True,  False, 24_000_000,    0),
    ("(+)", "합병 인수액",            True,  True,  0,             0),
    ("(+)", "계열사 전입",            True,  True,  0,             0),
    ("(-)", "퇴직급여 지급액",         True,  True,  165_000_000,   158_000_000),
    ("(-)", "중간정산금",             True,  True,  120_000_000,   120_000_000),
    ("(-)", "DC전환 지급액",          True,  True,  0,             0),
    ("(-)", "퇴직위로금 (명예퇴직금 등)", True,  False, 0,             0),
    ("(-)", "계열사 전출",            True,  True,  0,             0),
    ("(-)", "사업처분·분할",           True,  True,  0,             0),
    ("(-)", "운용관리수수료",          False, True,  0,             18_000_000),
    ("(-)", "자산관리수수료",          False, True,  0,             9_000_000),
]

#: 옛 두-표 서식을 읽는 쪽과 시험이 아직 참조한다. 표는 하나가 됐지만 낱말은
#: 그대로여야 한다.
OBLIGATION_ROWS = [(sign, name, ob) for sign, name, has_ob, _a, ob, _av in MOVEMENT_ROWS
                   if has_ob]
ASSET_ROWS = [(sign, name, av, 0) for sign, name, _o, has_asset, _ov, av in MOVEMENT_ROWS
              if has_asset]

#: 추계액 증감표의 (기초, 기말). **이것이 명부 검산이다.**
#:
#: 사람별 추계액만 맞대면 명부에서 아예 빠진 사람은 비교 대상이 없어 걸리지
#: 않는다. 기초에서 출발해 그 해에 드나든 것을 더하고 빼면 기말이 나와야 하고,
#: 그 기말은 명부 추계액의 합과 같아야 한다 — 한 사람이 통째로 빠지면 이 두
#: 줄이 그 사람의 추계액만큼 어긋난다.
#:
#: 예시 값은 **손으로 적지 않고 되짚어 만든다.** 기말은 작성 예시 두 사람의
#: 추계액 합계 그 자체이고, 기초는 거기서 그 해 유출입을 거꾸로 되짚은 값이다.
#: 숫자를 하나 박아 두면 예시 명부를 고칠 때마다 표가 조용히 어긋난다.
_EXAMPLE_ACCRUED = 79_219_178 + SECOND_ACTIVE["추계액"]
_EXAMPLE_MOVED = sum(
    amount if sign == "(-)" else -amount
    for sign, _name, has_ob, _a, amount, _av in MOVEMENT_ROWS if has_ob
)
OBLIGATION_ENDS = (_EXAMPLE_ACCRUED + _EXAMPLE_MOVED, _EXAMPLE_ACCRUED)

ASSET_OPENING = (12_000_000_000, 300_000_000)
#: 기말은 신탁 명세서의 숫자를 그대로 적는 자리다. 검증 줄이 0 이 되는 값.
ASSET_CLOSING = (13_615_000_000, 300_000_000)

#: 자산 분류별 공정가치 (문단 142 공시). (분류, 금액, 활성시장 공시가격)
#:
#: 문단 142 는 분류만으로 끝나지 않는다 — 각 분류를 **활성시장에 공시가격이
#: 있는 것과 없는 것으로 다시 나누라** 고 한다. 정기예금·GIC 처럼 시세가 없는
#: 자산이 자산의 대부분인 회사가 흔한데, 나누지 않으면 그 사실이 공시에
#: 드러나지 않는다.
ASSET_BREAKDOWN = [
    ("현금 및 현금성자산", 150_000_000, "있음"),
    ("정기예금·원리금보장 GIC", 10_622_000_000, "없음"),
    ("국공채", 1_500_000_000, "있음"),
    ("특수채·금융채", 700_000_000, "있음"),
    ("회사채", 400_000_000, "없음"),
    ("수익증권 (펀드)", 543_000_000, "있음"),
    ("그 밖의 자산", 0, "없음"),
]

#: 활성시장 공시가격 유무를 적는 말. 읽는 쪽과 같은 낱말이어야 한다.
QUOTED_WORDS: tuple[str, str] = ("있음", "없음")


def _assets(wb, *, filled: bool = False, numbers: dict | None = None) -> None:
    """[예치금] 시트 — 증감표와 구성내역.

    시트 이름이 종전에는 ``사외적립자산`` 이었다. 퇴직연금을 들지 않고 퇴직금
    제도만 둔 단체는 그 말을 쓰지 않아, 무엇을 적는 칸인지 알기 어려웠다.

    표 제목과 항목 이름은 읽는 쪽(:mod:`pension.general_info`)이 찾는 말과
    맞춰 두었다. 옛 이름도 계속 읽으므로 지난해 파일이 그대로 돌아간다.

    :param numbers: 채워 넣을 금액. 열쇠는 ``obligation``(항목→금액),
        ``asset``(항목→(예치금, 국민연금전환금)), ``opening``·``closing``(둘의 짝),
        ``breakdown``(분류→금액), ``extras``(항목→금액). 주면 예시 대신 이것을
        적는다. 검산줄이 0 이 되도록 **부르는 쪽이** 기말을 역산해 넘겨야 한다.
    """
    filled = filled or numbers is not None
    numbers = numbers or {}
    obligation_of = numbers.get("obligation")
    asset_of = numbers.get("asset")

    ws = wb.create_sheet("예치금", 2)
    row = _prepare(
        ws, "예치금",
        "신탁·보험 명세서의 숫자를 그대로 옮겨 주세요. 노란 칸이 입력, "
        "굵은 칸은 자동 계산입니다. ① 맨 아랫줄이 0 이어야 합니다.")
    # 부호 칸은 좁게. 항목 이름이 왼쪽 끝에 붙어야 표가 한 덩어리로 읽힌다.
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 32

    def money(row: int, column: int, value, *, formula: bool = False):
        cell = ws.cell(row, column, value)
        cell.number_format = "#,##0"
        cell.border = BORDER
        cell.font = Font(name=FACE, size=9, bold=formula,
                         color="000000" if (formula or filled) else "9C6500")
        if not formula and not filled:
            cell.fill = PatternFill("solid", fgColor="FFF2CC")
        return cell

    def label(row: int, sign: str, text_: str) -> None:
        sign_cell = ws.cell(row, 1, sign)
        sign_cell.font = Font(name=FACE, size=9, color="5B6478")
        sign_cell.alignment = Alignment(horizontal="center")
        sign_cell.border = BORDER
        cell = ws.cell(row, 2, text_)
        cell.font = Font(name=FACE, size=9)
        cell.border = BORDER

    #: 그 축에 해당 없는 칸의 회색. "여기는 적는 칸이 아니다" 가 보여야 한다.
    blocked = PatternFill("solid", fgColor="E4E7ED")

    def na(row: int, column: int) -> None:
        cell = ws.cell(row, column)
        cell.fill = blocked
        cell.border = BORDER

    # ── ① 추계액·예치금 증감 (한 표) ─────────────────────────────
    # 종전에는 추계액 증감과 예치금 증감이 표 두 개였는데, 항목 대부분이
    # 양쪽에 똑같이 들어가 담당자가 같은 숫자를 두 번 적었다. 한 표로 합치고
    # 열만 갈랐다.
    row = _band(ws, row, "① 퇴직급여추계액·예치금 증감", BAND_COLOURS[0],
                "유출입은 모두 양수로 기재하십시오 — 부호는 왼쪽 (+)(−) 가 정합니다. "
                "회색 칸은 그 축에 해당 없는 항목이고, 해당 없는 줄은 0 으로 "
                "두세요 — 줄을 지우면 맨 아랫줄 수식이 어긋납니다.")
    row = _heads(ws, row, (
        (1, ""), (2, "구분"), (3, "퇴직급여추계액"), (4, "예치금"),
        (5, "국민연금전환금"), (6, "적는 법")))

    opening = numbers.get("opening", ASSET_OPENING)
    closing = numbers.get("closing", ASSET_CLOSING)
    obligation_ends = numbers.get("obligation_ends", OBLIGATION_ENDS)

    opening_row = row
    label(opening_row, "", "기초 잔액 (전기말)")
    money(opening_row, 3, obligation_ends[0])
    money(opening_row, 4, opening[0])
    money(opening_row, 5, opening[1])
    _note(ws, opening_row, 6, "전기말 명부의 추계액 합계 · 전기말 명세서의 잔액")

    row = opening_row + 1
    for sign, text_, has_ob, has_asset, ob_amount, asset_amount in MOVEMENT_ROWS:
        label(row, sign, text_)
        if has_ob:
            money(row, 3, ob_amount if obligation_of is None
                  else obligation_of.get(text_, 0))
        else:
            na(row, 3)
        if has_asset:
            db, pension = asset_amount, 0
            if asset_of is not None:
                db, pension = asset_of.get(text_, (0, 0))
            money(row, 4, db)
            money(row, 5, pension)
        else:
            na(row, 4)
            na(row, 5)
        _note(ws, row, 6, "")
        row += 1
    last_move = row - 1

    closing_row = row
    label(closing_row, "", "기말 잔액 (결산일)")
    money(closing_row, 3, obligation_ends[1])
    money(closing_row, 4, closing[0])
    money(closing_row, 5, closing[1])
    _note(ws, closing_row, 6, "결산일 명부의 추계액 합계 · 결산일 명세서의 잔액")

    verify_row = closing_row + 1
    label(verify_row, "", "검증  (기초 + 유입 − 유출 − 기말)")
    # (+) 줄이 몇 개인지는 표에서 센다. 줄을 하나 더 넣고 여기 숫자를 안 고치면
    # 그 줄이 유출로 넘어가 검증이 두 배로 틀어진다.
    plus_count = sum(1 for sign, *_r in MOVEMENT_ROWS if sign == "(+)")
    plus_last = opening_row + plus_count
    for column in "CDE":
        cell = ws.cell(verify_row, ord(column) - 64,
                       f"={column}{opening_row}"
                       f"+SUM({column}{opening_row + 1}:{column}{plus_last})"
                       f"-SUM({column}{plus_last + 1}:{column}{last_move})"
                       f"-{column}{closing_row}")
        cell.number_format = "#,##0;[RED](#,##0)"
        cell.font = Font(name=FACE, size=9, bold=True)
        cell.border = BORDER
    _note(ws, verify_row, 6, "0 이 아니면 보내 주신 표 자체가 맞지 않는다는 뜻입니다")
    row = verify_row + 2

    # ── 명부 대조 (자동) ─────────────────────────────────────────
    # 이 표의 지급액들은 퇴직자명부를 더한 값과 같아야 한다. 다른 자료에서
    # 옮겨 적다 어긋나는 일이 잦아, 시트가 스스로 맞대어 보고 TRUE/FALSE 로
    # 알린다. 수식이라 명부를 고치면 따라 움직인다.
    at = {name: opening_row + 1 + index
          for index, (_s, name, *_r) in enumerate(MOVEMENT_ROWS)}
    where = {label: get_column_letter(index)
             for index, (_b, label, *_r) in enumerate(RETIRED, start=1)}

    def roster_sum(label: str) -> str:
        column = where[label]
        return f"SUM(퇴직자명부!{column}{FIRST_DATA_ROW}:{column}5004)"

    row = _band(ws, row, "② 명부 대조 (자동)", "5B6478",
                "위 표의 지급액과 퇴직자명부를 더한 값이 같은지 스스로 맞대어 "
                "봅니다. FALSE 가 뜨면 어느 한쪽이 빠졌거나 다른 기간의 금액이 "
                "섞인 것입니다. 퇴직자명부의 열 순서를 바꿨다면 맞지 않을 수 "
                "있습니다.")
    row = _heads(ws, row, ((1, ""), (2, "맞대는 것"), (3, "이 표"),
                           (4, "퇴직자명부 합"), (5, "일치"), (6, "적는 법")),
                 colour="5B6478")
    checks = [
        ("퇴직급여 지급액 — 예치금 열",
         f"=D{at['퇴직급여 지급액']}", roster_sum("사외자산 지급액"),
         "사외자산 지급액 열의 합"),
        ("지급액+DC전환+전출·처분 — 추계액 열",
         f"=C{at['퇴직급여 지급액']}+C{at['DC전환 지급액']}"
         f"+C{at['계열사 전출']}+C{at['사업처분·분할']}",
         roster_sum("퇴직급여 총지급액"),
         "총지급액 열의 합 — DC전환·전출자도 총지급액에 적히므로 함께 더해 맞댑니다"),
        ("퇴직위로금 — 추계액 열",
         f"=C{at['퇴직위로금 (명예퇴직금 등)']}", roster_sum("퇴직위로금 등"),
         "퇴직위로금 등 열의 합"),
        ("국민연금전환금 — 지급액 줄",
         f"=E{at['퇴직급여 지급액']}", roster_sum("국민연금 전환금"),
         "국민연금 전환금 열의 합"),
    ]
    for label_text, mine_formula, theirs_sum, hint in checks:
        label(row, "", label_text)
        money(row, 3, mine_formula, formula=True)
        money(row, 4, f"={theirs_sum}", formula=True)
        same = ws.cell(row, 5, f"=(C{row}=D{row})")
        same.font = Font(name=FACE, size=9, bold=True)
        same.border = BORDER
        same.alignment = Alignment(horizontal="center")
        _note(ws, row, 6, hint)
        row += 1
    row += 1

    # ── ③ 예치금 구성 (문단 142) ─────────────────────────────────
    row = _band(ws, row, "③ 예치금 구성  ※ 문단 142 공시", BAND_COLOURS[2],
                "기말 공정가치를 자산 종류별로. 분류마다 활성시장 공시가격이 "
                "있는지도 함께 적어 주세요 — 공시에 그대로 실립니다. 분류가 더 "
                "있으면 줄을 늘려도 됩니다.")
    row = _heads(ws, row, (
        (1, ""), (2, "자산 분류"), (3, "공정가치"),
        (4, "활성시장 공시가격"), (5, "적는 법")), spans={5: 6})
    first_detail = row
    for line in numbers.get("breakdown", ASSET_BREAKDOWN):
        name, amount = line[0], line[1]
        quoted = line[2] if len(line) > 2 else ""
        label(row, "", name)
        money(row, 3, amount)
        # 문단 142 는 분류마다 '활성시장에 공시가격이 있는지' 를 함께 묻는다.
        # 정기예금·GIC 가 자산의 대부분인 회사가 흔한데, 안 나누면 공시에
        # 그 사실이 드러나지 않는다.
        mark = _write(ws, row, 4, quoted, filled=filled, centre=True)
        mark.number_format = "General"
        _note(ws, row, 5, "")
        row += 1
    for _ in range(3):
        for column in range(1, 7):
            ws.cell(row, column).border = BORDER
        row += 1

    label(row, "", "합계")
    ws.cell(row, 2).font = Font(name=FACE, size=9, bold=True)
    money(row, 3, f"=SUM(C{first_detail}:C{row - 1})", formula=True)
    _note(ws, row, 4, f"기말 잔액(C{closing_row}+D{closing_row}) 과 같아야 합니다. "
                      f"공시가격은 {' / '.join(QUOTED_WORDS)} 중 하나로.")
    row += 2

    # ── ④ 그 밖의 입력 ───────────────────────────────────────────
    row = _band(ws, row, "④ 그 밖의 입력", BAND_COLOURS[3],
                "해당 없으면 0 으로 두세요. 자산인식상한은 비워 두면 미적용입니다.")
    row = _heads(ws, row, ((1, ""), (2, "항목"), (3, "금액"), (4, "적는 법")),
                 spans={4: 6})
    extras = [
        ("자산인식상한 (문단 64)", "",
         "초과적립일 때만. 환급·미래부담금 절감으로 얻을 효익의 현가. 비우면 상한 미적용"),
        ("기준일 현재 미지급 퇴직급여", 0,
         "이미 퇴직했는데 결산일까지 못 준 금액. 순확정급여부채에 더합니다"),
        ("기중 장기근속 지급액", 0, "장기급여(근속포상·휴가)로 기중에 나간 금액"),
        # '받은금액' 을 붙여 쓴다 — 프로그램이 찾는 말이 그것이라, 띄우면 못 읽는다.
        ("기중 장기근속 받은금액", 0, "전입 등으로 기중에 들어온 장기급여"),
    ]
    given = numbers.get("extras", {})
    for name, amount, hint in extras:
        label(row, "", name)
        money(row, 3, given.get(name, amount))
        _note(ws, row, 4, hint)
        row += 1
def build_workbook(
    *,
    basics: dict | None = None,
    groups: list | None = None,
    note: str = "",
    rules_filled: bool = False,
    specials: dict[str, str] | None = None,
    longterm: dict[str, str] | None = None,
    longterm_rows: list | None = None,
    numbers: dict | None = None,
    actives: list[dict] | None = None,
    retirees: list[dict] | None = None,
    events: list[dict] | None = None,
    prior: list[dict] | None = None,
    active_extras: tuple[str, ...] = (),
    retired_extras: tuple[str, ...] = (),
):
    """이 양식대로 된 통합문서 하나.

    빈 양식과 시험용 명부가 **같은 서식** 이어야 한다. 따로 만들면 회사에 보낸
    양식과 우리가 시험하는 명부가 서서히 갈라져, 정작 받아 본 파일에서 처음
    어긋난다. 그래서 시트를 만드는 곳은 여기 하나다.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    del wb["Sheet"]
    _guide(wb)

    # 기초자료 한 장. 담당자가 채우는 칸을 여기저기 흩어 두면, 시트를 오가며
    # 채우다가 한 장을 통째로 빼먹는다. 예치금만 따로 두는 것은 그쪽이 신탁·
    # 보험 명세서를 보고 옮기는 일이라 자료의 출처가 다르기 때문이다.
    sheet = wb.create_sheet("기초자료", 1)
    row = _prepare(sheet, "기초자료",
                   note or "노란 칸만 채우면 됩니다. 해당 없는 블록은 비워 두세요.")
    row = _basics(sheet, row, values=basics, groups=groups)
    row = _rules(sheet, row, filled=rules_filled, specials=specials)
    _longterm(sheet, row, filled=rules_filled, rows=longterm_rows,
              overview=longterm)

    _assets(wb, numbers=numbers)
    _sheet(wb, "재직자명부", ACTIVE, second=SECOND_ACTIVE,
           rows=actives, extras=active_extras)
    _sheet(wb, "퇴직자명부", RETIRED, second=SECOND_RETIRED,
           rows=retirees, extras=retired_extras)
    # 추가명부의 예시 줄은 사번에 '(예시)' 를 달아 둔다. 이 시트는 통째로
    # 비어 있는 것이 정상이라 예시가 남은 채 돌아오기 쉬운데, 표시가 있으면
    # 지우지 않고 보내도 산출에서 조용히 빠진다.
    _sheet(wb, "추가명부", EXTRA, rows=events,
           examples=None if events is not None else EXTRA_EXAMPLES)
    # 전기말 재직자. 열은 재직자명부와 똑같다 — 같은 사람들의 한 해 전 모습이라
    # 필요한 항목이 다를 이유가 없고, 회사도 작년 파일을 그대로 붙이면 된다.
    #
    # 작성 예시는 넣지 않는다. 이 시트는 회사가 가진 파일을 통째로 붙이는
    # 자리라, 예시 줄이 남아 있으면 그 사람이 전기 재직자로 섞여 든다.
    _sheet(wb, "전년명부", ACTIVE, rows=prior if prior is not None else [])
    return wb


def label_for(columns: list, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """필드명 → 이 양식의 열 이름.

    :mod:`pension.layout` 의 별칭표와 맞대어 짓는다. 이름을 손으로 한 벌 더
    적어 두면 한쪽만 고쳐졌을 때 조용히 어긋난다.
    """
    from .layout import normalize_header

    known: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            known.setdefault(normalize_header(name), key)

    found: dict[str, str] = {}
    for _block, label, *_rest in columns:
        key = known.get(normalize_header(label))
        if key:
            found.setdefault(key, label)
    return found


def write_roster_template(path: str | Path) -> Path:
    """빈 명부 양식을 만든다."""
    path = Path(path)
    save_workbook(build_workbook(), path)
    _embed_values(path)
    return path


def _embed_values(path: Path) -> None:
    """수식 칸에 계산 결과를 심는다.

    openpyxl 은 수식을 글자로만 적고 값을 남기지 않는다. 그러면 엑셀이 '제한된
    보기' 로 열었을 때 그 칸이 통째로 비어 보여, 받는 사람 눈에는 표가 깨진
    것처럼 보인다. 수식은 그대로 두므로 입력을 고치면 다시 계산된다.
    """
    import re
    import shutil
    import zipfile

    import openpyxl
    from openpyxl.utils import range_boundaries

    def value_of(ws, ref, seen):
        raw = ws[ref].value
        if isinstance(raw, str) and raw.startswith("="):
            if ref in seen:
                raise ValueError(f"순환 참조: {ref}")
            return evaluate(ws, raw[1:], seen | {ref})
        return float(raw) if isinstance(raw, (int, float)) else 0.0

    def span(ws, text, seen):
        left, right = text.split(":")
        c1, r1, c2, r2 = range_boundaries(f"{left}:{right}")
        return [value_of(ws, ws.cell(r, c).coordinate, seen)
                for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]

    def evaluate(ws, expr, seen):
        # 명부 대조가 쓰는 다른 시트 합계 — SUM(퇴직자명부!M5:M5004).
        expr = re.sub(
            r"SUM\((?:'([^']+)'|([^'!()=]+))!([A-Z]+\d+:[A-Z]+\d+)\)",
            lambda m: "(" + repr(sum(span(
                book[m.group(1) or m.group(2)], m.group(3), seen))) + ")",
            expr)
        expr = re.sub(r"SUM\(([A-Z]+\d+:[A-Z]+\d+)\)",
                      lambda m: "(" + repr(sum(span(ws, m.group(1), seen))) + ")",
                      expr)
        expr = re.sub(r"\$?([A-Z]{1,2})\$?([0-9]+)",
                      lambda m: repr(value_of(ws, m.group(1) + m.group(2), seen)),
                      expr)
        # 명부 대조의 참/거짓 — =(C26=D26). 양쪽을 셈해 같은지 본다.
        same = re.fullmatch(r"\(([^=]+)=([^=]+)\)", expr)
        if same:
            left = evaluate(ws, same.group(1), seen)
            right = evaluate(ws, same.group(2), seen)
            return ("b", abs(left - right) < 0.5)
        if not re.fullmatch(r"[0-9eE+\-*/.() ]+", expr):
            raise ValueError(f"풀 수 없는 수식: {expr}")
        return eval(expr)  # noqa: S307 — 숫자와 연산자만 남은 것을 확인했다

    book = openpyxl.load_workbook(path)
    wanted = {}
    for index, name in enumerate(book.sheetnames, start=1):
        ws = book[name]
        found = {}
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    # 다른 시트를 더하거나 참/거짓을 내는 수식(명부 대조)은 여기서
                    # 못 푼다. 그대로 두면 엑셀이 열 때 계산한다 — 제한된 보기
                    # 에서만 잠시 빈 칸으로 보인다.
                    number = evaluate(ws, cell.value[1:],
                                      frozenset({cell.coordinate}))
                    if isinstance(number, tuple):      # 참/거짓
                        found[cell.coordinate] = ("b", "1" if number[1] else "0")
                    else:
                        found[cell.coordinate] = ("n", repr(round(number, 6)))
        if found:
            wanted[index] = found
    if not wanted:
        return

    backup = path.with_suffix(".orig")
    shutil.copy2(path, backup)
    try:
        with zipfile.ZipFile(backup) as source, \
                zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                match = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", item.filename)
                if match and int(match.group(1)) in wanted:
                    text = data.decode("utf-8")
                    for ref, (kind, value) in wanted[int(match.group(1))].items():
                        pattern = (r'<c r="%s"([^>]*)>(<f[^>]*>.*?</f>)'
                                   r'(?:<v\s*/>|<v>.*?</v>)?</c>' % ref)
                        # 참/거짓 칸은 t="b" 를 달아야 1 이 아니라 TRUE 로 보인다.
                        mark = ' t="b"' if kind == "b" else ""
                        text, hits = re.subn(
                            pattern,
                            r'<c r="%s"\g<1>%s>\g<2><v>%s</v></c>' % (ref, mark, value),
                            text, count=1)
                        if hits != 1:
                            raise ValueError(f"{item.filename} 의 {ref} 를 찾지 못했다")
                    data = text.encode("utf-8")
                target.writestr(item, data)
    finally:
        backup.unlink(missing_ok=True)
