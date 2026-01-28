# backend/agent.py
# LLM prompts and evaluation logic.

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from backend import services

load_dotenv(".env.local") or load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set")
client = OpenAI(api_key=OPENAI_API_KEY)

LOG_LLM = os.getenv("LOG_LLM") == "1"


def _log_llm_call(label: str, started_at: float, *, model: str, extra: str = ""):
    if not LOG_LLM:
        return
    elapsed = time.time() - started_at
    tail = f" {extra}" if extra else ""
    print(f"[llm] {label} model={model} elapsed={elapsed:.2f}s{tail}")

SYSTEM = (
    "あなたは東京都限定の地理エージェント。"
    "ユーザーの目的に応じて適切なツールを自律的に選択する。"
    "必要に応じて複数ステップで解決してよい。"
    "カテゴリや場所、条件（半径/24時間/車椅子/件数など）はユーザーの意図に沿って抽出する。"
    "複数カテゴリがある場合は、単一カテゴリ検索の組み合わせや一括検索を使い分ける。"
    "旅行範囲の希望（狭い/標準/広い）が読み取れる場合は range に反映する。"
    "距離表現が曖昧でも許容し、文脈から最適な range を自律的に推定してよい。"
)

MAX_ITERS = 3
MAX_TOOL_STEPS = 6
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
VALIDATION_MODEL = os.getenv("OPENAI_VALIDATION_MODEL", MODEL)



def build_tools(*, mode: str):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_osm_tokyo",
                "description": "複数カテゴリをまとめて検索し、結果のstore_idを返す。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categories": {"type": "array", "items": {"type": "string", "enum": list(services.CATEGORY_MAP.keys())}},
                        "brand": {"type": ["string", "null"]},
                        "place": {"type": ["string", "null"]},
                        "radius_km": {"type": ["number", "null"]},
                        "open_24h": {"type": "boolean", "default": False},
                        "wheelchair": {"type": "boolean", "default": False},
                        "limit": {"type": ["integer", "null"]},
                        "union": {"type": "boolean", "default": True, "description": "Trueなら複数カテゴリ結果を一つのレイヤに結合（OR条件）"},
                        "range": {
                            "type": "string",
                            "enum": ["narrow", "standard", "wide"],
                            "default": "standard",
                            "description": "検索範囲の広さ。narrow=狭い、standard=標準、wide=広い。radius_km未指定時のデフォルトに反映。",
                        },
                    },
                    "required": ["categories"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_category_tokyo",
                "description": "単一カテゴリの検索を行いstore_idを返す。複数カテゴリは必要に応じて繰り返し呼び出す。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": list(services.CATEGORY_MAP.keys())},
                        "brand": {"type": ["string", "null"]},
                        "place": {"type": ["string", "null"]},
                        "radius_km": {"type": ["number", "null"]},
                        "open_24h": {"type": "boolean", "default": False},
                        "wheelchair": {"type": "boolean", "default": False},
                        "limit": {"type": ["integer", "null"]},
                        "range": {
                            "type": "string",
                            "enum": ["narrow", "standard", "wide"],
                            "default": "standard",
                            "description": "検索範囲の広さ。radius_km未指定時のデフォルトに反映。",
                        },
                    },
                    "required": ["category"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "merge_search_results",
                "description": "複数の検索結果(store_id)を結合し、必要なら1レイヤに統合する。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "store_ids": {"type": "array", "items": {"type": "string"}},
                        "union": {"type": "boolean", "default": True},
                    },
                    "required": ["store_ids"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "plan_trip_tokyo",
                "description": "旅行計画を作成し、行程とstore_idを返す。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "place": {"type": "string", "description": "基準地点（例: 上野駅）"},
                        "days": {"type": "integer", "default": 1, "minimum": 1, "maximum": 14},
                        "radius_km": {"type": "number", "default": 3},
                        "interests": {"type": "array", "items": {"type": "string", "enum": list(services.CATEGORY_MAP.keys())}, "default": []},
                        "per_day": {"type": "integer", "default": 6, "minimum": 1, "maximum": 12},
                        "open_24h": {"type": "boolean", "default": False},
                        "wheelchair": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "default": 300},
                        "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD（省略可）"},
                        "pace": {"type": "string", "enum": ["relaxed", "standard", "packed"], "default": "standard"},
                        "range": {
                            "type": "string",
                            "enum": ["narrow", "standard", "wide"],
                            "default": "standard",
                            "description": "旅行範囲の広さ。narrow=狭い、standard=標準、wide=広い。radius_km/limit/per_day未指定時のデフォルトに反映。",
                        },
                    },
                    "required": ["place"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "collect_trip_candidates",
                "description": "旅行候補スポットを収集しcandidate_idを返す。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "place": {"type": "string", "description": "基準地点（例: 上野駅）"},
                        "days": {"type": "integer", "default": 1, "minimum": 1, "maximum": 14},
                        "radius_km": {"type": "number", "default": 3},
                        "interests": {"type": "array", "items": {"type": "string", "enum": list(services.CATEGORY_MAP.keys())}, "default": []},
                        "per_day": {"type": "integer", "default": 6, "minimum": 1, "maximum": 12},
                        "open_24h": {"type": "boolean", "default": False},
                        "wheelchair": {"type": "boolean", "default": False},
                        "limit": {"type": "integer", "default": 300},
                        "range": {
                            "type": "string",
                            "enum": ["narrow", "standard", "wide"],
                            "default": "standard",
                            "description": "旅行範囲の広さ。radius_km/limit/per_day未指定時のデフォルトに反映。",
                        },
                    },
                    "required": ["place"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "build_trip_itinerary",
                "description": "candidate_idから旅行行程を生成しstore_idとJSON行程を返す。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "place": {"type": ["string", "null"], "description": "必要なら上書き地点"},
                        "days": {"type": "integer", "default": 1, "minimum": 1, "maximum": 14},
                        "per_day": {"type": "integer", "default": 6, "minimum": 1, "maximum": 12},
                        "pace": {"type": "string", "enum": ["relaxed", "standard", "packed"], "default": "standard"},
                        "start_date": {"type": ["string", "null"], "description": "YYYY-MM-DD（省略可）"},
                    },
                    "required": ["candidate_id"],
                },
            },
        },
    ]
    if mode == "search":
        return [tools[0], tools[1], tools[2]]
    if mode == "trip":
        return [tools[3], tools[4], tools[5]]
    return tools


