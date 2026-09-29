"""Fusion invariants that must hold for *any* observation sequence (backend/README).

These are the tests that catch plausible-looking wrong answers, which no demo ever will.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from argus_api.fusion.evidence import (
    FusionParams,
    NegativeEvidence,
    PositiveEvidence,
    fuse,
    fuse_log_odds,
    fuse_position,
    repair_evidence,
    sigmoid,
)

P = FusionParams()
T0 = datetime(2026, 6, 1, tzinfo=UTC)

devices = st.sampled_from([f"DEV-{i:02d}" for i in range(6)])
probs = st.floats(min_value=0.01, max_value=0.99)
sigmas = st.floats(min_value=0.5, max_value=30.0)
qual = st.floats(min_value=0.05, max_value=1.0)


@st.composite
def positive(draw, t_min=0):
    return PositiveEvidence(
        device_id=draw(devices),
        captured_at=T0 + timedelta(minutes=draw(st.integers(t_min, 60 * 24 * 30))),
        confidence=draw(probs),
        lat=12.96 + draw(st.floats(-1e-4, 1e-4)),
        lon=77.63 + draw(st.floats(-1e-4, 1e-4)),
        accuracy_m=draw(sigmas),
        quality=draw(qual),
        reliability=draw(st.floats(0.05, 1.0)),
        severity=draw(st.floats(0, 1)),
    )


@st.composite
def negative(draw):
    return NegativeEvidence(
        device_id=draw(devices),
        at=T0 + timedelta(minutes=draw(st.integers(0, 60 * 24 * 40))),
        quality=draw(qual),
        reliability=draw(st.floats(0.05, 1.0)),
    )


@settings(max_examples=300, deadline=None)
@given(
    st.lists(positive(), min_size=1, max_size=40),
    st.lists(negative(), max_size=20),
    st.floats(0.05, 0.95),
)
def test_confidence_stays_in_unit_interval(pos, neg, prior):
    f = fuse(prior, pos, neg, P)
    assert 0.0 <= f.confidence <= 1.0
    assert f.distinct_devices <= f.evidence_count == len(pos)


@settings(max_examples=300, deadline=None)
@given(
    st.lists(positive(), min_size=1, max_size=30),
    st.floats(0.51, 0.99),
    st.floats(0.05, 1.0),
    st.floats(0.05, 1.0),
)
def test_repeat_device_raises_confidence_strictly_less_than_new_device(pos, p, q, r):
    """The correlation discount: forty sightings from one bus are one bus's opinion."""
    last = max(e.captured_at for e in pos) + timedelta(minutes=1)
    seen = pos[0].device_id
    fresh = "DEV-NEW"
    assume(all(e.device_id != fresh for e in pos))

    def add(dev):
        e = PositiveEvidence(
            device_id=dev,
            captured_at=last,
            confidence=p,
            lat=12.96,
            lon=77.63,
            accuracy_m=5.0,
            quality=q,
            reliability=r,
        )
        return fuse_log_odds(0.5, [*pos, e], [], P)

    base = fuse_log_odds(0.5, pos, [], P)
    from_seen, from_new = add(seen), add(fresh)
    assert base < from_seen < from_new


@settings(max_examples=300, deadline=None)
@given(
    st.lists(
        st.tuples(st.floats(-1e-4, 1e-4), st.floats(-1e-4, 1e-4), sigmas), min_size=1, max_size=40
    ),
    st.tuples(st.floats(-1e-4, 1e-4), st.floats(-1e-4, 1e-4), sigmas),
)
def test_sigma_never_increases_and_never_drops_below_floor(pts, extra):
    pts = [(12.96 + a, 77.63 + b, s) for a, b, s in pts]
    extra = (12.96 + extra[0], 77.63 + extra[1], extra[2])
    _, _, s1 = fuse_position(pts, P.position_sigma_floor_m)
    _, _, s2 = fuse_position([*pts, extra], P.position_sigma_floor_m)
    assert s2 <= s1 + 1e-9
    assert s1 >= P.position_sigma_floor_m and s2 >= P.position_sigma_floor_m


