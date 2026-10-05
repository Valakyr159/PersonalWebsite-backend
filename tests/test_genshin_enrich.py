"""Banner characters missing from the tier list/teams get their role and teams from their own character page."""
import asyncio
import json
import os

os.environ.setdefault("GROQ_API_KEY", "test-key")

import httpx
import pytest

from src.mcp_server import genshin

REAL_FETCH_NEW = genshin.fetch_new_characters  # the real one, for the tests that exercise it
REAL_CLIENT = httpx.AsyncClient  # captured before any test patches it (patching twice would stack transports)


@pytest.fixture(autouse=True)
def no_waiting():
    async def instant(_seconds):
        pass

    genshin._sleep = instant
    genshin.fetch_new_characters = REAL_FETCH_NEW
    yield
    genshin._sleep = asyncio.sleep
    genshin.fetch_new_characters = REAL_FETCH_NEW


by = lambda name: genshin.ROSTER_BY_NAME[name.casefold()]["id"]
VESNA, VODY, ODETTE, FARUZAN = by("Vesna"), by("Vodyanitsa"), by("Odette"), by("Faruzan")


def base_meta():
    """What the main pass produced: Vesna is a banner but is on neither the tier list nor any team."""
    def team(name, names):
        return {"name": name, "reaction": "r", "tier": "S", "note": "", "members": [{"id": by(n), "role": "Support"} for n in names]}
    teams = [team("T1", ["Mavuika", "Xilonen", "Citlali", "Bennett"]), team("T2", ["Flins", "Columbina", "Ineffa", "Sucrose"]),
             team("T3", ["Skirk", "Escoffier", "Mona", "Furina"])]
    chars = [{"id": by(n), "role": "Main DPS" if n in ("Mavuika", "Flins", "Skirk") else "Support", "tier": "S"}
             for n in ("Mavuika", "Flins", "Skirk", "Xilonen", "Citlali", "Bennett", "Furina", "Sucrose", "Odette")]
    return {"patch": "7.1", "characters": chars, "teams": teams, "banners": [VESNA, VODY], "sources": [], "droppedTeams": 0}


def page(key):
    return genshin.CHARACTER_PAGE.format(key=key)


def good_entry(name="Vesna", role="Main DPS", members=("Vesna", "Odette", "Faruzan", "Vodyanitsa")):
    return {"name": name, "role": role, "teams": [{"name": "Stellar Swirl Vesna", "reaction": "Swirl", "members": list(members)}]}


# ---------- uncovered_candidates ----------

def test_candidates_already_covered_by_the_tier_list_or_a_team_are_not_enriched():
    meta = base_meta()
    meta["banners"] = [by("Skirk"), by("Bennett")]  # one in the tier list and teams, one in a team
    assert genshin.uncovered_candidates(meta) == []


def test_only_candidates_with_no_data_at_all_are_uncovered_and_capped():
    meta = base_meta()
    assert genshin.uncovered_candidates(meta) == [VESNA, VODY]
    meta["banners"] = [by(n) for n in ("Vesna", "Vodyanitsa", "Aloy", "Albedo", "Ganyu", "Keqing")]
    assert len(genshin.uncovered_candidates(meta)) == genshin.MAX_ENRICH


def test_new_characters_count_as_candidates_even_when_the_model_missed_them_as_banners():
    """The model listed only two banners (it does, ~half the time); genshin.gg's NEW badge still reveals Vesna and Vodyanitsa."""
    meta = base_meta()
    meta["banners"] = [by("Skirk")]
    meta["newCharacters"] = [VESNA, VODY, by("Skirk")]
    assert genshin.uncovered_candidates(meta) == [VESNA, VODY]  # Skirk is covered; order and uniqueness kept


# ---------- parse_new_characters / fetch_new_characters ----------

LIST_HTML = (
    '<a href="/characters/venti/" class="character-portrait"><img alt="Venti"><h2 class="character-name">Venti</h2></a>'
    '<a href="/characters/vesna/" class="character-portrait character-new"><img alt="Vesna" class="character-icon rarity-5">'
    '<h2 class="character-name">Vesna</h2><div class="new">NEW</div></a>'
    '<a href="/characters/vodyanitsa/" class="character-portrait character-new"><img alt="Vodyanitsa">'
    '<h2 class="character-name">Vodyanitsa</h2><div class="new">NEW</div></a>'
    '<a href="/characters/ayaka/" class="character-portrait character-new"><h2 class="character-name">Kamisato Ayaka</h2></a>'
    '<a href="/characters/not-a-real-one/" class="character-portrait character-new"><h2 class="character-name">Totally Made Up</h2></a>'
)


