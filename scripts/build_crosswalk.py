"""Build the Wikidata -> Correlates of War / Gleditsch-Ward country crosswalk.

Usage::

    python scripts/build_crosswalk.py SNAPSHOT_DIR COW_STATES_CSV GW_ISYSTEM_DAT OUT_CSV

Inputs:

* a HistWarDB snapshot directory (its ``participations.csv`` lists the
  states to map, with their lifespans);
* ``states2016.csv`` from the Correlates of War State System Membership
  data (https://correlatesofwar.org/data-sets/state-system-membership/);
* ``iisystem.dat`` from Gleditsch & Ward's list of independent states
  (http://ksgleditsch.com/statelist.html).

Matching, in order of precedence:

1. ``MANUAL`` -- hand-checked assignments for historical predecessors whose
   names differ from the system member they are coded as (e.g. the Kingdom
   of Prussia is COW 255 "Germany" before 1871).
2. ``name`` -- the state's English label or one of its aliases equals a COW
   (or G-W) state name after normalisation, and the Wikidata lifespan (when
   known) overlaps the COW (or G-W) membership period.

States with no match are written with empty codes: most are entities that
were never members of the interstate system (colonies, protectorates,
sub-national states, pre-1816 states). The output is reviewed by hand
before it is committed; see ``match`` and ``note`` columns.
"""

from __future__ import annotations

import csv
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

UA = {"User-Agent": "HistWarDB/0.1 (academic-research; +https://github.com/VincenzoManto/histwardb)"}

