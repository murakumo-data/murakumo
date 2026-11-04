from datetime import UTC, datetime, timedelta

from murakumo import scheduler, schedules
from murakumo.jobs import JobError, Jobs
from murakumo.scaffold import init_project


def test_the_scheduler_runs_each_schedule_at_its_time(tmp_path, monkeypatch):
    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "hourly", "0 * * * *", ["layer:raw"])
    schedules.add_schedule(project, "nightly", "30 2 * * *")
    clock = [datetime(2026, 1, 1, 1, 10, tzinfo=UTC)]
    ran, said = [], []

    def sleep(seconds):
        clock[0] += timedelta(seconds=seconds)

    class Done:
        returncode = 0

    monkeypatch.setattr(scheduler.subprocess, "run", lambda args, **kw: ran.append(args) or Done())
    count = scheduler.run(
        project, "dev", ["yellow_tripdata=landing/"], say=said.append, now=lambda: clock[0], sleep=sleep, runs=3
    )
    assert count == 3
    assert [a[a.index("--schedule") + 1] for a in ran] == ["hourly", "nightly", "hourly"]  # 02:00, 02:30, 03:00
    assert ran[0][-4:] == ["--env", "dev", "--data", "yellow_tripdata=landing/"]
    assert said[0].startswith("Next: hourly at") and "succeeded" in said[2]
    assert clock[0] == datetime(2026, 1, 1, 3, 0, tzinfo=UTC)


def test_the_scheduler_job_runs_beside_another(tmp_path):
    project = init_project(tmp_path / "taxi")
    jobs = Jobs(project.root)
    service = jobs.start("scheduler", {})
    assert service.command == "murakumo scheduler"
    other = jobs.start("generate", {})  # allowed beside the scheduler
    try:
        jobs.start("scheduler", {})
    except JobError as exc:
        assert "still running" in str(exc)
    else:
        raise AssertionError("a second scheduler started")
    jobs.wait(other.id)
    jobs.cancel(service.id)
    assert jobs.wait(service.id, timeout=20).state == "cancelled"
