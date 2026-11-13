@echo off
setlocal EnableExtensions
title Chip Audio Decoder - Build a standalone .exe
cd /d "%~dp0"
set "APPDIR=%LOCALAPPDATA%\ChipAudioDecoder"
set "VPY=%APPDIR%\venv\Scripts\python.exe"
echo.
echo   Builds a standalone copy that runs on a Windows PC with no Python installed.
echo   Result: the folder  dist\ChipAudioDecoder  next to this file. Copy the whole folder
echo   to another PC and start ChipAudioDecoder.exe inside it.
echo.
if not exist "%VPY%" goto needinstall
"%VPY%" -m pip install --disable-pip-version-check --quiet pyinstaller
if errorlevel 1 goto fail
"%VPY%" -m PyInstaller --noconfirm --clean --windowed --name ChipAudioDecoder ^
  --icon "%~dp0chipaudio\gui\icon.ico" --add-data "%~dp0chipaudio\gui\icon.ico;chipaudio\gui" ^
  --add-data "%~dp0chipaudio\readers\xgecu;chipaudio\readers\xgecu" ^
  --paths "%~dp0." --distpath "%~dp0dist" --workpath "%TEMP%\chipaudio_build" --specpath "%TEMP%\chipaudio_build" ^
  "%~dp0tools\launch.py"
if errorlevel 1 goto fail
echo.
echo   Built: %~dp0dist\ChipAudioDecoder\ChipAudioDecoder.exe
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
