# Disclaimer

**`wave-alpha` is not financial advice.**

This software is provided for **educational and research purposes only**. It is a
deterministic Elliott Wave analysis tool that produces calibrated probabilistic counts
and trade-shape signals — it does **not** provide investment advice, trade
recommendations, or solicitation to buy or sell any security.

## What this means in practice

- **Past patterns are not guarantees of future results.** Backtest equity curves
  reflect a specific universe, time window, and parameter choice. Out-of-sample
  performance is generally worse, often much worse.
- **The engine is not infallible.** Wave counts are inherently subjective; even
  rule-validated counts can fail when market regime changes.
- **The LLM reranker can hallucinate.** When enabled, the LLM influences ranking
  but does not validate counts. Treat narration as commentary, not analysis.
- **Data may be wrong, stale, or missing.** `wave-alpha` uses Yahoo Finance via
  `yfinance`. Splits, dividends, halts, and corporate actions can produce artifacts.
- **No warranty.** See `LICENSE`. The software is provided "as is."

## Your responsibility

- You are solely responsible for any trading decisions you make.
- Do your own research. Validate counts manually before trading.
- Never trade money you cannot afford to lose.
- Consult a licensed financial advisor for personalized advice.

## Author

`wave-alpha` is maintained by Alex Chen. The author is not a registered investment
advisor and has no fiduciary duty to users of this software.
