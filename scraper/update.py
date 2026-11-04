#!/usr/bin/env python3
"""Собирает объявления о продаже квартир с Циана, фильтрует их по config.json
и обновляет data/flats.json (подборка) и data/history.json (история цен).

Запуск:  python scraper/update.py
Тест без сети:  python scraper/update.py --fixture tests/sample_offers.json
"""
import argparse
import datetime as dt
import json
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.cian.ru",
    "Referer": "https://www.cian.ru/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
}
MSK = dt.timezone(dt.timedelta(hours=3))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- сеть

def post(json_query, retries=3):
    body = json.dumps({"jsonQuery": json_query}).encode()
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                text = r.read().decode("utf-8", "replace")
                status = r.status
            try:
                data = json.loads(text)
            except ValueError:
                raise ValueError(f"HTTP {status}, не JSON (вероятно, защита от ботов): {text[:200]!r}")
            if "data" not in data:
                raise ValueError("неожиданный ответ: " + str(data)[:200])
            return data["data"]
        except urllib.error.HTTPError as e:
            last = ValueError(f"HTTP {e.code}: {e.read()[:200]!r}")
            log(f"  запрос не удался ({last}), попытка {attempt + 1}/{retries}")
            time.sleep(5 * (attempt + 1))
            continue
        except (urllib.error.URLError, ValueError, TimeoutError) as e:
            last = e
            log(f"  запрос не удался ({e}), попытка {attempt + 1}/{retries}")
            time.sleep(5 * (attempt + 1))
    if "Captcha" in str(last):
        raise RuntimeError(
            "Циан показал капчу: с этого IP-адреса он не отдаёт данные. "
            "Сбор нужно запускать с сервера в России, см. docs/vps.md."
        )
    raise RuntimeError(f"Циан не отвечает: {last}")


def pause():
    time.sleep(1.5 + random.random() * 1.5)


def base_query(cfg):
    return {
        "_type": "flatsale",
        "engine_version": {"type": "term", "value": 2},
        "region": {"type": "terms", "value": [1]},
        "geo": {"type": "geo", "value": [{"type": "district", "id": int(i)} for i in cfg["districts"]]},
    }


def search_query(cfg, page, sort=None):
    q = base_query(cfg)
    q.update({
        "room": {"type": "terms", "value": cfg["rooms"]},
        "price": {"type": "range", "value": {"lte": cfg["max_price"]}},
        "total_area": {"type": "range", "value": {"gte": cfg["min_area"]}},
        "kitchen": {"type": "range", "value": {"gte": cfg["min_kitchen"]}},
        "page": {"type": "term", "value": page},
    })
    if cfg.get("exclude_first_floor"):
        q["is_first_floor"] = {"type": "term", "value": False}
    if sort:
        q["sort"] = {"type": "term", "value": sort}
    return q


def fetch_offers(cfg):
    """Циан отдаёт выдачу постранично и иногда с повторами, поэтому проходим
    несколько сортировок и склеиваем по id."""
    seen = {}
    total = None
    for sort in (None, "creation_date_desc", "price_object_order"):
        for page in range(1, cfg.get("max_pages", 6) + 1):
            d = post(search_query(cfg, page, sort))
            total = d.get("offerCount", total)
            offers = d.get("offersSerialized") or []
            for o in offers:
                seen.setdefault(offer_id(o), o)
            log(f"  сортировка={sort or 'default'} стр.{page}: {len(offers)} шт., всего уникальных {len(seen)}")
            pause()
            if len(offers) < 20:
                break
        if total is not None and len(seen) >= total:
            break
    return list(seen.values()), total


