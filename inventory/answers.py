"""The owner's answers (data/owner_answers.csv, written by the dashboard's "Questions for you" page) feed
the engine on every run.

Principles:
  - Answers are applied in memory on each run; sales.csv, item_map.csv and config/*.csv are never edited,
    so removing or changing an answer undoes its effect on the next run.
  - The latest answer per question wins (by timestamp, then file order); earlier ones are reported as superseded.
  - Hand-written override files (data/raw/case_cbm.csv, config/harvest.csv confirmed rows) win over answers;
    a clash is reported as a conflict.
  - "not sure" never changes anything. Notes are parsed strictly: if a needed code, size or month can't be
    read, nothing changes and the status says so.
  - Every row of the file ends up in outputs/answers_applied.csv with a status: unknown question ids and
    malformed rows are reported, never dropped, and never stop the run.

What each question type does (yes / no; "not sure" = no change):
  hold:PID            yes: hold released from the answer date (PID leaves the current holds, is planned and
                      counted again); no: stays on hold
  gap:PID:YYYY-MM     yes: those months were unavailable (filled, not scored); no: real zero sales, not asked again
  oneoff:PID:YYYY-MM  yes: stays capped in training (as today); no: real demand, not capped
  code:RAW            same-product form: yes merges RAW into its current code at the next --ingest; no keeps it apart.
                      no-current-code form: yes + a code in the note merges into that code at the next --ingest;
                      no = discontinued (left out of the plan and orders)
  new:PID             yes: keep (as today); no: discontinued. A note never sets a forecast.
  stock:on_order      yes: stronger warning (stock at sea is missing); no: nothing on the way, warning removed
  stock:units         yes: confirmed; no: no automatic change, a warning is shown on every order
  units:PID           yes: stock accepted, no longer flagged; no: stock treated as unknown (ordered as if none on
                      hand) unless the note gives the right number of cases
  invcode:RAW         yes + a product code in the note: its stock counts for that product; no: packaging, ignored
  cbm:PID             the note gives the case size ("0.045", "0.045 cbm", "40x30x25 cm", "16x12x10 in")
  container:limits    numbers with units in the note set container CBM and/or the weight limit (kg, lb, t)
  harvest:RULE        yes + "order <months>, arrives <months>" confirms the annual buy; no removes the candidate
"""
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

FIELDS = ["question_id", "answer", "note", "timestamp"]
VALID = {"yes", "no", "not sure"}
REPORT_COLUMNS = ["question_id", "type", "answer", "note", "timestamp", "status", "action"]

# statuses
APPLIED, CONFIRMED, NO_CHANGE = "applied", "confirmed", "no change"
NEEDS_INGEST, NOT_UNDERSTOOD, CONFLICT = "needs ingest", "note not understood", "conflict: override file wins"
NOT_FOUND, UNKNOWN, MALFORMED, SUPERSEDED = "not found", "unknown question", "malformed row", "superseded"

PID_TYPES = {"hold", "new", "units", "cbm", "commodity"}
MONTH_TYPES = {"gap", "oneoff"}
FIXED = {"stock": {"on_order", "units"}, "container": {"limits"}}
YM = re.compile(r"^\d{4}-\d{2}$")
CODE = re.compile(r"[A-Za-z]+\d+[A-Za-z0-9]*")


def parse_question(qid):
    """'gap:P1:2025-03' -> ('gap', 'P1', '2025-03'); None when the id is not a known question."""
    if not isinstance(qid, str) or ":" not in qid:
        return None
    kind, rest = qid.split(":", 1)
    rest = rest.strip()
    if not rest:
        return None
    if kind in PID_TYPES or kind in {"code", "invcode", "harvest"}:
        return kind, rest, None
    if kind in MONTH_TYPES:
        if ":" not in rest:
            return None
        key, month = rest.rsplit(":", 1)
        return (kind, key, month) if key and YM.match(month) else None
    if kind in FIXED:
        return (kind, rest, None) if rest in FIXED[kind] else None
    return None


