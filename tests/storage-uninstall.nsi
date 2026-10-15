; Exercise the production uninstall hooks in workspace-only synthetic installs.
Unicode true
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
Name "Infinite Canvas storage uninstall checks"
OutFile "..\.test-data\storage-uninstall-check.exe"
!include LogicLib.nsh
!include FileFunc.nsh
Var IC_TestUpdate
!define BUILD_UNINSTALLER
!define APP_EXECUTABLE_FILENAME "Infinite Canvas.exe"
!define PRODUCT_FILENAME "Infinite Canvas"
!define UNINSTALL_FILENAME "Uninstall Infinite Canvas.exe"
!define isUpdated '$IC_TestUpdate == "1"'
!include "..\build\installer.nsh"

Section
  ; Only the caller-supplied workspace fixture receives this test uninstaller.
  ClearErrors
  ReadINIStr $R1 "$EXEDIR\storage-uninstall-fixture.ini" InfiniteCanvas path
  IfErrors 0 +2
    Quit
  SetOutPath $R1
  WriteUninstaller "$R1\Uninstall Infinite Canvas.exe"
SectionEnd

Function un.onInit
  !insertmacro customUnInit
  StrCpy $INSTDIR $EXEDIR
  StrCpy $IC_DeletePersonal "0"
  StrCpy $IC_TestUpdate "0"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/TEST-DELETE" $R1
  ${IfNot} ${Errors}
    StrCpy $IC_DeletePersonal "1"
  ${EndIf}
  ClearErrors
  ${GetOptions} $R0 "/TEST-UPDATE" $R1
  ${IfNot} ${Errors}
    StrCpy $IC_TestUpdate "1"
    StrCpy $IC_IsUpdate "1"
  ${EndIf}
FunctionEnd

Section "un.Test"
  ; Enter the section silently, then simulate the interactive checkbox guard.
  ${If} $IC_DeletePersonal == "1"
    SetSilent normal
  ${EndIf}
  !insertmacro customUnInstall
  SetSilent silent
  !insertmacro customRemoveFiles
SectionEnd
