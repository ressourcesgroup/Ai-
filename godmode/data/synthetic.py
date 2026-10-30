"""Synthetic data factory v0: parameterized Luau tasks with verified answers.

Generation is template-based and deterministic per seed. Every sample is
verified before it is kept (synthetic data is never assumed correct):
- solutions must pass the structural checker with no lint errors;
- for repair tasks, the broken input must FAIL the checker/lint and the fixed
  output must PASS, so the label carries real signal.
Model-generated synthetic data (Phase 2+) will reuse the same verifier gate.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..luau import check_structure, lint
from .corpus import Document

NOUNS = ["Coins", "Gems", "Score", "Wins", "Kills", "Energy", "Stamina", "Xp", "Tokens", "Crystals"]
ACTIONS = ["Dash", "Heal", "Throw", "Block", "Cast", "Emote", "Craft", "Equip", "Upgrade", "Trade"]
REDUCERS = {
    "sum": ("total", "0", "total += value", "total"),
    "maximum": ("best", "-math.huge", "best = math.max(best, value)", "best"),
    "minimum": ("best", "math.huge", "best = math.min(best, value)", "best"),
    "average": ("total", "0", "total += value", "if #values > 0 then total / #values else 0"),
}


@dataclass
class SyntheticSample:
    task: str
    prompt: str
    solution: str
    broken: str | None = None

    def to_document(self) -> Document:
        text = f"<|user|>{self.prompt}\n<|assistant|>{self.solution}"
        return Document(text, "godmode-synthetic-v0", "MIT", f"synthetic://{self.task}", kind="chat", meta={"task": self.task})


def _reducer(rng: random.Random) -> SyntheticSample:
    op = rng.choice(list(REDUCERS))
    acc, init, step, ret = REDUCERS[op]
    name = rng.choice(["compute", "get", "calc"]) + op.capitalize()
    sol = (
        "--!strict\n"
        f"local function {name}(values: {{ number }}): number\n"
        f"\tlocal {acc} = {init}\n"
        "\tfor _, value in values do\n"
        f"\t\t{step}\n"
        "\tend\n"
        f"\treturn {ret}\n"
        "end\n\n"
        f"return {name}\n"
    )
    return SyntheticSample("reducer", f"Write a strict Luau function `{name}` that returns the {op} of an array of numbers.", sol)


def _validated_remote(rng: random.Random) -> SyntheticSample:
    action = rng.choice(ACTIONS)
    cooldown = rng.choice([0.25, 0.5, 1, 2, 5])
    max_len = rng.choice([16, 32, 64])
    sol = (
        "--!strict\n"
        'local ReplicatedStorage = game:GetService("ReplicatedStorage")\n'
        'local Players = game:GetService("Players")\n\n'
        'local remote = Instance.new("RemoteEvent")\n'
        f'remote.Name = "{action}Request"\n'
        "remote.Parent = ReplicatedStorage\n\n"
        f"local COOLDOWN = {cooldown}\n"
        "local lastUse: { [Player]: number } = {}\n\n"
        "Players.PlayerRemoving:Connect(function(player)\n"
        "\tlastUse[player] = nil\n"
        "end)\n\n"
        "remote.OnServerEvent:Connect(function(player: Player, option: unknown)\n"
        f"\tif typeof(option) ~= \"string\" or #option > {max_len} then\n"
        "\t\treturn\n"
        "\tend\n"
        "\tlocal now = os.clock()\n"
        "\tif now - (lastUse[player] or 0) < COOLDOWN then\n"
        "\t\treturn\n"
        "\tend\n"
        "\tlastUse[player] = now\n"
        f'\tprint(`{{player.Name}} used {action} with {{option}}`)\n'
        "end)\n"
    )
    prompt = (f"Create a server script with a RemoteEvent named {action}Request. Validate that the client argument is a string "
              f"of at most {max_len} characters and enforce a {cooldown}s per-player cooldown.")
    return SyntheticSample("validated_remote", prompt, sol)


def _leaderstats(rng: random.Random) -> SyntheticSample:
    stats = rng.sample(NOUNS, rng.randint(1, 3))
    lines = [
        "--!strict",
        'local Players = game:GetService("Players")',
        "",
        "Players.PlayerAdded:Connect(function(player)",
        '\tlocal leaderstats = Instance.new("Folder")',
        '\tleaderstats.Name = "leaderstats"',
    ]
    for s in stats:
        var = s[0].lower() + s[1:]
        lines += [f'\tlocal {var} = Instance.new("IntValue")', f'\t{var}.Name = "{s}"', f"\t{var}.Parent = leaderstats"]
    lines += ["\tleaderstats.Parent = player", "end)", ""]
    return SyntheticSample("leaderstats", f"Create leaderstats with IntValues: {', '.join(stats)}.", "\n".join(lines))


def _repair(rng: random.Random) -> SyntheticSample:
    base = rng.choice([_reducer, _validated_remote, _leaderstats])(rng)
    good = base.solution
    mode = rng.choice(["neq", "missing_end", "deprecated_wait"])
    if mode == "neq" and "~=" not in good:
        mode = "missing_end"
    if mode == "neq":
        broken, issue = good.replace("~=", "!=", 1), "uses `!=`, which is not a Luau operator"
    elif mode == "deprecated_wait":
        good = good.replace("--!strict\n", "--!strict\ntask.wait(1)\n", 1)
        broken, issue = good.replace("task.wait(1)", "wait(1)", 1), "uses the deprecated global `wait`"
    else:
        idx = good.rfind("end")
        broken, issue = good[:idx] + good[idx + 3 :], "is missing an `end`"
    prompt = f"This Luau code {issue}. Fix it.\n```lua\n{broken}```"
    return SyntheticSample(f"repair_{mode}", prompt, good, broken)


GENERATORS = [_reducer, _validated_remote, _leaderstats, _repair]


def is_clean(src: str) -> bool:
    return check_structure(src).ok and not lint(src)


def verify(sample: SyntheticSample) -> bool:
    if not is_clean(sample.solution):
        return False
    if sample.broken is not None and is_clean(sample.broken):
        return False  # the "bug" was not detectably a bug
    return True


def generate(n: int, seed: int = 0) -> tuple[list[SyntheticSample], int]:
    """Return (verified samples, number rejected by verification)."""
    rng = random.Random(seed)
    kept, rejected, seen = [], 0, set()
    attempts = 0
    while len(kept) < n and attempts < n * 20:
        attempts += 1
        sample = rng.choice(GENERATORS)(rng)
        key = sample.prompt + sample.solution
        if key in seen:
            continue
        seen.add(key)
        if verify(sample):
            kept.append(sample)
        else:
            rejected += 1
    return kept, rejected
