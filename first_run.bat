@echo off
setlocal
call install.bat
if errorlevel 1 exit /b 1
call run.bat
endlocal
