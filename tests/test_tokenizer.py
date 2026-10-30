from godmode.tokenizer import SPECIAL_TOKENS, BPETokenizer

TEXT = [
    'local Players = game:GetService("Players")\n\tlocal x = 1\n',
    "for _, player in Players:GetPlayers() do\n\tprint(player.Name)\nend\n",
] * 20


def test_roundtrip_including_unicode_and_whitespace():
    tok = BPETokenizer.train(TEXT, vocab_size=400)
    for s in TEXT + ["héllo 🌍 ~= ..= \t\n  end", ""]:
        assert tok.decode(tok.encode(s)) == s


def test_merges_compress():
    tok = BPETokenizer.train(TEXT, vocab_size=400)
    s = TEXT[0]
    assert len(tok.encode(s)) < len(s.encode()) / 2


def test_special_tokens_only_when_allowed():
    tok = BPETokenizer.train(TEXT, vocab_size=300)
    ids = tok.encode("<|user|>hi", allow_special=True)
    assert ids[0] == tok.token_id("<|user|>")
    assert tok.token_id("<|user|>") not in tok.encode("<|user|>hi")
    assert len(SPECIAL_TOKENS) == len(set(SPECIAL_TOKENS))


def test_training_is_deterministic_and_persists(tmp_path):
    a = BPETokenizer.train(TEXT, vocab_size=350)
    b = BPETokenizer.train(TEXT, vocab_size=350)
    assert a.merges == b.merges
    a.save(tmp_path / "t.json")
    c = BPETokenizer.load(tmp_path / "t.json")
    assert c.fingerprint() == a.fingerprint()
    assert c.encode(TEXT[1]) == a.encode(TEXT[1])
