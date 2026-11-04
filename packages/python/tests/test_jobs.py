import pytest

from murakumo.jobs import JobError, Jobs, command
from murakumo.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "proj")


def test_commands_are_built_from_checked_options(project, tmp_path):
    root = project.root
    (root / "data").mkdir()
    (root / "data" / "zones.csv").write_text("LocationID\n1\n")
    title, args = command(
        root,
        "build",
        {
            "environment": "prod",
            "sample": 50,
            "schedule": "daily",
            "run_date": "2024-01-31",
            "prefix": "pr_1_",
            "data": {"taxi_zone_lookup": "data/zones.csv"},
        },
    )
    assert title == "Build prod: daily"
    assert args == [
        "build",
        "--env",
        "prod",
        "--prefix",
        "pr_1_",
        "--sample",
        "50",
        "--data",
        "taxi_zone_lookup=data/zones.csv",
        "--schedule",
        "daily",
        "--run-date",
        "2024-01-31",
    ]
    assert command(root, "backfill", {"table": "zone_activity", "from": "2024-01-01", "to": "2024-01-07"})[1] == [
        "backfill",
        "zone_activity",
        "--from",
        "2024-01-01",
        "--to",
        "2024-01-07",
    ]
    assert command(root, "env_drop", {"prefix": "pr_1_"})[1] == ["env", "drop", "--yes", "--prefix", "pr_1_"]

    bad = [
        ("build", {"environment": "staging; rm -rf /"}),
        ("build", {"sample": "10; ls"}),
        ("build", {"data": {"t": "../../etc/passwd"}}),
        ("build", {"data": {"t": "missing.csv"}}),
        ("build", {"schedule": "--help"}),
        ("backfill", {"table": "t", "from": "2024-02-01", "to": "2024-01-01"}),
        ("env_clone", {"prefix": "PROD"}),
        ("run", {"engine": "bash"}),
        ("shell", {}),
    ]
    for kind, options in bad:
        with pytest.raises(JobError):
            command(root, kind, options)


def test_a_job_runs_streams_and_one_at_a_time(project):
    jobs = Jobs(project.root)
    job = jobs.start("generate", {})
    with pytest.raises(JobError, match="still running"):
        jobs.start("generate", {})
    done = jobs.wait(job.id)
    assert done.state == "succeeded" and done.code == 0
    assert any("generated" in line.lower() or "wrote" in line.lower() for line in done.lines)
    tail = done.as_dict(since=done.as_dict()["next"] - 1)
    assert len(tail["lines"]) == 1

    failed = jobs.wait(jobs.start("backfill", {"table": "no_such_table", "from": "2024-01-01", "to": "2024-01-02"}).id)
    assert failed.state == "failed" and failed.code != 0
    assert [j.id for j in jobs.jobs] == [job.id, failed.id]


def test_jobs_over_the_api(project):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from murakumo.server import create_app

    client = TestClient(create_app(project.root), base_url="http://localhost")
    started = client.post("/api/jobs", json={"kind": "generate"}).json()
    assert started["state"] == "running" and started["command"] == "murakumo generate"
    import time

    for _ in range(400):
        job = client.get(f"/api/jobs/{started['id']}").json()
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "succeeded"
    assert client.get("/api/jobs").json()["jobs"][0]["id"] == started["id"]
    assert client.post("/api/jobs", json={"kind": "shell", "options": {}}).status_code == 409


def test_spinner_frames_and_colours_are_cleaned(project, monkeypatch):
    import subprocess
    import sys

    from murakumo import jobs as jobs_module

    script = "[print(f'{c} Building...') for c in '\u280b\u2819\u2839']; print('\\x1b[1mdone\\x1b[0m')"
    real = subprocess.Popen
    monkeypatch.setattr(jobs_module.subprocess, "Popen", lambda cmd, **kw: real([sys.executable, "-c", script], **kw))
    job = jobs_module.Job("x", "test", "Test", "murakumo test")
    Jobs(project.root)._run(job, [])
    assert job.lines == ["\u280b Building...", "done"]
    assert job.state == "succeeded"
