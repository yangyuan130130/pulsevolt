@echo off
setlocal
cd /d "%~dp0"

rem 本机路径覆盖（build.local.bat 已加入 .gitignore，不会被提交）
if exist "%~dp0build.local.bat" call "%~dp0build.local.bat"
if not defined PYINSTALLER set "PYINSTALLER=pyinstaller"

echo ==========================================
echo   构建 PulseVolt.exe（约 1 分钟）
echo ==========================================
echo.

"%PYINSTALLER%" --noconfirm --onefile --windowed --name PulseVolt ^
  --icon icon.ico ^
  --add-data "panel/static;panel/static" ^
  --add-data "icon.ico;." ^
  --collect-all winrt ^
  --hidden-import pystray._win32 ^
  --hidden-import panel.server ^
  main.py
if errorlevel 1 goto fail

echo.
echo [2/3] 复制运行时数据（配置、协议库、插件）...
copy /y settings.json dist\settings.json >nul
if exist protocols xcopy /y /i /e /q protocols dist\protocols >nul
if exist plugins   xcopy /y /i /e /q plugins   dist\plugins   >nul
if not exist dist\captures md dist\captures >nul
rem 界面文件另存一份到 dist：单文件版的临时解压目录被清理时靠它兜底
if exist panel\static xcopy /y /i /e /q panel\static dist\panel\static >nul

echo [3/3] 完成
echo.
dir /b dist
echo.
echo 双击 dist\PulseVolt.exe 即可打开控制台。
pause
exit

:fail
echo.
echo 构建失败，请查看上面的编译输出。
pause
exit
