"""Shared fixtures: config with fast timings, fake Prometheus, fake K8s custom-objects API."""
import dataclasses
from unittest.mock import MagicMock

import pytest

from config import Config


@pytest.fixture
def cfg():
    return dataclasses.replace(
        Config(),
        chaos_duration_seconds=1,
        poll_interval_seconds=0.01,
        chaos_result_timeout_seconds=1,
        chaos_result_poll_seconds=0.01,
    )


class FakePrometheus:
    """Replaces requests.get. `values` is a list of floats / None (empty) / Exception, cycled at the end."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self, url, params=None, timeout=None):
        v = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if isinstance(v, Exception):
            raise v
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        result = [] if v is None else [{"metric": {}, "value": [0, str(v)]}]
        resp.json.return_value = {"status": "success", "data": {"result": result}}
        return resp


@pytest.fixture
def fake_prom(monkeypatch):
    def install(values):
        fp = FakePrometheus(values)
        monkeypatch.setattr("slo_monitor.requests.get", fp)
        return fp

    return install


@pytest.fixture
def custom_api():
    return MagicMock()
