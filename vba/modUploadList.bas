Attribute VB_Name = "modUploadList"
Option Explicit

' ############################################################################
'  modUploadList - 업로드 명부 생성 (원본 Module1 / Module2 대체)
'
'  원본 대비 달라진 점
'    1. 셀을 하나씩 읽고 쓰던 것을 배열 일괄 read/write 로 바꿨다.
'       1,000명 기준 셀 접근이 약 5만 회에서 2회로 줄어 수십 배 빨라진다.
'    2. Dim c_sabeon(1 To 100000) 식 정적 배열 40여 개(모듈당 약 30MB)를 없앴다.
'       읽어 들인 명부 크기만큼만 쓴다.
'    3. 오류를 만나도 멈추지 않고 modPensionCore 의 ErrLog 에 모은다.
'       끝나면 "검증결과" 시트에 전부 남기고 요약을 보여 준다.
'    4. 날짜 판정은 ParseRosterDate 하나로 처리한다(원본은 항목마다 복사).
'
'  실행: 개발도구 -> 매크로 -> UPLOADLIST_JAE / UPLOADLIST_TOI
'  선행: modPensionCore 를 함께 가져와야 한다.
' ############################################################################

Private Const SH_INPUT   As String = "Input"
Private Const SH_JAE     As String = "재직자명부"
Private Const SH_TOI     As String = "퇴직자명부"
Private Const SH_UP_JAE  As String = "UpLoad_Jae"
Private Const SH_UP_TOI  As String = "UpLoad_Toi"

Private Const JAE_FIRST_ROW As Long = 26
Private Const TOI_FIRST_ROW As Long = 22
Private Const RULE_FIRST_ROW As Long = 12
Private Const RULE_MAX_ROWS  As Long = 25

' Input 시트에서 읽은 직군 규칙
Private Type JobRule
    SourceName  As String
    MappedName  As String
    Nra         As Long
    JanNra      As Long
    AddAge      As Long
    ToiBeta     As String
    JanBeta     As String
    ToiWxqx     As String
    ToiBx       As String
    JanWxqx     As String
    JanBx       As String
    RetToiWxqx  As String
    RetJanWxqx  As String
End Type

Private mRules() As JobRule
Private mRuleCount As Long
Private mBaseDate As Date
Private mBaseYear As Long
Private mWageCheck As Double


' ── Input 시트 읽기 ─────────────────────────────────────────────────────────
Private Sub LoadInput()
    Dim ws As Worksheet, i As Long, r As Long
    Set ws = ThisWorkbook.Worksheets(SH_INPUT)

    mBaseDate = ws.Cells(3, 3).Value
    mBaseYear = Year(mBaseDate)
    mWageCheck = Val(ws.Cells(5, 3).Value)

    ReDim mRules(1 To RULE_MAX_ROWS)
    mRuleCount = 0

    ' 원본은 CountA(C12:C26) 으로 건수를 세어 그만큼만 읽었다. 중간에 빈 행이
    ' 있으면 뒤쪽 직군을 통째로 놓치므로, 여기서는 전 구간을 훑고 빈 행만 건너뛴다.
    For i = 0 To RULE_MAX_ROWS - 1
        r = RULE_FIRST_ROW + i
        If Len(Trim$(CStr(ws.Cells(r, 2).Value))) > 0 Or _
           Len(Trim$(CStr(ws.Cells(r, 3).Value))) > 0 Then
            mRuleCount = mRuleCount + 1
            With mRules(mRuleCount)
                .SourceName = Trim$(CStr(ws.Cells(r, 2).Value))
                .MappedName = Trim$(CStr(ws.Cells(r, 3).Value))
                If Len(.MappedName) = 0 Then .MappedName = .SourceName
                .Nra = Val(ws.Cells(r, 4).Value)
                .JanNra = Val(ws.Cells(r, 5).Value)
                .AddAge = Val(ws.Cells(r, 6).Value)
                .ToiBeta = Trim$(CStr(ws.Cells(r, 7).Value))
                .JanBeta = Trim$(CStr(ws.Cells(r, 8).Value))
                .ToiWxqx = Trim$(CStr(ws.Cells(r, 9).Value))
                .ToiBx = Trim$(CStr(ws.Cells(r, 10).Value))
                .JanWxqx = Trim$(CStr(ws.Cells(r, 11).Value))
                .JanBx = Trim$(CStr(ws.Cells(r, 12).Value))
                .RetToiWxqx = Trim$(CStr(ws.Cells(r, 13).Value))
                .RetJanWxqx = Trim$(CStr(ws.Cells(r, 14).Value))
            End With
        End If
    Next i
