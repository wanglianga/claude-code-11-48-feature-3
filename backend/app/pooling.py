"""Cross-center effect pooling at the coordinator.

Consumes ONLY the aggregate per-center diagnostics that passed the centers'
own minimum-cell checks (no patient-level records ever arrive here) and
produces:

- fixed-effect (inverse-variance) and random-effects (DerSimonian-Laird)
  pooled estimates per effect measure,
- heterogeneity diagnostics (Cochran's Q, I^2, H^2, tau^2, chi^2 p-value),
- center contribution ranges and weight shares,
- heterogeneity / stability hints and the premises of the chosen method,
- the comparable covariate support range across included centers.

Neither pooling method is treated as an automatic correct answer: the payload
always carries both estimators, the plan's primary choice, its premises and
the reasons centers could not be included. Centers that reported protected
categories are never zero-filled into the pool.
"""
from __future__ import annotations

import math

MEASURE_LABELS = {
    "risk_difference": "缓解率差（RD）",
    "log_odds_ratio": "对数比值比（log OR）",
    "log_odds_ratio_adjusted": "协变量调整对数比值比（log OR）",
}

PREMISES = {
    "fixed": (
        "固定效应汇总：假设所有中心估计同一个真实效应。共同效应假设合理时更精确；"
        "但当中心间存在真实差异时会低估变异，置信区间偏窄。"
    ),
    "random": (
        "随机效应汇总：允许各中心真实效应不同，估计平均效应。更保守且能表达中心间变异；"
        "但纳入中心很少或部分中心精度极低时，τ²（中心间方差）估计不稳定。"
    ),
}

# Random-effects pooling needs a minimum number of centers to be trustworthy.
MIN_RANDOM_CENTERS = 5
# A covariate is considered imbalanced beyond this |SMD|.
SMD_IMBALANCE = 0.1


# --- chi^2 survival function via the regularized upper incomplete gamma ------

def _gammq(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) = Γ(a, x) / Γ(a)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        # series expansion for the lower function, then complement
        ap = a
        total = 1.0 / a
        delta = total
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-14:
                break
        return max(0.0, min(1.0, 1.0 - total * math.exp(-x + a * math.log(x)
                                                          - math.lgamma(a))))
    # continued fraction for the upper function
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if b != 0 else tiny
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h))


def chi2_sf(x: float, df: int) -> float:
    """P(χ²_df > x)."""
    if df <= 0:
        return 1.0
    return _gammq(df / 2.0, x / 2.0)


# --- core estimators ----------------------------------------------------------

def pool_fixed(thetas: list[float], variances: list[float]) -> dict:
    """Inverse-variance (fixed-effect) pooling."""
    ws = [1.0 / v for v in variances]
    sw = sum(ws)
    theta = sum(w * t for w, t in zip(ws, thetas)) / sw
    se = math.sqrt(1.0 / sw)
    return {"theta": theta, "se": se, "weights": ws}


def pool_random(thetas: list[float], variances: list[float]) -> dict:
    """DerSimonian–Laird random-effects pooling."""
    k = len(thetas)
    fe = pool_fixed(thetas, variances)
    q = sum(w * (t - fe["theta"]) ** 2 for w, t in zip(fe["weights"], thetas))
    sw = sum(fe["weights"])
    c = sw - sum(w * w for w in fe["weights"]) / sw
    tau2 = max(0.0, (q - (k - 1)) / c) if c > 0 else 0.0
    ws = [1.0 / (v + tau2) for v in variances]
    theta = sum(w * t for w, t in zip(ws, thetas)) / sum(ws)
    se = math.sqrt(1.0 / sum(ws))
    return {"theta": theta, "se": se, "tau2": tau2, "weights": ws}


def heterogeneity(thetas: list[float], variances: list[float]) -> dict:
    k = len(thetas)
    fe = pool_fixed(thetas, variances)
    q = sum(w * (t - fe["theta"]) ** 2 for w, t in zip(fe["weights"], thetas))
    df = k - 1
    i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    return {"Q": q, "df": df, "I2": i2,
            "H2": q / df if df else 1.0, "p": chi2_sf(q, df) if df else 1.0}


