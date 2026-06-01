"""
Backtesting engine for option volatility signals.

Signals tested:
    iv_rv_ratio   — sell when IV/RV > threshold, buy when below
    iv_rank       — sell when rolling IV Rank > threshold
    term_structure — sell on contango, buy on inversion
    vix_regime    — buy/sell based on absolute IV level (SPY IV30 ≈ VIX)
    combined      — majority vote across iv_rv_ratio, iv_rank, term_structure

P&L model (short premium):
    pnl = (IV_at_entry − RV_realized_over_holding) / IV_at_entry

Long premium inverts the sign. Represents fraction of IV premium captured or lost.
"""
