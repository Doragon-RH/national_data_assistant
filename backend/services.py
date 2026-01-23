# backend/services.py
# Processing functions and data access for Tokyo OSM queries.

import json
import math
import re
import time
from math import asin, cos, radians, sin, sqrt
from typing import Any, Dict, List, Optional

import pathlib
import requests
import yaml

OVERPASS = "https://overpass-api.de/api/interpreter"
JAPAN = 'area["ISO3166-1"="JP"]["boundary"="administrative"]["admin_level"="2"];(._;)->.searchArea;'
TOKYO = 'area["name"="東京都"]["boundary"="administrative"]["admin_level"="4"];(._;)->.searchArea;'

CFG_PATH = pathlib.Path("config/taxonomy.yaml")
CATEGORY_MAP: dict[str, list[tuple[str, str]]] = {}
BRAND_PATTERNS: dict[str, str] = {}
_CFG_MTIME = 0.0

STORE: Dict[str, Dict[str, Any]] = {}  # {store_id: {"layers":{cat:[rows]}, "meta":{...}}}


def _new_store_id() -> str:
    return str(int(time.time() * 1000))


def _store_layers(layers: Dict[str, List[dict]], args: dict, *, meta_extra: Optional[dict] = None) -> str:
    meta = {"created_at": time.time(), "args": args}
    if meta_extra:
        meta.update(meta_extra)
    store_id = _new_store_id()
    STORE[store_id] = {"layers": layers, "meta": meta}
    return store_id


def _merge_union_layers(layers: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    union_rows: List[dict] = []
    for rows in layers.values():
        union_rows.extend(rows)
    if not union_rows:
        return {"union": []}

    seen = set()
    uniq = []
    for r in union_rows:
        key = (round(r["lat"], 6), round(r["lon"], 6), r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return {"union": uniq}


def load_taxonomy(force: bool = False):
    """taxonomy.yaml を読み込んでグローバル辞書を更新。ファイル変更時のみ再読込。"""
    global _CFG_MTIME, CATEGORY_MAP, BRAND_PATTERNS
    if not CFG_PATH.exists():
        raise RuntimeError(f"taxonomy file not found: {CFG_PATH}")
    mtime = CFG_PATH.stat().st_mtime
    if force or mtime > _CFG_MTIME:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            y = yaml.safe_load(f) or {}
        cats = y.get("categories", {}) or {}
        brands = y.get("brands", {}) or {}
        CATEGORY_MAP = {k: [tuple(p) for p in v] for k, v in cats.items()}
        BRAND_PATTERNS = brands
        _CFG_MTIME = mtime


def geocode(place: str):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1, "countrycodes": "jp"},
        headers={"User-Agent": "custom-map-api/1.0"},
        timeout=20,
    )
    r.raise_for_status()
    arr = r.json()
    if not arr:
        return None
    return {"lat": float(arr[0]["lat"]), "lon": float(arr[0]["lon"])}


def _brand_rx(b: Optional[str]):
    if not b:
        return None
    if b in BRAND_PATTERNS:
        return BRAND_PATTERNS[b]
    import unicodedata

    s = unicodedata.normalize("NFKC", b)
    s = re.sub(r"\s+", r"\\s*", s)
    s = s.replace("ー", "-").replace("−", "-")
    return rf"(?i){s}"


def _tag_filters(tags):
    return "".join([f'["{k}"="{v}"]' for k, v in tags])


def _tokyo():
    return "(area.searchArea)"


def _around(lat, lon, m):
    return f"(around:{m},{lat},{lon})"