@dataclass
class Answers:
    """Latest valid answer per question (`rows`) plus the log of what happened to every row of the file."""
    rows: pd.DataFrame
    log: dict = field(default_factory=dict)          # question_id -> report row (latest answers)
    problems: list = field(default_factory=list)     # malformed / unknown / superseded rows

    def of(self, kind):
        return self.rows[self.rows["type"] == kind]

    def get(self, kind, key):
        r = self.rows[(self.rows["type"] == kind) & (self.rows["key"] == key)]
        return r.iloc[-1] if len(r) else None

    def record(self, row, status, action):
        self.log[row["question_id"]] = dict(question_id=row["question_id"], type=row["type"], answer=row["answer"],
                                            note=row["note"], timestamp=row["timestamp"], status=status, action=action)

    def skip(self, kinds, reason):
        """Record 'no change' for answers of these types that this run had no use for."""
        for _, r in self.rows[self.rows["type"].isin(kinds)].iterrows():
            if r["question_id"] not in self.log:
                self.record(r, NO_CHANGE, reason)

    def report(self):
        rows = list(self.problems)
        for _, r in self.rows.iterrows():
            if r["question_id"] in self.log:
                rows.append(self.log[r["question_id"]])
            elif r["answer"] == "not sure":
                rows.append(dict(r[["question_id", "type", "answer", "note", "timestamp"]], status=NO_CHANGE,
                                 action="not sure: nothing changes; the question stays open"))
            else:
                rows.append(dict(r[["question_id", "type", "answer", "note", "timestamp"]], status=NO_CHANGE,
                                 action="not used in this run"))
        return pd.DataFrame(rows, columns=REPORT_COLUMNS)


def empty():
    return Answers(pd.DataFrame(columns=["question_id", "type", "key", "month", "answer", "note", "timestamp", "order"]))


def load(data_dir="data"):
    """Read data/owner_answers.csv. Never raises on bad content: bad rows become report rows."""
    path = Path(data_dir) / "owner_answers.csv"
    if not path.exists():
        return empty()
    problems, good = [], []

    def bad(raw, status, why):
        raw = list(raw) + [""] * (4 - len(raw))
        problems.append(dict(question_id=raw[0], type="", answer=raw[1], note=raw[2], timestamp=raw[3], status=status, action=why))

    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            lines = list(csv.reader(fh))
    except (OSError, csv.Error) as e:
        problems.append(dict(question_id="", type="", answer="", note="", timestamp="", status=MALFORMED,
                             action=f"could not read {path.name}: {e}"))
        return Answers(empty().rows, problems=problems)
    if not lines:
        return empty()
    body = lines
    if [c.strip().lower() for c in lines[0]] == FIELDS:
        body = lines[1:]
    else:
        problems.append(dict(question_id="", type="", answer="", note="", timestamp="", status=MALFORMED,
                             action=f"unexpected header {lines[0]!r}; rows read as {FIELDS}"))
    for i, raw in enumerate(body):
        if not any(str(c).strip() for c in raw):
            continue
        if len(raw) != 4:
            bad(raw, MALFORMED, f"row {i + 1}: {len(raw)} fields instead of 4")
            continue
        qid, ans, note, ts = (str(c).strip() for c in raw)
        ans = ans.lower()
        if not qid:
            bad(raw, MALFORMED, f"row {i + 1}: no question_id")
            continue
        if ans not in VALID:
            bad(raw, MALFORMED, f"row {i + 1}: answer {ans!r} is not yes / no / not sure")
            continue
        try:
            t = pd.Timestamp(ts)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        except (ValueError, TypeError):
            bad(raw, MALFORMED, f"row {i + 1}: timestamp {ts!r} is not a date")
            continue
        q = parse_question(qid)
        if q is None:
            bad(raw, UNKNOWN, f"row {i + 1}: question id {qid!r} is not a question the engine knows")
            continue
        good.append(dict(question_id=qid, type=q[0], key=q[1], month=q[2], answer=ans, note=note, timestamp=t, order=i))
    rows = pd.DataFrame(good, columns=["question_id", "type", "key", "month", "answer", "note", "timestamp", "order"])
    rows = rows.sort_values(["timestamp", "order"], kind="stable")
    older = rows[rows.duplicated("question_id", keep="last")]
    for _, r in older.iterrows():
        problems.append(dict(question_id=r["question_id"], type=r["type"], answer=r["answer"], note=r["note"],
                             timestamp=r["timestamp"], status=SUPERSEDED, action="a later answer to the same question wins"))
    rows = rows.drop_duplicates("question_id", keep="last").reset_index(drop=True)
    return Answers(rows, problems=problems)


