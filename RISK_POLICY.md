# Risk Policy

## Objective
Support aggressive trading while preventing uncontrolled account-level risk.

## Required controls
- Define maximum loss before entry.
- Define invalidation before entry.
- Prefer liquid instruments.
- Avoid trades where the stop cannot reasonably be executed.
- Account for gaps and slippage.
- Account for brokerage, taxes and other charges where data is available.
- Never assume a stop guarantees the exact exit price.
- Reduce confidence around major binary events unless the strategy explicitly accounts for them.

## Position sizing
If account equity is known, calculate position size from the maximum acceptable loss and stop distance.

If account equity is unknown, provide the formula rather than inventing a rupee amount.

Example:
Position quantity = maximum rupee loss / absolute entry-to-stop distance

Then check that the resulting position is practical relative to liquidity and concentration limits.

## Portfolio limits
The agent must explicitly flag:
- excessive single-stock concentration
- correlated positions
- excessive sector concentration
- excessive leverage
- overnight gap exposure
- event concentration

## No-trade conditions
Recommend no trade when:
- expected reward does not justify the risk
- liquidity is inadequate
- data quality is poor
- thesis depends on unverified information
- the setup has already become materially extended
- the trade is driven primarily by hype/social-media claims
