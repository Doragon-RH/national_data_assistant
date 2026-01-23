# -*- coding: utf-8 -*-
# pip install streamlit pandas requests pydeck python-dotenv openai==1.*

import os, re, json, math, time, requests
import numpy as np, pandas as pd, pydeck as pdk, streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

# =========================
# 環境変数・クライアント
# =========================
if os.path.exists(".env.local"):
    load_dotenv(".env.local")
else:
    load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or st.secrets.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が未設定です（.env / .env.local / Streamlit secrets を確認）")

client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# 設定
# =========================
APP_TITLE = "🗺️ 東京カスタムマップ（AIエージェント）"
DEFAULT_CENTER = {"lat": 35.6809591, "lon": 139.7673068}  # 東京駅付近
OVERPASS = "https://overpass-api.de/api/interpreter"

# 東京都の Overpass area を名前で特定（毎回クエリせず、定義検索をクエリに埋め込む）
# area[name="東京都"]["boundary"="administrative"]["admin_level"="4"] → .searchArea;
TOKYO_AREA_SNIPPET = 'area["name"="東京都"]["boundary"="administrative"]["admin_level"="4"];(._;)->.searchArea;'

# カテゴリ→OSMタグのマッピング（最低限 / 追加はここへ）
CATEGORY_MAP = {
    "convenience":  [('shop', 'convenience')],                 # コンビニ
    "cafe":         [('amenity', 'cafe')],
    "restaurant":   [('amenity', 'restaurant')],
    "park":         [('leisure', 'park')],
    "hospital":     [('amenity', 'hospital')],
    "clinic":       [('amenity', 'clinic')],
    "pharmacy":     [('amenity', 'pharmacy')],
    "school":       [('amenity', 'school')],
    "kindergarten": [('amenity', 'kindergarten')],
    "library":      [('amenity', 'library')],
    "station":      [('railway', 'station')],
    "attraction":   [('tourism', 'attraction')],
}

# ブランドゆらぎ（例示）
BRAND_PATTERNS = {
    "FamilyMart": r"(?i)(Family\s?Mart|ファミリーマート)",
    "7-Eleven":   r"(?i)(7[-\s]?Eleven|セブン[ー\-]?イレブン)",
    "Lawson":     r"(?i)(Lawson|ローソン)",
}

# =========================
# ユーティリティ
# =========================
@st.cache_data(show_spinner=False, ttl=3600)
def geocode(place: str):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1, "countrycodes": "jp"},
        headers={"User-Agent": "tokyo-custom-map/1.0"},
        timeout=20,
    )
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    return {"lat": float(items[0]["lat"]), "lon": float(items[0]["lon"])}

def make_bbox(lat: float, lon: float, radius_km: float):
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(0.01, abs(math.cos(math.radians(lat)))))
    return {"south": lat - dlat, "west": lon - dlon, "north": lat + dlat, "east": lon + dlon}

def _build_tag_filters(tags):
    # [('amenity','cafe'), ('shop','convenience')] -> Overpass用フィルタ文字列
    return "".join([f'["{k}"="{v}"]' for k, v in tags])

def _brand_regex(brand: str):
    if not brand:
        return None
    if brand in BRAND_PATTERNS:
        return BRAND_PATTERNS[brand]
    # 入力そのままにも対応（半角/全角スペースをゆるく）
    b = re.sub(r"\s+", r"\\s*", brand)
    return rf"(?i){b}"

def _opening_filter(open_24h: bool):
    return '["opening_hours"~"24/?7"]' if open_24h else ""

def _wheelchair_filter(needed: bool):
    return '["wheelchair"~"yes|limited"]' if needed else ""

def _limit_clause(limit: int | None):
    return f"->.all; (.all;)->.all; out center {limit};" if limit and limit > 0 else "out center;"

def _overpass_area_query():
    # 東京都のエリアを .searchArea に束ねる
    return TOKYO_AREA_SNIPPET

def _within_area_clause():
    return "(area.searchArea)"

def _around_clause(lat: float, lon: float, radius_m: int):
    return f"(around:{radius_m},{lat},{lon})"

@st.cache_data(show_spinner=False, ttl=120)
def query_overpass_tokyo(tags, brand=None, open_24h=False, wheelchair=False,
                         center=None, radius_km=None, limit=None):
    """
    東京都内限定で OSM を検索。必要なら地点中心+半径でも絞り込み。
    """
    brand_rx = _brand_regex(brand)
    brand_f = f'["brand"~"{brand_rx}"]' if brand_rx else ""
    name_f  = f'["name"~"{brand_rx}"]' if brand_rx else ""
    oper_f  = f'["operator"~"{brand_rx}"]' if brand_rx else ""
    extra   = _opening_filter(open_24h) + _wheelchair_filter(wheelchair)

    where_area = _within_area_clause()
    where_geo  = ""
    if center and radius_km:
        where_geo = _around_clause(center["lat"], center["lon"], int(radius_km * 1000))

    filt = _build_tag_filters(tags) + extra
    brand_or = brand_f or name_f or oper_f

    # node/way/relation すべてを対象にし、中心座標を out center で取得
    q = f"""
    [out:json][timeout:30];
    {_overpass_area_query()}
    (
      node{filt}{brand_or}{where_area}{where_geo};
      way {filt}{brand_or}{where_area}{where_geo};
      rel {filt}{brand_or}{where_area}{where_geo};
    );
    out center {limit if limit else ''};
    """

    r = requests.post(OVERPASS, data={"data": q}, timeout=45)
    r.raise_for_status()
    elements = r.json().get("elements", [])
    rows = []
    for e in elements:
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        t = e.get("tags", {})
        rows.append({
            "lat": float(lat),
            "lon": float(lon),
            "name": t.get("name"),
            "brand": t.get("brand"),
            "category": ",".join([f"{k}={v}" for k, v in tags]),
            "raw_tags": t,
        })
    return rows

