# Leading diagonal golden fixtures

Each fixture is a JSON file with the schema:

```json
{
  "name": "human-readable label",
  "ticker": "AAPL",
  "as_of": "2024-03-15",
  "bars": [
    {"timestamp": "2024-01-02", "open": "184.5", "high": "186.0", "low": "183.0", "close": "185.5", "volume": 12345, "interval": "1d"}
  ],
  "expected_top_pattern_at_degree_2": "leading_diagonal",
  "expected_runner_up_in_topk": "ending_diagonal"
}
```

`expected_top_pattern_at_degree_2` is one of:
- `"leading_diagonal"` - positive: LD must be #1 at degree 2.
- `"leading_diagonal_anywhere"` - positive (relaxed): LD must appear somewhere in degree-2 top-K (not necessarily rank 0).
- `"ending_diagonal"` / `"impulse"` - negative: LD must NOT be #1.
- `"primitive"` - any non-LD pattern; regression guard.

`expected_runner_up_in_topk` is optional. When set, the named pattern
must appear in the degree-2 top-K (not necessarily at position 2).

If `bars` is the literal string `"_use_helper_in_test_module_"`, the
loader calls a builder function in
`tests/elliott/test_leading_diagonal_golden.py` keyed by the fixture's
`name`. Real-chart fixtures use full bars arrays.

Acceptance: spec §5.2 requires >= 5 fixtures (>= 3 positives + 2
negatives) before merge. Synthetic seeds count toward the loader test
but not toward the acceptance bar — at least 3 of the 5 must be
real-chart fixtures.
