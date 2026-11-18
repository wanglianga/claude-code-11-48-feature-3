"""Center-local job executor.

Reads ONLY the center's own registry.csv, applies the cohort rule and the
statistical request, and returns aggregated counts/means. Individual records
never leave this module's caller (the center agent server).

Two job kinds:
- aggregate jobs (``request_spec``): grouped counts / means / rates.
- effect jobs (``effect_spec``): the center fits its local effect model under
  the common analysis rule and returns aggregate diagnostics (effect size,
  variance, in/excluded counts, covariate balance, convergence). Diagnostics
  are returned ONLY when the local 2x2 table passes the minimum-cell check;
  otherwise a protected category is reported and no effect values leave the
  center.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
}


class MissingMapping(Exception):
    def __init__(self, variable: str):
        self.variable = variable
        super().__init__(f"variable '{variable}' has no local field mapping")


def _coerce(v: str):
    if v == "":
        return None
    for cast in (int, float):
        try:
            return cast(v)
        except (ValueError, TypeError):
            continue
    return v


def load_records(data_dir: str | Path, mapping: dict[str, dict]) -> list[dict]:
    """Load registry.csv and rename local fields to canonical variables.

    mapping: {"patient.sex": {"field": "pat_sex", "value_map": {...}|None}, ...}
    """
    csv_path = Path(data_dir) / "registry.csv"
    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    header = set(rows[0].keys()) if rows else set()
    records = []
    for row in rows:
        rec = {}
        for var, spec in mapping.items():
            field = spec.get("field")
            if not field or field not in header:
                raise MissingMapping(var)
            val = row.get(field, "")
            vmap = spec.get("value_map")
            if vmap and val in vmap:
                val = vmap[val]
            rec[var] = _coerce(val)
        records.append(rec)
    return records


def _match(rec: dict, cond: dict) -> bool:
    val = rec.get(cond["variable"])
    op = OPS[cond["op"]]
    target = cond["value"]
    if val is None:
        return False
    try:
        return op(val, target)
    except TypeError:
        # e.g. comparing str with number — treat as non-match
        return False


def apply_rule(records: list[dict], conditions: list[dict]) -> list[dict]:
    includes = [c for c in conditions if c.get("kind") == "include"]
    excludes = [c for c in conditions if c.get("kind") == "exclude"]
    out = []
    for rec in records:
        if includes and not all(_match(rec, c) for c in includes):
            continue
        if any(_match(rec, c) for c in excludes):
            continue
        out.append(rec)
    return out


def _metric_name(spec: dict) -> str:
    if spec["type"] == "outcome_count":
        return f"outcome_{spec['value']}_n"
    if spec["type"] == "mean":
        return f"{spec['variable']}_mean"
    return spec["type"]


def required_variables(conditions: list[dict], request_spec: dict) -> set[str]:
    vs = {c["variable"] for c in conditions}
    vs.update(request_spec.get("group_by", []))
    for m in request_spec.get("metrics", []):
        if m["type"] in ("outcome_count", "mean"):
            vs.add(m["variable"])
    return vs


def execute(data_dir: str | Path, job: dict) -> dict:
    """Run one job. Returns {"status": "success", ...} or an error payload."""
    if job.get("effect_spec"):
        return execute_effect(data_dir, job)
    conditions = job.get("conditions", [])
    spec = job["request_spec"]
    mapping = job.get("mapping", {})

    missing = [v for v in sorted(required_variables(conditions, spec))
               if v not in mapping or not mapping[v].get("field")]
    if missing:
        return {"status": "missing_mapping",
                "error": f"no mapping for variable(s): {', '.join(missing)}"}
    try:
        records = load_records(data_dir, mapping)
    except MissingMapping as e:
        return {"status": "missing_mapping", "error": str(e)}

    cohort = apply_rule(records, conditions)
    group_vars = spec.get("group_by", [])
    metric_specs = [m for m in spec.get("metrics", []) if m["type"] != "count"]

    buckets: dict[tuple, list[dict]] = {}
    for rec in cohort:
        key = tuple(str(rec.get(v)) for v in group_vars)
        buckets.setdefault(key, []).append(rec)

    groups = []
    for key, members in sorted(buckets.items()):
        g = {
            "key": dict(zip(group_vars, key)),
            "label": " / ".join(key) if key else "ALL",
            "n": len(members),
            "metrics": {},
        }
        for ms in metric_specs:
            name = _metric_name(ms)
            if ms["type"] == "outcome_count":
                cnt = sum(1 for r in members if r.get(ms["variable"]) == ms["value"])
                g["metrics"][f"{name}"] = cnt
                g["metrics"][f"outcome_{ms['value']}_rate"] = (
                    round(cnt / len(members), 4) if members else None
                )
            elif ms["type"] == "mean":
                vals = [r.get(ms["variable"]) for r in members
                        if isinstance(r.get(ms["variable"]), (int, float))]
                s = round(sum(vals), 4)
                g["metrics"][name] = round(s / len(vals), 4) if vals else None
                g["metrics"][f"_{name}_sum"] = s
                g["metrics"][f"_{name}_n"] = len(vals)
        groups.append(g)

    return {
        "status": "success",
        "total_n": len(cohort),
        "groups": groups,
        "records_scanned": len(records),
    }


# ---------------------------------------------------------------------------
# Effect jobs: local effect-size diagnostics under the common analysis rule.
# ---------------------------------------------------------------------------

# Protected categories (never carry effect values back to the coordinator):
#   insufficient_events   - a 2x2 cell is exactly 0 (effect undefined)
#   small_cell_suppressed - a 2x2 cell is in (0, min_cell): disclosure risk
#   no_common_support     - treatment arms do not overlap on a covariate
#   model_not_converged   - the local adjusted model failed to converge
#   missing_mapping / failed - coverage gaps (same semantics as aggregate jobs)


def _effect_required(spec: dict, conditions: list[dict]) -> set[str]:
    vs = {c["variable"] for c in conditions}
    vs.add(spec["treatment_variable"])
    vs.add(spec["outcome_variable"])
    vs.update(spec.get("covariates", []))
    return vs


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _common_support(treated: list[dict], control: list[dict], covariates: list[str],
                    cov_types: dict) -> dict:
    """Positivity check: every covariate region occupied by one arm must also
    be occupied by the other. Numeric -> range overlap; categorical -> no level
    exclusive to one arm."""
    for cv in covariates:
        if cov_types.get(cv) == "numeric":
            t = [_num(r.get(cv)) for r in treated]
            c = [_num(r.get(cv)) for r in control]
            t = [v for v in t if v is not None]
            c = [v for v in c if v is not None]
            if not t or not c:
                return {"ok": False, "covariate": cv, "reason": "empty_covariate_arm"}
            if max(t) < min(c) or max(c) < min(t):
                return {"ok": False, "covariate": cv,
                        "reason": "disjoint_ranges",
                        "treated_range": [min(t), max(t)],
                        "control_range": [min(c), max(c)]}
        else:
            t_levels = {str(r.get(cv)) for r in treated if r.get(cv) is not None}
            c_levels = {str(r.get(cv)) for r in control if r.get(cv) is not None}
            only_t = sorted(t_levels - c_levels)
            only_c = sorted(c_levels - t_levels)
            if only_t or only_c:
                return {"ok": False, "covariate": cv,
                        "reason": "exclusive_levels",
                        "only_treated": only_t, "only_control": only_c}
    return {"ok": True}


def _balance(treated: list[dict], control: list[dict], covariates: list[str],
             cov_types: dict) -> dict:
    """Covariate balance summary: standardized mean differences per covariate
    (per level for categoricals) plus arm-level aggregate summaries."""
    out: dict = {}
    max_smd = 0.0
    for cv in covariates:
        if cov_types.get(cv) == "numeric":
            t = [v for v in (_num(r.get(cv)) for r in treated) if v is not None]
            c = [v for v in (_num(r.get(cv)) for r in control) if v is not None]
            if not t or not c:
                continue
            mt, mc = sum(t) / len(t), sum(c) / len(c)
            vt = sum((x - mt) ** 2 for x in t) / max(len(t) - 1, 1)
            vc = sum((x - mc) ** 2 for x in c) / max(len(c) - 1, 1)
            pooled = math.sqrt((vt + vc) / 2) if (vt + vc) > 0 else 0.0
            smd = (mt - mc) / pooled if pooled else 0.0
            max_smd = max(max_smd, abs(smd))
            out[cv] = {"type": "numeric", "smd": round(smd, 4),
                       "treated": {"mean": round(mt, 4), "sd": round(math.sqrt(vt), 4),
                                   "min": min(t), "max": max(t)},
                       "control": {"mean": round(mc, 4), "sd": round(math.sqrt(vc), 4),
                                   "min": min(c), "max": max(c)}}
        else:
            levels = sorted({str(r.get(cv)) for r in treated + control
                             if r.get(cv) is not None})
            level_rows = {}
            worst = 0.0
            for lv in levels:
                pt = sum(1 for r in treated if str(r.get(cv)) == lv) / max(len(treated), 1)
                pc = sum(1 for r in control if str(r.get(cv)) == lv) / max(len(control), 1)
                denom = math.sqrt((pt * (1 - pt) + pc * (1 - pc)) / 2)
                smd = (pt - pc) / denom if denom else 0.0
                worst = max(worst, abs(smd))
                level_rows[lv] = {"treated": round(pt, 4), "control": round(pc, 4),
                                  "smd": round(smd, 4)}
            max_smd = max(max_smd, worst)
            out[cv] = {"type": "categorical", "max_abs_smd": round(worst, 4),
                       "levels": level_rows}
    return {"covariates": out, "max_abs_smd": round(max_smd, 4),
            "imbalanced": max_smd > 0.1}


# --- tiny pure-python linear algebra (design matrices are small) -------------

def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve Ax=b by Gauss elimination with partial pivoting; None if singular."""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def _inverse(a: list[list[float]]) -> list[list[float]] | None:
    n = len(a)
    inv = []
    for col in range(n):
        e = [0.0] * n
        e[col] = 1.0
        sol = _solve(a, e)
        if sol is None:
            return None
        inv.append(sol)
    return [[inv[c][r] for c in range(n)] for r in range(n)]


