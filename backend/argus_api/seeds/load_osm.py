"""Load the road graph and the Road Asset Ledger's OSM expectations.

    uv run python -m argus_api.seeds.load_osm --bbox bengaluru-core           # live Overpass
    uv run python -m argus_api.seeds.load_osm --file data/bengaluru.osm.json  # offline

Ways are split at junction nodes into segments ``osm:way/{id}:{k}`` — the spatial key every
rollup uses, and the unit the edge map-matches to. From nodes it takes:

* ``highway=crossing`` (marked/zebra)  → expected ``missing_zebra`` (the ledger)
* ``traffic_sign=*``                    → expected ``missing_sign``
* ``highway=traffic_signals``           → ``has_signal`` on the segments that end there, so
  stopped time there is reported as intersection delay rather than link delay
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics.common import distance_to_segment_m
from argus_api.core.geo import bbox_of, line_length_m
from argus_api.db import session as db_session
from argus_api.db.models import ExpectedAsset, RoadSegment, Ward

BBOXES = {
    # docs/05: the core 8 × 8 km — MG Road, Shivajinagar, Indiranagar, Domlur, Koramangala.
    "bengaluru-core": (12.93, 77.56, 13.01, 77.65),
}
HIGHWAYS = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
)
ZEBRA_CROSSINGS = {"zebra", "marked", "uncontrolled", "traffic_signals"}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def overpass_query(s: float, w: float, n: float, e: float) -> str:
    hw = "|".join(HIGHWAYS)
    return f"""[out:json][timeout:180];
