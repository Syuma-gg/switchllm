"""基本の使い方。MODEL の1行だけ変えればプロバイダーが切り替わる。"""

from switchllm import Client, RateLimitError

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

# 3. 推論の強さは共通の reasoning_effort で指定する（各社の指定に、名前を変えずに入れる）
res = client.chat("1から100までの和は？", reasoning_effort="low")
print(res.text)

# 4. 共通化していないパラメータは、各社の形式のまま extra_by_provider に並べる。
#    選んだプロバイダーの分だけを送るので、MODEL を変えてもこの行は直さなくてよい。
res = client.chat("hi", extra_by_provider={
    "anthropic": {"metadata": {"user_id": "user-123"}},
    "openai": {"reasoning": {"summary": "auto"}},  # 推論の要約を返させる
})

# 5. 例外はプロバイダー共通
try:
    client.chat("hi")
except RateLimitError as e:
    print("rate limited by", e.provider)