@settings(max_examples=200, deadline=None)
@given(st.lists(positive(), min_size=1, max_size=20), st.lists(negative(), min_size=1, max_size=10))
def test_negative_evidence_only_lowers_confidence(pos, neg):
    last = max(e.captured_at for e in pos)
    later = [
        NegativeEvidence(
            n.device_id, last + (n.at - T0) + timedelta(seconds=1), n.quality, n.reliability
        )
        for n in neg
    ]
    with_neg = fuse(0.5, pos, later, P)
    without = fuse(0.5, pos, [], P)
    assert with_neg.confidence < without.confidence
    assert with_neg.negative_passes == len(later)


@settings(max_examples=200, deadline=None)
@given(st.lists(positive(), min_size=1, max_size=20), st.lists(negative(), max_size=10))
def test_negatives_before_the_last_sighting_do_not_count(pos, neg):
    last = max(e.captured_at for e in pos)
    earlier = [n for n in neg if n.at <= last]
    assert fuse(0.5, pos, earlier, P).negative_passes == 0


def test_three_independent_070_outrank_one_isolated_095():
    trio = [
        PositiveEvidence(f"D{i}", T0 + timedelta(hours=i), 0.70, 12.96, 77.63, 5.2)
        for i in range(3)
    ]
    lone = [PositiveEvidence("D9", T0, 0.95, 12.96, 77.63, 5.2)]
    assert fuse(0.5, trio, [], P).confidence > fuse(0.5, lone, [], P).confidence


def test_position_accuracy_table_from_docs():
    """docs/04: 1 pass ±5.2 m, 4 passes ±2.6 m, 12 passes floored at the multipath limit."""
    one = fuse_position([(12.96, 77.63, 5.2)], 2.0)[2]
    four = fuse_position([(12.96, 77.63, 5.2)] * 4, 2.0)[2]
    twelve = fuse_position([(12.96, 77.63, 5.2)] * 12, 2.0)[2]
    assert round(one, 1) == 5.2 and round(four, 1) == 2.6 and twelve == 2.0


def test_severity_is_median_not_mean():
    pos = [
        PositiveEvidence(f"D{i}", T0 + timedelta(hours=i), 0.9, 12.96, 77.63, 5.0, severity=s)
        for i, s in enumerate([0.4, 0.42, 0.44, 0.99])
    ]
    assert fuse(0.5, pos, [], P).severity_score == 0.43


def test_worsening_trend_detected():
    pos = [
        PositiveEvidence(
            f"D{i % 3}", T0 + timedelta(days=i), 0.9, 12.96, 77.63, 5.0, severity=0.40 + 0.02 * i
        )
        for i in range(10)
    ]
    assert fuse(0.5, pos, [], P).severity_trend == "worsening"


def test_canonical_evidence_is_best_quality_and_blurred():
    def ev(q, blurred=True):
        return {
            "uri": f"s3://b/{q}.jpg",
            "sha256": "0" * 64,
            "quality": q,
            "faces_blurred": blurred,
        }

    pos = [
        PositiveEvidence(f"D{i}", T0 + timedelta(days=i), 0.9, 12.96, 77.63, 5.0, evidence=e)
        for i, e in enumerate([ev(0.5), ev(0.99, False), ev(0.9), ev(0.7)])
    ]
    f = fuse(0.5, pos, [], P)
    assert f.canonical_evidence["quality"] == 0.9
    assert all(g["faces_blurred"] for g in f.evidence_gallery)


def test_sigmoid_is_stable_at_extremes():
    assert sigmoid(-1000) == 0.0 and sigmoid(1000) == 1.0


@settings(max_examples=200, deadline=None)
@given(st.lists(negative(), max_size=15), negative())
def test_repair_evidence_grows_with_each_miss_and_one_device_saturates(neg, extra):
    later = NegativeEvidence(
        extra.device_id, T0 + timedelta(days=60), extra.quality, extra.reliability
    )
    assert repair_evidence([*neg, later], P) > repair_evidence(neg, P)
    same = [NegativeEvidence("DEV-00", T0 + timedelta(hours=i), 1.0, 1.0) for i in range(50)]
    # γ = 0.5 bounds one camera's total at twice its best single pass.
    assert repair_evidence(same, P) < 2 * repair_evidence(same[:1], P) + 1e-9


def test_night_misses_weigh_less_than_clean_daylight_misses():
    day = [NegativeEvidence(f"D{i}", T0 + timedelta(hours=i), 0.9, 0.9) for i in range(3)]
    night = [NegativeEvidence(f"D{i}", T0 + timedelta(hours=i), 0.25, 0.9) for i in range(3)]
    assert repair_evidence(day, P) >= 3.0 > repair_evidence(night, P)
