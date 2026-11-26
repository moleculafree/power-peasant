; Power Peasant - NSIS installer
;
; Built by packaging/build.ps1 -Installer. Everything is passed as defines:
;   /DPP_SOURCE=<abs path to dist\PowerPeasant>
;   /DPP_OUTFILE=<abs path to the setup exe>
;   /DPP_VERSION=1.2.3          (numeric, for the file version)
;   /DPP_BUILD=v1.2.3           (display version)
; Default install folder: C:\Program Files\PowerPeasant (the folder, not the root).

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef PP_SOURCE
  !define PP_SOURCE "..\dist\PowerPeasant"
!endif
!ifndef PP_OUTFILE
  !define PP_OUTFILE "..\dist\release\PowerPeasant-setup.exe"
!endif
!ifndef PP_VERSION
  !define PP_VERSION "0.0.0"
!endif
!ifndef PP_BUILD
  !define PP_BUILD "dev"
!endif
!ifndef PP_ICON
  !define PP_ICON "app.ico"
!endif

Name "Power Peasant"
BrandingText "Power Peasant ${PP_BUILD}"
OutFile "${PP_OUTFILE}"
InstallDir "$PROGRAMFILES64\PowerPeasant"
InstallDirRegKey HKLM "Software\PowerPeasant" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

VIProductVersion "${PP_VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "Power Peasant"
VIAddVersionKey /LANG=1033 "FileDescription" "Power Peasant installer"
VIAddVersionKey /LANG=1033 "FileVersion" "${PP_VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${PP_VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "moleculafree"

!define MUI_ABORTWARNING
!define MUI_ICON "${PP_ICON}"
!define MUI_UNICON "${PP_ICON}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Ukrainian"
!insertmacro MUI_LANGUAGE "German"

; Refuse the bare Program Files root: uninstalling there would be dangerous.
Function .onVerifyInstDir
  ${If} $INSTDIR == "$PROGRAMFILES64"
  ${OrIf} $INSTDIR == "$PROGRAMFILES"
    Abort
  ${EndIf}
FunctionEnd

; Shortcuts and registry entries are machine-wide (admin install).
; SetRegView keeps the uninstall entry visible in the 64-bit Apps & Features list.
Function .onInit
  SetShellVarContext all
  SetRegView 64
FunctionEnd

Function un.onInit
  SetShellVarContext all
  SetRegView 64
FunctionEnd

Section "Power Peasant" SecMain
  SetOutPath "$INSTDIR"
  File /r "${PP_SOURCE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Runtime data (history, config, WebView2 profile) lives in data\ next to the
  ; exe, so it must stay writable when the app is installed in Program Files.
  CreateDirectory "$INSTDIR\data"
  nsExec::ExecToLog 'icacls "$INSTDIR\data" /grant *S-1-5-32-545:(OI)(CI)M'
  Pop $0
  StrCmp $0 "0" +2
    DetailPrint "Warning: could not grant write access to $INSTDIR\data (code $0)"

  ; Older builds wrote their uninstall entry to the 32-bit registry view.
  SetRegView 32
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant"
  DeleteRegKey HKLM "Software\PowerPeasant"
  SetRegView 64

  CreateDirectory "$SMPROGRAMS\Power Peasant"
  CreateShortcut "$SMPROGRAMS\Power Peasant\Power Peasant.lnk" "$INSTDIR\PowerPeasant.exe"
  CreateShortcut "$SMPROGRAMS\Power Peasant\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\Power Peasant.lnk" "$INSTDIR\PowerPeasant.exe"

  WriteRegStr HKLM "Software\PowerPeasant" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\PowerPeasant" "Version" "${PP_BUILD}"

  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "DisplayName" "Power Peasant"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "DisplayVersion" "${PP_BUILD}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "Publisher" "moleculafree"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "DisplayIcon" "$INSTDIR\PowerPeasant.exe"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                   "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant" \
                     "EstimatedSize" "$0"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\Power Peasant.lnk"
  Delete "$SMPROGRAMS\Power Peasant\Power Peasant.lnk"
  Delete "$SMPROGRAMS\Power Peasant\Uninstall.lnk"
  RMDir "$SMPROGRAMS\Power Peasant"

  ; targeted deletes, never RMDir /r on the whole install dir
  RMDir /r "$INSTDIR\_internal"
  RMDir /r "$INSTDIR\assets"
  Delete "$INSTDIR\PowerPeasant.exe"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$INSTDIR\*.*"

  ; history/settings (and the WebView2 profile) live in data\; ask before deleting
  IfSilent keep_data
  MessageBox MB_YESNO|MB_ICONQUESTION "Delete history and settings in $INSTDIR\data as well?" IDNO keep_data
  RMDir /r "$INSTDIR\data"
  keep_data:
  RMDir "$INSTDIR"

  ; remove both registry views: older builds used the 32-bit one
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant"
  DeleteRegKey HKLM "Software\PowerPeasant"
  SetRegView 32
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\PowerPeasant"
  DeleteRegKey HKLM "Software\PowerPeasant"
SectionEnd
