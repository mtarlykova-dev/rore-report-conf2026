"""Собирает REPORT.md из выгрузки amoCRM в data/.

Запуск: python3 scripts/build_report.py
"""
import csv
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
AMO = "https://roregroup.amocrm.ru/leads/detail/"

CONF_STATUS = {"142": "Участие", "143": "Не участие", "87065186": "Факт присутствия"}
SEG = {"off": "Офлайн-участники", "on": "Онлайн-участники", "none": "Не участвовали",
       "emp": "Сотрудники RoRe"}
NB_ORDER = ["Исследование", "Таргет", "Первый контакт", "Встреча назначена", "Прогрев",
            "Подготовка к тендеру", "Участие в тендере", "Выигрыш тендера",
            "Контракт заключен", "Отказ", "Проигрыш"]
NB_ACTIVE = NB_ORDER[:8]
NB_PAST_RESEARCH = NB_ORDER[1:9]
AUTO = "Родная Речь"
MANUAL_REJECT = {"Нет бюджета", "Не профильная компания", "Не актуально сейчас",
                 "Не целевой контакт", "Нет потребности", "Не наш сегмент / рынок",
                 "Уже работает с другим подрядчиком", "Отказался от дальнейшего контакта",
                 "Не отвечает / не выходит на связь", "Дубль", "Спам / мусорный лид"}


def read(name, delim):
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f, delimiter=delim))


agg = read("conf_aggregate.csv", "|")
for r in agg:
    r["count"] = int(r["count"])
linked = read("conf_leads_linked.csv", "|")
nb = {d["nb_lead_id"]: d for d in read("newbiz_linked_deals.csv", "\t")}


def ids(s):
    return [x for x in s.split(",") if x]


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def link(i):
    return f"[{i}]({AMO}{i})"


out = []
w = out.append
total = sum(r["count"] for r in agg)

# ---------- 1. Состав воронки ----------
seg_cnt = Counter()
for r in agg:
    seg_cnt[r["segment"]] += r["count"]
status_cnt = Counter()
for r in agg:
    status_cnt[r["status_id"]] += r["count"]

# ---------- 2. Офлайн-участники: отработка ----------
off = [r for r in agg if r["segment"] == "off"]
off_total = sum(r["count"] for r in off)
took = sum(r["count"] for r in off if r["new_lead"] == "1")
reason_rows = [r for r in off if r["reason"] != "-" and r["new_lead"] == "0"]
by_reason_manual = Counter()
by_reason_auto = Counter()
for r in reason_rows:
    (by_reason_auto if r["reason_set_by"] == AUTO else by_reason_manual)[r["reason"]] += r["count"]
untouched = sum(r["count"] for r in off if r["reason"] == "-" and r["new_lead"] == "0")
untouched_in_fact = sum(r["count"] for r in off if r["reason"] == "-" and r["new_lead"] == "0"
                        and r["status_id"] == "87065186")
manual_total = took + sum(by_reason_manual.values())
auto_total = sum(by_reason_auto.values())
active_lead = by_reason_manual.get("Действующий лид", 0)

# кто что сделал вручную
who = defaultdict(Counter)
for r in linked:
    if r["new_lead"] == "1" and r["new_lead_set_by"] not in ("-", AUTO):
        who[r["new_lead_set_by"]]["Новый лид"] += 1
    if r["reason"] != "-" and r["reason_set_by"] not in ("-", AUTO):
        who[r["reason_set_by"]][r["reason"]] += 1

# ---------- 3. Связь с NewBiz ----------
groups = {
    "Офлайн · «Новый лид»": [r for r in linked if r["segment"] == "off" and r["new_lead"] == "1"],
    "Офлайн · «Действующий лид»": [r for r in linked if r["segment"] == "off" and r["reason"] == "Действующий лид"],
    "Не участвовали (регистрация без визита)": [r for r in linked if r["segment"] == "none"],
}
group_deals = {}
for g, rows in groups.items():
    s = set()
    for r in rows:
        s.update(ids(r["nb_after_by_contact"]))
        if g.endswith("«Новый лид»"):
            s.update(ids(r["nb_after_by_company"]))  # 3 гостя с той же компанией, что уже перенесена
    group_deals[g] = s
all_conv = set().union(*group_deals.values())

# Национальные приоритеты — действующий клиент DARR, сделки до конференции уже были
np_deals = {i for i in all_conv if "ациональные" in nb[i]["name"]}
clean_conv = all_conv - np_deals