def fetch_medians(cfg):
    lo, hi = cfg["median_area_range"]
    res = {}
    for did, name in cfg["districts"].items():
        q = base_query(cfg)
        q["geo"] = {"type": "geo", "value": [{"type": "district", "id": int(did)}]}
        q.update({
            "room": {"type": "terms", "value": [2]},
            "object_type": {"type": "terms", "value": [1]},
            "total_area": {"type": "range", "value": {"gte": lo, "lte": hi}},
        })
        ppm = []
        for page in (1, 2, 3):
            q["page"] = {"type": "term", "value": page}
            d = post(q)
            offers = d.get("offersSerialized") or []
            for o in offers:
                if o.get("shareAmount") or o.get("isApartments"):
                    continue
                p, a = price_of(o), num(o.get("totalArea"))
                if p and a and p > 5_000_000:
                    ppm.append(p / a / 1000)
            pause()
            if len(offers) < 20:
                break
        if ppm:
            res[name] = round(statistics.median(ppm))
        log(f"  медиана {name}: {res.get(name)} тыс/м² по {len(ppm)} объявл.")
    return res


# ---------------------------------------------------------------- разбор

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def offer_id(o):
    return int(o.get("cianId") or o.get("id"))


def price_of(o):
    bt = o.get("bargainTerms") or {}
    return bt.get("priceRur") or bt.get("price")


def metro(o, cfg):
    bad = set(cfg.get("not_metro_stations", []))
    walk, transport = [], []
    for u in (o.get("geo") or {}).get("undergrounds") or []:
        lt = (u.get("lineType") or "").lower()
        if "mcd" in lt or "mcc" in lt or u.get("name") in bad or u.get("time") is None:
            continue
        (walk if u.get("transportType") == "walk" else transport).append((u["time"], u["name"]))
    w = min(walk) if walk else None
    t = min(transport) if transport else None
    return w, t


def district_of(o, cfg):
    names = set(cfg["districts"].values())
    for d in (o.get("geo") or {}).get("districts") or []:
        if d.get("name") in names:
            return d["name"]
    ds = (o.get("geo") or {}).get("districts") or []
    return ds[0]["name"] if ds else ""


def clean_address(s):
    s = re.sub(r"^(Россия,\s*)?(город\s+)?Москва( город)?,\s*", "", s or "")
    s = re.sub(r"^[А-Яа-яё .-]+ район,\s*", "", s)
    s = re.sub(r"^(Северо-Восточный|Северный) административный округ,\s*", "", s)
    return s.strip()


WC = r"(сан\.?\s*узел|санузл\w*|с\s*[/\\]\s*у(зел)?)"
WC_SEPARATE = WC + r"\s*(-|—)?\s*раздельн|раздельн\w*\s*" + WC

MATERIAL = {"panel": "панель", "monolith": "монолит", "brick": "кирпич", "block": "блочный",
            "monolithBrick": "монолит-кирпич", "wood": "дерево", "stalin": "сталинский"}