def ensure(answers, data_dir):
    return answers if answers is not None else load(data_dir)


# ------------------------------------------------------------------ holds and gaps
def _hold_runs(panel):
    """{product_id: first month of the hold run that reaches the last data month}."""
    last = panel["month"].max()
    held = panel[(panel["month"] == last) & (panel["status"] == "fda_hold")]["product_id"]
    runs = {}
    p = panel[panel["product_id"].isin(set(held))].sort_values(["product_id", "month"])
    for pid, g in p.groupby("product_id"):
        start = last
        for m, st in zip(reversed(list(g["month"])), reversed(list(g["status"]))):
            if st != "fda_hold":
                break
            start = m
        runs[pid] = start
    return runs


def effective_holds(panel, answers):
    """Holds now after the owner's hold answers. Returns (holds {product_id: 'YYYY-MM' since}, released set).
    A release counts when it was given on or after the month the current hold run started."""
    runs = _hold_runs(panel)
    released = set()
    for _, r in answers.of("hold").iterrows():
        pid = r["key"]
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: stays on hold; the question stays open")
        elif pid not in runs:
            answers.record(r, NOT_FOUND, "not on hold in the latest data month")
        elif r["answer"] == "no":
            answers.record(r, CONFIRMED, f"stays on hold (since {runs[pid]:%Y-%m})")
        elif r["timestamp"].tz_convert(None).to_period("M").to_timestamp() < runs[pid]:
            answers.record(r, NO_CHANGE, f"answered before the current hold started ({runs[pid]:%Y-%m}): ask again")
        else:
            released.add(pid)
            answers.record(r, APPLIED, f"hold released (on hold since {runs[pid]:%Y-%m}): planned and ordered again")
    holds = {pid: f"{m:%Y-%m}" for pid, m in runs.items() if pid not in released}
    return holds, released


def apply_gaps(panel, suspected, answers):
    """yes: the suspected gap's months become unavailable (fda_hold) in `panel`; no: kept as real zero sales.
    Both remove the gap from `suspected`. Returns (panel, suspected)."""
    panel, suspected = panel.copy(), suspected.copy()
    starts = pd.to_datetime(suspected["start"]) if len(suspected) else pd.Series(dtype="datetime64[ns]")
    for _, r in answers.of("gap").iterrows():
        m = pd.Timestamp(r["month"] + "-01")
        hit = suspected[(suspected["product_id"] == r["key"]) & (starts == m)] if len(suspected) else suspected
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: left as a suspected gap")
            continue
        if hit.empty:
            answers.record(r, NOT_FOUND, "no suspected gap starts that month any more")
            continue
        g = hit.iloc[0]
        months = (panel["product_id"] == r["key"]) & panel["month"].between(pd.Timestamp(g["start"]), pd.Timestamp(g["end"]))
        if r["answer"] == "yes":
            panel.loc[months, "status"] = "fda_hold"
            answers.record(r, APPLIED, f"{int(months.sum())} month(s) marked unavailable: filled, not scored")
        else:
            answers.record(r, APPLIED, "kept as real zero sales; not asked again")
        suspected = suspected.drop(hit.index)
        starts = starts.drop(hit.index)
    return panel, suspected.reset_index(drop=True)


def real_one_offs(spikes, answers):
    """(product_id, month) one-off spikes the owner says were real demand: not capped in training."""
    real = set()
    one = spikes[spikes["cause"] == "one_off"] if len(spikes) else spikes
    have = {(p, f"{m:%Y-%m}") for p, m in zip(one["product_id"], pd.to_datetime(one["month"]))} if len(one) else set()
    for _, r in answers.of("oneoff").iterrows():
        k = (r["key"], r["month"])
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: stays capped")
        elif k not in have:
            answers.record(r, NOT_FOUND, "no one-off spike that month any more")
        elif r["answer"] == "yes":
            answers.record(r, CONFIRMED, "one-off: stays capped in training")
        else:
            real.add(k)
            answers.record(r, APPLIED, "real demand: not capped in training")
    return real


