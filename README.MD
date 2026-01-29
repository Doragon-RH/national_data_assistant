# 起動（Docker）

必要なもの: Docker / Docker Compose

1) 環境変数を用意（.env.local）
```
OPENAI_API_KEY=sk-...
```

2) 起動
```
docker compose up --build
```

3) アクセス
- フロント: http://localhost:8501
- バックエンド: http://localhost:8000

#
