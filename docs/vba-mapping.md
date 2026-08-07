# VBA → Python 대응표

원본: `sample_2512_..._vba_v3.2_(260801)_v1.0.5.xlsm`
`Module1` (재직자, 1,631줄) · `Module2` (퇴직자, 1,380줄)

---

## 1. 프로시저 대응

| VBA | Python |
|---|---|
| `Module1.UPLOADLIST_JAE` | `readers.read_active_roster` + `validation.validate_active` + `upload.build_active_upload` |
| `Module2.UPLOADLIST_TOI` | `readers.read_retired_roster` + `validation.validate_retired` + `upload.build_retired_upload` |
| `Select Case Len(datetr)` 블록 (양 모듈 합계 9회 중복) | `dates.parse_roster_date` 1개 |
| `Sheets("Input")` 읽기 구간 | `config.read_config` |
| 사번 중복 이중 루프 | `validation._check_duplicate_ids` |
| 연령·정년연령 인라인 계산 | `actuarial.attained_age` / `normal_retirement_age` / `longterm_retirement_age` |
| `MsgBox` + `check = 1` + `GoTo END_SANCHUL` | `errors.IssueLog` (수집만 하고 계속 진행) |

---

## 2. 그대로 유지한 규칙

VBA 산출값과 어긋나면 안 되므로 의도적으로 보존한 동작입니다.

### 두 자리 연도 피벗

```vb
cyy = Left(datetr, 2) + 1 - 1
If cyy <= kyy Then yy1 = 2000 + cyy Else yy1 = 1900 + cyy
```

`kyy` 는 산출기준일 연도의 뒤 두 자리입니다. 기준일이 2025-12-31이면 `25` → 2025년,
`26` → 1926년. → `dates.pivot_two_digit_year`

### 만 연령 계산

```vb
If mm > mm2 Or (mm = mm2 And dd >= dd2) Then six = 0 Else six = 1
age = yy - yy2 - six
```

`dd >= dd2` 이므로 **생일 당일에 나이가 오릅니다**. 통상의 만 나이와 갈리는
유일한 지점이며, 기준일이 12월 31일이고 생일도 12월 31일인 사람만 영향을 받습니다.
원본과의 일치를 위해 유지했습니다. → `actuarial.attained_age`

### 정년연령 결정

```vb
If c_impi(jc) > age(jc) Then t_y(jc) = c_impi(jc)          ' 임금피크 연령
ElseIf age(jc) >= nra(jkn_j) Then t_y(jc) = age(jc) + add_age(jkn_j)
Else t_y(jc) = nra(jkn_j)
```

장기급여 정년은 임금피크 연령을 보지 않습니다. → `actuarial`

### 코드값 매핑

* 임직원: `"Y"`, `"y"`, `"임원"`, `"임"`, `2` → 임원, **그 외 전부 직원**
* 성별: `"녀"`, `"여"`, `"여자"`, `"여성"`, `2/4/6/8` → 여자, **그 외 전부 남자**
* 퇴직사유: `Left(cell, 2)` 로 앞 두 글자만 비교 (`"중도퇴직"` → `"중도"`)

판정되지 않는 값을 오류로 올리지 않고 기본값으로 흘려보내는 것도 원본과 같습니다.

### 그 밖에

* 중간정산일이 비었거나 입사일보다 이르면 → 입사일로 대체
* 일 기본급이 0이면 → `round(평균임금 / 30)`
* 사번이 비면 → `성별 + 생년월일 + 성명` 으로 생성
* 입사일 > 기준일인 재직자, 퇴사일 > 기준일인 퇴직자는 업로드 명부에서 제외
* 연령 15세 미만 / 100세 초과는 오류

---

## 3. 고친 것

### 3.1 `yy.mm.d일` 형식의 일자 위치 (버그)

길이 8의 `"25.12.3일"` 에서 일자는 7번째 문자(`3`)입니다. 그런데 VBA는 블록마다
읽는 위치가 달랐습니다.

| 항목 | VBA 코드 | 결과 |
|---|---|---|
| 입사일 (`Module1` 659행) | `dd1 = Mid(datetr, 7, 1)` | 정상 |
| 생년월일 (433행) | `dd1 = Mid(datetr, 6, 1)` | `.` 을 읽어 **항상 실패** |
| 중간정산일 (883행) | `dd1 = Mid(datetr, 6, 1)` | 항상 실패 |
| 전입일·추가지급 기준일 | `dd1 = Mid(datetr, 6, 1)` | 항상 실패 |

복사·붙여넣기가 어긋난 자리입니다. 올바른 `7`번째로 통일했습니다.
회귀 테스트: `tests/test_dates.py::test_yy_mm_d_with_suffix_reads_the_day_not_the_separator`

### 3.2 명부 끝 판정 (조용한 데이터 누락)

