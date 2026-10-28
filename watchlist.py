#!/usr/bin/env python3
"""Cross-reference Letterboxd lists with JustWatch (US) and build a static page.

Usage: python3 watchlist.py [output_dir]   (default: site/)
Uses only the Python standard library.
"""

import html
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone

# Letterboxd lists to combine. Label is shown on each film.
LISTS = [
    {"label": "Jacob", "url": "https://letterboxd.com/jfmurphy198/list/to-watch/"},
    {"label": "Mahima", "url": "https://letterboxd.com/astevemartini/list/jm-watchlist/"},
]

# Services you have, in display order. Each maps to JustWatch provider-name
# prefixes. Add-on channels (e.g. "HBO Max Amazon Channel") count as the
# underlying service.
SERVICES = {
    "Netflix": ["Netflix"],
    "Prime Video": ["Amazon Prime Video"],
    "Disney+": ["Disney Plus"],
    "Hulu": ["Hulu"],
    "HBO Max": ["HBO Max", "Max"],
    "Apple TV": ["Apple TV"],
    "Peacock": ["Peacock"],
    "Paramount+": ["Paramount Plus", "Paramount+"],
    "Tubi": ["Tubi"],
    "YouTube TV": ["YouTube TV"],
}

COUNTRY = "US"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
JW_URL = "https://apis.justwatch.com/graphql"
# Monetization types that cost nothing beyond a subscription (or nothing at all).
STREAM_TYPES = {"FLATRATE", "ADS", "FREE"}
# Digital storefronts (pay per film), with shorter names for the page.
STORE_NAMES = {"Amazon Video": "Amazon", "Apple TV Store": "Apple", "Fandango At Home": "Fandango", "Google Play Movies": "Google Play"}
DIGITAL = {"SD", "HD", "_4K"}
CHANNEL_SUFFIX = re.compile(r"\s+(Amazon Channel|Apple TV Channel|Roku Premium Channel)$", re.I)


def clean(name):
    name = CHANNEL_SUFFIX.sub("", name).strip()
    return {"MGM+": "MGM Plus", "Plex Player": "Plex", "Plex Channel": "Plex"}.get(name, name)

JW_QUERY = """
query Search($country: Country!, $language: Language!, $first: Int!, $filter: TitleFilter) {
  popularTitles(country: $country, first: $first, filter: $filter) {
    edges { node {
      objectType
      content(country: $country, language: $language) {
        title originalReleaseYear fullPath posterUrl
      }
      offers(country: $country, platform: WEB) {
        monetizationType presentationType standardWebURL retailPriceValue
        package { clearName technicalName }
      }
    } }
  }
}
"""


