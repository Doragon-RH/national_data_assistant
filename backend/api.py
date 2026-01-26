# backend/api.py

import json
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend import agent, services

services.load_taxonomy(force=True)


class NLQuery(BaseModel):
    text: str


class ReviewPayload(BaseModel):
    review_text: str
    context: dict


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "search_osm_tokyo":
        arguments = services.apply_range_defaults(arguments, mode="search")
        return services.run_search_tool(arguments)
    if name == "search_category_tokyo":
        return services.run_search_category_tool(arguments)
    if name == "merge_search_results":
        return services.merge_search_results_tool(arguments)
    if name == "plan_trip_tokyo":
        arguments = services.apply_range_defaults(arguments, mode="trip")
        return services.plan_trip_tokyo_impl(arguments)
    if name == "collect_trip_candidates":
        return services.collect_trip_candidates_tool(arguments)
    if name == "build_trip_itinerary":
        return services.build_trip_itinerary_tool(arguments)
    raise ValueError("unknown tool")


def _run_tool_by_mode(mode: str, args: dict) -> dict:
    if mode == "search":
        args = services.apply_range_defaults(args, mode="search")
        return services.run_search_tool(args)
    if mode == "trip":
        args = services.apply_range_defaults(args, mode="trip")
        return services.plan_trip_tokyo_impl(args)
    raise ValueError("unknown mode")


def _tool_call_dict(tc) -> dict:
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def _run_agent_tool_chain(user_text: str, mode: str) -> Dict[str, Any]:
    messages = [{"role": "system", "content": agent.SYSTEM}, {"role": "user", "content": user_text}]
    tools = agent.build_tools(mode=mode)
    nudge_used = False
    for _ in range(agent.MAX_TOOL_STEPS):
        resp = agent.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=300,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            if nudge_used:
                return {"error": "no tool calls"}
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "検索または旅行計画のリクエストです。必ず適切なツールを使って結果を取得してください。",
                }
            )
            nudge_used = True
            continue

        messages.append({"role": "assistant", "tool_calls": [_tool_call_dict(tc) for tc in msg.tool_calls], "content": msg.content})

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = _call_tool(tc.function.name, args)
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)}
            )
            if isinstance(result, dict) and result.get("result_type") in ("search", "trip"):
                final_args = result.get("args") or args
                return {"result": result, "args": final_args, "tool_name": tc.function.name}

    return {"error": "tool steps exceeded"}


