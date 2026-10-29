from fastapi.testclient import TestClient
from typer.testing import CliRunner

from murakumo import schedules
from murakumo.cli import app
from murakumo.generate import generate
from murakumo.scaffold import init_project
from murakumo.server import create_app

runner = CliRunner()


def test_docs_are_generated(tmp_path):
    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"], "Zones, hourly")
    files = generate(project).files
    page = files["docs/index.html"]
    assert "<title>taxi pipeline docs</title>" in page
    assert 'id="t-fct_trip"' in page and "every hour, on the hour" in page and "Zones, hourly" in page
    assert "md5(concat_ws(" in page  # expressions show where columns come from
    assert "http" not in page.split("<body>")[1].replace("https://github.com", "")  # needs nothing online
    markdown = files["docs/fct_trip.md"]
    assert "| `trip_id` |" in markdown and "[yellow_tripdata](yellow_tripdata.md)" in markdown
    assert "[fct_trip](fct_trip.md)" in files["docs/README.md"]
    assert generate(project).files["docs/index.html"] == page  # no dates: the same model gives the same page


def test_docs_endpoint_and_command(tmp_path):
    project = init_project(tmp_path / "taxi")
    client = TestClient(create_app(project.root), base_url="http://localhost")
    response = client.get("/api/pipeline-docs")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    result = runner.invoke(app, ["docs", "--no-open", "-p", str(project.root)])
    assert result.exit_code == 0 and "index.html" in result.output
