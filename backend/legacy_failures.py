# Legacy failure examples (do not import in runtime).
# This file preserves prior single-shot configurations and stricter failure setups.
from __future__ import annotations

import json

# Single-shot SYSTEM (prompt-only failure example)
SINGLE_SHOT_SYSTEM = (
    "あなたは東京都限定の地理エージェント。"
    "ユーザーの目的に応じて適切なツールを自律的に選択する。"
    "ツール呼び出しは1回のみで完結させる。"
    "カテゴリや場所、条件（半径/24時間/車椅子/件数など）はユーザーの意図に沿って抽出する。"
    "複数カテゴリがある場合は、単一カテゴリ検索のどれか1回で判断する。"
    "旅行範囲の希望（狭い/標準/広い）が読み取れる場合は range に反映する。"
    "距離表現が曖昧でも許容し、文脈から最適な range を自律的に推定してよい。"
)


def run_agent_tool_chain_single_shot(agent, call_tool, user_text: str, mode: str):
    """Single-shot tool selection (legacy reference)."""
    messages = [{"role": "system", "content": agent.SYSTEM}, {"role": "user", "content": user_text}]
    tools = agent.build_tools(mode=mode)
    resp = agent.client.chat.completions.create(
        model=agent.MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
        max_tokens=300,
    )
    msg = resp.choices[0].message
    if not msg.tool_calls:
        return {
            "error": "no tool calls",
            "llm_message": msg.content or "",
            "llm_finish_reason": resp.choices[0].finish_reason,
        }
    tc = msg.tool_calls[0]
    args = json.loads(tc.function.arguments or "{}")
    result = call_tool(tc.function.name, args)
    if isinstance(result, dict) and result.get("result_type") in ("search", "trip"):
        final_args = result.get("args") or args
        return {"result": result, "args": final_args, "tool_name": tc.function.name}
    return {"error": "tool result not final"}