# Wikidata QID or label -> (COW ccode or None, G-W gwcode or None, note).
# Checked by hand against both state lists. A QID key wins over a label key;
# QIDs are used where the label is ambiguous or disambiguated.
MANUAL = {
    # By QID
    "Q13426199": (710, 710, "Republic of China 1912-1949, coded as China"),
    "Q165154": (325, 325, "Kingdom of Sardinia 1720-1861, coded as Italy/Sardinia"),
    "Q600018": (310, 310, "Kingdom of Hungary 1920-1946"),
    "Q8680": (200, 200, "the 'British Empire' item is used as a participant for the United Kingdom"),
    "Q130654": (210, 210, "the 'Dutch Empire' item is used as a participant for the Netherlands"),
    # Later regimes and renamed states
    "Zaire": (490, 490, "Democratic Republic of the Congo"),
    "Kingdom of Cambodia": (811, 811, ""),
    "Khmer Republic": (811, 811, "Cambodia"),
    "Democratic Kampuchea": (811, 811, "Cambodia"),
    "People's Republic of Kampuchea": (811, 811, "Cambodia"),
    "Kingdom of Laos": (812, 812, ""),
    "Somali Democratic Republic": (520, 520, "Somalia"),
    "Somali Republic": (520, 520, "Somalia"),
    "Kingdom of Yemen": (678, 678, "Mutawakkilite Kingdom, coded as Yemen Arab Republic"),
    "South Yemen": (680, 680, "Yemen People's Republic"),
    "Ba'athist Iraq": (645, 645, ""),
    "Republic of Bosnia and Herzegovina": (346, 346, ""),
    "Slovak Republic": (317, None, "1939-1945 state; G-W codes Slovakia only from 1993"),
    "Oriental State of Uruguay": (165, 165, ""),
    "Centralist Republic of Mexico": (70, 70, ""),
    "Estado Novo": (235, 235, "Portugal 1933-1974"),
    "First Czechoslovak Republic": (315, 315, ""),
    "First Hungarian Republic": (310, 310, ""),
    "Government of Meiji Japan": (740, 740, ""),
    "Iran": (630, 630, ""),
    "Vietnam": (816, 816, "unified Vietnam continues the Democratic Republic of Vietnam"),
    # Explicitly not system members, despite a matching name
    "Italian protectorate of Albania": (None, None, "Italian protectorate 1939-1943, not independent"),
    # In the Gleditsch-Ward system but not in COW's
    "Konbaung dynasty": (None, 775, "Burma before 1885; COW codes Myanmar only from 1948"),
    "Nguyen dynasty": (None, 815, "G-W Vietnam (Annam) 1816-1893; not a COW member"),
    "United Provinces of the Río de la Plata": (None, 160, "G-W codes Argentina from 1816, COW from 1841"),
    "South African Republic": (None, 563, "Transvaal; G-W only"),
    "Durrani Empire": (None, 700, "Afghanistan; COW codes it only from 1919"),
    # Germany and its predecessors
    "Kingdom of Prussia": (255, 255, "COW/G-W code Prussia as 'Germany' before 1871"),
    "Prussia": (255, 255, "COW/G-W code Prussia as 'Germany' before 1871"),
    "North German Confederation": (255, 255, "successor of Prussia, coded as Germany"),
    "German Empire": (255, 255, ""),
    "Weimar Republic": (255, 255, ""),
    "German Reich": (255, 255, ""),
    "Nazi Germany": (255, 255, ""),
    "West Germany": (260, 260, "German Federal Republic"),
    "East Germany": (265, 265, "German Democratic Republic"),
    "Germany": (255, 260, "COW keeps 255 after 1990; G-W uses 260"),
    # Austria
    "Austrian Empire": (300, 300, "COW 300 'Austria-Hungary' covers 1816-1918"),
    "Habsburg monarchy": (300, 300, "COW 300 'Austria-Hungary' covers 1816-1918"),
    "Austria-Hungary": (300, 300, ""),
    "Austria–Hungary": (300, 300, ""),
    "First Austrian Republic": (305, 305, ""),
    "Federal State of Austria": (305, 305, ""),
    # Russia
    "Russian Empire": (365, 365, ""),
    "Russian Soviet Federative Socialist Republic": (365, 365, "COW codes Russia 1917-1922 as 365"),
    "Soviet Union": (365, 365, ""),
    "Russia": (365, 365, ""),
    # Ottoman Empire / Turkey
    "Ottoman Empire": (640, 640, "coded as Turkey"),
    "Turkey": (640, 640, ""),
    # Italy and pre-unification states
    "Kingdom of Sardinia": (325, 325, "COW/G-W code Sardinia as 'Italy' before 1861"),
    "Kingdom of Italy": (325, 325, ""),
    "Fascist Italy": (325, 325, ""),
    "Italy": (325, 325, ""),
    "Kingdom of the Two Sicilies": (329, 329, ""),
    "Papal States": (327, 327, ""),
    "Grand Duchy of Tuscany": (337, 337, ""),
    "Duchy of Modena and Reggio": (332, 332, ""),
    "Duchy of Parma and Piacenza": (335, 335, ""),
    # German states
    "Kingdom of Bavaria": (245, 245, ""),
    "Kingdom of Württemberg": (271, 271, ""),
    "Kingdom of Saxony": (269, 269, ""),
    "Grand Duchy of Baden": (267, 267, ""),
    "Electorate of Hesse": (273, 273, "Hesse Electoral"),
    "Grand Duchy of Hesse": (275, 275, "Hesse Grand Ducal"),
    "Kingdom of Hanover": (240, 240, ""),
    # France
    "Kingdom of France": (220, 220, ""),
    "Bourbon Restoration": (220, 220, ""),
    "Bourbon Restoration in France": (220, 220, ""),
    "July Monarchy": (220, 220, ""),
    "French Second Republic": (220, 220, ""),
    "Second French Empire": (220, 220, ""),
    "French Third Republic": (220, 220, ""),
    "Vichy France": (220, 220, ""),
    "French Fourth Republic": (220, 220, ""),
    "France": (220, 220, ""),
    # British Isles
    "United Kingdom of Great Britain and Ireland": (200, 200, ""),
    "United Kingdom": (200, 200, ""),
    # Iberia, Low Countries
    "Spanish Empire": (230, 230, ""),
    "Spain under the Restoration": (230, 230, ""),
    "Francoist Spain": (230, 230, ""),
    "Second Spanish Republic": (230, 230, ""),
    "Spain": (230, 230, ""),
    "Kingdom of Portugal": (235, 235, ""),
    "Portugal": (235, 235, ""),
    "United Kingdom of the Netherlands": (210, 210, ""),
    "Kingdom of the Netherlands": (210, 210, ""),
    "Netherlands": (210, 210, ""),
    # Balkans and Central Europe
    "Principality of Serbia": (345, 340, "G-W codes Serbia 1878-1918 as 340"),
    "Kingdom of Serbia": (345, 340, "G-W codes Serbia 1878-1918 as 340"),
    "Kingdom of Yugoslavia": (345, 345, ""),
    "Socialist Federal Republic of Yugoslavia": (345, 345, ""),
    "Federal Republic of Yugoslavia": (345, 345, ""),
    "Serbia and Montenegro": (345, 345, ""),
    "Principality of Montenegro": (341, 341, ""),
    "Kingdom of Montenegro": (341, 341, ""),
    "Kingdom of Romania": (360, 360, ""),
    "Socialist Republic of Romania": (360, 360, ""),
    "United Principalities": (360, 360, "Romania"),
    "Principality of Bulgaria": (355, 355, ""),
    "Kingdom of Bulgaria": (355, 355, ""),
    "Tsardom of Bulgaria": (355, 355, ""),
    "People's Republic of Bulgaria": (355, 355, ""),
    "Kingdom of Greece": (350, 350, ""),
    "Second Hellenic Republic": (350, 350, ""),
    "Kingdom of Hungary": (310, 310, "only the 1918-1946 state is a system member"),
    "Hungarian People's Republic": (310, 310, ""),
    "Hungarian Soviet Republic": (310, 310, ""),
    "Czechoslovakia": (315, 315, ""),
    "Second Polish Republic": (290, 290, ""),
    "Polish People's Republic": (290, 290, ""),
    # Asia and the Middle East
    "Qing dynasty": (710, 710, "coded as China"),
    "Republic of China (1912–1949)": (710, 710, "coded as China before 1949"),
    "Beiyang government": (710, 710, "coded as China"),
    "People's Republic of China": (710, 710, ""),
    "Taiwan": (713, 713, "Republic of China on Taiwan after 1949"),
    "Empire of Japan": (740, 740, ""),
    "Japan": (740, 740, ""),
    "Joseon": (730, 730, "coded as Korea"),
    "Korean Empire": (730, 730, "coded as Korea"),
    "North Korea": (731, 731, ""),
    "South Korea": (732, 732, ""),
    "Rattanakosin Kingdom": (800, 800, "coded as Thailand"),
    "Siam": (800, 800, "coded as Thailand"),
    "Qajar Iran": (630, 630, "coded as Iran"),
    "Pahlavi Iran": (630, 630, "coded as Iran"),
    "Imperial State of Iran": (630, 630, "coded as Iran"),
    "Emirate of Afghanistan": (700, 700, "coded as Afghanistan"),
    "Kingdom of Afghanistan": (700, 700, "coded as Afghanistan"),
    "Islamic Emirate of Afghanistan": (700, 700, "Taliban government 1996-2001"),
    "Kingdom of Egypt": (651, 651, ""),
    "United Arab Republic": (651, 651, "coded as Egypt"),
    "Kingdom of Iraq": (645, 645, ""),
    "Ethiopian Empire": (530, 530, "coded as Ethiopia"),
    "South Vietnam": (817, 817, "Republic of Vietnam"),
    "North Vietnam": (816, 816, "Democratic Republic of Vietnam"),
    # Americas
    "First Mexican Empire": (70, 70, ""),
    "Second Mexican Empire": (70, 70, ""),
    "Mexico": (70, 70, ""),
    "Empire of Brazil": (140, 140, ""),
    "Gran Colombia": (100, 100, "coded as Colombia"),
    "Republic of New Granada": (100, 100, "coded as Colombia"),
    "United States of Colombia": (100, 100, "coded as Colombia"),
}


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.casefold().replace("&", " and ")
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"^the ", "", name)


