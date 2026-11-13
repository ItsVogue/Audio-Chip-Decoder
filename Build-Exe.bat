@echo off
setlocal EnableExtensions
title Audio Chip Decoder - Build a standalone .exe
cd /d "%~dp0"
set "APPDIR=%LOCALAPPDATA%\AudioChipDecoder"
set "VPY=%APPDIR%\venv\Scripts\python.exe"
echo.
echo   Builds a standalone copy that runs on a Windows PC with no Python installed.
echo   Result: the folder  dist\AudioChipDecoder  next to this file. Copy the whole folder
echo   to another PC and start AudioChipDecoder.exe inside it.
echo.
if not exist "%VPY%" goto needinstall
"%VPY%" -m pip install --disable-pip-version-check --quiet pyinstaller
if errorlevel 1 goto fail
"%VPY%" -m PyInstaller --noconfirm --clean --windowed --name AudioChipDecoder ^
  --icon "%~dp0audiochip\gui\icon.ico" --add-data "%~dp0audiochip\gui\icon.ico;audiochip\gui" ^
  --add-data "%~dp0audiochip\readers\xgecu;audiochip\readers\xgecu" ^
  --paths "%~dp0." --distpath "%~dp0dist" --workpath "%TEMP%\audiochip_build" --specpath "%TEMP%\audiochip_build" ^
  "%~dp0tools\launch.py"
if errorlevel 1 goto fail
echo.
echo   Built: %~dp0dist\AudioChipDecoder\AudioChipDecoder.exe
echo.
pause
exit /b 0
:needinstall
echo   Run Install.bat first. The build uses the Python environment it creates.
:fail
echo.
echo   The build did not finish.
echo.
pause
exit /b 1
