"""
model.py — Layer 3: Prediction Model
======================================
Ensemble: Dixon-Coles (statistical) + Monte Carlo (10k simulations) + XGBoost (contextual).
Retrains after every match. MLflow tracks every experiment. DVC versions every model file.
Hard rules: only produces football prediction output. No off-topic generation.
"""

import os
import json
import pickle
import hashlib
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field
from pathlib import Path

import mlflow
import mlflow.xgboost
import polars as pl
from scipy.optimize import minimize
from scipy.stats import poisson
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, log_loss
from sklearn.preprocessing import LabelEncoder
from loguru import logger

# MLflow: optional — only used during retrain, not at startup
# Install locally via requirements-dev.txt, not deployed to Render
MLFLOW_AVAILABLE = False
try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    logger.warning("MLflow not installed — experiment tracking disabled")

# ─────────────────────────────────────────────
# HARD RULE: MODEL SCOPE GUARD
# This model ONLY outputs football match predictions.
# Any attempt to use it for other purposes is rejected.
# ─────────────────────────────────────────────

ALLOWED_OUTPUT_TYPES = {"win_probability", "scoreline_distribution", "confidence_range", "feature_importance"}

def _guard_output_type(output_type: str):
    if output_type not in ALLOWED_OUTPUT_TYPES:
        raise ValueError(
            f"SCOPE VIOLATION: Model only produces {ALLOWED_OUTPUT_TYPES}. "
            f"Requested: '{output_type}' — rejected."
        )


# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────

