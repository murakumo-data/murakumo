import pytest

from murakumo.ir.model import LoadPattern
from murakumo.loading import expand, fingerprint
from murakumo.scaffold import init_project

HEADER = "LocationID,Borough,Zone,service_zone\n"


def write(path, rows):
    path.write_text(HEADER + "".join(f"{r},Queens,Zone {r},Boro Zone\n" if isinstance(r, int) else r for r in rows))


def zones(project):
    import duckdb

    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    try:
        return con.execute('select "LocationID", "Zone" from raw_nyc_tlc.taxi_zone_lookup order by 1').fetchall()
    finally:
        con.close()


def test_expand_and_fingerprint(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.parquet").write_bytes(b"PAR1")
    (tmp_path / "notes.txt").write_text("not data")
    assert [p.split("/")[-1] for p in expand(str(tmp_path))] == ["a.csv", "b.parquet"]
    assert [p.split("/")[-1] for p in expand(str(tmp_path / "*.csv"))] == ["a.csv"]
    assert expand("https://example.com/x.parquet") == ["https://example.com/x.parquet"]
    (tmp_path / "copy.csv").write_text("x\n1\n")
    assert fingerprint(str(tmp_path / "a.csv")) == fingerprint(str(tmp_path / "copy.csv"))


def test_only_new_files_load_and_each_mode(tmp_path):
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    entity = project.model.entity("taxi_zone_lookup")
    landing = tmp_path / "landing"
    landing.mkdir()
    write(landing / "jan.csv", [1, 2])
    write(landing / "feb.csv", [3])

    # append: each file once.
    entity.load = LoadPattern.APPEND
    first = build(project, sample=10, data={"taxi_zone_lookup": str(landing)})
    assert first.error is None and first.files["taxi_zone_lookup"] == (2, 0) and first.loaded["taxi_zone_lookup"] == 3
    again = build(project, data={"taxi_zone_lookup": str(landing)})
    assert again.files["taxi_zone_lookup"] == (0, 2) and "taxi_zone_lookup" not in again.loaded
    (landing / "jan.csv").rename(landing / "january.csv")  # the same content under another name
    write(landing / "mar.csv", [4])
    third = build(project, data={"taxi_zone_lookup": str(landing)})
    assert third.files["taxi_zone_lookup"] == (1, 2) and third.loaded["taxi_zone_lookup"] == 1
    assert [z[0] for z in zones(project)] == [1, 2, 3, 4]

    # incremental: a changed row replaces the old one with the same key.
    entity.load = LoadPattern.INCREMENTAL
    write(landing / "apr.csv", ["2,Queens,Jamaica Bay,Boro Zone\n", 5])
    merged = build(project, data={"taxi_zone_lookup": str(landing)})
    assert merged.modes["taxi_zone_lookup"] == "incremental" and merged.loaded["taxi_zone_lookup"] == 2
    assert zones(project) == [(1, "Zone 1"), (2, "Jamaica Bay"), (3, "Zone 3"), (4, "Zone 4"), (5, "Zone 5")]

    # full: the table becomes every file given, once anything changed.
    entity.load = LoadPattern.FULL
    only = tmp_path / "snapshot"
    only.mkdir()
    write(only / "zones.csv", [7, 8])
    replaced = build(project, data={"taxi_zone_lookup": str(only)})
    assert replaced.loaded["taxi_zone_lookup"] == 2 and [z[0] for z in zones(project)] == [7, 8]

    import duckdb

    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    files = con.execute("select path, rows_ingested from audit.loaded_files order by loaded_at").fetchall()
    con.close()
    assert [p.split("/")[-1] for p, _ in files] == ["feb.csv", "jan.csv", "mar.csv", "apr.csv", "zones.csv"]


def test_a_late_row_reaches_the_incremental_fact(tmp_path):
    """A trip that arrives late (picked up before trips already built) is picked up by load time;
    by pickup time, the old way, it was missed."""
    pytest.importorskip("dbt.adapters.duckdb")
    import duckdb

    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    columns = [a.name for a in project.model.entity("yellow_tripdata").attributes]

    def trips(path, rows):
        lines = [",".join(columns)]
        for pickup in rows:
            values = {
                "VendorID": "1",
                "tpep_pickup_datetime": pickup,
                "tpep_dropoff_datetime": pickup.replace(":00:", ":30:"),
                "PULocationID": "1",
                "DOLocationID": "1",
                "trip_distance": "2.5",
                "total_amount": "12.00",
            }
            lines.append(",".join(values.get(c, "") for c in columns))
        path.write_text("\n".join(lines) + "\n")

    landing = tmp_path / "trips"
    landing.mkdir()
    write(tmp_path / "zones.csv", [1])
    trips(landing / "a.csv", ["2024-01-10 10:00:00", "2024-01-11 10:00:00"])
    data = {"taxi_zone_lookup": str(tmp_path / "zones.csv"), "yellow_tripdata": str(landing)}
    assert build(project, data=data).error is None
    trips(landing / "b.csv", ["2024-01-02 09:00:00"])  # late: before everything already built
    assert build(project, data=data).error is None
    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    pickups = [str(r[0]) for r in con.execute("select pickup_at from marts.fct_trip order by 1").fetchall()]
    con.close()
    assert pickups == ["2024-01-02 09:00:00", "2024-01-10 10:00:00", "2024-01-11 10:00:00"]
