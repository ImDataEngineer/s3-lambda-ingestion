"""Fixture pytest partagée : attente de LocalStack + apply Terraform + upload.

La session entière dépend de cette fixture. Si LocalStack n'est pas joignable
sur http://localhost:4566 (ou l'URL passée via `AWS_ENDPOINT_URL`), on FAIL
EXPLICITEMENT plutôt que de laisser pytest tomber sur des timeouts boto3
opaques 60s plus tard. C'est ce qui rend les messages d'échec lisibles.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import boto3
import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TERRAFORM_DIR = PROJECT_ROOT / "terraform"
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
FIXTURE_CSV = FIXTURES_DIR / "deliveries_2026-04-16.csv"
BUILD_DIR = PROJECT_ROOT / "build"

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
BUCKET = "sobral-deliveries"
TABLE = "delivery_events"
LAMBDA_FN = "sobral_ingest"

LOCALSTACK_READY_TIMEOUT_S = 60
DDB_POLL_TIMEOUT_S = 60
DDB_POLL_INTERVAL_S = 2


# ---------------------------------------------------------------------------
# LocalStack health gate
# ---------------------------------------------------------------------------


def _wait_for_localstack() -> None:
    """Poll /_localstack/health until the 3 services we need are 'available'."""
    deadline = time.time() + LOCALSTACK_READY_TIMEOUT_S
    last_err: str | None = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{ENDPOINT}/_localstack/health", timeout=2)
            if r.status_code == 200:
                services = r.json().get("services", {})
                required = ("s3", "lambda", "dynamodb")
                if all(services.get(s) in ("available", "running") for s in required):
                    return
                last_err = f"LocalStack joignable mais services pas prêts : {services}"
        except requests.RequestException as exc:  # noqa: PERF203
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1)
    pytest.fail(
        f"LocalStack injoignable sur {ENDPOINT} après {LOCALSTACK_READY_TIMEOUT_S}s.\n"
        f"Dernière erreur : {last_err}\n"
        "En local : lance `docker compose up -d localstack` puis attends que "
        "le healthcheck passe au vert (`docker compose ps`).\n"
        "En CI : la step `Start LocalStack via docker compose` du workflow doit "
        "passer — vérifie les logs LocalStack dans la section 'Dump LocalStack "
        "logs on failure'."
    )


# ---------------------------------------------------------------------------
# Terraform driver
# ---------------------------------------------------------------------------


def _run_terraform(cmd: list[str], allow_fail: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        cwd=TERRAFORM_DIR,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "TF_VAR_localstack_endpoint": ENDPOINT,
        },
    )
    if proc.returncode != 0 and not allow_fail:
        pytest.fail(
            f"Commande Terraform en échec : {' '.join(cmd)}\n"
            f"--- STDOUT ---\n{proc.stdout}\n"
            f"--- STDERR ---\n{proc.stderr}\n"
            "Indice : la cause la plus fréquente est un bloc `endpoints` mal "
            "configuré dans provider.tf (doit pointer sur LocalStack), ou "
            "une resource déclarée incomplètement (cf. les TODOs dans main.tf)."
        )
    return proc


def _clean_terraform_state() -> None:
    """Supprime tout vestige d'un run précédent — state EPHÉMÈRE par design.

    On NE veut PAS de state partagé entre runs CI : la CI doit pouvoir
    `terraform apply` sur un cluster LocalStack vierge sans erreur "already
    exists". En local, le learner peut commenter cette fonction s'il veut
    itérer plus vite — c'est documenté dans le README.
    """
    for pattern in ("terraform.tfstate", "terraform.tfstate.backup", ".terraform.lock.hcl"):
        for p in TERRAFORM_DIR.glob(pattern):
            p.unlink(missing_ok=True)
    dot_tf = TERRAFORM_DIR / ".terraform"
    if dot_tf.exists():
        shutil.rmtree(dot_tf)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def localstack_ready():
    _wait_for_localstack()
    return ENDPOINT


@pytest.fixture(scope="session")
def fixture_csv():
    if not FIXTURE_CSV.exists():
        # Régénère sur le coup — le contenu est déterministe (seed=42).
        subprocess.run(
            [sys.executable, "-m", "fixtures.generate_fixtures"],
            cwd=PROJECT_ROOT,
            check=True,
        )
    return FIXTURE_CSV


@pytest.fixture(scope="session")
def applied_stack(localstack_ready, fixture_csv):
    """Fait `terraform init && apply` puis upload la fixture. Renvoie un dict d'IDs."""
    _clean_terraform_state()
    _run_terraform(["terraform", "init", "-no-color", "-input=false"])
    _run_terraform(["terraform", "apply", "-auto-approve", "-no-color", "-input=false"])

    # Upload de la fixture — TRIGGERS la Lambda via l'event notification.
    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with FIXTURE_CSV.open("rb") as f:
        s3.put_object(
            Bucket=BUCKET,
            Key="incoming/deliveries_2026-04-16.csv",
            Body=f.read(),
            ContentType="text/csv",
        )

    return {
        "bucket": BUCKET,
        "table": TABLE,
        "lambda_function_name": LAMBDA_FN,
    }


@pytest.fixture(scope="session")
def ddb_client(applied_stack):
    return boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture(scope="session")
def s3_client(applied_stack):
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


@pytest.fixture(scope="session")
def logs_client(applied_stack):
    return boto3.client(
        "logs",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def wait_for_ddb_count(ddb, table: str, expected: int, timeout_s: int = DDB_POLL_TIMEOUT_S) -> int:
    """Poll DynamoDB Scan jusqu'à count == expected, ou timeout. Retourne le dernier count vu."""
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        resp = ddb.scan(TableName=table, Select="COUNT")
        last = resp.get("Count", 0)
        if last == expected:
            return last
        time.sleep(DDB_POLL_INTERVAL_S)
    return last
