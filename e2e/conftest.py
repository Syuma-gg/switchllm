"""E2E テストの設定（docs/e2e-tests.md、docs/testing.md）。

- **実 API で回す**（料金が発生する。ollama / lmstudio は除く）::

      python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5" --collect-only -q   # 回数を見るだけ
      python -m pytest e2e --models "anthropic:claude-sonnet-5-5,openai:gpt-5.5" --record          # 回して記録する

- **記録を再生する**（``--models`` を渡さないとき。ネットワークを使わない）。既定の ``python -m pytest`` にも入る。
  記録は e2e/recordings/ にあり、ライブラリが記録と違うリクエストを送るようになったら失敗する
"""

from __future__ import annotations

import contextlib
import datetime
import os
from pathlib import Path
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest
from e2e_recording import RECORDINGS, AsyncRecorder, Recorder, Replayer, load, recording_path, save
from e2e_support import (
    REPLAY_ENV,
    REPLAY_ENV_CLEARED,
    VISION_ADAPTERS,
    adapter_of,
    cloud_url_rewrites,
    pairs,
    relevant,
    requires_key,
    split_models,
    switch_premises,
)

import switchllm
from switchllm import AsyncClient, Client, SwitchLLMError
from switchllm.client import CONNECT_TIMEOUT, DEFAULT_TIMEOUT
from switchllm.providers import ADAPTERS, PRESETS

LIVE = pytest.StashKey[bool]()
MODELS = pytest.StashKey[list[str]]()
PAIRS = pytest.StashKey[list[tuple[str, str]]]()
REPORTS = pytest.StashKey[dict[str, pytest.TestReport]]()

REPLAY_KEY = "switchllm-replay-key"  # 再生ではネットワークを使わないので、キーは何でもよい
E2E_DIR = Path(__file__).parent

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
        help="実 API で試すモデル文字列をカンマ区切りで（環境変数 SWITCHLLM_E2E_MODELS でも可）。"
        "渡さなければ、記録を再生する",
    )
    group.addoption(
        "--vision-models",
        default=os.environ.get("SWITCHLLM_E2E_VISION_MODELS", ""),
        help="画像のテストも回す Chat Completions 互換のモデル（カンマ区切り）",
    )
    group.addoption("--record", action="store_true", help="実 API のやりとりを e2e/recordings/ に記録する（通ったテストだけ）")


def _recorded() -> list[dict[str, Any]]:
    return [load(p)[0] for p in sorted(RECORDINGS.rglob("*.json"))]


def pytest_configure(config: pytest.Config) -> None:
    for line in MARKERS:
        config.addinivalue_line("markers", line)
    models = split_models(config.getoption("--models"))
    live = bool(models)
    if config.getoption("--record") and not live:
        raise pytest.UsageError("--record records real API responses, so it needs --models (and costs money)")
    if live:
        for model in models:
            try:
                adapter_of(model)
            except ValueError as e:
                raise pytest.UsageError(str(e)) from None
        found_pairs = pairs(models)
    else:
        metas = _recorded()
        models = sorted({m for meta in metas if "pair" not in meta for m in meta["models"]})
        found_pairs = sorted({tuple(meta["pair"]) for meta in metas if "pair" in meta})
    config.stash[LIVE] = live
    config.stash[MODELS] = models
    config.stash[PAIRS] = found_pairs
    config.stash[REPORTS] = {}


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "model" in metafunc.fixturenames:
        models = metafunc.config.stash[MODELS]
        metafunc.parametrize("model", models, ids=models)
    if "pair" in metafunc.fixturenames:
        found = metafunc.config.stash[PAIRS]
        metafunc.parametrize("pair", found, ids=[f"{a}->{b}" for a, b in found])


def _params(item: pytest.Item) -> dict[str, Any]:
    callspec = getattr(item, "callspec", None)
    return callspec.params if callspec is not None else {}


def _has_params(item: pytest.Item) -> bool:
    # 渡すものがない（モデルが1つで切り替えの組がない、再生する記録がない）と、pytest は中身のない値で
    # テストを1つ作る。それは外す
    params = _params(item)
    return isinstance(params["model"], str) if "model" in params else isinstance(params.get("pair"), tuple)


def _applies(item: pytest.Item, vision_models: list[str]) -> bool:
    """実 API で回すときに、このモデルでこのテストを作るか。"""
    params = _params(item)
    if "model" not in params:
        return True
    model = params["model"]
    only = item.get_closest_marker("only")
    if only is not None and adapter_of(model) not in only.args:
        return False
    if item.get_closest_marker("needs_key") is not None and not requires_key(model):
        return False
    if item.get_closest_marker("vision") is not None:
        return adapter_of(model) in VISION_ADAPTERS or model in vision_models
    return True


