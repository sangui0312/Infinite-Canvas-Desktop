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
!else
  !include nsDialogs.nsh
  Var IC_DeletePersonal
  Var IC_DeleteCheckbox
  Var IC_DataPath
  Var IC_Deletable
  Var IC_IsUpdate

  !macro customUnInit
    StrCpy $IC_IsUpdate "0"
    ${GetParameters} $R0
    ClearErrors
    ${GetOptions} $R0 "--updated" $R1
    ${IfNot} ${Errors}
      StrCpy $IC_IsUpdate "1"
    ${EndIf}
  !macroend

  !macro customUnWelcomePage
    !insertmacro MUI_UNPAGE_WELCOME
    UninstPage custom un.IC_StoragePage un.IC_StorageLeave
  !macroend

  Function un.IC_StoragePage
    ${If} $IC_IsUpdate == "1"
      Abort
    ${EndIf}
    nsDialogs::Create 1018
    Pop $0
    ${If} $0 == error
      Abort
    ${EndIf}
    StrCpy $IC_DeletePersonal "0"
    StrCpy $IC_DataPath "未能读取，个人资料将保留。"
    StrCpy $IC_Deletable "0"
    InitPluginsDir
    ExecWait '"$INSTDIR\${PRODUCT_FILENAME}.exe" --storage-info "$PLUGINSDIR\storage.ini"' $1
    ${If} $1 == 0
      ReadINIStr $IC_DataPath "$PLUGINSDIR\storage.ini" InfiniteCanvas path
      ReadINIStr $IC_Deletable "$PLUGINSDIR\storage.ini" InfiniteCanvas deletable
    ${EndIf}
    ${NSD_CreateLabel} 0 0 100% 24u "卸载软件时，默认保留个人资料。"
    Pop $0
    ${NSD_CreateLabel} 0 30u 100% 52u "$IC_DataPath"
    Pop $0
    ${NSD_CreateCheckbox} 0 88u 100% 20u "同时删除个人数据（画布、素材、API 配置和设置）"
    Pop $IC_DeleteCheckbox
    ${NSD_SetState} $IC_DeleteCheckbox ${BST_UNCHECKED}
    ${If} $IC_Deletable != "1"
      EnableWindow $IC_DeleteCheckbox 0
    ${EndIf}
    ${NSD_CreateLabel} 0 117u 100% 40u "只处理当前软件管理的数据文件夹。迁移前保留的备份和外部引用素材不会删除。"
    Pop $0
    nsDialogs::Show
  FunctionEnd

  Function un.IC_StorageLeave
    ${NSD_GetState} $IC_DeleteCheckbox $IC_DeletePersonal
  FunctionEnd

  !macro customUnInstall
    ; Upgrade/silent uninstall must never remove user files or show deletion UI.
    ${If} $IC_IsUpdate != "1"
      ${If} $IC_DeletePersonal == ${BST_CHECKED}
      ${AndIfNot} ${Silent}
        ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --storage-uninstall --remove-personal-data' $0
      ${Else}
        ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --storage-uninstall' $0
      ${EndIf}
      ${If} $0 != 0
        MessageBox MB_OK|MB_ICONEXCLAMATION "个人资料或运行文件未通过清理检查，已停止卸载。请检查数据目录与权限后重试。" /SD IDOK
        Abort
      ${EndIf}
    ${EndIf}
  !macroend

  !macro customRemoveFiles
    ; Never recursively remove $INSTDIR: personal data may live beside the app.
    SetOutPath $TEMP
    StrCpy $R7 0
    ic_remove_program_retry:
    RMDir /r "$INSTDIR\locales"
    RMDir /r "$INSTDIR\resources"
    !insertmacro IC_RemoveProgramFiles
    ; Chromium helper children may release the executable just after ExecWait.
    IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" ic_remove_program_wait
    IfFileExists "$INSTDIR\resources\app.asar" ic_remove_program_wait ic_remove_program_done
    ic_remove_program_wait:
      IntOp $R7 $R7 + 1
      ${If} $R7 < 30
        Sleep 200
        Goto ic_remove_program_retry
      ${EndIf}
      MessageBox MB_OK|MB_ICONEXCLAMATION "程序文件仍被占用，请退出所有 Infinite Canvas 窗口后重试卸载。" /SD IDOK
      Abort
    ic_remove_program_done:
    RMDir "$INSTDIR"
  !macroend

  !macro IC_RemoveProgramFiles
    Delete "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
    Delete "$INSTDIR\${UNINSTALL_FILENAME}"
    Delete "$INSTDIR\chrome_100_percent.pak"
    Delete "$INSTDIR\chrome_200_percent.pak"
    Delete "$INSTDIR\d3dcompiler_47.dll"
    Delete "$INSTDIR\dxcompiler.dll"
    Delete "$INSTDIR\dxil.dll"
    Delete "$INSTDIR\ffmpeg.dll"
    Delete "$INSTDIR\icudtl.dat"
    Delete "$INSTDIR\libEGL.dll"
    Delete "$INSTDIR\libGLESv2.dll"
    Delete "$INSTDIR\LICENSE.electron.txt"
    Delete "$INSTDIR\LICENSES.chromium.html"
    Delete "$INSTDIR\resources.pak"
    Delete "$INSTDIR\snapshot_blob.bin"
    Delete "$INSTDIR\v8_context_snapshot.bin"
    Delete "$INSTDIR\vk_swiftshader.dll"
    Delete "$INSTDIR\vk_swiftshader_icd.json"
    Delete "$INSTDIR\vulkan-1.dll"
  !macroend
!endif

!endif
