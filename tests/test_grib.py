from godmode.data import load_source_dir
from godmode.eval import grib

TASKS = grib.load_tasks("benchmarks/grib/tasks_v0.jsonl")


def test_every_reference_solution_passes_its_own_checks():
    report = grib.run(TASKS, grib.reference_adapter(TASKS), "reference")
    failing = [r for r in report["results"] if not r["passed"]]
    assert report["pass_rate"] == 1.0, failing


def test_empty_answers_score_zero():
    assert grib.run(TASKS, grib.empty_adapter, "empty")["pass_rate"] == 0.0


def test_adapter_crash_is_a_task_failure():
    def boom(_):
        raise RuntimeError("x")

    assert grib.run(TASKS[:2], boom, "boom")["pass_rate"] == 0.0


def test_insecure_answer_flagged():
    task = next(t for t in TASKS if t.id == "grib-i-001")
    insecure = 'remote.OnServerEvent:Connect(function(player, emote)\n\tprint("Wave", emote)\nend)'
    r = grib.grade(task, insecure)
    assert not r.passed and r.security_failure


def test_extracts_fenced_code():
    assert grib.extract_code("Here:\n```lua\nprint(1)\n```\nDone") == "print(1)\n"


def test_contamination_detection():
    texts = [d.text for d in load_source_dir("data/seed_corpus")]
    report = grib.contamination_report(TASKS, texts)
    assert set(report) == {t.id for t in TASKS}
    exact = grib.contamination_report(TASKS[:1], [TASKS[0].reference])
    assert exact[TASKS[0].id] == 1.0
    clean = grib.run(TASKS, grib.reference_adapter(TASKS), "ref", grib.contaminated_ids(report))
    assert clean["pass_rate_clean"] == 1.0


# ---- grader hardening ----------------------------------------------------

BY_ID = {t.id: t for t in TASKS}


def _fails(task_id: str, answer: str) -> bool:
    return not grib.grade(BY_ID[task_id], answer).passed


def _passes(task_id: str, answer: str) -> bool:
    r = grib.grade(BY_ID[task_id], answer)
    return r.passed or print(task_id, r.failed_checks)


def test_task_ids_unique_and_tiers_valid():
    assert len({t.id for t in TASKS}) == len(TASKS) >= 20
    assert {t.tier for t in TASKS} == {"beginner", "intermediate", "advanced", "expert"}


def test_strip_comments_preserves_lines_and_directives():
    src = '--!strict\nlocal a = "--not a comment" -- task.wait\n--[[ multi\nline ]] print(a)\n'
    out = grib.strip_comments(src)
    assert out.count("\n") == src.count("\n")
    assert "--!strict" in out and "--not a comment" in out
    assert "task.wait" not in out and "multi" not in out
    assert grib.strip_comments('local s = "unterminated') == 'local s = "unterminated'


def test_comment_only_satisfaction_fails():
    answer = (
        '-- local Players = game:GetService("Players")\n'
        "-- Players.PlayerAdded:Connect(function(p) print(p.Name) end)\n"
        'local greeting = "hello"\nprint(greeting, 1 + 2)\n'
    )
    assert _fails("grib-b-002", answer)
    # ...and a forbidden word inside a comment is not held against an answer.
    assert _passes("grib-e-002", BY_ID["grib-e-002"].reference + "-- never call loadstring(source) again\n")


def test_b003_rewrite_without_not_equal_passes():
    answer = "local function sign(x: number): number\n\tif x == 0 then\n\t\treturn 0\n\tend\n\treturn if x > 0 then 1 else -1\nend\n\nreturn sign\n"
    assert _passes("grib-b-003", answer)
    assert _fails("grib-b-003", answer.replace("x == 0", "x != 0"))


def test_b005_global_wait_fails():
    assert _fails("grib-b-005", 'while true do\n\ttask.wait() wait(1)\n\tprint("tick")\nend\n')


def test_i001_allow_list_must_be_enforced():
    unenforced = (
        'local ReplicatedStorage = game:GetService("ReplicatedStorage")\n'
        "local ALLOWED = { Wave = true, Dance = true, Cheer = true }\n"
        'local remote = Instance.new("RemoteEvent")\nremote.Name = "RequestEmote"\nremote.Parent = ReplicatedStorage\n'
        "remote.OnServerEvent:Connect(function(player, emote)\n"
        '\tif typeof(emote) ~= "string" then\n\t\treturn\n\tend\n\tprint(player.Name, emote)\nend)\n'
    )
    assert _fails("grib-i-001", unenforced)


