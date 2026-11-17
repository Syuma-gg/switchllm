"""実 API を呼ぶ E2E テストの設定。**料金が発生する**（ollama / lmstudio は除く）。詳細は docs/e2e-tests.md。

    python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5" --collect-only -q   # 回数を見るだけ
    python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5"

既定の ``python -m pytest`` は tests/ だけを回すので、ここは含まれない（pyproject.toml の testpaths）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from e2e_support import VISION_ADAPTERS, adapter_of, pairs, relevant, requires_key, split_models, switch_premises

from switchllm import Client, SwitchLLMError

MODELS = pytest.StashKey[list[str]]()
CLIENTS = pytest.StashKey[dict[str, Client]]()

MARKERS = (
    "calls(n): このテストが API を呼ぶ最大の回数（収集したときに合計を表示する）",
    "premise(*ids): 確かめる未検証の前提（U 番号）。プロバイダーに関係のないものは数えない",
    "only(*adapters): このアダプターのモデルでだけ回す",
    "needs_key: APIキーの要るモデルでだけ回す（ローカルのサーバーには作らない）",
    "vision: 画像を受け付けるモデルでだけ回す（OpenAI・Anthropic・Gemini と --vision-models）",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("switchllm-e2e")
    group.addoption(
        "--models",
        default=os.environ.get("SWITCHLLM_E2E_MODELS", ""),
        help="試すモデル文字列をカンマ区切りで（環境変数 SWITCHLLM_E2E_MODELS でも可）。隣どうしで切り替えも試す",
    )
    group.addoption(
        "--vision-models",
        default=os.environ.get("SWITCHLLM_E2E_VISION_MODELS", ""),
        help="画像のテストも回す Chat Completions 互換のモデル（カンマ区切り）",
    )


def pytest_configure(config: pytest.Config) -> None:
    for line in MARKERS:
        config.addinivalue_line("markers", line)
    models = split_models(config.getoption("--models"))
    if not models:
        raise pytest.UsageError(
            "E2E tests call real APIs and cost money. Pass the models to try, e.g. "
            '--models "anthropic:claude-sonnet-5-5,openai:gpt-5.5" (or set SWITCHLLM_E2E_MODELS). '
            "Add --collect-only to see how many calls they make without calling any API."
        )
    for model in models:
        try:
            adapter_of(model)
        except ValueError as e:
            raise pytest.UsageError(str(e)) from None
    config.stash[MODELS] = models


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    models = metafunc.config.stash[MODELS]
    if "model" in metafunc.fixturenames:
        metafunc.parametrize("model", models, ids=models)
    if "pair" in metafunc.fixturenames:
        found = pairs(models)
        metafunc.parametrize("pair", found, ids=[f"{a}->{b}" for a, b in found])


def _applies(item: pytest.Item, model: str, vision_models: list[str]) -> bool:
    only = item.get_closest_marker("only")
    if only is not None and adapter_of(model) not in only.args:
        return False
    if item.get_closest_marker("needs_key") is not None and not requires_key(model):
        return False
    if item.get_closest_marker("vision") is not None:
        return adapter_of(model) in VISION_ADAPTERS or model in vision_models
    return True


def _premises(item: pytest.Item) -> tuple[str, ...]:
    params = getattr(item, "callspec", None)
    params = params.params if params is not None else {}
    if "pair" in params:
        return switch_premises(*params["pair"])
    mark = item.get_closest_marker("premise")
    if mark is None or "model" not in params:
        return ()
    return relevant(tuple(mark.args), params["model"])


def _models_of(item: pytest.Item) -> list[str]:
    params = item.callspec.params if hasattr(item, "callspec") else {}
    return [params["model"]] if "model" in params else list(params.get("pair", ()))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # 対象外のモデルには、skip ではなく、最初からテストを作らない（品質検査は skip を増やさない）
    vision_models = split_models(config.getoption("--vision-models"))
    keep, drop = [], []
    for item in items:
        params = item.callspec.params if hasattr(item, "callspec") else {}
        if "model" in params:
            ok = _applies(item, params["model"], vision_models)
        else:
            # モデルが1つなら切り替えの組がなく、pytest は中身のない組を作る。それも外す
            ok = isinstance(params.get("pair"), tuple)
        (keep if ok else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
    for item in items:
        item.user_properties.append(("premises", _premises(item)))

    if config.option.collectonly:
        return
    # キーが足りないと、途中まで料金を払ってから止まる。API を呼ぶ前に、すべてのクライアントを作る
    used = list(dict.fromkeys(m for item in items for m in _models_of(item)))
    try:
        config.stash[CLIENTS] = {m: Client(m) for m in used}
    except SwitchLLMError as e:
        pytest.exit(f"設定の誤り（API を呼ぶ前に止めた）: {e}", returncode=2)


def pytest_report_collectionfinish(config: pytest.Config, items: list[pytest.Item]) -> str:
    calls = sum(int(m.args[0]) for item in items for m in [item.get_closest_marker("calls")] if m is not None)
    models = ", ".join(config.stash[MODELS])
    return f"switchllm E2E: API を最大 {calls} 回呼ぶ（リトライを除く。料金が発生する）。モデル: {models}"


def pytest_sessionfinish(session: pytest.Session) -> None:
    for client in session.config.stash.get(CLIENTS, {}).values():
        client.close()


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(request: pytest.FixtureRequest, model: str) -> Client:
    return request.config.stash[CLIENTS][model]


@pytest.fixture
def adapter(model: str) -> str:
    return adapter_of(model)


@pytest.fixture
def note(request: pytest.FixtureRequest) -> Callable[[str], None]:
    """観察したこと（推論情報が返ったか、API が拒否したときの文言など）を、最後の一覧に残す。"""

    def add(text: str) -> None:
        request.node.user_properties.append(("note", text))

    return add


@pytest.fixture
def switch_clients(request: pytest.FixtureRequest, pair: tuple[str, str]) -> Iterator[tuple[Client, Client]]:
    a, b = pair
    first = request.config.stash[CLIENTS][a]
    other = first.with_model(b)  # 切り替えは with_model() で（利用者と同じ使い方）
    yield first, other
    other.close()


# --------------------------------------------------------------------------- #
# 最後の一覧
# --------------------------------------------------------------------------- #
def _finished(tr: pytest.TerminalReporter) -> list[pytest.TestReport]:
    """各テストの結果（本体の結果と、準備で失敗したもの）を、実行した順に返す。"""
    reports = [r for rs in tr.stats.values() for r in rs if isinstance(r, pytest.TestReport)]
    return [r for r in reports if r.when == "call" or (r.when == "setup" and not r.passed)]


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    reports = _finished(terminalreporter)
    if not reports:
        return
    tr = terminalreporter
    tr.section("switchllm E2E: U 番号ごとの結果")
    by_id: dict[str, list[str]] = {}
    for r in reports:
        premises = next((v for k, v in r.user_properties if k == "premises"), ())
        for i in premises:
            by_id.setdefault(i, []).append(f"{r.nodeid.split('::', 1)[-1]} {r.outcome.upper()}")
    for i in sorted(by_id, key=lambda s: int(s[1:])):
        tr.write_line(f"{i}: " + "、".join(by_id[i]))
    if not by_id:
        tr.write_line("（このモデルの組み合わせで確かめられる U 番号はなかった）")
    tr.section("switchllm E2E: 観察メモ")
    for r in reports:
        notes = [v for k, v in r.user_properties if k == "note"]
        if notes:
            tr.write_line(f"[{r.outcome.upper()}] {r.nodeid.split('::', 1)[-1]}: " + " / ".join(notes))
