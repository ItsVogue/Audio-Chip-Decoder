@echo off
setlocal EnableExtensions
title Audio Chip Decoder - Install
cd /d "%~dp0"

set "APPDIR=%LOCALAPPDATA%\AudioChipDecoder"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "PYSETUP=%TEMP%\python-3.12.10-amd64.exe"
set "PYEXE="

echo.
echo   Audio Chip Decoder - installer
echo   ------------------------------
echo   Installs for this Windows user only. No administrator rights are needed.
echo   Program folder: %APPDIR%
echo.

if not exist "%~dp0audiochip\__init__.py" goto notunzipped

rem ---- Step 1: find a 64-bit Python, version 3.10 or newer ----------------
echo   [1/5] Looking for Python...
call :trypy py -3.12
if defined PYEXE goto havepy
call :trypy py -3.13
if defined PYEXE goto havepy
call :trypy py -3.11
if defined PYEXE goto havepy
call :trypy py -3.10
if defined PYEXE goto havepy
call :trypy "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PYEXE goto havepy
call :trypy py -3
if defined PYEXE goto havepy
call :trypy python
if defined PYEXE goto havepy

echo         Python was not found. Downloading Python 3.12 from python.org (about 26 MB)...
curl.exe -L --fail --silent --show-error -o "%PYSETUP%" "%PYURL%"
if not errorlevel 1 goto gotsetup
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYSETUP%'"
:gotsetup
if not exist "%PYSETUP%" goto nopython
echo         Installing Python for this user. This takes a minute or two...
start /wait "" "%PYSETUP%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_test=0 Include_doc=0
call :trypy "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE goto nopython

:havepy
echo         Using: %PYEXE%

rem ---- Remove an install made under the app's earlier name ---------------
if exist "%LOCALAPPDATA%\ChipAudioDecoder\app" echo         Removing the earlier install, which had a different name...
if exist "%LOCALAPPDATA%\ChipAudioDecoder\app" rmdir /s /q "%LOCALAPPDATA%\ChipAudioDecoder"
if exist "%USERPROFILE%\Desktop\Chip Audio Decoder.lnk" del "%USERPROFILE%\Desktop\Chip Audio Decoder.lnk"
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Chip Audio Decoder.lnk" del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Chip Audio Decoder.lnk"

rem ---- Step 2: copy the program ------------------------------------------
echo   [2/5] Copying the program...
if not exist "%APPDIR%\app" mkdir "%APPDIR%\app"
robocopy "%~dp0audiochip" "%APPDIR%\app\audiochip" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto copyfail
copy /y "%~dp0requirements.txt" "%APPDIR%\app\requirements.txt" >nul
if errorlevel 1 goto copyfail

rem ---- Step 3: private Python environment --------------------------------
echo   [3/5] Creating a private Python environment...
if not exist "%APPDIR%\venv\Scripts\python.exe" %PYEXE% -m venv "%APPDIR%\venv"
if not exist "%APPDIR%\venv\Scripts\python.exe" goto venvfail

rem ---- Step 4: components (NumPy and Qt) ---------------------------------
echo   [4/5] Downloading components (about 120 MB the first time)...
"%APPDIR%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet --upgrade pip
"%APPDIR%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%APPDIR%\app\requirements.txt"
if errorlevel 1 goto pipfail
pushd "%APPDIR%\app"
"%APPDIR%\venv\Scripts\python.exe" -c "import numpy, PySide6.QtWidgets, audiochip.gui.main_window"
set "TESTRESULT=%ERRORLEVEL%"
popd
if not "%TESTRESULT%"=="0" goto testfail

rem ---- Step 5: shortcuts --------------------------------------------------
echo   [5/5] Creating shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make-shortcuts.ps1" -AppDir "%APPDIR%"
if errorlevel 1 echo         Shortcuts could not be created. Start the app with "%APPDIR%\Audio Chip Decoder.bat" instead.

> "%APPDIR%\Audio Chip Decoder.bat" echo @echo off
>> "%APPDIR%\Audio Chip Decoder.bat" echo cd /d "%APPDIR%\app"
>> "%APPDIR%\Audio Chip Decoder.bat" echo start "" "%APPDIR%\venv\Scripts\pythonw.exe" -m audiochip %%*

echo.
echo   Done. Start "Audio Chip Decoder" from the desktop or the Start menu.
echo.
pause
exit /b 0

rem ---- helpers -------------------------------------------------------------
:trypy
%* -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" >nul 2>&1
if not errorlevel 1 set "PYEXE=%*"
exit /b 0

:notunzipped
echo   The program files are not next to this installer.
echo   Unzip the whole download into a folder first, then run Install.bat from that folder.
goto fail

:nopython
echo.
echo   Python could not be downloaded or installed automatically.
echo   Install Python 3.12 (64-bit) from https://www.python.org/downloads/ and run Install.bat again.
goto fail

:copyfail
echo.
echo   The program files could not be copied to %APPDIR%.
echo   Close Audio Chip Decoder if it is running, then run Install.bat again.
goto fail

:venvfail
echo.
echo   The private Python environment could not be created in %APPDIR%\venv.
goto fail

:pipfail
echo.
echo   The components could not be downloaded. Check the internet connection and run Install.bat again.
echo   If the message above mentions "No matching distribution", install Python 3.12 (64-bit)
echo   from https://www.python.org/downloads/ and run Install.bat again.
goto fail

:testfail
echo.
echo   The program was installed but failed its start-up test. The message above says why.
goto fail

:fail
echo.
echo   Installation did not finish.
echo.
pause
exit /b 1
