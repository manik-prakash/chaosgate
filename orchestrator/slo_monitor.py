"""SLOMonitor: polls the Sloth-generated error-budget burn rate during chaos."""
import asyncio
import logging
import time

import requests

from config import Config

log = logging.getLogger(__name__)


class SLOMonitor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.polls = 0
        self.no_data_polls = 0
        self.max_burn = 0.0
        self.first_burn: float | None = None  # first sample of the run (shows pre-existing burn)

    def fetch(self) -> float | None:
        """One Prometheus instant query. None means unreachable or no data (callers decide what that means)."""
        query = f'slo:sli_error:ratio_rate5m{{sloth_service="{self.cfg.slo_service_name}"}}'
        try:
            resp = requests.get(
                f"{self.cfg.prometheus_url}/api/v1/query", params={"query": query}, timeout=5
            )
            resp.raise_for_status()
            result = resp.json()["data"]["result"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            log.warning("Prometheus query failed: %s", exc)
            return None
        if not result:
            log.warning("No SLO data for %s", self.cfg.slo_service_name)
            return None
        return float(result[0]["value"][1])

    def _query(self) -> float:
        """Monitoring must never block delivery: missing data counts as 0.0 (and is flagged in the report)."""
        value = self.fetch()
        if value is None:
            self.no_data_polls += 1
            return 0.0
        return value

    async def monitor(self, on_sample=None, keep_going=None) -> tuple[bool, float]:
        """Return (within_budget, max_burn_seen). Stops at the first breach.

        The window is a minimum: while keep_going() is true (chaos still running) monitoring continues past
        it, because a real chaos run outlasts its configured durations and must not run unmonitored."""
        self.polls = 0
        self.no_data_polls = 0
        self.max_burn = 0.0
        self.first_burn = None
        max_burn = 0.0
        deadline = time.monotonic() + self.cfg.chaos_duration_seconds
        while True:
            burn = await asyncio.to_thread(self._query)
            self.polls += 1
            if self.first_burn is None:
                self.first_burn = burn
            max_burn = self.max_burn = max(max_burn, burn)
            if on_sample:
                on_sample(burn)
            if burn > self.cfg.error_budget_threshold:
                log.error("Error budget breach: %s > %s", burn, self.cfg.error_budget_threshold)
                return False, max_burn
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if keep_going is None or not keep_going():
                    return True, max_burn
                await asyncio.sleep(self.cfg.poll_interval_seconds)
                continue
            await asyncio.sleep(min(self.cfg.poll_interval_seconds, remaining))
