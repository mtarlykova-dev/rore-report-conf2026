"""Строит REPORT.md и docs/index.html из data/snapshot.json.

Запуск: python3 scripts/build_report.py
"""
import json
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

users = S["users"]
conf_st = S["status_names"][str(C.CONF_PIPELINE_ID)]
nb_st = S["status_names"][str(C.NB_PIPELINE_ID)]


def uname(i):
    return users.get(str(i), str(i)) if i else None


def day(ts):
    return datetime.fromtimestamp(ts, MSK).strftime("%Y-%m-%d")


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


SEG_NAMES = {"off": "Офлайн-участники", "on": "Онлайн-участники", "none": "Не участвовали",
             "emp": "Сотрудники RoRe"}
PROC_NAMES = {"new": "Взяли в работу («Новый лид»)", "active": "«Действующий лид»",
              "manual": "Отказ вручную", "auto": "Закрыты массово", "todo": "Не разобраны"}


# ---------------------------------------------------------------- модель
def segment(l):
    cf = l["cf"]
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


guests = []
for l in S["conf_leads"]:
    cf = l["cf"]
    reason = cf.get(str(C.F_REASON))
    if isinstance(reason, list):
        reason = ", ".join(map(str, reason))
    new = bool(cf.get(str(C.F_NEW_LEAD)))
    r_by = uname(S["setters"]["reason"].get(str(l["id"])))
    n_by = uname(S["setters"]["new_lead"].get(str(l["id"])))
    if new:
        proc, who = "new", n_by
    elif reason == C.REASON_ACTIVE_LEAD:
        proc, who = "active", r_by
    elif reason:
        proc, who = ("auto" if (r_by is None or r_by in C.AUTO_USERS) else "manual"), r_by
    else:
        proc, who = "todo", None
    if who in C.AUTO_USERS:
        who = None
    linked = {i for c in l["contacts"] for i in after_c.get(c, [])}
    if new:  # гостя взяли, а сделку на эту компанию завёл коллега
        linked |= {i for c in l["companies"] for i in after_co.get(c, [])}
    guests.append({
        "id": l["id"], "seg": segment(l), "stage": conf_st.get(str(l["status_id"]), str(l["status_id"])),
        "reason": reason, "proc": proc, "who": who, "nb": sorted(linked),
        "nb_before": sorted({i for c in l["contacts"] for i in before_c.get(c, [])}),
    })

deal_src = defaultdict(set)
for g in guests:
    if g["seg"] != "emp":
        for i in g["nb"]:
            deal_src[i].add(g["id"])
gby = {g["id"]: g for g in guests}
deals = []
for i, gids in deal_src.items():
    n = nb_by_id[i]
    comp = n["companies"][0] if n["companies"] else None
    gs = [gby[x] for x in gids]
    deals.append({
        "id": i, "name": n["name"], "stage": nb_st.get(str(n["status_id"]), str(n["status_id"])),
        "resp": uname(n["responsible_user_id"]), "created": day(n["created_at"]), "budget": n["price"],
        "company": S["companies"].get(str(comp), "") if comp else "",
        "guests": sorted(gids), "segs": sorted({g["seg"] for g in gs}),
        "procs": sorted({g["proc"] for g in gs}), "excluded": C.EXCLUDE_NB_COMPANY_IDS.get(comp),
    })
order = {s: k for k, s in enumerate(C.NB_STAGE_ORDER)}
deals.sort(key=lambda d: (order.get(d["stage"], 99), d["name"]))
counted = [d for d in deals if not d["excluded"]]

# ---------------------------------------------------------------- цифры
seg_cnt = Counter(g["seg"] for g in guests)
off = [g for g in guests if g["seg"] == "off"]
proc_off = Counter(g["proc"] for g in off)
reasons_off = Counter((g["reason"], g["proc"]) for g in off if g["proc"] in ("manual", "auto", "active"))
todo_stage = Counter(g["stage"] for g in off if g["proc"] == "todo")
who = defaultdict(Counter)
for g in guests:
    if g["who"] and g["proc"] in ("new", "active", "manual"):
        who[g["who"]][g["proc"]] += 1