def test_parse_new_characters_reads_only_the_marked_ones_and_keeps_the_sites_slug():
    found = genshin.parse_new_characters(LIST_HTML)
    assert found[VESNA] == "vesna" and found[VODY] == "vodyanitsa"
    assert by("Kamisato Ayaka") in found and found[by("Kamisato Ayaka")] == "ayaka"  # the site's slug differs from our key
    assert by("Venti") not in found                                                   # no NEW badge
    assert len(found) == 3                                                            # unknown names are ignored


def test_parse_new_characters_survives_garbage_and_empty_pages():
    assert genshin.parse_new_characters("") == {}
    assert genshin.parse_new_characters("<html>no characters here</html>") == {}
    assert genshin.parse_new_characters('<a href="/characters/x/" class="character-new">no name</a>') == {}


def test_fetch_new_characters_is_one_plain_get_with_an_identifying_user_agent(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers["user-agent"]))
        return httpx.Response(200, text=LIST_HTML)

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    found = asyncio.run(REAL_FETCH_NEW())
    assert seen == [("GET", genshin.CHARACTER_LIST_URL, genshin.ENKA_USER_AGENT)] and VESNA in found


def test_fetch_new_characters_retries_a_network_blip_and_then_succeeds(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("blip", request=request)
        return httpx.Response(200, text=LIST_HTML)

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    assert VESNA in asyncio.run(REAL_FETCH_NEW()) and len(attempts) == 3


def test_fetch_new_characters_does_not_retry_a_404(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404)

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(REAL_FETCH_NEW()) == {} and len(attempts) == 1


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(404), httpx.ConnectError("down")])
def test_fetch_new_characters_never_raises_it_just_returns_nothing(monkeypatch, response):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(REAL_FETCH_NEW()) == {}


# ---------- merge_character_pages ----------

def test_merge_adds_role_and_team_flagged_unranked_without_inventing_a_tier_or_roles():
    meta = base_meta()
    added = genshin.merge_character_pages(meta, [good_entry()], {VESNA})
    assert added == 1
    ch = next(c for c in meta["characters"] if c["id"] == VESNA)
    assert ch == {"id": VESNA, "role": "Main DPS", "tier": "A", "unranked": True}
    team = meta["teams"][-1]
    assert team["unranked"] is True and team["tier"] == "A" and team["note"] == genshin.UNRANKED_NOTE
    roles = {m["id"]: m["role"] for m in team["members"]}
    assert roles[VESNA] == "Main DPS"        # from the page
    assert roles[ODETTE] == "Support"        # Odette is in the tier list with role Support: reused
    assert roles[FARUZAN] == "Support"       # unknown anywhere: safe default
    assert len(meta["teams"]) == 4


@pytest.mark.parametrize("bad_members", [
    ("Vesna", "Odette", "Faruzan"),                              # only 3
    ("Vesna", "Odette", "Faruzan", "Totally Made Up"),           # unknown name (hallucination)
    ("Vesna", "Odette", "Odette", "Faruzan"),                    # duplicate collapses to 3
    ("Odette", "Faruzan", "Mona", "Furina"),                     # does not contain the page's own character
])
def test_merge_rejects_teams_that_are_malformed_or_do_not_include_the_page_character(bad_members):
    meta = base_meta()
    genshin.merge_character_pages(meta, [good_entry(members=bad_members)], {VESNA})
    assert len(meta["teams"]) == 3 and all(not t.get("unranked") for t in meta["teams"])


def test_merge_ignores_characters_whose_page_was_not_actually_read():
    meta = base_meta()
    assert genshin.merge_character_pages(meta, [good_entry()], set()) == 0  # url_context reported an error
    assert len(meta["teams"]) == 3 and not any(c.get("unranked") for c in meta["characters"])


def test_merge_never_overrides_a_character_the_main_pass_already_knows():
    meta = base_meta()
    entry = good_entry(name="Skirk", role="Healer", members=("Skirk", "Odette", "Faruzan", "Vodyanitsa"))
    assert genshin.merge_character_pages(meta, [entry], {by("Skirk")}) == 0
    assert next(c for c in meta["characters"] if c["id"] == by("Skirk"))["role"] == "Main DPS"


