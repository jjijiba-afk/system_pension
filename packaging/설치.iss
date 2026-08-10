; 연금계리 산출 시스템 — 윈도우 설치 프로그램 (Inno Setup 6)
;
; 관리자 권한을 요구하지 않는다. 회사 PC 는 관리자 권한이 막혀 있는 경우가
; 흔한데, Program Files 에 설치하도록 만들면 그런 PC 에서는 아예 못 쓴다.
; 사용자 폴더에 설치하면 권한 없이 설치되고 지우기도 쉽다.
;
; 파이썬은 설치하지 않는다 — 필요가 없다. 실행 파일 안에 파이썬 해석기가
; 함께 묶여 있고, 전체 기능 화면의 계산은 브라우저 안(WebAssembly)에서 돈다.
;
; 빌드::
;
;     iscc /DSourceDir=..\dist\연금계리산출 /DOutputDir=..\dist packaging\설치.iss

#define AppName "연금계리 산출 시스템"
#define AppVersion "1.0.0"
#define AppExe "연금계리산출.exe"

#ifndef SourceDir
  #define SourceDir "..\dist\연금계리산출"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\연금계리산출
DefaultGroupName=연금계리 산출 시스템
DisableProgramGroupPage=yes
DisableDirPage=no

; 사용자 폴더에 설치한다 — 관리자 권한이 필요 없다.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

OutputDir={#OutputDir}
OutputBaseFilename=연금계리산출_설치
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

; 64비트 윈도우에서만 돌아간다(PyInstaller 가 64비트로 묶는다).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}

[Files]
; 꾸러미를 통째로 옮긴다. 실행 파일 둘, 기본자료, 설명서.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\연금계리 산출"; Filename: "{app}\{#AppExe}"
Name: "{group}\기본자료 폴더 열기"; Filename: "{app}\기본자료"
Name: "{group}\설명서"; Filename: "{app}\설명서"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\연금계리 산출"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 아이콘 만들기"; GroupDescription: "추가 작업:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "지금 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 설치 폴더에 남는 것들. 산출 내역과 등록 자료는 %APPDATA%\연금계리산출 에
; 있으므로 **지우지 않는다** — 프로그램을 지웠다고 작업물까지 없애면 안 된다.
Type: filesandordirs; Name: "{app}\기본자료"

[Messages]
; Inno 는 한국어 번역을 기본 제공하지 않는다. 마법사 버튼은 영어로 두되,
; 사람이 실제로 읽는 문장만 우리말로 바꾼다.
WelcomeLabel1=[name] 설치
WelcomeLabel2=이 컴퓨터에 [name/ver] 을(를) 설치합니다.%n%n관리자 권한이 필요 없고, 파이썬 등 따로 설치할 것도 없습니다. 계속하려면 Next 를 누르세요.
FinishedHeadingLabel=설치가 끝났습니다
FinishedLabel=[name] 을(를) 설치했습니다.%n%n분석 그래프·계리평가 보고서·산출 내역까지 전부 프로그램 안에 들어 있습니다. 처음이라면 [기본자료] 의 명부와 기초율로 한 번 돌려 보세요.
ClickFinish=Finish 를 눌러 마칩니다.
