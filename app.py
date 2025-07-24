import streamlit as st
import pandas as pd
import openai
import os
import json

openai.api_key = os.getenv("OPENAI_API_KEY")

# --- 関数定義 ---
function_definitions = [
    {
        "name": "format_sales",
        "description": "売上データを要約する",
        "parameters": {
            "type": "object",
            "properties": {
                "data_id": {"type": "string"},
                "prompt": {"type": "string"}
            },
            "required": ["data_id", "prompt"]
        }
    }
]

def call_llm_openai(data_id, prompt) -> dict:
    res = openai.ChatCompletion.create(
        model="gpt-3.5-turbo-0613",
        messages=[
            {"role": "system", "content": "与えられた情報から最適な関数と引数を選んでください。"},
            {"role": "user", "content": f"data_id={data_id}, prompt={prompt}"}
        ],
        functions=function_definitions,
        function_call="auto"
    )
    fc = res["choices"][0]["message"]["function_call"]
    return {
        "function": fc["name"],
        "arguments": json.loads(fc["arguments"])
    }

def format_sales(data_id, prompt):
    df = pd.read_csv(f"data/{data_id}.csv")
    summary = df.describe().to_dict()
    return {"summary": summary, "note": prompt}

func_table = {
    "format_sales": format_sales,
}

# --- Streamlit UI ---
st.title("🧙‍♂️ Pythonだけで回す AIデータアプリ（OpenAI Function Calling対応）")

data_list = {"sales":"Sales Data", "users":"User Data"}
data_id = st.selectbox("🔢 データを選択してください", list(data_list.keys()), format_func=lambda k: data_list[k])
prompt = st.text_input("✍️ プロンプトを入力")

if st.button("実行"):
    call = call_llm_openai(data_id, prompt)

    func = call.get("function")
    args = call.get("arguments", {})
    if func not in func_table:
        st.error(f"⚠️ 未定義の関数: {func}")
    else:
        result = func_table[func](**args)
        st.subheader("✅ 解析結果")
        st.json(result["summary"])
        st.write("💡 ノート:", result["note"])
