"""Fetch freely licensed photos for the Serve IQ site from Wikimedia Commons.

Run (Python 3.9):  .venv/bin/python scripts/fetch_images.py [--force]

What it does
  Players: takes the 48 men and 36 women with the most charted serves from site/data/serveiq.json,
  finds each player's English Wikipedia article (exact name, then "Name (tennis)"), checks that the
  article is about a tennis player (short description or categories), takes its lead image, and keeps
  it only if the file is hosted on Wikimedia Commons under CC0, public domain, CC BY or CC BY-SA.
  The photo is cropped to a square (biased toward the top of portrait images, where faces sit),
  resized to 320x320 and saved as site/img/players/<slug>.webp.

  Credits for every saved photo go to site/data/images.json, which has a "players" key only.
  The site shows player headshots only (v4). There are no scene photos, and this script never
  writes a "scenes" key or a site/img/scenes folder.

Re-runnable: API answers and downloaded originals are cached in output/image_cache (gitignored).
--force ignores the cache. Players listed in EXCLUDE are skipped (reviewed by hand, reason given).
"""

import argparse
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
DATA_JSON = os.path.join(SITE, "data", "serveiq.json")
IMAGES_JSON = os.path.join(SITE, "data", "images.json")
PLAYER_DIR = os.path.join(SITE, "img", "players")
CACHE_DIR = os.path.join(ROOT, "output", "image_cache")

UA = "ServeIQ/1.0 (https://github.com/ijunsu2082-hue/tennis-serve-iq)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PAUSE = 0.6  # seconds between network requests

N_MEN = 48
N_WOMEN = 36

# Hand review of the contact sheet. Server name -> reason. These are skipped, never replaced
# with a non-free file.
EXCLUDE = {
}

# Hand review: a different Commons file for a player whose lead image is a poor crop or a
# group photo. Server name -> "File:..." title. The license rule still applies.
FILE_OVERRIDE = {
    "Tommy Paul": "File:Tommy Paul (USA) (21635302405).jpg",  # lead image: tiny full-body figure
    "Justine Henin": "File:Justine Henin Miami (cropped).jpg",  # lead image not CC (copyrighted free use)
}

# Hand review: crop centre for a player, as fractions of the source width and height.
# Server name -> (cx, cy, side) where side is the square side as a fraction of the short edge.
CROP_OVERRIDE = {
    "Tommy Paul": (0.36, 0.19, 0.34),
    "Justine Henin": (0.52, 0.15, 0.34),
    "Ekaterina Alexandrova": (0.55, 0.17, 0.50),
    "Belinda Bencic": (0.38, 0.30, 0.65),  # keep a second face on a poster out of frame
    "Elina Svitolina": (0.46, 0.40, 0.65),
    "John Isner": (0.50, 0.24, 0.70),
    "Jo Wilfried Tsonga": (0.50, 0.25, 0.70),
    "Kiki Bertens": (0.57, 0.22, 0.55),
    "Pete Sampras": (0.65, 0.25, 0.60),
    # Wide action shots: a tight square on the head, so the face still reads in a 28-40 px chip.
    "Iga Swiatek": (0.51, 0.17, 0.48),
    "Nick Kyrgios": (0.56, 0.12, 0.45),
    "Lleyton Hewitt": (0.67, 0.16, 0.55),
    "Lorenzo Musetti": (0.52, 0.13, 0.48),
    "Botic Van De Zandschulp": (0.37, 0.14, 0.50),
    "Tallon Griekspoor": (0.50, 0.13, 0.48),
    "Tomas Berdych": (0.55, 0.135, 0.50),
    "Eugenie Bouchard": (0.50, 0.14, 0.48),
    "Dominic Thiem": (0.46, 0.17, 0.55),
}

# Hand-tidied author text where the Commons "Artist" field is long or messy.
AUTHOR_OVERRIDE = {
    "Steffi Graf": "Mark Henckel from Hamburg-Altona, Deutschland (original), César (derivative work)",
    "Justine Henin": "AndonicO at English Wikipedia (original), Vinkje83 (derivative work)",
}