MODELS_DIR = Path("backend/models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = Path("backend/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# TEAM RATINGS (Dixon-Coles)
# ─────────────────────────────────────────────

@dataclass
class TeamRatings:
    attack: float = 1.0
    defence: float = 1.0
    home_advantage: float = 0.0   # used if neutral venue (WC = no home advantage in group stage)


@dataclass
class DixonColesParams:
    """
    Dixon-Coles model parameters.
    team_ratings: dict of team_name → TeamRatings
    rho: low-scoring correction parameter (typically -0.1 to -0.15)
    """
    team_ratings: dict[str, TeamRatings] = field(default_factory=dict)
    rho: float = -0.13             # Dixon-Coles correction for 0-0, 1-0, 0-1, 1-1
    home_advantage: float = 1.1    # general home advantage multiplier (minimal in WC)
    fitted_on_matches: int = 0
    last_updated: Optional[str] = None


def _tau(x: int, y: int, lambda_: float, mu_: float, rho: float) -> float:
    """
    Dixon-Coles correction factor for low-scoring results.
    Corrects the underestimation of 0-0, 1-0, 0-1, 1-1 by pure Poisson.
    """
    if x == 0 and y == 0:
        return 1 - lambda_ * mu_ * rho
    elif x == 1 and y == 0:
        return 1 + mu_ * rho
    elif x == 0 and y == 1:
        return 1 + lambda_ * rho
    elif x == 1 and y == 1:
        return 1 - rho
    return 1.0


def dixon_coles_log_likelihood(params_flat: np.ndarray, matches: list[dict], team_index: dict) -> float:
    """
    Negative log-likelihood for Dixon-Coles model.
    Minimise this to find optimal attack/defence ratings.
    matches: list of {home_team, away_team, home_goals, away_goals}
    """
    n_teams = len(team_index)
    attacks = params_flat[:n_teams]
    defences = params_flat[n_teams:2*n_teams]
    rho = params_flat[2*n_teams]

    total_ll = 0.0
    for m in matches:
        hi = team_index.get(m["home_team"])
        ai = team_index.get(m["away_team"])
        if hi is None or ai is None:
            continue

        lambda_ = np.exp(attacks[hi] - defences[ai])  # home expected goals
        mu_ = np.exp(attacks[ai] - defences[hi])       # away expected goals

        hg = m["home_goals"]
        ag = m["away_goals"]

        # Dixon-Coles corrected probability
        tau = _tau(hg, ag, lambda_, mu_, rho)
        if tau <= 0:
            tau = 1e-10

        ll = (
            np.log(tau)
            + poisson.logpmf(hg, lambda_)
            + poisson.logpmf(ag, mu_)
        )
        total_ll += ll

    return -total_ll   # negative for minimisation


def fit_dixon_coles(matches: list[dict]) -> DixonColesParams:
    """
    Fit Dixon-Coles model on historical match data.
    matches: list of {home_team, away_team, home_goals, away_goals}
    """
    teams = sorted(set(
        [m["home_team"] for m in matches] + [m["away_team"] for m in matches]
    ))
    team_index = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    # Initial params: all attacks = 0, all defences = 0, rho = -0.1
    x0 = np.zeros(2 * n + 1)
    x0[2*n] = -0.1  # rho

    # Constraints: sum of attacks = 0 (identifiability)
    constraints = [{"type": "eq", "fun": lambda p: np.sum(p[:n])}]

    result = minimize(
        fun=dixon_coles_log_likelihood,
        x0=x0,
        args=(matches, team_index),
        method="L-BFGS-B",
        constraints=constraints,
        options={"maxiter": 500, "ftol": 1e-8},
    )

    params = DixonColesParams()
    for team, idx in team_index.items():
        params.team_ratings[team] = TeamRatings(
            attack=float(np.exp(result.x[idx])),
            defence=float(np.exp(result.x[n + idx])),
        )
    params.rho = float(result.x[2*n])
    params.fitted_on_matches = len(matches)
    params.last_updated = datetime.utcnow().isoformat()

    logger.info(
        f"[Model] Dixon-Coles fitted on {len(matches)} matches. "
        f"rho={params.rho:.4f}. Teams: {len(teams)}"
    )
    return params


# ─────────────────────────────────────────────
# MONTE CARLO SIMULATION
# ─────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    scoreline_distribution: dict[str, float]   # "1-0": 0.12, "2-1": 0.08, etc.
    expected_goals_home: float
    expected_goals_away: float
    simulations_run: int = 10_000


def monte_carlo_simulate(
    lambda_home: float,
    mu_away: float,
    n_simulations: int = 10_000,
    rho: float = -0.13,
    in_extra_time: bool = False,
) -> MonteCarloResult:
    """
    Simulate match n_simulations times using Poisson goals.
    Returns full scoreline distribution, not just win/draw/loss.
    """
    _guard_output_type("scoreline_distribution")

    rng = np.random.default_rng(seed=42)

    # Scale lambda/mu for extra time (30 min instead of 90)
    if in_extra_time:
        lambda_home = lambda_home * (30/90)
        mu_away = mu_away * (30/90)

    home_goals = rng.poisson(lambda_home, n_simulations)
    away_goals = rng.poisson(mu_away, n_simulations)

    # Dixon-Coles correction (approximate — resample low-score cases)
    for i in range(n_simulations):
        hg, ag = home_goals[i], away_goals[i]
        if hg <= 1 and ag <= 1:
            correction = _tau(hg, ag, lambda_home, mu_away, rho)
            if correction < 1.0 and rng.random() > correction:
                # Resample this match without DC correction
                home_goals[i] = rng.poisson(lambda_home)
                away_goals[i] = rng.poisson(mu_away)

    home_wins = np.sum(home_goals > away_goals)
    draws = np.sum(home_goals == away_goals)
    away_wins = np.sum(home_goals < away_goals)

    # Scoreline distribution (top 20 most likely)
    scoreline_counts: dict[str, int] = {}
    for hg, ag in zip(home_goals, away_goals):
        key = f"{hg}-{ag}"
        scoreline_counts[key] = scoreline_counts.get(key, 0) + 1

    scoreline_dist = {
        k: round(v / n_simulations, 4)
        for k, v in sorted(scoreline_counts.items(), key=lambda x: -x[1])[:20]
    }

    return MonteCarloResult(
        home_win_prob=float(home_wins / n_simulations),
        draw_prob=float(draws / n_simulations),
        away_win_prob=float(away_wins / n_simulations),
        scoreline_distribution=scoreline_dist,
        expected_goals_home=float(np.mean(home_goals)),
        expected_goals_away=float(np.mean(away_goals)),
        simulations_run=n_simulations,
    )


# ─────────────────────────────────────────────
# XGBOOST CONTEXTUAL MODEL
# ─────────────────────────────────────────────

XGBOOST_FEATURES = [
    "home_rest_days",
    "away_rest_days",
    "home_travel_km",
    "away_travel_km",
    "home_squad_avg_age",
    "away_squad_avg_age",
    "home_tournament_matches_played",
    "away_tournament_matches_played",
    "home_historical_wc_wins",
    "away_historical_wc_wins",
    "home_xg_rolling_5",
    "away_xg_rolling_5",
    "home_tournament_form",           # points in last 3 WC group matches (0-9)
    "away_tournament_form",
    "home_injury_count",              # flagged injures from news
    "away_injury_count",
    "home_dc_attack_rating",          # from Dixon-Coles
    "away_dc_attack_rating",
    "home_dc_defence_rating",
    "away_dc_defence_rating",
    "dc_home_win_prob",               # Dixon-Coles baseline
    "dc_draw_prob",
    "dc_away_win_prob",
    "match_importance",               # 1=group stage, 2=R32, 3=R16, 4=QF, 5=SF, 6=Final
]


def build_feature_vector(match_data: dict, dc_params: DixonColesParams) -> np.ndarray:
    """
    Build feature vector for XGBoost from match metadata + Dixon-Coles ratings.
    Returns shape (1, 24) for single match prediction.
    """
    home = match_data["home_team"]
    away = match_data["away_team"]

    home_r = dc_params.team_ratings.get(home, TeamRatings())
    away_r = dc_params.team_ratings.get(away, TeamRatings())

    # Expected goals from Dixon-Coles
    lambda_h = home_r.attack / away_r.defence
    mu_a = away_r.attack / home_r.defence

    # Quick DC win probabilities (for XGBoost as feature)
    dc_result = monte_carlo_simulate(lambda_h, mu_a, n_simulations=1000)

    features = np.array([[
        match_data.get("home_rest_days", 3),
        match_data.get("away_rest_days", 3),
        match_data.get("home_travel_km", 0),
        match_data.get("away_travel_km", 0),
        match_data.get("home_squad_avg_age", 26.0),
        match_data.get("away_squad_avg_age", 26.0),
        match_data.get("home_tournament_matches_played", 0),
        match_data.get("away_tournament_matches_played", 0),
        match_data.get("home_historical_wc_wins", 0),
        match_data.get("away_historical_wc_wins", 0),
        match_data.get("home_xg_rolling_5", lambda_h),
        match_data.get("away_xg_rolling_5", mu_a),
        match_data.get("home_tournament_form", 4),
        match_data.get("away_tournament_form", 4),
        match_data.get("home_injury_count", 0),
        match_data.get("away_injury_count", 0),
        home_r.attack,
        away_r.attack,
        home_r.defence,
        away_r.defence,
        dc_result.home_win_prob,
        dc_result.draw_prob,
        dc_result.away_win_prob,
        match_data.get("match_importance", 1),
    ]])
    return features


# ─────────────────────────────────────────────
# ENSEMBLE PREDICTION
# ─────────────────────────────────────────────

@dataclass
class EnsemblePrediction:
    match_id: str
    home_team: str
    away_team: str
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    confidence_range_low: float      # shown as range, not single number
    confidence_range_high: float
    predicted_winner: str            # "home", "draw", "away"
    predicted_scoreline: str         # most likely, e.g. "2-1"
    predicted_first_scorer: Optional[str]
    scoreline_distribution: dict[str, float]
    expected_goals_home: float
    expected_goals_away: float
    model_version: int
    training_matches_seen: int
    dc_contribution: float = 0.4
    mc_contribution: float = 0.35
    xgb_contribution: float = 0.25
    generated_at: str = ""
    confidence_shift_from_kickoff: float = 0.0   # set during live match


def _clamp(value: float, min_val: float = 0.02, max_val: float = 0.98) -> float:
    """Never show 0% or 100%. Minimum 2%, maximum 98%."""
    return max(min_val, min(max_val, value))


def ensemble_predict(
    match_data: dict,
    dc_params: DixonColesParams,
    xgb_model: Optional[XGBClassifier],
    model_version: int,
) -> EnsemblePrediction:
    """
    Combine Dixon-Coles + Monte Carlo + XGBoost into ensemble prediction.
    Weights: DC 40% + MC 35% + XGB 25% (XGB weight grows as training data increases).
    """
    _guard_output_type("win_probability")

    home = match_data["home_team"]
    away = match_data["away_team"]

    home_r = dc_params.team_ratings.get(home, TeamRatings())
    away_r = dc_params.team_ratings.get(away, TeamRatings())

    # Expected goals
    lambda_h = max(0.1, home_r.attack / away_r.defence)
    mu_a = max(0.1, away_r.attack / home_r.defence)

    # Dixon-Coles
    mc_result = monte_carlo_simulate(lambda_h, mu_a, n_simulations=10_000, rho=dc_params.rho)

    # XGBoost contextual adjustment
    xgb_home_win = mc_result.home_win_prob
    xgb_draw = mc_result.draw_prob
    xgb_away_win = mc_result.away_win_prob

    if xgb_model is not None:
        try:
            features = build_feature_vector(match_data, dc_params)
            probs = xgb_model.predict_proba(features)[0]  # [home_win, draw, away_win]
            xgb_home_win = float(probs[2])  # label 2 = home win (after LabelEncoder)
            xgb_draw = float(probs[0])      # label 0 = draw
            xgb_away_win = float(probs[1])  # label 1 = away win
        except Exception as e:
            logger.warning(f"[Model] XGBoost prediction failed, using MC only: {e}")

    # Scale XGBoost weight with training data (more data = more trust)
    training_matches = dc_params.fitted_on_matches
    xgb_weight = min(0.25, 0.05 + (training_matches / 104) * 0.20)
    dc_weight = 0.40
    mc_weight = 1.0 - dc_weight - xgb_weight

    # Ensemble (DC and MC share similar base, so we use MC as the combined DC+MC signal)
    raw_home = dc_weight * mc_result.home_win_prob + mc_weight * mc_result.home_win_prob + xgb_weight * xgb_home_win
    raw_draw = dc_weight * mc_result.draw_prob + mc_weight * mc_result.draw_prob + xgb_weight * xgb_draw
    raw_away = dc_weight * mc_result.away_win_prob + mc_weight * mc_result.away_win_prob + xgb_weight * xgb_away_win

    # Normalise
    total = raw_home + raw_draw + raw_away
    home_win_prob = _clamp(raw_home / total)
    draw_prob = _clamp(raw_draw / total)
    away_win_prob = _clamp(raw_away / total)

    # Re-normalise after clamping
    total2 = home_win_prob + draw_prob + away_win_prob
    home_win_prob /= total2
    draw_prob /= total2
    away_win_prob /= total2

    # Confidence range (±4% band to reflect model uncertainty)
    max_prob = max(home_win_prob, draw_prob, away_win_prob)
    conf_low = _clamp(max_prob - 0.04)
    conf_high = _clamp(max_prob + 0.04)

    # Predicted winner
    if home_win_prob >= draw_prob and home_win_prob >= away_win_prob:
        predicted_winner = "home"
    elif away_win_prob >= draw_prob:
        predicted_winner = "away"
    else:
        predicted_winner = "draw"

    # Most likely scoreline from MC distribution
    predicted_scoreline = max(mc_result.scoreline_distribution, key=mc_result.scoreline_distribution.get, default="1-1")

    return EnsemblePrediction(
        match_id=match_data.get("match_id", "unknown"),
        home_team=home,
        away_team=away,
        home_win_prob=round(home_win_prob, 4),
        draw_prob=round(draw_prob, 4),
        away_win_prob=round(away_win_prob, 4),
        confidence_range_low=round(conf_low, 4),
        confidence_range_high=round(conf_high, 4),
        predicted_winner=predicted_winner,
        predicted_scoreline=predicted_scoreline,
        predicted_first_scorer=match_data.get("predicted_first_scorer"),
        scoreline_distribution=mc_result.scoreline_distribution,
        expected_goals_home=round(mc_result.expected_goals_home, 2),
        expected_goals_away=round(mc_result.expected_goals_away, 2),
        model_version=model_version,
        training_matches_seen=dc_params.fitted_on_matches,
        dc_contribution=dc_weight,
        mc_contribution=mc_weight,
        xgb_contribution=xgb_weight,
        generated_at=datetime.utcnow().isoformat(),
    )


# ─────────────────────────────────────────────
# LIVE CONFIDENCE UPDATE (during match)
# ─────────────────────────────────────────────

def update_live_confidence(
    prediction: EnsemblePrediction,
    current_score: dict,
    current_minute: int,
    dc_params: DixonColesParams,
) -> EnsemblePrediction:
    """
    Update win probabilities based on current live score and minute.
    Uses remaining-time Monte Carlo + score state.
    Locked at 85 minutes — no updates after.
    """
    _guard_output_type("win_probability")

    if current_minute >= 85:
        return prediction  # Hard lock at 85 minutes

    home_score = current_score.get("home", 0)
    away_score = current_score.get("away", 0)

    # Remaining time factor
    minutes_remaining = max(0, 90 - current_minute)
    time_factor = minutes_remaining / 90.0

    home_r = dc_params.team_ratings.get(prediction.home_team, TeamRatings())
    away_r = dc_params.team_ratings.get(prediction.away_team, TeamRatings())

    lambda_remaining = max(0.05, (home_r.attack / away_r.defence) * time_factor)
    mu_remaining = max(0.05, (away_r.attack / home_r.defence) * time_factor)

    # Simulate remaining time from current state
    mc = monte_carlo_simulate(lambda_remaining, mu_remaining, n_simulations=5_000)

    # Adjust based on current score
    score_diff = home_score - away_score
    if score_diff > 0:
        # Home leading — adjust win prob upward
        leading_bonus = min(0.15, score_diff * 0.1 * (1 - time_factor))
    elif score_diff < 0:
        leading_bonus = max(-0.15, score_diff * 0.1 * (1 - time_factor))
    else:
        leading_bonus = 0.0

    raw_home = _clamp(mc.home_win_prob + leading_bonus)
    raw_draw = _clamp(mc.draw_prob)
    raw_away = _clamp(mc.away_win_prob - leading_bonus)

    total = raw_home + raw_draw + raw_away
    new_home = _clamp(raw_home / total)
    new_draw = _clamp(raw_draw / total)
    new_away = _clamp(raw_away / total)

    # Confidence shift from kickoff
    kickoff_home = prediction.home_win_prob
    shift = new_home - kickoff_home

    updated = EnsemblePrediction(**prediction.__dict__)
    updated.home_win_prob = round(new_home, 4)
    updated.draw_prob = round(new_draw, 4)
    updated.away_win_prob = round(new_away, 4)
    updated.confidence_shift_from_kickoff = round(shift, 4)
    updated.confidence_range_low = _clamp(max(new_home, new_draw, new_away) - 0.04)
    updated.confidence_range_high = _clamp(max(new_home, new_draw, new_away) + 0.04)

    return updated


# ─────────────────────────────────────────────
# XGBOOST TRAINING
# ─────────────────────────────────────────────

def train_xgboost(
    matches: list[dict],
    dc_params: DixonColesParams,
) -> tuple[XGBClassifier, dict]:
    """
    Train XGBoost classifier on historical match data + DC features.
    Returns (model, metrics_dict).
    """
    if len(matches) < 10:
        logger.warning(f"[Model] Only {len(matches)} training matches — XGBoost skipped (need ≥10)")
        return None, {}

    X_list, y_list = [], []
    for m in matches:
        try:
            features = build_feature_vector(m, dc_params)
            result = m.get("result")  # "home_win", "draw", "away_win"
            if result not in ("home_win", "draw", "away_win"):
                continue
            X_list.append(features[0])
            y_list.append(result)
        except Exception as e:
            logger.debug(f"[Model] Skipping match for training: {e}")
            continue

    if len(X_list) < 10:
        return None, {}

    X = np.array(X_list)
    le = LabelEncoder()
    y = le.fit_transform(y_list)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        use_label_encoder=False,
        eval_metric="mlogloss",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    y_pred = model.predict(X_test)
    y_prob = model.predict_proba(X_test)

    accuracy = float(accuracy_score(y_test, y_pred))
    logloss = float(log_loss(y_test, y_prob))

    # Feature importances
    fi = dict(zip(XGBOOST_FEATURES, model.feature_importances_.tolist()))

    metrics = {
        "accuracy": accuracy,
        "log_loss": logloss,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "feature_importances": fi,
        "classes": le.classes_.tolist(),
    }

    logger.info(f"[Model] XGBoost trained — accuracy={accuracy:.3f}, log_loss={logloss:.3f}")
    return model, metrics


# ─────────────────────────────────────────────
# MLFLOW EXPERIMENT TRACKING
# ─────────────────────────────────────────────

def setup_mlflow():
    """Configure MLflow with SQLite backend."""
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "sqlite:///backend/mlflow.db")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("delta_wc2026")
    logger.info(f"[MLflow] Tracking URI: {tracking_uri}")


