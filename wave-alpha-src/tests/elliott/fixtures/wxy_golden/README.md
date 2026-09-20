# W-X-Y golden fixtures

Each fixture is a JSON file with the schema:

```json
{
  "name": "human-readable label",
  "ticker": "AAPL",
  "as_of": "2024-03-15",
  "bars": [
    {"timestamp": "2024-01-02", "open": "184.5", "high": "186.0", "low": "183.0", "close": "185.5", "volume": 12345, "interval": "1d"}
  ],
  "expected_top_pattern_at_degree_2": "wxy",
  "expected_subcounts": {
    "W": "zigzag",
    "Y": "flat"
  }
}
```

`expected_top_pattern_at_degree_2` is one of:
- `"wxy"`          — W-X-Y is expected at top of degree-2 top-K (positive case)
- `"wxy_anywhere"` — W-X-Y appears somewhere in degree-2 candidates but not
                     necessarily at rank 0 (positive case, more permissive)
- `"primitive"`    — any non-wxy pattern; asserts wxy must NOT appear at top
                     (regression guard against false positives)

`expected_subcounts` is optional and consulted whenever the expected top
pattern asserts a wxy presence (`"wxy"` or `"wxy_anywhere"`). Missing labels
are not asserted — useful when X is degenerate or the analyst hasn't
committed to a specific Y subdivision.

The three synthetic seeds (`synthetic_positive.json`,
`synthetic_positive_strict.json`, `synthetic_negative_zigzag.json`) use the
magic string `"_use_helper_in_test_module_"` for the `bars` field; the
loader looks up a builder by `name` to produce bars dynamically. Real
ticker fixtures use a full bars array.

Two real-chart seeds ship alongside the synthetics:

- `msft_2018_wxy_at_top.json` — MSFT daily bars ending 2018-12-31 (200 bars
  back to 2018-03). The engine emits wxy at rank 0 of degree-2 with sub-counts
  `W = zigzag`, `X = expanded_flat`, `Y = zigzag` (all populated, no
  degenerate slot). Strongest positive case.
- `aapl_2022_wxy_anywhere.json` — AAPL daily bars ending 2022-10-15. wxy
  surfaces at rank 1 of degree-2 (zigzag wins rank 0); `wxy_anywhere` is
  the appropriate assertion.

A note on the synthetic seeds: `synthetic_positive.json` is named for the
*intent* of the underlying bars (engineered to embed a wxy structure), but
its assertion is `"primitive"` because the engine correctly prefers the
simpler zigzag explanation at rank 0 on this 4-pivot parent — that's the
expected dedupe behavior, and the file serves as a regression guard
against premature wxy promotion. `synthetic_positive_strict.json` uses the
same bars but asserts `"wxy_anywhere"` to verify the engine still produces
a valid wxy candidate (with `W = zigzag`, `Y = zigzag` sub-counts) deeper
in the degree-2 top-K.
