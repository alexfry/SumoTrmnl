#!/usr/bin/env python3
"""
fetch_profile.py — Fetches a random rikishi profile from api.sumostats.com
and writes it to a JSON file for TRMNL polling.

Environment variables:
  PROFILE_OUTPUT_PATH      — Where to write profile.json (e.g. gh-pages/profile.json)
  PROFILE_DIVISIONS_DEPTH  — 1–6: 1=Makuuchi, 2=+Juryo, 3=+Makushita, …, 6=all (default: 1)
  PROFILE_ACTIVE_ONLY      — "true" = current banzuke only, "false" = all-time (default: true)
  JP_FONT_PATH             — Path to NotoSansJP font for kanji image rendering
  JP_IMG_DIR               — Directory to save rendered kanji images
  JP_IMG_BASE_URL          — Public base URL for those images
"""

import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

SUMOSTATS_API = "https://api.sumostats.com/v1"

BASHO_MONTHS = [1, 3, 5, 7, 9, 11]

DIVISIONS_BY_DEPTH = {
    1: ["Makuuchi"],
    2: ["Makuuchi", "Juryo"],
    3: ["Makuuchi", "Juryo", "Makushita"],
    4: ["Makuuchi", "Juryo", "Makushita", "Sandanme"],
    5: ["Makuuchi", "Juryo", "Makushita", "Sandanme", "Jonidan"],
    6: ["Makuuchi", "Juryo", "Makushita", "Sandanme", "Jonidan", "Jonokuchi"],
}

