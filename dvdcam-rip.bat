@echo off
rem Drag a disc folder, the DVD drive or an ISO file onto this file to convert it.
rem You can also run it from a terminal: dvdcam-rip.bat "D:\" [options]
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "DVDCAM_RIP_LAUNCHER=bat"
rem Use "py" (the Python launcher) if there is one, otherwise "python".
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python isn't installed. Install it with:  winget install Python.Python.3.13
  echo or from https://www.python.org/downloads/
  echo.
  pause
  exit /b 9009
)
if "%~1"=="" (
  echo Drag the folder with your disc files ^(VIDEO_TS or DVD_RTAV^), the DVD drive,
  echo or an ISO file onto dvdcam-rip.bat.
  echo.
  pause
  exit /b 2
)
rem dvdcam-rip can remove its own folder, this file included. "(goto) 2>nul" on the
rem same line ends this script without reading the (possibly deleted) file again.
rem dvdcam-rip itself waits for Enter after an error, so the window stays readable.
%PY% -m dvdcam_rip "%~1" %2 %3 %4 %5 %6 %7 %8 %9 & (goto) 2>nul
