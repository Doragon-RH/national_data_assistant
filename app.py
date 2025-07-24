import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import openai
import os
import json
import requests

# .env.local からAPIキーなどを読み込む
load_dotenv(dotenv_path=".env.local")
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- 関数定義スキーマ ---
function_definitions = [
    {
        "name": "analyze_data",
        "description": "固定されたe-Stat APIから取得した統計データを分析する",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"}
            },
            "required": ["prompt"]
        }
    }
]

# --- LLMに関数呼び出しを依頼する ---
def call_llm_openai(prompt):
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "与えられた情報から関数を選び、引数を決定してください"},
            {"role": "user", "content": f"prompt={prompt}"}
        ],
        functions=function_definitions,
        function_call="auto"
    )
    fc = response.choices[0].message.function_call
    return {
        "function": fc.name,
        "arguments": json.loads(fc.arguments)
    }

# --- 実行されるローカル関数 ---
def analyze_data(prompt):
    try:
        fixed_api_url = "http://api.e-stat.go.jp/rest/3.0/app/json/getStatsData?appId=honbinliu3@gmail.com&lang=J&statsDataId=0000010106&metaGetFlg=Y&cntGetFlg=N&explanationGetFlg=Y&annotationGetFlg=Y&sectionHeaderFlg=1&replaceSpChars=0"
        res = requests.get(fixed_api_url)
        res.raise_for_status()
        json_data = res.json()

        values = json_data.get("GET_STATS_DATA", {}).get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])

        # データ整形
        records = []
        for v in values:
            value = v.get("$")
            try:
                value = float(value) if value is not None else None
            except ValueError:
                value = None
            records.append({
                "area": v.get("@area"),
                "time": v.get("@time"),
                "value": value
            })

        df = pd.DataFrame(records)

        if df.empty or df.shape[1] == 0:
            raise ValueError("取得されたデータが空、または列がありません")

        # データ型を明示的に変換（エラー回避のため）
        df["value"] = pd.to_numeric(df["value"], errors="coerce")

        summary = df.describe(include='all').to_dict()
        return {"summary": summary, "note": prompt}

    except Exception as e:
        return {
            "summary": {"error": f"データ取得または処理中にエラーが発生しました: {str(e)}"},
            "note": prompt
        }

func_table = {
    "analyze_data": analyze_data,
}

# --- Streamlit UI ---
st.set_page_config(page_title="APIデータ分析アプリ", layout="wide")
st.title("🌐 e-Stat APIによるAIデータ分析アプリ")

prompt = st.text_input("✍️ 分析内容の指示を入力してください", placeholder="例: 都道府県別に人口の最大値を出して")

if st.button("実行"):
    with st.spinner("LLMが分析方法を思案中..."):
        try:
            call = call_llm_openai(prompt)
            func = call.get("function")
            args = call.get("arguments", {})

            if func not in func_table:
                st.error(f"⚠️ 未定義の関数: {func}")
            else:
                result = func_table[func](**args)
                st.subheader("✅ 分析結果")
                if isinstance(result["summary"], dict):
                    st.json(result["summary"])
                else:
                    st.write(result["summary"])
                st.write("💡 ノート:", result["note"])

        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
else:
    st.info("分析指示を入力して、実行ボタンを押してください。e-Stat APIからデータを取得します。")