# ─────────────────────────────────────────────
# FULL RETRAIN PIPELINE
# ─────────────────────────────────────────────

@dataclass
class RetrainResult:
    deployed: bool
    model_version: int
    accuracy_before: float
    accuracy_after: float
    improvement_pct: float
    deploy_decision_reason: str
    training_duration_s: float
    mlflow_run_id: str
    model_path: Optional[str]


def retrain_model(
    all_matches: list[dict],       # all completed matches with results
    current_version: int,
    current_accuracy: float,
    tournament_phase: str,         # "group", "r32_r16", "qf_plus"
) -> RetrainResult:
    """
    Full retrain: Dixon-Coles → XGBoost → evaluate → deploy if threshold met.
    Logs everything to MLflow. Versions model file.
    tournament_phase controls deployment threshold.
    """
    import time as time_module
    start_t = time_module.time()

    # Deployment thresholds per phase
    thresholds = {
        "group":   0.02,   # +2% minimum improvement
        "r32_r16": 0.05,   # +5%
        "qf_plus": 0.08,   # +8%
    }
    threshold = thresholds.get(tournament_phase, 0.02)
    new_version = current_version + 1

    logger.info(f"[Retrain] Starting v{new_version} on {len(all_matches)} matches (phase: {tournament_phase})")

    # Backup current weights
    current_path = MODELS_DIR / f"model_v{current_version}.pkl"
    backup_path = MODELS_DIR / f"model_v{current_version}_backup.pkl"
    if current_path.exists():
        import shutil
        shutil.copy(current_path, backup_path)
        logger.info(f"[Retrain] Backed up v{current_version}")

    # 1. Fit Dixon-Coles on all completed matches
    completed = [m for m in all_matches if m.get("home_goals") is not None]
    dc_params = fit_dixon_coles(completed)

    # 2. Train XGBoost
    xgb_model, xgb_metrics = train_xgboost(completed, dc_params)
    accuracy_after = xgb_metrics.get("accuracy", current_accuracy)

    # 3. Evaluate improvement
    improvement = accuracy_after - current_accuracy
    improvement_pct = improvement * 100

    # 4. Deploy decision
    if improvement >= threshold:
        deployed = True
        reason = f"Improved {improvement_pct:.1f}% ≥ threshold {threshold*100:.0f}%"
    else:
        deployed = False
        reason = f"Improvement {improvement_pct:.1f}% < threshold {threshold*100:.0f}% — keeping v{current_version}"

    duration = time_module.time() - start_t

    # 5. Save model bundle (always save, even if not deployed — for DVC versioning)
    model_bundle = {
        "dc_params": dc_params,
        "xgb_model": xgb_model,
        "version": new_version,
        "trained_at": datetime.utcnow().isoformat(),
        "training_matches": len(completed),
        "accuracy": accuracy_after,
        "xgb_metrics": xgb_metrics,
    }
    new_model_path = MODELS_DIR / f"model_v{new_version}.pkl"
    with open(new_model_path, "wb") as f:
        pickle.dump(model_bundle, f)

    # 6. MLflow logging
    setup_mlflow()
    with mlflow.start_run(run_name=f"v{new_version}") as run:
        mlflow.log_param("model_version", new_version)
        mlflow.log_param("tournament_phase", tournament_phase)
        mlflow.log_param("training_matches", len(completed))
        mlflow.log_param("deploy_threshold", threshold)
        mlflow.log_param("deployed", deployed)

        mlflow.log_metric("accuracy_before", current_accuracy)
        mlflow.log_metric("accuracy_after", accuracy_after)
        mlflow.log_metric("improvement_pct", improvement_pct)
        mlflow.log_metric("training_duration_s", duration)
        mlflow.log_metric("dc_rho", dc_params.rho)

        if xgb_metrics.get("feature_importances"):
            for feat, imp in xgb_metrics["feature_importances"].items():
                mlflow.log_metric(f"fi_{feat}", imp)

        if xgb_model is not None:
            mlflow.xgboost.log_model(xgb_model, "xgboost_model")

        mlflow.log_artifact(str(new_model_path))
        run_id = run.info.run_id

    logger.info(
        f"[Retrain] v{new_version} complete: "
        f"accuracy {current_accuracy:.3f} → {accuracy_after:.3f} "
        f"({improvement_pct:+.1f}%) | deployed={deployed} | {duration:.1f}s"
    )

    return RetrainResult(
        deployed=deployed,
        model_version=new_version,
        accuracy_before=current_accuracy,
        accuracy_after=accuracy_after,
        improvement_pct=improvement_pct,
        deploy_decision_reason=reason,
        training_duration_s=duration,
        mlflow_run_id=run_id,
        model_path=str(new_model_path) if deployed else None,
    )


