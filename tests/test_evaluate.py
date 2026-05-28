"""IAmDataEng — rubric d'évaluation pour `ingestion.s3-lambda-localstack`.

Six checks déterministes alignés sur la spec du projet. Chaque check produit,
en cas d'échec, un message pédagogique en français qui pointe la cause
probable. Ce module est lancé tel quel par le workflow
`.github/workflows/iamdataeng-evaluate.yml` et par le learner en local via
`pytest tests/`.

Les tests s'appuient SUR L'ARTEFACT (la stack provisionnée + le contenu de
DynamoDB et S3 après upload), pas sur le code source. C'est volontaire : on
évalue le résultat fonctionnel, pas le style. Seul `iam_role_least_privilege`
fait du static analysis sur `terraform/main.tf` parce qu'il n'a pas d'analogue
runtime sur LocalStack Community (qui n'applique pas l'IAM).
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import (
    PROJECT_ROOT,
    TERRAFORM_DIR,
    wait_for_ddb_count,
)

EXPECTED_TOTAL = 1200
EXPECTED_MALFORMED = 2
EXPECTED_VALID = EXPECTED_TOTAL - EXPECTED_MALFORMED  # 1198


# ---------------------------------------------------------------------------
# Check 1 — terraform_apply_succeeds_against_localstack
# ---------------------------------------------------------------------------


def test_terraform_apply_succeeds_against_localstack(applied_stack):
    """`terraform init && apply` se sont terminés sans erreur (fixture session)."""
    # Si on est arrivé ici, la fixture `applied_stack` a déjà fait apply avec
    # succès — sinon elle aurait pytest.fail-é avec le stderr Terraform.
    # On vérifie en plus que les outputs Terraform sont bien présents.
    proc = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=TERRAFORM_DIR,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        pytest.fail(
            "Terraform apply s'est terminé mais `terraform output` ne renvoie rien.\n"
            "Tu as oublié de déclarer les blocs `output { ... }` en bas de main.tf "
            "(bucket_name, ddb_table_name, lambda_function_name).\n"
            f"stderr : {proc.stderr}"
        )


# ---------------------------------------------------------------------------
# Check 2 — dynamodb_row_count_matches
# ---------------------------------------------------------------------------


def test_dynamodb_row_count_matches(ddb_client, applied_stack):
    """Après upload, DynamoDB contient EXACTEMENT 1198 items (1200 - 2 malformées)."""
    count = wait_for_ddb_count(ddb_client, applied_stack["table"], EXPECTED_VALID)
    if count != EXPECTED_VALID:
        if count == 0:
            hint = (
                "0 item dans DynamoDB. Soit la Lambda ne s'est jamais déclenchée "
                "(aws_s3_bucket_notification mal câblée, cf. les TODO dans main.tf), "
                "soit elle a planté à la 1ʳᵉ ligne (lis les logs Lambda via "
                "`awslocal logs tail /aws/lambda/sobral_ingest`)."
            )
        elif count < EXPECTED_VALID:
            hint = (
                f"Il manque {EXPECTED_VALID - count} item(s). Tu as probablement "
                "ignoré les `UnprocessedItems` de batch_write_item — sous "
                "throttling DynamoDB renvoie des items à re-soumettre, ce n'est "
                "pas une erreur. Boucle jusqu'à liste vide."
            )
        else:
            hint = (
                f"{count - EXPECTED_VALID} item(s) en trop. Ton validateur "
                "parse_row() est trop permissif — les 2 lignes malformées "
                "(lat=NaN-broken, lon=999.999) doivent partir en dead-letter, "
                "pas en DynamoDB."
            )
        pytest.fail(
            f"Row count DynamoDB incorrect — attendu {EXPECTED_VALID}, obtenu {count}.\n{hint}"
        )


# ---------------------------------------------------------------------------
# Check 3 — malformed_rows_quarantined
# ---------------------------------------------------------------------------


def test_malformed_rows_quarantined(s3_client, applied_stack):
    """Le préfixe dead-letter/ contient exactement 1 objet, avec 2 lignes."""
    bucket = applied_stack["bucket"]
    resp = s3_client.list_objects_v2(Bucket=bucket, Prefix="dead-letter/")
    contents = resp.get("Contents", [])
    if not contents:
        pytest.fail(
            "Aucun objet sous s3://{}/dead-letter/. Soit ton handler n'écrit "
            "rien quand il rencontre des lignes pourries (mauvais — on perd la "
            "donnée), soit il `raise` à la 1ʳᵉ ligne pourrie et la Lambda "
            "plante avant d'avoir flushé le dead-letter. Traite la ligne "
            "pourrie comme DE LA DONNÉE, pas comme une exception.".format(bucket)
        )
    if len(contents) > 1:
        keys = [c["Key"] for c in contents]
        pytest.fail(
            f"Plusieurs objets dead-letter ({len(contents)}) au lieu d'un seul : "
            f"{keys}. Ton handler crée un nouveau fichier par ligne pourrie ? "
            "Aggrège toutes les lignes malformées d'un même upload dans UN SEUL "
            "objet (sinon en prod tu pollues le bucket avec des milliers de "
            "petits fichiers d'1 ligne)."
        )

    obj = s3_client.get_object(Bucket=bucket, Key=contents[0]["Key"])
    body = obj["Body"].read().decode("utf-8")
    reader = csv.reader(io.StringIO(body))
    rows = list(reader)
    # rows[0] = header, suivantes = data
    if len(rows) < 2:
        pytest.fail(
            f"Objet dead-letter trouvé mais vide (0 ligne de data). Clé : {contents[0]['Key']}.\n"
            "Ton write_dead_letter() écrit le header mais oublie les rows ? "
            "Vérifie l'appel à csv.DictWriter.writerows()."
        )
    data_rows = rows[1:]
    if len(data_rows) != EXPECTED_MALFORMED:
        pytest.fail(
            f"Le fichier dead-letter contient {len(data_rows)} ligne(s) au lieu de "
            f"{EXPECTED_MALFORMED}. Soit ton parse_row() rejette aussi des bonnes "
            "lignes (trop strict), soit il laisse passer des mauvaises (trop laxe). "
            "Les deux lignes pourries de la fixture ont lat=NaN-broken et lon=999.999 — "
            "rien d'autre ne doit échouer."
        )


# ---------------------------------------------------------------------------
# Check 4 — lambda_completes_under_timeout
# ---------------------------------------------------------------------------


def test_lambda_completes_under_timeout(logs_client, applied_stack):
    """La Lambda a terminé en < 10s (budget = 30s).

    On lit les logs CloudWatch émis par LocalStack et on cherche une ligne
    'REPORT RequestId=... Duration=Xms' typique du runtime AWS Lambda. Si
    Duration > 10000ms, c'est que le learner fait `put_item` en boucle au lieu
    de `batch_write_item`.
    """
    log_group = f"/aws/lambda/{applied_stack.get('lambda_function_name', 'sobral_ingest')}"
    try:
        streams = logs_client.describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=5,
        )
    except logs_client.exceptions.ResourceNotFoundException:
        pytest.fail(
            f"Le log group {log_group} n'existe pas. La Lambda ne s'est jamais "
            "exécutée — vérifie que l'event notification S3 -> Lambda est bien "
            "déclarée dans main.tf (aws_s3_bucket_notification.incoming_csv)."
        )

    if not streams.get("logStreams"):
        pytest.fail(
            f"Log group {log_group} vide. Idem : la Lambda n'a pas été invoquée. "
            "Vérifie aws_lambda_permission.allow_s3_invoke et le filter_prefix='incoming/'."
        )

    durations_ms: list[float] = []
    for stream in streams["logStreams"][:3]:
        events = logs_client.get_log_events(
            logGroupName=log_group,
            logStreamName=stream["logStreamName"],
            limit=200,
            startFromHead=False,
        )
        for ev in events.get("events", []):
            m = re.search(r"Duration:\s*([\d.]+)\s*ms", ev.get("message", ""))
            if m:
                durations_ms.append(float(m.group(1)))

    if not durations_ms:
        # LocalStack n'émet pas toujours la ligne REPORT — on ne fail pas
        # silencieusement mais on est explicite sur la limite du check.
        pytest.skip(
            "Aucune ligne REPORT trouvée dans les logs LocalStack — ce check "
            "dépend du format de log du runtime Lambda. Si la Lambda s'est "
            "exécutée correctement (les autres tests passent), considère ce "
            "skip comme bénin. Sur le vrai AWS, le check serait actif."
        )

    worst = max(durations_ms)
    if worst > 10_000:
        pytest.fail(
            f"La Lambda met {worst:.0f} ms (>10s) pour 1200 lignes. C'est très "
            "probablement que tu fais `dynamodb.put_item()` dans une boucle (1 "
            "round-trip réseau par ligne). Utilise batch_write_item (25 items "
            "par appel) — tu vas diviser le temps par ~25."
        )


# ---------------------------------------------------------------------------
# Check 5 — iam_role_least_privilege (static analysis sur main.tf)
# ---------------------------------------------------------------------------


def test_iam_role_least_privilege():
    """Static check : pas de Action='*' ou Resource='*' dans la policy Lambda.

    LocalStack Community n'applique pas l'IAM, donc un check runtime serait
    bidon. On lit main.tf et on vérifie que le learner n'a pas pris le
    raccourci `actions = ["*"]` / `resources = ["*"]` dans le bloc de policy
    qui scope la Lambda. C'est précisément le genre de chose qu'un reviewer
    AWS catche en code review.
    """
    main_tf = (TERRAFORM_DIR / "main.tf").read_text(encoding="utf-8")

    # On considère le contenu après le bloc `lambda_inline` (la policy de la Lambda).
    # On cherche les patterns dangereux UNIQUEMENT à l'intérieur des statements
    # qui mentionnent `actions = [...]` ou `resources = [...]`. Une regex
    # multiligne simple suffit pour les patterns triviaux ; on n'essaie pas de
    # parser HCL — on catche les paresses.
    forbidden_patterns = [
        (r'actions\s*=\s*\[\s*"\*"\s*\]', "actions = [\"*\"]"),
        (r'resources\s*=\s*\[\s*"\*"\s*\]', "resources = [\"*\"]"),
        (r'"Action"\s*:\s*"\*"', "\"Action\": \"*\""),
        (r'"Resource"\s*:\s*"\*"', "\"Resource\": \"*\""),
    ]
    hits = []
    for pattern, label in forbidden_patterns:
        for m in re.finditer(pattern, main_tf, flags=re.IGNORECASE):
            line_num = main_tf[: m.start()].count("\n") + 1
            hits.append((label, line_num))

    if hits:
        details = "\n".join(f"  - {label} (ligne {ln})" for label, ln in hits)
        pytest.fail(
            "Ta policy IAM est un firehose — un wildcard `*` traîne dans main.tf :\n"
            f"{details}\n"
            "Une Lambda qui lit incoming/* et écrit dead-letter/* sur UN bucket, "
            "écrit dans UNE table DynamoDB et logue dans CloudWatch a besoin de "
            "4-5 actions PRÉCISES sur 2-3 ARNs PRÉCIS. Liste-les explicitement. "
            "LocalStack laisse passer (l'IAM n'est pas enforced en Community), "
            "mais sur le vrai AWS ta sécurité tient à cette discipline."
        )


# ---------------------------------------------------------------------------
# Check 6 — readme_documents_localstack_boundary
# ---------------------------------------------------------------------------


def test_readme_documents_localstack_boundary():
    """Le README contient une section 'Where this differs from real AWS' (FR ou EN).

    Cible la formulation FR par défaut, mais on accepte l'EN aussi (cas d'un
    learner qui code-switche). On cherche un titre H2 contenant les marqueurs
    clés ('différences', 'real AWS', 'vrai AWS', 'differs') puis on vérifie
    qu'au moins 2 limitations LocalStack Community sont citées.
    """
    readme = PROJECT_ROOT / "README.fr.md"
    if not readme.exists():
        pytest.fail(
            "README.fr.md introuvable à la racine du projet. C'est ton livrable "
            "principal côté documentation — supprimer ce fichier, c'est livrer "
            "du code sans contexte."
        )
    content = readme.read_text(encoding="utf-8")

    # Détection du titre de section (H2 ou H3) contenant le marqueur.
    section_pattern = re.compile(
        r"^#{2,3}\s+.*(différenc|differs|real\s+AWS|vrai\s+AWS).*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = section_pattern.search(content)
    if not match:
        pytest.fail(
            "Pas de section 'Différences vs le vrai AWS' (ou 'Where this differs "
            "from real AWS') dans README.fr.md. Un projet LocalStack qui se "
            "respecte documente ce que LocalStack N'apprend PAS — un recruteur "
            "te posera la question en entretien, autant l'avoir écrit."
        )
    # Extraire le contenu de la section jusqu'au prochain titre de niveau >= H2.
    start = match.end()
    next_section = re.search(r"^#{1,3}\s+", content[start:], re.MULTILINE)
    end = start + next_section.start() if next_section else len(content)
    body = content[start:end].lower()

    # Liste des limitations attendues — on en exige au moins 2 distinctes.
    markers = {
        "iam": ["iam", "least privilege", "policy", "permission"],
        "network": ["network", "latency", "vpc", "réseau", "latence"],
        "cost": ["cost", "coût", "billing", "facture"],
        "kms": ["kms", "encryption", "chiffrement", "sse"],
        "scaling": ["concurrency", "throttl", "scaling", "limite de concurrence"],
        "monitoring": ["cloudwatch", "metric", "métrique", "observability"],
    }
    hit_categories = sum(
        1 for words in markers.values() if any(w in body for w in words)
    )
    if hit_categories < 2:
        pytest.fail(
            f"La section 'différences vs vrai AWS' n'évoque qu'{hit_categories} "
            "limitation distincte de LocalStack Community. La rubric exige AU "
            "MOINS 2 limitations parmi : enforcement IAM, latence/réseau, coût, "
            "KMS/chiffrement S3, limites de concurrence Lambda, "
            "CloudWatch/observability. Détaille."
        )
