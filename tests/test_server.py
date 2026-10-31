import json
import urllib.error
import urllib.request

import pytest
import torch

from godmode.server import GodmodeService, ModelBackend, Retriever, make_server, render_prompt, serve_in_thread
from godmode.server.app import MAX_BODY
from godmode.server.model_backend import postprocess
from godmode.server.retrieval import BM25Index, tokenize

LUAU_DOCS = {
    "tween.luau": "-- Move a part with TweenService\nlocal TweenService = game:GetService(\"TweenService\")\n"
                  "local tween = TweenService:Create(part, TweenInfo.new(1), {Position = Vector3.new(0, 10, 0)})\n"
                  "tween:Play()\n",
    "data.luau": "-- Save coins with DataStoreService\nlocal store = game:GetService(\"DataStoreService\"):GetDataStore(\"x\")\n"
                 "local ok = pcall(function() return store:UpdateAsync(\"k\", function(old) return old end) end)\n",
    "remote.luau": "-- RemoteEvent handler\nremote.OnServerEvent:Connect(function(player, v)\n"
                   "\tif typeof(v) ~= \"number\" then return end\nend)\n",
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    d = tmp_path_factory.mktemp("corpus")
    (d / "SOURCE.json").write_text(json.dumps({"name": "test-corpus", "license": "MIT"}))
    for name, text in LUAU_DOCS.items():
        (d / name).write_text(text)
    return d


@pytest.fixture(scope="module")
def server(corpus, tmp_path_factory):
    retriever = Retriever([str(corpus)], cache_dir=str(tmp_path_factory.mktemp("cache")))
    service = GodmodeService(ModelBackend(None), retriever)
    httpd = make_server("127.0.0.1", 0, service)
    serve_in_thread(httpd)
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def call(base, path, body=None, headers=None, method=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health(server):
    status, body = call(server, "/health")
    assert status == 200 and body["ok"] and body["protocol_version"] == 1
    assert body["model"]["loaded"] is False and "no checkpoint" in body["model"]["detail"]
    assert "obby" in body["components"]["tools"] and "knockdown" in body["components"]["animation_styles"]
    assert "not the neural model" in body["components"]["router"]


def test_chat_build_request(server):
    status, body = call(server, "/chat", {"message": "make an obby with 6 red platforms", "autonomy": 3})
    assert status == 200 and body["source"] == "tool:obby" and body["applied"] is False
    assert body["intent"]["name"] == "build_obby" and body["intent"]["params"]["count"] == 6
    types = [a["type"] for a in body["actions"]]
    assert types == ["create_instances", "create_script"]
    assert all(a["requires_approval"] for a in body["actions"]) and body["actions"][0]["id"] == "a1"
    assert body["verification"] and all(v["ok"] for v in body["verification"])
    assert "tool:obby" in body["reply_markdown"] and "nothing has been changed" in body["reply_markdown"]


def test_chat_animation_request(server):
    status, body = call(server, "/chat", {"message": "make a fugitive style chase sprint animation", "autonomy": 4})
    assert status == 200 and body["source"] == "tool:animation"
    anim = body["actions"][0]
    assert anim["type"] == "create_animation" and anim["style"] == "run" and anim["looped"]
    assert anim["hierarchy"]["LowerTorso"] == "HumanoidRootPart"
    assert "titles" in anim["provenance"].lower()
    assert body["actions"][1]["type"] == "create_script"
    assert all(a["requires_approval"] is False for a in body["actions"])  # level 4, non-destructive
    assert any(v["check"].startswith("animation:") and v["ok"] for v in body["verification"])


def test_chat_question_uses_cited_retrieval(server):
    status, body = call(server, "/chat", {"message": "how do I move a part with TweenService?"})
    assert status == 200 and body["source"] == "retrieval" and body["actions"] == []
    assert body["citations"] and body["citations"][0]["path"].endswith("tween.luau")
    assert body["citations"][0]["license"] == "MIT"
    assert "not answers written by GODMODE's model" in body["reply_markdown"]


def test_chat_freeform_without_model_is_honest(server):
    status, body = call(server, "/chat", {"message": "write a function that shuffles a table", "autonomy": 2})
    assert status == 200 and body["source"] == "none" and body["actions"] == []
    assert "not available" in body["reply_markdown"]


def test_autonomy_one_returns_no_actions(server):
    status, body = call(server, "/chat", {"message": "make a datastore save system", "autonomy": 1})
    assert status == 200 and body["actions"] == [] and body["source"] == "tool:datastore"
    assert "UpdateAsync" in body["reply_markdown"]  # code shown as text instead


def test_destructive_action_always_flagged(server):
    ctx = {"selection": [{"path": "Workspace.OldMap", "className": "Model", "name": "OldMap"}]}
    status, body = call(server, "/chat", {"message": "delete the selected model", "context": ctx, "autonomy": 5})
    assert status == 200
    (a,) = body["actions"]
    assert a["type"] == "delete_instance" and a["destructive"] is True and a["requires_approval"] is True


def test_verify_endpoint(server):
    status, body = call(server, "/verify", {"source": "while true do\n\twait(1)\nend\n"})
    assert status == 200 and body["source"] == "verifier"
    assert body["structure"]["ok"] and body["lint"][0]["rule"] == "GM006" and body["passed"] is False
    status, body = call(server, "/verify", {"source": "local x = (1\n"})
    assert body["structure"]["ok"] is False


def test_errors_are_json(server):
    assert call(server, "/nope")[0] == 404
    assert call(server, "/chat", method="GET")[0] == 405
    status, body = call(server, "/chat", raw=b"{not json")
    assert status == 400 and body["error"]["code"] == "bad_json"
    assert call(server, "/chat", {"message": "hi", "autonomy": 9})[0] == 400
    assert call(server, "/chat", {"message": ""})[0] == 400
    status, body = call(server, "/chat", raw=b"x" * (MAX_BODY + 10))  # server drains, then answers 413
    assert status == 413 and body["error"]["code"] == "too_large"


def test_token_auth(corpus, tmp_path):
    service = GodmodeService(ModelBackend(None), Retriever([str(corpus)], cache_dir=str(tmp_path)))
    httpd = make_server("127.0.0.1", 0, service, token="s3cret")
    serve_in_thread(httpd)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, body = call(base, "/health")
        assert status == 401 and body["error"]["code"] == "unauthorized"
        assert call(base, "/health", headers={"X-Godmode-Token": "wrong"})[0] == 401
        assert call(base, "/chat", {"message": "help"})[0] == 401
        assert call(base, "/health", headers={"X-Godmode-Token": "s3cret"})[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_default_bind_is_localhost():
    import inspect

    from godmode.server import app
    assert inspect.signature(app.make_server).parameters["host"].default == "127.0.0.1"
    httpd = make_server(port=0, service=GodmodeService(ModelBackend(None), Retriever([])))
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_bm25_ranks_and_caches(corpus, tmp_path):
    r = Retriever([str(corpus)], cache_dir=str(tmp_path))
    hits = r.search("DataStore UpdateAsync coins", k=2)
    assert hits[0].path.endswith("data.luau") and hits[0].source == "test-corpus"
    assert list(tmp_path.glob("bm25-*.pkl"))
    r2 = Retriever([str(corpus)], cache_dir=str(tmp_path))
    r2.index()
    assert "cached" in r2.detail
    assert "tween" in tokenize("TweenService") and "service" in tokenize("TweenService")
    assert BM25Index([]).search("x") == []


def test_prompt_styles():
    assert render_prompt("make a part\nred") == "-- make a part\n-- red\n"
    assert render_prompt("x", "chat") == "<|user|>x\n<|assistant|>"
    assert postprocess("local a = 1\nprint(a)\n\nlocal junk = 2", "comment") == "local a = 1\nprint(a)"
    assert postprocess("if x then\n\n\tprint(1)\nend<|eos|>zz", "comment") == "if x then\n\n\tprint(1)\nend"
    with pytest.raises(ValueError):
        render_prompt("x", "weird")


def _tiny_checkpoint(path):
    from godmode.model import GodmodeLM, ModelConfig
    from godmode.tokenizer import BPETokenizer
    from godmode.train import TrainConfig, save_checkpoint

    tok = BPETokenizer.train(list(LUAU_DOCS.values()) * 3, vocab_size=300)
    torch.manual_seed(0)
    model = GodmodeLM(ModelConfig(vocab_size=tok.vocab_size, d_model=32, n_layers=1, n_heads=2, n_kv_heads=1,
                                  max_seq_len=64))
    save_checkpoint(path, model, tok, 0, {}, TrainConfig())


@pytest.mark.parametrize("style", ["comment", "chat"])
def test_model_backend_with_tiny_checkpoint(tmp_path, style):
    ckpt = tmp_path / "tiny.pt"
    _tiny_checkpoint(ckpt)
    mb = ModelBackend(str(ckpt), n=3, max_new_tokens=24, prompt_style=style)
    res = mb.generate_code("make a part")
    assert mb.status()["loaded"] is True
    assert res.source == f"model:{ckpt}" and res.candidates == 3
    if res.ok:  # random weights rarely produce verified code; either way the result must be honest
        assert res.verified >= 1 and res.code
    else:
        assert "could not produce verified code" in res.message and res.code is None


def test_model_backend_missing_checkpoint(tmp_path):
    res = ModelBackend(str(tmp_path / "missing.pt")).generate_code("x")
    assert not res.ok and "not found" in res.message


def test_main_parses_args(monkeypatch):
    from godmode.server import app

    captured = {}

    class Fake:
        server_address = ("127.0.0.1", 1)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured["closed"] = True

    def fake_make(host, port, service, token, verbose):
        captured.update(host=host, port=port, token=token, ckpt=service.model.ckpt_path)
        return Fake()

    monkeypatch.setattr(app, "make_server", fake_make)
    assert app.main(["--port", "9999", "--token", "T", "--ckpt", "x.pt"]) == 0
    assert captured == {"host": "127.0.0.1", "port": 9999, "token": "T", "ckpt": "x.pt", "closed": True}


def test_plausibility_filter_rejects_token_soup():
    from godmode.server.model_backend import plausibility_problems

    assert plausibility_problems(" gtwemediSharpness temp-- exact в в ed command gtwe gtwe")
    assert plausibility_problems("x x x x x x x x x x x x x x x x x x x x = 1 local") != []
    assert plausibility_problems("local function sum(t)\n\tlocal s = 0\n\tfor _, v in ipairs(t) do\n\t\ts += v\n"
                                 "\tend\n\treturn s\nend") == []