# ------------------------------------------------------------------ products and codes
def _base(raw):
    from .ingest import OLD_MARK, TBD_MARK
    return re.sub(TBD_MARK, "", re.sub(OLD_MARK, "", raw)).strip(" -")


def code_merges(answers, raw_codes, current=None):
    """{raw code: target code} merges the owner confirmed. raw_codes: every raw item code known (item_map or
    the export). current: optional {raw: product_id} from the existing item_map, to tell applied from pending."""
    codes = {c.upper(): c for c in raw_codes}
    out = {}
    for _, r in answers.of("code").iterrows():
        raw = codes.get(r["key"].upper())
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: kept separate")
            continue
        if raw is None:
            answers.record(r, NOT_FOUND, "item code not in the sales data")
            continue
        base = codes.get(_base(raw).upper())
        same_form = base is not None and base != raw
        if r["answer"] == "no":
            if same_form:
                answers.record(r, CONFIRMED, f"kept separate from {base}")
            continue                                   # no-current-code form: discontinued, see discontinued()
        if same_form:
            target = base
        else:
            found = [codes[t.upper()] for t in CODE.findall(r["note"]) if t.upper() in codes and codes[t.upper()] != raw]
            if not found:
                answers.record(r, NOT_UNDERSTOOD, "no known item code in the note")
                continue
            target = found[0]
        out[raw] = target
        done = current is not None and current.get(raw) == target
        answers.record(r, APPLIED if done else NEEDS_INGEST,
                       f"merged into {target}" if done else f"merges into {target} at the next run with --ingest")
    return out


def discontinued(answers, item_map=None):
    """Products the owner says are no longer sold (new:PID no; code:RAW no for a code with no current code)."""
    out = set()
    for _, r in answers.of("new").iterrows():
        if r["answer"] == "no":
            out.add(r["key"])
            answers.record(r, APPLIED, "discontinued: left out of the plan and brand orders")
        elif r["answer"] == "yes":
            answers.record(r, CONFIRMED, "kept; its forecast is unchanged")
        else:
            answers.record(r, NO_CHANGE, "not sure: kept; the question stays open")
    if item_map is not None and len(item_map):
        im = item_map.assign(key=item_map["raw_item"].astype(str).str.upper())
        codes = set(im["key"])
        for _, r in answers.of("code").iterrows():
            if r["answer"] != "no":
                continue
            row = im[im["key"] == r["key"].upper()]
            if row.empty:
                answers.record(r, NOT_FOUND, "item code not in the sales data")
                continue
            if _base(row["raw_item"].iloc[0]).upper() in codes - {r["key"].upper()}:
                continue                               # same-product form: handled (confirmed) in code_merges()
            pid = row["product_id"].iloc[0]
            out.add(pid)
            answers.record(r, APPLIED, f"discontinued: {pid} left out of the plan and brand orders")
    return out


# ------------------------------------------------------------------ stock
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")


def _number(text):
    m = NUM.search(str(text))
    return float(m.group(0).replace(",", "")) if m else None


def apply_stock_counts(stock, answers):
    """units:PID no -> on_hand_cases = the number in the note, else 0 (stock unknown: ordered as if none on
    hand). Returns (stock, {product_id: line note})."""
    s, notes = stock.copy(), {}
    col = "on_hand_cases" if "on_hand_cases" in s else "on_hand_units"
    for _, r in answers.of("units").iterrows():
        pid = r["key"]
        if r["answer"] != "no":
            continue                                   # yes / not sure: recorded by cover_flags()
        if pid not in set(s["product_id"]):
            answers.record(r, NOT_FOUND, "no stock row for this product")
            continue
        n = _number(r["note"])
        if n is not None and n >= 0:
            s.loc[s["product_id"] == pid, col] = n
            notes[pid] = f"stock corrected by owner to {n:,.0f} cases"
            answers.record(r, APPLIED, f"on hand set to {n:,.0f} cases")
        else:
            s.loc[s["product_id"] == pid, col] = 0.0
            notes[pid] = "stock count disputed by owner: ordered as if none on hand"
            answers.record(r, APPLIED, "stock treated as unknown: ordered as if none on hand")
    return s, notes


