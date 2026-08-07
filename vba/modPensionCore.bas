Attribute VB_Name = "modPensionCore"
Option Explicit

' ############################################################################
'  modPensionCore - 명부 처리 공용 함수
'
'  원본 Module1/Module2 에서 항목마다 복사되어 있던 날짜 판정 블록(모듈당 4~5회,
'  합계 900줄 이상)을 함수 하나로 모았다. 연령 계산, 정년연령 결정, 오류 수집도
'  여기에 둔다.
'
'  Module1/Module2 대비 고친 점
'    1. 길이 8 "yy.mm.d일" 형식에서 일자를 7번째 문자에서 읽는다.
'       원본은 생년월일/중간정산일/전입일/추가지급기준일 블록이 6번째(구분자 '.')를
'       읽어 항상 실패했다. 입사일 블록만 7번째로 옳게 읽고 있었다.
'    2. 오류를 만나도 MsgBox 로 멈추지 않고 모아 둔다(ErrLog_*).
'
'  가져오기: VBE 창 -> 파일 -> 파일 가져오기(M) -> 이 파일 선택
' ############################################################################

' ── 오류 수집기 ─────────────────────────────────────────────────────────────
' 원본은 오류 하나마다 MsgBox 를 띄우고 GoTo END_SANCHUL 로 산출을 끝냈다.
' 담당자는 고치고 다시 돌리기를 오류 건수만큼 반복해야 했다.
' 여기서는 시트/행/열/사번과 함께 모아 두었다가 한 번에 보여 준다.

Private mIssues As Collection

Public Sub ErrLog_Reset()
    Set mIssues = New Collection
End Sub

Public Sub ErrLog_Add(ByVal sheetName As String, ByVal rowNo As Long, _
                      ByVal colLetter As String, ByVal seq As Long, _
                      ByVal empId As String, ByVal message As String)
    If mIssues Is Nothing Then ErrLog_Reset
    mIssues.Add sheetName & "!" & colLetter & rowNo & _
                IIf(Len(empId) > 0, "  사번=" & empId, "") & _
                "  (" & seq & "번째)  " & message
End Sub

Public Function ErrLog_Count() As Long
    If mIssues Is Nothing Then
        ErrLog_Count = 0
    Else
        ErrLog_Count = mIssues.Count
    End If
End Function

' 수집된 오류를 최대 maxLines 건까지 한 덩어리 문자열로.
Public Function ErrLog_Text(Optional ByVal maxLines As Long = 25) As String
    Dim i As Long, s As String
    If ErrLog_Count = 0 Then
        ErrLog_Text = ""
        Exit Function
    End If
    For i = 1 To mIssues.Count
        If i > maxLines Then
            s = s & vbCrLf & "… 외 " & (mIssues.Count - maxLines) & "건"
            Exit For
        End If
        s = s & mIssues(i) & vbCrLf
    Next i
    ErrLog_Text = s
End Function

' 오류 목록을 "검증결과" 시트에 남긴다. 엑셀에서 바로 필터링해 볼 수 있다.
Public Sub ErrLog_WriteSheet(Optional ByVal sheetName As String = "검증결과")
    Dim ws As Worksheet, i As Long

    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets(sheetName)
    On Error GoTo 0
    If ws Is Nothing Then
        Set ws = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
        ws.Name = sheetName
    End If

    ws.Cells.ClearContents
    ws.Cells(1, 1).Value = "검증 결과 (" & Format(Now, "yyyy-mm-dd hh:nn") & ")"
    ws.Cells(2, 1).Value = "건수: " & ErrLog_Count

    If ErrLog_Count > 0 Then
        For i = 1 To mIssues.Count
            ws.Cells(3 + i, 1).Value = mIssues(i)
        Next i
    Else
        ws.Cells(4, 1).Value = "이상 없음"
    End If
    ws.Columns(1).AutoFit
End Sub


' ── 날짜 파싱 ───────────────────────────────────────────────────────────────

' 두 자리 연도를 네 자리로. 기준일 연도의 뒤 두 자리 이하면 2000년대, 넘으면 1900년대.
' 원본: If cyy <= kyy Then yy1 = 2000 + cyy Else yy1 = 1900 + cyy
Public Function PivotYear(ByVal twoDigit As Long, ByVal baseYear As Long) As Long
    If twoDigit <= (baseYear Mod 100) Then
        PivotYear = 2000 + twoDigit
    Else
        PivotYear = 1900 + twoDigit
    End If