End Sub

' 직군명으로 규칙 번호 찾기. 못 찾으면 0.
' 원본은 완전일치 비교라 앞뒤 공백 하나로 오류가 났다. 여기서는 Trim 후 비교한다.
Private Function FindRule(ByVal name As String) As Long
    Dim i As Long, key As String
    key = Trim$(name)
    For i = 1 To mRuleCount
        If mRules(i).SourceName = key Then
            FindRule = i
            Exit Function
        End If
    Next i
    FindRule = 0
End Function

' Input 에 값이 있으면 그것을, 없으면 명부 칼럼 값을 쓴다(원본 규칙).
Private Function PickRule(ByVal fromInput As String, ByVal fromRoster As Variant) As String
    If Len(fromInput) > 0 Then
        PickRule = fromInput
    Else
        PickRule = Trim$(CStr(fromRoster))
    End If
End Function


' ############################################################################
'  재직자명부 -> UpLoad_Jae
' ############################################################################
Public Sub UPLOADLIST_JAE()
    Dim ws As Worksheet, wsOut As Worksheet
    Dim src As Variant, out() As Variant
    Dim lastRow As Long, n As Long, i As Long, w As Long
    Dim t0 As Double

    Dim empId As String, jobRaw As String, gender As String, empType As String
    Dim birth As Date, hire As Date, settle As Date, transIn As Date, extraDay As Date
    Dim ok As Boolean, ri As Long
    Dim wage As Double, dailyPay As Double
    Dim age As Long, hireAge As Long, peak As Long
    Dim ids As Object

    t0 = Timer
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual
    ErrLog_Reset

    On Error GoTo Finally

    LoadInput
    If mRuleCount = 0 Then
        MsgBox "Input 시트에 직군 규칙이 없습니다. B12 부터 채워 주세요.", vbExclamation
        GoTo Finally
    End If

    Set ws = ThisWorkbook.Worksheets(SH_JAE)
    lastRow = LastDataRow(ws, JAE_FIRST_ROW, 3, 5, 6, 8, 9)
    If lastRow < JAE_FIRST_ROW Then
        MsgBox "재직자명부에 데이터가 없습니다.", vbExclamation
        GoTo Finally
    End If

    n = lastRow - JAE_FIRST_ROW + 1
    ' 배열 일괄 읽기. 이 한 줄이 원본의 셀 단위 접근 수만 회를 대체한다.
    src = ws.Range(ws.Cells(JAE_FIRST_ROW, 1), ws.Cells(lastRow, 36)).Value

    ReDim out(1 To n, 1 To 36)
    Set ids = CreateObject("Scripting.Dictionary")
    ids.CompareMode = 1     ' 대소문자 무시
    w = 0

    For i = 1 To n
        ri = JAE_FIRST_ROW + i - 1

        empId = Trim$(CStr(src(i, 3) & ""))
        jobRaw = Trim$(CStr(src(i, 5) & ""))
        empType = NormEmployeeType(src(i, 4))
        gender = NormGender(src(i, 7))

        ' 사번 중복. 원본은 이중 루프로 O(n^2) 비교 후 첫 건에서 멈췄다.
        If Len(empId) > 0 Then
            If ids.Exists(empId) Then
                ErrLog_Add SH_JAE, ri, "C", i, empId, _
                           "사번이 중복됩니다 (앞선 행: " & ids(empId) & ")"
            Else
                ids.Add empId, ri
            End If
        End If

        ' 직군
        Dim rn As Long
        rn = FindRule(jobRaw)
        If rn = 0 Then
            ErrLog_Add SH_JAE, ri, "E", i, empId, _
                       "직군 '" & jobRaw & "' 이(가) Input 시트에 없습니다"
            GoTo NextJae
        End If

        ' 생년월일
        birth = ParseRosterDate(src(i, 8), mBaseYear, ok)
        If Not ok Or birth = 0 Then
            ErrLog_Add SH_JAE, ri, "H", i, empId, "생년월일을 확인해 주세요"
            GoTo NextJae
        End If

        ' 입사일
        hire = ParseRosterDate(src(i, 9), mBaseYear, ok)
        If Not ok Or hire = 0 Then
            ErrLog_Add SH_JAE, ri, "I", i, empId, "입사일자를 확인해 주세요"
            GoTo NextJae
        End If
        If hire > mBaseDate Then
            ErrLog_Add SH_JAE, ri, "I", i, empId, _
                       "입사일자가 산출기준일보다 늦습니다 (산출 대상에서 제외)"
            GoTo NextJae
        End If

        ' 중간정산일. 비었거나 입사일보다 이르면 입사일로.
        settle = ParseRosterDate(src(i, 10), mBaseYear, ok)
        If Not ok Then
            ErrLog_Add SH_JAE, ri, "J", i, empId, "중간정산일을 확인해 주세요"
            GoTo NextJae
        End If
        If settle = 0 Or settle < hire Then settle = hire

        ' 전입일 / 추가지급 기준일 (없어도 된다)
        transIn = ParseRosterDate(src(i, 21), mBaseYear, ok)
        If Not ok Then
            ErrLog_Add SH_JAE, ri, "U", i, empId, "전입일을 확인해 주세요"
            transIn = 0
        End If
        extraDay = ParseRosterDate(src(i, 34), mBaseYear, ok)
        If Not ok Then
            ErrLog_Add SH_JAE, ri, "AH", i, empId, "추가지급 기준일을 확인해 주세요"
            extraDay = 0
        End If

        ' 연령
        age = AttainedAge(birth, mBaseDate)
        If age < 15 Or age > 100 Then
            ErrLog_Add SH_JAE, ri, "H", i, empId, _
                       "산출기준일 만 연령이 " & age & "세입니다 (15~100세를 벗어남)"
            GoTo NextJae
        End If
        hireAge = AttainedAge(birth, hire)
        If hireAge < 15 Or hireAge > 100 Then
            ErrLog_Add SH_JAE, ri, "I", i, empId, _
                       "입사 시점 만 연령이 " & hireAge & "세입니다 (15~100세를 벗어남)"
            GoTo NextJae
        End If

        ' 평균임금
        wage = Val(src(i, 11) & "")
        If wage <= 0 Then
            ErrLog_Add SH_JAE, ri, "K", i, empId, "30일 평균임금이 비었거나 0 이하입니다"
            GoTo NextJae
        ElseIf wage < mWageCheck Then
            ErrLog_Add SH_JAE, ri, "K", i, empId, _
                       "30일 평균임금이 체크금액(" & Format(mWageCheck, "#,##0") & ") 미만입니다"
            GoTo NextJae
        End If

        ' 제도구분
        If Len(Trim$(CStr(src(i, 17) & ""))) = 0 Then
            ErrLog_Add SH_JAE, ri, "Q", i, empId, "퇴직급여 제도구분이 공란입니다"
            GoTo NextJae
        End If

        ' 사번이 비면 성별+생년월일+성명으로 생성(원본과 동일)
        If Len(empId) = 0 Then
            empId = gender & Format(birth, "yyyy-mm-dd") & Trim$(CStr(src(i, 6) & ""))
        End If

        ' 일 기본급이 0 이면 평균임금 / 30
        dailyPay = Val(src(i, 14) & "")
        If dailyPay = 0 Then dailyPay = WorksheetFunction.Round(wage / 30, 0)

        peak = Val(src(i, 20) & "")

        ' ── 출력 행 만들기 ──────────────────────────────────────────────
        w = w + 1
        out(w, 1) = w
        out(w, 2) = empId
        out(w, 3) = empType
        out(w, 4) = mRules(rn).MappedName
        out(w, 5) = src(i, 6)
        out(w, 6) = gender
        out(w, 7) = birth
        out(w, 8) = hire
        out(w, 9) = settle
        out(w, 10) = wage
        out(w, 11) = Val(src(i, 12) & "")
        out(w, 12) = Val(src(i, 13) & "")
        out(w, 13) = dailyPay
        out(w, 14) = Val(src(i, 15) & "")
        out(w, 15) = Val(src(i, 16) & "")
        out(w, 16) = src(i, 17)
        out(w, 17) = Val(src(i, 18) & "")
        out(w, 18) = src(i, 19)
        out(w, 19) = IIf(peak = 0, "", peak)
        out(w, 20) = IIf(transIn = 0, "", transIn)
        out(w, 21) = Val(src(i, 23) & "")
        out(w, 22) = Val(src(i, 24) & "")
        out(w, 23) = src(i, 26)
        out(w, 24) = SeveranceNRA(age, mRules(rn).Nra, mRules(rn).AddAge, peak)
        out(w, 25) = LongTermNRA(age, mRules(rn).JanNra, mRules(rn).AddAge)
        out(w, 26) = Val(src(i, 27) & "")
        out(w, 27) = PickRule(mRules(rn).ToiBeta, src(i, 28))
        out(w, 28) = PickRule(mRules(rn).JanBeta, src(i, 29))
        out(w, 29) = PickRule(mRules(rn).ToiWxqx, src(i, 30))
        out(w, 30) = PickRule(mRules(rn).ToiBx, src(i, 31))
        out(w, 31) = PickRule(mRules(rn).JanWxqx, src(i, 32))
        out(w, 32) = PickRule(mRules(rn).JanBx, src(i, 33))
        out(w, 33) = IIf(extraDay = 0, "", extraDay)
        out(w, 34) = Val(src(i, 35) & "")
        out(w, 35) = src(i, 36)
        out(w, 36) = age

