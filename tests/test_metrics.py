"""Tests for the broker-backed queue-depth collector."""

from unittest.mock import MagicMock, patch

from app.metrics import QueueDepthCollector


def test_collector_reads_broker_llen() -> None:
    """collect() yields one gauge family with a sample per queue from LLEN."""
    fake = MagicMock()
    fake.llen.side_effect = lambda q: {"high": 1, "default": 2, "low": 3}[q]
    with patch("app.metrics.redis.Redis.from_url", return_value=fake):
        families = list(QueueDepthCollector().collect())
    assert len(families) == 1
    samples = {s.labels["queue_name"]: s.value for s in families[0].samples}
    assert samples == {"high": 1.0, "default": 2.0, "low": 3.0}


def test_collector_survives_broker_error() -> None:
    """A broker failure must not raise; the family is still yielded (empty)."""
    with patch("app.metrics.redis.Redis.from_url", side_effect=Exception("down")):
        families = list(QueueDepthCollector().collect())
    assert len(families) == 1
