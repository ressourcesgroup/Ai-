"""Thinking levels, model placement, SFT data encoding, script families and free-form routing."""

import random

import pytest
import torch

from godmode.data.script_families import FAMILIES, HELD_OUT_FAMILIES, generate, verify_solution
from godmode.server import GodmodeService, ModelBackend, Retriever
from godmode.server.model_backend import ModelResult, is_sft_checkpoint, parse_placement
from godmode.server.service import BadRequest
from godmode.server.thinking import DEFAULT_LEVEL, LEVELS, Trace, list_levels, resolve_level
from godmode.tokenizer import BPETokenizer
from godmode.tools import route
from godmode.train.sft import SFTConfig, encode_example, lr_at, make_batches

# ---------------------------------------------------------------- thinking levels


def test_levels_scale_compute_monotonically():
    order = ["low", "medium", "high", "xhigh", "max"]
    assert list(LEVELS) == order and DEFAULT_LEVEL == "medium"
    samples = [LEVELS[k].samples for k in order]
    budgets = [LEVELS[k].max_new_tokens for k in order]
    assert samples == sorted(samples) and len(set(samples)) == 5
    assert budgets == sorted(budgets)
    assert not LEVELS["low"].rerank and LEVELS["max"].rerank
    assert [lv["label"] for lv in list_levels()] == ["Low", "Medium", "High", "Extra high", "Max"]


@pytest.mark.parametrize("value,expected", [(None, "medium"), ("low", "low"), ("Extra high", "xhigh"),
                                            ("extra-high", "xhigh"), ("XHIGH", "xhigh"), ("Max", "max")])
def test_resolve_level(value, expected):
    assert resolve_level(value).id == expected


@pytest.mark.parametrize("bad", ["ultra", "", 3, ["low"]])
def test_resolve_level_rejects(bad):
    with pytest.raises(ValueError):
        resolve_level(bad)


def test_trace_records_steps():
    t = Trace(LEVELS["high"])
    t.step("A", "first")
    t.step("B", "second")
    j = t.to_json("done")
    assert j["level"] == "high" and j["summary"] == "done" and j["elapsed_ms"] >= 0
    assert [s["label"] for s in j["steps"]] == ["A", "B"] and all(s["ms"] >= 0 for s in j["steps"])


# ---------------------------------------------------------------- placement


def test_parse_placement():
    assert parse_placement("-- LocalScript in StarterPlayer.StarterPlayerScripts: hold shift to sprint\nlocal x = 1") == \
        ("LocalScript", "StarterPlayer.StarterPlayerScripts", "HoldShiftToSprint")
    assert parse_placement("-- Script in ServerScriptService: kill brick\n") == ("Script", "ServerScriptService", "KillBrick")
    assert parse_placement("-- Script in StarterGui: nope\n") is None  # server Script in a client container
    assert parse_placement("local x = 1\n") is None


# ---------------------------------------------------------------- service with a fake model


class FakeModel:
    tag = "model:fake"

    def __init__(self, code):
        self.code, self.levels = code, []

    def status(self):
        return {"loaded": True, "checkpoint": "fake"}

    def generate_code(self, request, level=None, trace=None):
        self.levels.append(level.id if level else None)
        if trace is not None:
            trace.step("Sample candidates", "fake")
        if self.code is None:
            return ModelResult(False, "nothing verified", source=self.tag, candidates=level.samples if level else 1)
        return ModelResult(True, "1 of 1 passed", code=self.code, source=self.tag, candidates=1, verified=1)


CODE = "-- Script in ServerScriptService: coin pickup\nlocal Players = game:GetService(\"Players\")\nprint(Players)\n"


def _service(code=CODE):
    return GodmodeService(FakeModel(code), Retriever([]))


def test_health_lists_thinking_levels():
    h = _service().health()
    assert [lv["id"] for lv in h["thinking_levels"]] == list(LEVELS)


def test_chat_model_request_uses_level_and_placement():
    svc = _service()
    out = svc.chat({"message": "make a coin that gives cash when touched", "autonomy": 5, "thinking": "Extra high"})
    assert svc.model.levels == ["xhigh"]
    assert out["thinking"]["level"] == "xhigh" and out["thinking"]["steps"]
    labels = [s["label"] for s in out["thinking"]["steps"]]
    assert "Sample candidates" in labels and "Place script" in labels
    (a,) = out["actions"]
    assert (a["class"], a["parent_path"], a["name"]) == ("Script", "ServerScriptService", "CoinPickup")
    assert a["requires_approval"] is True  # model code is always confirmed, even at autonomy 5
    assert "always_confirm" not in a


def test_chat_model_failure_is_honest():
    out = _service(None).chat({"message": "make a coin that gives cash", "autonomy": 3, "thinking": "low"})
    assert out["actions"] == [] and "No code is returned" in out["reply_markdown"]


def test_chat_rejects_bad_thinking():
    with pytest.raises(BadRequest):
        _service().chat({"message": "hi", "thinking": "ultra"})


def test_tool_requests_still_use_tools_and_report_trace():
    out = _service().chat({"message": "build an obby", "autonomy": 3})
    assert out["source"] == "tool:obby" and out["thinking"]["level"] == DEFAULT_LEVEL


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize("msg", ["make a kill brick", "make a coin that gives cash when touched",
                                 "add sprint when holding shift", "create a teleporter pad"])
def test_open_requests_go_to_the_model(msg):
    assert route(msg, {}).name == "freeform_code"


def test_tools_keep_their_requests():
    assert route("build an obby with kill bricks", {}).name == "build_obby"
    assert route("hello there", {}).name == "unknown"


