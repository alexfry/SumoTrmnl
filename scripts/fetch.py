#!/usr/bin/env python3
"""
fetch.py — Fetches current sumo tournament data from sumo-api.com
and pushes merge variables to a Trmnl private plugin webhook.

Environment variables required:
  TRMNL_PLUGIN_UUID  — UUID from your Trmnl private plugin settings
  TRMNL_API_KEY      — Your Trmnl API key (for authentication)

Usage:
  python3 scripts/fetch.py

Basho schedule: Jan (01), Mar (03), May (05), Jul (07), Sep (09), Nov (11)
Each runs for 15 days starting on the second Sunday of the month.
"""

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

SUMO_API_BASE = "https://sumo-api.com/api"
TRMNL_WEBHOOK = "https://usetrmnl.com/api/custom_plugins/{uuid}"

BASHO_MONTHS = [1, 3, 5, 7, 9, 11]
BASHO_NAMES = {
    1: "Hatsu Basho",
    3: "Haru Basho",
    5: "Natsu Basho",
    7: "Nagoya Basho",
    9: "Aki Basho",
    11: "Kyushu Basho",
}
DIVISION = "Makuuchi"

RANK_PREFIXES = {
    "Yokozuna": "Y",
    "Ozeki": "O",
    "Sekiwake": "S",
    "Komusubi": "K",
    "Maegashira": "M",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def basho_id(year: int, month: int) -> str:
    return f"{year}{month:02d}"


def abbreviate_rank(rank_str: str) -> str:
    """Convert 'Maegashira 18 East' → 'M18e', 'Yokozuna 1 West' → 'Y1w', etc."""
    if not rank_str:
        return ""
    parts = rank_str.split()
    if len(parts) < 3:
        return rank_str
    prefix = RANK_PREFIXES.get(parts[0], parts[0][0])
    number = parts[1]
    side = "e" if parts[2].lower() == "east" else "w"
    return f"{prefix}{number}{side}"


def second_sunday(year: int, month: int) -> datetime:
    """Return the date of the second Sunday of a given month."""
    d = datetime(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    d += timedelta(weeks=1)
    return d


def current_basho_info(now: datetime) -> dict:
    """Determine the active or most recently completed basho, and the next one."""
    year = now.year
    total_days = 15

    for _ in range(3):
        for month in sorted(BASHO_MONTHS, reverse=True):
            start = second_sunday(year, month)
            end = start + timedelta(days=total_days - 1)
            if now.date() >= start.date():
                if now.date() <= end.date():
                    day = (now.date() - start.date()).days + 1
                    status = "active"
                else:
                    day = total_days
                    status = "complete"

                bid = basho_id(year, month)
                name = BASHO_NAMES[month]

                next_month_idx = BASHO_MONTHS.index(month) + 1
                if next_month_idx >= len(BASHO_MONTHS):
                    next_year, next_month = year + 1, BASHO_MONTHS[0]
                else:
                    next_year, next_month = year, BASHO_MONTHS[next_month_idx]
                next_start = second_sunday(next_year, next_month)
                days_until = (next_start.date() - now.date()).days

                return {
                    "basho_id": bid,
                    "basho_name": name,
                    "basho_year": str(year),
                    "day": day,
                    "total_days": total_days,
                    "status": status,
                    "next_basho_id": basho_id(next_year, next_month),
                    "next_basho_name": BASHO_NAMES[next_month],
                    "days_until_next": days_until,
                }
        year -= 1

    raise RuntimeError("Could not determine current basho")


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_banzuke(bid: str) -> tuple[list, dict, dict]:
    """
    Fetch banzuke. Returns:
      - standings list (sorted by wins)
      - record_by_shikona dict for W-L lookup in torikumi
      - jp_by_id dict for Japanese name lookup by rikishi ID
    """
    url = f"{SUMO_API_BASE}/basho/{bid}/banzuke/{DIVISION}"
    try:
        data = get_json(url)
    except urllib.error.HTTPError as e:
        print(f"Warning: could not fetch banzuke ({e})")
        return [], {}, {}

    wrestlers = []
    jp_by_id = {}

    for side in ("east", "west"):
        for entry in data.get(side, []):
            shikona = entry.get("shikonaEn", "")
            shikona_jp = entry.get("shikonaJp", "")
            rikishi_id = entry.get("rikishiID", "")
            record = entry.get("record", [])

            wins = sum(1 for r in record if r.get("result") == "win")
            losses = sum(1 for r in record if r.get("result") == "loss")
            absent = sum(1 for r in record if r.get("result") not in ("win", "loss"))

            wrestlers.append({
                "rank": abbreviate_rank(entry.get("rank", "")),
                "shikona": shikona,
                "shikona_jp": shikona_jp,
                "wins": wins,
                "losses": losses,
                "absent": absent,
            })

            if rikishi_id:
                jp_by_id[str(rikishi_id)] = shikona_jp

    wrestlers.sort(key=lambda w: (-w["wins"], w["losses"]))
    record_by_shikona = {w["shikona"]: w for w in wrestlers}
    return wrestlers, record_by_shikona, jp_by_id


def fetch_head_to_head(east_id: str, west_id: str) -> str:
    """Return head-to-head record as 'EW-WW' (east wins - west wins)."""
    if not east_id or not west_id:
        return ""
    url = f"{SUMO_API_BASE}/rikishi/{east_id}/matches/{west_id}"
    try:
        data = get_json(url)
        east_wins = sum(data.get("kimariteWins", {}).values())
        east_losses = sum(data.get("kimariteLosses", {}).values())
        return f"{east_wins}-{east_losses}"
    except urllib.error.HTTPError:
        return ""


def fetch_torikumi(bid: str, day: int, record_by_shikona: dict, jp_by_id: dict) -> list:
    """Fetch the torikumi (bout schedule) for a given day."""
    url = f"{SUMO_API_BASE}/basho/{bid}/torikumi/{DIVISION}/{day}"
    try:
        data = get_json(url)
    except urllib.error.HTTPError as e:
        print(f"Warning: could not fetch torikumi ({e})")
        return []

    bouts = []
    for i, match in enumerate(data.get("torikumi", []), start=1):
        east = match.get("eastShikona", "")
        east_id = str(match.get("eastId", ""))
        east_jp = jp_by_id.get(east_id, "").split("\u3000")[0]

        west = match.get("westShikona", "")
        west_id = str(match.get("westId", ""))
        west_jp = jp_by_id.get(west_id, "").split("\u3000")[0]

        winner_id = str(match.get("winnerId", ""))
        winner = east if winner_id == east_id else (west if winner_id == west_id else "")

        east_record = record_by_shikona.get(east, {})
        west_record = record_by_shikona.get(west, {})

        past = fetch_head_to_head(east_id, west_id)

        bouts.append({
            "bout": i,
            "east": east,
            "east_jp": east_jp,
            "east_rank": abbreviate_rank(match.get("eastRank", "")),
            "east_wins": east_record.get("wins", 0),
            "east_losses": east_record.get("losses", 0),
            "west": west,
            "west_jp": west_jp,
            "west_rank": abbreviate_rank(match.get("westRank", "")),
            "west_wins": west_record.get("wins", 0),
            "west_losses": west_record.get("losses", 0),
            "winner": winner,
            "kimarite": match.get("kimarite", ""),
            "past": past,
        })

    return bouts


# ── Trmnl push ─────────────────────────────────────────────────────────────────

def push_to_trmnl(uuid: str, api_key: str, variables: dict) -> None:
    url = TRMNL_WEBHOOK.format(uuid=uuid)
    payload = json.dumps({"merge_variables": variables}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            print(f"Trmnl response [{resp.status}]: {body}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Trmnl error [{e.code}]: {body}")
        raise


# ── Japanese font subset ───────────────────────────────────────────────────────

def generate_jp_font_css(jp_names: list, font_path: str) -> str:
    """Subset the JP font to only the characters in use and return a @font-face CSS string."""
    import base64, io
    from fonttools import subset as ft_subset

    chars = set()
    for name in jp_names:
        if name:
            chars.update(name)

    if not chars:
        return ""

    options = ft_subset.Options()
    options.name_IDs = []  # strip name table to save space
    font = ft_subset.load_font(font_path, options)
    subsetter = ft_subset.Subsetter(options=options)
    subsetter.populate(unicodes=[ord(c) for c in chars])
    subsetter.subset(font)

    buf = io.BytesIO()
    ft_subset.save_font(font, buf, options)
    b64 = base64.b64encode(buf.getvalue()).decode()

    size_kb = len(buf.getvalue()) / 1024
    print(f"Font subset: {len(chars)} chars, {size_kb:.1f}KB")
    return f"@font-face {{ font-family: 'SumoJP'; src: url('data:font/otf;base64,{b64}') format('opentype'); }}"


# ── File output ────────────────────────────────────────────────────────────────

def write_data_file(path: str, variables: dict) -> None:
    import pathlib
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(variables, f, ensure_ascii=False, indent=2)
    print(f"Data written to {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    uuid = os.environ.get("TRMNL_PLUGIN_UUID")
    api_key = os.environ.get("TRMNL_API_KEY")
    output_path = os.environ.get("DATA_OUTPUT_PATH")

    if not uuid and not api_key and not output_path:
        raise EnvironmentError(
            "Set DATA_OUTPUT_PATH to write a JSON file, or set "
            "TRMNL_PLUGIN_UUID + TRMNL_API_KEY to push to Trmnl (or both)."
        )

    now = datetime.now(tz=timezone(timedelta(hours=9)))  # JST
    print(f"Current time (JST): {now.strftime('%Y-%m-%d %H:%M')}")

    info = current_basho_info(now)
    print(f"Basho: {info['basho_name']} {info['basho_year']} | Status: {info['status']} | Day: {info['day']}")

    standings, record_by_shikona, jp_by_id = fetch_banzuke(info["basho_id"])
    print(f"Fetched {len(standings)} wrestlers from banzuke")

    torikumi = fetch_torikumi(info["basho_id"], info["day"], record_by_shikona, jp_by_id)
    print(f"Fetched {len(torikumi)} bouts for day {info['day']}")

    font_path = os.environ.get("JP_FONT_PATH")
    jp_font_css = ""
    if font_path:
        jp_names = [b.get("east_jp", "") for b in torikumi] + [b.get("west_jp", "") for b in torikumi]
        jp_font_css = generate_jp_font_css(jp_names, font_path)

    leader_wins = standings[0]["wins"] if standings else 0
    leaders = [w["shikona"] for w in standings if w["wins"] == leader_wins]

    variables = {
        **info,
        "division": DIVISION,
        "standings": standings,
        "torikumi": torikumi,
        "leaders": leaders,
        "leader_wins": leader_wins,
        "jp_font_css": jp_font_css,
        "last_updated": now.strftime("%Y-%m-%d %H:%M JST"),
    }

    if output_path:
        write_data_file(output_path, variables)

    if uuid and api_key:
        push_to_trmnl(uuid, api_key, variables)

    print("Done.")


if __name__ == "__main__":
    main()
