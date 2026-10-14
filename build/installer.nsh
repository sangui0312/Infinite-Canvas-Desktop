!ifndef IC_INSTALLER_DIRECTORY_INCLUDED
!define IC_INSTALLER_DIRECTORY_INCLUDED

!include LogicLib.nsh
!include FileFunc.nsh

!ifndef BUILD_UNINSTALLER
  ; NSIS uses the final component of InstallDir as the Browse-dialog suffix.
  ; electron-builder still selects the real per-user/previous install location.
  InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_FILENAME}"

  Function IC_NormalizeInstallRoot
    Push $0
    ${GetRoot} "$INSTDIR" $0
    ${If} $0 != ""
      ${If} $INSTDIR == $0
      ${OrIf} $INSTDIR == "$0\"
      ${OrIf} $INSTDIR == "$0/"
        StrCpy $INSTDIR "$0\${PRODUCT_FILENAME}"
      ${EndIf}
    ${EndIf}
    Pop $0
  FunctionEnd

  !macro customInit
    ; Also protect a root-only /D= argument, including silent installation.
    Call IC_NormalizeInstallRoot
  !macroend
!endif

!endif
