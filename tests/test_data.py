import json

import numpy as np
import pytest

from godmode.data import Document, TokenStream, build_corpus, generate, load_source_dir, pack, split_documents
from godmode.data.synthetic import is_clean, verify
from godmode.tokenizer import BPETokenizer


def test_seed_corpus_loads_with_license():
    docs = load_source_dir("data/seed_corpus")
    assert len(docs) >= 10 and all(d.license == "MIT" for d in docs)


def test_refuses_unknown_provenance(tmp_path):
    (tmp_path / "a.luau").write_text("print(1)")
    with pytest.raises(PermissionError):
        load_source_dir(tmp_path)
    (tmp_path / "SOURCE.json").write_text(json.dumps({"name": "x", "license": "Proprietary"}))
    with pytest.raises(PermissionError):
        load_source_dir(tmp_path)


def _doc(text):
    return Document(text, "t", "MIT", "p")


def test_dedup_and_quality_filter():
    base = "local function add(a: number, b: number): number\n\treturn a + b\nend\n\nreturn add\n"
    near = base.replace("add", "sum") + "-- a comment\n"
    broken = "local function f()\n\treturn 1\n-- missing end, padded to be long enough to pass length filter"
    kept, report = build_corpus([_doc(base), _doc(base + "\n\n"), _doc(near), _doc(broken), _doc("x=1")])
    assert len(kept) == 2  # base and its renamed variant (different tokens => not a near-dup at k=5)
    assert report.rejected["exact_duplicate"] == 1
    assert report.rejected["luau_structure"] == 1
    assert report.rejected["too_short"] == 1


def test_synthetic_samples_are_verified():
    samples, rejected = generate(60, seed=3)
    assert len(samples) == 60
    for s in samples:
        assert verify(s) and is_clean(s.solution)
        if s.broken is not None:
            assert not is_clean(s.broken)


def test_split_is_deterministic_and_disjoint():
    docs = [_doc(f"local x{i} = {i}\nreturn x{i}\n" * 3) for i in range(50)]
    tr, va = split_documents(docs, 0.2)
    tr2, va2 = split_documents(docs, 0.2)
    assert [d.text for d in va] == [d.text for d in va2]
    assert not {d.text for d in tr} & {d.text for d in va}


def test_pack_and_stream():
    docs = [_doc("print('hello world')\n" * 20)] * 3
    tok = BPETokenizer.train([d.text for d in docs], vocab_size=300)
    ids = pack(docs, tok)
    assert ids.dtype == np.uint16 and (ids == tok.token_id("<|eos|>")).sum() == 3
    s = TokenStream(ids, 16)
    x, y = s.batch(4)
    assert x.shape == (4, 16) and np.array_equal(x[:, 1:].numpy(), y[:, :-1].numpy())


def test_clustered_split_keeps_templates_together():
    from godmode.data import ngram_overlap, split_clustered

    # Realistic templates: a long shared body with one varying constant (like the synthetic generator).
    body_a = "local function clampValue(x: number, lo: number, hi: number): number\n\tif x < lo then\n\t\treturn lo\n\tend\n\tif x > hi then\n\t\treturn hi\n\tend\n\treturn x\nend\n"
    body_b = "local Players = game:GetService(\"Players\")\nPlayers.PlayerAdded:Connect(function(player)\n\tlocal folder = Instance.new(\"Folder\")\n\tfolder.Name = \"leaderstats\"\n\tfolder.Parent = player\nend)\n"
    family_a = [_doc(body_a + f"local LIMIT = {i}\nreturn clampValue\n") for i in range(30)]
    family_b = [_doc(body_b + f"local VERSION = {i}\n") for i in range(30)]
    docs = family_a + family_b
    old_tr, old_va = split_documents(docs, 0.3)
    tr, va, te = split_clustered(docs, 0.3, 0.3, threshold=0.3)
    texts = lambda ds: [d.text for d in ds]
    assert ngram_overlap(texts(old_va), texts(old_tr), n=5) > 0.5  # per-document split leaks templates
    for held in (va, te):
        if held and tr:
            assert ngram_overlap(texts(held), texts(tr), n=5) < 0.2
    assert len(tr) + len(va) + len(te) == len(docs)


def test_dedup_normalization_respects_strings():
    from godmode.data.corpus import normalize_for_dedup

    a = 'local url = "http://a--b"\nprint(url)'
    b = 'local url = "http://a--c"\nprint(url)'
    assert normalize_for_dedup(a) != normalize_for_dedup(b)
    assert normalize_for_dedup("local x = 1 -- one\n") == normalize_for_dedup("local x = 1")


def test_minhash_tracks_jaccard_and_lsh_finds_near_dups():
    from godmode.data.corpus import LSHIndex, MinHasher

    h = MinHasher(num_perm=128)
    base = " ".join(f"tok{i}" for i in range(300))
    near = base.replace("tok150", "other")
    far = " ".join(f"zz{i}" for i in range(300))
    assert h.similarity(h.signature(base), h.signature(near)) > 0.9
    assert h.similarity(h.signature(base), h.signature(far)) < 0.1
    idx = LSHIndex()
    idx.add(h.signature(base))
    assert idx.query(h.signature(near), 0.85) == 0
    assert idx.query(h.signature(far), 0.85) is None


def test_jsonl_prompt_completion_source(tmp_path):
    (tmp_path / "SOURCE.json").write_text(json.dumps({"name": "x", "license": "MIT", "format": "prompt_completion"}))
    (tmp_path / "train.jsonl").write_text(json.dumps({"prompt": "-- greet\n", "completion": "print('hi')"}) + "\n")
    docs = load_source_dir(tmp_path)
    assert len(docs) == 1 and docs[0].text == "-- greet\nprint('hi')" and docs[0].meta["upstream_split"] == "train"
