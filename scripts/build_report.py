"""Строит REPORT.md и docs/index.html из data/snapshot.json.

Запуск: python3 scripts/build_report.py
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
S = json.loads((ROOT / "data" / "snapshot.json").read_text(encoding="utf-8"))
AMO = f"https://{S['domain']}/leads/detail/"
MSK = timezone(timedelta(hours=3))
DAY = 86400

users = S["users"]
conf_st = S["status_names"][str(C.CONF_PIPELINE_ID)]
nb_st = S["status_names"][str(C.NB_PIPELINE_ID)]
ORDER = {s: k for k, s in enumerate(C.NB_STAGE_ORDER)}


def uname(i):
    return users.get(str(i), str(i)) if i else None


def day(ts):
    return datetime.fromtimestamp(ts, MSK).strftime("%Y-%m-%d")


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def val(cf, fid):
    v = cf.get(str(fid))
    if isinstance(v, list):
        v = ", ".join(map(str, v))
    return v


def setter(kind, lid):
    v = S["setters"][kind].get(str(lid))
    if v is None:
        return None, None
    if isinstance(v, list):
        return uname(v[0]), v[1]
    return uname(v), None


SEG_NAMES = {"off": "Офлайн-участники", "on": "Онлайн-участники", "none": "Не участвовали",
             "emp": "Сотрудники RoRe"}
PROC_NAMES = {"new": "Взяли в работу («Новый лид»)", "active": "«Действующий лид»",
              "manual": "Отказ вручную", "auto": "Закрыты массово", "todo": "Не разобраны"}


def segment(cf):
    if cf.get(str(C.F_EMPLOYEE)):
        return "emp"
    if cf.get(str(C.F_OFFLINE)):
        return "off"
    if cf.get(str(C.F_ONLINE)):
        return "on"
    return "none"


def index(arr, key):
    m = defaultdict(list)
    for n in arr:
        for x in n[key]:
            m[x].append(n["id"])
    return m


nb_after = [n for n in S["nb_leads"] if n["created_at"] > C.CONF_DATE_TS]
nb_before = [n for n in S["nb_leads"] if n["created_at"] <= C.CONF_DATE_TS]
after_c, after_co = index(nb_after, "contacts"), index(nb_after, "companies")
before_c = index(nb_before, "contacts")
nb_by_id = {n["id"]: n for n in S["nb_leads"]}


def excluded(i):
    n = nb_by_id[i]
    return bool(n["companies"]) and n["companies"][0] in C.EXCLUDE_NB_COMPANY_IDS


# ---------------------------------------------------------------- гости
guests = []
for l in S["conf_leads"]:
    cf = l["cf"]
    reason = val(cf, C.F_REASON)
    new = bool(cf.get(str(C.F_NEW_LEAD)))
    r_by, r_ts = setter("reason", l["id"])
    n_by, n_ts = setter("new_lead", l["id"])
    if new:
        proc, who, ts = "new", n_by, n_ts
    elif reason == C.REASON_ACTIVE_LEAD:
        proc, who, ts = "active", r_by, r_ts
    elif reason:
        proc = "auto" if (r_by is None or r_by in C.AUTO_USERS) else "manual"
        who, ts = r_by, r_ts
    else:
        proc, who, ts = "todo", None, None
    if who in C.AUTO_USERS:
        who = None
    linked = {i for c in l["contacts"] for i in after_c.get(c, [])}
    if new:  # гостя взяли, а сделку на эту компанию завёл коллега
        linked |= {i for c in l["companies"] for i in after_co.get(c, [])}
    view = val(cf, C.F_VIEW_PCT)
    try:
        view = float(view) if view is not None else None
    except ValueError:
        view = None
    guests.append({
        "id": l["id"], "seg": segment(cf), "stage": conf_st.get(str(l["status_id"]), str(l["status_id"])),
        "reason": reason, "proc": proc, "who": who,
        "mark_day": (ts - C.CONF_DATE_TS) // DAY if (ts and proc in ("new", "active", "manual")) else None,
        "nb": sorted(linked), "nb_before": sorted({i for c in l["contacts"] for i in before_c.get(c, [])}),
        "src": val(cf, C.F_SOURCE) or "Не указан",
        "reg": bool(cf.get(str(C.F_REGISTERED))), "appr": val(cf, C.F_APPROVED) or "",
        "ver": bool(cf.get(str(C.F_VERIFIED))), "ticket": bool(cf.get(str(C.F_TICKET))), "view": view,
        "target": not (reason in C.NON_TARGET_REASONS),
    })
gby = {g["id"]: g for g in guests}

# ---------------------------------------------------------------- сделки NewBiz
deal_src = defaultdict(set)
for g in guests:
    if g["seg"] != "emp":
        for i in g["nb"]:
            deal_src[i].add(g["id"])


def reached(n):
    """Самый дальний (не проигрышный) этап, на котором была сделка."""
    sts = [n["status_id"]] + [s for _, s in S.get("nb_history", {}).get(str(n["id"]), [])]
    names = [nb_st.get(str(s), "") for s in sts]
    best = max((ORDER.get(x, 0) for x in names if x not in C.NB_LOST), default=ORDER["Исследование"])
    return C.NB_STAGE_ORDER[max(best, ORDER["Исследование"])]


deals = []
for i, gids in deal_src.items():
    n = nb_by_id[i]
    comp = n["companies"][0] if n["companies"] else None
    gs = [gby[x] for x in gids]
    prob = val(n["cf"], C.F_NB_PROB)
    deals.append({
        "id": i, "name": n["name"], "stage": nb_st.get(str(n["status_id"]), str(n["status_id"])),
        "reached": reached(n), "resp": uname(n["responsible_user_id"]), "created": day(n["created_at"]),
        "days": (n["created_at"] - C.CONF_DATE_TS) // DAY, "budget": n["price"],
        "prob": int(prob) if prob and str(prob).isdigit() else None,
        "agency": val(n["cf"], C.F_NB_AGENCY) or "", "lostWhy": val(n["cf"], C.F_NB_LOST_WHY) or "",
        "company": S["companies"].get(str(comp), "") if comp else "",
        "guests": sorted(gids), "segs": sorted({g["seg"] for g in gs}),
        "procs": sorted({g["proc"] for g in gs}), "srcs": sorted({g["src"] for g in gs}),
        "excluded": C.EXCLUDE_NB_COMPANY_IDS.get(comp),
    })
deals.sort(key=lambda d: (ORDER.get(d["stage"], 99), d["name"]))
counted = [d for d in deals if not d["excluded"]]
dby = {d["id"]: d for d in deals}

# ---------------------------------------------------------------- метрики (для REPORT.md)
ext = [g for g in guests if g["seg"] != "emp"]
off = [g for g in ext if g["seg"] == "off"]
seg_cnt = Counter(g["seg"] for g in guests)
proc_off = Counter(g["proc"] for g in off)


def gdeals(gs):
    return {i for g in gs for i in g["nb"] if not excluded(i)}


def at_least(ds, stage):
    return [d for d in ds if ORDER.get(d["reached"], 0) >= ORDER[stage]]


def funnel(gs):
    reg = [g for g in gs if g["reg"]]
    ap = [g for g in reg if g["appr"] == "Офлайн"]
    ver = [g for g in ap if g["ver"]]
    tic = [g for g in ver if g["ticket"]]
    came = [g for g in gs if g["seg"] == "off"]
    tgt = [g for g in came if g["target"]]
    done = [g for g in tgt if g["proc"] != "todo"]
    sel = [g for g in tgt if g["proc"] in ("new", "active")]
    ds = [dby[i] for i in gdeals(came)]
    steps = [
        ("Зарегистрировались", len(reg), "гостей"),
        ("Одобрен офлайн-формат", len(ap), "гостей"),
        ("Прошли верификацию", len(ver), "гостей"),
        ("Получили билет", len(tic), "гостей"),
        ("Пришли офлайн", len(came), "гостей"),
        ("Целевые (не клиенты/партнёры/PR)", len(tgt), "гостей"),
        ("Разобраны NewBiz", len(done), "гостей"),
        ("Отобраны в работу", len(sel), "гостей"),
        ("Сделка в NewBiz", len(ds), "сделок"),
        ("Дальше «Исследования»", len(at_least(ds, "Таргет")), "сделок"),
        ("Встреча назначена и дальше", len(at_least(ds, "Встреча назначена")), "сделок"),
        ("Участие в тендере и дальше", len(at_least(ds, "Участие в тендере")), "сделок"),
        ("Выигрыш / контракт", len(at_least(ds, "Выигрыш тендера")), "сделок"),
    ]
    return steps


steps = funnel(ext)
src_rows = []
for src, cnt in Counter(g["src"] for g in ext).most_common():
    gs = [g for g in ext if g["src"] == src]
    came = [g for g in gs if g["seg"] == "off"]
    sel = [g for g in came if g["proc"] in ("new", "active")]
    ds = gdeals(gs)
    src_rows.append((src, cnt, len(came), len(sel), len(ds)))

lost = sum(1 for d in counted if d["stage"] in C.NB_LOST)
won = [d for d in counted if d["stage"] in ("Выигрыш тендера", "Контракт заключен")]
mark_days = [g["mark_day"] for g in ext if g["mark_day"] is not None]
deal_days = [d["days"] for d in counted]
on_ap = [g for g in ext if g["appr"] == "Онлайн"]
on_came = [g for g in on_ap if g["seg"] == "on"]
off_ap = [g for g in ext if g["appr"] == "Офлайн"]
off_came = [g for g in off_ap if g["seg"] == "off"]
gen = datetime.fromisoformat(S["generated_at"]).astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")
med = lambda xs: f"{statistics.median(xs):.0f}" if xs else "—"  # noqa: E731

# ---------------------------------------------------------------- REPORT.md
md = []
w = md.append
w("# Отработка гостей «Конференции 2026» командой NewBiz")
w("")
w(f"_Обновлено автоматически из amoCRM: **{gen}**. "
  "Интерактивная версия: https://mtarlykova-dev.github.io/rore-report-conf2026/_")
w("")
w("## Коротко")
w("")
w(f"- Офлайн пришли **{len(off)}** гостей (без сотрудников); явка от одобренных на офлайн — "
  f"**{pct(len(off_came), len(off_ap))}**.")
w(f"- Разобрано NewBiz **{len(off) - proc_off['todo']}** из {len(off)}, **не разобрано {proc_off['todo']}**.")
w(f"- Сделок в NewBiz: **{len(counted)}**; от офлайн-гостей — {steps[8][1]} "
  f"(**{pct(steps[8][1], len(off))}** от пришедших, **{pct(steps[8][1], steps[7][1])}** от отобранных).")
w(f"- Дальше «Исследования» дошли **{steps[9][1]}** сделок, до встречи — **{steps[10][1]}**, "
  f"до тендера — **{steps[11][1]}**, выигрыш/контракт — **{steps[12][1]}**; отказ/проигрыш сейчас — {lost}.")
w(f"- Скорость: медиана **{med(mark_days)}** дн. от конференции до разметки гостя и **{med(deal_days)}** дн. "
  "до создания сделки.")
w("")
w("## 1. Сквозная воронка: регистрация → сделка")
w("")
w("| Шаг | Кол-во | От предыдущего шага | От регистрации |")
w("|---|---:|---:|---:|")
prev = None
for name, v, unit in steps:
    w(f"| {name} | {v} {unit} | {pct(v, prev) if prev is not None else '—'} | {pct(v, steps[0][1])} |")
    prev = v
w("")
w("Шаги до «Пришли офлайн» — по полям регистрации; шаги после «Сделка в NewBiz» — по самому дальнему этапу "
  "в истории сделки (если сделку потом проиграли, она всё равно засчитана на достигнутом этапе).")
w("")
w("## 2. Явка")
w("")
w("| Формат | Одобрено | Пришли / смотрели | Явка |")
w("|---|---:|---:|---:|")
w(f"| Офлайн | {len(off_ap)} | {len(off_came)} | {pct(len(off_came), len(off_ap))} |")
w(f"| Онлайн | {len(on_ap)} | {len(on_came)} | {pct(len(on_came), len(on_ap))} |")
views = [g["view"] for g in ext if g["seg"] == "on" and g["view"] is not None]
if views:
    w("")
    w(f"Онлайн: медиана просмотра трансляции **{med(views)}%**, досмотрели больше половины — "
      f"**{sum(v >= 50 for v in views)}** из {len(views)}. Онлайн-гостей NewBiz не отрабатывал — это резерв.")
w("")
w("## 3. Конверсия по источнику лида")
w("")
w("| Источник | Гостей | Пришли офлайн | Отобраны NewBiz | Сделок NewBiz | Гость → сделка |")
w("|---|---:|---:|---:|---:|---:|")
for src, n, came, sel, ds in src_rows:
    w(f"| {src} | {n} | {came} ({pct(came, n)}) | {sel} | {ds} | {pct(ds, n)} |")
w("")
w("## 4. Отработка офлайн-гостей")
w("")
w("| Результат | Гостей | Доля |")
w("|---|---:|---:|")
for k in ("new", "active", "manual", "auto", "todo"):
    w(f"| {PROC_NAMES[k]} | {proc_off[k]} | {pct(proc_off[k], len(off))} |")
w("")
w("### По сотрудникам")
w("")
w("| Сотрудник | Разобрал | Отобрал | Сделок NewBiz | Дальше «Исследования» | Отобран → сделка |")
w("|---|---:|---:|---:|---:|---:|")
people = defaultdict(list)
for g in ext:
    if g["who"] and g["proc"] in ("new", "active", "manual"):
        people[g["who"]].append(g)
for p, gs in sorted(people.items(), key=lambda x: -len(x[1])):
    sel = [g for g in gs if g["proc"] in ("new", "active")]
    ds = [dby[i] for i in gdeals(sel)]
    wd = [g for g in sel if any(not excluded(i) for i in g["nb"])]
    w(f"| {p} | {len(gs)} | {len(sel)} | {len(ds)} | {len(at_least(ds, 'Таргет'))} | {pct(len(wd), len(sel))} |")
w("")
w("## 5. Сделки NewBiz")
w("")
w("| Этап сейчас | Сделок |")
w("|---|---:|")
sc = Counter(d["stage"] for d in counted)
for s in C.NB_STAGE_ORDER:
    if sc[s]:
        w(f"| {s} | {sc[s]} |")
w(f"| **Всего** | **{len(counted)}** |")
w("")
budget = sum(d["budget"] for d in counted)
w(f"Бюджет в сделках: **{budget:,} ₽**, в выигранных — **{sum(d['budget'] for d in won):,} ₽**. ".replace(",", " ")
  + f"Бюджет заполнен в {sum(1 for d in counted if d['budget'])} из {len(counted)} сделок, "
  f"вероятность — в {sum(1 for d in counted if d['prob'] is not None)}.")
w("")
w("| Сделка | Этап | Дошла до | Ответственный | Создана | Гости |")
w("|---|---|---|---|---|---|")
for d in deals:
    mark = " ⚑" if d["excluded"] else ""
    gl = ", ".join(f"[{g}]({AMO}{g})" for g in d["guests"])
    w(f"| [{d['name']}]({AMO}{d['id']}){mark} | {d['stage']} | {d['reached']} | {d['resp']} | {d['created']} | {gl} |")
w("")
if any(d["excluded"] for d in deals):
    w("⚑ — не в конверсии: " + "; ".join(sorted(set(C.EXCLUDE_NB_COMPANY_IDS.values()))) + ".")
    w("")
w("## Что проверить")
w("")
w(f"- Не разобраны: **{proc_off['todo']}** офлайн-гостей.")
al_none = [g for g in off if g["proc"] == "active" and not g["nb"] and not g["nb_before"]]
if al_none:
    w("- «Действующий лид», но сделки в NewBiz нет ни до, ни после конференции: "
      + ", ".join(f"[{g['id']}]({AMO}{g['id']})" for g in al_none) + ".")
new_none = [g for g in off if g["proc"] == "new" and not g["nb"]]
if new_none:
    w("- «Новый лид», но сделка в NewBiz не найдена: "
      + ", ".join(f"[{g['id']}]({AMO}{g['id']})" for g in new_none) + ".")
unmarked = [g for g in ext if g["proc"] in ("todo", "auto") and any(not excluded(i) for i in g["nb"])]
if unmarked:
    w(f"- Гости со сделкой в NewBiz, но без отметки «Новый лид»/«Действующий лид»: **{len(unmarked)}** — "
      + ", ".join(f"[{g['id']}]({AMO}{g['id']})" for g in unmarked) + ".")
w("")
w("---")
w("Обновляется GitHub Actions раз в сутки и по кнопке *Run workflow*. Настройки — `scripts/config.py`, "
  "методика — README.")
(ROOT / "REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- docs/index.html
G_KEYS = ["id", "seg", "proc", "reason", "who", "nb", "src", "reg", "appr", "ver", "ticket", "view",
          "target", "mark_day"]
payload = {
    "generated": gen, "amo": AMO, "segNames": SEG_NAMES, "procNames": PROC_NAMES,
    "stageOrder": C.NB_STAGE_ORDER, "lost": sorted(C.NB_LOST), "empCount": seg_cnt["emp"],
    "gKeys": G_KEYS,
    "guests": [[g[k] if k != "nb" else [i for i in g["nb"] if not excluded(i)] for k in G_KEYS]
               for g in ext],
    "deals": deals,
}
tpl = (ROOT / "scripts" / "template.html").read_text(encoding="utf-8")
data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "index.html").write_text(tpl.replace("/*__DATA__*/null", data), encoding="utf-8")
print(f"OK: гостей {len(guests)}, офлайн {len(off)}, сделок {len(counted)} (+{len(deals) - len(counted)} искл.)")
