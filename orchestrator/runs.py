"""RunTracker: records every gate run (live progress + history) and streams changes to subscribers."""
import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class RunTracker:
    def __init__(self, path: Path | str | None = None, keep: int = 50):
        self._path = Path(path) if path else None
        self._keep = keep
        self._runs: dict[str, dict] = {}  # insertion order = oldest first
        self._subs: set[asyncio.Queue] = set()
        self._started_at: dict[str, float] = {}
        self._counter = 0
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        if not self._path or not self._path.is_file():
            return
        try:
            for run in json.loads(self._path.read_text(encoding="utf-8")):
                if run.get("state") == "RUNNING":  # orchestrator died mid-run
                    run["state"] = "INTERRUPTED"
                    run["reason"] = "orchestrator restarted during this run"
                self._runs[run["id"]] = run
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Could not load run history from %s: %s", self._path, exc)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(list(self._runs.values())), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            log.warning("Could not save run history: %s", exc)

    # ---- mutations ---------------------------------------------------
    def start(self, rollout: str, namespace: str, revision: str | None, experiments: list[dict],
              threshold: float, window_seconds: int) -> str:
        self._counter += 1
        run_id = f"{rollout}-{int(time.time())}-{self._counter}"
        self._started_at[run_id] = time.monotonic()
        self._runs[run_id] = {
            "id": run_id, "rollout": rollout, "namespace": namespace, "revision": revision,
            "state": "RUNNING", "started": _now(), "finished": None,
            "experiments": [
                {"name": e["name"], "duration": e["duration"], "status": "Pending", "verdict": None}
                for e in experiments
            ],
            "samples": [], "threshold": threshold, "window_seconds": window_seconds,
            "decision": None, "reason": None, "report": None,
        }
        while len(self._runs) > self._keep:
            self._runs.pop(next(iter(self._runs)))
        self._publish(run_id)
        return run_id

    def experiment(self, run_id: str, name: str, status: str, verdict: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        entry = next((e for e in run["experiments"] if e["name"] == name), None)
        if entry is None:
            entry = {"name": name, "duration": 0, "status": status, "verdict": verdict}
            run["experiments"].append(entry)
        entry["status"], entry["verdict"] = status, verdict
        self._publish(run_id)

    def sample(self, run_id: str, burn: float) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        t = round(time.monotonic() - self._started_at.get(run_id, time.monotonic()), 1)
        run["samples"].append({"t": t, "burn": burn})
        self._publish(run_id)

    def finish(self, run_id: str, decision: str, reason: str, report: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        for e in run["experiments"]:  # anything that never finished was stopped by the early cancel
            if e["status"] != "Done":
                e["status"] = "Cancelled"
        run.update(state=decision, decision=decision, reason=reason, report=report, finished=_now())
        self._publish(run_id)

    def fail(self, run_id: str, reason: str) -> None:
        """The gate itself broke (a bug or an unexpected error): close the run out instead of leaving it RUNNING."""
        run = self._runs.get(run_id)
        if run is None:
            return
        for e in run["experiments"]:
            if e["status"] != "Done":
                e["status"] = "Cancelled"
        run.update(state="ERROR", reason=reason, finished=_now())
        self._publish(run_id)

    # ---- reads / pub-sub ----------------------------------------------
    def all(self) -> list[dict]:
        return [copy.deepcopy(r) for r in reversed(self._runs.values())]  # newest first

    def get(self, run_id: str) -> dict | None:
        run = self._runs.get(run_id)
        return copy.deepcopy(run) if run else None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _publish(self, run_id: str) -> None:
        self._save()
        event = {"type": "run", "run": copy.deepcopy(self._runs[run_id])}
        for q in self._subs:
            q.put_nowait(event)
