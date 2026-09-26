@echo off
REM Backtestar strategin med historik direkt fran MT5 (terminalen maste vara igang).
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
set SYMBOL=EURUSD
set /p SYMBOL=Symbol [EURUSD]: 
set FROM=2024-01-01
set /p FROM=Fran datum [2024-01-01]: 
python backtest.py --symbol %SYMBOL% --from %FROM%
echo.
echo Alla affarer finns i backtest_results\trades.csv
pause