(
  way["highway"~"^({hw})(_link)?$"]({s},{w},{n},{e});
  node["highway"~"^(crossing|traffic_signals)$"]({s},{w},{n},{e});
  node["traffic_sign"]({s},{w},{n},{e});
);
out body;
>;
out skel qt;"""


def fetch(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode({"data": overpass_query(*bbox)}).encode()
    with urllib.request.urlopen(urllib.request.Request(OVERPASS_URL, data=data), timeout=240) as r:
        return json.loads(r.read())


def parse_maxspeed(v: str | None) -> int | None:
    if not v:
        return None
    m = re.match(r"\s*(\d+)\s*(mph)?", v)
    if not m:
        return None
    kmh = int(m.group(1))
    return round(kmh * 1.609) if m.group(2) else kmh


def build(osm: dict[str, Any]) -> tuple[list[RoadSegment], list[ExpectedAsset]]:
    nodes: dict[int, tuple[float, float]] = {}
    tagged: dict[int, dict[str, str]] = {}
    ways = []
    for el in osm.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
            if el.get("tags"):
                tagged[el["id"]] = el["tags"]
        elif el["type"] == "way" and "highway" in el.get("tags", {}):
            ways.append(el)

    usage: Counter[int] = Counter()
    for w in ways:
        for i, nid in enumerate(w["nodes"]):
            usage[nid] += 1 if 0 < i < len(w["nodes"]) - 1 else 2  # ends always split
    signals = {nid for nid, t in tagged.items() if t.get("highway") == "traffic_signals"}

    segments: list[RoadSegment] = []
    node_segment: dict[int, str] = {}
    for w in ways:
        tags = w["tags"]
        pieces, cur = [], []
        for nid in w["nodes"]:
            if nid not in nodes:
                continue
            cur.append(nid)
            if len(cur) > 1 and usage[nid] > 1:
                pieces.append(cur)
                cur = [nid]
        if len(cur) > 1:
            pieces.append(cur)
        for k, piece in enumerate(pieces):
            coords = [[nodes[n][1], nodes[n][0]] for n in piece]
            length = line_length_m(coords)
            if length < 1.0:
                continue
            seg_id = f"osm:way/{w['id']}:{k}"
            min_lon, min_lat, max_lon, max_lat = bbox_of(coords)
            segments.append(
                RoadSegment(
                    segment_id=seg_id,
                    osm_way_id=w["id"],
                    coordinates=coords,
                    name=tags.get("name") or tags.get("ref"),
                    highway=tags.get("highway"),
                    oneway=tags.get("oneway") in ("yes", "true", "1", "-1"),
                    maxspeed_kmh=parse_maxspeed(tags.get("maxspeed")),
                    length_m=length,
                    has_signal=piece[-1] in signals or piece[0] in signals,
                    min_lon=min_lon,
                    min_lat=min_lat,
                    max_lon=max_lon,
                    max_lat=max_lat,
                )
            )
            for n in piece[1:-1] if len(piece) > 2 else piece:
                node_segment.setdefault(n, seg_id)

    expected: list[ExpectedAsset] = []
    for nid, t in tagged.items():
        cls = None
        if t.get("highway") == "crossing" and (
            t.get("crossing") in ZEBRA_CROSSINGS or t.get("crossing_ref") == "zebra"
        ):
            cls = "missing_zebra"
        elif "traffic_sign" in t:
            cls = "missing_sign"
        if cls is None:
            continue
        lat, lon = nodes[nid]
        seg_id = node_segment.get(nid) or _nearest_segment(segments, lat, lon, 30.0)
        if seg_id is None:
            continue
        expected.append(
            ExpectedAsset(
                expected_id=f"osm:node/{nid}",
                class_id=cls,
                segment_id=seg_id,
                lat=lat,
                lon=lon,
                source="osm",
            )
        )
    return segments, expected


def _nearest_segment(
    segments: list[RoadSegment], lat: float, lon: float, max_m: float
) -> str | None:
    pad = max_m / 111_000.0 * 2
    best, best_d = None, max_m
    for s in segments:
        if not (
            s.min_lat - pad <= lat <= s.max_lat + pad and s.min_lon - pad <= lon <= s.max_lon + pad
        ):
            continue
        d = distance_to_segment_m(s, lat, lon)
        if d < best_d:
            best, best_d = s.segment_id, d
    return best


def assign_wards(session: Session) -> int:
    """Set ``road_segment.ward_id`` from the segment midpoint's containing ward polygon."""
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    wards = session.scalars(select(Ward)).all()
    if not wards:
        return 0
    shapes = [shape(w.geometry) for w in wards]
    tree = STRtree(shapes)
    n = 0
    for seg in session.scalars(select(RoadSegment)):
        mid = seg.coordinates[len(seg.coordinates) // 2]
        pt = Point(mid[0], mid[1])
        for i in tree.query(pt, predicate="intersects"):
            seg.ward_id = wards[int(i)].ward_id
            n += 1
            break
    return n


def load(session: Session, osm: dict[str, Any]) -> tuple[int, int]:
    segments, expected = build(osm)
    for s in segments:
        session.merge(s)
    session.flush()
    for e in expected:
        if session.get(ExpectedAsset, e.expected_id) is None:
            session.add(e)
    assign_wards(session)
    return len(segments), len(expected)


def main() -> None:
    ap = argparse.ArgumentParser(description="Load OSM road segments and ledger expectations")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bbox", help=f"named ({', '.join(BBOXES)}) or s,w,n,e — queries Overpass")
    g.add_argument("--file", type=Path, help="Overpass JSON saved earlier")
    ap.add_argument("--save", type=Path, help="with --bbox: also save the Overpass JSON here")
    args = ap.parse_args()
    if args.file:
        osm = json.loads(args.file.read_text(encoding="utf-8"))
    else:
        bbox = BBOXES.get(args.bbox) or tuple(float(x) for x in args.bbox.split(","))
        osm = fetch(bbox)  # type: ignore[arg-type]
        if args.save:
            args.save.write_text(json.dumps(osm), encoding="utf-8")
    with db_session.session_scope() as s:
        n_seg, n_exp = load(s, osm)
    print(f"{n_seg} segments, {n_exp} ledger expectations")


if __name__ == "__main__":
    main()