End Function

Private Function IsDigitAt(ByVal s As String, ByVal pos As Long) As Boolean
    If pos < 1 Or pos > Len(s) Then
        IsDigitAt = False
    Else
        IsDigitAt = (Mid$(s, pos, 1) Like "[0-9]")
    End If
End Function

' Mid(s, start, length) 를 정수로. 숫자가 아니면 ok 를 False 로.
Private Function NumAt(ByVal s As String, ByVal start As Long, ByVal length As Long, _
                       ByRef ok As Boolean) As Long
    Dim chunk As String, i As Long
    chunk = Mid$(s, start, length)
    If Len(chunk) <> length Then ok = False: Exit Function
    For i = 1 To length
        If Not (Mid$(chunk, i, 1) Like "[0-9]") Then ok = False: Exit Function
    Next i
    NumAt = CLng(chunk)
End Function

' 명부 셀 값 하나를 날짜로. 실패하면 ok = False.
' 빈 값이면 ok = True, 반환값 0 (원본이 0 을 "없음" 으로 쓰는 것과 같다).
'
' 지원 형식(원본 주석과 동일): mdd, mmdd, ymmdd, yymmdd, yy.m.d, y.mm.d, y.m.dd,
'   yy.mm.d, yy.m.dd, yy.m.d일, y.mm.d일, y.m.dd일, yyyymmdd, yyyy.m.d, yy.mm.dd,
'   yy.mm.d일, yy.m.dd일, yy.mm.dd일, yyyymm-dd, yyyy-mmdd, yyyy.mm.d, yyyy.m.dd,
'   yyyy.mm.dd, yyyy.mm.d일, yyyy.m.dd일, yyyy.mm.dd일
Public Function ParseRosterDate(ByVal v As Variant, ByVal baseYear As Long, _
                                ByRef ok As Boolean) As Date
    Dim s As String, n As Long
    Dim y As Long, m As Long, d As Long

    ok = True
    ParseRosterDate = 0

    If IsEmpty(v) Then Exit Function
    If VarType(v) = vbDate Then
        ParseRosterDate = CDate(v)
        Exit Function
    End If

    s = Trim$(CStr(v))
    s = Replace$(s, "'", "")
    s = Replace$(s, " ", "")
    If Len(s) = 0 Then Exit Function

    n = Len(s)

    Select Case n
        Case 3                                  ' mdd
            y = 2000: m = NumAt(s, 1, 1, ok): d = NumAt(s, 2, 2, ok)

        Case 4                                  ' mmdd
            y = 2000: m = NumAt(s, 1, 2, ok): d = NumAt(s, 3, 2, ok)

        Case 5                                  ' ymmdd
            y = 2000 + NumAt(s, 1, 1, ok): m = NumAt(s, 2, 2, ok): d = NumAt(s, 4, 2, ok)

        Case 6
            If IsDigitAt(s, 2) Then
                y = PivotYear(NumAt(s, 1, 2, ok), baseYear)
                If IsDigitAt(s, 3) Then         ' yymmdd
                    m = NumAt(s, 3, 2, ok): d = NumAt(s, 5, 2, ok)
                Else                            ' yy.m.d
                    m = NumAt(s, 4, 1, ok): d = NumAt(s, 6, 1, ok)
                End If
            Else
                y = 2000 + NumAt(s, 1, 1, ok)
                If IsDigitAt(s, 4) Then         ' y.mm.d
                    m = NumAt(s, 3, 2, ok): d = NumAt(s, 6, 1, ok)
                Else                            ' y.m.dd
                    m = NumAt(s, 3, 1, ok): d = NumAt(s, 5, 2, ok)
                End If
            End If

        Case 7
            If IsDigitAt(s, 2) Then
                y = PivotYear(NumAt(s, 1, 2, ok), baseYear)
                If IsDigitAt(s, 5) Then         ' yy.mm.d
                    m = NumAt(s, 4, 2, ok): d = NumAt(s, 7, 1, ok)
                ElseIf IsDigitAt(s, 7) Then     ' yy.m.dd
                    m = NumAt(s, 4, 1, ok): d = NumAt(s, 6, 2, ok)
                Else                            ' yy.m.d일
                    m = NumAt(s, 4, 1, ok): d = NumAt(s, 6, 1, ok)
                End If
            Else
                y = 2000 + NumAt(s, 1, 1, ok)
                If IsDigitAt(s, 4) Then         ' y.mm.d일
                    m = NumAt(s, 3, 2, ok): d = NumAt(s, 6, 1, ok)
                Else                            ' y.m.dd일
                    m = NumAt(s, 3, 1, ok): d = NumAt(s, 5, 2, ok)
                End If
            End If

        Case 8
            If IsDigitAt(s, 3) Then
                y = NumAt(s, 1, 4, ok)
                If IsDigitAt(s, 4) And IsDigitAt(s, 5) Then   ' yyyymmdd
                    m = NumAt(s, 5, 2, ok): d = NumAt(s, 7, 2, ok)
                Else                                          ' yyyy.m.d
                    m = NumAt(s, 6, 1, ok): d = NumAt(s, 8, 1, ok)
                End If
            Else
                y = PivotYear(NumAt(s, 1, 2, ok), baseYear)
                If IsDigitAt(s, 5) Then
                    If IsDigitAt(s, 8) Then     ' yy.mm.dd
                        m = NumAt(s, 4, 2, ok): d = NumAt(s, 7, 2, ok)
                    Else                        ' yy.mm.d일
                        ' 원본은 여기서 Mid(s, 6, 1) 즉 구분자를 읽어 항상 실패했다.
                        m = NumAt(s, 4, 2, ok): d = NumAt(s, 7, 1, ok)
                    End If
                Else                            ' yy.m.dd일
                    m = NumAt(s, 4, 1, ok): d = NumAt(s, 6, 2, ok)
                End If
            End If

        Case 9
            If IsDigitAt(s, 5) Then
                If IsDigitAt(s, 7) Then         ' yy.mm.dd일
                    y = PivotYear(NumAt(s, 1, 2, ok), baseYear)
                    m = NumAt(s, 4, 2, ok): d = NumAt(s, 7, 2, ok)
                Else                            ' yyyymm-dd
                    y = NumAt(s, 1, 4, ok): m = NumAt(s, 5, 2, ok): d = NumAt(s, 8, 2, ok)
                End If
            Else
                y = NumAt(s, 1, 4, ok)
                If IsDigitAt(s, 7) Then
                    m = NumAt(s, 6, 2, ok)
                    If IsDigitAt(s, 8) Then     ' yyyy-mmdd
                        d = NumAt(s, 8, 2, ok)
                    Else                        ' yyyy.mm.d
                        d = NumAt(s, 9, 1, ok)
                    End If
                Else                            ' yyyy.m.dd
                    m = NumAt(s, 6, 1, ok): d = NumAt(s, 8, 2, ok)
                End If
            End If

        Case 10
            y = NumAt(s, 1, 4, ok)
            If IsDigitAt(s, 7) Then
                m = NumAt(s, 6, 2, ok)
                If IsDigitAt(s, 10) Then        ' yyyy.mm.dd
                    d = NumAt(s, 9, 2, ok)
                Else                            ' yyyy.mm.d일
                    d = NumAt(s, 9, 1, ok)
                End If
            Else                                ' yyyy.m.dd일
                m = NumAt(s, 6, 1, ok): d = NumAt(s, 8, 2, ok)
            End If

        Case 11                                 ' yyyy.mm.dd일
            If IsDigitAt(s, 5) Or IsDigitAt(s, 8) Or IsDigitAt(s, 11) Then
                ok = False
            Else
                y = NumAt(s, 1, 4, ok): m = NumAt(s, 6, 2, ok): d = NumAt(s, 9, 2, ok)
            End If

        Case Else
            ok = False
    End Select

    If Not ok Then Exit Function
    If y < 1900 Or y > 2200 Or m < 1 Or m > 12 Or d < 1 Or d > 31 Then
        ok = False
        Exit Function
    End If

    On Error GoTo BadDate
    ParseRosterDate = DateSerial(y, m, d)
    ' DateSerial 은 2월 30일을 3월 2일로 굴려 버리므로 되돌려 확인한다.
    If Day(ParseRosterDate) <> d Or Month(ParseRosterDate) <> m Then GoTo BadDate
    Exit Function

