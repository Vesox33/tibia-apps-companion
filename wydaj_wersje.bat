@echo off
rem Wydanie nowej wersji: buduje .exe i publikuje je na GitHubie (aktualizacja trafia do uzytkownikow).
cd /d "%~dp0"
if not exist .venv py -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt pyinstaller || goto :err
.venv\Scripts\python tools\release.py
pause
exit /b 0
:err
echo Nie udalo sie zainstalowac bibliotek.
pause
exit /b 1
