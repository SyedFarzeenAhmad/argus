from __future__ import annotations

import io
import zipfile
from datetime import timedelta

from sqlalchemy import func, select

from argus_api.db.models import (
    Asset,
    EvidenceCheck,
    ExpectedAsset,
    GtfsStopTime,
    Incident,
    RoadSegment,
    Telemetry,
    Ward,
)
from argus_api.db.types import utcnow
from argus_api.ingest.validation import validator
from argus_api.jobs.run import retention, verify_pending
from argus_api.seeds import load_gtfs, load_osm, load_wards, simulate
from tests import factories as f

OSM = {
    "elements": [
        {"type": "node", "id": 1, "lat": 12.9700, "lon": 77.6000},
        {
            "type": "node",
            "id": 2,
            "lat": 12.9700,
            "lon": 77.6010,
            "tags": {"highway": "crossing", "crossing": "zebra"},
        },
        {
            "type": "node",
            "id": 3,
            "lat": 12.9700,
            "lon": 77.6020,
            "tags": {"highway": "traffic_signals"},
        },
        {"type": "node", "id": 4, "lat": 12.9700, "lon": 77.6030},
        {"type": "node", "id": 5, "lat": 12.9690, "lon": 77.6020},
        {"type": "node", "id": 6, "lat": 12.9710, "lon": 77.6020},
        {
            "type": "node",
            "id": 7,
            "lat": 12.97003,
            "lon": 77.6025,
            "tags": {"traffic_sign": "IN:stop"},
        },
        {
            "type": "way",
            "id": 100,
            "nodes": [1, 2, 3, 4],
            "tags": {"highway": "primary", "name": "Test Road", "maxspeed": "40"},
        },
        {"type": "way", "id": 200, "nodes": [5, 3, 6], "tags": {"highway": "residential"}},
    ]
}


def test_osm_ways_split_at_junctions_and_ledger_expectations(session):
    n_seg, n_exp = load_osm.load(session, OSM)
    session.commit()
    ids = {s.segment_id for s in session.scalars(select(RoadSegment))}
    assert ids == {"osm:way/100:0", "osm:way/100:1", "osm:way/200:0", "osm:way/200:1"}
    first = session.get(RoadSegment, "osm:way/100:0")
    assert first.has_signal and first.maxspeed_kmh == 40 and 200 < first.length_m < 230
    zebra = session.get(ExpectedAsset, "osm:node/2")
    assert zebra.class_id == "missing_zebra" and zebra.segment_id == "osm:way/100:0"
    sign = session.get(ExpectedAsset, "osm:node/7")
    assert sign.class_id == "missing_sign" and sign.segment_id == "osm:way/100:1"


def test_wards_loaded_and_segments_assigned(session):
    load_osm.load(session, OSM)
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"KGISWardNo": 150, "KGISWardName": "Domlur"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [77.59, 12.96],
                            [77.61, 12.96],
                            [77.61, 12.98],
                            [77.59, 12.98],
                            [77.59, 12.96],
                        ]
                    ],
                },
            }
        ],
    }
    assert load_wards.load(session, fc, "bbmp") == 1
    session.commit()
    assert session.get(Ward, "bbmp:150").name == "Domlur"
    assert session.get(RoadSegment, "osm:way/100:0").ward_id == "bbmp:150"


def test_gtfs_zip(session, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "routes.txt", "route_id,route_short_name,route_long_name\nR1,500D,Hebbal-Silk Board\n"
        )
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\n4412,Domlur,12.96,77.63\n")
        z.writestr(
            "trips.txt", "route_id,service_id,trip_id,direction_id\nR1,WK,T1,0\nR1,WK,T2,0\n"
        )
        z.writestr(
            "stop_times.txt",
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,07:00:00,07:00:00,4412,1\nT2,07:10:00,07:10:00,4412,1\n"
            "T2,25:10:00,25:10:00,4412,2\n",
        )
    path = tmp_path / "gtfs.zip"
    path.write_bytes(buf.getvalue())
    counts = load_gtfs.load(session, path, "bmtc")
    session.commit()
    assert counts == {"routes": 1, "stops": 1, "trips": 2, "stop_times": 3}
    late = session.get(GtfsStopTime, ("T2", 2))
    assert late.stop_id == "bmtc:4412" and late.arrival_s == 25 * 3600 + 600
    from argus_api.analytics.transit import _scheduled_headway_s

    assert _scheduled_headway_s(session, "500D") == 600


def test_simulation_runs_through_real_pipeline(factory, bus, session, tmp_path):
    log = tmp_path / "sim.jsonl"
    stats = simulate.run(days=1, n_devices=12, seed=7, factory=factory, jsonl=log, quiet=True)
    assert stats["rejected"] == 0 and stats["stored"] > 1000
    assets = session.scalars(select(Asset)).all()
    assert assets and all(a.segment_id.startswith("sim:") for a in assets if a.segment_id)
    assert any(a.state == "reported" for a in assets)
    from argus_api.fusion.engine import FusionEngine

    for a in assets[:25]:
        validator("asset").validate(FusionEngine(session, bus=bus).contract(a))
    first = log.read_text(encoding="utf-8").splitlines()[0]
    assert '"topic": "argus/v1/sim/BLR-SIM-' in first


def test_retention_expires_personal_data_only(ingestor, session, store):
    old = utcnow() - timedelta(days=400)
    ingestor.handle("incident", f.incident(at=old))
    ingestor.handle("telemetry", f.telemetry(at=old))
    ingestor.handle("telemetry", f.telemetry(at=utcnow() - timedelta(hours=1)))
    dry = retention(session, utcnow(), dry_run=True)
    assert dry["telemetry"] == 1 and dry["incidents_redacted"] == 1
    session.rollback()
    out = retention(session, utcnow())
    session.commit()
    assert out["telemetry"] == 1
    assert session.scalar(select(func.count()).select_from(Telemetry)) == 1
    inc = session.scalars(select(Incident)).one()
    assert "plate" not in (inc.subject_vehicle or {}) and "clip_uri" not in inc.evidence
    assert inc.triggers  # the kinematics — not personal data — survive


def test_pending_evidence_verified_later(ingestor, session, store):
    msg = f.incident()
    ingestor.handle("incident", msg)
    uri = msg["evidence"]["clip_uri"]
    assert session.get(EvidenceCheck, uri).status == "pending"
    store.objects[uri] = msg["incident_id"].encode()  # arrives over depot wifi overnight
    assert verify_pending(session, utcnow())["verified"] == 1
    session.commit()
    assert session.get(EvidenceCheck, uri).status == "verified"


def test_migration_matches_models(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from argus_api.core.config import get_settings

    monkeypatch.setattr(get_settings(), "database_url", f"sqlite:///{tmp_path / 'm.db'}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if the models have drifted from the migration
    command.downgrade(cfg, "base")
