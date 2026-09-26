//+------------------------------------------------------------------+
//|                                               MT5TradingBot.mq5  |
//|  Trendfoljande Expert Advisor: EMA-kors + RSI + EMA 200 + ADX,   |
//|  ATR-baserad SL/TP, riskbaserad lotstorlek, dags- och total-     |
//|  forlustgrans (prop-firma), helgstangning och tidsfilter.        |
//|  Samma regler som Python-boten i detta projekt.                  |
//+------------------------------------------------------------------+
#property copyright "MT5 Trading Bot"
#property version   "1.11"

#include <Trade/Trade.mqh>

//--- Strategi
input group "Strategi"
input ENUM_TIMEFRAMES InpTimeframe     = PERIOD_CURRENT; // Tidsram (CURRENT = diagrammets)
input int             InpFastEMA       = 9;              // Snabb EMA
input int             InpSlowEMA       = 21;             // Langsam EMA
input bool            InpUseTrendFilter= true;           // Anvand trendfilter (EMA 200)
input int             InpTrendEMA      = 200;            // Trend-EMA
input int             InpRSIPeriod     = 14;             // RSI-period
input double          InpRSIOverbought = 70.0;           // Inga kop over denna RSI
input double          InpRSIOversold   = 30.0;           // Inga salj under denna RSI
input int             InpATRPeriod     = 14;             // ATR-period
input double          InpSLATRMult     = 1.5;            // Stop loss = X * ATR
input double          InpTPATRMult     = 3.0;            // Take profit = X * ATR
input bool            InpUseADXFilter  = true;           // Handla bara nar det finns en trend (ADX)
input int             InpADXPeriod     = 14;             // ADX-period
input double          InpADXMin        = 25.0;           // Minsta ADX for att handla
input bool            InpCloseOnOpposite = true;         // Stang och vand vid motsatt signal

//--- Risk
input group "Risk"
input double InpRiskPercent     = 0.5;   // % av saldot som riskeras per affar
input int    InpMaxOpenPositions= 3;     // Max oppna positioner (alla symboler, denna EA)
input double InpMaxDailyLossPct = 4.0;   // Dagsforlust (%) som stoppar handeln resten av dagen
input double InpMaxTotalLossPct = 8.0;   // Total forlust (%) under startsaldot som stoppar EA:n helt
input double InpStartBalance    = 0.0;   // Startsaldo for totalgransen (0 = saldot vid forsta start)
input bool   InpCloseOnLimit    = true;  // Stang aven oppna positioner nar en grans nas
input int    InpMaxSpreadPoints = 30;    // Max spread i points

//--- Tider (brokerns SERVERTID, klockan i MT5)
input group "Tider (servertid)"
input bool   InpCloseBeforeWeekend = true;  // Stang alla positioner fredag kvall
input int    InpFridayCloseHour    = 20;    // Fran denna timme pa fredagar
input bool   InpUseSessionFilter   = false; // Oppna bara nya affarer mellan tiderna nedan (ej D1)
input int    InpSessionStartHour   = 8;     // Starttimme
input int    InpSessionEndHour     = 20;    // Sluttimme (exklusive)

//--- Ovrigt
input group "Ovrigt"
input bool   InpDryRun          = true;      // true = simulera bara (loggar, skickar inga order)
input long   InpMagicNumber     = 20260926;  // Unikt ID for EA:ns positioner
input int    InpDeviationPoints = 20;        // Max slippage i points

CTrade   trade;
int      hFast = INVALID_HANDLE, hSlow = INVALID_HANDLE, hTrend = INVALID_HANDLE;
int      hRSI = INVALID_HANDLE, hATR = INVALID_HANDLE, hADX = INVALID_HANDLE;
datetime lastBarTime = 0;
double   dayStartEquity = 0.0;
double   localDayId = -1;
double   startBalance = 0.0;
bool     guardWarned = false;
bool     halted = false;
bool     weekendLogged = false;

