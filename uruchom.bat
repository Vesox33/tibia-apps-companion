@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Pierwsze uruchomienie - instaluje biblioteki, to potrwa chwile...
  py -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\pip install -q -r requirements.txt
start "" .venv\Scripts\pythonw.exe main.py
