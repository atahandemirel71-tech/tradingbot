@echo off
REM Installerar allt som boten behover. Dubbelklicka en gang.
cd /d "%~dp0"
echo ==== MT5 Trading Bot - installation ====
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo Python hittades inte.
    echo Installera Python 3.10 eller nyare fran https://www.python.org/downloads/
    echo och bocka i "Add Python to PATH" i installationen. Kor sedan denna fil igen.
    pause
    exit /b 1
)
python --version
if not exist .venv (
    echo Skapar virtuell miljo...
    python -m venv .venv || goto fail
)
call .venv\Scripts\activate.bat || goto fail
echo Installerar paket (kan ta en minut)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto fail
if not exist .env (
    copy .env.example .env >nul
    echo.
    echo Fyll i ditt MT5-konto, losenord och server i filen som oppnas nu, spara och stang.
    notepad .env
)
echo.
echo ==== Klart! ====
echo  1. Oppna MT5, logga in pa DEMOKONTO och sla pa knappen "Algo Trading".
echo  2. Dubbelklicka backtest.bat for att testa strategin pa historisk data.
echo  3. Dubbelklicka start_bot.bat for att starta boten (simulering tills du
echo     satter dry_run: false i config.yaml).
pause
exit /b 0
:fail
echo.
echo Nagot gick fel - se felmeddelandet ovan.
pause
exit /b 1