def cover_flags(cover, answers):
    """units:PID yes -> no longer flagged in stock_cover_check.csv."""
    c = cover.copy()
    for _, r in answers.of("units").iterrows():
        if r["answer"] == "yes":
            hit = c["product_id"] == r["key"]
            if hit.any():
                c.loc[hit, "flag"] = "confirmed by owner"
                answers.record(r, CONFIRMED, "stock accepted; no longer flagged")
            else:
                answers.record(r, NOT_FOUND, "no stock row for this product")
        elif r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: still flagged")
    return c


def inventory_codes(answers, product_ids):
    """invcode answers -> ({RAW_UPPER: product_id}, {RAW_UPPER ignored as packaging})."""
    pids = {p.upper(): p for p in product_ids}
    mapping, ignore = {}, set()
    for _, r in answers.of("invcode").iterrows():
        key = r["key"].upper()
        if r["answer"] == "no":
            ignore.add(key)
            answers.record(r, APPLIED, "packaging/material: ignored")
        elif r["answer"] == "yes":
            found = [pids[t.upper()] for t in CODE.findall(r["note"]) if t.upper() in pids]
            if found:
                mapping[key] = found[0]
                answers.record(r, APPLIED, f"its stock counts for {found[0]}")
            else:
                answers.record(r, NOT_UNDERSTOOD, "no known product code in the note")
        else:
            answers.record(r, NO_CHANGE, "not sure: left out of the plan")
    return mapping, ignore


ON_ORDER_YES = ("owner says stock is already ordered or at sea, but the inventory file has no on-order column: "
                "these orders are too large until an export with quantity on order arrives")
UNITS_NO = "owner says the inventory units are wrong: every quantity may be off; check the stock file before ordering"


def stock_warnings(answers):
    """(on_order_answer 'yes'/'no'/None, extra warning text) from stock:on_order and stock:units."""
    on_order, extra = None, ""
    r = answers.get("stock", "on_order")
    if r is not None:
        if r["answer"] == "no":
            on_order = "no"
            answers.record(r, APPLIED, "nothing on the way: the missing on-order warning is removed")
        elif r["answer"] == "yes":
            on_order = "yes"
            answers.record(r, APPLIED, "stronger warning on every order")
        else:
            answers.record(r, NO_CHANGE, "not sure: warning stays")
    r = answers.get("stock", "units")
    if r is not None:
        if r["answer"] == "no":
            extra = UNITS_NO
            answers.record(r, APPLIED, "no automatic change; warning shown on every order until confirmed by hand")
        elif r["answer"] == "yes":
            answers.record(r, CONFIRMED, "inventory units confirmed (single packs, cases for the listed prefixes)")
        else:
            answers.record(r, NO_CHANGE, "not sure: nothing changes")
    return on_order, extra


# ------------------------------------------------------------------ case sizes, containers, harvest
_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "inch": 0.0254, "inches": 0.0254, '"': 0.0254}
_DIMS = re.compile(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inches|inch|in|\"|m)(?![a-z])",
                   re.IGNORECASE)
_CBM = re.compile(r"(\d*\.?\d+)\s*(?:cbm|m3|m³|cubic\s*met(?:er|re)s?)", re.IGNORECASE)


def parse_cbm(note):
    """Case volume in CBM from a note, or None."""
    t = str(note).strip()
    m = _DIMS.search(t)
    if m:
        f = _TO_M[m.group(4).lower()]
        v = float(m.group(1)) * float(m.group(2)) * float(m.group(3)) * f ** 3
    elif _CBM.search(t):
        v = float(_CBM.search(t).group(1))
    elif re.fullmatch(r"\d*\.?\d+", t):
        v = float(t)
    else:
        return None
    return round(v, 5) if 0 < v < 5 else None