def _safe_json_loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _count_itinerary_spots(itinerary: list[dict]) -> int:
    return sum(len(day.get("items") or []) for day in (itinerary or []))


def validate_search_result(user_text: str, args: dict, result: dict) -> dict:
    stats = result.get("stats") or {}
    total = sum(stats.values())
    payload = {"user_text": user_text, "args": args, "stats": stats, "total": total}
    messages = [
        {"role": "system", "content": "あなたは検索結果の検証担当。標準の厳しさで達成判定を行う。"},
        {
            "role": "user",
            "content": (
                "次の情報を見て、行った動作（ツール選択/引数/結果）が妥当か評価し、"
                "その上で目標が達成できているか判定してください。"
                "標準の厳しさ: 目安として total>=5 なら達成扱いに近いが、条件が厳しい場合は未達でもよい。"
                "複数カテゴリの場合は偏りが強すぎないかも考慮する。"
                'JSONで {"success": true/false, "reason": "...", "action_ok": true/false} のみ返す。\n'
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        started_at = time.time()
        resp = client.chat.completions.create(
            model=VALIDATION_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=120,
        )
        _log_llm_call("validate_search", started_at, model=VALIDATION_MODEL)
        data = _safe_json_loads(resp.choices[0].message.content or "")
    except Exception:
        data = None

    if not isinstance(data, dict):
        return {"success": total >= 5, "reason": "件数ベースで判定しました。", "action_ok": True}

    return {"success": bool(data.get("success")), "reason": str(data.get("reason") or ""), "action_ok": bool(data.get("action_ok", True))}


def validate_trip_result(user_text: str, args: dict, result: dict) -> dict:
    itinerary = result.get("itinerary") or []
    spots = _count_itinerary_spots(itinerary)
    days = int(args.get("days") or 1)
    per_day = int(args.get("per_day") or 6)
    payload = {
        "user_text": user_text,
        "args": args,
        "stats": result.get("stats") or {},
        "spots": spots,
        "days": days,
        "per_day": per_day,
        "day_counts": [len(d.get("items") or []) for d in itinerary],
    }
    messages = [
        {"role": "system", "content": "あなたは旅行計画の検証担当。標準の厳しさで達成判定を行う。"},
        {
            "role": "user",
            "content": (
                "次の情報を見て、行った動作（ツール選択/引数/結果）が妥当か評価し、"
                "その上で目標が達成できているか判定してください。"
                "標準の厳しさ: 目安として spots >= days*per_day*0.95 を満たし、"
                "各日の件数がやや少ない場合でも未達と判断してよい。"
                'JSONで {"success": true/false, "reason": "...", "action_ok": true/false} のみ返す。\n'
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        started_at = time.time()
        resp = client.chat.completions.create(
            model=VALIDATION_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=120,
        )
        _log_llm_call("validate_trip", started_at, model=VALIDATION_MODEL)
        data = _safe_json_loads(resp.choices[0].message.content or "")
    except Exception:
        data = None

    if not isinstance(data, dict):
        threshold = max(3, int(days * per_day * 0.95))
        day_counts = [len(d.get("items") or []) for d in itinerary]
        day_ok = all(c >= max(1, int(per_day * 0.8)) for c in day_counts)
        return {"success": spots >= threshold and day_ok, "reason": "件数ベースで判定しました。", "action_ok": True}

    return {"success": bool(data.get("success")), "reason": str(data.get("reason") or ""), "action_ok": bool(data.get("action_ok", True))}


def summarize_result(user_text: str, args: dict, result: dict, *, success: bool, reason: str, mode: str) -> str:
    payload = {"mode": mode, "user_text": user_text, "args": args, "result": result, "success": success, "reason": reason}
    messages = [
        {"role": "system", "content": "あなたは日本語で短く要約するアシスタント。失敗時は理由も説明する。"},
        {
            "role": "user",
            "content": (
                "次の情報を読み、簡潔に要約してください。"
                "成功時は要約と次の行動に役立つ短いヒントを添える。"
                "失敗時は理由を一言で説明し、条件の調整案を示す。"
                f"\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        started_at = time.time()
        resp = client.chat.completions.create(
            model=VALIDATION_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=200,
        )
        _log_llm_call("summarize", started_at, model=VALIDATION_MODEL)
        return resp.choices[0].message.content or ""
    except Exception:
        return "要約生成に失敗しました。"


def extract_args_from_review(mode: str, user_text: str, args: dict, review_text: str, last_result: dict) -> dict | None:
    payload = {
        "mode": mode,
        "user_text": user_text,
        "current_args": args,
        "review_text": review_text,
        "last_result_stats": last_result.get("stats"),
    }
    messages = [
        {"role": "system", "content": "あなたはユーザーのレビューを反映して検索条件を修正するアシスタント。"},
        {
            "role": "user",
            "content": (
                "次のレビュー内容に沿って検索条件を修正してください。"
                "修正は必要最小限に留め、カテゴリやplaceは明示がない限り変更しない。"
                "JSONで修正後のargsを返してください。\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]
    try:
        started_at = time.time()
        resp = client.chat.completions.create(
            model=VALIDATION_MODEL,
            messages=messages,
            temperature=0.2,
            max_tokens=200,
        )
        _log_llm_call("extract_review", started_at, model=VALIDATION_MODEL)
        return _safe_json_loads(resp.choices[0].message.content or "")
    except Exception:
        return None