FREE_LICENSE = re.compile(r"^(CC0( 1\.0)?|Public domain|PD.*|CC BY(-SA)? \d(\.\d)?.*)$", re.I)


def slug(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def clean_text(s):
    """Plain text: strip tags, unescape entities, replace characters the site copy bans."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    for bad, good in (("—", "-"), ("–", "-"), ("−", "-"), ("·", ","),
                      ("×", "x"), ("~", ""), ("→", "to"), (" ", " ")):
        s = s.replace(bad, good)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_cache_mem = {}
FORCE = False


def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180])


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            time.sleep(PAUSE)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001  retry on network or 429
            wait = 2 + attempt * 4
            print("    retry in %ds after %s" % (wait, e), file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("failed: " + url)


def api(base, params):
    params = dict(params, format="json", formatversion="2")
    url = base + "?" + urllib.parse.urlencode(params)
    import hashlib
    key = "api_" + hashlib.sha1(url.encode("utf-8")).hexdigest()
    path = _cache_path(key + ".json")
    if not FORCE and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    data = json.loads(http_get(url).decode("utf-8"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def download(url):
    import hashlib
    path = _cache_path("img_" + hashlib.sha1(url.encode("utf-8")).hexdigest() + ".bin")
    if not FORCE and os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    b = http_get(url)
    with open(path, "wb") as f:
        f.write(b)
    return b


def wiki_lookup(title):
    d = api(WIKI_API, {"action": "query", "titles": title, "redirects": 1,
                       "prop": "pageimages|description|categories", "piprop": "name",
                       "cllimit": "max"})
    pages = d.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or pages[0].get("invalid"):
        return None
    return pages[0]


def is_tennis_player(page):
    desc = (page.get("description") or "").lower()
    if "tennis" in desc and ("player" in desc or "coach" in desc or "commentator" in desc):
        return True
    for c in page.get("categories", []):
        t = c.get("title", "").lower()
        if "tennis players" in t or "tennis player" in t:
            return True
    return False


def commons_info(file_title, width):
    if not file_title.startswith("File:"):
        file_title = "File:" + file_title
    d = api(COMMONS_API, {"action": "query", "titles": file_title, "prop": "imageinfo",
                          "iiprop": "url|size|extmetadata|mime", "iiurlwidth": width})
    pages = d.get("query", {}).get("pages", [])
    if not pages:
        return None, "no page"
    p = pages[0]
    if p.get("missing") or "imageinfo" not in p:
        return None, "not on Commons"
    ii = p["imageinfo"][0]
    md = ii.get("extmetadata", {})

    def g(k):
        return (md.get(k) or {}).get("value", "")

    lic = clean_text(g("LicenseShortName"))
    if not FREE_LICENSE.match(lic) or re.search(r"\b(NC|ND)\b", lic, re.I):
        return None, "license not free: " + (lic or "none")
    if g("NonFree").lower() in ("true", "1"):
        return None, "marked non-free"
    author = clean_text(g("Artist")) or clean_text(g("Credit")) or "Unknown author"
    if len(author) > 120:
        author = author[:117].rsplit(" ", 1)[0] + "..."
    lic_url = clean_text(g("LicenseUrl"))
    if not lic_url and lic.lower().startswith(("public domain", "pd")):
        lic_url = "https://commons.wikimedia.org/wiki/Help:Public_domain"
    if not lic_url and lic.lower().startswith("cc0"):
        lic_url = "https://creativecommons.org/publicdomain/zero/1.0/"
    return {
        "title": p["title"],
        "thumb": ii.get("thumburl") or ii["url"],
        "width": ii.get("width"), "height": ii.get("height"),
        "mime": ii.get("mime"),
        "author": author, "license": lic, "license_url": lic_url,
        "source": ii.get("descriptionurl") or
        "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_")),
    }, None


def square_crop(im, override=None):
    w, h = im.size
    if override:
        cx, cy, frac = override
        side = int(min(w, h) * frac)
        left = int(cx * w - side / 2)
        top = int(cy * h - side / 2)
    else:
        side = min(w, h)
        left = (w - side) // 2
        if h > w:
            top = int((h - side) * 0.12)  # faces sit high in portrait photos
        else:
            top = 0
    left = max(0, min(left, w - side))
    top = max(0, min(top, h - side))
    return im.crop((left, top, left + side, top + side))


def to_rgb(im):
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def credit(info, src):
    return {"src": src, "author": info["author"], "license": info["license"],
            "license_url": info["license_url"], "source": info["source"]}


def top_players(data):
    f = data["players"]["fields"]
    gi, si, ni = f.index("gender"), f.index("server"), f.index("serves")
    rows = data["players"]["rows"]
    men = sorted([r for r in rows if r[gi] == "m"], key=lambda r: -r[ni])[:N_MEN]
    women = sorted([r for r in rows if r[gi] == "w"], key=lambda r: -r[ni])[:N_WOMEN]
    return [("m", r[si]) for r in men] + [("w", r[si]) for r in women]


def fetch_players(out, log):
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    os.makedirs(PLAYER_DIR, exist_ok=True)
    found = {"m": 0, "w": 0}
    for gender, name in top_players(data):
        print("- %s (%s)" % (name, gender))
        if name in EXCLUDE:
            log.append((name, "excluded after review: " + EXCLUDE[name]))
            _remove(slug(name))
            continue
        page = None
        for title in (name, name + " (tennis)"):
            p = wiki_lookup(title)
            if p and is_tennis_player(p):
                page = p
                break
        if page is None:
            log.append((name, "no English Wikipedia tennis article found"))
            continue
        file_title = FILE_OVERRIDE.get(name) or page.get("pageimage")
        if not file_title:
            log.append((name, "article has no lead image"))
            continue
        info, why = commons_info(file_title, 640)
        if info is None:
            log.append((name, "lead image rejected (%s): %s" % (why, file_title)))
            continue
        try:
            im = Image.open(io.BytesIO(download(info["thumb"])))
            im.load()
        except Exception as e:  # noqa: BLE001
            log.append((name, "image download or decode failed: %s" % e))
            continue
        if min(im.size) < 200:
            log.append((name, "image too small: %dx%d" % im.size))
            continue
        im = to_rgb(square_crop(to_rgb(im), CROP_OVERRIDE.get(name)))
        im = im.resize((320, 320), Image.LANCZOS)
        rel = "img/players/%s.webp" % slug(name)
        im.save(os.path.join(SITE, rel), "WEBP", quality=78, method=6)
        if name in AUTHOR_OVERRIDE:
            info["author"] = AUTHOR_OVERRIDE[name]
        out["players"][name] = credit(info, rel)
        found[gender] += 1
    return found


def _remove(s):
    p = os.path.join(PLAYER_DIR, s + ".webp")
    if os.path.exists(p):
        os.remove(p)


def main():
    global FORCE
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    FORCE = a.force
    out = {"players": {}}
    log = []
    found = fetch_players(out, log)
    # Remove player files that are no longer credited
    keep = {os.path.basename(v["src"]) for v in out["players"].values()}
    if os.path.isdir(PLAYER_DIR):
        for fn in os.listdir(PLAYER_DIR):
            if fn.endswith(".webp") and fn not in keep:
                os.remove(os.path.join(PLAYER_DIR, fn))
    with open(IMAGES_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    total = 0
    if os.path.isdir(PLAYER_DIR):
        total += sum(os.path.getsize(os.path.join(PLAYER_DIR, x)) for x in os.listdir(PLAYER_DIR))
    print("\nSummary")
    print("  men with photos:   %d of %d" % (found["m"], N_MEN))
    print("  women with photos: %d of %d" % (found["w"], N_WOMEN))
    print("  site/img total: %.2f MB" % (total / 1024 / 1024))
    if log:
        print("  skipped (%d):" % len(log))
        for n, why in log:
            print("    %s: %s" % (n, why))


if __name__ == "__main__":
    main()
