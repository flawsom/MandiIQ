"""
Loss classifier for the Superstore Margin Intelligence System.

Predicts whether an order line will be unprofitable (profit < 0)
using pre-approval features only. Includes SHAP explainability.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import joblib
import json
import warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import train_test_split, ParameterGrid
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    classification_report,
    roc_auc_score,
    precision_recall_fscore_support,
    confusion_matrix,
    roc_curve,
)
from sklearn.utils.class_weight import compute_class_weight
import xgboost as xgb
import shap

# MLflow for experiment tracking
try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False
    print("  MLflow not installed. Skipping experiment tracking.")


def prepare_features(df, feature_cols=None):
    """Prepare features for modeling: encode categoricals, scale numeric."""
    if feature_cols is None:
        feature_cols = [
            "category", "sub_category", "region", "segment",
            "discount", "quantity", "ship_mode", "shipping_delay",
        ]

    df = df.copy()

    # Define categorical and numeric columns
    cat_cols = ["category", "sub_category", "region", "segment", "ship_mode"]
    num_cols = ["discount", "quantity", "shipping_delay"]

    # Encode categoricals
    encoders = {}
    for col in cat_cols:
        if col in df.columns:
            le = LabelEncoder()
            df[f"{col}_encoded"] = le.fit_transform(df[col].astype(str))
            encoders[col] = le

    # Scale numeric
    scaler = StandardScaler()
    available_num = [c for c in num_cols if c in df.columns]
    df["num_scaled"] = scaler.fit_transform(df[available_num]).tolist()

    # Build feature matrix
    encoded_cols = [f"{c}_encoded" for c in cat_cols if c in df.columns]
    X = df[encoded_cols + available_num].copy()

    return X, encoders, scaler, encoded_cols, available_num


def run_hyperparameter_sweep(X_train, y_train, X_test, y_test, weights):
    """
    Run a hyperparameter sweep for XGBoost, logged to MLflow.
    Demonstrates experiment tracking with model comparison.
    """
    scale_pos_weight = weights[0] / weights[1]
    
    # Small grid sweep for demonstration
    param_grid = {
        "n_estimators": [100, 200],
        "max_depth": [3, 5, 7],
        "learning_rate": [0.05, 0.1],
    }
    
    print("\n  Running hyperparameter sweep (6 combinations)...")
    best_auc = 0
    best_params = None
    best_model = None
    
    for params in ParameterGrid(param_grid):
        model = xgb.XGBClassifier(
            **params,
            scale_pos_weight=scale_pos_weight,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric="auc",
            use_label_encoder=False,
        )
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        probs = model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, probs)
        
        print(f"    n_est={params['n_estimators']:>3} depth={params['max_depth']} lr={params['learning_rate']} -> AUC={auc:.4f}")
        
        # Log to MLflow
        if MLFLOW_AVAILABLE and mlflow.active_run():
            mlflow.log_params(params)
            mlflow.log_metric("roc_auc", auc)
        
        if auc > best_auc:
            best_auc = auc
            best_params = params
            best_model = model
    
    print(f"  Best params: {best_params} (AUC={best_auc:.4f})")
    return best_model, best_params, best_auc


def train_classifier(
    df: pd.DataFrame,
    model_dir: str = "models",
    test_size: float = 0.2,
    random_state: int = 42,
    use_mlflow: bool = True,
    run_hyperopt: bool = True,
) -> dict:
    """
    Train loss classifier (XGBoost) with SHAP analysis.
    Returns metrics and model artifacts.
    """
    print("=== Training Loss Classifier ===")
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    
    # Start MLflow run
    if use_mlflow and MLFLOW_AVAILABLE:
        mlflow.set_experiment("loss_classifier")
        mlflow.start_run(run_name="train_classifier")
        print("  MLflow tracking enabled")

    # Prepare features
    X, encoders, scaler, cat_encoded, num_cols = prepare_features(df)
    y = df["is_loss"].values

    print(f"  Features: {list(X.columns)}")
    print(f"  Target distribution: {np.bincount(y)} ({y.mean()*100:.1f}% positive)")

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )
    # Save test indices for later reference
    _, test_idx = train_test_split(
        np.arange(len(df)), test_size=test_size, random_state=random_state, stratify=y
    )
    y_test_index = test_idx

    # Compute class weights
    classes = np.array([0, 1])
    weights = compute_class_weight("balanced", classes=classes, y=y_train)
    class_weight_dict = {0: weights[0], 1: weights[1]}
    print(f"  Class weights: {class_weight_dict}")
    
    if MLFLOW_AVAILABLE and mlflow.active_run():
        mlflow.log_params({
            "class_weight_0": float(weights[0]),
            "class_weight_1": float(weights[1]),
            "test_size": test_size,
            "n_train": len(X_train),
            "n_test": len(X_test),
        })

    # --- Hyperparameter sweep ---
    if run_hyperopt:
        best_xgb, best_params, best_xgb_auc = run_hyperparameter_sweep(
            X_train, y_train, X_test, y_test, weights
        )
        xgb_model = best_xgb
    else:
        # Train default XGBoost
        print("\n  Training XGBoost (default params)...")
        scale_pos_weight = weights[0] / weights[1]
        xgb_model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.1,
            scale_pos_weight=scale_pos_weight,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=random_state,
            eval_metric="auc",
            use_label_encoder=False,
        )
        xgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        best_params = {"n_estimators": 200, "max_depth": 5, "learning_rate": 0.1}

    xgb_probs = xgb_model.predict_proba(X_test)[:, 1]
    xgb_auc = roc_auc_score(y_test, xgb_probs)
    xgb_pred = xgb_model.predict(X_test)
    xgb_prec, xgb_rec, xgb_f1, _ = precision_recall_fscore_support(
        y_test, xgb_pred, average="binary"
    )
    print(f"  XGB ROC-AUC: {xgb_auc:.4f}, F1: {xgb_f1:.4f}, Prec: {xgb_prec:.4f}, Rec: {xgb_rec:.4f}")

    # --- Model 1: Logistic Regression (baseline) ---
    print("\n  Training Logistic Regression...")
    lr = LogisticRegression(
        class_weight=class_weight_dict,
        max_iter=1000,
        random_state=random_state,
    )
    lr.fit(X_train, y_train)
    lr_probs = lr.predict_proba(X_test)[:, 1]
    lr_auc = roc_auc_score(y_test, lr_probs)
    lr_pred = lr.predict(X_test)
    lr_prec, lr_rec, lr_f1, _ = precision_recall_fscore_support(
        y_test, lr_pred, average="binary"
    )
    print(f"  LR ROC-AUC: {lr_auc:.4f}, F1: {lr_f1:.4f}, Prec: {lr_prec:.4f}, Rec: {lr_rec:.4f}")
    
    # Log to MLflow
    if MLFLOW_AVAILABLE and mlflow.active_run():
        mlflow.log_metrics({
            "lr_roc_auc": float(lr_auc),
            "xgb_roc_auc": float(xgb_auc),
            "xgb_f1": float(xgb_f1),
            "xgb_precision": float(xgb_prec),
            "xgb_recall": float(xgb_rec),
        })
        mlflow.log_params(best_params)
        mlflow.set_tag("model_type", "XGBoost")

    # Pick the best model (by AUC)
    if xgb_auc >= lr_auc:
        best_model = xgb_model
        best_name = "XGBoost"
        best_auc = xgb_auc
        best_pred = xgb_pred
        best_probs = xgb_probs
    else:
        best_model = lr
        best_name = "LogisticRegression"
        best_auc = lr_auc
        best_pred = lr_pred
        best_probs = lr_probs

    print(f"\n  Best model: {best_name} (ROC-AUC: {best_auc:.4f})")
    
    if MLFLOW_AVAILABLE and mlflow.active_run():
        mlflow.set_tag("best_model", best_name)
        mlflow.log_metric("best_roc_auc", float(best_auc))

    # --- SHAP Analysis ---
    print("\n  Computing SHAP values...")
    if best_name == "XGBoost":
        explainer = shap.TreeExplainer(best_model)
    else:
        explainer = shap.LinearExplainer(best_model, X_train)

    shap_values = explainer.shap_values(X_test)

    # Global SHAP summary
    shap_importance = np.abs(shap_values).mean(axis=0)
    feature_importance = pd.DataFrame({
        "feature": list(X.columns),
        "importance": shap_importance,
    }).sort_values("importance", ascending=False)

    print(f"\n  Top 5 SHAP features:")
    for _, row in feature_importance.head(5).iterrows():
        print(f"    {row['feature']}: {row['importance']:.4f}")

    # --- Save artifacts ---
    artifacts = {
        "model": best_model,
        "encoders": encoders,
        "scaler": scaler,
        "cat_encoded": cat_encoded,
        "num_cols": num_cols,
        "feature_names": list(X.columns),
        "model_name": best_name,
        "metrics": {
            "roc_auc": float(best_auc),
            "precision": float(xgb_prec if best_name == "XGBoost" else lr_prec),
            "recall": float(xgb_rec if best_name == "XGBoost" else lr_rec),
            "f1": float(xgb_f1 if best_name == "XGBoost" else lr_f1),
            "lr_roc_auc": float(lr_auc),
            "xgb_roc_auc": float(xgb_auc),
        },
        "class_weight_dict": class_weight_dict,
        "test_size": test_size,
        "feature_importance": feature_importance.to_dict("records"),
    }

    # Save model
    model_path = Path(model_dir) / "loss_classifier.pkl"
    joblib.dump(artifacts, model_path)
    print(f"\n  Model saved to {model_path}")
    
    # Log model to MLflow registry
    if use_mlflow and MLFLOW_AVAILABLE and mlflow.active_run():
        mlflow.log_artifact(str(model_path))
        try:
            mlflow.register_model(
                f"runs:/{mlflow.active_run().info.run_id}/artifacts/loss_classifier.pkl",
                "LossClassifier"
            )
            print("  Model registered in MLflow Model Registry as 'LossClassifier'")
        except Exception as e:
            print(f"  MLflow registry logging skipped: {e}")
        mlflow.end_run()

    # Also save SHAP values for dashboard
    shap_dict = {
        "shap_values": shap_values.tolist(),
        "X_test": X_test.values.tolist(),
        "feature_names": list(X.columns),
        "base_value": float(explainer.expected_value) if hasattr(explainer, 'expected_value') else 0.0,
    }
    with open(Path(model_dir) / "shap_values.json", "w") as f:
        json.dump(shap_dict, f)

    # Save test data reference
    test_indices = y_test_index
    test_df = df.iloc[test_indices].copy()
    test_df["predicted_loss"] = best_pred
    test_df["loss_probability"] = best_probs
    test_df.to_csv(Path(model_dir) / "test_predictions.csv", index=False)

    # Classification report
    print(f"\n  Classification Report ({best_name}):")
    print(classification_report(y_test, best_pred, target_names=["Profitable", "Loss"]))

    return artifacts


def load_classifier(model_dir: str = "models") -> dict:
    """Load trained classifier artifacts."""
    model_path = Path(model_dir) / "loss_classifier.pkl"
    if not model_path.exists():
        raise FileNotFoundError(f"No trained model found at {model_path}")
    return joblib.load(model_path)


def predict_loss(
    features: dict,
    artifacts: dict,
) -> dict:
    """
    Predict loss probability for a single order configuration.
    
    Args:
        features: dict with keys like category, sub_category, region, segment,
                  discount, quantity, ship_mode, shipping_delay
        artifacts: loaded model artifacts dict
    """
    model = artifacts["model"]
    encoders = artifacts["encoders"]
    scaler = artifacts["scaler"]
    cat_encoded = artifacts["cat_encoded"]
    num_cols = artifacts["num_cols"]
    feature_names = artifacts["feature_names"]

    # Build input vector
    row = {}
    for col in ["category", "sub_category", "region", "segment", "ship_mode"]:
        if col in encoders:
            val = features.get(col, "Unknown")
            try:
                row[f"{col}_encoded"] = encoders[col].transform([str(val)])[0]
            except ValueError:
                row[f"{col}_encoded"] = 0  # Unknown category

    for col in num_cols:
        row[col] = features.get(col, 0)

    # Build DataFrame in correct order
    input_df = pd.DataFrame([{fn: row.get(fn, 0) for fn in feature_names}])

    # Predict
    proba = model.predict_proba(input_df)[0, 1]
    pred = model.predict(input_df)[0]

    # SHAP explanation
    if isinstance(model, xgb.XGBClassifier):
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(input_df)
    else:
        explainer = shap.LinearExplainer(model, np.zeros((1, len(feature_names))))
        shap_values = explainer.shap_values(input_df)

    # Get top contributing features
    shap_dict = {}
    for i, fn in enumerate(feature_names):
        shap_dict[fn] = float(shap_values[0, i])

    top_features = sorted(shap_dict.items(), key=lambda x: abs(x[1]), reverse=True)[:3]

    return {
        "loss_probability": float(proba),
        "prediction": int(pred),
        "top_3_shap": [(feat, round(val, 4)) for feat, val in top_features],
    }


if __name__ == "__main__":
    from src.features.engineer import engineer_features
    df = pd.read_csv("data/processed/superstore_clean.csv")
    df = engineer_features(df)
    train_classifier(df)
