@echo off
rem Buduje TibiaAppsCompanion.exe (folder dist\TibiaAppsCompanion). Uruchom w folderze programu.
cd /d "%~dp0"
if not exist .venv py -m venv .venv
.venv\Scripts\pip install -r requirements.txt pyinstaller || goto :err
.venv\Scripts\pyinstaller --noconfirm --clean --onedir --windowed --name TibiaAppsCompanion --icon assets\icon.ico ^
  --add-data "assets;assets" ^
  --add-data "huntuploader\data;huntuploader/data" ^
  main.py || goto :err
echo.
echo Gotowe: dist\TibiaAppsCompanion\TibiaAppsCompanion.exe  (przenos caly folder, nie sam .exe)
pause
exit /b 0
:err
echo.
echo Budowanie nie powiodlo sie - zobacz komunikaty powyzej.
pause
exit /b 1
