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


def test_a_load_with_many_rows_set_aside_is_reported():
    found = monitor.evaluate([], {}, Monitoring(), NOW, None, None, [("zones", 100, 30), ("trips", 100, 1)])
    assert [(f.check, f.subject) for f in found] == [("quarantined", "zones")]
    assert "30 of the latest load's 100 rows" in found[0].message


def test_quarantine_and_a_contract_breach_from_a_real_build(tmp_path):
    import duckdb

    from murakumo import contracts
    from murakumo.ir.model import QualityRule
    from murakumo.project import save_model

    project = init_project(tmp_path / "taxi")
    zones = project.model.entity("taxi_zone_lookup")
    zones.attribute("Borough").tests = [QualityRule(type="accepted_values", values=["Queens"], action="quarantine")]
    save_model(project)
    data = tmp_path / "zones.csv"
    data.write_text("LocationID,Borough,Zone,service_zone\n1,Queens,a,x\n2,Atlantis,b,x\n3,Mars,c,x\n")
    result = build(project, data={"taxi_zone_lookup": str(data)}, sample=20)
    assert result.error is None
    found = monitor.check(project)["findings"]
    aside = [f for f in found if f["check"] == "quarantined"]
    assert aside and "2 of the latest load's 3 rows" in aside[0]["message"]
    with duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True) as db:
        rows = db.execute(
            "select location_id, _failed_rule from staging.stg_nyc_tlc__taxi_zone_lookup__quarantine order by 1"
        ).fetchall()
    assert rows == [(2, "borough: accepted_values"), (3, "borough: accepted_values")]
    breaches = contracts.check(project)["breaches"]
    assert any(b["what"] == "rules" and "2 of the latest load's 3 rows" in b["message"] for b in breaches)