def test_merge_skips_a_team_already_present_and_survives_garbage():
    meta = base_meta()
    dup = good_entry(members=("Mavuika", "Xilonen", "Citlali", "Bennett"))  # same four as T1, no Vesna
    assert genshin.merge_character_pages(meta, [dup], {VESNA}) == 0
    assert genshin.merge_character_pages(meta, "not a list", {VESNA}) == 0
    garbage = [None, 5, {"name": None}, {"name": "Vesna", "teams": "x"}, {"name": "Vesna", "teams": [None, "y", {"members": "z"}]}]
    assert genshin.merge_character_pages(meta, garbage, {VESNA}) == 0
    assert len(meta["teams"]) == 3 and not any(c.get("unranked") for c in meta["characters"])


def test_a_page_with_a_role_but_no_usable_team_adds_nothing():
    """A role alone would make the character look covered while giving the planner nothing to work with."""
    meta = base_meta()
    entry = {"name": "Vesna", "role": "Main DPS", "teams": []}
    assert genshin.merge_character_pages(meta, [entry], {VESNA}) == 0
    assert VESNA not in {c["id"] for c in meta["characters"]}


def test_an_invalid_role_label_falls_back_to_support_instead_of_being_trusted():
    meta = base_meta()
    genshin.merge_character_pages(meta, [good_entry(role="God Tier Carry")], {VESNA})
    assert next(c for c in meta["characters"] if c["id"] == VESNA)["role"] == "Support"


def test_a_team_found_through_another_characters_page_still_gets_the_right_roles():
    """Vodyanitsa's page lists the Vesna team first: Vesna's seat must use the role from HER page, not the default."""
    meta = base_meta()
    vody = {"name": "Vodyanitsa", "role": "Support", "teams": [
        {"name": "Stellar Swirl Vesna", "reaction": "Swirl", "members": ["Vesna", "Odette", "Faruzan", "Vodyanitsa"]}]}
    added = genshin.merge_character_pages(meta, [vody, good_entry()], {VESNA, VODY})  # Vodyanitsa processed first
    assert added == 2 and len(meta["teams"]) == 4  # the same team is not added twice
    roles = {m["id"]: m["role"] for m in meta["teams"][-1]["members"]}
    assert roles[VESNA] == "Main DPS" and roles[VODY] == "Support"
    registered = {c["id"]: c["role"] for c in meta["characters"] if c.get("unranked")}
    assert registered == {VESNA: "Main DPS", VODY: "Support"}  # Vesna is covered even though her own team was a duplicate


def test_the_same_character_listed_twice_is_only_added_once():
    meta = base_meta()
    assert genshin.merge_character_pages(meta, [good_entry(), good_entry()], {VESNA}) == 1
    assert [c["id"] for c in meta["characters"]].count(VESNA) == 1


# ---------- fetch_meta end to end (mocked Gemini) ----------

