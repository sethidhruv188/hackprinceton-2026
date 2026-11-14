"""Pick the forecaster that feeds the ordering rule, by data.

Candidates (every forecast for month M uses data through M-5 only):
  avg          average of the last 6 months (the business today)
  ml           current ML model (Tweedie mean on all demand)
  ml_regular   ML trained on regular demand (large orders removed) + the product's typical
               large-order cases per month (inventory/large_orders.py)
  ml_simple    simpler ML (num_leaves 7, min_child_samples 50, L2 1.0) on the better of ml / ml_regular
  seasonal     same month last year x (last 6 months / same 6 months a year earlier), ratio capped 0.5-2
  blend_w      w x best ML + (1 - w) x avg, w in 0.25, 0.5, 0.75 (w = 0 is avg, w = 1 is best ML)
  blend3       (best ML + avg + seasonal) / 3

Fair test: the carry-over simulation (inventory/simulate.py) at cushions 1-4, cover 2 (each path is
extended one month flat, for every candidate alike, so cushion 4 fits), lead time
105-130 days (seeded), same starting stock. Each candidate's fill rate is read at EQUAL STOCK: the
average stock averaging needs at cushion 3 in that period (linear between the candidate's cushion
points), and as points versus averaging at the same stock (gap_vs_avg, averaged over the candidate's
cushion points inside averaging's stock range; this is the selection score). The winner is chosen on
Apr-Jul 2026 and confirmed on Aug-Oct 2026; if it does not beat averaging there, averaging is used.

  python -m inventory.tournament            # writes outputs/forecaster_tournament.{json,csv}, config/forecaster.txt
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import checks, large_orders as LO, policy, simulate, spikes
from .forecast import SIMPLE_PARAMS, average_baseline, forecast, month_kind, train
from .gaps import clean_demand
from .schema import load

ROOT = Path(__file__).resolve().parent.parent
CUSHIONS = (1.0, 2.0, 3.0, 4.0)
WEIGHTS = (0.25, 0.5, 0.75)
CHOICE_FILE = ROOT / "config" / "forecaster.txt"


def ml_paths(panel, products, decisions, n_ahead, lead_gap=4, power=1.5, item_map=None, params=None,
             lines=None, log=print, tag="ml"):
    """Forecast paths like simulate.forecast_paths; with `lines`, train on regular demand and add the
    typical large-order volume back."""
    out = []
    for D in decisions:
        D = pd.Timestamp(D)
        cutoff = D - pd.DateOffset(months=lead_gap)
        if lines is not None:
            hist, typ = LO.regular_panel(panel, lines, cutoff)
        else:
            hist, typ = panel[panel["month"] < cutoff], pd.Series(dtype=float)
        clean = clean_demand(hist)
        sp = spikes.detect(hist, clean, item_map)
        clean["spike"] = spikes.spike_flags(clean, sp)
        clean["target_cap"] = spikes.one_off_caps(clean, sp)
        n_hist = clean["month"].nunique()
        got = []
        for h in range(lead_gap + 1, lead_gap + 1 + n_ahead):
            target = cutoff - pd.DateOffset(months=1) + pd.DateOffset(months=h)
            if n_hist - h >= simulate.MIN_TARGET_MONTHS:
                m = train(clean, products, powers=[power], quantiles=(), h=h, params=params)
                f = forecast(clean, products, m, h, driver=f"mean_{power}").set_index("product_id")["mean"]
                got.append(f)
            else:
                f = pd.concat(got[-3:], axis=1).mean(axis=1)
            vals = f + f.index.map(typ).fillna(0.0).to_numpy() if len(typ) else f
            out.append(pd.DataFrame({"decision": D, "product_id": f.index, "month": target, tag: vals.to_numpy()}))
        log(f"  {tag}: decision {D:%Y-%m} done")
    return pd.concat(out, ignore_index=True)


def seasonal_paths(panel, decisions, n_ahead, lead_gap=4, cap=(0.5, 2.0)):
    """Same month last year (the most recent year already known) x the recent-vs-year-earlier ratio."""
    out = []
    for D in decisions:
        D = pd.Timestamp(D)
        cutoff = D - pd.DateOffset(months=lead_gap)
        clean = clean_demand(panel[panel["month"] < cutoff])
        W = clean.pivot_table(index="product_id", columns="month", values="demand", aggfunc="sum")
        last = cutoff - pd.DateOffset(months=1)
        rec = [last - pd.DateOffset(months=i) for i in range(6)]
        prev = [m - pd.DateOffset(months=12) for m in rec]
        R, P = W.reindex(columns=rec).to_numpy(), W.reindex(columns=prev).to_numpy()
        both = ~np.isnan(R) & ~np.isnan(P)                     # compare only month pairs known in both years
        num = pd.Series(np.where(both, R, 0).sum(axis=1), index=W.index).where(both.any(axis=1))
        den = pd.Series(np.where(both, P, 0).sum(axis=1), index=W.index).where(both.any(axis=1))
        ratio = (num / den.where(den > 0)).clip(*cap).fillna(1.0)
        avg = average_baseline(panel, cutoff)
        for k in range(n_ahead):
            T = D + pd.DateOffset(months=k)
            src = T - pd.DateOffset(months=12)
            while src > last:
                src -= pd.DateOffset(months=12)
            base = W[src] if src in W.columns else pd.Series(np.nan, index=W.index)
            v = (base * ratio).where(base.notna(), W.index.map(avg).to_series(index=W.index))
            out.append(pd.DataFrame({"decision": D, "product_id": W.index, "month": T, "seasonal": v.fillna(0.0).to_numpy()}))
    return pd.concat(out, ignore_index=True)


def extend_flat(paths, months=1):
    """Repeat each decision's last forecast month `months` more times (all candidates alike), so
    cushion 4 + cover 2 (312 days) fits inside every path."""
    last = paths.groupby("decision")["month"].transform("max")
    tail = paths[paths["month"] == last]
    extra = [tail.assign(month=tail["month"] + pd.DateOffset(months=k)) for k in range(1, months + 1)]
    return pd.concat([paths, *extra], ignore_index=True)


def cached(out, tag, build, log=print):
    """Build expensive paths once; later runs on the same decisions reuse the checkpoint."""
    f = Path(out) / f"tournament_cache_{tag}.csv"
    if f.exists():
        log(f"  reused checkpoint {f.name}")
        return pd.read_csv(f, dtype={"product_id": str}, parse_dates=["decision", "month"])
    p = build()
    p.to_csv(f, index=False)
    return p


def run_sims(paths, panel, eligible, sim_months, methods, cover=2.0, seed=7, log=print):
    sims = {}
    for c in CUSHIONS:
        sims[c] = simulate.simulate(paths, panel, eligible, sim_months, c, cover, seed, methods=methods)
        log(f"  simulated cushion {c:g} for {len(methods)} candidates")
    return sims


def period_table(sims, months, reg=None, kind=None):
    """Per candidate and cushion: fill rate and average stock over `months` (optionally a segment)."""
    rows = []
    for c, s in sims.items():
        g = s[s["month"].isin(months)]
        if reg is not None:
            g = g[g["product_id"].isin(reg)]
        for m, x in g.groupby("method"):
            sc = x[x["scored"]]
            rows.append(dict(method=m, cushion=c, fill=100 * sc["shipped"].sum() / sc["demand"].sum() if sc["demand"].sum() else np.nan,
                             stock=x["end_stock"].sum() / max(x["month"].nunique(), 1)))
    return pd.DataFrame(rows)


def gap_vs_avg(t, ref_method="avg"):
    """Percentage points of fill rate versus averaging AT THE SAME STOCK: for each cushion point of a
    candidate whose stock lies inside averaging's cushion 1-4 range, its fill minus averaging's fill
    interpolated at that stock; averaged over those points (NaN when no point overlaps)."""
    ref = t[t["method"] == ref_method].sort_values("stock")
    lo, hi = ref["stock"].min(), ref["stock"].max()
    out = {}
    for m, g in t.groupby("method"):
        g = g[(g["stock"] >= lo) & (g["stock"] <= hi)]
        out[m] = float(np.mean(g["fill"] - np.interp(g["stock"], ref["stock"], ref["fill"]))) if len(g) else np.nan
    return pd.Series(out)


def at_equal_stock(t, ref_method="avg", ref_cushion=3.0):
    """Fill rate of each candidate at the stock averaging holds at cushion 3 (NaN outside its range)."""
    S = float(t[(t["method"] == ref_method) & (t["cushion"] == ref_cushion)]["stock"].iloc[0])
    out = {}
    for m, g in t.groupby("method"):
        g = g.sort_values("stock")
        out[m] = float(np.interp(S, g["stock"], g["fill"])) if g["stock"].min() <= S <= g["stock"].max() else np.nan
    return pd.Series(out), S


def run(data="data", out="outputs", lead_gap=4, log=print):
    t0 = time.time()
    data, out = Path(data), Path(out)
    panel, products, _ = load(data)
    item_map = pd.read_csv(data / "item_map.csv") if (data / "item_map.csv").exists() else None
    lines = LO.load_lines(data / "order_lines.csv")
    saved = pd.read_csv(out / "sim_forecast_paths.csv", dtype={"product_id": str}, parse_dates=["decision", "month"])
    decisions = sorted(saved["decision"].unique())
    n_ahead = saved.groupby("decision")["month"].nunique().max()
    paths = saved.rename(columns={"model": "ml"})[["decision", "product_id", "month", "ml", "avg"]]
    log(f"reused saved paths for avg and ml ({len(decisions)} decisions, {n_ahead} months each)")

    # eligible products and scoring windows, as in run.py
    first_cut = pd.Timestamp(decisions[0]) - pd.DateOffset(months=lead_gap)
    win = panel[(panel["month"] < first_cut) & (panel["month"] >= first_cut - pd.DateOffset(months=6))]
    eligible = sorted(set(win.loc[win["status"] == "sellable", "product_id"]) & set(paths["product_id"]))
    sim_months = list(pd.DatetimeIndex(decisions))
    sel = [m for m in sim_months if pd.Timestamp("2026-04-01") <= m <= pd.Timestamp("2026-07-01")]
    conf = [m for m in sim_months if pd.Timestamp("2026-08-01") <= m <= pd.Timestamp("2026-10-01")]

    log("building ml_regular paths (large orders removed, typical large volume added back)...")
    reg = cached(out, "ml_regular", lambda: ml_paths(panel, products, decisions, n_ahead, lead_gap, item_map=item_map,
                                                     lines=lines, log=log, tag="ml_regular"), log)
    paths = paths.merge(reg, on=["decision", "product_id", "month"], how="left")
    sims = run_sims(extend_flat(paths), panel, eligible, sim_months, ["avg", "ml", "ml_regular"], log=log)
    g_sel = gap_vs_avg(period_table(sims, sel)).fillna(-np.inf)
    best_ml = "ml_regular" if g_sel["ml_regular"] >= g_sel["ml"] else "ml"
    log(f"Apr-Jul, points vs averaging at equal stock: ml {g_sel['ml']:+.2f}, ml_regular {g_sel['ml_regular']:+.2f} -> "
        f"simpler ML is trained like {best_ml}")

    log("building ml_simple paths...")
    simple = cached(out, f"ml_simple_{best_ml}", lambda: ml_paths(panel, products, decisions, n_ahead, lead_gap, item_map=item_map,
                    params=SIMPLE_PARAMS, lines=lines if best_ml == "ml_regular" else None, log=log, tag="ml_simple"), log)
    paths = paths.merge(simple, on=["decision", "product_id", "month"], how="left")
    paths = paths.merge(seasonal_paths(panel, decisions, n_ahead, lead_gap), on=["decision", "product_id", "month"], how="left")
    sims_ml = run_sims(extend_flat(paths), panel, eligible, sim_months, ["avg", "ml_simple"], log=log)
    g2 = gap_vs_avg(period_table(sims_ml, sel)).fillna(-np.inf)
    best = max([(g_sel[best_ml], best_ml), (g2["ml_simple"], "ml_simple")])[1]
    log(f"best ML on Apr-Jul at equal stock: {best} (ml_simple {g2['ml_simple']:+.2f} points vs averaging)")
    for w in WEIGHTS:
        paths[f"blend_{w:g}"] = w * paths[best] + (1 - w) * paths["avg"]
    paths["blend3"] = (paths[best] + paths["avg"] + paths["seasonal"].fillna(paths["avg"])) / 3
    cands = ["avg", "ml", "ml_regular", "ml_simple", "seasonal"] + [f"blend_{w:g}" for w in WEIGHTS] + ["blend3"]
    paths[cands] = paths[cands].fillna(0.0).clip(lower=0)
    paths.to_csv(out / "tournament_paths.csv", index=False)
    log("simulating every candidate at cushions 1-4...")
    sims = run_sims(extend_flat(paths), panel, eligible, sim_months, cands, log=log)

    regs = checks.regularity_groups(panel).set_index("product_id")["regularity"]
    pre = [m for m in sim_months if month_kind(m) == "pre-Diwali month" and m in conf]
    t_sel, t_conf = period_table(sims, sel), period_table(sims, conf)
    e_sel, S_sel = at_equal_stock(t_sel)
    e_conf, S_conf = at_equal_stock(t_conf)
    table = pd.DataFrame({"points_vs_avg_apr_jul": gap_vs_avg(t_sel), "points_vs_avg_aug_oct": gap_vs_avg(t_conf),
                          "fill_equal_stock_apr_jul": e_sel, "fill_equal_stock_aug_oct": e_conf})
    c3 = t_conf[t_conf["cushion"] == 3.0].set_index("method")
    table["fill_cushion3_aug_oct"] = c3["fill"]
    table["avg_stock_cushion3_aug_oct"] = c3["stock"]
    if pre:
        tp = period_table(sims, pre)
        table["fill_pre_diwali_cushion3"] = tp[tp["cushion"] == 3.0].set_index("method")["fill"]
        table["stock_pre_diwali_cushion3"] = tp[tp["cushion"] == 3.0].set_index("method")["stock"]
    for _, _, lab in checks.REG_BINS:
        tg = period_table(sims, conf, reg=set(regs[regs == lab].index))
        table[f"fill_cushion3_aug_oct_{lab}"] = tg[tg["cushion"] == 3.0].set_index("method")["fill"]
    table = table.reindex(cands)
    winner = table["points_vs_avg_apr_jul"].fillna(-np.inf).idxmax()
    confirmed = bool(table.loc[winner, "points_vs_avg_aug_oct"] > 0) if winner != "avg" else True
    chosen = winner if confirmed else "avg"
    weight = float(chosen.split("_")[1]) if chosen.startswith("blend_") else None
    curves = pd.concat({"apr_jul": t_sel, "aug_oct": t_conf}, names=["period"]).reset_index(level=0)
    curves.to_csv(out / "forecaster_curves.csv", index=False)
    table.round(2).to_csv(out / "forecaster_tournament.csv")
    result = dict(created=pd.Timestamp.now().isoformat(timespec="seconds"), chosen=chosen, best_ml=best,
                  winner_apr_jul=winner, confirmed_aug_oct=confirmed, weight=weight,
                  equal_stock_reference=dict(apr_jul=round(S_sel), aug_oct=round(S_conf),
                                             note="average stock the 6-month averaging needs at cushion 3"),
                  selection_period="2026-04..2026-07", confirmation_period="2026-08..2026-10",
                  pre_diwali_month=[f"{m:%Y-%m}" for m in pre], products=len(eligible),
                  table=json.loads(table.round(2).to_json(orient="index")),
                  runtime_minutes=round((time.time() - t0) / 60, 1))
    (out / "forecaster_tournament.json").write_text(json.dumps(result, indent=2))
    CHOICE_FILE.write_text(f"{chosen}\n" + (f"weight={weight}\n" if weight is not None else "")
                           + f"best_ml={best}\n# chosen by inventory/tournament.py on {result['created'][:10]}: "
                             f"winner on Apr-Jul = {winner}; confirmed on Aug-Oct = {confirmed}\n")
    log(f"chosen: {chosen} (winner on Apr-Jul: {winner}; confirmed on Aug-Oct: {confirmed}); {result['runtime_minutes']} min")
    return result, table


def read_choice(path=CHOICE_FILE):
    """{'name': ..., 'weight': ..., 'best_ml': ...} from config/forecaster.txt (default: ml)."""
    if not Path(path).exists():
        return dict(name="ml", weight=None, best_ml="ml")
    lines = [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
    kv = dict(l.split("=", 1) for l in lines[1:] if "=" in l)
    return dict(name=lines[0], weight=float(kv["weight"]) if "weight" in kv else None, best_ml=kv.get("best_ml", "ml"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="outputs")
    a = ap.parse_args()
    res, tab = run(a.data, a.out, log=lambda m: print(m, flush=True))
    pd.set_option("display.width", 250)
    print(tab.round(2).to_string())


def final_forecast(choice, panel, products, fc_ml, item_map=None, lines=None, months=12, power=1.5, log=print):
    """The chosen forecaster's forecast for the `months` months after the data ends (all data).
    fc_ml: the current ML model's forecast (product_id, month, mean, p90). Returns product_id, month,
    likely, high, plus the component columns used."""
    last = panel["month"].max()
    D = last + pd.DateOffset(months=1)
    base = fc_ml[["product_id", "month", "mean", "p90"]].rename(columns={"mean": "ml"}).copy()
    base["month"] = pd.to_datetime(base["month"])
    need = {choice["name"]}
    if choice["name"].startswith("blend"):
        need |= {choice["best_ml"], "avg"} | ({"seasonal"} if choice["name"] == "blend3" else set())
    if "avg" in need:
        avg = average_baseline(panel, D)
        base["avg"] = base["product_id"].map(avg).fillna(0.0)
    if "seasonal" in need:
        s = seasonal_paths(panel, [D], months, lead_gap=0)
        base = base.merge(s[["product_id", "month", "seasonal"]], on=["product_id", "month"], how="left")
    for name in ("ml_regular", "ml_simple"):
        if name in need:
            use_lines = lines if (name == "ml_regular" or choice["best_ml"] == "ml_regular") else None
            params = SIMPLE_PARAMS if name == "ml_simple" else None
            p = ml_paths(panel, products, [D + pd.DateOffset(months=0)], months, lead_gap=0, power=power, item_map=item_map,
                         params=params, lines=use_lines, log=log, tag=name)
            base = base.merge(p[["product_id", "month", name]], on=["product_id", "month"], how="left")
    n = choice["name"]
    if n.startswith("blend_"):
        w = choice["weight"]
        base["likely"] = w * base[choice["best_ml"]] + (1 - w) * base["avg"]
    elif n == "blend3":
        base["likely"] = (base[choice["best_ml"]] + base["avg"] + base["seasonal"].fillna(base["avg"])) / 3
    else:
        base["likely"] = base[n]
    base["likely"] = base["likely"].fillna(0.0).clip(lower=0)
    base["high"] = np.maximum(base["p90"].fillna(0.0), base["likely"])
    return base