def load_cow(path: Path) -> list[dict]:
    with open(path, encoding="latin-1", newline="") as f:
        return [
            {"code": int(r["ccode"]), "abb": r["stateabb"], "name": r["statenme"],
             "start": int(r["styear"]), "end": int(r["endyear"])}
            for r in csv.DictReader(f)
        ]


def load_gw(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="latin-1") as f:
        for line in f:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 5:
                continue
            rows.append({"code": int(parts[0]), "abb": parts[1], "name": parts[2],
                         "start": int(parts[3][-4:]), "end": int(parts[4][-4:])})
    return rows


def fetch_names(qids: list[str]) -> dict[str, set]:
    names: dict[str, set] = {}
    for i in range(0, len(qids), 50):
        batch = qids[i : i + 50]
        r = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": "|".join(batch), "props": "labels|aliases",
                    "languages": "en", "format": "json"},
            headers=UA,
            timeout=60,
        )
        r.raise_for_status()
        for qid, entity in r.json().get("entities", {}).items():
            found = {entity.get("labels", {}).get("en", {}).get("value", "")}
            found |= {a["value"] for a in entity.get("aliases", {}).get("en", [])}
            names[qid] = {n for n in found if n}
        time.sleep(1)
    return names


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    """At least two calendar years in common.

    A single shared boundary year is not enough: it is typically a colony or
    protectorate ending the year its independent successor joins the system
    (e.g. the Colony of Jamaica and Jamaica, both in 1962).
    """
    lo = max(a_start if a_start is not None else -10_000, b_start)
    hi = min(a_end if a_end is not None else 10_000, b_end)
    return hi - lo >= 1