stage_cnt = Counter(d["stage"] for d in counted)
deals_off = [d for d in counted if "off" in d["segs"]]
selected_off = proc_off["new"] + proc_off["active"]
lost = sum(stage_cnt[s] for s in C.NB_LOST)
beyond = sum(v for s, v in stage_cnt.items()
             if s not in C.NB_LOST and order.get(s, 0) > order["Исследование"])
budget = sum(d["budget"] for d in counted)
gen = datetime.fromisoformat(S["generated_at"]).astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")


def conv_rows():
    rows = []
    for seg in ("off", "on", "none"):
        segg = [g for g in guests if g["seg"] == seg]
        any_deal = False
        for proc in ("new", "active", "manual", "auto", "todo"):
            gg = [g for g in segg if g["proc"] == proc]
            ds = {i for g in gg for i in g["nb"] if not excluded(i)}
            if not gg or (not ds and proc not in ("new", "active")):
                continue
            any_deal = any_deal or bool(ds)
            wd = [g for g in gg if any(not excluded(i) for i in g["nb"])]
            rows.append((f"{SEG_NAMES[seg]} · {PROC_NAMES[proc]}", len(gg), len(wd), len(ds)))
        if not any_deal and seg != "off":
            rows.append((f"{SEG_NAMES[seg]} · все", len(segg), 0, 0))
    return rows


# ---------------------------------------------------------------- REPORT.md
md = []
w = md.append
w("# Отработка гостей «Конференции 2026» командой NewBiz")
w("")
w(f"_Обновлено автоматически из amoCRM: **{gen}**. Интерактивная версия — `docs/index.html`._")
w("")
w("## Коротко")
w("")
w(f"- В воронке конференции **{len(guests)}** сделок, офлайн-гостей (без сотрудников) — **{len(off)}**: "
  "это рабочий список NewBiz.")
w(f"- Разобрано **{len(off) - proc_off['todo']}** из {len(off)} ({pct(len(off) - proc_off['todo'], len(off))}). "
  f"Вручную NewBiz: **{proc_off['new']}** «Новый лид», **{proc_off['active']}** «Действующий лид», "
  f"**{proc_off['manual']}** других отказов. Массово закрыто: **{proc_off['auto']}**.")
w(f"- **Не разобрано: {proc_off['todo']}** ({pct(proc_off['todo'], len(off))}).")
w(f"- Сделок в NewBiz из гостей: **{len(counted)}**. От офлайн-гостей — **{len(deals_off)}**: "
  f"{pct(len(deals_off), len(off))} офлайн-аудитории, {pct(len(deals_off), selected_off)} от отобранных NewBiz.")
w(f"- Дальше «Исследования»: **{beyond}**, на «Исследовании»: **{stage_cnt['Исследование']}**, "
  f"выигрыш тендера: **{stage_cnt['Выигрыш тендера']}**, контракт: **{stage_cnt['Контракт заключен']}**, "
  f"отказ/проигрыш: **{lost}**.")
w("")
w("## 1. Состав воронки «Конференция 2026»")
w("")
w("| Сегмент | Сделок | Доля |")
w("|---|---:|---:|")
for k in ("off", "on", "none", "emp"):
    w(f"| {SEG_NAMES[k]} | {seg_cnt[k]} | {pct(seg_cnt[k], len(guests))} |")
w(f"| **Всего** | **{len(guests)}** | 100% |")
w("")
w("## 2. Отработка офлайн-гостей")
w("")
w("| Результат | Сделок | Доля |")
w("|---|---:|---:|")
w(f"| {PROC_NAMES['new']} | {proc_off['new']} | {pct(proc_off['new'], len(off))} |")
for (r, p), v in sorted(reasons_off.items(), key=lambda x: (x[0][1] == "auto", -x[1])):
    tag = " _(массово)_" if p == "auto" else ""
    w(f"| Причина отказа: {r}{tag} | {v} | {pct(v, len(off))} |")
