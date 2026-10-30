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
