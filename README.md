# MT5 Trading Bot (24/7)

En Python-bot som kopplar upp sig mot MetaTrader 5 och automatiskt köper och säljer dygnet runt.

> ⚠️ **Viktigt:** Trading med hävstång innebär hög risk och du kan förlora mer än du tror.
> Ingen strategi vinner garanterat. Kör boten på ett **demokonto** i flera veckor innan du
> ens överväger riktiga pengar. Boten startar i `dry_run: true` (simulering) som standard.

## Snabbstart (Windows)

1. Installera **MetaTrader 5** och logga in på ett **demokonto**. Slå på knappen **Algo Trading**.
2. Installera **Python 3.10+** från python.org (bocka i "Add Python to PATH").
3. Packa upp zip-filen och dubbelklicka **`installera.bat`** – fyll i konto/lösenord/server när anteckningar öppnas.
4. Dubbelklicka **`backtest.bat`** för att testa strategin på historisk data.
5. Dubbelklicka **`start_bot.bat`** för att starta boten (simulering tills du sätter `dry_run: false`).

Detaljerna nedan förklarar varje steg.

## Alternativ: Expert Advisor direkt i MT5 (ingen Python behövs)

Python-boten körs som ett eget program **bredvid** MT5 och syns därför inte i MT5:s Navigator.
Vill du ha boten **inne i MT5** använder du `mql5/MT5TradingBot.mq5` – samma strategi och riskregler.

1. I MT5: *Arkiv → Öppna datamapp* (File → Open Data Folder).
2. Gå till `MQL5\Experts` och kopiera in `MT5TradingBot.mq5`.
3. Tryck **F4** (öppnar MetaEditor), öppna filen och tryck **F7** (Kompilera). Det ska stå `0 errors`.
4. Tillbaka i MT5: högerklicka *Expert Advisors* i Navigator (Ctrl+N) → *Uppdatera*. `MT5TradingBot` syns nu.
5. Öppna ett diagram (t.ex. EURUSD, M15) och dra EA:n till diagrammet. Bocka i **Allow Algo Trading**
   och slå på knappen **Algo Trading** i verktygsfältet.
6. `InpDryRun = true` betyder att den bara loggar (fliken *Experter*). Sätt `false` för att handla på riktigt.
   Dra EA:n till ett diagram per symbol du vill handla.

**Backtest i MT5:** tryck *Ctrl+R* (Strategitestare), välj `MT5TradingBot`, symbol, tidsram och period
och klicka *Starta*. I testaren handlar EA:n alltid, oavsett `InpDryRun`.

## Hur den handlar

Strategi (trendföljande) på stängda candles, per symbol:

| Villkor | Köp (BUY) | Sälj (SELL) |
|---|---|---|
| EMA-kors | EMA 9 korsar **upp** över EMA 21 | EMA 9 korsar **ned** under EMA 21 |
| Trendfilter | Pris över EMA 200 | Pris under EMA 200 |
| RSI-filter | RSI under 70 | RSI över 30 |

- **Stop loss** = 1,5 × ATR, **Take profit** = 3,0 × ATR (varje affär har alltid SL/TP hos brokern).
- Vid motsatt signal stängs positionen och vänds.

Riskskydd:
- **Positionsstorlek** räknas ut så att en träffad stop loss kostar max `risk_per_trade_pct` (1 %) av saldot.
- **Max antal öppna positioner** (standard 3).
- **Daglig förlustgräns**: tappar kontot 5 % under dagen pausas nya affärer till nästa dag (UTC).
- **Spreadfilter**: inga affärer när spreaden är för stor.
- Boten rör bara sina egna positioner (identifieras med `magic_number`).
- Återansluter automatiskt om MT5 tappar anslutningen, och `start_bot.bat` startar om boten om den kraschar.

Allt går att ändra i `config.yaml`.

## Installation (Windows)

MetaTrader5-paketet för Python fungerar bara på **Windows**.

1. Installera **MetaTrader 5** från din broker och logga in (gärna ett demokonto).
2. I MT5: *Verktyg → Alternativ → Expert Advisors* → bocka i **Allow algorithmic trading**,
   och slå på knappen **Algo Trading** i verktygsfältet (ska vara grön).
3. Installera **Python 3.10+** från python.org (bocka i "Add Python to PATH").
4. Öppna en terminal i projektmappen:
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
5. Kopiera `.env.example` till `.env` och fyll i konto, lösenord och server
   (eller lämna tomt för att använda kontot som redan är inloggat i terminalen).