# --- hints --------------------------------------------------------------------

def _hints(rows: list[dict], measure: str, het: dict, re_: dict,
           min_random_centers: int) -> list[dict]:
    """rows: included centers with diagnostics. Human-readable hints; severity
    info|warn|bad. Never phrased as a definitive clinical conclusion."""
    hints: list[dict] = []
    k = len(rows)
    thetas = [r["diagnostics"]["effects"][measure]["theta"] for r in rows]

    i2 = het["I2"]
    if i2 >= 50:
        hints.append({"type": "heterogeneity", "severity": "warn",
                      "text": f"中心间异质性显著（I²={i2:.0f}%，Q={het['Q']:.1f}，"
                              f"df={het['df']}，p={het['p']:.3f}）：汇总值不能代表所有中心。"})
    elif i2 >= 25:
        hints.append({"type": "heterogeneity", "severity": "info",
                      "text": f"中心间存在中等异质性（I²={i2:.0f}%）。"})
    else:
        hints.append({"type": "heterogeneity", "severity": "info",
                      "text": f"中心间异质性较低（I²={i2:.0f}%）。"})

    pos = [r["center"] for r, t in zip(rows, thetas) if t > 0]
    neg = [r["center"] for r, t in zip(rows, thetas) if t < 0]
    if pos and neg:
        hints.append({
            "type": "direction", "severity": "bad",
            "text": (f"效应方向不一致：{', '.join(pos)} 为正，{', '.join(neg)} 为负。"
                     "把比率直接相加会让大中心掩盖小中心的反向结果。")})
        ns = {r["center"]: r["diagnostics"]["n_included"] for r in rows}
        neg_sizes = [ns[c] for c in neg]
        pos_sizes = [ns[c] for c in pos]
        smaller = larger = None
        if max(neg_sizes) <= min(pos_sizes):
            smaller, larger = neg, pos  # the smaller centers point negative
        elif max(pos_sizes) <= min(neg_sizes):
            smaller, larger = pos, neg
        if smaller:
            hints.append({
                "type": "size_direction", "severity": "warn",
                "text": (f"方向差异与中心规模相关：较小的中心（{', '.join(smaller)}）"
                         f"与较大的中心（{', '.join(larger)}）方向相反，"
                         "固定效应权重会让大中心主导汇总。")})

    imbalanced = [r["center"] for r in rows
                  if r["diagnostics"].get("balance", {}).get("imbalanced")]
    if imbalanced and pos and neg:
        hints.append({
            "type": "case_mix", "severity": "warn",
            "text": (f"中心 {', '.join(imbalanced)} 的治疗组间协变量失衡"
                     f"（|SMD|>{SMD_IMBALANCE}）：中心病例组合不同，"
                     "异质性可能部分来自病例组合差异，不能简单解释为治疗效果不稳定。")})

    if k < min_random_centers:
        hints.append({
            "type": "random_stability", "severity": "warn",
            "text": (f"纳入中心仅 {k} 家（<{min_random_centers}）：随机效应 τ² "
                     "估计不稳定，随机效应结果仅供参考。")})
    if re_["tau2"] == 0.0 and het["I2"] > 0:
        hints.append({"type": "random_stability", "severity": "info",
                      "text": "τ² 估计触及边界 0，随机效应退化为固定效应。"})
    ses = [r["diagnostics"]["effects"][measure]["se"] for r in rows]
    if ses and max(ses) > 2 * sorted(ses)[len(ses) // 2]:
        hints.append({"type": "random_stability", "severity": "info",
                      "text": "部分中心精度远低于中位水平，随机效应权重分配受其影响较大。"})
    return hints


def _support_range(rows: list[dict]) -> dict:
    """Comparable covariate support: the region where EVERY included center has
    both treatment arms represented (intersection of per-center arm ranges)."""
    numeric: dict[str, list] = {}
    categorical: dict[str, set] = {}
    for r in rows:
        covs = r["diagnostics"].get("balance", {}).get("covariates", {})
        for cv, b in covs.items():
            if b["type"] == "numeric":
                lo = max(b["treated"]["min"], b["control"]["min"])
                hi = min(b["treated"]["max"], b["control"]["max"])
                if cv in numeric:
                    numeric[cv][0] = max(numeric[cv][0], lo)
                    numeric[cv][1] = min(numeric[cv][1], hi)
                else:
                    numeric[cv] = [lo, hi]
            else:
                both = {lv for lv, p in b["levels"].items()
                        if p["treated"] > 0 and p["control"] > 0}
                if cv in categorical:
                    categorical[cv] &= both
                else:
                    categorical[cv] = both
    out: dict = {}
    for cv, (lo, hi) in numeric.items():
        out[cv] = {"type": "numeric",
                   "range": [lo, hi] if lo <= hi else None,
                   "note": None if lo <= hi else "纳入中心间无共同可比范围"}
    for cv, levels in categorical.items():
        out[cv] = {"type": "categorical", "levels": sorted(levels)}
    return out


def build_pooling(center_rows: list[dict], primary_measure: str,
                  primary_pooling: str,
                  min_random_centers: int = MIN_RANDOM_CENTERS) -> dict:
    """center_rows: [{"center": code, "diagnostics": {...}}] — included centers
    only. Returns the full coordinator-side pooling payload."""
    k = len(center_rows)
    measures = list(MEASURE_LABELS)
    pooled_measures: dict = {}
    hints: list[dict] = []

    if k == 0:
        hints.append({"type": "no_pool", "severity": "bad",
                      "text": "没有中心通过本地检查，无法形成任何汇总估计；"
                              "不以零效应补位。"})
    elif k == 1:
        hints.append({"type": "singleton_pool", "severity": "warn",
                      "text": "仅 1 家中心纳入：汇总值即该中心自身结果，"
                              "不能视为跨中心证据。"})

    for m in measures:
        rows = [r for r in center_rows
                if r["diagnostics"].get("effects", {}).get(m)]
        if not rows:
            continue
        thetas = [r["diagnostics"]["effects"][m]["theta"] for r in rows]
        variances = [max(r["diagnostics"]["effects"][m]["variance"], 1e-12)
                     for r in rows]
        fe = pool_fixed(thetas, variances)
        re_ = pool_random(thetas, variances)
        het = heterogeneity(thetas, variances)
        sw_fe, sw_re = sum(fe["weights"]), sum(re_["weights"])
        contributions = []
        for r, w_f, w_r in zip(rows, fe["weights"], re_["weights"]):
            d = r["diagnostics"]
            contributions.append({
                "center": r["center"],
                "theta": round(d["effects"][m]["theta"], 6),
                "se": round(d["effects"][m]["se"], 6),
                "n_included": d["n_included"],
                "weight_fe": round(w_f / sw_fe, 4),
                "weight_re": round(w_r / sw_re, 4),
            })
        ordered = sorted(contributions, key=lambda c: c["theta"])
        pooled_measures[m] = {
            "label": MEASURE_LABELS[m],
            "fixed": {"theta": round(fe["theta"], 6), "se": round(fe["se"], 6),
                      "ci": [round(fe["theta"] - 1.96 * fe["se"], 6),
                             round(fe["theta"] + 1.96 * fe["se"], 6)]},
            "random": {"theta": round(re_["theta"], 6), "se": round(re_["se"], 6),
                       "ci": [round(re_["theta"] - 1.96 * re_["se"], 6),
                              round(re_["theta"] + 1.96 * re_["se"], 6)],
                       "tau2": round(re_["tau2"], 8)},
            "heterogeneity": {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                              for kk, vv in het.items()},
            "contributions": contributions,
            "contribution_range": {"min": ordered[0], "max": ordered[-1]},
        }
        if m == primary_measure and rows:
            hints.extend(_hints(rows, m, het, re_, min_random_centers))

    stability = []
    if 0 < k < min_random_centers:
        stability.append("unstable_few_centers")

    return {
        "k_included": k,
        "measure_primary": primary_measure,
        "measures": pooled_measures,
        "hints": hints,
        "premises": {"fixed": PREMISES["fixed"], "random": PREMISES["random"],
                     "primary_pooling": primary_pooling},
        "support_range": _support_range(center_rows) if k else {},
        "stability": stability,
    }