def test_i002_failed_load_must_not_return_zero():
    old_reference = (
        'local DataStoreService = game:GetService("DataStoreService")\n'
        'local store = DataStoreService:GetDataStore("Coins")\n'
        "local function loadCoins(player: Player): number\n"
        "\tlocal ok, value = pcall(function()\n\t\treturn store:GetAsync(tostring(player.UserId))\n\tend)\n"
        '\tif ok and type(value) == "number" then\n\t\treturn value\n\tend\n\treturn 0\nend\n\nreturn loadCoins\n'
    )
    assert _fails("grib-i-002", old_reference)
    assert "GetDataStore(STORE_NAME)" in BY_ID["grib-i-002"].reference


def test_i003_requires_delta_time_and_no_unbounded_wait():
    ref = BY_ID["grib-i-003"].reference
    assert _fails("grib-i-003", ref.replace("deltaTime", "width"))
    hanging = (
        'local RunService = game:GetService("RunService")\n'
        'local spinner = workspace:WaitForChild("Spinner") :: BasePart\n'
        "local connection\nconnection = RunService.Heartbeat:Connect(function(dt)\n"
        "\tspinner.CFrame *= CFrame.Angles(0, dt, 0)\nend)\n"
        "spinner.AncestryChanged:Connect(function()\n\tconnection:Disconnect()\nend)\n"
    )
    assert _fails("grib-i-003", hanging)


def _a001(body: str) -> str:
    return (
        'local Players = game:GetService("Players")\n'
        'local remote = game:GetService("ReplicatedStorage"):WaitForChild("DealDamage") :: RemoteEvent\n'
        "local COOLDOWN = 0.5\nlocal last = {}\n"
        "Players.PlayerRemoving:Connect(function(p)\n\tlast[p] = nil\nend)\n"
        "remote.OnServerEvent:Connect(function(player, target, amount)\n"
        '\tif typeof(target) ~= "Instance" or not target:IsA("Model") then\n\t\treturn\n\tend\n'
        "\tif os.clock() - (last[player] or 0) < COOLDOWN then\n\t\treturn\n\tend\n"
        "\tlast[player] = os.clock()\n"
        '\tlocal mine = player.Character and player.Character:FindFirstChild("HumanoidRootPart")\n'
        '\tlocal theirs = target:FindFirstChild("HumanoidRootPart")\n'
        "\tif not mine or not theirs or (mine.Position - theirs.Position).Magnitude > 10 then\n\t\treturn\n\tend\n"
        '\tlocal humanoid = target:FindFirstChildOfClass("Humanoid")\n'
        f"\tif humanoid then\n{body}\n\tend\nend)\n"
    )


def test_a001_client_amount_in_any_form_fails():
    assert _passes("grib-a-001", _a001("\t\thumanoid:TakeDamage(10)"))
    assert _fails("grib-a-001", _a001("\t\thumanoid:TakeDamage(math.clamp(tonumber(amount) or 0, 0, 10))"))
    assert _fails("grib-a-001", _a001("\t\thumanoid.Health = humanoid.Health - amount"))
    assert _fails("grib-a-001", _a001("\t\tlocal dmg = amount\n\t\thumanoid:TakeDamage(dmg)"))
    no_cooldown = _a001("\t\thumanoid:TakeDamage(10)").replace("COOLDOWN", "GAP").replace("os.clock()", "0")
    assert _fails("grib-a-001", no_cooldown)


def test_a002_close_without_waiting_fails():
    ref = BY_ID["grib-a-002"].reference
    head = ref[: ref.index("game:BindToClose")]
    fire_and_forget = head + "game:BindToClose(function()\n\tfor player in profiles do\n\t\ttask.spawn(finalSave, player)\n\tend\nend)\n"
    assert _fails("grib-a-002", fire_and_forget)


def test_a003_limit_must_be_enforced():
    ref = BY_ID["grib-a-003"].reference
    assert _fails("grib-a-003", ref.replace("\tif #recent >= LIMIT then\n\t\treturn nil\n\tend\n", ""))
    module_based = (
        'local ReplicatedStorage = game:GetService("ReplicatedStorage")\n'
        "local RateLimiter = require(script.Parent.RateLimiter)\n"
        "local limiter = RateLimiter.new(3, 0.3)\n"
        'local remote = Instance.new("RemoteFunction")\nremote.Name = "GetQuote"\nremote.Parent = ReplicatedStorage\n'
        "-- RateLimiter disconnects its own PlayerRemoving cleanup in :Destroy().\n"
        "game:GetService(\"Players\").PlayerRemoving:Connect(function() end)\n"
        "remote.OnServerInvoke = function(player)\n\tif not limiter:Allow(player) then return nil end\n"
        '\treturn "Stay curious."\nend\n'
    )
    assert _passes("grib-a-003", module_based)


def test_e001_leaky_connection_fails():
    leaky = BY_ID["grib-e-001"].reference
    leaky = leaky[: leaky.index("function CooldownService.Destroy")] + "return CooldownService\n"
    leaky = leaky.replace("self.removing = Players", "Players")
    assert _fails("grib-e-001", leaky)