BadDate:
    ok = False
    ParseRosterDate = 0
End Function


' ── 연령·정년연령 ───────────────────────────────────────────────────────────

' 만 연령. 원본과 같이 생일 당일을 이미 지난 것으로 본다(dd >= dd2).
Public Function AttainedAge(ByVal birth As Date, ByVal asOf As Date) As Long
    Dim passed As Boolean
    passed = (Month(asOf) > Month(birth)) Or _
             (Month(asOf) = Month(birth) And Day(asOf) >= Day(birth))
    AttainedAge = Year(asOf) - Year(birth) - IIf(passed, 0, 1)
End Function

' 퇴직급여 정년연령.
'   임금피크 연령이 현재 연령보다 크면 그 값, 이미 정년을 넘겼으면 현재 연령 + 가산연수.
Public Function SeveranceNRA(ByVal age As Long, ByVal nra As Long, _
                             ByVal addAge As Long, ByVal wagePeakAge As Long) As Long
    If wagePeakAge > age Then
        SeveranceNRA = wagePeakAge
    ElseIf age >= nra Then
        SeveranceNRA = age + addAge
    Else
        SeveranceNRA = nra
    End If
End Function

' 장기급여 정년연령. 임금피크 연령을 보지 않는다(원본과 동일).
Public Function LongTermNRA(ByVal age As Long, ByVal jnra As Long, ByVal addAge As Long) As Long
    If age >= jnra Then
        LongTermNRA = age + addAge
    Else
        LongTermNRA = jnra
    End If