//--- Statistik som skrivs ut nar EA:n stoppas / backtesten ar klar
int      stCrosses = 0, stSkipADX = 0, stSkipTrend = 0, stSkipRSI = 0, stSkipSame = 0;
int      stSkipSession = 0, stSkipMaxPos = 0, stSkipSpread = 0, stSkipStops = 0, stSkipMinLot = 0;
int      stOpened = 0, stRejected = 0, stDailyLimit = 0, stWeekend = 0;
double   stRiskPctSum = 0.0, stRiskPctMax = 0.0, stSpreadMax = 0.0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpFastEMA >= InpSlowEMA)
     { Print("Fel: snabb EMA maste vara mindre an langsam EMA"); return INIT_PARAMETERS_INCORRECT; }
   if(InpRiskPercent <= 0 || InpRiskPercent > 5)
     { Print("Fel: risk per affar maste vara mellan 0 och 5 %"); return INIT_PARAMETERS_INCORRECT; }
   if(InpSLATRMult <= 0 || InpTPATRMult <= 0)
     { Print("Fel: ATR-multiplarna maste vara positiva"); return INIT_PARAMETERS_INCORRECT; }
   if(InpFridayCloseHour < 0 || InpFridayCloseHour > 23 || InpSessionStartHour < 0 ||
      InpSessionStartHour > 23 || InpSessionEndHour < 0 || InpSessionEndHour > 23)
     { Print("Fel: timmar maste vara 0-23"); return INIT_PARAMETERS_INCORRECT; }

   hFast  = iMA(_Symbol, InpTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, InpTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hTrend = iMA(_Symbol, InpTimeframe, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI   = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
   hATR   = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   hADX   = iADXWilder(_Symbol, InpTimeframe, InpADXPeriod);
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hTrend == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE || hADX == INVALID_HANDLE)
     { Print("Kunde inte skapa indikatorer: ", GetLastError()); return INIT_FAILED; }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpDeviationPoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   // Startsaldo for totalgransen sparas i en global variabel sa det overlever omstarter.
   if(InpStartBalance > 0)
      startBalance = InpStartBalance;
   else
     {
      if(!GlobalVariableCheck(GvName("start")))
         GlobalVariableSet(GvName("start"), AccountInfoDouble(ACCOUNT_BALANCE));
      startBalance = GlobalVariableGet(GvName("start"));
     }
   if(InpMaxTotalLossPct > 0)
      PrintFormat("Totalgrans: startsaldo %.2f, EA:n stoppar vid equity %.2f", startBalance,
                  startBalance * (1.0 - InpMaxTotalLossPct / 100.0));

   PrintFormat("%s: tick size %s, tick value %.5f (%s), kontrakt %.2f, lot min %.2f steg %.2f, spread nu %d points",
               _Symbol, DoubleToString(SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE), _Digits),
               TickValue(), AccountInfoString(ACCOUNT_CURRENCY),
               SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE), SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN),
               SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
   PrintFormat("MT5TradingBot startad pa %s %s - %s", _Symbol, EnumToString(Timeframe()),
               IsDryRun() ? "DRY RUN (inga riktiga order)" : "LIVE-HANDEL");
   if(!IsDryRun() && !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("VARNING: 'Algo Trading' ar avstangt i terminalen - sla pa knappen i verktygsfaltet");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   PrintSummary();
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   IndicatorRelease(hTrend);
   IndicatorRelease(hRSI);
   IndicatorRelease(hATR);
   IndicatorRelease(hADX);
  }

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES Timeframe()
  {
   return InpTimeframe == PERIOD_CURRENT ? (ENUM_TIMEFRAMES)_Period : InpTimeframe;
  }

// Dry run galler bara live; i Strategy Tester handlar EA:n alltid (simulerat).
bool IsDryRun()
  {
   return InpDryRun && !MQLInfoInteger(MQL_TESTER);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateDailyGuard();
   if(!CheckLimits())
      return;

   datetime barTime = iTime(_Symbol, Timeframe(), 0);
   if(barTime == 0 || barTime == lastBarTime)
      return;                        // agera bara en gang per ny candle

   int signal = 0;                   // 1 = kop, -1 = salj, 0 = inget
   double atr = 0.0;
   if(!EvaluateSignal(signal, atr))
      return;                        // data inte redo - forsok igen nasta tick
   lastBarTime = barTime;
   if(signal == 0)
      return;

   HandleSignal(signal, atr);
  }