def reply(payload, read_urls):
    return httpx.Response(200, json={"candidates": [{
        "content": {"parts": [{"text": "```json\n" + json.dumps(payload) + "\n```"}]},
        "urlContextMetadata": {"urlMetadata": [{"retrievedUrl": u, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"} for u in read_urls]}}]})


def raw_base():
    """The main pass as the model writes it (names, not ids)."""
    names = {i: n for n, i in ((c["name"]["en"], c["id"]) for c in genshin.ROSTER)}
    meta = base_meta()
    return {"patch": "7.1",
            "characters": [{"name": names[c["id"]], "role": c["role"], "tier": c["tier"]} for c in meta["characters"]],
            "teams": [{"name": t["name"], "reaction": "r", "tier": "S", "note": "",
                       "members": [{"name": names[m["id"]], "role": "Support"} for m in t["members"]]} for t in meta["teams"]],
            "banners": [{"name": "Vesna"}, {"name": "Vodyanitsa"}]}


class Gemini:
    """Routes the two kinds of request: the main pass and the character-page pass."""
    def __init__(self, enrich_reply=None, enrich_status=200, base=None, list_html=""):
        self.calls, self.enrich_reply, self.enrich_status, self.base = [], enrich_reply, enrich_status, base or raw_base()
        self.list_html = list_html

    def __call__(self, request):
        if request.url.host == "genshin.gg":  # the plain GET for the NEW badges
            self.calls.append("list")
            return httpx.Response(200, text=self.list_html)
        prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        if "character pages" in prompt:
            self.calls.append("enrich")
            if self.enrich_status != 200:
                return httpx.Response(self.enrich_status)
            return reply(self.enrich_reply, [page("vesna"), page("vodyanitsa")])
        self.calls.append("main")
        return reply(self.base, ["https://genshin.gg/tier-list/", "https://genshin.gg/teams/"])


def run_fetch(monkeypatch, gemini):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_META_MODELS", "big,small")
    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(gemini), **kw))
    return asyncio.run(genshin.fetch_meta())


def test_fetch_meta_gives_banner_characters_a_role_and_teams_from_their_pages(monkeypatch):
    g = Gemini(enrich_reply=[good_entry(), {"name": "Vodyanitsa", "role": "Support", "teams": [
        {"name": "Skirk Freeze", "reaction": "Freeze", "members": ["Skirk", "Escoffier", "Furina", "Vodyanitsa"]}]}])
    meta = run_fetch(monkeypatch, g)
    assert [c for c in g.calls if c != "list"] == ["main", "enrich"]  # exactly one extra Gemini request
    new_teams = [t for t in meta["teams"] if t.get("unranked")]
    assert {t["name"] for t in new_teams} == {"Stellar Swirl Vesna", "Skirk Freeze"}
    assert {c["id"] for c in meta["characters"] if c.get("unranked")} == {VESNA, VODY}
    assert page("vesna") in {s["url"] for s in meta["sources"]}  # the pages actually read are credited
    assert meta["degraded"] is False and sorted(meta["banners"]) == sorted([VESNA, VODY])


def test_a_new_character_the_model_missed_as_a_banner_is_still_enriched_from_its_site_slug(monkeypatch):
    """The model lists only Skirk (it did so in ~half the real runs); the NEW badge reveals Vesna and Vodyanitsa."""
    base = raw_base()
    base["banners"] = [{"name": "Skirk"}]
    g = Gemini(base=base, list_html=LIST_HTML, enrich_reply=[good_entry()])
    requested = []
    original = g.__call__

    def spy(request):
        if request.url.host != "genshin.gg":
            prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
            if "character pages" in prompt:
                requested.append(prompt)  # remember which pages the enrichment asked Gemini to read
        return original(request)

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_META_MODELS", "big,small")
    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(spy), **kw))
    meta = asyncio.run(genshin.fetch_meta())

    assert sorted(meta["newCharacters"]) == sorted([VESNA, VODY, by("Kamisato Ayaka")])
    assert meta["banners"] == [by("Skirk")]               # banners stay exactly what the model said
    assert any(t.get("unranked") for t in meta["teams"])  # but Vesna now has a team
    assert page("vesna") in requested[0] and page("vodyanitsa") in requested[0]  # the site's own slugs were used


def test_new_characters_are_recorded_even_when_the_meta_is_degraded(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_META_MODELS", "big,small")
    g = Gemini(list_html=LIST_HTML)

    def handler(request):
        if request.url.host != "genshin.gg" and request.url.path.split("/")[-1].startswith("big"):
            return httpx.Response(404)
        return g(request)

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    meta = asyncio.run(genshin.fetch_meta())
    assert meta["degraded"] is True and meta["banners"] == []
    assert VESNA in meta["newCharacters"] and "enrich" not in g.calls  # reliable badge kept, no enrichment from the backup model


def test_a_failed_enrichment_keeps_the_good_main_meta(monkeypatch):
    for g in (Gemini(enrich_status=500), Gemini(enrich_reply="this is not a list at all")):
        meta = run_fetch(monkeypatch, g)
        assert len(meta["teams"]) == 3 and not any(t.get("unranked") for t in meta["teams"])
        assert meta["degraded"] is False  # still a valid, fresh meta


def test_no_extra_request_when_every_banner_character_already_has_data(monkeypatch):
    base = raw_base()
    base["banners"] = [{"name": "Skirk"}, {"name": "Bennett"}]
    g = Gemini(base=base)
    run_fetch(monkeypatch, g)
    assert [c for c in g.calls if c != "list"] == ["main"]  # no extra Gemini request


def test_a_meta_from_the_backup_model_is_not_enriched(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_META_MODELS", "big,small")
    g = Gemini(enrich_reply=[good_entry()])
    calls = []

    def handler(request):
        calls.append(request.url.path.split("/")[-1].split(":")[0])
        return httpx.Response(503) if calls[-1] == "big" else g(request)

    monkeypatch.setattr(genshin.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    meta = asyncio.run(genshin.fetch_meta())
    assert meta["degraded"] is True and meta["banners"] == [] and "enrich" not in g.calls


def test_unranked_flags_survive_validation_and_serialization():
    """The payload is stored as JSON in Supabase and read back: the flags must round-trip."""
    meta = base_meta()
    genshin.merge_character_pages(meta, [good_entry()], {VESNA})
    restored = json.loads(json.dumps(meta))
    assert restored["teams"][-1]["unranked"] is True and restored["characters"][-1]["unranked"] is True