stage_cnt = Counter(nb[i]["stage"] for i in clean_conv)

# ---------- write ----------
w("# Отработка гостей «Конференции 2026» командой NewBiz")
w("")
w("Дата конференции: **16.07.2026**. Срез данных из amoCRM: **17.11.2026**. "
  "Воронки: «Конференция 2026» (id 10631686) и «NewBiz» (id 9673226).")
w("")
w("## Коротко")
w("")
w(f"- В воронке конференции **{total}** сделок, из них офлайн-гостей (без сотрудников RoRe) — **{off_total}**. "
  "Именно они — рабочий список NewBiz по инструкции (этап «Факт присутствия»).")
w(f"- Разобрано **{off_total - untouched}** из {off_total} офлайн-гостей ({pct(off_total - untouched, off_total)}). "
  f"Вручную командой NewBiz — **{manual_total}** ({took} взяты как «Новый лид», "
  f"{active_lead} — «Действующий лид», {manual_total - took - active_lead} — отказ по другим причинам). "
  f"Ещё **{auto_total}** закрыты массово с учётной записи «{AUTO}» (клиенты, партнёры, PR-партнёры и т.п.).")
w(f"- **Не разобрано: {untouched}** офлайн-гостей ({pct(untouched, off_total)}) — " +
  ("все они" if untouched_in_fact == untouched else f"{untouched_in_fact} из них") + " так и стоят на этапе «Факт присутствия» без причины отказа и без отметки «Новый лид».")
conv_off = group_deals["Офлайн · «Новый лид»"] | group_deals["Офлайн · «Действующий лид»"]
w(f"- В воронку NewBiz из гостей конференции перешло **{len(clean_conv)}** сделок "
  f"(+{len(np_deals)} сделки «Национальных приоритетов» — действующего клиента DARR, их считаем отдельно). "
  f"От офлайн-гостей — **{len(conv_off)}** сделок, это **{pct(len(conv_off), off_total)}** офлайн-аудитории.")
past = sum(stage_cnt[s] for s in NB_PAST_RESEARCH)
w(f"- Из {len(clean_conv)} сделок **{stage_cnt['Исследование']}** ещё на этапе «Исследование», "
  f"**{past}** продвинулись дальше, "
  f"**{stage_cnt['Выигрыш тендера']}** — «Выигрыш тендера», "
  f"**{stage_cnt['Отказ'] + stage_cnt['Проигрыш']}** — отказ/проигрыш. "
  "Онлайн-участники в NewBiz не перешли ни разу.")
w("")

w("## 1. Кто был в воронке «Конференция 2026»")
w("")
w("| Сегмент | Сделок | Доля |")
w("|---|---:|---:|")
for k in ["off", "on", "none", "emp"]:
    w(f"| {SEG[k]} | {seg_cnt[k]} | {pct(seg_cnt[k], total)} |")
w(f"| **Всего** | **{total}** | 100% |")
w("")
w("Сегмент определён по полям сделки: «Сотрудник» → сотрудники; «Участвовал офлайн» → офлайн; "
  "«Участвовал онлайн» → онлайн; иначе — не участвовал. Этапы: "
  + ", ".join(f"{CONF_STATUS[k]} — {status_cnt[k]}" for k in ["142", "87065186", "143"]) + ".")
w("")

w("## 2. Как отработали офлайн-гостей")
w("")
w("```mermaid")
w("pie showData title Офлайн-гости конференции (" + str(off_total) + ")")
w(f'  "Взяли: Новый лид" : {took}')
w(f'  "Действующий лид" : {active_lead}')
w(f'  "Отказ вручную (NewBiz)" : {manual_total - took - active_lead}')
w(f'  "Закрыты массово (клиенты/партнёры)" : {auto_total}')
w(f'  "Не разобраны" : {untouched}')
w("```")
w("")
w("| Результат | Сделок | Доля от офлайн-гостей | Кто проставил |")
w("|---|---:|---:|---|")
w(f"| Взяли в работу («Новый лид») | {took} | {pct(took, off_total)} | NewBiz |")
for k, v in by_reason_manual.most_common():
    w(f"| Причина отказа: {k} | {v} | {pct(v, off_total)} | NewBiz |")
for k, v in by_reason_auto.most_common():
    w(f"| Причина отказа: {k} | {v} | {pct(v, off_total)} | массово («{AUTO}») |")
