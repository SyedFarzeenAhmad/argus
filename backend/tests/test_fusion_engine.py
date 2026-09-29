"""End-to-end fusion behaviour: the lifecycle in docs/04, driven through real ingest."""

from __future__ import annotations

from datetime import timedelta

import pytest
from jsonschema.exceptions import ValidationError  # noqa: F401
from sqlalchemy import select

from argus_api.db.models import (
    Asset,
    AssetEvent,
    Device,
    ExpectedAsset,
    HardNegative,
    RoadSegment,
    WorkOrder,
)
from argus_api.db.types import utcnow
from argus_api.fusion.engine import FusionEngine
from argus_api.ingest.validation import validator
from tests import factories as f

T0 = f.BASE_TIME


def run(factory, bus, now=None):
    with factory() as s:
        eng = FusionEngine(s, bus=bus)
        stats = eng.run_once(now=now or T0 + timedelta(days=1))
        s.commit()
        eng.publish_events()
    return stats


def only_asset(session, cls="pothole"):
    session.expire_all()
    return session.scalars(select(Asset).where(Asset.class_id == cls)).one()


def test_single_device_stays_candidate(ingestor, factory, session, bus):
    for i in range(10):
        ingestor.handle("observation", f.observation(at=T0 + timedelta(minutes=i)))
    run(factory, bus)
    a = only_asset(session)
    assert a.state == "candidate"
    assert a.evidence_count == 10 and a.distinct_devices == 1
    assert session.scalars(select(WorkOrder)).first() is None


def test_distinct_devices_confirm_and_draft_work_order(ingestor, factory, session, bus):
    for i, dev in enumerate(["BLR-BUS-0001-EDGE", "BLR-BUS-0002-EDGE", "BLR-BUS-0003-EDGE"]):
        ingestor.handle(
            "observation",
            f.observation(device=dev, at=T0 + timedelta(hours=i), lat=f.LAT + i * 1e-5),
        )
    run(factory, bus)
    a = only_asset(session)
    assert a.state == "reported"  # confirmed, then work order auto-issued
    assert a.confirmed_at is not None and a.confidence >= 0.85
    assert a.accuracy_m < 5.2
    wo = session.scalars(select(WorkOrder)).one()
    assert wo.reference == "ARG-BBMP-150-000001" and wo.priority_rank == 1
    assert wo.sla_due_at - wo.issued_at == timedelta(days=7)  # medium band
    created = [e for e in bus.published if e["type"] == "asset.created"]
    assert created, "asset.created must be published"
    validator("asset").validate(created[-1]["data"])


def test_three_independent_070_outrank_one_isolated_095(ingestor, factory, session, bus):
    for i, dev in enumerate(["BLR-BUS-0001-EDGE", "BLR-BUS-0002-EDGE", "BLR-BUS-0003-EDGE"]):
        ingestor.handle(
            "observation", f.observation(device=dev, confidence=0.70, at=T0 + timedelta(hours=i))
        )
    ingestor.handle(
        "observation", f.observation(device="BLR-BUS-0009-EDGE", confidence=0.95, lat=f.LAT + 0.01)
    )
    run(factory, bus)
    session.expire_all()
    trio, lone = sorted(session.scalars(select(Asset)).all(), key=lambda a: -a.evidence_count)
    assert trio.confidence > lone.confidence
    assert trio.state == "reported" and lone.state == "candidate"


def test_separate_segments_never_merge(ingestor, factory, session, bus):
    ingestor.handle("observation", f.observation(segment="osm:way/1:0"))
    ingestor.handle("observation", f.observation(segment="osm:way/2:0", lat=f.LAT + 2e-5))
    run(factory, bus)
    assert len(session.scalars(select(Asset)).all()) == 2


def confirm_pothole(ingestor, factory, bus):
    for i, dev in enumerate(["BLR-BUS-0001-EDGE", "BLR-BUS-0002-EDGE"]):
        ingestor.handle("observation", f.observation(device=dev, at=T0 + timedelta(hours=i)))
    run(factory, bus)


