"""Similarity-augmented C5.0-style classifier.

This module approximates C5.0 with scikit-learn's entropy-based
DecisionTreeClassifier. For low-confidence predictions, it blends tree
confidence with cosine similarity from historical buyers.

Public API:
- train(historical_buyers)
- predict(gross_income, monthly_loans, tenure_months, employment_type, age, dependents)
- get_meta()
"""

import json
import numpy as np
from decimal import Decimal
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import accuracy_score
from sklearn.metrics.pairwise import cosine_similarity

# Label encoding
_LABEL_MAP = {
    "Qualified":              2,
    "Conditionally Qualified": 1,
    "Not Qualified":           0,
}
_REV_LABEL = {v: k for k, v in _LABEL_MAP.items()}

# Employment stability score (higher = more stable for banks)
_EMP_MAP = {
    "employed":                 5,
    "ofw-landbased":            4,
    "ofw-seafarer":             4,
    "licensed-professional":    5,
    "with-financial-support":   3,
    "with-attorney-in-fact":    3,
    "with-co-borrower":         4,
    # Legacy aliases (for previously saved records)
    "ofw":                      4,
    "business-owner":           3,
    "self-employed":            3,
    "unemployed":               2,
    "":                         3,
}

# Civil-status multiplier (affects purchasing power perception)
_CS_MAP = {
    "married":   3,
    "single":    2,
    "widowed":   2,
    "separated": 1,
    "":          2,
}

FEATURE_NAMES = [
    "gross_income",
    "monthly_loans",
    "tenure_months",
    "employment_enc",   # encoded employment stability
    "age",
    "dependents",
    "dti_ratio",
    "net_monthly",      #    gross_income*0.72 - monthly_loans
]

# In-memory singleton state
_model:     DecisionTreeClassifier | None = None
_scaler:    MinMaxScaler | None           = None
_train_X:   np.ndarray | None            = None
_train_y:   np.ndarray | None            = None
_model_meta: dict                        = {"trained": False}

# Qualification criteria (admin configurable)
_criteria: dict = {
    "dti_qualified_max":    30.0,   # DTI <= this  → Qualified
    "dti_conditional_max":  42.0,   # DTI <= this  → Conditionally Qualified
    "confidence_threshold": 0.72,   # below this   → similarity augmentation
    "min_tenure_months":    6,      # < this       → tenure risk flag
    "min_gross_income":     15000.0,# < this       → income flag
    "stability_employed":                 5,
    "stability_ofw_landbased":            4,
    "stability_ofw_seafarer":             4,
    "stability_licensed_professional":    5,
    "stability_with_financial_support":   3,
    "stability_with_attorney_in_fact":    3,
    "stability_with_co_borrower":         4,
}


def update_criteria(new_criteria: dict) -> None:
    """Update runtime qualification criteria (called on app startup + admin save)."""
    global _criteria, _EMP_MAP
    for k, v in new_criteria.items():
        if k in _criteria:
            _criteria[k] = v
    # Sync _EMP_MAP from the configurable stability scores
    _EMP_MAP["employed"] = int(_criteria["stability_employed"])
    _EMP_MAP["ofw-landbased"] = int(_criteria["stability_ofw_landbased"])
    _EMP_MAP["ofw-seafarer"] = int(_criteria["stability_ofw_seafarer"])
    _EMP_MAP["licensed-professional"] = int(_criteria["stability_licensed_professional"])
    _EMP_MAP["with-financial-support"] = int(_criteria["stability_with_financial_support"])
    _EMP_MAP["with-attorney-in-fact"] = int(_criteria["stability_with_attorney_in_fact"])
    _EMP_MAP["with-co-borrower"] = int(_criteria["stability_with_co_borrower"])

    # Legacy aliases
    _EMP_MAP["ofw"] = _EMP_MAP["ofw-landbased"]
    _EMP_MAP["business-owner"] = _EMP_MAP["with-financial-support"]
    _EMP_MAP["self-employed"] = _EMP_MAP["with-financial-support"]
    _EMP_MAP["unemployed"] = _EMP_MAP["with-financial-support"]
    _EMP_MAP[""] = _EMP_MAP["with-financial-support"]