def cbm_overrides(answers, case_cbm_path=None):
    """{product_id: cbm_per_case} from cbm answers. data/raw/case_cbm.csv wins (reported as a conflict)."""
    file_ids = set()
    if case_cbm_path and Path(case_cbm_path).exists():
        f = pd.read_csv(case_cbm_path, dtype={"product_id": str})
        file_ids = set(f.dropna(subset=["cbm_per_case"])["product_id"]) if "cbm_per_case" in f else set()
    out = {}
    for _, r in answers.of("cbm").iterrows():
        v = parse_cbm(r["note"])
        if r["answer"] == "not sure" and v is None:
            answers.record(r, NO_CHANGE, "not sure: the weight estimate stays")
        elif v is None:
            answers.record(r, NOT_UNDERSTOOD, "no case size in the note (e.g. 0.045 cbm or 40x30x25 cm)")
        elif r["key"] in file_ids:
            answers.record(r, CONFLICT, f"note says {v} CBM; data/raw/case_cbm.csv has its own value and wins")
        else:
            out[r["key"]] = v
            answers.record(r, APPLIED, f"{v} CBM per case (source: owner answer)")
    return out


_QTY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kgs?|kilograms?|lbs?|pounds?|tonnes?|tons?|t|cbm|m3|m³)(?![a-z])", re.IGNORECASE)
_KG = {"kg": 1.0, "kgs": 1.0, "kilogram": 1.0, "kilograms": 1.0, "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237,
       "pounds": 0.45359237, "t": 1000.0, "ton": 1000.0, "tons": 1000.0, "tonne": 1000.0, "tonnes": 1000.0}


def container_config(cfg, answers):
    """container:limits -> cfg with container_cbm and/or max_kg from the note (numbers need units)."""
    cfg = dict(cfg)
    r = answers.get("container", "limits")
    if r is None:
        return cfg
    if r["answer"] == "not sure":
        answers.record(r, NO_CHANGE, "not sure: container settings unchanged")
        return cfg
    found = {}
    for num, unit in _QTY.findall(r["note"]):
        v, u = float(num.replace(",", "")), unit.lower()
        if u in ("cbm", "m3", "m³"):
            found["cbm"] = v
        else:
            found["max_kg"] = round(v * _KG[u], 1)
    if not found:
        if r["answer"] == "yes" and not r["note"].strip():
            answers.record(r, CONFIRMED, f"{cfg['cbm']:g} CBM confirmed; no weight limit given")
        else:
            answers.record(r, NOT_UNDERSTOOD, "no number with a unit (cbm, kg, lb, t) in the note")
        return cfg
    cfg.update(found)
    answers.record(r, APPLIED, ", ".join(f"{'container CBM' if k == 'cbm' else 'weight limit kg'} = {v:g}" for k, v in found.items()))
    return cfg


_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_SPEC = _MON + r"(?:\s*(?:-|–|to|through|until)\s*" + _MON + r")?"
_ORDER = re.compile(r"\border\w*\W+(?:in\W+|during\W+)?" + _SPEC, re.IGNORECASE)
_ARRIVE = re.compile(r"\barriv\w*\W+(?:in\W+|by\W+|around\W+)?" + _SPEC, re.IGNORECASE)


def _months(m):
    a = m.group(1)[:3].title()
    return a if not m.group(2) else f"{a}-{m.group(2)[:3].title()}"


def parse_harvest(note):
    """'order Jan, arrives Apr-May' -> ('Jan', 'Apr-May'); None unless both are given."""
    o, a = _ORDER.search(str(note)), _ARRIVE.search(str(note))
    return (_months(o), _months(a)) if o and a else None


_HARVEST = re.compile(r"\bharvest\w*\W+(?:is\W+|in\W+|from\W+|during\W+)*" + _SPEC, re.IGNORECASE)


def parse_harvest_months(note):
    """'harvest Oct-Dec' -> ('Oct', 'Dec'); 'harvest in March' -> ('Mar', 'Mar'); None if not stated."""
    m = _HARVEST.search(str(note))
    if not m:
        return None
    a = m.group(1)[:3].title()
    return a, (m.group(2)[:3].title() if m.group(2) else a)


def commodity_overrides(answers, cmap):
    """commodity:PID answers -> {product_id: commodity, or None = not a harvest crop}. yes + the crop in the note
    (a commodity id such as 'toor' or any of its keywords, e.g. 'pigeon pea'), matched against
    config/commodity_map.csv in file order; no = not a harvest crop (stays on the regular rule)."""
    out = {}
    for _, r in answers.of("commodity").iterrows():
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: stays on the regular rule; the question stays open")
        elif r["answer"] == "no":
            out[r["key"]] = None
            answers.record(r, APPLIED, "not a harvest crop: ordered with the regular rule")
        else:
            note = str(r["note"])
            hit = next((c for c, rx in zip(cmap["commodity"], cmap["rx"])
                        if re.search(rf"\b{re.escape(c.replace('_', ' '))}\b|\b{re.escape(c)}\b", note, re.IGNORECASE) or rx.search(note)), None)
            if hit is None:
                answers.record(r, NOT_UNDERSTOOD, "no known crop in the note (e.g. toor, moong, basmati, almond)")
            else:
                out[r["key"]] = hit
                answers.record(r, APPLIED, f"mapped to {hit}")
    return out