End Function


' ── 코드값 정규화 ───────────────────────────────────────────────────────────

' 임원 판정: "Y","y","임원","임",2. 그 외는 전부 직원(원본과 동일).
Public Function NormEmployeeType(ByVal v As Variant) As String
    Dim s As String
    s = Trim$(CStr(v))
    If s = "Y" Or s = "y" Or s = "임원" Or s = "임" Or s = "2" Then
        NormEmployeeType = "임원"
    Else
        NormEmployeeType = "직원"
    End If
End Function

' 여자 판정: "녀","여","여자","여성",2/4/6/8. 그 외는 전부 남자(원본과 동일).
Public Function NormGender(ByVal v As Variant) As String
    Dim s As String
    s = Trim$(CStr(v))
    Select Case s
        Case "녀", "여", "여자", "여성", "2", "4", "6", "8"
            NormGender = "여자"
        Case Else
            NormGender = "남자"
    End Select
End Function

' 지급(퇴직)사유. 앞 두 글자로 판정하는 원본 규칙을 유지한다.
' 판정 못 하면 빈 문자열.
Public Function NormRetirementReason(ByVal v As Variant) As String
    Dim head As String
    head = Left$(Trim$(CStr(v)), 2)
    Select Case head
        Case "1", "중도", "계약", "자진", "의원": NormRetirementReason = "1"
        Case "2", "사망":                        NormRetirementReason = "2"
        Case "3", "DC", "dc":                    NormRetirementReason = "3"
        Case "4", "정년", "임금":                NormRetirementReason = "4"
        Case "5", "계열", "전출":                NormRetirementReason = "5"
        Case "6", "사업", "분할":                NormRetirementReason = "6"
        Case Else:                               NormRetirementReason = ""
    End Select
End Function


' ── 시트 유틸 ───────────────────────────────────────────────────────────────

' 명부의 마지막 데이터 행.
'
' 원본은 CountA(H26:H100000) 로 "건수" 를 세어 시작행 + 건수까지만 읽었다.
' CountA 는 마지막 행 번호가 아니라 값이 든 칸의 개수이므로, 생년월일이 빈 행이
' 중간에 하나 있으면 명부 끝이 한 줄 잘리고 마지막 사람이 조용히 누락된다.
' 여기서는 여러 열을 함께 보아 실제 마지막 행을 찾는다.
Public Function LastDataRow(ByVal ws As Worksheet, ByVal firstRow As Long, _
                            ParamArray cols() As Variant) As Long
    Dim i As Long, r As Long, last As Long
    last = firstRow - 1
    For i = LBound(cols) To UBound(cols)
        r = ws.Cells(ws.Rows.Count, CLng(cols(i))).End(xlUp).Row
        If r > last Then last = r
    Next i
    If last < firstRow Then last = firstRow - 1
    LastDataRow = last
End Function

' 열 번호를 문자로 (오류 메시지에 쓴다).
Public Function ColLetter(ByVal col As Long) As String
    Dim a As String
    a = Split(Cells(1, col).Address(True, False), "$")(0)
    ColLetter = a
End Function