def get_criteria() -> dict:
    """Return a copy of the current qualification criteria."""
    return _criteria.copy()


# Feature encoding helper

def _encode(gross_income: float, monthly_loans: float, tenure_months: int,
            employment_type: str, age: int, dependents: int) -> list[float]:
    """Return the 8-dimensional feature vector for one applicant."""
    dti        = (monthly_loans / gross_income * 100) if gross_income > 0 else 100.0
    net_monthly = gross_income * 0.72 - monthly_loans
    emp_enc    = float(_EMP_MAP.get(str(employment_type).lower().strip(), 1))
    return [
        float(gross_income),
        float(monthly_loans),
        float(tenure_months or 0),
        emp_enc,
        float(age or 30),
        float(dependents or 0),
        float(dti),
        float(net_monthly),
    ]


# Training

def train(historical_buyers) -> None:
    """Fit the C5.0 tree + scaler from a list/query of HistoricalBuyer ORM objects."""
    global _model, _scaler, _train_X, _train_y, _model_meta

    rows, labels = [], []
    invalid_label_rows = 0
    for b in historical_buyers:
        try:
            raw_outcome = str(getattr(b, "outcome", "") or "").strip()
            if raw_outcome not in _LABEL_MAP:
                invalid_label_rows += 1
                continue

            feat = _encode(
                float(b.gross_income  or 0),
                float(b.monthly_loans or 0),
                int(b.tenure_months   or 0),
                b.employment_type     or "employed",
                int(getattr(b, "age", None) or 30),
                int(b.dependents      or 0),
            )
            lbl = _LABEL_MAP[raw_outcome]
            rows.append(feat)
            labels.append(lbl)
        except Exception:
            continue

    if len(rows) < 10:
        _model = None
        _scaler = None
        _train_X = None
        _train_y = None
        _model_meta = {
            "trained":   False,
            "reason":    "insufficient_data",
            "n_samples": len(rows),
            "invalid_labels_skipped": int(invalid_label_rows),
            "note":      "Need at least 10 historical records to train.",
        }
        return

    X = np.array(rows, dtype=float)
    y = np.array(labels)

    scaler  = MinMaxScaler()
    X_scaled = scaler.fit_transform(X)

    # C5.0 ≈ DecisionTree with information-gain (entropy) criterion
    clf = DecisionTreeClassifier(
        criterion="entropy",       # information gain, same basis as C5.0
        max_depth=7,               # prevent overfit
        min_samples_leaf=3,
        min_samples_split=6,
        class_weight="balanced",   # handle imbalanced outcome classes
        random_state=42,
    )
    clf.fit(X_scaled, y)

    train_acc = float(accuracy_score(y, clf.predict(X_scaled)))

    _model    = clf
    _scaler   = scaler
    _train_X  = X_scaled
    _train_y  = y
    _model_meta = {
        "trained":          True,
        "n_samples":        int(len(rows)),
        "invalid_labels_skipped": int(invalid_label_rows),
        "train_accuracy":   round(train_acc * 100, 1),
        "n_qualified":      int((y == 2).sum()),
        "n_conditional":    int((y == 1).sum()),
        "n_not_qualified":  int((y == 0).sum()),
        "tree_depth":       int(clf.get_depth()),
        "n_leaves":         int(clf.get_n_leaves()),
    }


# Similarity measure

def _compute_similarity(feat_scaled: np.ndarray, top_k: int = 50) -> float:
    """
    Cosine-similarity of applicant vs historical buyers.
    Returns a float in [0, 1] representing how closely the applicant
    resembles historically successful (Qualified / Conditionally Qualified) buyers.
    """
    if _train_X is None or len(_train_X) == 0:
        # No training data to compare against — must be Low similarity per 09_02_2026 spec
        return 0.15

    sims = cosine_similarity([feat_scaled], _train_X)[0]

    # Weight favourable outcomes higher
    weights = np.where(_train_y == 2, 1.5,
              np.where(_train_y == 1, 1.0, 0.4))

    weighted_sims = sims * weights

    # Limit to top-k by weighted similarity (performance optimisation)
    if len(weighted_sims) > top_k:
        top_idx       = np.argsort(weighted_sims)[-top_k:]
        sims_top      = sims[top_idx]
        labels_top    = _train_y[top_idx]
    else:
        sims_top   = sims
        labels_top = _train_y

    total_sim     = sims_top.sum()
    if total_sim == 0:
        return 0.15

    qualified_sim = sims_top[labels_top >= 1].sum()   # Qualified or Conditional
    return float(min(0.99, qualified_sim / total_sim))


