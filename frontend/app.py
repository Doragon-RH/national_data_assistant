# frontend/app.py
# pip install streamlit pandas requests pydeck

import os
import requests
import pandas as pd
import pydeck as pdk
import streamlit as st

# ---------------------
# 共通設定
# ---------------------
def get_backend_url():
    # 1) 環境変数 BACKEND_URL
    env = os.getenv("BACKEND_URL")
    if env:
        return env
    # 2) secrets（あれば）
    try:
        return st.secrets["BACKEND_URL"]
    except Exception:
        pass
    # 3) デフォルト
    return "http://localhost:8000"


API = get_backend_url()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "240"))

st.set_page_config(page_title="Tokyo Custom Map", layout="wide")

# ---------------------
# CSS（配色を固定：サイドバー=水色, 本体=白, 文字=黒）
# ---------------------
# ---------------------
# CSS（配色を固定：サイドバー=水色, 本体=白, 文字=黒）
# ---------------------
st.markdown(
    """
    <style>
    /* =========================
       ベース（ダークモード無視）
       ========================= */
    html, body, .stApp {
        background-color: #ffffff !important;
        color: #000000 !important;
    }

    /* =========================
       ヘッダー/ツールバー（ダークモード対策）
       ========================= */
    header[data-testid="stHeader"],
    .stApp > header {
        background-color: #ffffff !important;
    }
    header[data-testid="stHeader"] *,
    .stApp > header * {
        color: #000000 !important;
    }
    [data-testid="stToolbar"] {
        background-color: #ffffff !important;
    }
    [data-testid="stToolbar"] * {
        color: #000000 !important;
    }
    [data-testid="stDecoration"] {
        background-color: #ffffff !important;
    }

    /* =========================
       サイドバー
       ========================= */
    [data-testid="stSidebar"] > div:first-child {
        background-color: #e0f4ff !important;
    }

    /* サイドバー内テキストは黒固定 */
    [data-testid="stSidebar"] * {
        color: #000000 !important;
    }

    /* =========================
       code 表示（Backend URL 等）
       ========================= */
    code, .small-caption code {
        background-color: #f5f5f5 !important;  /* 明るいグレー */
        color: #000000 !important;             /* 黒文字 */
        border-radius: 4px;
        padding: 0.1rem 0.3rem;
    }

    /* 小さめキャプション */
    .small-caption {
        font-size: 0.8rem;
        color: #333333;
    }

    /* ステータス用カード */
    .stat-card {
        padding: 0.6rem 0.8rem;
        border-radius: 0.6rem;
        border: 1px solid rgba(0,0,0,0.08);
        background-color: #ffffff;
        color: #000000;
    }

    /* 凡例バッジ */
    .legend-badge {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        border-radius: 999px;
        margin-right: 0.3rem;
        margin-bottom: 0.2rem;
        font-size: 0.8rem;
        border: 1px solid rgba(0,0,0,0.08);
        background-color: #e0f4ff;
        color: #000000;
    }

    /* =========================
       入力（text_area / text_input）
       ========================= */
    [data-testid="stTextArea"] textarea,
    [data-testid="stTextInput"] input {
        background-color: #ffffff !important;
        color: #000000 !important;
        border: 1px solid rgba(0,0,0,0.18) !important;
        outline: none !important;
        box-shadow: none !important;
        caret-color: #000000 !important;
    }

    /* プレースホルダ文字（薄すぎ問題の対策） */
    [data-testid="stTextArea"] textarea::placeholder,
    [data-testid="stTextInput"] input::placeholder {
        color: rgba(0,0,0,0.55) !important;
        opacity: 1 !important;
    }

    /* フォーカス時の視認性を上げる */
    [data-testid="stTextArea"] textarea:focus,
    [data-testid="stTextInput"] input:focus {
        border-color: #2a7de1 !important;
        box-shadow: 0 0 0 3px rgba(42, 125, 225, 0.2) !important;
    }

    /* =========================
       ボタン（実行）
       ========================= */
    [data-testid="stButton"] button {
        color: #000000 !important;              /* ボタン文字を黒に固定 */
        background-color: #ffffff !important;
        border: 1px solid rgba(0,0,0,0.20) !important;
    }
    [data-testid="stButton"] button:hover {
        border-color: rgba(0,0,0,0.35) !important;
    }

    /* =========================
       st.info / st.warning / st.error の本文が見えない対策
       ========================= */
    [data-testid="stAlert"] {
        color: #000000 !important;
    }
    [data-testid="stAlert"] * {
        color: #000000 !important;
    }

    /* =========================
       タブ（マップ/旅行プラン/生データ）
       ========================= */
    [data-testid="stTabs"] button {
        color: #000000 !important;
    }
    [data-testid="stTabs"] button[aria-selected="true"] {
        color: #000000 !important;
        font-weight: 700;
    }
    [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
        background-color: #2a7de1 !important;
    }
    [data-testid="stTabs"] [data-baseweb="tab-border"] {
        background-color: rgba(0,0,0,0.12) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.title("東京カスタムマップ（API連携）")
st.markdown(
    "<p class='small-caption'>自然言語でスポット検索や旅行プランを作成し、東京の地図上に表示するアプリです。</p>",
    unsafe_allow_html=True,
)

# ---------------------
# サイドバー：モード切り替え
# ---------------------
with st.sidebar:
    st.markdown("### 設定")

    mode = st.radio("モード", ["スポット検索", "旅行計画"], index=0)

    if mode == "スポット検索":
        st.caption("例:『渋谷駅近くのカフェ。24時間がいいけど、厳しすぎるなら外してもいい。』")
        placeholder = "上野駅周辺の公園"
    else:
        st.caption("例:『上野駅を拠点に1日か2日くらい。公園を中心に回りたい。』")
        placeholder = "上野駅を拠点に1日か2日くらい。公園を中心に回りたい。"

    text = st.text_area("自然言語でリクエスト", placeholder, height=90)
    run_col1, run_col2 = st.columns([1, 1.2])
    with run_col1:
        run = st.button("実行")
    with run_col2:
        st.write("")  # 余白
        st.markdown(
            f"<span class='small-caption'>Backend: <code>{API}</code></span>",
            unsafe_allow_html=True,
        )

# カラー設定（layer名 → 色）
color_cycle = {
    "convenience": [200, 30, 30],
    "cafe":        [30, 120, 200],
    "park":        [30, 160, 80],
    "restaurant":  [180, 120, 40],
    "pharmacy":    [120, 60, 20],
    "hospital":    [160, 40, 160],
    "station":     [0, 0, 0],
    "attraction":  [200, 100, 0],
    "hotel":       [200, 120, 200],
    "union":       [100, 100, 255],  # 複数カテゴリ結合用レイヤ
}

# ---------------------
# 初期表示（まだ実行してないとき）
# ---------------------
if not run:
    st.info(
        "左のサイドバーからモードを選び、自然言語で条件を入力して「実行」を押すと結果が表示されます。"
    )
    st.markdown(
        """
        - **スポット検索**：コンビニ・カフェ・公園などのスポットを一括検索して地図表示  
        - **旅行計画**：拠点・日数・興味のあるカテゴリから、1日のタイムライン付きプランを自動生成  
        """
    )

# ---------------------
# メイン処理
# ---------------------
if run and text.strip():
    try:
        with st.spinner("APIに問い合わせ中..."):
            if mode == "スポット検索":
                endpoint = f"{API}/v1/map/query"
            else:
                endpoint = f"{API}/v1/trip/plan"

            resp = requests.post(endpoint, json={"text": text}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            q = resp.json()

        # ---- レビュー要求がある場合 ----
        if q.get("require_review"):
            st.warning("結果の妥当性確認が必要です。レビューを入力してください。")
            if q.get("review_reason"):
                st.info(f"理由: {q.get('review_reason')}")

            review_text = st.text_area("レビュー内容", "例: 遠すぎる場所を除外して、近場中心で作り直してください。", height=90)
            review_submit = st.button("レビューを送信")

            if review_submit:
                if not review_text.strip():
                    st.warning("レビュー内容を入力してください。")
                    st.stop()
                with st.spinner("レビューを反映中..."):
                    resp = requests.post(
                        f"{API}/v1/review/continue",
                        json={"review_text": review_text, "context": q.get("review_context", {})},
                        timeout=REQUEST_TIMEOUT,
                    )
                    resp.raise_for_status()
                    q = resp.json()
            else:
                st.stop()

            if q.get("require_review"):
                st.warning("レビュー後も妥当性が確認できませんでした。内容を調整して再送してください。")
                st.stop()

        # ---- 上部：サマリー & ステータス ----
        top_left, top_right = st.columns([2, 1])

        with top_left:
            st.subheader("サマリー")
            st.write(q.get("summary", ""))

        with top_right:
            st.subheader("ステータス")
            stats = q.get("stats")
            if isinstance(stats, dict) and stats:
                for k, v in stats.items():
                    st.markdown(
                        f"<div class='stat-card'><b>{k}</b><br>{v}</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<div class='stat-card'>ステータスはありません。</div>",
                    unsafe_allow_html=True,
                )

        store_id = q.get("store_id")
        itinerary = q.get("itinerary")  # 旅行計画モードのときだけ入っている想定

        # ---- タブレイアウト（マップ / 旅行プラン / 生データ）----
        tabs = st.tabs(["マップ", "旅行プラン", "生データ"])

        # ---- タブ1: マップ ----
        with tabs[0]:
            if not store_id:
                st.info("マップを表示する store_id がありません。クエリを見直してください。")
            else:
                with st.spinner("地図データ取得中..."):
                    geo = requests.get(f"{API}/v1/map/{store_id}/geojson", timeout=REQUEST_TIMEOUT)
                    geo.raise_for_status()
                    geojson = geo.json()

                feats = geojson.get("features", [])
                if not feats:
                    st.info("該当スポットが見つかりませんでした。条件を調整してください。")
                else:
                    rows = []
                    for f in feats:
                        lon, lat = f["geometry"]["coordinates"]
                        p = f.get("properties", {})
                        rows.append(
                            {
                                "lat": lat,
                                "lon": lon,
                                "layer": p.get("layer", "unknown"),
                                "name": p.get("name"),
                                "brand": p.get("brand"),
                            }
                        )
                    df = pd.DataFrame(rows)

                    # カテゴリフィルタ
                    all_layers = sorted(df["layer"].unique().tolist())
                    st.markdown("#### フィルタ")
                    selected_layers = st.multiselect(
                        "表示するカテゴリ",
                        all_layers,
                        default=all_layers,
                    )
                    mask = df["layer"].isin(selected_layers)
                    df_view = df[mask].copy()

                    if df_view.empty:
                        st.warning("選択されたカテゴリにはスポットがありません。")
                    else:
                        # 凡例
                        st.markdown("##### 凡例")
                        legend_html = ""
                        for layer_name in selected_layers:
                            emoji = ""
                            if "cafe" in layer_name:
                                emoji = ""
                            elif "park" in layer_name:
                                emoji = ""
                            elif "convenience" in layer_name:
                                emoji = ""
                            elif "station" in layer_name:
                                emoji = ""
                            elif "attraction" in layer_name:
                                emoji = ""
                            legend_html += f"<span class='legend-badge'>{layer_name}</span>"
                        st.markdown(legend_html, unsafe_allow_html=True)

                        # マップ描画
                        layers = []
                        for layer_name, g in df_view.groupby("layer"):
                            layers.append(
                                pdk.Layer(
                                    "ScatterplotLayer",
                                    g,
                                    get_position="[lon, lat]",
                                    get_radius=30,
                                    pickable=True,
                                    get_fill_color=color_cycle.get(
                                        layer_name, [100, 100, 100]
                                    ),
                                )
                            )

                        view = pdk.ViewState(
                            latitude=float(df_view["lat"].mean()),
                            longitude=float(df_view["lon"].mean()),
                            zoom=12,
                        )
                        st.pydeck_chart(
                            pdk.Deck(
                                layers=layers,
                                initial_view_state=view,
                                tooltip={"text": "{layer}\n{name}\n{brand}"},
                            )
                        )

        # ---- タブ2: 旅行プラン ----
        with tabs[1]:
            if itinerary:
                st.subheader("旅行プラン")
                for day in itinerary:
                    day_no = day.get("day")
                    place = day.get("place")
                    st.markdown(f"### Day {day_no} - {place}")

                    for item in day.get("items", []):
                        time_txt = item.get("time", "")
                        name = item.get("name", "")
                        cat = item.get("category", "")
                        dist = item.get("distance_km_from_center")
                        dist_txt = f" / 中心から{dist}km" if dist is not None else ""
                        st.markdown(
                            f"- ⏰ {time_txt} ｜ **{name}** （{cat}{dist_txt}）"
                        )
            else:
                st.info("旅行計画モードで実行すると、ここに行程が表示されます。")

        # ---- タブ3: 生データ ----
        with tabs[2]:
            st.subheader("APIレスポンス（JSON）")
            st.json(q)

            if store_id:
                st.markdown("---")
                st.subheader("GeoJSON（概要）")
                st.json(
                    {
                        "meta": geojson.get("meta", {}),
                        "features_count": len(geojson.get("features", [])),
                    }
                )

    except requests.exceptions.RequestException as e:
        st.error(f"HTTPエラーが発生しました: {e}")
    except Exception as e:
        st.error(f"予期しないエラーが発生しました: {e}")
