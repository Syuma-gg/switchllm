"""基本の使い方。MODEL の1行だけ変えればプロバイダーが切り替わる。"""

from unillm import Client, RateLimitError

MODEL = "anthropic:claude-sonnet-5-5"
# MODEL = "openai:gpt-5.5"
# MODEL = "gemini:gemini-flash-latest"
# MODEL = "ollama:llama3.2"          # ローカル、APIキー不要

client = Client(MODEL)

# 1. 単発の質問
res = client.chat("日本の首都は？一言で。", max_tokens=50)
print(res.text, res.usage.total_tokens, res.stop_reason)

# 2. ストリーミング
for ev in client.stream("俳句を1つ詠んで", max_tokens=200):
    if ev.type == "text":
        print(ev.text, end="", flush=True)
    elif ev.type == "done":
        print(f"\n(出力 {ev.response.usage.output_tokens} tokens)")

# 3. プロバイダー固有のパラメータは extra でリクエスト本体にそのまま渡す（ここだけはプロバイダー依存）。
#    thinking / reasoning の指定方法はモデル世代で変わるので、各社ドキュメントで確認してから入れる。
if client.provider.key == "anthropic":
    res = client.chat("hi", extra={"metadata": {"user_id": "user-123"}})

# 4. 例外はプロバイダー共通
try:
    client.chat("hi")
except RateLimitError as e:
    print("rate limited by", e.provider)
