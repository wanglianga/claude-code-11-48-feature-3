"""Deterministic fictional registry data generator for the six demo centers.

Each center gets its own local field names (exercising variable mapping) and
C5 deliberately lacks the lab value field (exercising missing-mapping gaps).
C6 is a tiny center whose small groups trigger small-cell suppression.

Usage:
    python -m center_agent.datagen --root data/centers            # all centers
    python -m center_agent.datagen --root data/centers --center C2 --seed 43
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

# canonical variable -> per-center local CSV column
CENTER_FIELDS = {
    "C1": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date",
           "followup.lab_value": "fu_lab"},
    "C2": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date",
           "followup.lab_value": "fu_lab"},
    "C3": {"patient.sex": "gender", "patient.birth_year": "yob",
           "diagnosis.code": "diag_code", "diagnosis.date": "diag_date",
           "treatment.path": "treat_path", "treatment.start_date": "treat_start",
           "followup.outcome": "outcome", "followup.date": "visit_date",
           "followup.lab_value": "lab"},
    "C4": {"patient.sex": "sex", "patient.birth_year": "birth_year",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "path", "treatment.start_date": "start_date",
           "followup.outcome": "outcome", "followup.date": "fu_date",
           "followup.lab_value": "lab_value"},
    "C5": {"patient.sex": "pat_sex", "patient.birth_year": "pat_byear",
           "diagnosis.code": "dx_code", "diagnosis.date": "dx_date",
           "treatment.path": "tx_path", "treatment.start_date": "tx_start",
           "followup.outcome": "fu_outcome", "followup.date": "fu_date"},
    # C5 has NO followup.lab_value column -> missing mapping
    "C6": {"patient.sex": "SEX", "patient.birth_year": "BYEAR",
           "diagnosis.code": "DX", "diagnosis.date": "DXD",
           "treatment.path": "PATH", "treatment.start_date": "TXD",
           "followup.outcome": "OUTCOME", "followup.date": "FUD",
           "followup.lab_value": "LAB"},
}

# C2 stores outcomes as local codes; the center mapping carries this value map.
C2_OUTCOME_CODES = {"R": "remission", "S": "stable", "P": "progression", "D": "death"}

CENTER_SIZES = {"C1": 180, "C2": 150, "C3": 220, "C4": 160, "C5": 140, "C6": 12}
DEFAULT_SEEDS = {"C1": 4201, "C2": 4202, "C3": 4203, "C4": 4204, "C5": 4205, "C6": 4206}

DIAGNOSES = ["RD01", "RD02", "RD03", "RD04"]
PATHS = ["A", "B", "C"]
PATH_WEIGHTS = [0.45, 0.45, 0.10]
OUTCOMES = ["remission", "stable", "progression", "death"]
OUTCOME_WEIGHTS = [0.45, 0.30, 0.18, 0.07]

# --- Cross-center effect scenarios (for the effect-aggregation feature) -------
# Remission probability shift of the treated arm (path A) vs control (path B).
# C1-C3 are large centres with a positive effect; C4 is a mid-size centre whose
# effect points the OTHER way; C5's treatment arms do not overlap on birth year
# (no common support); C6 is tiny, so its 2x2 cells fall below the min-cell k.
EFFECT_SHIFT = {"C1": 0.25, "C2": 0.25, "C3": 0.25, "C4": -0.15, "C5": 0.0, "C6": 0.25}
BASE_REMISSION = 0.35
NON_REMISSION = (["stable", "progression", "death"], [0.55, 0.32, 0.13])

# C2: patients born >= 1990 get their outcome determined by sex (remission iff
# female) instead of by treatment. A plan restricted to birth_year >= 1990 then
# separates perfectly on sex, so C2's local model cannot converge — the demo
# scenario for "single-centre model failure".
C2_SEPARATION_BYEAR = 1990


def _sex_for(center: str, path: str, rng: random.Random) -> str:
    # C4's treated arm skews female (case-mix imbalance across arms).
    if center == "C4" and path == "A":
        return "F" if rng.random() < 0.8 else "M"
    return rng.choice(["F", "M"])


def _byear_for(center: str, path: str, rng: random.Random) -> int:
    # C5: treated and control arms live on disjoint birth-year ranges, so the
    # covariate common-support check must exclude C5 from pooling.
    if center == "C5":
        if path == "A":
            return rng.randint(1955, 1970)
        if path == "B":
            return rng.randint(1985, 2015)
    return rng.randint(1955, 2015)


def _outcome_for(center: str, path: str, sex: str, byear: int,
                 rng: random.Random) -> str:
    if center == "C2" and byear >= C2_SEPARATION_BYEAR:
        return "remission" if sex == "F" else "stable"
    p = BASE_REMISSION + (EFFECT_SHIFT[center] if path == "A" else 0.0)
    p = min(0.97, max(0.03, p))
    if rng.random() < p:
        return "remission"
    return rng.choices(NON_REMISSION[0], weights=NON_REMISSION[1], k=1)[0]


def generate_center(center: str, root: Path, seed: int | None = None,
                    size: int | None = None) -> dict:
    rng = random.Random(seed if seed is not None else DEFAULT_SEEDS[center])
    n = size if size is not None else CENTER_SIZES[center]
    fields = CENTER_FIELDS[center]
    rows = []
    for i in range(1, n + 1):
        path = rng.choices(PATHS, weights=PATH_WEIGHTS, k=1)[0]
        sex = _sex_for(center, path, rng)
        byear = _byear_for(center, path, rng)
        dx = rng.choice(DIAGNOSES)
        dx_date = f"{rng.randint(2019, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        tx_start = f"{rng.randint(2020, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        outcome = _outcome_for(center, path, sex, byear, rng)
        fu_date = f"{rng.randint(2021, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        lab = round(rng.gauss(6.0, 1.8), 2)
        row = {"patient_id": f"{center}-P{i:04d}"}
        for var, col in fields.items():
            if var == "patient.sex":
                row[col] = sex
            elif var == "patient.birth_year":
                row[col] = byear
            elif var == "diagnosis.code":
                row[col] = dx
            elif var == "diagnosis.date":
                row[col] = dx_date
            elif var == "treatment.path":
                row[col] = path
            elif var == "treatment.start_date":
                row[col] = tx_start
            elif var == "followup.outcome":
                if center == "C2":  # local outcome codes
                    row[col] = {"remission": "R", "stable": "S",
                                "progression": "P", "death": "D"}[outcome]
                else:
                    row[col] = outcome
            elif var == "followup.date":
                row[col] = fu_date
            elif var == "followup.lab_value":
                row[col] = lab
        rows.append(row)

    cdir = root / center
    cdir.mkdir(parents=True, exist_ok=True)
    csv_path = cdir / "registry.csv"
    header = ["patient_id"] + list(fields.values())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    meta = {
        "center": center,
        "seed": seed if seed is not None else DEFAULT_SEEDS[center],
        "patients": n,
        "fields": header,
        "dx_years": sorted({r[fields["diagnosis.date"]][:4] for r in rows}),
    }
    (cdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/centers")
    ap.add_argument("--center", choices=list(CENTER_FIELDS), default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--size", type=int, default=None)
    args = ap.parse_args()
    root = Path(args.root)
    centers = [args.center] if args.center else list(CENTER_FIELDS)
    for c in centers:
        meta = generate_center(c, root, seed=args.seed, size=args.size)
        print(f"{c}: {meta['patients']} patients -> {root / c / 'registry.csv'}")


if __name__ == "__main__":
    main()