NextJae:
    Next i

    ' ── 배열 일괄 쓰기 ──────────────────────────────────────────────────
    Set wsOut = ThisWorkbook.Worksheets(SH_UP_JAE)
    wsOut.Range("A6:AJ100000").ClearContents
    If w > 0 Then
        wsOut.Range(wsOut.Cells(6, 1), wsOut.Cells(5 + w, 36)).Value = out
        wsOut.Range(wsOut.Cells(6, 7), wsOut.Cells(5 + w, 9)).NumberFormat = "yyyy-mm-dd"
        wsOut.Range(wsOut.Cells(6, 20), wsOut.Cells(5 + w, 20)).NumberFormat = "yyyy-mm-dd"
        wsOut.Range(wsOut.Cells(6, 33), wsOut.Cells(5 + w, 33)).NumberFormat = "yyyy-mm-dd"
    End If

    ReportResult "재직자", n, w, t0, SH_UP_JAE, SH_JAE

Finally:
    Application.Calculation = xlCalculationAutomatic
    Application.ScreenUpdating = True
    If Err.Number <> 0 Then
        MsgBox "산출 중 오류가 발생했습니다." & vbCrLf & vbCrLf & _
               Err.Number & ": " & Err.Description, vbCritical
    End If
End Sub


' ############################################################################
'  퇴직자명부 -> UpLoad_Toi
' ############################################################################
Public Sub UPLOADLIST_TOI()
    Dim ws As Worksheet, wsOut As Worksheet
    Dim src As Variant, out() As Variant
    Dim lastRow As Long, n As Long, i As Long, w As Long, ri As Long, rn As Long
    Dim t0 As Double, ok As Boolean

    Dim empId As String, jobRaw As String, gender As String, reason As String
    Dim birth As Date, hire As Date, exitDay As Date, fundDay As Date
    Dim total As Double, fund As Double
    Dim ids As Object

    t0 = Timer
    Application.ScreenUpdating = False
    Application.Calculation = xlCalculationManual
    ErrLog_Reset

    On Error GoTo Finally

    LoadInput
    If mRuleCount = 0 Then
        MsgBox "Input 시트에 직군 규칙이 없습니다. B12 부터 채워 주세요.", vbExclamation
        GoTo Finally
    End If

    Set ws = ThisWorkbook.Worksheets(SH_TOI)
    lastRow = LastDataRow(ws, TOI_FIRST_ROW, 3, 5, 6, 8, 9)
    If lastRow < TOI_FIRST_ROW Then
        MsgBox "퇴직자명부에 데이터가 없습니다.", vbExclamation
        GoTo Finally
    End If

    n = lastRow - TOI_FIRST_ROW + 1
    src = ws.Range(ws.Cells(TOI_FIRST_ROW, 1), ws.Cells(lastRow, 24)).Value

    ReDim out(1 To n, 1 To 23)
    Set ids = CreateObject("Scripting.Dictionary")
    ids.CompareMode = 1
    w = 0

    For i = 1 To n
        ri = TOI_FIRST_ROW + i - 1

        empId = Trim$(CStr(src(i, 3) & ""))
        jobRaw = Trim$(CStr(src(i, 5) & ""))
        gender = NormGender(src(i, 7))

        If Len(empId) > 0 Then
            If ids.Exists(empId) Then
                ErrLog_Add SH_TOI, ri, "C", i, empId, _
                           "사번이 중복됩니다 (앞선 행: " & ids(empId) & ")"
            Else
                ids.Add empId, ri
            End If
        End If

        rn = FindRule(jobRaw)
        If rn = 0 Then
            ErrLog_Add SH_TOI, ri, "E", i, empId, _
                       "직군 '" & jobRaw & "' 이(가) Input 시트에 없습니다"
            GoTo NextToi
        End If

        birth = ParseRosterDate(src(i, 8), mBaseYear, ok)
        If Not ok Or birth = 0 Then
            ErrLog_Add SH_TOI, ri, "H", i, empId, "생년월일을 확인해 주세요"
            GoTo NextToi
        End If

        hire = ParseRosterDate(src(i, 9), mBaseYear, ok)
        If Not ok Or hire = 0 Then
            ErrLog_Add SH_TOI, ri, "I", i, empId, "입사일을 확인해 주세요"
            GoTo NextToi
        End If

        ' 퇴사일. 비면 산출기준일로 본다(체크리스트 5번).
        exitDay = ParseRosterDate(src(i, 10), mBaseYear, ok)
        If Not ok Then
            ErrLog_Add SH_TOI, ri, "J", i, empId, "퇴사일을 확인해 주세요"
            GoTo NextToi
        End If
        If exitDay = 0 Then exitDay = mBaseDate
        If exitDay <= hire Then
            ErrLog_Add SH_TOI, ri, "J", i, empId, "퇴사일이 입사일보다 이르거나 같습니다"
            GoTo NextToi
        End If
        If exitDay > mBaseDate Then
            ErrLog_Add SH_TOI, ri, "J", i, empId, _
                       "퇴사일이 산출기준일보다 늦습니다 (산출 대상에서 제외)"
            GoTo NextToi
        End If

        fundDay = ParseRosterDate(src(i, 11), mBaseYear, ok)
        If Not ok Then
            ErrLog_Add SH_TOI, ri, "K", i, empId, "사외적립자산 지급일을 확인해 주세요"
            fundDay = 0
        End If

        reason = NormRetirementReason(src(i, 12))
        If Len(reason) = 0 Then
            ErrLog_Add SH_TOI, ri, "L", i, empId, _
                       "지급(퇴직)사유를 1~6 중 하나로 입력해 주세요"
            GoTo NextToi
        End If

        If Len(Trim$(CStr(src(i, 13) & ""))) = 0 Then
            ErrLog_Add SH_TOI, ri, "M", i, empId, "퇴직급여 제도구분이 공란입니다"
            GoTo NextToi
        End If

        total = Val(src(i, 14) & "")
        fund = Val(src(i, 15) & "")
        If fund > total And total > 0 Then
            ErrLog_Add SH_TOI, ri, "O", i, empId, _
                       "사외자산 지급금액이 총지급금액보다 큽니다"
        End If

        If Len(empId) = 0 Then
            empId = gender & Format(birth, "yyyy-mm-dd") & Trim$(CStr(src(i, 6) & ""))
        End If

        w = w + 1
        out(w, 1) = w
        out(w, 2) = empId
        out(w, 3) = NormEmployeeType(src(i, 4))
        out(w, 4) = mRules(rn).MappedName
        out(w, 5) = src(i, 6)
        out(w, 6) = gender
        out(w, 7) = birth
        out(w, 8) = hire
        out(w, 9) = exitDay
        out(w, 10) = IIf(fundDay = 0, "", fundDay)
        out(w, 11) = reason
        out(w, 12) = src(i, 13)
        out(w, 13) = total
        out(w, 14) = fund
        out(w, 15) = Val(src(i, 16) & "")
        out(w, 16) = Val(src(i, 17) & "")
        out(w, 17) = Val(src(i, 18) & "")
        out(w, 18) = Val(src(i, 19) & "")
        out(w, 19) = src(i, 20)
        out(w, 20) = src(i, 21)
        out(w, 21) = PickRule(mRules(rn).RetToiWxqx, src(i, 22))
        out(w, 22) = PickRule(mRules(rn).RetJanWxqx, src(i, 23))
        out(w, 23) = src(i, 24)