# Rule-based fallback

def _rule_predict(dti: float, tenure_months: int,
                  employment_type: str) -> tuple[str, float]:
    """Simple DTI+employment rule when model is not trained yet."""
    emp = str(employment_type).lower().strip()
    q_max  = _criteria["dti_qualified_max"]
    c_max  = _criteria["dti_conditional_max"]
    t_min  = _criteria["min_tenure_months"]
    if dti < q_max and tenure_months >= t_min:
        return "Qualified", 0.82
    if dti <= c_max or (dti < q_max and tenure_months < t_min):
        return "Conditionally Qualified", 0.58
    return "Not Qualified", 0.25


def _rule_sim(dti: float, tenure_months: int, employment_type: str) -> float:
    """Pseudo similarity score derived from rules alone."""
    score = 1.0
    q_max = _criteria.get("dti_qualified_max", 30.0)
    c_max = _criteria.get("dti_conditional_max", 42.0)
    if dti > c_max:
        score -= (dti - c_max) / 100.0
    elif dti > q_max:
        score -= (dti - q_max) / 200.0
    if tenure_months < 6:
        score -= 0.10
    return max(0.05, min(0.99, score))


# Explanation factors

def _build_factors(dti: float, gross_income: float, monthly_loans: float,
                   tenure_months: int, employment_type: str,
                   age: int, dependents: int) -> list[dict]:
    factors = []
    q_max = _criteria["dti_qualified_max"]
    c_max = _criteria["dti_conditional_max"]
    t_min = _criteria["min_tenure_months"]
    i_min = _criteria["min_gross_income"]

    # DTI factor (most influential)
    if dti > c_max:
        factors.append({
            "key":   "Debt-to-Income Ratio",
            "value": f"{dti:.1f}%",
            "note":  f"DTI exceeds {c_max:.0f}% - high financial risk for lenders.",
            "flag":  "danger",
        })
    elif dti > q_max:
        factors.append({
            "key":   "Debt-to-Income Ratio",
            "value": f"{dti:.1f}%",
            "note":  f"DTI is in the borderline range ({q_max:.0f}–{c_max:.0f}%).",
            "flag":  "warning",
        })
    else:
        factors.append({
            "key":   "Debt-to-Income Ratio",
            "value": f"{dti:.1f}%",
            "note":  f"DTI is within acceptable range (< {q_max:.0f}%).",
            "flag":  "success",
        })

    # Employment tenure
    if tenure_months < t_min:
        factors.append({
            "key":   "Employment Tenure",
            "value": f"{tenure_months} months",
            "note":  f"Less than {t_min} months of work history increases risk.",
            "flag":  "warning",
        })
    elif tenure_months >= 24:
        factors.append({
            "key":   "Employment Tenure",
            "value": f"{tenure_months} months",
            "note":  "Stable employment history (≥ 2 years).",
            "flag":  "success",
        })
    else:
        factors.append({
            "key":   "Employment Tenure",
            "value": f"{tenure_months} months",
            "note":  "Employment history is acceptable.",
            "flag":  "info",
        })

    # Gross income
    if gross_income < i_min:
        factors.append({
            "key":   "Monthly Income",
            "value": f"₱{gross_income:,.0f}",
            "note":  f"Income below ₱{i_min:,.0f} may be insufficient for most property loans.",
            "flag":  "danger",
        })
    elif gross_income >= 50_000:
        factors.append({
            "key":   "Monthly Income",
            "value": f"₱{gross_income:,.0f}",
            "note":  "Strong income base supports higher loan capacity.",
            "flag":  "success",
        })
    else:
        factors.append({
            "key":   "Monthly Income",
            "value": f"₱{gross_income:,.0f}",
            "note":  "Income is within the qualified range.",
            "flag":  "info",
        })

    # Employment type
    emp = str(employment_type).lower()
    if emp == "with-financial-support":
        factors.append({
            "key":   "Employment Status",
            "value": "With Financial Support",
            "note":  "A support-based repayment setup can help, but lenders may require stronger guarantor evidence.",
            "flag":  "warning",
        })
    elif emp in {"ofw-landbased", "ofw-seafarer"}:
        factors.append({
            "key":   "Employment Status",
            "value": "OFW",
            "note":  "OFW status may qualify for Pag-IBIG OFW Fund programs.",
            "flag":  "info",
        })

    return factors[:4]