def evaluate(o, cfg):
    """Возвращает (квартира, None) или (None, причина отсева)."""
    rooms = o.get("roomsCount")
    price = price_of(o)
    area = num(o.get("totalArea"))
    kitchen = num(o.get("kitchenArea"))
    floor = o.get("floorNumber")
    b = o.get("building") or {}
    floors = b.get("floorsCount")
    desc = (o.get("description") or "").lower()

    if rooms not in cfg["rooms"]:
        return None, "другое число комнат"
    if cfg.get("exclude_shares") and (o.get("shareAmount") or "share" in (o.get("category") or "").lower()):
        return None, "доля"
    if cfg.get("exclude_apartments") and o.get("isApartments"):
        return None, "апартаменты"
    if not price or price > cfg["max_price"]:
        return None, "дороже бюджета"
    if not area or area < cfg["min_area"]:
        return None, "маленькая площадь"
    if not kitchen or kitchen < cfg["min_kitchen"]:
        return None, "маленькая кухня"
    if cfg.get("exclude_first_floor") and floor == 1:
        return None, "первый этаж"
    if cfg.get("exclude_last_floor") and floor and floors and floor >= floors:
        return None, "последний этаж"
    if floors and floors < cfg.get("min_floors_in_house", 0):
        return None, "пятиэтажка/малоэтажный дом"
    if cfg.get("exclude_renovation") and o.get("demolishedInMoscowProgramm"):
        return None, "дом под реновацию"
    balc, logg = o.get("balconiesCount") or 0, o.get("loggiasCount") or 0
    if cfg.get("require_balcony") and balc + logg == 0:
        return None, "нет балкона/лоджии"
    if cfg.get("exclude_adjacent_rooms") and "смежн" in desc and "изолир" not in desc:
        return None, "смежные комнаты"

    walk, transport = metro(o, cfg)
    if walk and walk[0] <= cfg["max_walk_min"]:
        access = "walk"
    elif walk is None and transport and transport[0] <= cfg["max_transport_min"]:
        access = "transport"
    else:
        return None, "далеко от метро"

    group = "c" if rooms == 3 else ("a" if access == "walk" else "b")
    year = b.get("buildYear") or ((b.get("deadline") or {}).get("year"))
    flat = {
        "id": offer_id(o),
        "url": f"https://www.cian.ru/sale/flat/{offer_id(o)}/",
        "group": group,
        "rooms": rooms,
        "price": int(price),
        "area": area,
        "kitchen": kitchen,
        "floor": floor,
        "floors": floors,
        "address": clean_address((o.get("geo") or {}).get("userInput")),
        "district": district_of(o, cfg),
        "metro": f"{walk[1]} {walk[0]} пеш" if access == "walk" else f"{transport[1]} ~{transport[0]} тр",
        "walk_min": walk[0] if walk else None,
        "house": ", ".join(x for x in [MATERIAL.get(b.get("materialType"), ""), str(year or "")] if x),
        "new_building": (o.get("category") or "").startswith("newBuilding"),
        "balcony": balc,
        "loggia": logg,
        "parking": (b.get("parking") or {}).get("type"),
        "sale_type": (o.get("bargainTerms") or {}).get("saleType"),
        "owner": bool(o.get("isByHomeowner")),
        "isolated": "изолир" in desc,
        "separate_wc": bool(re.search(WC_SEPARATE, desc)),
        "added_ts": o.get("addedTimestamp"),
    }
    return flat, None


# ---------------------------------------------------------------- сборка

def dedupe(flats):
    """Одна квартира у разных агентов: тот же район, площадь, этаж и этажность."""
    groups = {}
    for f in flats:
        key = (f["district"], round(f["area"], 1), f["floor"], f["floors"])
        groups.setdefault(key, []).append(f)
    out = []
    for g in groups.values():
        g.sort(key=lambda f: f["price"])
        main = dict(g[0])
        main["duplicates"] = [{"id": d["id"], "url": d["url"], "price": d["price"]} for d in g[1:]]
        out.append(main)
    return out


