"""
Prefect orchestration flow for the Margin Intelligence System.

Automates: ingest → validate → feature engineer → train → evaluate → register model
Triggerable manually or on a schedule.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import warnings
warnings.filterwarnings("ignore")

from prefect import flow, task
from prefect.logging import get_run_logger


@task(name="Clean Data", retries=1, retry_delay_seconds=30)
def task_clean_data():
    """Ingest and clean the raw data."""
    logger = get_run_logger()
    logger.info("Starting data cleaning...")
    
    from src.data.clean import run_pipeline
    df = run_pipeline()
    
    logger.info(f"Cleaned data: {len(df)} rows")
    return df


@task(name="Initialize Database")
def task_init_db():
    """Initialize DuckDB database."""
    logger = get_run_logger()
    logger.info("Initializing DuckDB...")
    
    from src.data.init_db import init_database
    init_database()
    
    logger.info("DuckDB initialized")


@task(name="Engineer Features")
def task_engineer_features(df):
    """Add engineered features."""
    logger = get_run_logger()
    logger.info("Engineering features...")
    
    from src.features.engineer import engineer_features
    df = engineer_features(df)
    
    logger.info(f"Features engineered: {df.shape[1]} columns")
    return df


@task(name="Run Causal Analysis")
def task_causal_analysis(df):
    """Run causal analysis."""
    logger = get_run_logger()
    logger.info("Running causal analysis...")
    
    from src.models.causal import run_causal_pipeline
    results = run_causal_pipeline(df)
    
    logger.info(f"Causal analysis complete: R²={results['regression']['r_squared']:.4f}")
    return results


@task(name="Train Classifier", retries=1, retry_delay_seconds=60)
def task_train_classifier(df):
    """Train the loss classifier with MLflow tracking."""
    logger = get_run_logger()
    logger.info("Training classifier...")
    
    import mlflow
    mlflow.set_experiment("loss_classifier")
    
    with mlflow.start_run() as run:
        from src.models.classifier import train_classifier
        artifacts = train_classifier(df)
        
        # Log metrics to MLflow
        mlflow.log_metrics(artifacts["metrics"])
        mlflow.log_param("model_type", artifacts.get("model_name", "XGBoost"))
        mlflow.log_param("feature_count", len(artifacts.get("feature_names", [])))
        
        # Log the model
        mlflow.sklearn.log_model(artifacts["model"], "model")
        
        # Register in model registry
        mlflow.register_model(
            f"runs:/{run.info.run_id}/model",
            "loss_classifier"
        )
        
        logger.info(f"Classifier trained: ROC-AUC={artifacts['metrics']['roc_auc']:.4f}")
    
    return artifacts


@task(name="Run Forecasting", retries=1, retry_delay_seconds=60)
def task_run_forecast(df):
    """Run forecasting comparison."""
    logger = get_run_logger()
    logger.info("Running forecasting...")
    
    import mlflow
    mlflow.set_experiment("forecasting")
    
    with mlflow.start_run():
        from src.models.forecast import run_forecasting_pipeline
        results = run_forecasting_pipeline(df)
        
        mlflow.log_metric("prophet_mape", results["prophet"]["metrics"]["mape"])
        mlflow.log_metric("lstm_mape", results["lstm"]["metrics"]["mape"])
        mlflow.log_param("better_model", results["better_model"])
        mlflow.log_param("training_months", results["training_months"])
    
    logger.info(f"Forecasting complete: best={results['better_model']}")
    return results


@task(name="Run Optimization")
def task_optimize():
    """Run discount optimization."""
    logger = get_run_logger()
    logger.info("Running optimization...")
    
    from src.models.optimizer import batch_optimize
    recs = batch_optimize()
    
    logger.info(f"Optimization complete: {len(recs)} recommendations")


@task(name="Run SQL Validation")
def task_sql_validation():
    """Run SQL validation queries."""
    logger = get_run_logger()
    logger.info("Running SQL validation...")
    
    from src.data.init_db import get_connection
    sql_dir = Path("sql")
    for sql_file in sorted(sql_dir.glob("*.sql")):
        con = get_connection()
        try:
            result = con.execute(open(sql_file).read()).fetchdf()
            logger.info(f"  {sql_file.name}: {len(result)} rows")
        except Exception as e:
            logger.error(f"  {sql_file.name}: FAILED - {e}")
            raise  # Fail loudly per FR-1.3
        con.close()
    
    logger.info("SQL validation complete")


@task(name="Validate Data")
def task_validate():
    """Run data validation tests."""
    logger = get_run_logger()
    import subprocess
    
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_data_validation.py", "-v"],
        capture_output=True, text=True, timeout=120
    )
    
    logger.info(result.stdout)
    
    if result.returncode != 0:
        logger.error(f"Validation FAILED: {result.stderr}")
        raise RuntimeError("Data validation failed — pipeline aborted")
    
    logger.info("All data validation tests passed")


@flow(name="Margin Intelligence Full Pipeline")
def full_pipeline():
    """Orchestrate the complete MLOps pipeline end-to-end."""
    logger = get_run_logger()
    logger.info("=" * 60)
    logger.info("  STARTING FULL PIPELINE")
    logger.info("=" * 60)
    
    # Phase 1: Data
    df = task_clean_data()
    task_init_db()
    df = task_engineer_features(df)
    
    # Phase 2: Analysis
    task_causal_analysis(df)
    
    # Phase 3: Training
    task_train_classifier(df)
    task_run_forecast(df)
    task_optimize()
    
    # Phase 4: Validation
    task_sql_validation()
    task_validate()
    
    logger.info("=" * 60)
    logger.info("  PIPELINE COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    # Run the flow
    full_pipeline()