NextToi:
    Next i

    Set wsOut = ThisWorkbook.Worksheets(SH_UP_TOI)
    wsOut.Range("A6:Y100000").ClearContents
    If w > 0 Then
        wsOut.Range(wsOut.Cells(6, 1), wsOut.Cells(5 + w, 23)).Value = out
        wsOut.Range(wsOut.Cells(6, 7), wsOut.Cells(5 + w, 10)).NumberFormat = "yyyy-mm-dd"
    End If

    ReportResult "퇴직자", n, w, t0, SH_UP_TOI, SH_TOI

Finally:
    Application.Calculation = xlCalculationAutomatic
    Application.ScreenUpdating = True
    If Err.Number <> 0 Then
        MsgBox "산출 중 오류가 발생했습니다." & vbCrLf & vbCrLf & _
               Err.Number & ": " & Err.Description, vbCritical
    End If
End Sub


' ── 결과 안내 ───────────────────────────────────────────────────────────────
Private Sub ReportResult(ByVal kind As String, ByVal readCount As Long, _
                         ByVal writeCount As Long, ByVal t0 As Double, _
                         ByVal outSheet As String, ByVal srcSheet As String)
    Dim msg As String, secs As Double
    secs = Timer - t0

    msg = kind & " 명부 " & Format(readCount, "#,##0") & "명을 읽어 " & _
          Format(writeCount, "#,##0") & "명을 업로드 명부에 기록했습니다." & vbCrLf & _
          "소요시간 " & Format(secs, "0.0") & "초" & vbCrLf & vbCrLf

    If ErrLog_Count = 0 Then
        msg = msg & "검증 이상 없음."
        ThisWorkbook.Worksheets(outSheet).Activate
    Else
        ErrLog_WriteSheet
        msg = msg & "확인이 필요한 항목 " & ErrLog_Count & "건이 있습니다." & vbCrLf & _
              "'검증결과' 시트에 전체 목록을 남겼습니다." & vbCrLf & vbCrLf & _
              ErrLog_Text(15)
        ThisWorkbook.Worksheets("검증결과").Activate
    End If

    MsgBox msg, IIf(ErrLog_Count = 0, vbInformation, vbExclamation), "업로드 명부 생성"
End Sub