# Main prediction entry point

def predict(gross_income, monthly_loans, tenure_months,
            employment_type, age, dependents):
    """
    Run the Similarity-Augmented C5.0 classification.

    Returns
    -------
    status          : str   - "Qualified" | "Conditionally Qualified" | "Not Qualified"
    dti             : float - debt-to-income ratio (%)
    max_loanable    : Decimal
    similarity_score: float - [0, 1] confidence / similarity score
    factors_json    : str   - JSON array of top explanation factors
    """
    gross_income  = float(gross_income   or 0)
    monthly_loans = float(monthly_loans  or 0)
    tenure_months = int(tenure_months    or 0)
    age           = int(age              or 30)
    dependents    = int(dependents       or 0)
    employment_type = str(employment_type or "employed").lower().strip()

    dti         = (monthly_loans / gross_income * 100) if gross_income > 0 else 100.0
    net_monthly  = gross_income * 0.72 - monthly_loans
    max_loanable = max(Decimal("0.00"), Decimal(str(round(net_monthly * 12 * 15, 2))))

    feat = _encode(gross_income, monthly_loans, tenure_months,
                   employment_type, age, dependents)

    if _model is not None and _scaler is not None:
        # C5.0 decision-tree classification
        feat_scaled = _scaler.transform([feat])[0]
        proba       = _model.predict_proba([feat_scaled])[0]
        classes     = _model.classes_

        proba_dict  = {int(c): float(p) for c, p in zip(classes, proba)}
        q_prob      = proba_dict.get(2, 0.0)
        c_prob      = proba_dict.get(1, 0.0)
        n_prob      = proba_dict.get(0, 0.0)
        dt_pred     = int(_model.predict([feat_scaled])[0])
        dt_conf     = float(max(proba))

        # Similarity augmentation for borderline cases
        sim_score = _compute_similarity(feat_scaled)

        CONFIDENCE_THRESHOLD = _criteria["confidence_threshold"]

        if dt_conf >= CONFIDENCE_THRESHOLD:
            # High-confidence tree prediction: accept directly
            status     = _REV_LABEL.get(dt_pred, "Not Qualified")
            final_conf = dt_conf
        else:
            # Borderline: blend tree probabilities with similarity signal
            aug_q = q_prob + (sim_score * 0.25)
            aug_c = c_prob + (sim_score * 0.10)

            if aug_q >= 0.52:
                status = "Qualified"
            elif aug_q + aug_c >= 0.50 or sim_score >= 0.65:
                status = "Conditionally Qualified"
            else:
                status = "Not Qualified"

            final_conf = round((dt_conf + sim_score) / 2, 4)

        similarity_score = round(min(0.99, final_conf), 4)

    else:
        # Fallback: pure rule-based — with no training data, similarity must be Low (per 09_02_2026 Issue 2)
        status, _ = _rule_predict(dti, tenure_months, employment_type)
        # Cap similarity to Low tier (<0.40) when untrained; preserves some variance for display but never High
        raw_sim = _rule_sim(dti, tenure_months, employment_type)
        similarity_score = round(min(0.35, raw_sim), 4)
        # Extra guard: if insufficient training data, force Low regardless of DTI
        if _train_X is None or len(_train_X) == 0:
            similarity_score = round(min(similarity_score, 0.30), 4)

    factors = _build_factors(dti, gross_income, monthly_loans,
                             tenure_months, employment_type, age, dependents)
    factors_json = json.dumps(factors)

    return status, round(dti, 2), max_loanable, similarity_score, factors_json


# Metadata

def get_meta() -> dict:
    """Return a copy of the current training metadata."""
    return _model_meta.copy()
