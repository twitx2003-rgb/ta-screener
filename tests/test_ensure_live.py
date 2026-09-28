"""The X news loop starts the live watch when GitHub's schedule did not."""
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
