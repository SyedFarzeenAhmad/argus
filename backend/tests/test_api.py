"""API contract tests: roles, redaction, audit, and that every asset the API emits validates
against contracts/schemas/asset.schema.json."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from argus_api.core.auth import hash_password, issue_token
from argus_api.db.models import AppUser, AuditLog, RoadSegment, Ward
from argus_api.db.types import iso, utcnow
from argus_api.fusion.engine import FusionEngine
from argus_api.ingest.validation import validator
from tests import factories as f

NOW = utcnow().replace(microsecond=0)
T = NOW - timedelta(days=2)
SEG_COORDS = [[77.6300, 12.9620], [77.6320, 12.9610]]


@pytest.fixture()
def client(factory, bus, store):
    from argus_api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def hdr(role: str, user: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user or f'{role}.user', role)}"}


@pytest.fixture()
def world(session, ingestor, factory, bus):
    """One ward, one segment, a confirmed pothole, a lone candidate, an incident, telemetry."""
    session.add(
        Ward(
            ward_id=f.WARD,
            name="Domlur",
            engineer_of_record="AEE Domlur",
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [[77.62, 12.95], [77.64, 12.95], [77.64, 12.97], [77.62, 12.97], [77.62, 12.95]]
                ],
            },
            min_lon=77.62,
            min_lat=12.95,
            max_lon=77.64,
            max_lat=12.97,
        )
    )
    session.flush()
    session.add(
        RoadSegment(
            segment_id=f.SEGMENT,
            coordinates=SEG_COORDS,
            name="Old Airport Rd",
            highway="primary",
            maxspeed_kmh=50,
            length_m=248.0,
            ward_id=f.WARD,
            has_signal=True,
            min_lon=77.63,
            min_lat=12.961,
            max_lon=77.632,
            max_lat=12.962,
        )
    )
    session.commit()
    for i, dev in enumerate(["BLR-BUS-0001-EDGE", "BLR-BUS-0002-EDGE", "BLR-BUS-0003-EDGE"]):
        ingestor.handle("observation", f.observation(device=dev, at=T + timedelta(hours=i)))
    ingestor.handle(
        "observation",
        f.observation(device="BLR-BUS-0009-EDGE", lat=f.LAT + 0.003, at=T, segment="osm:way/99:0"),
    )
    for i in range(6):
        ingestor.handle(
            "segment_pass",
            f.segment_pass(
                device=f"BLR-BUS-000{i % 3 + 1}-EDGE",
                at=T + timedelta(hours=i, minutes=5),
                found=["pothole"],
                speed=12.0,
                dwell=10.0,
                stopped=25.0,
                occupancy=0.6,
            ),
        )
    ingestor.handle("incident", f.incident(at=T + timedelta(hours=1)))
    ingestor.handle("telemetry", f.telemetry(at=NOW - timedelta(seconds=10), quality=0.5))
    with factory() as s:
        FusionEngine(s, bus=bus).run_once(now=NOW)
        s.commit()


def test_auth_required(client):
    assert client.get("/api/v1/assets").status_code == 401


def test_token_flow(client, session):
    session.add(
        AppUser(username="eng.rao", password_hash=hash_password("s3cret-pass"), role="engineer")
    )
    session.commit()
    r = client.post("/api/v1/auth/token", data={"username": "eng.rao", "password": "s3cret-pass"})
    assert r.status_code == 200
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json() == {"username": "eng.rao", "role": "engineer"}
    assert (
        client.post(
            "/api/v1/auth/token", data={"username": "eng.rao", "password": "wrong"}
        ).status_code
        == 401
    )


def test_default_view_hides_candidates_and_emits_valid_contracts(client, world):
    r = client.get("/api/v1/assets", headers=hdr("viewer"))
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    a = body["items"][0]
    validator("asset").validate(a)
    assert a["state"] == "reported" and a["distinct_devices"] == 3
    assert a["work_order"]["ward_name"] == "Domlur"
    everything = client.get("/api/v1/assets?state=all", headers=hdr("viewer")).json()
    assert everything["total"] == 2
    for item in everything["items"]:
        validator("asset").validate(item)


def test_asset_filters(client, world):
    h = hdr("viewer")
    assert client.get("/api/v1/assets?class=waterlogging", headers=h).json()["total"] == 0
    assert client.get("/api/v1/assets?bbox=77.0,12.0,77.1,12.1", headers=h).json()["total"] == 0
    assert client.get("/api/v1/assets?severity=critical", headers=h).json()["total"] == 0
    assert client.get(f"/api/v1/assets?ward={f.WARD}", headers=h).json()["total"] == 1
    assert client.get("/api/v1/assets?bbox=1,2,3", headers=h).status_code == 422


def test_asset_detail_has_history(client, world):
    aid = client.get("/api/v1/assets", headers=hdr("viewer")).json()["items"][0]["asset_id"]
    d = client.get(f"/api/v1/assets/{aid}", headers=hdr("viewer")).json()
    assert len(d["observations"]) == 3
    assert [h["to"] for h in d["history"]] == ["candidate", "confirmed", "reported"]
    assert d["work_orders"][0]["status"] == "issued"


def test_reject_needs_named_engineer(client, world, session):
    aid = client.get("/api/v1/assets", headers=hdr("viewer")).json()["items"][0]["asset_id"]
    assert (
        client.post(f"/api/v1/assets/{aid}/reject", json={}, headers=hdr("viewer")).status_code
        == 403
    )
    r = client.post(
        f"/api/v1/assets/{aid}/reject",
        json={"reason": "tar patch"},
        headers=hdr("engineer", "eng.rao"),
    )
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    assert session.scalars(select(AuditLog).where(AuditLog.action == "asset.reject")).one()


def test_work_orders_list_status_and_export(client, world, session):
    items = client.get("/api/v1/work-orders", headers=hdr("viewer")).json()["items"]
    assert len(items) == 1 and items[0]["reference"] == "ARG-BBMP-150-000001"
    ref = items[0]["reference"]
    r = client.post(
        f"/api/v1/work-orders/{ref}/status",
        json={"status": "completed"},
        headers=hdr("engineer", "eng.rao"),
    )
    assert r.status_code == 200 and r.json()["status"] == "awaiting_verification"
    assert r.json()["asset_state"] == "in_progress"  # only the fleet can say it's fixed
    csv = client.get("/api/v1/work-orders/export.csv", headers=hdr("engineer", "eng.rao"))
    assert csv.status_code == 200 and csv.text.startswith("reference,ward_id")
    assert ref in csv.text
    assert session.scalars(select(AuditLog).where(AuditLog.action == "work_orders.export")).one()


def test_incident_redaction_audit_and_report(client, world, session):
    items = client.get("/api/v1/incidents", headers=hdr("viewer")).json()["items"]
    assert len(items) == 1 and "KA 05" not in str(items[0])
    iid = items[0]["incident_id"]
    assert client.get(f"/api/v1/incidents/{iid}", headers=hdr("viewer")).status_code == 403
    d = client.get(f"/api/v1/incidents/{iid}", headers=hdr("engineer", "eng.rao")).json()
    assert d["subject_vehicle"]["plate"]["text"] == "KA 05 MJ 7213"
    pdf = client.get(f"/api/v1/incidents/{iid}/report.pdf", headers=hdr("engineer", "eng.rao"))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-1.4")
    ev = client.get(
        f"/api/v1/incidents/{iid}/evidence?item=plate",
        headers=hdr("engineer", "eng.rao"),
        follow_redirects=False,
    )
    assert ev.status_code == 307
    actions = {
        a.action for a in session.scalars(select(AuditLog).where(AuditLog.actor == "eng.rao"))
    }
    assert {"incident.view", "incident.report", "incident.evidence.plate"} <= actions


def test_anonymous_kiosk_cannot_see_audited_things(client, world, settings, monkeypatch):
    monkeypatch.setattr(settings, "anonymous_role", "engineer")
    assert client.get("/api/v1/assets").status_code == 200
    iid = client.get("/api/v1/incidents").json()["items"][0]["incident_id"]
    assert client.get(f"/api/v1/incidents/{iid}").status_code == 401


def test_congestion_with_dwell_correction_and_attribution(client, world):
    r = client.get(
        f"/api/v1/analytics/congestion?at={iso(T + timedelta(hours=6))}&window=720",
        headers=hdr("viewer"),
    ).json()
    seg = r["segments"][0]
    assert seg["passes"] == 6 and seg["freeflow_method"] == "maxspeed"
    # 248 m in (53.6 − 10) s is 20.5 km/h, not the raw 12 — dwell at the stop is excluded.
    assert seg["speed_kmh"] == pytest.approx(20.48, abs=0.05)
    assert seg["congestion_index"] == pytest.approx(1 - 20.48 / 40.0, abs=0.01)
    assert seg["attribution"]["cause"] == "demand"  # occupancy 0.6
    assert seg["intersection_delay_s"] == 15.0  # 25 s stopped − 10 s dwell, signalised
    assert seg["low_confidence"] is False and seg["path"] == SEG_COORDS


def test_bottlenecks_rank_by_passenger_hours(client, world):
    r = client.get(
        f"/api/v1/analytics/bottlenecks?since={iso(T - timedelta(hours=1))}", headers=hdr("viewer")
    ).json()
    assert r["occupancy_source"] == "assumed"
    assert r["items"][0]["passenger_hours_lost"] > 0


def test_pedestrian_density_is_a_ratio_of_sums(client, world):
    q = f"since={iso(T - timedelta(hours=1))}"
    seg = client.get(
        f"/api/v1/analytics/pedestrian-density?{q}&resolution=segment", headers=hdr("viewer")
    ).json()["segments"][0]
    assert seg["density_per_100m2"] == pytest.approx(23 / 1840 * 100, rel=1e-3)
    hexes = client.get(
        f"/api/v1/analytics/pedestrian-density?{q}&resolution=10", headers=hdr("viewer")
    ).json()
    assert hexes["cells"] and all(
        c["density_per_100m2"] == pytest.approx(1.25, rel=1e-3) for c in hexes["cells"]
    )
    assert hexes["caveats"]
    assert (
        client.get(
            f"/api/v1/analytics/pedestrian-density?{q}&preset=bogus", headers=hdr("viewer")
        ).status_code
        == 422
    )


def test_ward_scorecard_normalises_by_km_surveyed(client, world):
    w = client.get("/api/v1/analytics/ward-scorecard", headers=hdr("viewer")).json()["wards"][0]
    assert w["ward_id"] == f.WARD and w["km_surveyed"] == pytest.approx(0.248)
    assert w["deficiency_index"] > 0 and w["open_assets"] == 1
    assert w["work_orders_issued"] == 1 and w["closure_rate"] == 0.0


def test_road_condition_and_coverage(client, world):
    rc = client.get("/api/v1/analytics/road-condition", headers=hdr("viewer")).json()["segments"]
    assert rc[0]["status"] == "surveyed" and 0 <= rc[0]["condition"] < 100
    cov = client.get("/api/v1/analytics/coverage", headers=hdr("viewer")).json()
    assert cov["summary"]["km_surveyed"] == pytest.approx(0.25, abs=0.01)
    assert cov["segments"][0]["status"] == "surveyed"


def test_route_delay_and_attribution(client, world):
    r = client.get(
        f"/api/v1/analytics/route-delay?route=500D&since={iso(T - timedelta(hours=1))}",
        headers=hdr("viewer"),
    ).json()
    assert r["delay"]["samples"] == 1 and r["delay"]["median_s"] == 214
    assert r["attribution"]["segments"][0]["segment_id"] == f.SEGMENT
    assert "500D" in r["attribution"]["statement"]


def test_fleet_live_flags_dirty_lens(client, world):
    v = client.get("/api/v1/fleet/live", headers=hdr("viewer")).json()["vehicles"]
    assert v and "camera_quality:front" in v[0]["flags"]
    s = client.get("/api/v1/fleet/summary", headers=hdr("viewer")).json()
    assert s["devices_online"] == 1 and s["health_flags"]["camera_quality"] == 1


def test_geojson(client, world):
    wards = client.get("/api/v1/geo/wards", headers=hdr("viewer")).json()
    assert wards["features"][0]["properties"]["name"] == "Domlur"
    segs = client.get("/api/v1/geo/segments", headers=hdr("viewer")).json()
    assert segs["features"][0]["geometry"]["type"] == "LineString"


def test_http_ingest_is_admin_only(client, world):
    msg = f.observation()
    assert (
        client.post(
            "/api/v1/ingest/observation", json=msg, headers=hdr("engineer", "e")
        ).status_code
        == 403
    )
    r = client.post(
        "/api/v1/ingest/observation", json=[msg, msg, {"bad": 1}], headers=hdr("admin", "root")
    ).json()
    assert r["stored"] == 1 and r["duplicate"] == 1 and len(r["rejected"]) == 1


def test_websocket_streams_events(client, world, bus, ingestor):
    with client.websocket_connect(
        f"/ws/live?token={issue_token('v', 'viewer')}&types=telemetry"
    ) as ws:
        assert ws.receive_json()["type"] == "hello"
        ingestor.handle("telemetry", f.telemetry(at=NOW - timedelta(seconds=5)))
        msg = ws.receive_json()
        assert msg["type"] == "telemetry" and msg["data"]["device_id"] == "BLR-BUS-4417-EDGE"


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/live?token=nope") as ws:
        ws.receive_json()


def test_admin_devices_and_audit(client, world):
    h = hdr("admin", "root")
    devs = client.get("/api/v1/admin/devices", headers=h).json()["items"]
    assert any(d["device_id"] == "BLR-BUS-0001-EDGE" for d in devs)
    r = client.post("/api/v1/admin/devices/BLR-BUS-0001-EDGE", json={"action": "revoke"}, headers=h)
    assert r.json()["revoked"] is True
    log = client.get("/api/v1/admin/audit?action=device.revoke", headers=h).json()["items"]
    assert log[0]["actor"] == "root"
    assert client.get("/api/v1/admin/audit", headers=hdr("engineer", "e")).status_code == 403
