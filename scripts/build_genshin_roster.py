"""
Builds the Genshin character roster from Enka.Network's public asset store
(https://github.com/EnkaNetwork/API-docs/tree/master/store/gi).

Run whenever a new character ships:

    python scripts/build_genshin_roster.py

Writes the roster to this repo (the backend validates Gemini's output against
it) and, when the sibling frontend repo is present, to its `public/genshin/`
folder so the Angular page can render the grid without calling the backend.
"""
import json
import re
import sys
from pathlib import Path

import httpx

BASE = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/gi"
ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = [
    ROOT / "src" / "mcp_server" / "data" / "genshin_roster.json",
    ROOT.parent / "PersonalWebsite" / "public" / "genshin" / "roster.json",
]

ELEMENTS = {"Fire": "Pyro", "Water": "Hydro", "Wind": "Anemo", "Electric": "Electro",
            "Ice": "Cryo", "Rock": "Geo", "Grass": "Dendro"}
WEAPONS = {"WEAPON_SWORD_ONE_HAND": "Sword", "WEAPON_CLAYMORE": "Claymore", "WEAPON_POLE": "Polearm",
           "WEAPON_BOW": "Bow", "WEAPON_CATALYST": "Catalyst"}
RARITY = {"QUALITY_ORANGE": 5, "QUALITY_ORANGE_SP": 5, "QUALITY_PURPLE": 4}  # _SP = Aloy
LANGS = ("en", "es", "pt")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> int:
    avatars = httpx.get(f"{BASE}/avatars.json", timeout=30).raise_for_status().json()
    locs = httpx.get(f"{BASE}/locs.json", timeout=30).raise_for_status().json()

    roster, skipped = [], []
    for avatar_id, a in avatars.items():
        element, weapon, rarity = ELEMENTS.get(a.get("Element")), WEAPONS.get(a.get("WeaponType")), RARITY.get(a.get("QualityType"))
        names = {lang: locs.get(lang, {}).get(str(a.get("NameTextMapHash"))) for lang in LANGS}
        side = (a.get("SideIconName") or "").removeprefix("/ui/").removesuffix(".png")
        # Travelers ("10000005-504") change element per skill depot: not modelled yet.
        if "-" in avatar_id or not (element and weapon and rarity and names["en"] and side):
            skipped.append(f"{avatar_id} {names['en']}")
            continue
        roster.append({
            "id": int(avatar_id),
            "key": slug(names["en"]),
            "name": {lang: names[lang] or names["en"] for lang in LANGS},
            "element": element,
            "weapon": weapon,
            "rarity": rarity,
            # Portrait: strip "Side_". A few characters use a different file name
            # for the full icon, so the UI falls back to `sideIcon` on error.
            "icon": side.replace("UI_AvatarIcon_Side_", "UI_AvatarIcon_"),
            "sideIcon": side,
        })

    roster.sort(key=lambda c: c["id"])
    payload = json.dumps({"source": "enka.network store/gi", "characters": roster}, ensure_ascii=False, separators=(",", ":"))
    for out in OUTPUTS:
        if out.parent.parent.exists() or out == OUTPUTS[0]:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(payload, encoding="utf-8")
            print(f"wrote {out.relative_to(ROOT.parent)} ({len(payload) // 1024} KB)")
    print(f"{len(roster)} characters; skipped: {skipped or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
