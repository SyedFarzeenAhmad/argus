"""The fusion engine: raw observations and passes in, lifecycle-managed assets out.

One cycle (``FusionEngine.run_once``):

1. **Positive evidence** — unfused observations are clustered into assets (clustering.py) and
   each touched asset is recomputed from its full history (evidence.py).
2. **Negative evidence** — each new segment pass that *qualified* (assessable, unoccluded)
   and inspected a class without finding it adds a non-observation to every asset of that
   class on that segment, and a ledger absence to every expected asset there.
3. **Lifecycle** — promotion, auto-resolution, re-opening, and the stale sweep.

State timestamps (``confirmed_at``, ``resolved_at``, work-order ``issued_at``) come from the
evidence that caused them, not from the wall clock, so replayed or seeded history produces
the same assets it would have produced live.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from argus_api.core.config import Settings, get_settings
from argus_api.core.geo import haversine_m
from argus_api.db.models import (
    Asset,
    AssetEvent,
    AssetNegative,
    Device,
    ExpectedAsset,
    HardNegative,
    LedgerAbsence,
    Observation,
    PassProcessed,
    RoadSegment,
    SegmentPass,
    Ward,
)
from argus_api.db.types import utcnow
from argus_api.fusion import clustering
from argus_api.fusion.actions import LEDGER_CLASSES, POLICIES, PRESENCE_TO_MISSING, band_for, policy
from argus_api.fusion.evidence import (
    FusionParams,
    NegativeEvidence,
    PositiveEvidence,
    condition_quality,
    fuse,
    logit,
    range_attenuation,
    sigmoid,
)
from argus_api.fusion.work_orders import (
    FLEET_VERIFIED,
    close_work_order,
    create_work_order,
    latest_work_order,
    open_work_order,
    priority_score,
    rerank_ward,
)
from argus_api.realtime.bus import EventBus, get_bus
from argus_api.serializers import asset_to_contract

log = structlog.get_logger()

ACTIVE_STATES = ("candidate", "confirmed", "reported", "in_progress")
ASSOCIABLE_STATES = (*ACTIVE_STATES, "resolved", "stale", "rejected")
_DEG_PER_M = 1.0 / 111_000.0

# Reliability is a Beta(9, 1) prior (mean 0.9) updated by confirmed vs rejected contributions.
_REL_A, _REL_B = 9.0, 1.0


def device_reliability(confirmed: int, rejected: int) -> float:
    r = (_REL_A + confirmed) / (_REL_A + _REL_B + confirmed + rejected)
    return min(1.0, max(0.05, r))


class FusionEngine:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.s = session
        self.cfg = settings or get_settings()
        self.params = FusionParams.from_settings(self.cfg)
        self.bus = bus or get_bus()
        self._events: dict[uuid.UUID, str] = {}

    # ─── Public entry points ────────────────────────────────────────────────

    def run_once(self, batch: int = 1000, now: datetime | None = None) -> dict[str, int]:
        n_obs = self.process_observations(batch)
        n_pass = self.process_passes(batch)
        n_stale = self.sweep_stale(now or utcnow())
        return {"observations": n_obs, "passes": n_pass, "stale": n_stale}

    def publish_events(self) -> int:
        """Call after commit: the bus must never announce state the DB might roll back."""
        events, self._events = self._events, {}
        for asset_id, etype in events.items():
            asset = self.s.get(Asset, asset_id)
            if asset is not None:
                self.bus.publish(etype, self.contract(asset))
        return len(events)

    def contract(self, asset: Asset) -> dict[str, Any]:
        wo = latest_work_order(self.s, asset.asset_id)
        ward_name = None
        if wo is not None and wo.ward_id:
            ward = self.s.get(Ward, wo.ward_id)
            ward_name = ward.name if ward else None
        return asset_to_contract(asset, wo, ward_name)

    # ─── 1. Positive evidence ───────────────────────────────────────────────

    def process_observations(self, limit: int = 1000) -> int:
        rows = self.s.scalars(
            select(Observation)
            .where(Observation.asset_id.is_(None), Observation.class_id.notin_(LEDGER_CLASSES))
            .order_by(Observation.captured_at)
            .limit(limit)
        ).all()
        if not rows:
            return 0

        partitions: dict[tuple[str | None, str], list[Observation]] = defaultdict(list)
        for o in rows:
            partitions[(o.segment_id, o.class_id)].append(o)

        touched: set[uuid.UUID] = set()
        created: set[uuid.UUID] = set()
        for (segment_id, class_id), obs in partitions.items():
            existing = self._nearby_assets(segment_id, class_id, obs)
            result = clustering.assign(
                [clustering.Point(o.observation_id, o.lat, o.lon, o.accuracy_m) for o in obs],
                [clustering.Point(a.asset_id, a.lat, a.lon, a.accuracy_m) for a in existing],
                eps_m=self.cfg.cluster_eps_m,
                min_samples=self.cfg.cluster_min_samples,
            )
            by_id = {o.observation_id: o for o in obs}
            for obs_id, asset_id in result.to_existing.items():
                by_id[obs_id].asset_id = asset_id
                touched.add(asset_id)
            for group in result.new_groups:
                members = [by_id[k] for k in group]
                asset = self._new_asset(members)
                for o in members:
                    o.asset_id = asset.asset_id
                touched.add(asset.asset_id)
                created.add(asset.asset_id)
        self.s.flush()

        for asset_id in touched:
            asset = self.s.get(Asset, asset_id)
            if asset is not None:
                self.refresh(asset)
                if asset_id in created:
                    self._emit(asset_id, "asset.created")

        self._ledger_presence(rows)
        return len(rows)

    def _nearby_assets(
        self, segment_id: str | None, class_id: str, obs: list[Observation]
    ) -> list[Asset]:
        pad = (self.cfg.cluster_eps_m + 50.0) * _DEG_PER_M
        lat_pad = pad
        lon_pad = pad / max(0.2, math.cos(math.radians(obs[0].lat)))
        q = select(Asset).where(
            Asset.class_id == class_id,
            Asset.state.in_(ASSOCIABLE_STATES),
            Asset.lat.between(min(o.lat for o in obs) - lat_pad, max(o.lat for o in obs) + lat_pad),
            Asset.lon.between(min(o.lon for o in obs) - lon_pad, max(o.lon for o in obs) + lon_pad),
        )
        q = q.where(
            Asset.segment_id.is_(None) if segment_id is None else Asset.segment_id == segment_id
        )
        return list(self.s.scalars(q).all())

    def _new_asset(self, members: list[Observation]) -> Asset:
        first = min(members, key=lambda o: o.captured_at)
        asset = Asset(
            asset_id=uuid.uuid4(),
            class_id=first.class_id,
            state="candidate",
            lat=first.lat,
            lon=first.lon,
            accuracy_m=first.accuracy_m,
            geo_source=first.geo_source,
            segment_id=first.segment_id,
            ward_id=first.ward_id,
            confidence=first.confidence,
            log_odds=logit(first.confidence),
            first_seen_at=first.captured_at,
            last_evidence_at=first.captured_at,
            last_inspected_at=first.captured_at,
            updated_at=utcnow(),
        )
        self.s.add(asset)
        self.s.flush()
        self._event(asset, None, "candidate", "first observation", first.captured_at)
        return asset

    # ─── Recompute one asset from its history ───────────────────────────────

    def refresh(self, asset: Asset) -> None:
        if asset.class_id in LEDGER_CLASSES:
            self._refresh_missing(asset)
            return
        obs = self.s.scalars(
            select(Observation).where(Observation.asset_id == asset.asset_id)
        ).all()
        if not obs:
            return
        rel = self._reliability({o.device_id for o in obs})
        positives = [
            PositiveEvidence(
                device_id=o.device_id,
                captured_at=o.captured_at,
                confidence=o.confidence,
                lat=o.lat,
                lon=o.lon,
                accuracy_m=o.accuracy_m,
                quality=condition_quality(o.conditions) * range_attenuation(o.range_m),
                reliability=rel.get(o.device_id, self.cfg.default_device_reliability),
                severity=o.severity_score,
                area_m2=o.area_m2,
                evidence=o.evidence,
                observation_id=str(o.observation_id),
                segment_id=o.segment_id,
                offset_m=o.offset_m,
                ward_id=o.ward_id,
                map_match_confidence=o.map_match_confidence,
            )
            for o in obs
        ]
        negs = self.s.scalars(
            select(AssetNegative).where(AssetNegative.asset_id == asset.asset_id)
        ).all()
        rel.update(self._reliability({n.device_id for n in negs} - set(rel)))
        negatives = [
            NegativeEvidence(
                device_id=n.device_id,
                at=n.at,
                quality=n.quality,
                reliability=rel.get(n.device_id, self.cfg.default_device_reliability),
            )
            for n in negs
        ]
        f = fuse(policy(asset.class_id).prior, positives, negatives, self.params)

        asset.log_odds = f.log_odds
        asset.confidence = f.confidence
        asset.lat, asset.lon, asset.accuracy_m = f.lat, f.lon, f.accuracy_m
        asset.evidence_count = f.evidence_count
        asset.distinct_devices = f.distinct_devices
        asset.negative_passes = f.negative_passes
        asset.severity_score = f.severity_score if f.severity_score is not None else 0.5
        asset.severity_band = band_for(asset.severity_score, asset.class_id)
        asset.severity_trend = f.severity_trend
        asset.physical = {"area_m2": round(f.area_m2, 3)} if f.area_m2 is not None else None
        # Raw observations expire after 180 days (docs/08), so a recompute sees only the
        # retained window. The fused state then reflects retained evidence, but the date the
        # fleet first saw the thing is history and must not move forward.
        asset.first_seen_at = min(asset.first_seen_at, f.first_seen_at)
        asset.last_evidence_at = f.last_evidence_at
        asset.last_inspected_at = max(asset.last_inspected_at, f.last_evidence_at)
        asset.canonical_evidence = f.canonical_evidence
        asset.evidence_gallery = f.evidence_gallery or None
        asset.segment_id = f.segment_id or asset.segment_id
        asset.offset_m = f.offset_m
        asset.map_match_confidence = f.map_match_confidence
        asset.ward_id = f.ward_id or self._segment_ward(asset.segment_id) or asset.ward_id
        asset.updated_at = utcnow()

        self._lifecycle(asset, f.repair_evidence_nats, f.negative_devices)
        self._emit(asset.asset_id, "asset.updated")

    def _reliability(self, device_ids: set[str]) -> dict[str, float]:
        if not device_ids:
            return {}
        rows = self.s.scalars(select(Device).where(Device.device_id.in_(device_ids))).all()
        return {d.device_id: d.reliability for d in rows}

    def _segment_ward(self, segment_id: str | None) -> str | None:
        if not segment_id:
            return None
        seg = self.s.get(RoadSegment, segment_id)
        return seg.ward_id if seg else None

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    def _lifecycle(self, asset: Asset, repair_nats: float = 0.0, negative_devices: int = 0) -> None:
        st = asset.state
        if st == "rejected":
            return

        if st == "resolved":
            if asset.resolved_at and asset.last_evidence_at > asset.resolved_at:
                # The fleet saw it again. A resolved asset re-opens — and if it happens within
                # the durability window, the repair was a cover, not a fix (docs/07 §5).
                asset.reopen_count += 1
                asset.last_reopened_at = asset.last_evidence_at
                asset.resolved_at = None
                asset.confirmed_at = None
                self._transition(
                    asset, "candidate", "re-observed after resolution", asset.last_evidence_at
                )
                st = "candidate"
            else:
                return

        if st == "stale":
            # Refresh only runs when new evidence or a qualifying pass touched the asset, so
            # reaching here means somebody looked again.
            st = self._state_from_work_order(asset)
            self._transition(asset, st, "surveyed again", asset.last_inspected_at)

        if (
            st == "candidate"
            and asset.distinct_devices >= self.cfg.confirm_distinct_devices
            and asset.confidence >= self.cfg.confirm_probability
        ):
            self._confirm(asset, asset.last_evidence_at)
            st = asset.state

        # Only a conclusion can be resolved; a candidate the fleet stops seeing just loses
        # confidence. Resolution mirrors confirmation: enough qualifying misses, from more
        # than one bus (one bus not seeing it is occlusion), carrying enough evidence that a
        # detector of known recall would not have missed a still-present defect that often.
        if (
            st in ("confirmed", "reported", "in_progress")
            and asset.negative_passes >= self.cfg.resolve_negative_passes
            and negative_devices >= self.cfg.resolve_distinct_devices
            and repair_nats >= self.cfg.resolve_evidence_nats
        ):
            at = self._nth_negative_time(asset) or asset.last_inspected_at
            asset.resolved_at = at
            self._transition(
                asset,
                "resolved",
                f"{asset.negative_passes} qualifying passes found nothing",
                at,
            )
            self._emit(asset.asset_id, "asset.resolved")
            wo = open_work_order(self.s, asset.asset_id)
            if wo is not None:
                close_work_order(self.s, wo, FLEET_VERIFIED, at)
            return

        wo = open_work_order(self.s, asset.asset_id)
        if wo is not None:
            wo.priority_score = priority_score(self.s, asset, asset.last_inspected_at)
            wo.severity_band = asset.severity_band or wo.severity_band
            rerank_ward(self.s, wo.ward_id)

    def _confirm(self, asset: Asset, at: datetime) -> None:
        asset.confirmed_at = at
        self._transition(
            asset,
            "confirmed",
            f"{asset.distinct_devices} distinct devices, p={asset.confidence:.2f}",
            at,
        )
        self._credit_devices(asset)
        if open_work_order(self.s, asset.asset_id) is None:
            create_work_order(self.s, asset, at, self.cfg.auto_issue_work_orders)
            if self.cfg.auto_issue_work_orders:
                self._transition(asset, "reported", "work order issued to ward", at)
        self._register_historical_expectation(asset)

    def _state_from_work_order(self, asset: Asset) -> str:
        wo = open_work_order(self.s, asset.asset_id)
        if wo is not None:
            return (
                "in_progress"
                if wo.status in ("in_progress", "awaiting_verification")
                else ("reported" if wo.status == "issued" else "confirmed")
            )
        return "confirmed" if asset.confirmed_at else "candidate"

    def _nth_negative_time(self, asset: Asset) -> datetime | None:
        rows = self.s.scalars(
            select(AssetNegative.at)
            .where(
                AssetNegative.asset_id == asset.asset_id, AssetNegative.at > asset.last_evidence_at
            )
            .order_by(AssetNegative.at)
        ).all()
        n = self.cfg.resolve_negative_passes
        return rows[n - 1] if len(rows) >= n else None

    def _transition(
        self, asset: Asset, to: str, reason: str, at: datetime, actor: str = "fusion"
    ) -> None:
        if asset.state == to:
            return
        self.s.add(
            AssetEvent(
                asset_id=asset.asset_id,
                at=at,
                from_state=asset.state,
                to_state=to,
                reason=reason,
                actor=actor,
            )
        )
        log.info(
            "asset.transition",
            asset_id=str(asset.asset_id),
            cls=asset.class_id,
            frm=asset.state,
            to=to,
            reason=reason,
        )
        asset.state = to
        asset.updated_at = utcnow()

    def _event(self, asset: Asset, frm: str | None, to: str, reason: str, at: datetime) -> None:
        self.s.add(
            AssetEvent(
                asset_id=asset.asset_id,
                at=at,
                from_state=frm,
                to_state=to,
                reason=reason,
                actor="fusion",
            )
        )

    def _emit(self, asset_id: uuid.UUID, etype: str) -> None:
        rank = {"asset.updated": 0, "asset.created": 1, "asset.resolved": 2}
        cur = self._events.get(asset_id)
        if cur is None or rank[etype] > rank[cur]:
            self._events[asset_id] = etype

    def _credit_devices(self, asset: Asset) -> None:
        devices = self.s.scalars(
            select(Observation.device_id).where(Observation.asset_id == asset.asset_id).distinct()
        ).all()
        for d in self.s.scalars(select(Device).where(Device.device_id.in_(devices))).all():
            d.confirmed_contributions += 1
            d.reliability = device_reliability(d.confirmed_contributions, d.rejected_contributions)

    # ─── 2. Negative evidence ───────────────────────────────────────────────

    def pass_qualifies(self, p: SegmentPass) -> bool:
        occlusion = float((p.conditions or {}).get("occlusion") or 0.0)
        return (
            p.assessable_frac >= self.cfg.min_assessable_fraction
            and occlusion <= self.cfg.max_negative_occlusion
        )

    def process_passes(self, limit: int = 1000) -> int:
        passes = self.s.scalars(
            select(SegmentPass)
            .outerjoin(PassProcessed, PassProcessed.segment_pass_id == SegmentPass.segment_pass_id)
            .where(PassProcessed.segment_pass_id.is_(None))
            .order_by(SegmentPass.exited_at)
            .limit(limit)
        ).all()
        touched: set[uuid.UUID] = set()
        for p in passes:
            touched |= self._apply_pass(p)
            self.s.add(PassProcessed(segment_pass_id=p.segment_pass_id))
        self.s.flush()
        for asset_id in touched:
            asset = self.s.get(Asset, asset_id)
            if asset is not None:
                self.refresh(asset)
        return len(passes)

    def _apply_pass(self, p: SegmentPass) -> set[uuid.UUID]:
        inspected = set(p.inspected_for or [])
        found = set(p.found or [])
        if not inspected:
            return set()
        qualifies = self.pass_qualifies(p)
        quality = condition_quality(p.conditions) * p.assessable_frac
        touched: set[uuid.UUID] = set()

        assets = self.s.scalars(
            select(Asset).where(
                Asset.segment_id == p.segment_id,
                Asset.class_id.in_(inspected - LEDGER_CLASSES),
                Asset.state.in_((*ACTIVE_STATES, "stale")),
            )
        ).all()
        for a in assets:
            if not qualifies:
                continue  # an occluded pass is not an inspection, positive or negative
            if p.exited_at > a.last_inspected_at:
                a.last_inspected_at = p.exited_at
                touched.add(a.asset_id)
            if a.class_id in found or p.entered_at <= a.last_evidence_at:
                continue  # ambiguous (another instance was found) or older than the last sighting
            exists = self.s.scalar(
                select(func.count())
                .select_from(AssetNegative)
                .where(
                    AssetNegative.asset_id == a.asset_id,
                    AssetNegative.segment_pass_id == p.segment_pass_id,
                )
            )
            if not exists:
                self.s.add(
                    AssetNegative(
                        asset_id=a.asset_id,
                        segment_pass_id=p.segment_pass_id,
                        device_id=p.device_id,
                        at=p.exited_at,
                        quality=quality,
                    )
                )
                touched.add(a.asset_id)

        if qualifies:
            touched |= self._ledger_absences(p, inspected, found, quality)
        return touched

    # ─── Road Asset Ledger: missing_* by map difference ─────────────────────

    def _ledger_absences(
        self, p: SegmentPass, inspected: set[str], found: set[str], quality: float
    ) -> set[uuid.UUID]:
        touched: set[uuid.UUID] = set()
        for presence_cls, missing_cls in PRESENCE_TO_MISSING.items():
            if presence_cls not in inspected or presence_cls in found:
                # Not looked for, or seen on this pass: presence is decided from the
                # observations themselves (_ledger_presence), never inferred here.
                continue
            expected = self.s.scalars(
                select(ExpectedAsset).where(
                    ExpectedAsset.segment_id == p.segment_id,
                    ExpectedAsset.class_id == missing_cls,
                    ExpectedAsset.active.is_(True),
                )
            ).all()
            for e in expected:
                if e.last_positive_at and p.entered_at <= e.last_positive_at:
                    continue
                if self.s.scalar(
                    select(func.count())
                    .select_from(LedgerAbsence)
                    .where(
                        LedgerAbsence.expected_id == e.expected_id,
                        LedgerAbsence.segment_pass_id == p.segment_pass_id,
                    )
                ):
                    continue
                self.s.add(
                    LedgerAbsence(
                        expected_id=e.expected_id,
                        segment_pass_id=p.segment_pass_id,
                        device_id=p.device_id,
                        at=p.exited_at,
                        weight=quality,
                    )
                )
                self.s.flush()
                in_school = bool((p.pedestrian or {}).get("in_school_zone"))
                asset_id = self._evaluate_expected(e, in_school)
                if asset_id is not None:
                    touched.add(asset_id)
        return touched

    def _absences(self, e: ExpectedAsset) -> list[LedgerAbsence]:
        q = select(LedgerAbsence).where(LedgerAbsence.expected_id == e.expected_id)
        if e.last_positive_at is not None:
            q = q.where(LedgerAbsence.at > e.last_positive_at)
        return list(self.s.scalars(q.order_by(LedgerAbsence.at)).all())

    def _open_missing_asset(self, expected_id: str) -> Asset | None:
        return self.s.scalars(
            select(Asset).where(
                Asset.expected_id == expected_id, Asset.state.notin_(("resolved", "rejected"))
            )
        ).first()

    def _evaluate_expected(self, e: ExpectedAsset, in_school_zone: bool) -> uuid.UUID | None:
        absences = self._absences(e)
        devices = {a.device_id for a in absences}
        asset = self._open_missing_asset(e.expected_id)
        if asset is None:
            if (
                len(absences) < self.cfg.ledger_min_absences
                or len(devices) < self.cfg.ledger_min_devices
            ):
                return None
            asset = Asset(
                asset_id=uuid.uuid4(),
                class_id=e.class_id,
                state="candidate",
                lat=e.lat,
                lon=e.lon,
                accuracy_m=self.cfg.position_sigma_floor_m,
                segment_id=e.segment_id,
                ward_id=self._segment_ward(e.segment_id),
                confidence=0.5,
                log_odds=0.0,
                first_seen_at=absences[0].at,
                last_evidence_at=absences[-1].at,
                last_inspected_at=absences[-1].at,
                expected_id=e.expected_id,
                severity_score=0.9 if in_school_zone else 0.7,
            )
            self.s.add(asset)
            self.s.flush()
            self._event(
                asset,
                None,
                "candidate",
                "ledger: expected but repeatedly not observed",
                absences[-1].at,
            )
            self._emit(asset.asset_id, "asset.created")
        elif in_school_zone:
            asset.severity_score = max(asset.severity_score or 0.0, 0.9)
        self._refresh_missing(asset, e, absences)
        return asset.asset_id

    def _refresh_missing(
        self,
        asset: Asset,
        e: ExpectedAsset | None = None,
        absences: list[LedgerAbsence] | None = None,
    ) -> None:
        e = e or (self.s.get(ExpectedAsset, asset.expected_id) if asset.expected_id else None)
        if e is None:
            return
        absences = absences if absences is not None else self._absences(e)
        if not absences:
            return
        rel = self._reliability({a.device_id for a in absences})
        L = logit(policy(asset.class_id).prior)
        per_device: dict[str, int] = defaultdict(int)
        neg = logit(self.cfg.negative_evidence_probability)
        for a in absences:
            w = rel.get(a.device_id, self.cfg.default_device_reliability) * a.weight
            L += w * self.cfg.correlation_discount ** per_device[a.device_id] * neg
            per_device[a.device_id] += 1
        asset.log_odds = L
        asset.confidence = sigmoid(L)
        asset.evidence_count = len(absences)
        asset.distinct_devices = len(per_device)
        asset.last_evidence_at = absences[-1].at
        asset.last_inspected_at = max(asset.last_inspected_at, absences[-1].at)
        asset.severity_band = band_for(asset.severity_score, asset.class_id)
        asset.severity_trend = "unknown"
        asset.ledger = {
            k: v
            for k, v in {
                "expected_from": {"osm": "osm", "municipal": "municipal_import"}.get(
                    e.source, e.source
                ),
                "expected_reference": e.expected_id,
                "consecutive_absences": len(absences),
                "last_positive_at": e.last_positive_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                if e.last_positive_at
                else None,
            }.items()
            if v is not None
        }
        asset.updated_at = utcnow()
        if asset.state == "stale":
            self._transition(
                asset, self._state_from_work_order(asset), "surveyed again", asset.last_inspected_at
            )
        if asset.state == "candidate":
            self._confirm(asset, asset.last_evidence_at)
        self._emit(asset.asset_id, "asset.updated")

    def _ledger_presence(self, observations: list[Observation]) -> None:
        """A sighting of the presence class near an expected asset resets its absences and
        resolves any open missing_* conclusion — the crossing was seen again."""
        radius = self.cfg.ledger_presence_radius_m
        for o in observations:
            missing_cls = PRESENCE_TO_MISSING.get(o.class_id)
            if missing_cls is None or not o.segment_id:
                continue
            for e in self.s.scalars(
                select(ExpectedAsset).where(
                    ExpectedAsset.segment_id == o.segment_id, ExpectedAsset.class_id == missing_cls
                )
            ).all():
                if haversine_m(o.lat, o.lon, e.lat, e.lon) > radius:
                    continue
                if e.last_positive_at is None or o.captured_at > e.last_positive_at:
                    e.last_positive_at = o.captured_at
                missing = self._open_missing_asset(e.expected_id)
                if missing is not None and o.captured_at > missing.last_evidence_at:
                    missing.resolved_at = o.captured_at
                    self._transition(
                        missing, "resolved", "expected asset observed again", o.captured_at
                    )
                    self._emit(missing.asset_id, "asset.resolved")
                    wo = open_work_order(self.s, missing.asset_id)
                    if wo is not None:
                        close_work_order(self.s, wo, FLEET_VERIFIED, o.captured_at)

    def _register_historical_expectation(self, asset: Asset) -> None:
        """A crossing the fleet has itself observed becomes an expectation: if it later
        vanishes, that is a ``historical_observation`` missing_* conclusion."""
        missing_cls = PRESENCE_TO_MISSING.get(asset.class_id)
        if missing_cls is None or not asset.segment_id:
            return
        for e in self.s.scalars(
            select(ExpectedAsset).where(
                ExpectedAsset.segment_id == asset.segment_id, ExpectedAsset.class_id == missing_cls
            )
        ).all():
            if haversine_m(asset.lat, asset.lon, e.lat, e.lon) <= self.cfg.ledger_presence_radius_m:
                return
        self.s.add(
            ExpectedAsset(
                expected_id=f"argus:asset/{asset.asset_id}",
                class_id=missing_cls,
                segment_id=asset.segment_id,
                lat=asset.lat,
                lon=asset.lon,
                source="historical_observation",
                valid_from=asset.first_seen_at,
                last_positive_at=asset.last_evidence_at,
            )
        )

    # ─── 3. Stale sweep ─────────────────────────────────────────────────────

    def sweep_stale(self, now: datetime) -> int:
        """`stale` is a real state: silence about a road nobody drove is not "no defects"."""
        cutoff = now - timedelta(days=self.cfg.stale_after_days)
        rows = self.s.scalars(
            select(Asset).where(Asset.state.in_(ACTIVE_STATES), Asset.last_inspected_at < cutoff)
        ).all()
        for a in rows:
            self._transition(
                a, "stale", f"no qualifying pass in {self.cfg.stale_after_days} days", now
            )
            self._emit(a.asset_id, "asset.updated")
        return len(rows)

    # ─── Operator feedback ──────────────────────────────────────────────────

    def reject(self, asset: Asset, operator: str, reason: str | None, now: datetime) -> None:
        """Operator says "not real": hard negative for retraining, and every contributing
        device is believed a little less from now on."""
        if asset.state == "rejected":
            return
        obs = self.s.scalars(
            select(Observation).where(Observation.asset_id == asset.asset_id)
        ).all()
        self.s.add(
            HardNegative(
                asset_id=asset.asset_id,
                class_id=asset.class_id,
                observation_ids=[str(o.observation_id) for o in obs],
                evidence=[o.evidence for o in obs if o.evidence],
                rejected_by=operator,
                reason=reason,
                at=now,
            )
        )
        devices = {o.device_id for o in obs}
        for d in self.s.scalars(select(Device).where(Device.device_id.in_(devices))).all():
            d.rejected_contributions += 1
            d.reliability = device_reliability(d.confirmed_contributions, d.rejected_contributions)
        wo = open_work_order(self.s, asset.asset_id)
        if wo is not None:
            close_work_order(self.s, wo, operator, now)
        if asset.expected_id:
            # A rejected missing_* means the expectation was wrong (the map is out of date);
            # retire it, or the next absence would raise the same conclusion again.
            e = self.s.get(ExpectedAsset, asset.expected_id)
            if e is not None:
                e.active = False
        self._transition(asset, "rejected", reason or "operator rejected", now, actor=operator)
        self._emit(asset.asset_id, "asset.updated")

    def set_work_order_status(
        self, asset: Asset, status: str, operator: str, now: datetime
    ) -> None:
        """Engineer-driven progress. Note there is no manual "resolved": an engineer can say
        the work is done, but only the fleet can say the defect is gone."""
        wo = open_work_order(self.s, asset.asset_id)
        if wo is None:
            raise ValueError("asset has no open work order")
        if status == "issued":
            wo.status, wo.issued_at = "issued", wo.issued_at or now
            self._transition(asset, "reported", "work order issued", now, actor=operator)
        elif status == "in_progress":
            wo.status = "in_progress"
            self._transition(asset, "in_progress", "work started", now, actor=operator)
        elif status == "completed":
            wo.status = "awaiting_verification"
            self._transition(
                asset,
                "in_progress",
                "work reported complete; awaiting fleet verification",
                now,
                actor=operator,
            )
        else:
            raise ValueError(f"unknown status {status!r}")
        self._emit(asset.asset_id, "asset.updated")


def pending_counts(session: Session) -> dict[str, int]:
    obs = session.scalar(
        select(func.count()).select_from(Observation).where(Observation.asset_id.is_(None))
    )
    passes = session.scalar(
        select(func.count())
        .select_from(SegmentPass)
        .outerjoin(
            PassProcessed, and_(PassProcessed.segment_pass_id == SegmentPass.segment_pass_id)
        )
        .where(PassProcessed.segment_pass_id.is_(None))
    )
    return {"observations": obs or 0, "passes": passes or 0}


__all__ = ["FusionEngine", "device_reliability", "pending_counts", "POLICIES"]