def _premises(item: pytest.Item) -> tuple[str, ...]:
    params = _params(item)
    if "pair" in params:
        return switch_premises(*params["pair"])
    mark = item.get_closest_marker("premise")
    if mark is None or "model" not in params:
        return ()
    return relevant(tuple(mark.args), params["model"])


def _models_of(item: pytest.Item) -> list[str]:
    params = _params(item)
    return [params["model"]] if "model" in params else list(params.get("pair", ()))


def _recording_of(item: pytest.Item) -> Path:
    return recording_path(RECORDINGS, item.path.stem, item.name)


def _is_e2e(item: pytest.Item) -> bool:
    # pytest_collection_modifyitems などはセッション全体のフックなので、tests/ のテストにも呼ばれる
    return E2E_DIR in item.path.parents


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # 対象外のものには、skip ではなく、最初からテストを作らない（品質検査は skip を増やさない）。
    # 再生では、記録のあるものだけを残す
    live = config.stash[LIVE]
    vision_models = split_models(config.getoption("--vision-models"))
    keep, drop = [], []
    for item in items:
        if not _is_e2e(item):
            ok = True
        elif not _has_params(item):
            ok = False
        else:
            # 記録があるのは、記録したときに対象だったテストだけなので、再生では記録の有無だけを見る
            ok = _applies(item, vision_models) if live else _recording_of(item).is_file()
        (keep if ok else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
    e2e_items = [item for item in items if _is_e2e(item)]
    for item in e2e_items:
        item.user_properties.append(("premises", _premises(item)))

    if not live or config.option.collectonly:
        return
    # キーが足りないと、途中まで料金を払ってから止まる。API を呼ぶ前に、すべてのクライアントを作ってみる
    for model in dict.fromkeys(m for item in e2e_items for m in _models_of(item)):
        try:
            Client(model).close()
        except SwitchLLMError as e:
            pytest.exit(f"設定の誤り（API を呼ぶ前に止めた）: {e}", returncode=2)


def pytest_report_collectionfinish(config: pytest.Config, items: list[pytest.Item]) -> str | None:
    items = [item for item in items if _is_e2e(item)]
    if not config.stash[LIVE]:
        return f"switchllm E2E: 記録を再生する（{len(items)} 件。ネットワークは使わない）" if items else None
    calls = sum(int(m.args[0]) for item in items for m in [item.get_closest_marker("calls")] if m is not None)
    record = "e2e/recordings/ に記録する" if config.getoption("--record") else "記録しない（--record で記録する）"
    return (f"switchllm E2E: 実 API を最大 {calls} 回呼ぶ（リトライを除く。料金が発生する）。{record}。"
            f"モデル: {', '.join(config.stash[MODELS])}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    report = yield
    if report.when == "call" or not report.passed:
        item.config.stash[REPORTS][item.nodeid] = report
    return report


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #
def _secrets(extra: list[str]) -> list[str]:
    """記録から消す値: 各プロバイダーのキーの環境変数の値と、テストで渡したキー。"""
    envs = {e for cls in ADAPTERS.values() for e in cls.api_key_env}
    envs |= {e for p in PRESETS.values() for e in (p.api_key_env or ())}
    return [os.environ[e] for e in envs if os.environ.get(e)] + extra


TIMEOUT = httpx.Timeout(DEFAULT_TIMEOUT, connect=CONNECT_TIMEOUT)  # 利用者が Client を作ったときと同じ待ち


class _Exchanges:
    """1つのテストのやりとり。実 API なら記録し、再生なら記録した応答を返す。同期と非同期で共有する。"""

    def __init__(self, request: pytest.FixtureRequest) -> None:
        self.request = request
        self.live: bool = request.config.stash[LIVE]
        self.path = _recording_of(request.node)
        self.recorder: Recorder | AsyncRecorder | None = None
        self.replayer: Replayer | None = None
        self.passed_keys: list[str] = []
        if not self.live:
            _, exchanges = load(self.path)
            self.replayer = Replayer(exchanges)

    def transport(self) -> httpx.BaseTransport:
        if self.replayer is not None:
            return httpx.MockTransport(self.replayer)
        self.recorder = Recorder(httpx.HTTPTransport())
        return self.recorder

    def async_transport(self) -> httpx.AsyncBaseTransport:
        if self.replayer is not None:
            return httpx.MockTransport(self.replayer)
        self.recorder = AsyncRecorder(httpx.AsyncHTTPTransport())
        return self.recorder

    def client_kwargs(self, model: str, kw: dict[str, Any]) -> dict[str, Any]:
        if not self.live:
            kw.setdefault("retry_backoff", 0)
            if requires_key(model):
                kw.setdefault("api_key", REPLAY_KEY)
        if kw.get("api_key"):
            self.passed_keys.append(kw["api_key"])
        return kw

    def finish(self) -> None:
        """テストが通ったら、再生では記録をすべて使ったか確かめ、``--record`` なら記録を保存する。"""
        node, config = self.request.node, self.request.config
        report = config.stash[REPORTS].get(node.nodeid)
        if report is None or not report.passed:
            return
        if self.replayer is not None:
            unused = self.replayer.unused()
            assert not unused, f"{unused} recorded request(s) were not sent. Re-record with --models ... --record"
        if self.recorder is not None and config.getoption("--record"):
            params = _params(node)
            meta: dict[str, Any] = {
                "nodeid": node.nodeid,
                "models": _models_of(node),
                "recorded_at": datetime.date.today().isoformat(),
                "switchllm_version": switchllm.__version__,
            }
            if "pair" in params:
                meta["pair"] = list(params["pair"])
            save(
                self.path,
                meta,
                self.recorder.exchanges,
                secrets=_secrets(self.passed_keys),
                rewrite=cloud_url_rewrites(os.environ),
            )


@pytest.fixture(autouse=True)
def _replay_cloud_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """再生では、クラウドの経路の URL を、記録に残した決まった値で組み立てる（e2e_support.REPLAY_ENV）。"""
    if request.config.stash[LIVE]:
        return
    for name in REPLAY_ENV_CLEARED:
        monkeypatch.delenv(name, raising=False)
    for name, value in REPLAY_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def make_client(request: pytest.FixtureRequest) -> Iterator[Callable[..., Client]]:
    """このテスト用のクライアントを作る。実 API なら記録し、再生なら記録した応答を返す。"""
    exchanges = _Exchanges(request)
    http = httpx.Client(transport=exchanges.transport(), timeout=TIMEOUT)

    def make(model: str, **kw: Any) -> Client:
        return Client(model, http_client=http, **exchanges.client_kwargs(model, kw))

    yield make
    http.close()
    exchanges.finish()


@pytest.fixture
def open_async_client(request: pytest.FixtureRequest) -> Iterator[Callable[..., Any]]:
    """このテスト用の ``AsyncClient`` を ``async with open_async_client(model) as client:`` で開く。

    接続は、テストの中のイベントループで作って閉じる（ループをまたいで使わないため）。1つのテストで1回だけ開く。"""
    exchanges = _Exchanges(request)
    transport = exchanges.async_transport()

    @contextlib.asynccontextmanager
    async def open_client(model: str, **kw: Any) -> AsyncIterator[AsyncClient]:
        http = httpx.AsyncClient(transport=transport, timeout=TIMEOUT)
        try:
            yield AsyncClient(model, http_client=http, **exchanges.client_kwargs(model, kw))
        finally:
            await http.aclose()

    yield open_client
    exchanges.finish()


def replay_kw(config: pytest.Config, model: str) -> dict[str, Any]:
    """with_model() で別のプロバイダーへ切り替えるときに足す引数（再生では、キーの環境変数がなくてもよいように）。"""
    return {"api_key": REPLAY_KEY} if not config.stash[LIVE] and requires_key(model) else {}


@pytest.fixture
def client(make_client: Callable[..., Client], model: str) -> Client:
    return make_client(model)


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
def switch_clients(request: pytest.FixtureRequest, make_client: Callable[..., Client],
                   pair: tuple[str, str]) -> Iterator[tuple[Client, Client]]:
    a, b = pair
    first = make_client(a)
    other = first.with_model(b, **replay_kw(request.config, b))  # 切り替えは with_model() で（利用者と同じ使い方）
    yield first, other
    other.close()


# --------------------------------------------------------------------------- #
# 最後の一覧（実 API で回したときだけ）
# --------------------------------------------------------------------------- #
def _finished(tr: pytest.TerminalReporter) -> list[pytest.TestReport]:
    """各テストの結果（本体の結果と、準備で失敗したもの）を返す。"""
    reports = [r for rs in tr.stats.values() for r in rs if isinstance(r, pytest.TestReport)]
    return [r for r in reports if r.when == "call" or (r.when == "setup" and not r.passed)]


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter, config: pytest.Config) -> None:
    reports = [r for r in _finished(terminalreporter) if "e2e" in r.nodeid.split("/")[0]]
    if not config.stash[LIVE] or not reports:
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
    if config.getoption("--record"):
        saved = sum(r.passed for r in reports)
        tr.write_line(f"\n記録した: {saved} 件（e2e/recordings/）。失敗したテストは記録していない（前の記録があれば残る）")
