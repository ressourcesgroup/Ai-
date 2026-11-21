"""Request-relevance check on model candidates (EN/FR/ES) and FR/ES animation routing."""

from godmode.server.model_backend import ModelBackend, relevance_problems
from godmode.tools.router import route

COIN_SCRIPT = (
    "local Players = game:GetService(\"Players\")\n"
    "local part = script.Parent\n"
    "part.Touched:Connect(function(hit)\n"
    "\tlocal player = Players:GetPlayerFromCharacter(hit.Parent)\n"
    "\tif player then\n"
    "\t\tlocal coins = player.leaderstats.Coins\n"
    "\t\tcoins.Value += 1\n"
    "\tend\n"
    "end)\n"
)
SPEED_SCRIPT = COIN_SCRIPT.replace(
    "\t\tlocal coins = player.leaderstats.Coins\n\t\tcoins.Value += 1\n",
    "\t\tlocal hum = hit.Parent:FindFirstChildOfClass(\"Humanoid\")\n\t\tif hum then hum.WalkSpeed = 32 end\n")


def test_relevance_rejects_working_code_for_a_different_request():
    missing, checked = relevance_problems("give players a speed boost when they touch a part", COIN_SCRIPT)
    assert checked == ["speed", "touch"] and missing == ["speed"]
    assert relevance_problems("give players a speed boost when they touch a part", SPEED_SCRIPT)[0] == []


def test_relevance_understands_french_and_spanish():
    assert relevance_problems("donne plus de vitesse au joueur quand il touche la pièce", COIN_SCRIPT)[0] == ["speed"]
    assert relevance_problems("aumenta la velocidad del jugador cuando toca la parte", SPEED_SCRIPT)[0] == []
    missing, checked = relevance_problems("guarda las monedas del jugador", COIN_SCRIPT)
    assert "datastore" in checked and missing == ["datastore"]
    ds = "local DataStoreService = game:GetService(\"DataStoreService\")\nlocal coins = 0\n"
    assert relevance_problems("sauvegarde les pièces du joueur", ds)[0] == []


def test_relevance_is_unjudged_not_passed_when_request_names_no_known_concept():
    assert relevance_problems("make something cool", COIN_SCRIPT) == ([], [])


class _Tok:
    def encode(self, s, allow_special=False):
        return [0]

    def decode(self, c):
        return c


class _CannedModel(ModelBackend):
    """Real generate_code verification loop; only sampling is replaced by canned candidates."""

    def __init__(self, candidates):
        super().__init__("fake.pt")
        self.prompt_style, self._tok, self._model, self._cands = "chat", _Tok(), object(), candidates

    def _load(self):
        return True

    def _sample(self, ids, k, max_new, t, gen):
        return self._cands[:k]


def test_generate_code_rejects_irrelevant_candidates_instead_of_calling_them_verified():
    out = _CannedModel([COIN_SCRIPT]).generate_code("give players a speed boost when they touch a part")
    assert not out.ok and out.code is None
    rel = [c for c in out.verification if c["check"] == "relevance:model_candidate"]
    assert rel and not rel[0]["ok"] and "speed" in rel[0]["detail"]
    out = _CannedModel([SPEED_SCRIPT]).generate_code("give players a speed boost when they touch a part")
    assert out.ok and "WalkSpeed" in out.code


def test_french_and_spanish_animation_requests_route_to_the_animation_tool():
    i = route("fais une animation de danse")
    assert i.name == "animation" and i.params["style"] == "dance"
    i = route("crea una animación de saltar")
    assert i.name == "animation" and i.params["style"] == "jump"
    assert route("crea un jump pad").name != "animation"
    assert route("build a house with a door").name == "build_house"


def test_header_topic_rejects_a_memorised_script_for_another_task():
    sword = "-- Script in ServerScriptService: classicsword tool\nlocal h = Instance.new(\"Part\")\nh.Color = Color3.new(1, 0, 0)\ntask.wait(1)\n"
    missing, _ = relevance_problems("escribe un script que cambia el color de una parte cada segundo", sword)
    assert missing == ["off-topic sword script"]
    assert relevance_problems("haz una espada que cambia de color cada segundo", sword)[0] == []
    door = "-- Script in ServerScriptService: tween door\nlocal TweenService = game:GetService(\"TweenService\")\n"
    assert relevance_problems("crea una puerta que se abre con tween", door)[0] == []


def test_header_topic_rejects_spinning_part_for_a_colour_request():
    spin = "-- Script in ServerScriptService: spinning part\nlocal p = Instance.new(\"Part\")\np.Color = Color3.new(0, 1, 1)\nwhile true do task.wait(1) end\n"
    assert relevance_problems("escribe un script que cambia el color de una parte cada segundo", spin)[0] == ["off-topic spin script"]
    assert relevance_problems("fais une partie qui tourne", spin)[0] == []