def _fit_logistic(rows: list[dict], out_var: str, out_val, covariates: list[str],
                  cov_types: dict, max_iter: int = 25, tol: float = 1e-7) -> dict:
    """IRLS logistic regression: outcome ~ treated + covariates.

    rows: [{"treated": 0|1, "y": 0|1, cov...}]. Returns convergence status,
    coefficients and the covariance matrix (for the adjusted log-OR)."""
    # numeric covariates are standardized for numerical stability
    num_stats: dict[str, tuple[float, float]] = {}
    terms: list[tuple[str, str | None]] = [("intercept", None), ("treated", None)]
    dropped: list[str] = []
    for cv in covariates:
        if cov_types.get(cv) == "numeric":
            vals = [float(r[cv]) for r in rows if isinstance(r.get(cv), (int, float))]
            if not vals:
                dropped.append(cv)
                continue
            mean = sum(vals) / len(vals)
            sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(len(vals) - 1, 1))
            if sd < 1e-12:
                dropped.append(cv)
                continue
            num_stats[cv] = (mean, sd)
            terms.append((cv, None))
        else:
            levels = sorted({str(r.get(cv)) for r in rows if r.get(cv) is not None})
            for lv in levels[1:]:  # first level is the reference
                terms.append((cv, lv))

    def features(r: dict) -> list[float] | None:
        row = []
        for name, level in terms:
            if name == "intercept":
                row.append(1.0)
            elif name == "treated":
                row.append(float(r["treated"]))
            elif level is None:  # numeric, standardized
                v = r.get(name)
                if not isinstance(v, (int, float)):
                    return None
                mean, sd = num_stats[name]
                row.append((float(v) - mean) / sd)
            else:  # categorical dummy
                v = r.get(name)
                if v is None:
                    return None
                row.append(1.0 if str(v) == level else 0.0)
        return row

    X, y = [], []
    skipped = 0
    for r in rows:
        f = features(r)
        if f is None:
            skipped += 1
            continue
        X.append(f)
        y.append(float(r["y"]))
    p = len(terms)
    if len(X) < p + 2:
        return {"converged": False, "reason": "too few complete records for the model"}

    beta = [0.0] * p
    cov = None
    for it in range(1, max_iter + 1):
        xtw_x = [[0.0] * p for _ in range(p)]
        xtw_r = [0.0] * p
        for xi, yi in zip(X, y):
            eta = max(-35.0, min(35.0, sum(b * x for b, x in zip(beta, xi))))
            mu = 1.0 / (1.0 + math.exp(-eta))
            w = max(mu * (1 - mu), 1e-9)
            ri = yi - mu
            for a in range(p):
                xtw_r[a] += xi[a] * ri
                for bcol in range(a, p):
                    xtw_x[a][bcol] += xi[a] * w * xi[bcol]
        for a in range(p):
            for bcol in range(a):
                xtw_x[a][bcol] = xtw_x[bcol][a]
        delta = _solve(xtw_x, xtw_r)
        if delta is None:
            return {"converged": False, "reason": "singular design matrix"}
        beta = [b + d for b, d in zip(beta, delta)]
        if max(abs(b) for b in beta) > 15.0:
            return {"converged": False,
                    "reason": "separation (coefficient divergence)"}
        if max(abs(d) for d in delta) < tol:
            cov = _inverse(xtw_x)
            if cov is None:
                return {"converged": False, "reason": "singular information matrix"}
            return {"converged": True, "beta": beta, "cov": cov,
                    "iterations": it, "terms": [t[0] if t[1] is None else f"{t[0]}={t[1]}"
                                                for t in terms],
                    "n_model": len(X), "n_skipped": skipped,
                    "dropped_covariates": dropped}
    return {"converged": False, "reason": "max iterations reached"}