6. Kontrollera symbolnamnen i `config.yaml` – vissa brokers använder t.ex. `EURUSD.m` eller `EURUSDm`.

## Starta

```bat
start_bot.bat
```
eller `python run_bot.py`. Loggar skrivs till konsolen och till `logs/bot.log`.

Börja med `dry_run: true` och läs loggen – du ser exakt vilka affärer den *skulle* ha gjort.
När du är nöjd (och backtesten ser bra ut): sätt `dry_run: false` (fortfarande på demokonto först!).

## Backtest – testa strategin på historisk data

Kör **alltid** en backtest innan du låter boten handla. Backtesten använder exakt samma
signaler, stop loss/take profit och positionsstorlek som den riktiga boten, med inställningarna i `config.yaml`.

**Direkt från MT5** (Windows, terminalen igång) – hämtar historik och symboldata från din broker:
```bat
python backtest.py --symbol EURUSD --from 2024-01-01 --to 2026-01-01
python backtest.py --symbol GBPUSD --from 2024-01-01 --timeframe H1
```

**Från CSV-fil** (fungerar på alla datorer). I MT5: *Visa → Symboler → Staplar*, välj symbol och
tidsram, klicka *Begär* och sedan *Exportera staplar*. Kör sedan:
```bat
python backtest.py --csv EURUSD_M15.csv
python backtest.py --csv USDJPY_M15.csv --point 0.001 --tick-value 0.67
```
Generiska CSV-filer med kolumnerna `time,open,high,low,close` (valfritt `spread`) fungerar också.

Exempel på resultat:
```
===== Backtest SYNTH_M15 =====
Antal affärer    385
Vinstandel       31.4 %
Nettoresultat    -2,034.00
Avkastning       -20.34 %
Max drawdown     35.94 %
Profit factor    0.92
...
```
(siffrorna ovan är från slumpmässig testdata, inte riktiga kurser.)

Alla affärer sparas i `backtest_results/trades.csv` och equity-kurvan i `backtest_results/equity.csv`
(öppna i Excel för att göra en graf).

Användbara flaggor: `--balance 5000`, `--spread 15` (points), `--commission 7` (per lot tur och retur),
`--timeframe H1`.

**Så tolkar du resultatet:**
- **Profit factor** över ca 1,3 och en **max drawdown** du klarar av känslomässigt är ett minimum.
- Testa flera symboler och tidsperioder. Om det bara fungerar på en period är det troligen tur.
- Justera inte parametrar tills resultatet ser perfekt ut – det kallas *överoptimering* och
  fungerar nästan aldrig framåt. Testa ändringar på en period och verifiera på en annan.
- Backtesten är något försiktig: träffar en candle både SL och TP räknas det som förlust, och slippage
  utöver spreaden simuleras inte. Varje symbol testas för sig, så gränsen `max_open_positions` gäller inte.

## Köra 24/7

Boten måste köras på en dator som är igång hela tiden, med MT5-terminalen öppen.

- **Rekommenderat: en Windows-VPS** (många brokers erbjuder gratis VPS, annars t.ex. Contabo, Vultr, ForexVPS).
- Starta automatiskt vid inloggning: tryck `Win+R`, skriv `shell:startup`, och lägg en genväg till
  `start_bot.bat` där. Lägg även en genväg till MT5 (`terminal64.exe`) där.
- Stäng av viloläge/strömsparläge i Windows.

Observera: forex-marknaden är stängd på helger. Boten fortsätter köra men handlar inte när marknaden är stängd.

## Testa koden

```bash
pip install -r requirements-dev.txt
pytest
```
Testerna använder en simulerad MT5-klient och kan köras på vilket operativsystem som helst.

## Filer

| Fil | Innehåll |
|---|---|
| `run_bot.py` | Startpunkt |
| `config.yaml` | Alla inställningar |
| `bot/strategy.py` | Indikatorer (EMA, RSI, ATR) och signaler |
| `bot/risk.py` | Positionsstorlek och daglig förlustgräns |
| `bot/trader.py` | Huvudloopen som handlar |
| `bot/mt5_client.py` | Kommunikation med MetaTrader 5 |
| `mql5/MT5TradingBot.mq5` | Samma bot som Expert Advisor inne i MT5 |
| `backtest.py` | Backtest från MT5 eller CSV |
| `bot/backtest.py` | Backtestmotorn |