w(f"| **Не разобраны** | **{proc_off['todo']}** | **{pct(proc_off['todo'], len(off))}** |")
w("")
if todo_stage:
    w("Неразобранные по этапам: " + ", ".join(f"{k} — {v}" for k, v in todo_stage.most_common()) + ".")
    w("")
w("### Кто из NewBiz что сделал")
w("")
w("| Сотрудник | Новый лид | Действующий лид | Другие отказы |")
w("|---|---:|---:|---:|")
for p, c in sorted(who.items(), key=lambda x: -sum(x[1].values())):
    w(f"| {p} | {c['new']} | {c['active']} | {c['manual']} |")
w("")
w("Разметка с учёток " + ", ".join(f"«{u}»" for u in sorted(C.AUTO_USERS))
  + " считается массовой, а не ручной работой NewBiz.")
w("")
w("## 3. Конверсия в NewBiz")
w("")
w("Сделка NewBiz связана с гостем, если она создана после конференции и у неё тот же контакт "
  "(для гостей с «Новый лид» — или та же компания).")
w("")
w("| Гости | Всего | Со сделкой в NewBiz | Сделок | Конверсия |")
w("|---|---:|---:|---:|---:|")
for name, n, wd, ds in conv_rows():
    w(f"| {name} | {n} | {wd} | {ds} | {pct(wd, n)} |")
w("")
w("### Этапы")
w("")
w("| Этап NewBiz | Сделок | Доля |")
w("|---|---:|---:|")
for s in C.NB_STAGE_ORDER:
    if stage_cnt[s]:
        w(f"| {s} | {stage_cnt[s]} | {pct(stage_cnt[s], len(counted))} |")
w(f"| **Всего** | **{len(counted)}** | 100% |")
w("")
w(f"Сумма бюджетов: **{budget:,} ₽**".replace(",", " ") + " (у большинства сделок бюджет не заполнен).")
w("")
w("### Сделки")
w("")
w("| Сделка | Этап | Ответственный | Создана | Гости |")
w("|---|---|---|---|---|")
for d in deals:
    mark = " ⚑" if d["excluded"] else ""
    gl = ", ".join(f"[{g}]({AMO}{g})" for g in d["guests"])
    w(f"| [{d['name']}]({AMO}{d['id']}){mark} | {d['stage']} | {d['resp']} | {d['created']} | {gl} |")
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
unmarked = [g for g in guests if g["seg"] != "emp" and g["proc"] in ("todo", "auto")
            and any(not excluded(i) for i in g["nb"])]
if unmarked:
    w(f"- Гости со сделкой в NewBiz, но без отметки «Новый лид»/«Действующий лид»: **{len(unmarked)}** — "
      + ", ".join(f"[{g['id']}]({AMO}{g['id']})" for g in unmarked) + ".")
w("")
w("---")
w("Обновляется GitHub Actions раз в сутки и по кнопке *Run workflow*: "
  "`scripts/fetch_amo.py` → `scripts/build_report.py`. Настройки — `scripts/config.py`.")
(ROOT / "REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")

# ---------------------------------------------------------------- docs/index.html
payload = {
    "generated": gen, "amo": AMO, "segNames": SEG_NAMES, "procNames": PROC_NAMES,
    "stageOrder": C.NB_STAGE_ORDER, "lost": sorted(C.NB_LOST),
    "guests": [[g["id"], g["seg"], g["proc"], g["reason"] or "", g["who"] or "",
                [i for i in g["nb"] if not excluded(i)]] for g in guests],
    "deals": deals,
}
tpl = (ROOT / "scripts" / "template.html").read_text(encoding="utf-8")
data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = tpl.replace("/*__DATA__*/null", data)
(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "index.html").write_text(html, encoding="utf-8")
print(f"OK: гостей {len(guests)}, офлайн {len(off)}, сделок {len(counted)} (+{len(deals) - len(counted)} искл.)")
