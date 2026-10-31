"""Compositional (trigger x effect) data, French/Spanish prompts, multilingual routing, model-first scripts."""

import random

from godmode.data import compose, script_families as sf
from godmode.server.model_backend import ModelBackend, ModelResult
from godmode.server.retrieval import Retriever
from godmode.server.service import MODEL_FIRST, GodmodeService
from godmode.tools.router import route


def test_compose_train_samples_all_verify_and_pass_their_own_checks():
    samples, rejected = compose.generate(200, seed=3)
    assert rejected == 0 and len(samples) >= 190
    for s in samples:
        assert compose.verify(s.solution)
        g = compose.grade(s.checks, s.solution)
        assert g["correct"], (s.prompt, g["missing"])
        assert not s.spec.held_out()
    assert {s.spec.lang for s in samples} == set(compose.LANGS)


def test_compose_heldout_split_only_has_heldout_pairs():
    held, rejected = compose.generate(80, seed=4, split="heldout")
    assert rejected == 0 and held
    assert all(s.spec.held_out() for s in held)
    assert all(s.spec.pairs() & compose.HELD_OUT_PAIRS for s in held)


def test_compose_grade_catches_wrong_numbers_and_unparseable_code():
    s = compose.generate(5, seed=9)[0][0]
    shifted = "".join(str((int(c) + 1) % 10) if c.isdigit() else c for c in s.solution)
    bad = compose.grade(s.checks, shifted)
    assert not bad["correct"]
    broken = compose.grade(s.checks, s.solution + "\nend end )")
    assert not broken["verified"] and not broken["correct"]


def test_family_prompts_translate_to_french_and_spanish_with_same_values():
    rng = random.Random(0)
    for fam in sf.FAMILIES:
        for lang in ("fr", "es"):
            core, _ = sf.FAMILIES[fam](random.Random(1))
            assert sf.translate_core(core, lang, rng) is not None, (fam, lang, core)
    # numbers are carried over exactly
    t = sf.translate_core("lava that does 35 damage", "fr", rng)
    assert t is None or "35" in t


def test_family_generate_multilingual_is_verified():
    samples, rejected = sf.generate(3, seed=2, langs=("en", "fr", "es"))
    assert rejected == 0 and len(samples) > 50


def test_router_understands_french_and_spanish():
    assert route("fais une porte qui s'ouvre").name == "tween_door"
    assert route("haz una casa").name == "build_house"
    assert route("construis une tour").name == "build_tower"
    assert route("¿cómo guardo datos?").name == "question"
    assert route("comment sauvegarder les données ?").name == "question"
    assert route("crée un script qui tue le joueur quand il touche la brique").name == "freeform_code"
    assert route("escribe un código que cure al jugador cada 5 segundos").name == "freeform_code"


def test_multi_eval_prompts_are_balanced_and_references_score_complete():
    from collections import Counter

    from godmode.eval import multi_eval

    ps = multi_eval.prompts(3)
    counts = Counter((p["set"], p["lang"]) for p in ps)
    assert len(counts) == 12 and set(counts.values()) == {3}
    for p in ps:
        assert multi_eval.grade_row(p, p["reference"])["complete"], p["prompt"]
        assert not multi_eval.grade_row(p, None)["complete"]


class _FakeModel(ModelBackend):
    def __init__(self, ok: bool):
        super().__init__("fake.pt")
        self.ok, self.prompt_style, self.calls = ok, "chat", 0

    def _load(self):
        return True

    def generate_code(self, request, level=None, trace=None):
        self.calls += 1
        if not self.ok:
            return ModelResult(False, "no verified candidate", None, self.tag, [], candidates=4)
        code = "-- Script in ServerScriptService: door\nlocal x = 1\nprint(x)\n"
        return ModelResult(True, "verified", code, self.tag, [{"check": "parse", "ok": True}], candidates=4,
                           verified=1)


def test_script_tool_intents_use_the_model_first_and_fall_back_to_the_tool():
    assert "tween_door" in MODEL_FIRST
    m = _FakeModel(ok=True)
    out = GodmodeService(m, Retriever([])).chat({"message": "make a door that opens"})
    assert m.calls == 1 and out["source"].startswith("model:")
    m = _FakeModel(ok=False)
    out = GodmodeService(m, Retriever([])).chat({"message": "make a door that opens"})
    assert m.calls == 1 and out["source"].startswith("tool:")
    m = _FakeModel(ok=True)
    out = GodmodeService(m, Retriever([])).chat({"message": "make a door that opens", "engine": "tool"})
    assert m.calls == 0 and out["source"].startswith("tool:")
    # builds stay deterministic tools
    m = _FakeModel(ok=True)
    out = GodmodeService(m, Retriever([])).chat({"message": "build a house"})
    assert m.calls == 0 and out["source"].startswith("tool:")