def test_e002_env_trick_fails():
    trick = (
        "local remote = game:GetService(\"ReplicatedStorage\").RunCode\n"
        "remote.OnServerEvent:Connect(function(player, source)\n"
        '\tif typeof(source) ~= "string" then\n\t\treturn\n\tend\n'
        '\tgetfenv()["load" .. "string"](source)()\nend)\n'
    )
    assert _fails("grib-e-002", trick)


# Each new task: an answer that looks plausible but has the targeted bug must fail.
WRONG_ANSWERS = {
    "grib-b-006": "script.Parent.Touched:Connect(function(hit)\n\thit.Parent.Humanoid.Health -= 10\nend)\n",
    "grib-i-005": (
        'local ReplicatedStorage = game:GetService("ReplicatedStorage")\n'
        'local shout = Instance.new("RemoteEvent")\nshout.Name = "ShoutMessage"\nshout.Parent = ReplicatedStorage\n'
        "shout.OnServerEvent:Connect(function(player, message)\n"
        '\tif typeof(message) == "string" and #message <= 100 then\n'
        "\t\tshout:FireAllClients(player.UserId, message) -- unfiltered\n\tend\nend)\n"
    ),
    "grib-i-006": (
        'local AskReady = game:GetService("ReplicatedStorage"):WaitForChild("AskReady") :: RemoteFunction\n'
        "local function askReady(player: Player): boolean\n"
        "\tlocal ok, result = pcall(AskReady.InvokeClient, AskReady, player)\n"
        '\treturn ok and typeof(result) == "boolean" and result\nend\nreturn askReady\n'
    ),
    "grib-a-004": (
        'local Players = game:GetService("Players")\nlocal RunService = game:GetService("RunService")\n'
        "local player = Players.LocalPlayer\nlocal last\n"
        "RunService.Heartbeat:Connect(function(dt)\n"
        '\tlocal root = player.Character and player.Character:FindFirstChild("HumanoidRootPart")\n'
        "\tif root and last and (root.Position - last).Magnitude > 16 * dt * 2 then\n"
        "\t\troot.CFrame = CFrame.new(last)\n\tend\n\tlast = root and root.Position\nend)\n"
    ),
    "grib-a-005": (
        "local TradeService = require(script.Parent.TradeService)\n"
        'game:GetService("ReplicatedStorage").TradeAccept.OnServerEvent:Connect(function(player, tradeId)\n'
        '\tif typeof(tradeId) ~= "string" then\n\t\treturn\n\tend\n'
        "\tlocal trade = TradeService.trades[tradeId]\n"
        "\tif not trade or (player ~= trade.a and player ~= trade.b) then\n\t\treturn\n\tend\n"
        "\ttrade.accepted[player] = true\n"
        "\tif trade.accepted[trade.a] and trade.accepted[trade.b] and TradeService.stillOwnsItems(trade) then\n"
        "\t\tTradeService.saveInventories(trade)\n\t\tTradeService.giveItems(trade)\n"
        "\t\tTradeService.trades[tradeId] = nil\n\tend\nend)\n"
    ),
    "grib-e-003": (
        "--!strict\nlocal remote = game:GetService(\"ReplicatedStorage\"):WaitForChild(\"FireWeapon\") :: RemoteEvent\n"
        "remote.OnServerEvent:Connect(function(player: Player, victim: unknown)\n"
        '\tif typeof(victim) == "Instance" and victim:IsA("Humanoid") then\n'
        "\t\tvictim:TakeDamage(20)\n\tend\nend)\n"
    ),
    "grib-e-004": (
        'local Players = game:GetService("Players")\n'
        'game:GetService("ReplicatedStorage").AdminCommand.OnServerEvent:Connect(function(player, command, targetName)\n'
        '\tif typeof(command) ~= "string" or typeof(targetName) ~= "string" then\n\t\treturn\n\tend\n'
        '\tif player.Name == "Builderman" then\n'
        "\t\tlocal target = Players:FindFirstChild(targetName)\n"
        "\t\tif target then\n\t\t\ttarget[command](target)\n\t\tend\n\tend\nend)\n"
    ),
}


def test_new_task_wrong_answers_fail():
    for task_id, answer in WRONG_ANSWERS.items():
        assert task_id in BY_ID
        assert grib.check_structure(answer).ok, task_id  # the bug is semantic, not syntactic
        assert _fails(task_id, answer), task_id


def test_short_ngram_signal_catches_templated_answers():
    import re

    src = open("data/seed_corpus/CombatServer.server.luau").read()
    words = re.findall(r"\w+|[^\w\s]", src)
    templated = " ".join(f"X{i}" if i % 13 == 0 else w for i, w in enumerate(words))
    task = grib.Task("t", "expert", "c", "p", [], templated)
    assert grib.contamination_report([task], [src], short_n=None)["t"] == 0.0  # 13-grams alone miss it
    assert grib.contaminated_ids(grib.contamination_report([task], [src])) == {"t"}
