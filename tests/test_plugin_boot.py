"""Boot and drive the whole Studio plugin inside the strict Roblox mock.

These run the real plugin source (translated to Lua 5.4) against ``roblox_mock.lua``.
They are NOT Roblox Studio: they prove the plugin's own logic boots, wires its UI,
records undo waypoints and cleans up, against our mock's model of the API.
"""

from pathlib import Path

import pytest

from godmode.luau import lua54

pytestmark = pytest.mark.skipif(not lua54.available(), reason="liblua 5.4 not installed")

SRC = Path(__file__).resolve().parents[1] / "plugin" / "src"

HELPERS = '''
W = __mock.widgets[1]
TB = __mock.toolbars[1]:GetChildren()[1]
function SEND(t)
  local i = W:FindFirstChild("Input", true); i.Text = t
  __mock.fire(W:FindFirstChild("Send", true).MouseButton1Click); __mock.flush(); __mock.advance(1)
end
function BTN(t)
  local last
  for _, d in ipairs(W:GetDescendants()) do
    if d.ClassName == "TextButton" and d.Text == t then last = d end
  end
  return last
end
function TEXTS()
  local out = {}
  for _, d in ipairs(W:GetDescendants()) do
    if (d.ClassName == "TextLabel" or d.ClassName == "TextButton") and d.Text ~= "" then out[#out + 1] = d.Text end
  end
  return out
end
'''


@pytest.fixture
def rt():
    r = lua54.PluginRuntime(SRC)
    r.run(HELPERS)
    yield r
    r.close()


def open_widget(rt):
    rt.run("__mock.click(TB); __mock.flush()")


def test_boot_is_clean_with_one_toolbar_and_one_hidden_widget(rt):
    out = rt.json('''return {errors=__mock.errors, tb=#__mock.toolbars, w=#__mock.widgets,
        enabled=W.Enabled, dep=__mock.deprecated}''')
    assert out["errors"] == [] and out["tb"] == 1 and out["w"] == 1
    assert out["enabled"] is False
    # the plugin must use CreateDockWidgetPluginGuiAsync, not the deprecated sync call
    assert "Plugin.CreateDockWidgetPluginGui" not in out["dep"]


def test_toolbar_button_toggles_widget_and_does_not_leak_connections(rt):
    open_widget(rt)
    first = rt.json("return {en=W.Enabled, active=__mock.button_active(TB), conns=__mock.stats.connections}")
    assert first["en"] is True and first["active"] is True
    rt.run("__mock.click(TB); __mock.flush()")
    assert rt.json("return {en=W.Enabled, active=__mock.button_active(TB)}") == {"en": False, "active": False}
    for _ in range(10):
        rt.run("__mock.click(TB); __mock.flush(); __mock.click(TB); __mock.flush()")
    after = rt.json("return {conns=__mock.stats.connections, errors=__mock.errors}")
    assert after["errors"] == []
    assert after["conns"] <= first["conns"], (first, after)


def test_health_check_without_a_server_reports_instead_of_crashing(rt):
    open_widget(rt)
    rt.run("__mock.advance(6)")
    out = rt.json("return {errors=__mock.errors, texts=TEXTS()}")
    assert out["errors"] == []
    assert any("unreachable" in t.lower() or "offline" in t.lower() for t in out["texts"]), out["texts"]


def test_offline_build_apply_undo_redo_preserves_user_content(rt):
    open_widget(rt)
    out = rt.json('''
      local keep = Instance.new("Part"); keep.Name = "UserPart"; keep.Parent = workspace
      SEND("build a house")
      local input = W:FindFirstChild("Input", true).Text
      local pending = #workspace:GetChildren()
      __mock.fire(BTN("Apply").MouseButton1Click); __mock.flush(); __mock.advance(1)
      local names = {}; for _, c in ipairs(workspace:GetChildren()) do names[#names + 1] = c.Name end
      local built = #workspace:GetDescendants()
      local CHS = game:GetService("ChangeHistoryService")
      CHS:Undo(); local undone = {}; for _, c in ipairs(workspace:GetChildren()) do undone[#undone + 1] = c.Name end
      CHS:Redo(); local redone = #workspace:GetDescendants()
      return {errors=__mock.errors, input=input, pending=pending, names=names, built=built,
              undone=undone, redone=redone, hist=__mock.history, unrec=#__mock.unrecorded}
    ''')
    assert out["errors"] == []
    assert out["input"] == ""            # input cleared after send
    assert out["pending"] == 1           # nothing built before Apply
    assert sorted(out["names"]) == ["GodmodeHouse", "UserPart"]
    assert out["built"] > 5
    assert out["hist"][0]["op"] == "Commit" and out["hist"][0]["added"] > 0
    assert out["undone"] == ["UserPart"]   # undo removes only the AI's build
    assert out["redone"] == out["built"]
    assert out["unrec"] == 0               # every change went through a waypoint


def test_discard_builds_nothing(rt):
    open_widget(rt)
    out = rt.json('''
      SEND("build a tower")
      __mock.fire(BTN("Discard").MouseButton1Click); __mock.flush(); __mock.advance(1)
      return {errors=__mock.errors, ws=#workspace:GetChildren(), hist=#__mock.history, apply=BTN("Apply") ~= nil}
    ''')
    assert out == {"errors": [], "ws": 0, "hist": 0, "apply": False}


def test_empty_message_is_not_sent(rt):
    open_widget(rt)
    out = rt.json('''
      local before = #TEXTS()
      SEND("   ")
      SEND("")
      return {errors=__mock.errors, same=#TEXTS() == before}
    ''')
    assert out == {"errors": [], "same": True}


