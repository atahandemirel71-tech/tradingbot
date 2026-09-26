//+------------------------------------------------------------------+
//|                                               MT5TradingBot.mq5  |
//|  Trendfoljande Expert Advisor: EMA-kors + RSI + EMA 200-filter,  |
//|  ATR-baserad SL/TP, riskbaserad lotstorlek och daglig forlust-   |
//|  grans. Samma regler som Python-boten i detta projekt.           |
//+------------------------------------------------------------------+
#property copyright "MT5 Trading Bot"
#property version   "1.00"

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
input bool            InpCloseOnOpposite = true;         // Stang och vand vid motsatt signal

//--- Risk
input group "Risk"
input double InpRiskPercent     = 1.0;   // % av saldot som riskeras per affar
input int    InpMaxOpenPositions= 3;     // Max oppna positioner (alla symboler, denna EA)
input double InpMaxDailyLossPct = 5.0;   // Pausa nya affarer efter sa har stor dagsforlust (%)
input int    InpMaxSpreadPoints = 30;    // Max spread i points

//--- Ovrigt
input group "Ovrigt"
input bool   InpDryRun          = true;      // true = simulera bara (loggar, skickar inga order)
input long   InpMagicNumber     = 20260926;  // Unikt ID for EA:ns positioner
input int    InpDeviationPoints = 20;        // Max slippage i points