# ─────────────────────────────────────────────
# MODEL PRUNING (Render storage management)
# ─────────────────────────────────────────────

def prune_model_versions(current_version: int, tournament_phase: str):
    """
    Keep storage lean on Render free tier.
    Group stage: keep every 5th. R32/R16: every other. QF+: all. Always keep last 3.
    """
    all_model_files = sorted(MODELS_DIR.glob("model_v*.pkl"))
    versions = []
    for f in all_model_files:
        try:
            v = int(f.stem.replace("model_v", "").replace("_backup", ""))
            if "_backup" not in f.stem:
                versions.append((v, f))
        except ValueError:
            continue

    versions.sort(key=lambda x: x[0])
    always_keep = {v for v, _ in versions[-3:]}  # always keep last 3

    for v, path in versions:
        if v in always_keep:
            continue
        if tournament_phase == "group" and v % 5 != 0:
            path.unlink(missing_ok=True)
            logger.debug(f"[Prune] Removed model_v{v}.pkl (group stage)")
        elif tournament_phase == "r32_r16" and v % 2 != 0:
            path.unlink(missing_ok=True)
            logger.debug(f"[Prune] Removed model_v{v}.pkl (r32_r16)")
        # qf_plus: keep all


# ─────────────────────────────────────────────
# MODEL LOAD
# ─────────────────────────────────────────────

