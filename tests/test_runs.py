from runs import RunTracker

EXPS = [{"name": "pod-delete", "duration": 60}, {"name": "pod-cpu-hog-exec", "duration": 60}]


def start(t):
    return t.start("web", "default", "h1", EXPS, 0.02, 180)


def test_start_creates_running_run_with_pending_experiments():
    t = RunTracker()
    run = t.get(start(t))
    assert run["state"] == "RUNNING" and run["rollout"] == "web" and run["revision"] == "h1"
    assert [e["status"] for e in run["experiments"]] == ["Pending", "Pending"]


def test_experiment_sample_and_finish():
    t = RunTracker()
    rid = start(t)
    t.experiment(rid, "pod-delete", "Running")
    t.experiment(rid, "pod-delete", "Done", "Pass")
    t.sample(rid, 0.004)
    t.finish(rid, "PROMOTED", "ok", "web-1.html")
    run = t.get(rid)
    assert run["experiments"][0] == {"name": "pod-delete", "duration": 60, "status": "Done", "verdict": "Pass"}
    assert run["samples"][0]["burn"] == 0.004
    assert run["state"] == run["decision"] == "PROMOTED" and run["report"] == "web-1.html"
    assert run["finished"]


def test_unknown_experiment_is_appended():
    t = RunTracker()
    rid = start(t)
    t.experiment(rid, "extra", "Running")
    assert t.get(rid)["experiments"][-1]["name"] == "extra"


def test_all_is_newest_first_and_capped():
    t = RunTracker(keep=3)
    ids = [start(t) for _ in range(5)]
    assert [r["id"] for r in t.all()] == ids[:1:-1]  # last 3, newest first


def test_get_returns_copy():
    t = RunTracker()
    rid = start(t)
    t.get(rid)["state"] = "HACKED"
    assert t.get(rid)["state"] == "RUNNING"


def test_persistence_roundtrip_and_interrupted(tmp_path):
    path = tmp_path / "runs.json"
    t = RunTracker(path)
    done = start(t)
    t.finish(done, "ABORTED", "bad")
    running = start(t)
    t2 = RunTracker(path)
    assert t2.get(done)["state"] == "ABORTED"
    assert t2.get(running)["state"] == "INTERRUPTED"


def test_corrupt_history_file_is_ignored(tmp_path):
    path = tmp_path / "runs.json"
    path.write_text("{not json")
    assert RunTracker(path).all() == []


async def test_subscribers_receive_events():
    t = RunTracker()
    q = t.subscribe()
    rid = start(t)
    t.sample(rid, 0.01)
    first, second = q.get_nowait(), q.get_nowait()
    assert first["type"] == "run" and first["run"]["id"] == rid
    assert len(second["run"]["samples"]) == 1
    t.unsubscribe(q)
    t.sample(rid, 0.02)
    assert q.empty()


def test_finish_cancels_experiments_that_never_completed():
    t = RunTracker()
    rid = start(t)
    t.experiment(rid, "pod-delete", "Done", "Pass")
    t.experiment(rid, "pod-cpu-hog-exec", "Running")
    t.finish(rid, "ABORTED", "budget")
    statuses = [(e["name"], e["status"]) for e in t.get(rid)["experiments"]]
    assert statuses == [("pod-delete", "Done"), ("pod-cpu-hog-exec", "Cancelled")]


def test_fail_marks_the_run_as_error():
    t = RunTracker()
    rid = start(t)
    t.experiment(rid, "pod-delete", "Running")
    t.fail(rid, "internal error: boom")
    run = t.get(rid)
    assert run["state"] == "ERROR" and run["reason"] == "internal error: boom" and run["finished"]
    assert run["experiments"][0]["status"] == "Cancelled"
