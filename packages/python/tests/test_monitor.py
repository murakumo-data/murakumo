from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from murakumo import monitor
from murakumo.cli import app
from murakumo.generate.verify import build
from murakumo.manifest import Alert, Monitoring
from murakumo.scaffold import init_project

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
runner = CliRunner()


def daily(table, sizes, rejected=0, last_hours_ago=1.0):
    """One load a day, the last one ``last_hours_ago`` hours before NOW."""
    last = NOW - timedelta(hours=last_hours_ago)
    count = len(sizes)
    return [
        monitor.Load(table, last - timedelta(days=count - 1 - i), size, rejected if i == count - 1 else 0)
        for i, size in enumerate(sizes)
    ]


def found(loads, freshness=None, **settings):
    result = monitor.evaluate(loads, freshness or {}, Monitoring(**settings), NOW)
    return [(f.level, f.check, f.subject) for f in result], result


def test_a_usual_load_is_not_reported():
    assert found(daily("orders", [1000, 1040, 980, 1010, 995, 1020]))[0] == []


def test_a_load_far_from_usual_is_reported():
    keys, findings = found(daily("orders", [1000, 1040, 980, 1010, 995, 300]))
    assert keys == [("warn", "volume", "orders")] and "70% fewer than the usual 1,000" in findings[0].message
    assert found(daily("orders", [1000, 1040, 980, 1010, 995, 2600]))[0] == [("warn", "volume", "orders")]
    assert found(daily("orders", [1000, 1040, 980, 1010, 995, 0]))[0] == [("error", "volume", "orders")]


def test_a_table_that_always_varies_is_given_room():
    # Its loads swing between 100 and 1,900: 1,700 is within what it does.
    assert found(daily("events", [100, 1900, 200, 1800, 150, 1850, 300, 1700]))[0] == []


def test_too_little_history_says_nothing():
    assert found(daily("new_table", [1000, 5]))[0] == []


def test_rejected_rows():
    keys, findings = found(daily("orders", [1000, 1000, 1000, 1000, 1000, 1000], rejected=200))
    assert keys == [("warn", "rejected", "orders")] and "200 of 1,200 rows (17%)" in findings[0].message
    assert found(daily("orders", [1000] * 6, rejected=10))[0] == []


def test_late_by_its_setting_or_by_its_own_habit():
    loads = daily("orders", [1000] * 6, last_hours_ago=30)
    assert found(loads, {"orders": (24, 48)})[0] == [("warn", "late", "orders")]
    assert found(daily("orders", [1000] * 6, last_hours_ago=50), {"orders": (24, 48)})[0] == [
        ("error", "late", "orders")
    ]
    assert found(loads)[0] == []  # no setting: a daily table 30 hours old is not late
    keys, findings = found(daily("orders", [1000] * 6, last_hours_ago=24 * 4))
    assert keys == [("warn", "late", "orders")] and "usually loads every 24 hours" in findings[0].message


def test_a_failed_run_and_its_tests():
    run = {"schedule": "nightly", "status": "failed", "models_failed": 1, "tests_failed": 2}
    tests = [{"model": "fct_trip", "test": "not_null", "column": "trip_id", "failures": 7, "severity": "error"}]
    result = monitor.evaluate([], {}, Monitoring(), NOW, run, tests)
    assert [(f.level, f.subject) for f in result] == [("error", "fct_trip.trip_id"), ("error", "nightly")]
    assert monitor.evaluate([], {}, Monitoring(), NOW, {"status": "success"}, []) == []


def test_it_reads_what_a_real_build_recorded(tmp_path):
    project = init_project(tmp_path / "taxi")
    assert build(project, sample=50).ok
    result = monitor.check(project)
    assert result["recorded"] and result["tables"] == 2 and result["findings"] == []
    done = runner.invoke(app, ["monitor", "-p", str(project.root)])
    assert done.exit_code == 0 and "Nothing unusual in 2 table(s)" in done.output


def test_nothing_recorded_yet(tmp_path):
    project = init_project(tmp_path / "taxi")
    result = monitor.check(project)
    assert not result["recorded"] and result["findings"] == []
    assert runner.invoke(app, ["monitor", "-p", str(project.root)]).exit_code == 0


def test_alerts_go_only_to_https_webhooks_that_are_set(tmp_path, monkeypatch):
    project = init_project(tmp_path / "taxi")
    project.manifest.quality.alerts = [
        Alert(kind="slack", url_env="SLACK_HOOK"),
        Alert(kind="webhook", url_env="PLAIN_HOOK", on=["error", "warn"]),
    ]
    findings = [{"level": "warn", "check": "volume", "subject": "orders", "message": "fewer rows"}]
    monkeypatch.delenv("SLACK_HOOK", raising=False)
    monkeypatch.setenv("PLAIN_HOOK", "http://example.test/hook")
    sent = monitor.notify(project, findings)
    assert sent == ["webhook: PLAIN_HOOK must be an https:// address, nothing sent"]  # slack takes errors only
    findings[0]["level"] = "error"
    assert monitor.notify(project, findings)[0] == "slack: SLACK_HOOK is not set, nothing sent"
    assert "1 thing(s) to look at in taxi (prod)" in monitor.message(project, findings, "prod")


def test_an_alert_written_as_the_docs_show_it_loads(tmp_path):
    # YAML 1.1 reads the bare key `on` as true; the manifest must still load.
    from murakumo.project import load_project

    root = init_project(tmp_path / "taxi").root
    with (root / "murakumo.yaml").open("a") as file:
        file.write("\nquality:\n  alerts:\n    - kind: slack\n      url_env: HOOK\n      on: [error, warn]\n")
    assert load_project(root).project.manifest.quality.alerts[0].on == ["error", "warn"]
