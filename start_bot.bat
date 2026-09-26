@echo off
REM Startar boten och startar om den automatiskt om den skulle krascha.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
:loop
python run_bot.py
echo Boten stoppades (kod %errorlevel%). Startar om om 30 sekunder... (Ctrl+C for att avbryta)
timeout /t 30 /nobreak >nul
goto loop
