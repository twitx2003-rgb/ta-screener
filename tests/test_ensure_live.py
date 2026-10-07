"""The X news loop starts the live watch and the nightly run when GitHub's schedule did not."""
from __future__ import annotations

from datetime import datetime, timezone

from tascreen import github


def test_active_runs_counts_every_waiting_or_running_state():
    asked = []

    def get(url, headers):
        asked.append(url.split("status=")[1].split("&")[0])
        return {"total_count": 1 if "in_progress" in url else 0}

    assert github.active_runs("live.yml", token="t", get=get) == 1
    assert asked == ["queued", "waiting", "pending", "in_progress"]
    assert github.active_runs("live.yml", token="t", get=lambda u, h: None) is None
    assert github.active_runs("live.yml", token="") is None


def test_the_live_watch_is_started_only_when_due_and_missing(monkeypatch, capsys):
    import run

    started = []
    monkeypatch.setattr(github, "dispatch", lambda wf, inputs: started.append(wf) or 204)
    settings = run.load_settings()
    monday_0800_ny = datetime(2026, 1, 5, 13, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 1, 10, 13, 0, tzinfo=timezone.utc)
    monday_night = datetime(2026, 1, 6, 2, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(github, "active_runs", lambda wf: 0)
    for when in (saturday, monday_night):
        run.ensure_live(settings, now=when)
    assert started == []
    monkeypatch.setattr(github, "active_runs", lambda wf: 1)
    run.ensure_live(settings, now=monday_0800_ny)
    assert started == []
    monkeypatch.setattr(github, "active_runs", lambda wf: 0)
    assert run.ensure_live(settings, now=monday_0800_ny) == 0 and started == ["live.yml"]
    assert capsys.readouterr().out.strip().splitlines()[-1] == "ensure-live: started (204)"


def test_runs_since_asks_for_runs_created_after_the_close():
    asked = []

    def get(url, headers):
        asked.append(url)
        return {"workflow_runs": [{"status": "completed", "conclusion": "failure", "id": 1}]}

    since = datetime(2026, 1, 5, 21, 15, tzinfo=timezone.utc)
    assert github.runs_since("run.yml", since, token="t", get=get) == [
        {"status": "completed", "conclusion": "failure"}]
    assert "created=%3E%3D2026-01-05T21%3A15%3A00Z" in asked[0]
    assert github.runs_since("run.yml", since, token="t", get=lambda u, h: None) is None
    assert github.runs_since("run.yml", since, token="") is None


def test_the_nightly_run_is_started_only_when_due_and_no_run_succeeded(monkeypatch, capsys):
    import run

    started, given = [], []
    monkeypatch.setattr(github, "dispatch", lambda wf, inputs: given.append(inputs) or started.append(wf) or 204)
    settings = run.load_settings()
    monday_1700_ny = datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc)      # the scheduled time's turn
    monday_night = datetime(2026, 1, 6, 6, 0, tzinfo=timezone.utc)         # 01:00 New York
    tuesday_0730_ny = datetime(2026, 1, 6, 12, 30, tzinfo=timezone.utc)    # the live watch's turn
    saturday_noon = datetime(2026, 1, 10, 17, 0, tzinfo=timezone.utc)
    done = {"status": "completed", "conclusion": "success"}
    failed = {"status": "completed", "conclusion": "failure"}

    monkeypatch.setattr(github, "runs_since", lambda wf, since: [])
    for when in (monday_1700_ny, tuesday_0730_ny, saturday_noon):
        run.ensure_nightly(settings, now=when)
    assert started == []
    capsys.readouterr()
    for runs, said in (([{"status": "in_progress", "conclusion": None}], "running"),
                       ([failed, done], "done"), ([failed] * 3, "gave up (3 runs failed)")):
        monkeypatch.setattr(github, "runs_since", lambda wf, since, runs=runs: runs)
        run.ensure_nightly(settings, now=monday_night)
        assert started == [] and capsys.readouterr().out.strip() == f"ensure-nightly: {said}"
    seen = []
    monkeypatch.setattr(github, "runs_since", lambda wf, since: seen.append(since) or [failed])
    assert run.ensure_nightly(settings, now=monday_night) == 0 and started == ["run.yml"]
    assert given == [{"save": "true"}]                    # else the run keeps only the token and logs
    assert seen[0] == datetime(2026, 1, 5, 21, 15, tzinfo=timezone.utc)   # 16:15 New York in winter
    assert capsys.readouterr().out.strip() == "ensure-nightly: started (204, 1 failed before)"
    friday_night = datetime(2026, 1, 10, 4, 0, tzinfo=timezone.utc)        # 23:00 New York, Friday
    monkeypatch.setattr(github, "runs_since", lambda wf, since: [])
    run.ensure_nightly(settings, now=friday_night)
    assert started == ["run.yml", "run.yml"]
