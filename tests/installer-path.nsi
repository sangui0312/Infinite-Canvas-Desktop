; Executes the production NSIS path function without installing or touching a drive root.
Unicode true
RequestExecutionLevel user
SilentInstall silent
Name "Infinite Canvas path checks"
OutFile "..\.test-data\installer-path-test.exe"
!define PRODUCT_FILENAME "Infinite Canvas"
!include "..\build\installer.nsh"

!macro CheckPath INPUT EXPECTED
  StrCpy $INSTDIR "${INPUT}"
  Call IC_NormalizeInstallRoot
  ${If} $INSTDIR != "${EXPECTED}"
    FileWrite $9 "FAIL ${INPUT} => $INSTDIR$\r$\n"
    FileClose $9
    SetErrorLevel 1
    Quit
  ${EndIf}
  FileWrite $9 "PASS ${INPUT} => $INSTDIR$\r$\n"
!macroend

Function .onInit
  FileOpen $9 "$EXEDIR\installer-path-results.txt" w
  !insertmacro CheckPath "D:\" "D:\Infinite Canvas"
  !insertmacro CheckPath "E:" "E:\Infinite Canvas"
  !insertmacro CheckPath "F:/" "F:\Infinite Canvas"
  !insertmacro CheckPath "D:\Infinite Canvas" "D:\Infinite Canvas"
  !insertmacro CheckPath "D:\Infinite Canvas\" "D:\Infinite Canvas"
  !insertmacro CheckPath "E:\Apps\Infinite Canvas" "E:\Apps\Infinite Canvas"
  !insertmacro CheckPath "E:\My Custom Folder" "E:\My Custom Folder"
  !insertmacro CheckPath "\\server\share\" "\\server\share\Infinite Canvas"
  !insertmacro CheckPath "\\server\share\Infinite Canvas" "\\server\share\Infinite Canvas"
  StrCpy $INSTDIR "D:\"
  Call IC_NormalizeInstallRoot
  Call IC_NormalizeInstallRoot
  ${If} $INSTDIR != "D:\Infinite Canvas"
    FileClose $9
    SetErrorLevel 1
    Quit
  ${EndIf}
  FileWrite $9 "PASS repeated normalization$\r$\n"
  FileClose $9
  SetErrorLevel 0
  Quit
FunctionEnd

Section
SectionEnd