def _post_overpass(query: str):
    last_exc = None
    for attempt in range(3):
        try:
            r = requests.post(OVERPASS, data={"data": query}, timeout=45)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            last_exc = e
            status = getattr(e.response, "status_code", None)
            if status in (429, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except requests.RequestException as e:
            last_exc = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc


def query_osm_tokyo(tags, *, brand=None, open_24h=False, wheelchair=False,
                    center=None, radius_km=None, limit=None):
    rx = _brand_rx(brand)
    brand_f = f'["brand"~"{rx}"]' if rx else ""
    name_f = f'["name"~"{rx}"]' if rx else ""
    oper_f = f'["operator"~"{rx}"]' if rx else ""
    brand_or = brand_f or name_f or oper_f

    extra = ""
    if open_24h:
        extra += '["opening_hours"~"24/?7"]'
    if wheelchair:
        extra += '["wheelchair"~"yes|limited"]'

    where_geo = _around(center["lat"], center["lon"], int(radius_km * 1000)) if (center and radius_km) else ""
    filt = _tag_filters(tags) + extra

    out_n = int(limit or 300)
    if out_n <= 0:
        out_n = 300

    q = f"""
    [out:json][timeout:30];
    {TOKYO}
    (
      node{filt}{brand_or}{_tokyo()}{where_geo};
    );
    out body {out_n};
    """

    r = _post_overpass(q)

    rows = []
    for e in r.json().get("elements", []):
        lat = e.get("lat")
        lon = e.get("lon")
        if lat is None or lon is None:
            continue
        t = e.get("tags", {}) or {}
        rows.append(
            {
                "lat": float(lat),
                "lon": float(lon),
                "name": t.get("name"),
                "brand": t.get("brand"),
            }
        )
    return rows


def apply_range_defaults(args: dict, *, mode: str) -> dict:
    r = (args.get("range") or "standard").strip()
    if r not in ("narrow", "standard", "wide"):
        r = "standard"

    if mode == "search":
        radius_default = {"narrow": 1.5, "standard": 3.0, "wide": 8.0}[r]
        limit_default = {"narrow": 120, "standard": 200, "wide": 300}[r]
        if args.get("radius_km") is None:
            args["radius_km"] = radius_default
        if args.get("limit") is None:
            args["limit"] = limit_default
    elif mode == "trip":
        radius_default = {"narrow": 2.5, "standard": 5.0, "wide": 12.0}[r]
        limit_default = {"narrow": 200, "standard": 300, "wide": 500}[r]
        per_day_default = {"narrow": 5, "standard": 6, "wide": 7}[r]
        if args.get("radius_km") is None:
            args["radius_km"] = radius_default
        if args.get("limit") is None:
            args["limit"] = limit_default
        if args.get("per_day") is None:
            args["per_day"] = per_day_default

    return args


def revise_search_args(args: dict, *, attempt: int) -> dict:
    new_args = dict(args)
    if new_args.get("radius_km") is not None:
        new_args["radius_km"] = min(float(new_args["radius_km"]) * 1.8, 15.0)
    else:
        new_args["radius_km"] = 3.0

    if new_args.get("limit") is not None:
        new_args["limit"] = min(int(new_args["limit"]) * 2, 500)
    else:
        new_args["limit"] = 300

    if attempt >= 2:
        if new_args.get("open_24h"):
            new_args["open_24h"] = False
        if new_args.get("wheelchair"):
            new_args["wheelchair"] = False

    return new_args


def revise_trip_args(args: dict, *, attempt: int) -> dict:
    new_args = dict(args)
    if new_args.get("radius_km") is not None:
        new_args["radius_km"] = min(float(new_args["radius_km"]) * 1.8, 20.0)
    else:
        new_args["radius_km"] = 5.0

    if new_args.get("limit") is not None:
        new_args["limit"] = min(int(new_args["limit"]) * 2, 800)
    else:
        new_args["limit"] = 500

    if attempt >= 2:
        if new_args.get("open_24h"):
            new_args["open_24h"] = False
        if new_args.get("wheelchair"):
            new_args["wheelchair"] = False
        if new_args.get("per_day") is not None:
            new_args["per_day"] = max(3, int(int(new_args["per_day"]) * 0.8))

    return new_args


def run_search_tool(args: dict):
    cats = args.get("categories", [])
    brand = args.get("brand")
    place = args.get("place")
    radius_km = args.get("radius_km")
    open_24h = bool(args.get("open_24h", False))
    wheelchair = bool(args.get("wheelchair", False))
    limit = int(args.get("limit") or 300)
    union = bool(args.get("union", True))

    center = geocode(place) if place else None
    layers: Dict[str, list] = {}
    union_rows: List[dict] = []

    for c in cats:
        if c not in CATEGORY_MAP:
            print(f"[warn] unknown category: {c}")
            continue
        tags = CATEGORY_MAP[c]
        rows = query_osm_tokyo(
            tags,
            brand=brand,
            open_24h=open_24h,
            wheelchair=wheelchair,
            center=center,
            radius_km=radius_km,
            limit=limit,
        )
        for r in rows:
            r.setdefault("layer", c)
        layers[c] = rows
        if union:
            union_rows.extend(rows)

    if union and union_rows:
        seen = set()
        uniq = []
        for r in union_rows:
            key = (round(r["lat"], 6), round(r["lon"], 6), r.get("name"))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        layers = {"union": uniq}

    store_id = _store_layers(layers, args)
    stats = {k: len(v) for k, v in layers.items()}
    return {"store_id": store_id, "stats": stats, "args": args, "result_type": "search"}


def run_search_category_tool(args: dict):
    category = args.get("category")
    if category not in CATEGORY_MAP:
        raise ValueError(f"unknown category: {category}")

    base_args = {
        "categories": [category],
        "brand": args.get("brand"),
        "place": args.get("place"),
        "radius_km": args.get("radius_km"),
        "open_24h": bool(args.get("open_24h", False)),
        "wheelchair": bool(args.get("wheelchair", False)),
        "limit": args.get("limit"),
        "union": False,
        "range": args.get("range"),
    }
    base_args = apply_range_defaults(base_args, mode="search")

    center = geocode(base_args.get("place")) if base_args.get("place") else None
    rows = query_osm_tokyo(
        CATEGORY_MAP[category],
        brand=base_args.get("brand"),
        open_24h=bool(base_args.get("open_24h", False)),
        wheelchair=bool(base_args.get("wheelchair", False)),
        center=center,
        radius_km=base_args.get("radius_km"),
        limit=int(base_args.get("limit") or 300),
    )
    for r in rows:
        r.setdefault("layer", category)

    layers = {category: rows}
    store_id = _store_layers(layers, base_args, meta_extra={"type": "category_search"})
    stats = {category: len(rows)}
    return {"store_id": store_id, "stats": stats, "args": base_args, "result_type": "category"}


def merge_search_results_tool(args: dict):
    store_ids = args.get("store_ids") or []
    if not store_ids:
        raise ValueError("store_ids is required")

    layers: Dict[str, List[dict]] = {}
    merged_categories: List[str] = []
    merged_args: Dict[str, Any] = {}

    for idx, store_id in enumerate(store_ids):
        data = STORE.get(store_id)
        if not data:
            raise ValueError(f"store_id not found: {store_id}")
        for layer, rows in data.get("layers", {}).items():
            layers.setdefault(layer, []).extend(rows)

        meta_args = (data.get("meta") or {}).get("args") or {}
        if idx == 0:
            merged_args.update(meta_args)
        for c in meta_args.get("categories") or []:
            if c not in merged_categories:
                merged_categories.append(c)

    union = bool(args.get("union", True))
    merged_args["categories"] = merged_categories
    merged_args["union"] = union

    if union:
        layers = _merge_union_layers(layers)

    store_id = _store_layers(layers, merged_args, meta_extra={"type": "merged_search", "source_store_ids": store_ids})
    stats = {k: len(v) for k, v in layers.items()}
    return {"store_id": store_id, "stats": stats, "args": merged_args, "result_type": "search"}


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def _collect_trip_candidates(args: dict):
    place = args.get("place")
    days = max(1, min(int(args.get("days", 1)), 14))
    radius_km = float(args.get("radius_km", 3))
    interests = [c for c in (args.get("interests") or []) if c in CATEGORY_MAP]
    per_day = max(1, min(int(args.get("per_day", 6)), 12))
    open_24h = bool(args.get("open_24h", False))
    wheelchair = bool(args.get("wheelchair", False))
    limit = int(args.get("limit", 300))

    center = geocode(place)
    if not center:
        raise ValueError(f"geocode failed: {place}")

    buckets: Dict[str, List[dict]] = {}
    for c in (interests or []):
        rows = query_osm_tokyo(
            CATEGORY_MAP[c],
            center=center,
            radius_km=radius_km,
            open_24h=open_24h,
            wheelchair=wheelchair,
            limit=limit,
        )
        for r in rows:
            r.setdefault("layer", c)
            r["dist_km"] = _haversine(center["lat"], center["lon"], r["lat"], r["lon"])
        rows.sort(key=lambda x: (x["dist_km"], x.get("name") or ""))
        take = min(len(rows), max(30, per_day * days * 2 // max(1, len(interests))))
        buckets[c] = rows[:take]

    return center, buckets


def _build_itinerary_from_buckets(place: str, center: dict, buckets: Dict[str, List[dict]], *, days: int, per_day: int, pace: str):
    itinerary = []
    idx_by_cat = {k: 0 for k in buckets.keys()}
    cats = list(buckets.keys())
    cat_cursor = 0

    for d in range(days):
        items = []

        for _ in range(per_day):
            if not cats:
                break

            tried = 0
            picked = False
            while tried < len(cats):
                cat = cats[(cat_cursor + tried) % len(cats)]
                idx = idx_by_cat.get(cat, 0)

                if idx < len(buckets[cat]):
                    items.append(buckets[cat][idx])
                    idx_by_cat[cat] = idx + 1
                    cat_cursor = (cats.index(cat) + 1) % len(cats)
                    picked = True
                    break

                tried += 1

            if not picked:
                break

        start_h = {"relaxed": 10, "standard": 9, "packed": 8}[pace]
        slot = []
        for j, r in enumerate(items):
            hour = start_h + j * int(8 / per_day + 1)
            slot.append(
                {
                    "time": f"{hour:02d}:00",
                    "name": r.get("name") or f"{r['layer'].title()}",
                    "category": r["layer"],
                    "lat": r["lat"],
                    "lon": r["lon"],
                    "brand": r.get("brand"),
                    "distance_km_from_center": round(r["dist_km"], 3),
                }
            )

        itinerary.append({"day": d + 1, "place": place, "center": center, "items": slot})

    union_rows = []
    for day in itinerary:
        for it in day["items"]:
            union_rows.append(
                {
                    "lat": it["lat"],
                    "lon": it["lon"],
                    "name": it["name"],
                    "brand": it.get("brand"),
                    "layer": it["category"],
                }
            )

    return itinerary, union_rows


def collect_trip_candidates_tool(args: dict):
    args = apply_range_defaults(args, mode="trip")
    place = args.get("place")
    center, buckets = _collect_trip_candidates(args)

    store_id = _store_layers(
        buckets,
        args,
        meta_extra={"type": "trip_candidates", "center": center, "place": place},
    )
    stats = {k: len(v) for k, v in buckets.items()}
    return {"candidate_id": store_id, "stats": stats, "args": args, "result_type": "candidate"}


def build_trip_itinerary_tool(args: dict):
    candidate_id = args.get("candidate_id")
    if not candidate_id:
        raise ValueError("candidate_id is required")

    data = STORE.get(candidate_id)
    if not data:
        raise ValueError(f"candidate_id not found: {candidate_id}")

    meta = data.get("meta") or {}
    meta_args = meta.get("args") or {}
    place = args.get("place") or meta.get("place") or meta_args.get("place")
    center = meta.get("center")
    if not center and place:
        center = geocode(place)
    if not center:
        raise ValueError("center not found for itinerary")

    days = max(1, min(int(args.get("days") or meta_args.get("days") or 1), 14))
    per_day = max(1, min(int(args.get("per_day") or meta_args.get("per_day") or 6), 12))
    pace = args.get("pace") or meta_args.get("pace") or "standard"

    itinerary, union_rows = _build_itinerary_from_buckets(
        place,
        center,
        data.get("layers") or {},
        days=days,
        per_day=per_day,
        pace=pace,
    )

    args_out = dict(meta_args)
    args_out.update({"place": place, "days": days, "per_day": per_day, "pace": pace})

    store_id = _store_layers(
        {"union": union_rows},
        args_out,
        meta_extra={"type": "trip", "source_candidate_id": candidate_id},
    )
    stats = {"days": days, "per_day": per_day, "spots": len(union_rows)}
    return {
        "store_id": store_id,
        "itinerary": itinerary,
        "stats": stats,
        "args": args_out,
        "result_type": "trip",
    }


def plan_trip_tokyo_impl(args: dict):
    place = args.get("place")
    days = max(1, min(int(args.get("days", 1)), 14))
    per_day = max(1, min(int(args.get("per_day", 6)), 12))
    pace = args.get("pace", "standard")

    center, buckets = _collect_trip_candidates(args)
    itinerary, union_rows = _build_itinerary_from_buckets(
        place,
        center,
        buckets,
        days=days,
        per_day=per_day,
        pace=pace,
    )

    store_id = _store_layers(
        {"union": union_rows},
        args,
        meta_extra={"type": "trip"},
    )

    stats = {"days": days, "per_day": per_day, "spots": len(union_rows)}
    return {"store_id": store_id, "itinerary": itinerary, "stats": stats, "args": args, "result_type": "trip"}