def execute_effect(data_dir: str | Path, job: dict) -> dict:
    """Local effect diagnostics under the common analysis rule.

    Returns {"status": "included", "diagnostics": {...}} only when every local
    check passes; otherwise a protected category with NO effect values."""
    conditions = job.get("conditions", [])
    spec = job["effect_spec"]
    mapping = job.get("mapping", {})
    min_cell = int(spec.get("min_cell", 5))
    treat_var = spec["treatment_variable"]
    treated_val, control_val = spec["treated"], spec["control"]
    out_var, out_val = spec["outcome_variable"], spec["outcome_value"]
    covariates = list(spec.get("covariates", []))
    cov_types = dict(spec.get("covariate_types", {}))

    missing = [v for v in sorted(_effect_required(spec, conditions))
               if v not in mapping or not mapping[v].get("field")]
    if missing:
        return {"status": "missing_mapping",
                "error": f"no mapping for variable(s): {', '.join(missing)}"}
    try:
        records = load_records(data_dir, mapping)
    except MissingMapping as e:
        return {"status": "missing_mapping", "error": str(e)}

    cohort = apply_rule(records, conditions)
    n_cohort = len(cohort)

    treated_recs, control_recs = [], []
    other_arm = 0
    for r in cohort:
        v = r.get(treat_var)
        if v == treated_val:
            treated_recs.append(r)
        elif v == control_val:
            control_recs.append(r)
        else:
            other_arm += 1
    t_missing = sum(1 for r in treated_recs if r.get(out_var) is None)
    c_missing = sum(1 for r in control_recs if r.get(out_var) is None)
    treated_recs = [r for r in treated_recs if r.get(out_var) is not None]
    control_recs = [r for r in control_recs if r.get(out_var) is not None]
    n_t, n_c = len(treated_recs), len(control_recs)
    n_included = n_t + n_c
    excluded = {"other_arm": other_arm, "missing_outcome": t_missing + c_missing}

    base = {"n_cohort": n_cohort, "n_included": n_included,
            "n_excluded": n_cohort - n_included, "excluded_breakdown": excluded,
            "min_cell": min_cell}

    # 1) degenerate arms: one arm has no patients at all -> no shared support
    if n_t == 0 or n_c == 0:
        return {"status": "no_common_support",
                "detail": {**base, "reason": "empty_treatment_arm"}}

    et = sum(1 for r in treated_recs if r.get(out_var) == out_val)
    ec = sum(1 for r in control_recs if r.get(out_var) == out_val)
    cells = {"events_treated": et, "nonevents_treated": n_t - et,
             "events_control": ec, "nonevents_control": n_c - ec}

    # 2) zero cells: effect undefined (too few events) — methodological exclusion
    if any(v == 0 for v in cells.values()):
        return {"status": "insufficient_events",
                "detail": {**base, "zero_cells": sum(1 for v in cells.values() if v == 0)}}

    # 3) small cells: disclosure risk — values must not leave the center
    if any(0 < v < min_cell for v in cells.values()):
        return {"status": "small_cell_suppressed", "detail": dict(base)}

    # 4) covariate common support between the arms
    support = _common_support(treated_recs, control_recs, covariates, cov_types)
    if not support["ok"]:
        return {"status": "no_common_support", "detail": {**base, **support}}

    # 5) local adjusted model must converge
    model_rows = [{"treated": 1, "y": 1 if r.get(out_var) == out_val else 0, **r}
                  for r in treated_recs] + [
                     {"treated": 0, "y": 1 if r.get(out_var) == out_val else 0, **r}
                     for r in control_recs]
    fit = _fit_logistic(model_rows, out_var, out_val, covariates, cov_types)
    if not fit["converged"]:
        return {"status": "model_not_converged",
                "detail": {**base, "reason": fit["reason"]}}

    # --- all checks passed: aggregate diagnostics may leave the center -------
    p1, p0 = et / n_t, ec / n_c
    rd = p1 - p0
    v_rd = p1 * (1 - p1) / n_t + p0 * (1 - p0) / n_c
    lor = math.log((et / (n_t - et)) / (ec / (n_c - ec)))
    v_lor = 1 / et + 1 / (n_t - et) + 1 / ec + 1 / (n_c - ec)
    # adjusted log-OR from the local model (treatment coefficient)
    beta_t = fit["beta"][1]
    se_t = math.sqrt(fit["cov"][1][1])

    diagnostics = {
        **base,
        "n_treated": n_t, "n_control": n_c,
        "events_treated": et, "events_control": ec,
        "effects": {
            "risk_difference": {"theta": round(rd, 6), "variance": round(v_rd, 8),
                                "se": round(math.sqrt(v_rd), 6)},
            "log_odds_ratio": {"theta": round(lor, 6), "variance": round(v_lor, 8),
                               "se": round(math.sqrt(v_lor), 6)},
            "log_odds_ratio_adjusted": {"theta": round(beta_t, 6),
                                        "variance": round(se_t * se_t, 8),
                                        "se": round(se_t, 6)},
        },
        "balance": _balance(treated_recs, control_recs, covariates, cov_types),
        "support": {"ok": True},
        "converged": True,
        "model": {"iterations": fit["iterations"], "n_model": fit["n_model"],
                  "terms": fit["terms"],
                  "dropped_covariates": fit["dropped_covariates"]},
    }
    return {"status": "included", "diagnostics": diagnostics}