//+------------------------------------------------------------------+
//| Signal pa senaste STANGDA candle (index 1) jamfort med index 2.  |
//+------------------------------------------------------------------+
bool EvaluateSignal(int &signal, double &atr)
  {
   double fast[], slow[], trend[], rsi[], atrBuf[], adxBuf[];
   ArraySetAsSeries(fast, true);
   ArraySetAsSeries(slow, true);
   ArraySetAsSeries(trend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(atrBuf, true);
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(hFast, 0, 1, 2, fast) != 2 || CopyBuffer(hSlow, 0, 1, 2, slow) != 2 ||
      CopyBuffer(hTrend, 0, 1, 1, trend) != 1 || CopyBuffer(hRSI, 0, 1, 1, rsi) != 1 ||
      CopyBuffer(hATR, 0, 1, 1, atrBuf) != 1 || CopyBuffer(hADX, 0, 1, 1, adxBuf) != 1)
      return false;
   if(Bars(_Symbol, Timeframe()) < InpTrendEMA + 5)
      return false;

   double close = iClose(_Symbol, Timeframe(), 1);
   atr = atrBuf[0];
   signal = 0;
   if(atr <= 0 || close <= 0)
      return true;

   bool crossedUp   = fast[1] <= slow[1] && fast[0] > slow[0];
   bool crossedDown = fast[1] >= slow[1] && fast[0] < slow[0];

   if(crossedUp || crossedDown)
      stCrosses++;
   if((crossedUp || crossedDown) && InpUseADXFilter && adxBuf[0] < InpADXMin)
     {
      stSkipADX++;
      PrintFormat("%s: EMA-kors men ADX %.1f < %.1f (ingen trend) - ingen affar", _Symbol, adxBuf[0], InpADXMin);
      return true;
     }

   if(crossedUp)
     {
      if(InpUseTrendFilter && close <= trend[0])
        { stSkipTrend++; PrintFormat("%s: uppkors men under trend-EMA - ingen affar", _Symbol); }
      else if(rsi[0] >= InpRSIOverbought)
        { stSkipRSI++; PrintFormat("%s: uppkors men RSI %.1f overkopt - ingen affar", _Symbol, rsi[0]); }
      else
        { signal = 1; PrintFormat("%s: KOPSIGNAL (EMA-kors upp, RSI %.1f)", _Symbol, rsi[0]); }
     }
   else if(crossedDown)
     {
      if(InpUseTrendFilter && close >= trend[0])
        { stSkipTrend++; PrintFormat("%s: nedkors men over trend-EMA - ingen affar", _Symbol); }
      else if(rsi[0] <= InpRSIOversold)
        { stSkipRSI++; PrintFormat("%s: nedkors men RSI %.1f oversalt - ingen affar", _Symbol, rsi[0]); }
      else
        { signal = -1; PrintFormat("%s: SALJSIGNAL (EMA-kors ned, RSI %.1f)", _Symbol, rsi[0]); }
     }
   return true;
  }

//+------------------------------------------------------------------+
void HandleSignal(int signal, double atr)
  {
   ENUM_POSITION_TYPE wanted = signal > 0 ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;

   // Befintliga positioner for denna symbol och EA
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber ||
         PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) == wanted)
        {
         stSkipSame++;
         PrintFormat("%s: har redan en position at samma hall", _Symbol);
         return;
        }
      if(!InpCloseOnOpposite)
         return;
      PrintFormat("%s: STANGER #%I64u (vinst %.2f) pa motsatt signal", _Symbol, ticket,
                  PositionGetDouble(POSITION_PROFIT));
      if(IsDryRun())
        {
         Print("DRY RUN: stangning skickades inte");
         return;                      // i dry run finns positionen kvar, oppna inte en ny
        }
      if(!trade.PositionClose(ticket))
        {
         PrintFormat("Kunde inte stanga #%I64u: %s", ticket, trade.ResultRetcodeDescription());
         return;
        }
     }

   OpenPosition(signal, atr);
  }