w(f"| **Не разобраны** | **{untouched}** | **{pct(untouched, off_total)}** | — |")
w(f"| Итого | {off_total} | 100% | |")
w("")
w("### Кто из NewBiz что сделал")
w("")
w("| Сотрудник | Новый лид | Действующий лид | Другие отказы |")
w("|---|---:|---:|---:|")
for p, c in sorted(who.items(), key=lambda x: -sum(x[1].values())):
    other = sum(v for k, v in c.items() if k not in ("Новый лид", "Действующий лид"))
    w(f"| {p} | {c['Новый лид']} | {c['Действующий лид']} | {other} |")
w("")
w("> «Родная Речь» — общая админская учётка: с неё массово проставлены «Сотрудник», «Действующий клиент», "
  "«Действующий партнёр», «PR-партнёр» и т.д. Это не ручная отработка NewBiz, поэтому показано отдельно.")
w("")
w("Сотрудники RoRe (" + str(seg_cnt["emp"]) + ") и онлайн-участники в рабочий список не входили; "
  "у онлайн-участников причины отказа проставлены только массово "
  f"({sum(r['count'] for r in agg if r['segment'] == 'on' and r['reason'] != '-')} шт.).")
w("")

w("## 3. Что перешло в NewBiz")
w("")
w("Сделка NewBiz считается пришедшей с конференции, если она **создана после 16.07.2026** и у неё **тот же контакт**, "
  "что у гостя в воронке конференции (для 3 гостей с отметкой «Новый лид» связь найдена по компании — "
  "сделку на компанию уже завёл коллега).")
w("")
w("| Откуда | Гостей | Сделок в NewBiz | Конверсия гость → сделка |")
w("|---|---:|---:|---:|")
base = {"Офлайн · «Новый лид»": took, "Офлайн · «Действующий лид»": active_lead}
for g, rows in groups.items():
    deals = group_deals[g] - np_deals
    guests = [r for r in rows if (set(ids(r["nb_after_by_contact"])) | (set(ids(r["nb_after_by_company"])) if "Новый" in g else set())) - np_deals]
    b = base.get(g, seg_cnt["none"])
    w(f"| {g} | {len(guests)} из {b} | {len(deals)} | {pct(len(guests), b)} |")
w(f"| Онлайн-участники | 0 из {seg_cnt['on']} | 0 | 0% |")
w(f"| **Итого (без Нацприоритетов)** | | **{len(clean_conv)}** | |")
w("")
w("Конверсия от всего офлайн-потока: "
  f"**{len(conv_off)} сделок / {off_total} гостей = {pct(len(conv_off), off_total)}**; "
  f"от отобранных NewBiz лидов («Новый лид» + «Действующий лид» = {took + active_lead}): "
  f"**{pct(len(conv_off), took + active_lead)}** (несколько гостей из одной компании → одна сделка).")
w("")

w("### На каком этапе сейчас")
w("")
w("| Этап NewBiz | Сделок | Доля |")
w("|---|---:|---:|")
for s in NB_ORDER:
    if stage_cnt[s]:
        w(f"| {s} | {stage_cnt[s]} | {pct(stage_cnt[s], len(clean_conv))} |")
w(f"| **Всего** | **{len(clean_conv)}** | 100% |")
w("")
active = sum(stage_cnt[s] for s in NB_ACTIVE)
budget = sum(int(nb[i]["budget"]) for i in clean_conv)
w(f"В активной работе (не отказ/проигрыш): **{active}**. Сумма бюджетов в сделках: **{budget:,} ₽** "
  "(у большинства сделок бюджет не заполнен).".replace(",", " "))
w("")
w("```mermaid")
w("flowchart LR")
w(f'  A["Офлайн-гости<br/>{off_total}"] --> B["Отобраны NewBiz<br/>{took + active_lead}"]')
w(f'  B --> C["Сделки в NewBiz<br/>{len(conv_off)}"]')
w(f'  N["Не участвовали<br/>{seg_cnt["none"]}"] --> C2["Сделки в NewBiz<br/>{len(group_deals["Не участвовали (регистрация без визита)"] - np_deals)}"]')
w(f'  C --> D["Дальше «Исследования»<br/>{sum(stage_cnt[s] for s in NB_PAST_RESEARCH if True) }"]')
w("  C2 --> D")
w(f'  D --> E["Выигрыш тендера<br/>{stage_cnt["Выигрыш тендера"]}"]')
w("```")
w("")