def name_variants(name: str) -> set:
    """A system name and its parts: "Iran (Persia)" -> iran persia / iran / persia; "Italy/Sardinia" -> ..."""
    variants = {name}
    outer = re.sub(r"\s*\(.*?\)", "", name).strip()
    variants.add(outer)
    variants |= set(re.findall(r"\((.*?)\)", name))
    for v in list(variants):
        variants |= {part.strip() for part in v.split("/")}
    return {normalize(v) for v in variants if v}


def name_match(names: set, start, end, system: list[dict]):
    # Labels disambiguated by HistWarDB carry a " (Q123)" suffix.
    wanted = {normalize(re.sub(r" \(Q\d+\)$", "", n)) for n in names}
    hits = {(s["code"], s["name"]) for s in system
            if name_variants(s["name"]) & wanted and overlaps(start, end, s["start"], s["end"])}
    codes = {code for code, _ in hits}
    return (codes.pop(), None) if len(codes) == 1 else None


def main(snapshot_dir: str, cow_path: str, gw_path: str, out_path: str) -> None:
    with open(Path(snapshot_dir) / "participations.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    states = {}
    for r in rows:
        states.setdefault(r["country_qid"], r)
    cow, gw = load_cow(Path(cow_path)), load_gw(Path(gw_path))
    cow_names = {s["code"]: s for s in cow}
    names = fetch_names(sorted(states))

    def year(value):
        return int(float(value)) if value not in ("", None) else None

    out = []
    for qid, r in sorted(states.items(), key=lambda kv: kv[1]["country"]):
        label = r["country"]
        start, end = year(r["country_start"]), year(r["country_end"])
        cow_code = gw_code = None
        match, note = "", ""
        manual = MANUAL.get(qid) or MANUAL.get(label)
        if manual:
            cow_code, gw_code, note = manual
            match = "manual"
        else:
            all_names = names.get(qid, set()) | {label}
            hit = name_match(all_names, start, end, cow)
            if hit:
                cow_code, match = hit[0], "name"
            gw_hit = name_match(all_names, start, end, gw)
            if gw_hit:
                gw_code = gw_hit[0]
                match = match or "name"
        out.append({
            "country_qid": qid,
            "country": label,
            "cow_code": cow_code if cow_code is not None else "",
            "cow_name": cow_names[cow_code]["name"] if cow_code in cow_names else "",
            "gw_code": gw_code if gw_code is not None else "",
            "match": match,
            "note": note,
        })

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(out)
    matched = sum(1 for o in out if o["cow_code"] != "")
    print(f"{len(out)} states, {matched} with a COW code, {sum(1 for o in out if o['gw_code'] != '')} with a G-W code")


if __name__ == "__main__":
    main(*sys.argv[1:5])