def test_unload_stops_background_loops(rt):
    open_widget(rt)
    rt.run("__mock.advance(6)")
    before = rt.json("return __mock.pending_sleepers()")
    rt.run("__mock.unload(); __mock.advance(12)")
    after = rt.json("return {n=__mock.pending_sleepers(), errors=__mock.errors}")
    assert after["errors"] == []
    assert after["n"] < before, (before, after)


SAVE_URL = '''
  return function(url)
    __mock.fire(BTN("⚙").MouseButton1Click); __mock.flush()
    for _, d in ipairs(W:GetDescendants()) do
      if d.ClassName == "TextBox" and d.PlaceholderText == "http://127.0.0.1:8765" then d.Text = url end
    end
    __mock.fire(BTN("Save").MouseButton1Click); __mock.flush(); __mock.advance(1)
    local warned = false
    for _, t in ipairs(TEXTS()) do if t:find("is not on this computer:", 1, true) then warned = true end end
    return warned
  end
'''


@pytest.mark.parametrize("url,warned", [
    ("http://[::1]:8765", False),
    ("http://127.0.0.1:8765", False),
    ("http://localhost:8765", False),
    ("http://example.com:8765", True),
])
def test_saving_a_server_url_warns_only_for_remote_hosts(rt, url, warned):
    open_widget(rt)
    rt.run("SAVE = (function() " + SAVE_URL + " end)()")
    out = rt.json(f"local w = SAVE({lua_str(url)}); return {{errors=__mock.errors, warned=w}}")
    assert out == {"errors": [], "warned": warned}


def lua_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_offline_animation_apply_registers_a_preview_id(rt):
    open_widget(rt)
    out = rt.json('''
      SEND("make an idle animation")
      -- every proposed action has its own card; apply them all in order
      local applies = {}
      for _, d in ipairs(W:GetDescendants()) do
        if d.ClassName == "TextButton" and d.Text == "Apply" then applies[#applies + 1] = d end
      end
      for _, b in ipairs(applies) do __mock.fire(b.MouseButton1Click); __mock.flush(); __mock.advance(1) end
      local preview, failed, notapplied = false, false, false
      for _, t in ipairs(TEXTS()) do
        if t:find("preview id", 1, true) then preview = true end
        if t:find("could not register", 1, true) then failed = true end
        if t:find("Did NOT apply", 1, true) then notapplied = true end
      end
      local sp = game:GetService("StarterPlayer")
      local script = sp:FindFirstChild("StarterCharacterScripts") and sp.StarterCharacterScripts:FindFirstChild("PlayGodmodeIdle")
      return {errors=__mock.errors, cards=#applies, preview=preview, failed=failed, notapplied=notapplied,
              script=script ~= nil}
    ''')
    assert out == {"errors": [], "cards": 2, "preview": True, "failed": False, "notapplied": False, "script": True}


def test_double_click_apply_builds_once(rt):
    open_widget(rt)
    out = rt.json('''
      SEND("build a house")
      local b = BTN("Apply")
      __mock.fire(b.MouseButton1Click); __mock.fire(b.MouseButton1Click); __mock.flush(); __mock.advance(1)
      __mock.fire(b.MouseButton1Click); __mock.flush(); __mock.advance(1)
      local houses = 0
      for _, c in ipairs(workspace:GetChildren()) do if c.Name == "GodmodeHouse" then houses = houses + 1 end end
      return {errors=__mock.errors, houses=houses, hist=#__mock.history}
    ''')
    assert out == {"errors": [], "houses": 1, "hist": 1}


def test_closing_and_reopening_keeps_a_pending_card_usable(rt):
    open_widget(rt)
    rt.run('SEND("build a tower")')
    before = rt.json("return __mock.stats.connections")
    for _ in range(5):
        rt.run("__mock.click(TB); __mock.flush(); __mock.click(TB); __mock.flush()")
    out = rt.json('''
      local conns = __mock.stats.connections
      __mock.fire(BTN("Apply").MouseButton1Click); __mock.flush(); __mock.advance(1)
      return {errors=__mock.errors, en=W.Enabled, conns=conns, ws=#workspace:GetChildren(), hist=#__mock.history}
    ''')
    assert out["errors"] == [] and out["en"] is True
    assert out["conns"] <= before, (before, out)
    assert out["ws"] == 1 and out["hist"] == 1


def test_offline_gui_apply_creates_a_real_screengui_and_undo_removes_it(rt):
    open_widget(rt)
    out = rt.json('''
      SEND("make a main menu gui")
      local applies = 0
      for _, d in ipairs(W:GetDescendants()) do
        if d.ClassName == "TextButton" and d.Text == "Apply" then applies = applies + 1 end
      end
      __mock.fire(BTN("Apply").MouseButton1Click); __mock.flush(); __mock.advance(1)
      local SG = game:GetService("StarterGui")
      local gui = SG:FindFirstChild("MainMenuGui")
      local buttons, scripts, n = 0, 0, 0
      if gui then
        for _, d in ipairs(gui:GetDescendants()) do
          n = n + 1
          if d.ClassName == "TextButton" then buttons = buttons + 1 end
          if d:IsA("LuaSourceContainer") then scripts = scripts + 1 end
        end
      end
      local cls = gui and gui.ClassName
      game:GetService("ChangeHistoryService"):Undo()
      return {errors=__mock.errors, applies=applies, cls=cls, buttons=buttons, scripts=scripts, n=n,
              gone=SG:FindFirstChild("MainMenuGui") == nil, unrec=#__mock.unrecorded}
    ''')
    assert out == {"errors": [], "applies": 1, "cls": "ScreenGui", "buttons": 3, "scripts": 0,
                   "n": 17, "gone": True, "unrec": 0}