def harvest_calendar(calendar, answers):
    """harvest:<rule_id> answers for config/harvest_calendar.csv rows (rule_id = commodity_country).
    yes: confirmed with the row's months, or the months in the note ("harvest Oct-Dec"), which win;
    no: rejected (not bought by harvest from that country); not sure: no change.
    Returns the calendar with `confirmed` (yes / no / rejected) and `month_source` columns."""
    cal = calendar.copy()
    cal["confirmed"] = cal["confirmed_by_owner"].astype(str).str.strip().str.lower().map({"yes": "yes"}).fillna("no")
    cal["month_source"] = "calendar"
    for _, r in answers.of("harvest").iterrows():
        hit = cal["rule_id"] == r["key"]
        if not hit.any():
            continue                                   # a config/harvest.csv rule: harvest_rules() records it
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: still unconfirmed; the regular rule applies")
        elif r["answer"] == "no":
            cal.loc[hit, "confirmed"] = "rejected"
            answers.record(r, APPLIED, "not bought by harvest from this country: the regular rule applies")
        else:
            months = parse_harvest_months(r["note"])
            if months:
                cal.loc[hit, ["harvest_start_month", "harvest_end_month", "month_source"]] = [months[0], months[1], "owner answer"]
            if not str(cal.loc[hit, "harvest_start_month"].iloc[0]).strip():
                answers.record(r, NOT_UNDERSTOOD, "no harvest months known: add them in the note, e.g. 'harvest Oct-Dec'")
                continue
            cal.loc[hit, "confirmed"] = "yes"
            s, e = cal.loc[hit, ["harvest_start_month", "harvest_end_month"]].iloc[0]
            answers.record(r, APPLIED, f"harvest buy confirmed: harvest {s}-{e or s}" + (" (months from the note)" if months else ""))
    return cal


def harvest_rules(rules, answers, skip=()):
    """harvest:RULE answers -> rules with confirmed months, or the candidate removed. Rules already confirmed
    in config/harvest.csv win. skip: rule ids handled elsewhere (harvest calendar rows)."""
    rules = rules.copy()
    for _, r in answers.of("harvest").iterrows():
        if r["key"] in set(skip):
            continue
        hit = rules["rule_id"] == r["key"]
        if not hit.any():
            answers.record(r, NOT_FOUND, "no such rule in config/harvest.csv")
            continue
        already = str(rules.loc[hit, "confirmed"].iloc[0]).strip().lower() == "yes"
        if r["answer"] == "not sure":
            answers.record(r, NO_CHANGE, "not sure: still a candidate")
        elif already:
            answers.record(r, CONFIRMED if r["answer"] == "yes" else CONFLICT,
                           "config/harvest.csv already confirms this rule" + ("" if r["answer"] == "yes" else " (config wins)"))
        elif r["answer"] == "no":
            rules = rules[~hit]
            answers.record(r, APPLIED, "not an annual buy: its products follow the regular rule")
        else:
            months = parse_harvest(r["note"])
            if months is None:
                answers.record(r, NOT_UNDERSTOOD, "the note needs both, e.g. 'order Jan, arrives Apr-May'")
                continue
            rules.loc[hit, ["confirmed", "order_months", "arrival_months"]] = ["yes", months[0], months[1]]
            answers.record(r, APPLIED, f"annual buy: order {months[0]}, arrives {months[1]}")
    return rules


def summary(report):
    """One line for the run report."""
    if report.empty:
        return "no owner answers yet (data/owner_answers.csv)"
    c = report["status"].value_counts()
    return f"{len(report)} answer rows: " + ", ".join(f"{k} {v}" for k, v in c.items()) + " -> outputs/answers_applied.csv"
