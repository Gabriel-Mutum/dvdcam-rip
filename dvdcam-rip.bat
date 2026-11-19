@echo off
rem Drag a disc folder (or the DVD drive) onto this file to convert it.
rem You can also run it from a terminal: dvdcam-rip.bat "D:\"
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if "%~1"=="" (
  echo Drag the folder with your disc files ^(VIDEO_TS^), or the DVD drive, onto dvdcam-rip.bat.
  echo.
  pause
  exit /b 2
)
python -m dvdcam_rip "%~1" --open %2 %3 %4 %5 %6 %7 %8 %9
echo.
pause