# ---------------------------------------------------------------- SFT data


@pytest.fixture(scope="module")
def tok():
    texts = [s.solution for s in generate(2, seed=0)[0]]
    return BPETokenizer.train(texts, vocab_size=400)


def test_encode_example_masks_prompt(tok):
    ids, tg = encode_example(tok, "make a kill brick", "local x = 1", 512)
    head = tok.encode("<|bos|><|user|>make a kill brick\n<|assistant|>", allow_special=True)
    assert ids[:len(head)] == head and ids[-1] == tok.token_id("<|eos|>")
    assert all(t == -100 for t in tg[:len(head) - 1])
    assert tg[len(head) - 1:-1] == ids[len(head):]  # every script token (and eos) is a target
    assert tg[-1] == -100
    assert encode_example(tok, "x", "local y = 2\n" * 400, 64) is None  # too long is dropped, not truncated


def test_make_batches_pads_with_ignored_targets(tok):
    ex = [encode_example(tok, f"p{i}", "local a = " + "1 + " * i + "1", 512) for i in range(10)]
    pad = tok.token_id("<|pad|>")
    seen = 0
    for x, y in make_batches(ex, 4, random.Random(0), pad):
        assert x.shape == y.shape
        assert bool(((x == pad) <= (y == -100)).all())  # padding never contributes to the loss
        seen += x.shape[0]
    assert seen == 10


def test_lr_schedule():
    cfg = SFTConfig(steps=100, warmup=10, lr=1e-3, min_lr=1e-4)
    assert lr_at(0, cfg) == pytest.approx(1e-4) and lr_at(9, cfg) == pytest.approx(1e-3)
    assert lr_at(100, cfg) == pytest.approx(1e-4)


def test_sft_checkpoint_detection():
    assert is_sft_checkpoint({"metrics": {"sft": True}})
    assert is_sft_checkpoint({"metrics": {"val_loss_before": 2.0}})
    assert not is_sft_checkpoint({"metrics": {}}) and not is_sft_checkpoint({})


def test_auto_style_resolves_on_load(tmp_path):
    from godmode.model import GodmodeLM, ModelConfig
    from godmode.train import TrainConfig, save_checkpoint

    t = BPETokenizer.train(["local a = 1\nprint(a)\n"] * 5, vocab_size=280)
    torch.manual_seed(0)
    m = GodmodeLM(ModelConfig(vocab_size=t.vocab_size, d_model=32, n_layers=1, n_heads=2, n_kv_heads=1, max_seq_len=64))
    for metrics, style in (({"sft": True}, "chat"), ({}, "comment")):
        p = tmp_path / f"{style}.pt"
        save_checkpoint(p, m, t, 0, metrics, TrainConfig())
        mb = ModelBackend(str(p), n=1, max_new_tokens=4)
        assert mb.prompt_style == "auto"
        mb.generate_code("x")
        assert mb.prompt_style == style


# ---------------------------------------------------------------- script families


def test_every_family_generates_verified_complete_scripts():
    samples, rejected = generate(3, seed=7)
    assert rejected == 0
    assert {s.family for s in samples} == set(FAMILIES)
    for s in samples:
        assert verify_solution(s.solution), s.family
        assert parse_placement(s.solution) is not None, (s.family, s.solution.splitlines()[0])


def test_held_out_families_are_excluded_from_training_generation():
    train_families = [f for f in FAMILIES if f not in HELD_OUT_FAMILIES]
    samples, _ = generate(2, seed=1, families=train_families)
    assert not {s.family for s in samples} & set(HELD_OUT_FAMILIES)


# ---------------------------------------------------------------- script eval scoring


def test_script_eval_scoring():
    from godmode.eval.script_eval import eval_prompts, score, summarize

    ref = '-- Script in ServerScriptService: x\nlocal P = game:GetService("Players")\nlocal R = game:GetService("RunService")\n'
    good = '-- Script in ServerScriptService: y\nlocal P = game:GetService("Players")\nprint(P)\n'
    assert score(good, ref) == {"verified": True, "placed": True, "class_ok": True, "service_recall": 0.5,
                                "api_recall": 1.0, "complete": True}
    # a valid script for a different task must not count as complete
    pad = ('-- Script in ServerScriptService: pad\nlocal P = game:GetService("Players")\n'
           'local pad = Instance.new("Part")\npad.Material = Enum.Material.Neon\nh.WalkSpeed = 40\n')
    katana = ('-- Script in ServerScriptService: katana\nlocal P = game:GetService("Players")\n'
              'local t = Instance.new("Tool")\nt.RequiresHandle = true\n')
    off = score(katana, pad)
    assert off["class_ok"] and off["service_recall"] == 1.0 and off["api_recall"] < 0.5 and not off["complete"]
    wrong = '-- LocalScript in StarterGui: y\nprint(1)\n'
    s = score(wrong, ref)
    assert s["placed"] and not s["class_ok"] and not s["complete"]
    assert score(None, ref)["verified"] is False
    ps = eval_prompts(1, 2, seed=5)
    assert {p["split"] for p in ps} == {"seen", "held_out"}
    assert {p["family"] for p in ps if p["split"] == "held_out"} == set(HELD_OUT_FAMILIES)
    rows = [{**score(good, ref), "split": "seen", "seconds": 1.0}, {**score(None, ref), "split": "seen", "seconds": 3.0}]
    assert summarize(rows)["seen"]["complete"] == 0.5 and summarize(rows)["seen"]["mean_seconds"] == 2.0
