from wave_alpha.backtest.coherence_bucket import COHERENCE_BUCKETS, bucket_for


def test_full_bucket_above_threshold():
    assert bucket_for(0.85) == "full"
    assert bucket_for(0.7001) == "full"
    assert bucket_for(1.0) == "full"


def test_partial_bucket_in_range():
    assert bucket_for(0.5) == "partial"
    assert bucket_for(0.4) == "partial"
    assert bucket_for(0.6999) == "partial"


def test_disagreement_below_partial():
    assert bucket_for(0.0) == "disagreement"
    assert bucket_for(0.39) == "disagreement"
    assert bucket_for(-0.5) == "disagreement"  # safety fallback


def test_buckets_constant_shape():
    # (name, lo, hi) tuples, used by count_layer to iterate static groups
    names = [name for (name, _, _) in COHERENCE_BUCKETS]
    assert names == ["full", "partial", "disagreement"]
