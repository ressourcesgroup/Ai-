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
