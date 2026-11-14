"""MLflow tracking for the training pipeline.

Every pipeline command becomes an MLflow run (parameters, metrics, the produced procedures
as artifacts), and every Claude call is traced automatically (inputs, outputs, tokens, latency).
Destination: the Databricks workspace when DATABRICKS_HOST is set, otherwise ./mlruns.
Set MLFLOW_DISABLE=1 to switch it off.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT", "/Shared/spanner-training")
_ready = False


class _NoMlflow:
    """Stands in for mlflow when tracking is disabled, so callers don't need ifs."""

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _setup():
    global _ready
    import mlflow

    if not _ready:
        if os.getenv("DATABRICKS_HOST"):
            mlflow.set_tracking_uri("databricks")
            mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT", EXPERIMENT))
        else:  # local fallback only; the real destination is Databricks
            os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
            mlflow.set_tracking_uri((ROOT / "mlruns").as_uri())
            mlflow.set_experiment("spanner-training")
        mlflow.anthropic.autolog()  # traces every Claude call
        _ready = True
    return mlflow


@contextlib.contextmanager
def run(name: str, params: dict | None = None):
    """with tracking.run("label-videos", {...}) as mlf: mlf.log_metric(...)"""
    if os.getenv("MLFLOW_DISABLE") == "1":
        yield _NoMlflow()
        return
    mlflow = _setup()
    with mlflow.start_run(run_name=name):
        if params:
            mlflow.log_params({k: (str(v)[:500]) for k, v in params.items()})
        yield mlflow
