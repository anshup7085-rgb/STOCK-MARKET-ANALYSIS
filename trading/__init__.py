"""
Execution layer.

`market/` is the read-only research half: it measures, scores and plans. This
package is the half that acts on a plan, and every path through it that can
touch money runs the same gauntlet:

    scan -> intent -> risk engine -> confirmation -> broker -> journal

Paper is the default and needs nothing. Live needs an explicit opt-in, a
broker session the user minted by hand, the ROADMAP evidence gates (or a
journaled, reasoned override for per-order-confirmed trading), and it writes
every decision and every order attempt to a hash-chained journal that makes a
retrospective edit detectable. See TRADING.md.
"""
