@echo off
REM ============================================================
REM  연금계리 산출 시스템 - Windows 실행파일 빌드
REM
REM  준비물: 파이썬 3.10 이상 (python.org 공식 배포판, tkinter 포함)
REM  사용법: 이 파일을 더블클릭하거나 명령프롬프트에서 build.bat 실행
REM  결과물: dist\연금계리산출.exe  (더블클릭용 GUI)
REM          dist\pension-cli.exe   (배치 자동화용 CLI)
REM ============================================================
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo [1/4] 파이썬 확인
python --version
if errorlevel 1 (
    echo.
    echo   파이썬을 찾을 수 없습니다.
    echo   https://www.python.org/downloads/ 에서 설치하되,
    echo   설치 화면의 "Add Python to PATH" 를 반드시 체크하세요.
    pause
    exit /b 1
)

echo.
echo [2/4] 가상환경 준비
if not exist ".venv" python -m venv .venv
call .venv\Scripts\activate.bat

echo.
echo [3/4] 의존성 설치
python -m pip install --upgrade pip --quiet
python -m pip install -e ".[build,dev]" --quiet
if errorlevel 1 (
    echo   의존성 설치에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo   테스트 실행
python -m pytest -q
if errorlevel 1 (
    echo.
    echo   테스트가 실패했습니다. 빌드를 중단합니다.
    pause
    exit /b 1
)

echo.
echo [4/4] 실행파일 빌드
python -m PyInstaller pension.spec --noconfirm --clean
if errorlevel 1 (
    echo   빌드에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  빌드 완료
echo    dist\연금계리산출.exe   더블클릭하면 GUI 가 뜹니다
echo    dist\pension-cli.exe    pension-cli calc 명부.xlsm 기초율.xlsx -o 결과.xlsx
echo ============================================================
echo.
pause
