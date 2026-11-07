@echo off
rem Aktualizacja listy spawnow: zbiera dane z tibiadraptor.com i publikuje je w repozytorium.
cd /d "%~dp0"
if not exist .venv py -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt || goto :err
.venv\Scripts\python tools\publish_spawns.py %*
pause
exit /b 0
:err
echo Nie udalo sie zainstalowac bibliotek.
pause
exit /b 1