def load_model(version: Optional[int] = None) -> tuple[DixonColesParams, Optional[XGBClassifier], int]:
    """
    Load model bundle from disk.
    If version is None, loads the latest deployed version.
    Returns (dc_params, xgb_model, version).
    """
    if version is None:
        # Find latest version file
        files = sorted(MODELS_DIR.glob("model_v*.pkl"), key=lambda f: int(f.stem.replace("model_v", "")))
        backup_files = [f for f in files if "_backup" not in f.stem]
        if not backup_files:
            logger.warning("[Model] No model files found — using default Dixon-Coles params")
            return DixonColesParams(), None, 0
        latest = backup_files[-1]
    else:
        latest = MODELS_DIR / f"model_v{version}.pkl"

    try:
        with open(latest, "rb") as f:
            bundle = pickle.load(f)
        dc = bundle["dc_params"]
        xgb = bundle.get("xgb_model")
        ver = bundle.get("version", 0)
        logger.info(f"[Model] Loaded model v{ver} from {latest}")
        return dc, xgb, ver
    except Exception as e:
        logger.error(f"[Model] Failed to load model from {latest}: {e}")
        return DixonColesParams(), None, 0


# ─────────────────────────────────────────────
# CONFIDENCE DISPLAY FORMATTER
# ─────────────────────────────────────────────

