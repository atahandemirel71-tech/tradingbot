# MT5 Trading Bot (24/7)

En Python-bot som kopplar upp sig mot MetaTrader 5 och automatiskt köper och säljer dygnet runt.

> ⚠️ **Viktigt:** Trading med hävstång innebär hög risk och du kan förlora mer än du tror.
> Ingen strategi vinner garanterat. Kör boten på ett **demokonto** i flera veckor innan du
> ens överväger riktiga pengar. Boten startar i `dry_run: true` (simulering) som standard.

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
När du är nöjd: sätt `dry_run: false` (fortfarande på demokonto först!).

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