app = FastAPI(title="Custom Map API (Tokyo)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def taxonomy_hot_reload(request, call_next):
    try:
        services.load_taxonomy(force=False)
    except Exception as e:
        print(f"[taxonomy] reload failed: {e}")
    return await call_next(request)


@app.post("/v1/map/query")
def map_query(payload: NLQuery):
    user_text = payload.text
    chain = _run_agent_tool_chain(user_text, mode="search")
    if chain.get("error"):
        summary = agent.summarize_result(user_text, {}, {}, success=False, reason="パラメータ抽出に失敗しました。", mode="search")
        return {"summary": summary, "success": False, "failure_reason": "パラメータ抽出に失敗しました。"}

    args = chain.get("args") or {}
    last_result: Dict[str, Any] = chain.get("result") or {}
    last_reason = ""

    for attempt in range(1, agent.MAX_ITERS + 1):
        if attempt > 1:
            args = services.revise_search_args(args, attempt=attempt - 1)
            try:
                last_result = _run_tool_by_mode("search", args)
            except Exception as e:
                raise HTTPException(400, f"検索に失敗しました: {e}")

        validation = agent.validate_search_result(user_text, args, last_result)
        if validation.get("action_ok") is False:
            store_id = last_result.get("store_id")
            if store_id:
                services.STORE.pop(store_id, None)
            summary = agent.summarize_result(user_text, args, last_result, success=False, reason=validation.get("reason", ""), mode="search")
            return {
                "summary": summary,
                "store_id": None,
                "stats": last_result.get("stats"),
                "success": False,
                "failure_reason": validation.get("reason") or "処理が妥当でないと判断されました。",
                "evaluation": validation,
                "require_review": True,
                "review_reason": validation.get("reason") or "",
                "review_context": {"mode": "search", "user_text": user_text, "args": args, "last_result": last_result},
                "attempts": attempt,
            }
        if validation.get("success"):
            summary = agent.summarize_result(user_text, args, last_result, success=True, reason="", mode="search")
            return {
                "summary": summary,
                "store_id": last_result.get("store_id"),
                "stats": last_result.get("stats"),
                "success": True,
                "failure_reason": "",
                "evaluation": validation,
                "attempts": attempt,
            }

        last_reason = validation.get("reason") or "目的が未達でした。"

    summary = agent.summarize_result(user_text, args, last_result, success=False, reason=last_reason, mode="search")
    return {
        "summary": summary,
        "store_id": last_result.get("store_id"),
        "stats": last_result.get("stats"),
        "success": False,
        "failure_reason": last_reason,
        "evaluation": validation if isinstance(validation, dict) else {},
        "attempts": agent.MAX_ITERS,
    }


@app.post("/v1/trip/plan")
def trip_plan(payload: NLQuery):
    user_text = payload.text
    chain = _run_agent_tool_chain(user_text, mode="trip")
    if chain.get("error"):
        summary = agent.summarize_result(user_text, {}, {}, success=False, reason="パラメータ抽出に失敗しました。", mode="trip")
        return {"summary": summary, "success": False, "failure_reason": "パラメータ抽出に失敗しました。"}

    args = chain.get("args") or {}
    last_result: Dict[str, Any] = chain.get("result") or {}
    last_reason = ""

    for attempt in range(1, agent.MAX_ITERS + 1):
        if attempt > 1:
            args = services.revise_trip_args(args, attempt=attempt - 1)
            try:
                last_result = _run_tool_by_mode("trip", args)
            except Exception as e:
                raise HTTPException(400, f"旅行計画に失敗しました: {e}")

        validation = agent.validate_trip_result(user_text, args, last_result)
        if validation.get("action_ok") is False:
            store_id = last_result.get("store_id")
            if store_id:
                services.STORE.pop(store_id, None)
            summary = agent.summarize_result(user_text, args, last_result, success=False, reason=validation.get("reason", ""), mode="trip")
            return {
                "summary": summary,
                **last_result,
                "store_id": None,
                "success": False,
                "failure_reason": validation.get("reason") or "処理が妥当でないと判断されました。",
                "evaluation": validation,
                "require_review": True,
                "review_reason": validation.get("reason") or "",
                "review_context": {"mode": "trip", "user_text": user_text, "args": args, "last_result": last_result},
                "attempts": attempt,
            }
        if validation.get("success"):
            summary = agent.summarize_result(user_text, args, last_result, success=True, reason="", mode="trip")
            return {
                "summary": summary,
                **last_result,
                "success": True,
                "failure_reason": "",
                "evaluation": validation,
                "attempts": attempt,
            }

        last_reason = validation.get("reason") or "目的が未達でした。"

    summary = agent.summarize_result(user_text, args, last_result, success=False, reason=last_reason, mode="trip")
    return {
        "summary": summary,
        **last_result,
        "success": False,
        "failure_reason": last_reason,
        "evaluation": validation if isinstance(validation, dict) else {},
        "attempts": agent.MAX_ITERS,
    }


@app.post("/v1/review/continue")
def review_continue(payload: ReviewPayload):
    ctx = payload.context or {}
    review_text = (payload.review_text or "").strip()
    mode = ctx.get("mode")
    user_text = ctx.get("user_text") or ""
    args = ctx.get("args") or {}
    last_result = ctx.get("last_result") or {}

    if not review_text or mode not in ("search", "trip"):
        raise HTTPException(400, "invalid review payload")

    new_args = agent.extract_args_from_review(mode, user_text, args, review_text, last_result)
    if not isinstance(new_args, dict):
        raise HTTPException(400, "review parsing failed")

    new_args = services.apply_range_defaults(new_args, mode=mode)

    last_reason = ""
    validation: Dict[str, Any] = {}
    for attempt in range(1, agent.MAX_ITERS + 1):
        try:
            last_result = _run_tool_by_mode(mode, new_args)
        except Exception as e:
            raise HTTPException(400, f"処理に失敗しました: {e}")

        if mode == "search":
            validation = agent.validate_search_result(user_text, new_args, last_result)
        else:
            validation = agent.validate_trip_result(user_text, new_args, last_result)

        if validation.get("action_ok") is False:
            store_id = last_result.get("store_id")
            if store_id:
                services.STORE.pop(store_id, None)
            summary = agent.summarize_result(user_text, new_args, last_result, success=False, reason=validation.get("reason", ""), mode=mode)
            return {
                "summary": summary,
                **(last_result if mode == "trip" else {}),
                "store_id": None,
                "stats": last_result.get("stats"),
                "success": False,
                "failure_reason": validation.get("reason") or "処理が妥当でないと判断されました。",
                "evaluation": validation,
                "require_review": True,
                "review_reason": validation.get("reason") or "",
                "review_context": {"mode": mode, "user_text": user_text, "args": new_args, "last_result": last_result},
                "attempts": attempt,
            }

        if validation.get("success"):
            summary = agent.summarize_result(user_text, new_args, last_result, success=True, reason="", mode=mode)
            return {
                "summary": summary,
                **(last_result if mode == "trip" else {}),
                "store_id": last_result.get("store_id"),
                "stats": last_result.get("stats"),
                "success": True,
                "failure_reason": "",
                "evaluation": validation,
                "attempts": attempt,
            }

        last_reason = validation.get("reason") or "目的が未達でした。"
        if attempt < agent.MAX_ITERS:
            if mode == "search":
                new_args = services.revise_search_args(new_args, attempt=attempt)
            else:
                new_args = services.revise_trip_args(new_args, attempt=attempt)

    summary = agent.summarize_result(user_text, new_args, last_result, success=False, reason=last_reason, mode=mode)
    return {
        "summary": summary,
        **(last_result if mode == "trip" else {}),
        "store_id": last_result.get("store_id"),
        "stats": last_result.get("stats"),
        "success": False,
        "failure_reason": last_reason,
        "evaluation": validation if isinstance(validation, dict) else {},
        "attempts": agent.MAX_ITERS,
    }


@app.get("/v1/map/{store_id}/geojson")
def map_geojson(store_id: str):
    data = services.STORE.get(store_id)
    if not data:
        raise HTTPException(404, "store_id not found")
    feats = []
    for layer, rows in data["layers"].items():
        for r in rows:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                    "properties": {"layer": layer, "name": r.get("name"), "brand": r.get("brand")},
                }
            )
    return {"type": "FeatureCollection", "features": feats, "meta": data["meta"]}