def format_confidence_display(prediction: EnsemblePrediction) -> dict:
    """
    Format prediction for frontend display.
    Shows range not single number. Never 0% or 100%.
    """
    _guard_output_type("confidence_range")

    def pct_range(prob: float) -> str:
        low = max(2, round((prob - 0.04) * 100))
        high = min(98, round((prob + 0.04) * 100))
        return f"{low}-{high}%"

    return {
        "home_win": pct_range(prediction.home_win_prob),
        "draw": pct_range(prediction.draw_prob),
        "away_win": pct_range(prediction.away_win_prob),
        "predicted_scoreline": prediction.predicted_scoreline,
        "top_scorelines": [
            {"scoreline": k, "probability": f"{round(v*100, 1)}%"}
            for k, v in list(prediction.scoreline_distribution.items())[:5]
        ],
        "model_version": f"v{prediction.model_version}",
        "trained_on": f"Based on {prediction.training_matches_seen} matches",
        "confidence_shift": (
            f"{prediction.home_team} {'+' if prediction.confidence_shift_from_kickoff >= 0 else ''}"
            f"{round(prediction.confidence_shift_from_kickoff * 100, 1)}% since kickoff"
            if prediction.confidence_shift_from_kickoff != 0 else None
        ),
        "locked_at_85": False,
    }
