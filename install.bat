@echo off
setlocal

if not exist venv (
  echo Creating virtual environment...
  python -m venv venv
)

call venv\Scripts\activate

if exist requirements.txt (
  pip install -r requirements.txt
) else (
  pip install PySide6 requests
)

echo Install complete.
endlocal