def score(f, med):
    s = 0.0
    if f.get("vs_median") is not None:
        # каждый процент ниже медианы = +1, но не больше 20: в районах с дорогими
        # новостройками (Марфино) медиана завышена и иначе забивает остальные плюсы
        s += min(20, -f["vs_median"])
    s += 3 if f["loggia"] else 0
    s += 2 if f["separate_wc"] else 0
    s += 1 if f["isolated"] else 0
    s += 2 if (f["walk_min"] or 99) <= 10 else 0
    s += 1 if f["sale_type"] == "free" else 0
    s += 1 if f["kitchen"] >= 10 else 0
    return round(s, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="JSON со списком сырых объявлений вместо запроса к Циану")
    ap.add_argument("--today", help="дата прогона YYYY-MM-DD (для тестов)")
    ap.add_argument("--check", action="store_true", help="только проверить, отдаёт ли Циан данные с этого IP")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    if args.check:
        try:
            d = post(search_query(cfg, 1), retries=1)
        except RuntimeError as e:
            print(f"НЕ РАБОТАЕТ: {e}")
            sys.exit(1)
        print(f"ЦИАН ОТВЕЧАЕТ: найдено {d.get('offerCount')} объявлений по вашим условиям")
        return
    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    hist_path = os.path.join(data_dir, "history.json")
    flats_path = os.path.join(data_dir, "flats.json")
    history = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else {}
    old = json.load(open(flats_path, encoding="utf-8")) if os.path.exists(flats_path) else {}

    now = dt.datetime.now(MSK)
    today = args.today or now.date().isoformat()

    if args.fixture:
        raw = json.load(open(args.fixture, encoding="utf-8"))
        total = len(raw)
        medians = old.get("medians") or {}
    else:
        log("Загружаю объявления…")
        raw, total = fetch_offers(cfg)
        log("Считаю медианы по районам…")
        try:
            medians = fetch_medians(cfg)
        except RuntimeError as e:
            log(f"  медианы не обновлены: {e}")
            medians = old.get("medians") or {}
    if not raw:
        log("Циан вернул пустую выдачу — данные не трогаю.")
        sys.exit(1)

    passed, excluded = [], {}
    for o in raw:
        f, why = evaluate(o, cfg)
        if f:
            passed.append(f)
        else:
            excluded[why] = excluded.get(why, 0) + 1

    # история цен по всем прошедшим фильтр объявлениям (включая дубли)
    for f in passed:
        h = history.setdefault(str(f["id"]), {"first_seen": today, "prices": []})
        if not h["prices"] or h["prices"][-1][1] != f["price"]:
            h["prices"].append([today, f["price"]])
        h["last_seen"] = today
        h["address"] = f["address"]

    flats = dedupe(passed)
    new_cut = (dt.date.fromisoformat(today) - dt.timedelta(days=cfg.get("new_badge_days", 7))).isoformat()
    for f in flats:
        h = history[str(f["id"])]
        f["first_seen"] = h["first_seen"]
        f["is_new"] = h["first_seen"] >= new_cut and h["first_seen"] != history.get("_first_run", today)
        first_price = h["prices"][0][1]
        f["price_change_pct"] = round((f["price"] / first_price - 1) * 100, 1) if first_price != f["price"] else 0
        f["price_history"] = h["prices"]
        m = medians.get(f["district"])
        f["ppm"] = round(f["price"] / f["area"] / 1000)
        f["vs_median"] = round((f["ppm"] / m - 1) * 100) if m else None
        f["score"] = score(f, m)
    history.setdefault("_first_run", today)

    top = sorted(flats, key=lambda f: -f["score"])[:5]
    for f in flats:
        f["top"] = f in top

    active_ids = {str(f["id"]) for f in passed}
    gone = []
    for k, h in history.items():
        if k.startswith("_") or k in active_ids:
            continue
        if h.get("last_seen") and h["last_seen"] >= (dt.date.fromisoformat(today) - dt.timedelta(days=14)).isoformat():
            gone.append({"id": int(k), "address": h.get("address"), "last_price": h["prices"][-1][1], "last_seen": h["last_seen"]})

    out = {
        "updated": now.isoformat(timespec="minutes"),
        "source": "cian.ru",
        "criteria": cfg,
        "medians": medians,
        "stats": {"fetched": len(raw), "cian_total": total, "passed": len(flats), "excluded": excluded},
        "flats": sorted(flats, key=lambda f: (f["group"], f["ppm"])),
        "gone": sorted(gone, key=lambda g: g["last_seen"], reverse=True),
    }
    json.dump(out, open(flats_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(history, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    new_n = sum(f["is_new"] for f in flats)
    drops = sum(1 for f in flats if f["price_change_pct"] < 0)
    log(f"Готово: получено {len(raw)}, подходит {len(flats)}, новых {new_n}, со снижением цены {drops}.")
    log(f"Отсеяно: {excluded}")


if __name__ == "__main__":
    main()