//+------------------------------------------------------------------+
void OpenPosition(int signal, double atr)
  {
   if(!EntriesAllowed(TimeCurrent()))
     { stSkipSession++; PrintFormat("%s: utanfor handelstiderna - ingen ny affar", _Symbol); return; }
   if(CountMyPositions() >= InpMaxOpenPositions)
     { stSkipMaxPos++; PrintFormat("%s: max antal oppna positioner (%d) natt", _Symbol, InpMaxOpenPositions); return; }
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
     { PrintFormat("%s: symbolen gar inte att handla just nu", _Symbol); return; }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0 || point <= 0)
      return;

   double spreadPoints = (ask - bid) / point;
   stSpreadMax = MathMax(stSpreadMax, spreadPoints);
   if(spreadPoints > InpMaxSpreadPoints)
     { stSkipSpread++; PrintFormat("%s: spread %.0f > max %d points - hoppar over", _Symbol, spreadPoints, InpMaxSpreadPoints); return; }

   double entry  = signal > 0 ? ask : bid;
   double slDist = atr * InpSLATRMult;
   double tpDist = atr * InpTPATRMult;
   double sl = NormalizeDouble(signal > 0 ? entry - slDist : entry + slDist, digits);
   double tp = NormalizeDouble(signal > 0 ? entry + tpDist : entry - tpDist, digits);

   double minDist = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * point;
   if(MathAbs(entry - sl) < minDist || MathAbs(entry - tp) < minDist)
     { stSkipStops++; PrintFormat("%s: SL/TP for nara brokerns minimum - hoppar over", _Symbol); return; }

   double volume = LotSize(MathAbs(entry - sl));
   if(volume <= 0)
     { stSkipMinLot++; PrintFormat("%s: positionsstorlek under brokerns minimum for %.2f %% risk", _Symbol, InpRiskPercent); return; }

   double riskMoney = MathAbs(entry - sl) / SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE) * TickValue() * volume;
   double riskPct = riskMoney / AccountInfoDouble(ACCOUNT_BALANCE) * 100.0;
   PrintFormat("%s: OPPNAR %s %.2f lots @ %s SL=%s TP=%s (risk %.2f = %.2f %% av saldot)", _Symbol,
               signal > 0 ? "KOP" : "SALJ", volume, DoubleToString(entry, digits), DoubleToString(sl, digits),
               DoubleToString(tp, digits), riskMoney, riskPct);
   if(IsDryRun())
     { Print("DRY RUN: order skickades inte (satt InpDryRun = false for riktig handel)"); return; }

   bool ok = signal > 0 ? trade.Buy(volume, _Symbol, ask, sl, tp, "MT5TradingBot")
                        : trade.Sell(volume, _Symbol, bid, sl, tp, "MT5TradingBot");
   if(!ok || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
     {
      stRejected++;
      PrintFormat("%s: order avvisad: %u %s", _Symbol, trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return;
     }
   stOpened++;
   stRiskPctSum += riskPct;
   stRiskPctMax = MathMax(stRiskPctMax, riskPct);
  }

//+------------------------------------------------------------------+
//| Lots sa att en traffad SL kostar ca InpRiskPercent % av saldot.  |
//+------------------------------------------------------------------+
double TickValue()
  {
   double v = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   return v > 0 ? v : SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
  }

double LotSize(double slDistance)
  {
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = TickValue();
   double volMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volMax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(balance <= 0 || slDistance <= 0 || tickSize <= 0 || tickValue <= 0 || volStep <= 0)
      return 0.0;

   double riskMoney  = balance * InpRiskPercent / 100.0;
   double lossPerLot = slDistance / tickSize * tickValue;
   double volume = MathFloor(riskMoney / lossPerLot / volStep + 1e-9) * volStep;
   volume = MathMin(volume, volMax);
   if(volume < volMin)
      return 0.0;
   int volDigits = (int)MathMax(0, -MathFloor(MathLog10(volStep)));
   return NormalizeDouble(volume, volDigits);
  }

//+------------------------------------------------------------------+
int CountMyPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         count++;
     }
   return count;
  }

//+------------------------------------------------------------------+
//| Globala variabler delas av alla diagram med samma konto + magic. |
//+------------------------------------------------------------------+
string GvName(string suffix)
  {
   return StringFormat("MT5TB_%I64d_%I64d_%s", AccountInfoInteger(ACCOUNT_LOGIN), InpMagicNumber, suffix);
  }

void UpdateDailyGuard()
  {
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   double dayId = now.year * 1000 + now.day_of_year;
   if(!GlobalVariableCheck(GvName("day")) || GlobalVariableGet(GvName("day")) != dayId)
     {
      GlobalVariableSet(GvName("day"), dayId);
      GlobalVariableSet(GvName("dayeq"), AccountInfoDouble(ACCOUNT_EQUITY));
     }
   if(dayId != localDayId)
     {
      localDayId = dayId;
      guardWarned = false;
     }
   dayStartEquity = GlobalVariableGet(GvName("dayeq"));
  }

