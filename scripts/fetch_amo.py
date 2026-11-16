"""Выгружает из amoCRM всё, что нужно для отчёта, в data/snapshot.json.

Нужны переменные окружения:
  AMO_TOKEN  — долгосрочный токен интеграции amoCRM
  AMO_DOMAIN — домен аккаунта (по умолчанию roregroup.amocrm.ru)

Запуск: python3 scripts/fetch_amo.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOKEN = os.environ.get("AMO_TOKEN")
if not TOKEN:
    sys.exit("Нет AMO_TOKEN: создайте долгосрочный токен в amoCRM и передайте его в переменной окружения.")
BASE = os.environ.get("AMO_BASE_URL", f"https://{C.AMO_DOMAIN}")


def get(path, params=None):
    """GET с учётом лимита amoCRM (до 7 запросов/с) и повтором на 429/5xx. 204 → None."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    for attempt in range(6):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                time.sleep(0.16)
                if r.status == 204:
                    return None
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if e.code == 401:
                sys.exit("amoCRM ответил 401: токен недействителен или истёк.")
            raise
    raise RuntimeError(f"amoCRM не отвечает: {url}")


def paged(path, key, params=None, limit=250):
    params = dict(params or {})
    params["limit"] = limit
    page = 1
    while True:
        params["page"] = page
        j = get(path, params)
        if not j:
            return
        yield from j["_embedded"][key]
        if not j.get("_links", {}).get("next"):
            return
        page += 1


def cf_values(lead):
    out = {}
    for f in lead.get("custom_fields_values") or []:
        vals = [v.get("value") for v in f["values"]]
        out[str(f["field_id"])] = vals[0] if len(vals) == 1 else vals
    return out


def emb_ids(lead, key):
    return [x["id"] for x in (lead.get("_embedded") or {}).get(key) or []]


def leads(pipeline_id):
    res = []
    for l in paged("/api/v4/leads", "leads", {"with": "contacts", f"filter[pipeline_id][]": pipeline_id}):
        res.append({
            "id": l["id"], "name": l["name"], "status_id": l["status_id"],
            "responsible_user_id": l["responsible_user_id"], "created_by": l["created_by"],
            "created_at": l["created_at"], "price": l.get("price") or 0,
            "cf": cf_values(l), "contacts": emb_ids(l, "contacts"), "companies": emb_ids(l, "companies"),
        })
    return res


def last_setter(field_id, lead_ids):
    """Кто последним менял поле сделки → {lead_id: user_id}."""
    out, ts = {}, {}
    params = {"filter[type]": f"custom_field_{field_id}_value_changed", "filter[entity]": "lead",
              "filter[created_at][from]": C.CONF_DATE_TS}
    for e in paged("/api/v4/events", "events", params, limit=100):
        lid = e["entity_id"]
        if lid in lead_ids and e["created_at"] >= ts.get(lid, 0):
            out[lid], ts[lid] = e["created_by"], e["created_at"]
    return out


def main():
    print("Воронки…")
    pipes = {p["id"]: p for p in get("/api/v4/leads/pipelines")["_embedded"]["pipelines"]}
    status_names = {pid: {s["id"]: s["name"] for s in pipes[pid]["_embedded"]["statuses"]}
                    for pid in (C.CONF_PIPELINE_ID, C.NB_PIPELINE_ID)}
    print("Пользователи…")
    users = {u["id"]: u["name"] for u in paged("/api/v4/users", "users")}
    print("Сделки конференции…")
    conf = leads(C.CONF_PIPELINE_ID)
    print(f"  {len(conf)}")
    print("Сделки NewBiz…")
    nb = leads(C.NB_PIPELINE_ID)
    print(f"  {len(nb)}")
    ids = {l["id"] for l in conf}
    print("События по полям «Причина отказа» и «Новый лид»…")
    setters = {"reason": last_setter(C.F_REASON, ids), "new_lead": last_setter(C.F_NEW_LEAD, ids)}

    comp_ids = sorted({c for l in nb for c in l["companies"]})
    companies = {}
    for i in range(0, len(comp_ids), 50):
        chunk = comp_ids[i:i + 50]
        for c in paged("/api/v4/companies", "companies", {"filter[id][]": chunk}):
            companies[c["id"]] = c["name"]

    snap = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domain": C.AMO_DOMAIN,
        "status_names": {str(k): {str(s): n for s, n in v.items()} for k, v in status_names.items()},
        "users": {str(k): v for k, v in users.items()},
        "companies": {str(k): v for k, v in companies.items()},
        "conf_leads": conf, "nb_leads": nb,
        "setters": {k: {str(l): u for l, u in v.items()} for k, v in setters.items()},
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    print("Готово: data/snapshot.json")


if __name__ == "__main__":
    main()