MISSERS = ["BLR-BUS-0011-EDGE", "BLR-BUS-0012-EDGE", "BLR-BUS-0013-EDGE"]


def misses(ingestor, start_day: int, devices=MISSERS, **kw):
    """Clean daylight passes that looked for potholes and saw none."""
    kw = {"occlusion": 0.0, "assessable": 1.0, **kw}
    for i, dev in enumerate(devices):
        ingestor.handle(
            "segment_pass", f.segment_pass(device=dev, at=T0 + timedelta(days=start_day + i), **kw)
        )


def test_negative_passes_resolve_and_close_work_order(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    misses(ingestor, 1)
    run(factory, bus, now=T0 + timedelta(days=4))
    a = only_asset(session)
    assert a.state == "resolved" and a.resolved_at is not None
    wo = session.scalars(select(WorkOrder)).one()
    assert wo.status == "closed" and wo.closed_by == "auto:fleet-verified"
    assert any(e["type"] == "asset.resolved" for e in bus.published)


def test_one_bus_missing_it_repeatedly_is_occlusion_not_repair(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    misses(ingestor, 1, devices=["BLR-BUS-0011-EDGE"] * 6)
    run(factory, bus, now=T0 + timedelta(days=8))
    a = only_asset(session)
    assert a.negative_passes == 6 and a.state == "reported"


def test_murky_misses_are_not_enough_to_resolve(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    misses(ingestor, 1, occlusion=0.5, assessable=0.72, illumination="night")
    run(factory, bus, now=T0 + timedelta(days=4))
    a = only_asset(session)
    assert a.negative_passes == 3 and a.state == "reported"


def test_candidates_are_never_resolved(ingestor, factory, session, bus):
    ingestor.handle("observation", f.observation(device="BLR-BUS-0001-EDGE"))
    run(factory, bus)
    misses(ingestor, 1)
    run(factory, bus, now=T0 + timedelta(days=4))
    a = only_asset(session)
    assert a.state == "candidate" and a.negative_passes == 3 and a.confidence < 0.5


def test_young_asset_absorbs_a_noisy_repeat_sighting(ingestor, factory, session, bus):
    ingestor.handle("observation", f.observation(device="BLR-BUS-0001-EDGE"))
    run(factory, bus)
    # ~11 m away: outside eps = 8 m, inside 2·√(5.2² + 5.2²) ≈ 14.7 m.
    ingestor.handle(
        "observation",
        f.observation(device="BLR-BUS-0002-EDGE", lat=f.LAT + 1.0e-4, at=T0 + timedelta(hours=1)),
    )
    run(factory, bus)
    assert only_asset(session).evidence_count == 2


def test_low_assessable_passes_are_not_negative_evidence(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    for d in range(1, 6):
        ingestor.handle("segment_pass", f.segment_pass(at=T0 + timedelta(days=d), assessable=0.3))
    run(factory, bus, now=T0 + timedelta(days=6))
    a = only_asset(session)
    assert a.state == "reported" and a.negative_passes == 0


def test_found_class_is_not_negative(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    for d in range(1, 5):
        ingestor.handle(
            "segment_pass", f.segment_pass(at=T0 + timedelta(days=d), found=["pothole"])
        )
    run(factory, bus, now=T0 + timedelta(days=5))
    assert only_asset(session).negative_passes == 0


def test_resolved_asset_reopens_on_new_sighting(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    misses(ingestor, 1)
    run(factory, bus, now=T0 + timedelta(days=4))
    assert only_asset(session).state == "resolved"
    ingestor.handle(
        "observation", f.observation(device="BLR-BUS-0003-EDGE", at=T0 + timedelta(days=20))
    )
    run(factory, bus, now=T0 + timedelta(days=21))
    a = only_asset(session)
    assert a.reopen_count == 1 and a.state != "resolved"
    assert len(session.scalars(select(Asset)).all()) == 1


def test_stale_after_30_days_without_a_qualifying_pass(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    run(factory, bus, now=T0 + timedelta(days=31))
    assert only_asset(session).state == "stale"
    ingestor.handle("segment_pass", f.segment_pass(at=T0 + timedelta(days=32), found=["pothole"]))
    run(factory, bus, now=T0 + timedelta(days=32))
    assert only_asset(session).state == "reported"


def test_reject_writes_hard_negative_and_lowers_reliability(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    with factory() as s:
        a = s.scalars(select(Asset)).one()
        before = s.get(Device, "BLR-BUS-0001-EDGE").reliability
        FusionEngine(s, bus=bus).reject(a, "eng.rao", "tar patch", utcnow())
        s.commit()
        assert s.get(Device, "BLR-BUS-0001-EDGE").reliability < before
    assert only_asset(session).state == "rejected"
    hn = session.scalars(select(HardNegative)).one()
    assert len(hn.observation_ids) == 2 and hn.rejected_by == "eng.rao"
    wo = session.scalars(select(WorkOrder)).one()
    assert wo.status == "closed" and wo.closed_by == "eng.rao"
    # A rejected asset absorbs further sightings instead of spawning new candidates.
    ingestor.handle(
        "observation", f.observation(device="BLR-BUS-0005-EDGE", at=T0 + timedelta(days=2))
    )
    run(factory, bus)
    assert only_asset(session).state == "rejected"


@pytest.fixture()
def crossing(session):
    session.add(
        RoadSegment(
            segment_id=f.SEGMENT,
            coordinates=[[77.6309, 12.9618], [77.6320, 12.9610]],
            length_m=248.0,
            min_lon=77.6309,
            min_lat=12.9610,
            max_lon=77.6320,
            max_lat=12.9618,
        )
    )
    session.flush()
    session.add(
        ExpectedAsset(
            expected_id="osm:node/1029384756",
            class_id="missing_zebra",
            segment_id=f.SEGMENT,
            lat=f.LAT,
            lon=f.LON,
            source="osm",
            valid_from=T0 - timedelta(days=100),
        )
    )
    session.commit()


def test_ledger_raises_missing_zebra_after_repeated_qualifying_absence(
    ingestor, factory, session, bus, crossing
):
    devices = [f"BLR-BUS-{i:04d}-EDGE" for i in range(4)]
    for i in range(12):
        ingestor.handle(
            "segment_pass",
            f.segment_pass(device=devices[i % 4], at=T0 + timedelta(hours=i), school=True),
        )
    run(factory, bus)
    a = only_asset(session, "missing_zebra")
    assert a.state == "reported"
    assert a.ledger["expected_from"] == "osm" and a.ledger["consecutive_absences"] == 12
    assert a.severity_band == "critical"  # school zone
    validator("asset").validate(FusionEngine(session, bus=bus).contract(a))


def test_ledger_ignores_night_absences_and_too_few_devices(
    ingestor, factory, session, bus, crossing
):
    for i in range(12):
        ingestor.handle(
            "segment_pass", f.segment_pass(device="BLR-BUS-0001-EDGE", at=T0 + timedelta(hours=i))
        )
    run(factory, bus)
    assert session.scalars(select(Asset)).first() is None  # one device is occlusion, not fact


def test_presence_resets_ledger_and_resolves_missing(ingestor, factory, session, bus, crossing):
    devices = [f"BLR-BUS-{i:04d}-EDGE" for i in range(4)]
    for i in range(12):
        ingestor.handle(
            "segment_pass", f.segment_pass(device=devices[i % 4], at=T0 + timedelta(hours=i))
        )
    run(factory, bus)
    ingestor.handle("observation", f.observation(cls="faded_zebra", at=T0 + timedelta(days=2)))
    run(factory, bus)
    assert only_asset(session, "missing_zebra").state == "resolved"
    assert session.get(ExpectedAsset, "osm:node/1029384756").last_positive_at is not None


def test_events_recorded(ingestor, factory, session, bus):
    confirm_pothole(ingestor, factory, bus)
    states = [e.to_state for e in session.scalars(select(AssetEvent).order_by(AssetEvent.id))]
    assert states == ["candidate", "confirmed", "reported"]
