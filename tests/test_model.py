import pytest
import torch

from godmode.model import GodmodeLM, ModelConfig

VARIANTS = {
    "dense": {},
    "moe": {"n_experts": 4, "top_k": 2},
    "recurrent": {"n_loops": 2},
    "mha": {"n_kv_heads": 4},
}


def make(**kw):
    torch.manual_seed(0)
    base = dict(vocab_size=97, d_model=32, n_layers=2, n_heads=4, n_kv_heads=2, max_seq_len=32)
    return GodmodeLM(ModelConfig(**{**base, **kw}))


@pytest.mark.parametrize("kw", VARIANTS.values(), ids=VARIANTS.keys())
def test_forward_shapes_and_loss(kw):
    m = make(**kw)
    x = torch.randint(0, 97, (2, 12))
    out = m(x, targets=x)
    assert out.logits.shape == (2, 12, 97)
    assert torch.isfinite(out.loss)


@pytest.mark.parametrize("kw", VARIANTS.values(), ids=VARIANTS.keys())
def test_causality(kw):
    m = make(**kw).eval()
    x = torch.randint(0, 97, (1, 10))
    y = x.clone()
    y[0, 7:] = (y[0, 7:] + 1) % 97
    a, b = m(x).logits, m(y).logits
    assert torch.allclose(a[:, :7], b[:, :7], atol=1e-5)
    assert not torch.allclose(a[:, 7:], b[:, 7:])


@pytest.mark.parametrize("kw", VARIANTS.values(), ids=VARIANTS.keys())
def test_kv_cache_matches_full_forward(kw):
    m = make(**kw).eval()
    x = torch.randint(0, 97, (2, 12))
    full = m(x).logits
    out = m(x[:, :5], use_cache=True)
    parts, cache = [out.logits], out.cache
    for t in range(5, 12):
        out = m(x[:, t : t + 1], cache=cache)
        parts.append(out.logits)
        cache = out.cache
    assert torch.allclose(full, torch.cat(parts, 1), atol=1e-4)


def test_moe_aux_loss_and_param_accounting():
    m = make(n_experts=4, top_k=2)
    x = torch.randint(0, 97, (2, 12))
    out = m(x, targets=x)
    assert len(out.moe_stats) == 1  # moe_every=2 with 2 layers -> layer 2 only
    s = out.moe_stats[0]
    assert s.tokens_per_expert.sum().item() == 2 * 12 * 2  # every token routed to top_k experts
    assert s.aux_loss.item() >= 1.0 - 1e-4  # Switch loss is minimised (=1) at uniform routing
    assert out.loss.item() > out.lm_loss.item()
    assert m.num_params(active_only=True) < m.num_params()


@pytest.mark.parametrize("kw", VARIANTS.values(), ids=VARIANTS.keys())
def test_can_overfit_a_batch(kw):
    m = make(**kw)
    opt = torch.optim.AdamW(m.parameters(), lr=1e-2)
    x = torch.randint(0, 97, (4, 16))
    first = None
    for _ in range(60):
        out = m(x[:, :-1], targets=x[:, 1:])
        opt.zero_grad()
        out.loss.backward()
        opt.step()
        first = first if first is not None else out.lm_loss.item()
    assert out.lm_loss.item() < 0.3 * first


def test_generate_respects_limits_and_is_deterministic_greedy():
    m = make()
    prompt = torch.randint(0, 97, (1, 4))
    a = m.generate(prompt, 10, temperature=0)
    b = m.generate(prompt, 10, temperature=0)
    assert torch.equal(a, b) and a.shape[1] == 14
    assert m.generate(prompt, 1000, temperature=0).shape[1] <= 32


def test_config_validation():
    with pytest.raises(ValueError):
        ModelConfig(d_model=30, n_heads=4)
    with pytest.raises(ValueError):
        ModelConfig(n_experts=2, top_k=3)