def fetch(url, data=None, headers=None, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", s)


def read_list(url):
    """Return [{title, year, slug}] for every film on a Letterboxd list, all pages."""
    films, page = [], 1
    while True:
        page_html = fetch(url if page == 1 else f"{url}page/{page}/")
        items = re.findall(r'data-item-name="([^"]*)"[^>]*?data-item-slug="([^"]*)"', page_html)
        if not items:
            break
        for name, slug in items:
            name = html.unescape(name)
            m = re.match(r"^(.*) \((\d{4})\)$", name)
            title, year = (m.group(1), int(m.group(2))) if m else (name, None)
            films.append({"title": title, "year": year, "slug": slug})
        if f"/page/{page + 1}/" not in page_html:
            break
        page += 1
        time.sleep(1)
    return films


def jw_search(title):
    body = {
        "operationName": "Search",
        "query": JW_QUERY,
        "variables": {
            "country": COUNTRY,
            "language": "en",
            "first": 6,
            "filter": {"searchQuery": title, "objectTypes": ["MOVIE"]},
        },
    }
    res = json.loads(fetch(JW_URL, json.dumps(body).encode(), {"Content-Type": "application/json"}))
    if res.get("errors"):
        raise RuntimeError(res["errors"])
    return [e["node"] for e in res["data"]["popularTitles"]["edges"]]


def best_match(film, nodes):
    t, y = norm(film["title"]), film["year"]

    def score(n):
        c = n["content"]
        s = 0
        if norm(c["title"]) == t:
            s += 2
        if y and c["originalReleaseYear"]:
            d = abs(c["originalReleaseYear"] - y)
            s += 3 if d == 0 else 1 if d == 1 else -3
        return s

    ranked = sorted(nodes, key=score, reverse=True)
    if ranked and score(ranked[0]) >= 3:
        return ranked[0]
    return None


def service_for(clear_name):
    base = clean(clear_name)
    for svc, prefixes in SERVICES.items():
        for p in prefixes:
            if base == p or base.startswith(p + " "):
                return svc
    return None


def summarize(node):
    """Collapse JustWatch offers into mine / free / other subs / rent / buy.

    mine = included with our subscriptions at no extra cost. rent/buy = pay per
    film, cheapest digital price per store.
    """
    mine, free, other, rent, buy = {}, {}, {}, {}, {}

    def cheapest(bucket, o):
        if o["presentationType"] not in DIGITAL:
            return
        store = STORE_NAMES.get(o["package"]["clearName"], o["package"]["clearName"])
        price = o.get("retailPriceValue")
        cur = bucket.get(store)
        if cur is None or (price is not None and (cur["price"] is None or price < cur["price"])):
            bucket[store] = {"name": store, "url": o["standardWebURL"], "price": price}

    for o in node["offers"]:
        name, kind, link = o["package"]["clearName"], o["monetizationType"], o["standardWebURL"]
        if kind in STREAM_TYPES:
            svc = service_for(name)
            if svc:
                mine.setdefault(svc, link)
            elif kind in ("ADS", "FREE"):
                free.setdefault(clean(name), link)
            else:
                other.setdefault(clean(name), link)
        elif kind == "RENT":
            cheapest(rent, o)
        elif kind == "BUY":
            cheapest(buy, o)
    order = list(SERVICES)
    return {
        "mine": [{"name": k, "url": v} for k, v in sorted(mine.items(), key=lambda kv: order.index(kv[0]))],
        "free": [{"name": k, "url": v} for k, v in sorted(free.items())],
        "other": [{"name": k, "url": v} for k, v in sorted(other.items())],
        "rent": sorted(rent.values(), key=by_price),
        "buy": sorted(buy.values(), key=by_price),
    }


def by_price(o):
    return (o["price"] is None, o["price"] or 0, o["name"])


def poster(url):
    return "https://images.justwatch.com" + url.replace("{profile}", "s166").replace("{format}", "webp") if url else None


def build():
    films = {}
    for lst in LISTS:
        for f in read_list(lst["url"]):
            entry = films.setdefault(f["slug"], {**f, "lists": []})
            if lst["label"] not in entry["lists"]:
                entry["lists"].append(lst["label"])
        print(f"{lst['label']}: list read", file=sys.stderr)

    out = []
    for i, f in enumerate(films.values(), 1):
        rec = {
            "title": f["title"],
            "year": f["year"],
            "lists": f["lists"],
            "letterboxd": f"https://letterboxd.com/film/{f['slug']}/",
        }
        try:
            node = best_match(f, jw_search(f["title"]))
        except Exception as e:
            print(f"  ! {f['title']}: {e}", file=sys.stderr)
            node = None
        if node:
            rec["justwatch"] = "https://www.justwatch.com" + node["content"]["fullPath"]
            rec["poster"] = poster(node["content"]["posterUrl"])
            rec.update(summarize(node))
        else:
            rec["unmatched"] = True
        out.append(rec)
        print(f"  [{i}/{len(films)}] {f['title']} -> {', '.join(x['name'] for x in rec.get('mine', [])) or '-'}", file=sys.stderr)
        time.sleep(0.3)

    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "services": list(SERVICES),
        "lists": [l["label"] for l in LISTS],
        "films": out,
    }


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "site"
    data = build()
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "data.json"), "w") as fh:
        json.dump(data, fh, indent=1)
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "template.html")) as fh:
        page = fh.read()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    refresh = f"https://github.com/{repo}/actions/workflows/refresh.yml" if repo else ""
    page = page.replace("__DATA__", json.dumps(data).replace("</", "<\\/")).replace("__REFRESH_URL__", refresh)
    with open(os.path.join(out_dir, "index.html"), "w") as fh:
        fh.write(page)
    static = os.path.join(here, "static")
    for name in os.listdir(static):
        shutil.copy(os.path.join(static, name), out_dir)
    print(f"Wrote {out_dir}/index.html ({len(data['films'])} films)", file=sys.stderr)


if __name__ == "__main__":
    main()
