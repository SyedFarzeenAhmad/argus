"""Load ward boundaries from GeoJSON.

    uv run python -m argus_api.seeds.load_wards --source bbmp --file data/bbmp-wards.geojson

BBMP ward boundaries are published as open data (e.g. via opencity.in). Field names vary
between releases, so the id and name fields are auto-detected and can be overridden.
Re-assigns every road segment to its ward afterwards.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from argus_api.core.geo import bbox_of
from argus_api.db import session as db_session
from argus_api.db.models import Ward
from argus_api.seeds.load_osm import assign_wards

ID_FIELDS = ("ward_no", "WARD_NO", "KGISWardNo", "ward_id", "wardno", "id")
NAME_FIELDS = ("ward_name", "WARD_NAME", "KGISWardName", "name", "wardname")
POP_FIELDS = ("population", "POP_TOTAL", "pop")


def _pick(props: dict[str, Any], explicit: str | None, candidates: tuple[str, ...]) -> Any:
    if explicit:
        return props.get(explicit)
    for k in candidates:
        if props.get(k) not in (None, ""):
            return props[k]
    return None


def _coords(geom: dict[str, Any]):
    if geom["type"] == "Polygon":
        return [c for ring in geom["coordinates"] for c in ring]
    return [c for poly in geom["coordinates"] for ring in poly for c in ring]


def load(
    session: Session,
    fc: dict[str, Any],
    source: str,
    id_field: str | None = None,
    name_field: str | None = None,
) -> int:
    n = 0
    for f in fc["features"]:
        geom, props = f.get("geometry"), f.get("properties") or {}
        if not geom or geom["type"] not in ("Polygon", "MultiPolygon"):
            continue
        raw_id = _pick(props, id_field, ID_FIELDS) or f.get("id")
        if raw_id is None:
            continue
        raw_id = (
            str(int(raw_id)) if isinstance(raw_id, float) and raw_id.is_integer() else str(raw_id)
        )
        min_lon, min_lat, max_lon, max_lat = bbox_of(_coords(geom))
        pop = _pick(props, None, POP_FIELDS)
        session.merge(
            Ward(
                ward_id=f"{source}:{raw_id}",
                name=str(_pick(props, name_field, NAME_FIELDS) or f"Ward {raw_id}"),
                geometry=geom,
                population=int(pop) if pop not in (None, "") else None,
                min_lon=min_lon,
                min_lat=min_lat,
                max_lon=max_lon,
                max_lat=max_lat,
            )
        )
        n += 1
    session.flush()
    assign_wards(session)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Load ward polygons")
    ap.add_argument("--source", default="bbmp", help="ward id prefix, e.g. bbmp")
    ap.add_argument("--file", type=Path, required=True, help="GeoJSON FeatureCollection")
    ap.add_argument("--id-field")
    ap.add_argument("--name-field")
    args = ap.parse_args()
    fc = json.loads(args.file.read_text(encoding="utf-8"))
    with db_session.session_scope() as s:
        n = load(s, fc, args.source, args.id_field, args.name_field)
    print(f"{n} wards loaded")


if __name__ == "__main__":
    main()