w("### Список сделок")
w("")
w("| Сделка NewBiz | Этап | Ответственный | Создана | Источник | Гость(и) в «Конференции 2026» |")
w("|---|---|---|---|---|---|")
src = defaultdict(list)
for g, rows in groups.items():
    for r in rows:
        for i in ids(r["nb_after_by_contact"]) + (ids(r["nb_after_by_company"]) if "Новый" in g else []):
            if i in all_conv:
                src[i].append((g, r["conf_lead_id"]))
for i in sorted(all_conv, key=lambda x: (NB_ORDER.index(nb[x]["stage"]), nb[x]["name"])):
    d = nb[i]
    gs = sorted({g for g, _ in src[i]})
    cl = ", ".join(link(c) for c in sorted({c for _, c in src[i]}))
    mark = " ⚑" if i in np_deals else ""
    w(f"| [{d['name']}]({AMO}{i}){mark} | {d['stage']} | {d['responsible']} | {d['created_at']} | {'; '.join(gs)} | {cl} |")
w("")
w("⚑ — «Национальные приоритеты»: действующий клиент DARR, сделки в NewBiz были и до конференции; в конверсию не включены.")
w("")

w("## 4. Что требует внимания")
w("")
w(f"1. **{untouched} офлайн-гостей не разобраны** — нет ни причины отказа, ни «Новый лид». "
  "Это остаток в рабочей ссылке; без разметки по ним не посчитать конверсию.")
no_nb_new = [r for r in groups["Офлайн · «Новый лид»"] if not ids(r["nb_after_by_contact"]) and not ids(r["nb_after_by_company"])]
no_nb_al = [r for r in groups["Офлайн · «Действующий лид»"] if not ids(r["nb_after_by_contact"])]
no_prior = [r for r in no_nb_al if not ids(r["nb_before_by_contact"])]
w(f"2. **{len(no_nb_al)} гостей с причиной «Действующий лид» без новой сделки в NewBiz.** "
  f"У {len(no_nb_al) - len(no_prior)} из них сделка в NewBiz была ещё до конференции — это по инструкции. "
  f"У {len(no_prior)} сделки нет ни до, ни после — стоит проверить: "
  + ", ".join(link(r["conf_lead_id"]) for r in no_prior) + ".")
nonpart = [r for r in groups['Не участвовали (регистрация без визита)'] if set(ids(r["nb_after_by_contact"])) - np_deals]
w(f"3. **{len(nonpart)} зарегистрировавшихся, но не пришедших гостей** "
  "дали сделки в NewBiz, но в воронке конференции они никак не помечены — их не видно в отчёте по «Новый лид». "
  "У 3 из 4 компаний сделки в NewBiz были и до конференции. Стоит договориться, отмечать ли таких гостей.")
w("4. Большинство сделок (" + str(stage_cnt["Исследование"]) + ") стоят на «Исследовании» с названием «Скоуп?» — "
  "через месяц имеет смысл повторить срез, чтобы увидеть реальную конверсию в «Таргет» и дальше.")
w("")

w("## Методика")
w("")
w("- Данные выгружены из amoCRM API v4 (сделки обеих воронок с контактами и компаниями + события изменения полей "
  "«Причина отказа», «Новый лид»). Кто проставил поле — по последнему событию изменения.")
w("- `data/conf_aggregate.csv` — все 3 792 сделки воронки конференции, сгруппированные по признакам; "
  "`data/conf_leads_linked.csv` — гости, у которых есть ручная разметка или связанная сделка NewBiz; "
  "`data/newbiz_linked_deals.csv` — сделки NewBiz, связанные с гостями (включая связь только по компании, "
  "она в конверсию не входит — см. приложение).")
w("- Пересобрать отчёт: `python3 scripts/build_report.py`.")
w("")
w("## Приложение. Сделки NewBiz с компаниями-гостями (связь только по компании)")
w("")
w("Эти сделки заведены после конференции на компании, сотрудники которых были в воронке конференции, "
  "но контакт в сделке другой. Прямой связи с отработкой гостей нет, в конверсию не включены.")
w("")
w("| Сделка | Этап | Ответственный | Создана |")
w("|---|---|---|---|")
for i, d in sorted(nb.items(), key=lambda x: (NB_ORDER.index(x[1]["stage"]), x[1]["name"])):
    if d["link"] == "company_only" and i not in all_conv:
        w(f"| [{d['name']}]({AMO}{i}) | {d['stage']} | {d['responsible']} | {d['created_at']} |")
w("")

(ROOT / "REPORT.md").write_text("\n".join(out), encoding="utf-8")
print("ok", len(clean_conv), stage_cnt)
