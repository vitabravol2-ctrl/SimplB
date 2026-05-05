@echo off
setlocal

if not exist venv\Scripts\activate (
  echo venv not found. Please run install.bat first.
  exit /b 1
)

call venv\Scripts\activate
python main.py

endlocal