//+------------------------------------------------------------------+
//| Forlustgranser och helgstangning. false = gor inget mer nu.      |
//+------------------------------------------------------------------+
bool CheckLimits()
  {
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   if(halted || (InpMaxTotalLossPct > 0 && startBalance > 0 &&
                 equity <= startBalance * (1.0 - InpMaxTotalLossPct / 100.0)))
     {
      if(!halted)
        {
         PrintFormat("TOTAL FORLUSTGRANS %.1f %% NADD (equity %.2f). Stanger allt och stoppar EA:n. "
                     "Ange nytt InpStartBalance for att fortsatta.", InpMaxTotalLossPct, equity);
         halted = true;
         CloseAllMine(true, "totalgrans", true);
        }
      return false;
     }

   if(InpMaxDailyLossPct > 0 && dayStartEquity > 0 &&
      (dayStartEquity - equity) / dayStartEquity * 100.0 >= InpMaxDailyLossPct)
     {
      if(!guardWarned)
        {
         PrintFormat("Daglig forlustgrans %.1f %% nadd (start %.2f, nu %.2f) - ingen handel resten av dagen",
                     InpMaxDailyLossPct, dayStartEquity, equity);
         guardWarned = true;
         stDailyLimit++;
         if(InpCloseOnLimit)
            CloseAllMine(true, "daglig grans", true);
        }
      return false;
     }

   if(WeekendCloseDue(TimeCurrent()))
     {
      if(!IsDryRun() || !weekendLogged)
         CloseAllMine(false, "helgstangning", !weekendLogged);
      if(!weekendLogged)
        {
         stWeekend++;
         PrintFormat("%s: helgstangning - inga nya affarer forran marknaden oppnar igen", _Symbol);
        }
      weekendLogged = true;
      return false;
     }
   weekendLogged = false;
   return true;
  }

bool WeekendCloseDue(datetime t)
  {
   if(!InpCloseBeforeWeekend)
      return false;
   MqlDateTime d;
   TimeToStruct(t, d);
   return d.day_of_week == 6 || d.day_of_week == 0 || (d.day_of_week == 5 && d.hour >= InpFridayCloseHour);
  }

bool EntriesAllowed(datetime t)
  {
   if(WeekendCloseDue(t))
      return false;
   if(!InpUseSessionFilter)
      return true;
   MqlDateTime d;
   TimeToStruct(t, d);
   if(InpSessionStartHour <= InpSessionEndHour)
      return d.hour >= InpSessionStartHour && d.hour < InpSessionEndHour;
   return d.hour >= InpSessionStartHour || d.hour < InpSessionEndHour;
  }

//+------------------------------------------------------------------+
//| Stanger EA:ns positioner (alla symboler eller bara denna).       |
//+------------------------------------------------------------------+
void CloseAllMine(bool allSymbols, string reason, bool verbose)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || PositionGetInteger(POSITION_MAGIC) != InpMagicNumber)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(!allSymbols && sym != _Symbol)
         continue;
      if(verbose || !IsDryRun())
         PrintFormat("%s: STANGER #%I64u (vinst %.2f): %s", sym, ticket, PositionGetDouble(POSITION_PROFIT), reason);
      if(IsDryRun())
        {
         if(verbose)
            Print("DRY RUN: stangning skickades inte");
         continue;
        }
      if(!trade.PositionClose(ticket))
         PrintFormat("Kunde inte stanga #%I64u: %s", ticket, trade.ResultRetcodeDescription());
     }
  }
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| Sammanfattning langst ner i Journal/Experter.                    |
//+------------------------------------------------------------------+
void PrintSummary()
  {
   Print("================ MT5TradingBot SAMMANFATTNING ================");
   PrintFormat("EMA-korsningar: %d", stCrosses);
   PrintFormat("  bortfiltrerade: ADX %d, trend-EMA %d, RSI %d", stSkipADX, stSkipTrend, stSkipRSI);
   PrintFormat("  hoppade over: redan position %d, handelstid %d, max positioner %d, spread %d, SL/TP-minimum %d, minsta lot %d",
               stSkipSame, stSkipSession, stSkipMaxPos, stSkipSpread, stSkipStops, stSkipMinLot);
   PrintFormat("Oppnade affarer: %d (avvisade av brokern: %d)", stOpened, stRejected);
   if(stOpened > 0)
      PrintFormat("Planerad risk per affar: snitt %.2f %%, max %.2f %% (installt %.2f %%)",
                  stRiskPctSum / stOpened, stRiskPctMax, InpRiskPercent);
   PrintFormat("Dagsgrans nadd: %d ggr, helgstangningar: %d, total grans nadd: %s", stDailyLimit, stWeekend,
               halted ? "JA" : "nej");
   PrintFormat("Storsta spread vid signal: %.0f points (max tillatet %d)", stSpreadMax, InpMaxSpreadPoints);
   Print("==============================================================");
  }
//+------------------------------------------------------------------+
