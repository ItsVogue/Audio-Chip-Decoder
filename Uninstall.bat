@echo off
setlocal EnableExtensions
title Audio Chip Decoder - Uninstall
set "APPDIR=%LOCALAPPDATA%\AudioChipDecoder"
echo.
echo   This removes Audio Chip Decoder from: %APPDIR%
echo   Your dumps, exported WAV files and saved device profiles are not touched.
echo.
choice /c YN /m "  Remove it"
if errorlevel 2 exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($f in 'Desktop','Programs') { $p = Join-Path ([Environment]::GetFolderPath($f)) 'Audio Chip Decoder.lnk'; if (Test-Path $p) { Remove-Item $p } }"
if exist "%APPDIR%" rmdir /s /q "%APPDIR%"
if exist "%APPDIR%" echo   Some files are in use. Close Audio Chip Decoder and run Uninstall.bat again.
if not exist "%APPDIR%" echo   Removed.
echo.
pause