```vb
c_tot_n = Application.WorksheetFunction.CountA(Range("h26:h100000"))
For jc = 1 To c_tot_n
    jc1 = jc + 25
```

`CountA` 는 **비어 있지 않은 칸의 개수** 이지 마지막 행 번호가 아닙니다.
생년월일이 빈 행이 중간에 하나 있으면 명부 끝이 한 줄 잘리고, 마지막 사람이
오류 없이 조용히 빠집니다. 100명 중 3명이 생년월일 미기재면 뒤 3명이 사라집니다.

→ `readers._last_data_row` 가 실제 마지막 행을 찾고, 앵커 열이 비어도 다른 열에
값이 있으면 데이터로 취급합니다.

### 3.3 `Input` 직군 규칙도 같은 문제

```vb
jkcnt = Application.WorksheetFunction.CountA(Range("c12:c26"))
```

중간에 빈 행이 있으면 뒤쪽 직군을 통째로 놓칩니다. → `config.read_config` 는
전 구간을 훑고 빈 행만 건너뜁니다.

### 3.4 직군명 공백

VBA는 `Cells(jc1, 5).Value = list_jkn(j)` 완전일치 비교라 앞뒤 공백 하나로
"직군을 확인해주세요" 오류가 납니다. → 공백을 정리한 뒤 비교합니다.

### 3.5 오류 처리

VBA는 첫 오류에서 `MsgBox` → `GoTo END_SANCHUL` 로 중단합니다. 오류가 20건이면
고치고 다시 돌리기를 20번 반복해야 합니다. 게다가 메시지가 `"3 번째 임직원 …"`
이라 시트에서 직접 세어 찾아야 합니다.

→ 전부 수집하고, **시트·행 번호·열 문자·사번** 을 함께 남깁니다.

```
[ERROR] JAE_WAGE_BELOW_CHECK (재직자명부!K28) 사번=A003: 30일 평균임금 100원이 체크금액 1,000,000원 미만입니다
```

---

## 4. 규칙이 어긋나 자동으로 고르지 않은 것

### 지급사유 `'임금피크제도에 따른 중간정산'`

같은 통합문서 안에서 두 규칙이 다릅니다.

* VBA `Module2`: `Case "4", "정년", "임금"` → **4 (정년퇴직)**
* `퇴직자명부` AC18 수식: `IF(OR($Z22="DC전환", $Z22="임금피크제도에 따른 중간정산"), 3, ...)` → **3 (DC전환/당기 중간정산 후 퇴직)**

어느 쪽이 맞는지는 회사 규정에 달렸습니다. VBA를 따라 4로 처리하되
`TOI_REASON_AMBIGUOUS` 경고를 남겨 담당자가 확인하도록 했습니다.
→ `normalize.is_ambiguous_reason`

---

## 5. 열 배치

`readers.ACTIVE_COLUMNS` / `RETIRED_COLUMNS` 에 열 번호와 이름을 표로 두었습니다.
명부 서식이 바뀌면 이 표만 고치면 되고, 검증 메시지의 열 문자도 자동으로 따라갑니다.

### 재직자명부 (26행부터)

| 열 | 항목 | | 열 | 항목 |
|---|---|---|---|---|
| C | 사번 | | R | 중간정산 지급금액 |
| D | 임직원구분 | | S | 장기급여 산출대상여부 |
| E | 직군 | | T | 임금피크 연령 |
| F | 성명 | | U | 전입일 |
| G | 성별 | | W | 장기종업원급여 지급금액 |
| H | 생년월일 | | X | 전입액 |
| I | 입사일자 | | AA | 가산(감소) 지급률 |
| J | 중간정산일 | | AB~AC | 지급률 규정 (퇴직/장기) |
| K | 30일 평균임금 | | AD~AG | 퇴직률·승급률 규정 |
| L | 명예퇴직 산정용 임금 | | AH | 추가지급 기준일 |
| M | 퇴직급여추계액 | | AI | 추가지급 기본급 |
| N | 일 기본급 | | AJ | 원가코드 |
| O~P | 가산·차감 근속연수 | | | |
| Q | 퇴직급여 제도구분 | | | |

### 퇴직자명부 (22행부터)

| 열 | 항목 | | 열 | 항목 |
|---|---|---|---|---|
| C~I | 사번 ~ 입사일 (재직자와 동일) | | O | 사외자산 지급금액 |
| J | 퇴사일 (DC전환일·전출일) | | P | 국민연금전환금 |
| K | 사외적립자산 지급일 | | Q | 장기종업원급여 지급금액 |
| L | 지급(퇴직)사유 구분 | | R | 퇴직위로금 등 |
| M | 퇴직급여 제도구분 | | S | 전출지급금액 |
| N | 퇴직급여 총지급금액 | | V~W | 퇴직률 규정 |
| | | | X | 원가코드 |