# Rank tier letter prefixes per division depth (for highest_rank filtering)
ELIGIBLE_TIERS_BY_DEPTH = {
    1: ("Y", "O", "S", "K", "M"),
    2: ("Y", "O", "S", "K", "M", "J"),
    3: ("Y", "O", "S", "K", "M", "J", "Ms"),
    4: ("Y", "O", "S", "K", "M", "J", "Ms", "Sd"),
    5: ("Y", "O", "S", "K", "M", "J", "Ms", "Sd", "Jd"),
    6: ("Y", "O", "S", "K", "M", "J", "Ms", "Sd", "Jd", "Jk"),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_json(url: str, params: dict | None = None) -> dict:
    if params:
        url = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def second_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    days_until_sunday = (6 - d.weekday()) % 7
    first_sunday = d.replace(day=d.day + days_until_sunday)
    return first_sunday.replace(day=first_sunday.day + 7)


def current_basho_id(now: datetime) -> str:
    """Return the current or most recent basho ID in YYYYMM format."""
    year = now.year
    today = now.date()
    for _ in range(3):
        for month in sorted(BASHO_MONTHS, reverse=True):
            start = second_sunday(year, month)
            if today >= start:
                return f"{year}{month:02d}"
        year -= 1
    raise RuntimeError("Could not determine basho ID")


def rank_in_depth(rank: str, depth: int) -> bool:
    """Return True if highest_rank falls within the given division depth."""
    if not rank:
        return False
    for tier in ELIGIBLE_TIERS_BY_DEPTH[depth]:
        # Match exactly (e.g. "Ms" must not match "M")
        rest = rank[len(tier):]
        if rank.startswith(tier) and (not rest or rest[0].isdigit()):
            return True
    return False


def calculate_age(birth_date_str: str) -> int | None:
    if not birth_date_str:
        return None
    try:
        birth = datetime.strptime(birth_date_str, "%Y-%m-%d").date()
        today = date.today()
        return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except ValueError:
        return None


def format_basho(bid) -> str:
    """202305 → '2023-05'"""
    s = str(bid) if bid else ""
    return f"{s[:4]}-{s[4:]}" if len(s) == 6 else s


# ── Rikishi selection ──────────────────────────────────────────────────────────

def pick_active_rikishi_id(depth: int) -> int:
    """Return a random active rikishi_id within the eligible division depth."""
    candidates = []
    page = 0
    while page < 20:
        data = get_json(f"{SUMOSTATS_API}/rikishi", {
            "active": "true",
            "pageSize": 100,
            "pageIndex": page,
        })
        entries = data.get("data", [])
        if not entries:
            break
        for r in entries:
            if rank_in_depth(r.get("rank", ""), depth):
                candidates.append(r.get("id"))
        # If we have candidates and have scanned at least 200 wrestlers, that's enough
        if candidates and page >= 1:
            break
        page += 1
    if not candidates:
        raise RuntimeError(f"No active rikishi found for depth={depth}")
    return random.choice(candidates)


def pick_alltime_rikishi_id(depth: int) -> int:
    """Return a random rikishi_id from all-time records filtered by division depth."""
    # Fetch first page to get total count
    first = get_json(f"{SUMOSTATS_API}/rikishi", {"pageSize": 1, "pageIndex": 0})
    total = first.get("total") or first.get("count") or 0

    candidates = []
    if total > 0:
        page_size = 100
        total_pages = (total + page_size - 1) // page_size
        # Try random pages until we have candidates (usually succeeds first try for large divisions)
        seen_pages = set()
        for _ in range(10):
            page = random.randint(0, total_pages - 1)
            if page in seen_pages:
                continue
            seen_pages.add(page)
            data = get_json(f"{SUMOSTATS_API}/rikishi", {"pageSize": page_size, "pageIndex": page})
            for r in data.get("data", []):
                if rank_in_depth(r.get("highest_rank", ""), depth):
                    candidates.append(r.get("id") or r.get("rikishi_id"))
            if candidates:
                break
    else:
        # Fallback: use first page without total
        data = get_json(f"{SUMOSTATS_API}/rikishi", {"pageSize": 100, "pageIndex": 0})
        for r in data.get("data", []):
            if rank_in_depth(r.get("highest_rank", ""), depth):
                candidates.append(r.get("id") or r.get("rikishi_id"))

    if not candidates:
        raise RuntimeError(f"No eligible rikishi found for depth={depth}")
    return random.choice(candidates)


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_profile(rikishi_id: int) -> dict:
    return get_json(f"{SUMOSTATS_API}/rikishi/{rikishi_id}", {"expand": "heya"})


def fetch_career_achievements(rikishi_id: int) -> dict:
    """Count yusho, jun_yusho, and special prizes from full banzuke history."""
    yusho = jun_yusho = special_prizes = 0
    page = 0
    while True:
        data = get_json(f"{SUMOSTATS_API}/banzuke", {
            "rikishi_id": rikishi_id,
            "pageSize": 100,
            "pageIndex": page,
        })
        entries = data.get("data", [])
        for e in entries:
            if e.get("yusho"):
                yusho += 1
            if e.get("jun_yusho"):
                jun_yusho += 1
            if e.get("gino_sho") or e.get("kanto_sho") or e.get("shukun_sho"):
                special_prizes += 1
        if len(entries) < 100:
            break
        page += 1
    return {"yusho": yusho, "jun_yusho": jun_yusho, "special_prizes": special_prizes}


# ── Image handling ────────────────────────────────────────────────────────────

def mirror_photo(image_url: str, rikishi_id: int, photos_dir: str, base_url: str) -> str:
    """Download the rikishi photo and save to GitHub Pages, return the mirrored URL."""
    import pathlib

    if not image_url:
        return ""

    pathlib.Path(photos_dir).mkdir(parents=True, exist_ok=True)

    # Detect extension from URL, default to jpg
    ext = "jpg"
    path_part = image_url.split("?")[0]
    if "." in path_part.split("/")[-1]:
        ext = path_part.rsplit(".", 1)[-1].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"

    filename = f"{rikishi_id}.{ext}"
    filepath = os.path.join(photos_dir, filename)

    try:
        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SumoTrmnl/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            with open(filepath, "wb") as f:
                f.write(resp.read())
        print(f"Photo saved: {filepath}")
        return f"{base_url}/{filename}"
    except Exception as e:
        print(f"Warning: could not download photo ({e})")
        return ""


def render_kanji_image(text: str, img_dir: str, base_url: str, font_path: str, size: int = 48) -> str:
    from PIL import Image, ImageDraw, ImageFont
    import hashlib
    import pathlib

    if not text:
        return ""
    pathlib.Path(img_dir).mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(font_path, size)

    # Include size in hash so different sizes get different filenames
    key = f"{text}@{size}"
    filename = hashlib.md5(key.encode()).hexdigest()[:12] + ".png"
    filepath = os.path.join(img_dir, filename)

    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0] + 4
    h = bbox[3] - bbox[1] + 4
    img = Image.new("L", (w, h), 255)
    ImageDraw.Draw(img).text((2, 2 - bbox[1]), text, fill=0, font=font)
    img.save(filepath, "PNG")
    return f"{base_url}/{filename}"


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    output_path = os.environ.get("PROFILE_OUTPUT_PATH")
    if not output_path:
        raise EnvironmentError("PROFILE_OUTPUT_PATH is required")

    depth = int(os.environ.get("PROFILE_DIVISIONS_DEPTH", "1"))
    active_only = os.environ.get("PROFILE_ACTIVE_ONLY", "true").lower() == "true"
    font_path = os.environ.get("JP_FONT_PATH")
    img_dir = os.environ.get("JP_IMG_DIR")
    img_base_url = os.environ.get("JP_IMG_BASE_URL")
    photos_dir = os.environ.get("PROFILE_PHOTOS_DIR")
    photos_base_url = os.environ.get("PROFILE_PHOTOS_BASE_URL")

    now = datetime.now(tz=timezone(timedelta(hours=9)))  # JST
    print(f"fetch_profile | depth={depth} active_only={active_only} | {now.strftime('%Y-%m-%d %H:%M JST')}")

    if active_only:
        rikishi_id = pick_active_rikishi_id(depth)
    else:
        rikishi_id = pick_alltime_rikishi_id(depth)

    print(f"Selected rikishi_id: {rikishi_id}")

    profile = fetch_profile(rikishi_id)
    shikona = profile.get("shikona", "")
    print(f"Profile fetched: {shikona}")

    achievements = fetch_career_achievements(rikishi_id)

    heya = profile.get("heya") or {}
    heya_name = heya.get("name", "") if isinstance(heya, dict) else ""

    kanji = profile.get("shikona_kanji", "")
    kanji_img_url = ""
    if font_path and img_dir and img_base_url and kanji:
        kanji_img_url = render_kanji_image(kanji, img_dir, img_base_url, font_path, size=48)
        print(f"Kanji image: {kanji_img_url}")

    # Mirror photo to GitHub Pages so TRMNL's renderer can load it reliably
    photo_url = ""
    raw_image_url = profile.get("image_url", "")
    if photos_dir and photos_base_url and raw_image_url:
        photo_url = mirror_photo(raw_image_url, rikishi_id, photos_dir, photos_base_url)
    else:
        photo_url = raw_image_url  # fallback: use direct URL

    retired_basho = profile.get("retired_basho")
    status = f"Retired {format_basho(retired_basho)}" if retired_basho else "Active"

    height = profile.get("height")
    weight = profile.get("weight")

    variables = {
        "rikishi_id":     rikishi_id,
        "shikona":        shikona,
        "shikona_kanji":  kanji,
        "shikona_kana":   profile.get("shikona_kana", ""),
        "rank":           profile.get("rank", ""),
        "highest_rank":   profile.get("highest_rank", ""),
        "heya":           heya_name,
        "shusshin":       profile.get("shusshin", ""),
        "age":            calculate_age(profile.get("birth_date")),
        "height":         f"{height:.1f}cm" if height else "",
        "weight":         f"{weight:.1f}kg" if weight else "",
        "total_wins":     profile.get("total_wins", 0),
        "total_losses":   profile.get("total_losses", 0),
        "total_absents":  profile.get("total_absents", 0),
        "total_bashos":   profile.get("total_bashos", 0),
        "yusho":          achievements["yusho"],
        "jun_yusho":      achievements["jun_yusho"],
        "special_prizes": achievements["special_prizes"],
        "image_url":      photo_url,
        "kanji_img_url":  kanji_img_url,
        "first_basho":    format_basho(profile.get("first_basho")),
        "latest_basho":   format_basho(profile.get("latest_basho")),
        "status":         status,
        "description":    profile.get("description", ""),
        "last_updated":   now.strftime("%Y-%m-%d %H:%M JST"),
    }

    import pathlib
    pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(variables, f, ensure_ascii=False, indent=2)
    print(f"Written to {output_path}")
    print("Done.")


if __name__ == "__main__":
    main()