CTrade   trade;
int      hFast = INVALID_HANDLE, hSlow = INVALID_HANDLE, hTrend = INVALID_HANDLE;
int      hRSI = INVALID_HANDLE, hATR = INVALID_HANDLE;
datetime lastBarTime = 0;
int      dayOfYear = -1;
double   dayStartEquity = 0.0;
bool     guardWarned = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpFastEMA >= InpSlowEMA)
     { Print("Fel: snabb EMA maste vara mindre an langsam EMA"); return INIT_PARAMETERS_INCORRECT; }
   if(InpRiskPercent <= 0 || InpRiskPercent > 5)
     { Print("Fel: risk per affar maste vara mellan 0 och 5 %"); return INIT_PARAMETERS_INCORRECT; }
   if(InpSLATRMult <= 0 || InpTPATRMult <= 0)
     { Print("Fel: ATR-multiplarna maste vara positiva"); return INIT_PARAMETERS_INCORRECT; }

   hFast  = iMA(_Symbol, InpTimeframe, InpFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hSlow  = iMA(_Symbol, InpTimeframe, InpSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hTrend = iMA(_Symbol, InpTimeframe, InpTrendEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI   = iRSI(_Symbol, InpTimeframe, InpRSIPeriod, PRICE_CLOSE);
   hATR   = iATR(_Symbol, InpTimeframe, InpATRPeriod);
   if(hFast == INVALID_HANDLE || hSlow == INVALID_HANDLE || hTrend == INVALID_HANDLE ||
      hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE)
     { Print("Kunde inte skapa indikatorer: ", GetLastError()); return INIT_FAILED; }

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpDeviationPoints);
   trade.SetTypeFillingBySymbol(_Symbol);

   PrintFormat("MT5TradingBot startad pa %s %s - %s", _Symbol, EnumToString(Timeframe()),
               IsDryRun() ? "DRY RUN (inga riktiga order)" : "LIVE-HANDEL");
   if(!IsDryRun() && !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      Print("VARNING: 'Algo Trading' ar avstangt i terminalen - sla pa knappen i verktygsfaltet");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(hFast);
   IndicatorRelease(hSlow);
   IndicatorRelease(hTrend);
   IndicatorRelease(hRSI);
   IndicatorRelease(hATR);
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
   double fast[], slow[], trend[], rsi[], atrBuf[];
   ArraySetAsSeries(fast, true);
   ArraySetAsSeries(slow, true);
   ArraySetAsSeries(trend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(hFast, 0, 1, 2, fast) != 2 || CopyBuffer(hSlow, 0, 1, 2, slow) != 2 ||
      CopyBuffer(hTrend, 0, 1, 1, trend) != 1 || CopyBuffer(hRSI, 0, 1, 1, rsi) != 1 ||
      CopyBuffer(hATR, 0, 1, 1, atrBuf) != 1)
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

   if(crossedUp)
     {
      if(InpUseTrendFilter && close <= trend[0])
         PrintFormat("%s: uppkors men under trend-EMA - ingen affar", _Symbol);
      else if(rsi[0] >= InpRSIOverbought)
         PrintFormat("%s: uppkors men RSI %.1f overkopt - ingen affar", _Symbol, rsi[0]);
      else
        { signal = 1; PrintFormat("%s: KOPSIGNAL (EMA-kors upp, RSI %.1f)", _Symbol, rsi[0]); }
     }
   else if(crossedDown)
     {
      if(InpUseTrendFilter && close >= trend[0])
         PrintFormat("%s: nedkors men over trend-EMA - ingen affar", _Symbol);
      else if(rsi[0] <= InpRSIOversold)
         PrintFormat("%s: nedkors men RSI %.1f oversalt - ingen affar", _Symbol, rsi[0]);
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
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!DailyGuardAllows(equity))
      return;
   if(CountMyPositions() >= InpMaxOpenPositions)
     { PrintFormat("%s: max antal oppna positioner (%d) natt", _Symbol, InpMaxOpenPositions); return; }
   if(SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL)
     { PrintFormat("%s: symbolen gar inte att handla just nu", _Symbol); return; }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(ask <= 0 || bid <= 0 || point <= 0)
      return;

   double spreadPoints = (ask - bid) / point;
   if(spreadPoints > InpMaxSpreadPoints)
     { PrintFormat("%s: spread %.0f > max %d points - hoppar over", _Symbol, spreadPoints, InpMaxSpreadPoints); return; }

   double entry  = signal > 0 ? ask : bid;
   double slDist = atr * InpSLATRMult;
   double tpDist = atr * InpTPATRMult;
   double sl = NormalizeDouble(signal > 0 ? entry - slDist : entry + slDist, digits);
   double tp = NormalizeDouble(signal > 0 ? entry + tpDist : entry - tpDist, digits);

   double minDist = (SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) + 1) * point;
   if(MathAbs(entry - sl) < minDist || MathAbs(entry - tp) < minDist)
     { PrintFormat("%s: SL/TP for nara brokerns minimum - hoppar over", _Symbol); return; }

   double volume = LotSize(MathAbs(entry - sl));
   if(volume <= 0)
     { PrintFormat("%s: positionsstorlek under brokerns minimum for %.2f %% risk", _Symbol, InpRiskPercent); return; }

   PrintFormat("%s: OPPNAR %s %.2f lots @ %s SL=%s TP=%s", _Symbol, signal > 0 ? "KOP" : "SALJ", volume,
               DoubleToString(entry, digits), DoubleToString(sl, digits), DoubleToString(tp, digits));
   if(IsDryRun())
     { Print("DRY RUN: order skickades inte (satt InpDryRun = false for riktig handel)"); return; }

   bool ok = signal > 0 ? trade.Buy(volume, _Symbol, ask, sl, tp, "MT5TradingBot")
                        : trade.Sell(volume, _Symbol, bid, sl, tp, "MT5TradingBot");
   if(!ok || (trade.ResultRetcode() != TRADE_RETCODE_DONE && trade.ResultRetcode() != TRADE_RETCODE_PLACED))
      PrintFormat("%s: order avvisad: %u %s", _Symbol, trade.ResultRetcode(), trade.ResultRetcodeDescription());
  }

//+------------------------------------------------------------------+
//| Lots sa att en traffad SL kostar ca InpRiskPercent % av saldot.  |
//+------------------------------------------------------------------+
double LotSize(double slDistance)
  {
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickValue <= 0)
      tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
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
void UpdateDailyGuard()
  {
   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   if(now.day_of_year != dayOfYear)
     {
      dayOfYear = now.day_of_year;
      dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      guardWarned = false;
     }
  }

bool DailyGuardAllows(double equity)
  {
   if(InpMaxDailyLossPct <= 0 || dayStartEquity <= 0)
      return true;
   double drawdownPct = (dayStartEquity - equity) / dayStartEquity * 100.0;
   if(drawdownPct < InpMaxDailyLossPct)
      return true;
   if(!guardWarned)
     {
      PrintFormat("Daglig forlustgrans %.1f %% nadd (start %.2f, nu %.2f) - inga nya affarer idag",
                  InpMaxDailyLossPct, dayStartEquity, equity);
      guardWarned = true;
     }
   return false;
  }
//+------------------------------------------------------------------+