# =========================
# Tool（関数呼び出し）定義
# =========================
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_osm_tokyo",
            "description": "自然言語の意図を東京都内のOSM検索に変換して実行する。カテゴリは複数指定可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(CATEGORY_MAP.keys())},
                        "description": "検索するカテゴリ（例: convenience, cafe, park など）"
                    },
                    "brand": {"type": ["string", "null"], "description": "ブランド名（例: FamilyMart, Lawson）"},
                    "place": {"type": ["string", "null"], "description": "基準地点の地名（例: 渋谷駅）。省略時は東京都全域"},
                    "radius_km": {"type": ["number", "null"], "description": "place 周辺の半径（km）。省略時は全域"},
                    "open_24h": {"type": "boolean", "default": False},
                    "wheelchair": {"type": "boolean", "default": False},
                    "limit": {"type": ["integer", "null"], "description": "最大件数ヒント"},
                },
                "required": ["categories"]
            },
        },
    }
]

def call_tool(name, arguments):
    if name != "search_osm_tokyo":
        raise ValueError(f"unknown tool {name}")
    cats = arguments.get("categories", [])
    brand = arguments.get("brand")
    place = arguments.get("place")
    radius_km = arguments.get("radius_km")
    open_24h = bool(arguments.get("open_24h", False))
    wheelchair = bool(arguments.get("wheelchair", False))
    limit = arguments.get("limit")

    center = None
    if place:
        center = geocode(place) or DEFAULT_CENTER

    results = {}
    for cat in cats:
        tags = CATEGORY_MAP.get(cat)
        if not tags:
            continue
        rows = query_overpass_tokyo(tags, brand=brand, open_24h=open_24h,
                                    wheelchair=wheelchair, center=center,
                                    radius_km=radius_km, limit=limit)
        results[cat] = rows
    return results

# =========================
# LLMオーケストレーター
# =========================
SYSTEM = (
    "あなたは東京都限定の地理エージェントです。"
    "ユーザーの自然言語指示から、対象カテゴリ（convenience, cafe, park など）、"
    "必要ならブランド（例: FamilyMart）、基準地点(place)と半径(radius_km)、"
    "24時間営業(open_24h)、車椅子対応(wheelchair)、件数上限(limit)を抽出し、"
    "search_osm_tokyo を1回以上呼び出して結果を得てください。"
    "カテゴリ名は必ず定義済みのenumから選びます。"
    "曖昧なときは妥当な仮定を置きます（例: place未指定→東京都全域）。"
)

def run_agent(user_text: str):
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_text},
    ]
    tool_results = None
    summary = ""

    for _ in range(4):  # 連鎖最大4ターン
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append({"role": "assistant", "content": None, "tool_calls": msg.tool_calls})
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = call_tool(tc.function.name, args)
                tool_results = result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue
        # ツール呼び出しがない＝最終テキスト
        summary = msg.content or ""
        break

    return {"summary": summary, "results": tool_results or {}}

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.markdown("**例:** 「渋谷駅 半径1.5kmのカフェと公園」「ファミリーマートだけ」「24時間のコンビニ」")
    user_text = st.text_input("自然言語でリクエスト", "渋谷駅 半径1.5kmでカフェとコンビニ（ファミマ）を表示")
    run_btn = st.button("実行")

if run_btn and user_text.strip():
    with st.spinner("AIエージェントが検索条件を抽出 → OSMにクエリ中..."):
        t0 = time.time()
        out = run_agent(user_text)
        dt = time.time() - t0

    st.subheader("🧾 サマリー")
    st.write(out["summary"])
    st.caption(f"所要時間: {dt:.2f}s（東京都内に限定）")

    # 地図レイヤーを作成
    layers = []
    center_lat, center_lon = DEFAULT_CENTER["lat"], DEFAULT_CENTER["lon"]
    color_cycle = {
        "convenience": [200, 30, 30],  # 赤系
        "cafe": [30, 120, 200],        # 青系
        "park": [30, 160, 80],         # 緑系
        "restaurant": [180, 120, 40],
        "hospital": [160, 40, 160],
        "clinic": [160, 40, 160],
        "pharmacy": [120, 60, 20],
        "school": [40, 80, 160],
        "kindergarten": [40, 80, 160],
        "library": [80, 80, 80],
        "station": [0, 0, 0],
        "attraction": [200, 100, 0],
    }

    any_points = False
    for cat, rows in (out["results"] or {}).items():
        df = pd.DataFrame(rows or [])
        if df.empty:
            continue
        any_points = True
        center_lat, center_lon = float(df["lat"].mean()), float(df["lon"].mean())
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            df,
            get_position='[lon, lat]',
            get_radius=30,
            pickable=True,
            get_fill_color=color_cycle.get(cat, [100, 100, 100]),
        ))

    if any_points:
        deck = pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=12),
            tooltip={"text": "{name}\n{brand}\n{category}"},
        )
        st.pydeck_chart(deck)
    else:
        st.info("該当するスポットが見つかりませんでした。条件（カテゴリ/ブランド/半径）を調整してください。")
