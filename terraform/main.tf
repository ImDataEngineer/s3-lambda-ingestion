# Sobral ingestion stack — S3 -> Lambda -> DynamoDB.
#
# You are completing this file. The skeleton below declares the resource
# anchors and points at the gaps with TODO comments. Every `aws_*` resource
# here is supported by LocalStack Community (no Pro features required).
#
# Idempotence reminder
# --------------------
# `terraform apply` MUST be repeatable. The rubric runs it twice in CI and
# compares state. That means:
#   - DON'T hard-code resource IDs that include random suffixes regenerated
#     on every plan (e.g. `random_id` without `keepers`).
#   - DON'T `local-exec` an `aws s3 cp` that uploads on every apply — that
#     would change object versions and break the "no diff on re-apply" check.
#   - The CSV upload is done by the TEST code, not by Terraform.

# ---------------------------------------------------------------------------
# 1. S3 bucket
# ---------------------------------------------------------------------------
# A single bucket holds both the inbound CSVs (under `incoming/`) and the
# dead-letter rejects (under `dead-letter/`). Separating them by prefix keeps
# IAM policies tight (Lambda can read incoming/* and write dead-letter/* only).

resource "aws_s3_bucket" "deliveries" {
  bucket = var.bucket_name
  # TODO: ajouter `force_destroy = true` est tentant pour les tests, mais
  #       documente pourquoi tu l'actives ou pas — en prod c'est NON.
}

# ---------------------------------------------------------------------------
# 2. DynamoDB table — partition key + sort key
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "delivery_events" {
  name         = var.ddb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "truck_id"
  range_key    = "delivery_id"

  # TODO : déclare les deux attributs utilisés en hash_key et range_key.
  #        DynamoDB exige un bloc `attribute {}` PAR colonne référencée par
  #        une clé. Ici on en a deux : truck_id (numérique) et delivery_id
  #        (string). À toi d'écrire les deux blocs avec la bonne syntaxe
  #        Terraform et les types AttributeType corrects (consulte la doc
  #        de `aws_dynamodb_table`).
}

# ---------------------------------------------------------------------------
# 3. IAM — Lambda execution role + scoped policy
# ---------------------------------------------------------------------------
# Note importante : LocalStack Community n'applique PAS l'IAM par défaut
# (toute Lambda peut tout faire dans LocalStack même avec un rôle vide).
# La rubric vérifie quand même que ta policy est SCOPÉE — c'est ce qu'un
# reviewer regardera quand tu porteras vers le vrai AWS.

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "sobral-ingest-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# TODO : rédige une policy scopée pour le rôle d'exécution de la Lambda.
#
# Contraintes (la rubric le vérifie statiquement) :
#   - PAS de "*" sur Action ni sur Resource (least privilege).
#   - Aucun wildcard `s3:*` ou `dynamodb:*` ouvert sur tout le compte.
#
# La Lambda a besoin de pouvoir, et rien de plus :
#   - lire les CSV déposés (préfixe `incoming/` du bucket S3)
#   - écrire les lignes rejetées (préfixe `dead-letter/`)
#   - faire un BatchWriteItem dans la table DynamoDB
#   - émettre ses logs CloudWatch
#
# À toi de découvrir la syntaxe `data "aws_iam_policy_document"` +
# `resource "aws_iam_role_policy"`. Les ARN à utiliser sont
# `aws_s3_bucket.deliveries.arn` et
# `aws_dynamodb_table.delivery_events.arn`. La doc Terraform pour AWS
# détaille chaque champ (statement, actions, resources, sid).

# ---------------------------------------------------------------------------
# 4. Lambda function — zip is built by terraform/build_lambda.sh
# ---------------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = var.lambda_zip_path
}

resource "aws_lambda_function" "sobral_ingest" {
  function_name    = var.lambda_function_name
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_handler.lambda_handler"
  runtime          = "python3.11"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      DDB_TABLE_NAME     = var.ddb_table_name
      DEAD_LETTER_PREFIX = "dead-letter/"
      # NB : LocalStack injecte automatiquement AWS_ENDPOINT_URL dans les
      # runtime containers Lambda depuis la version 2.x. Tu n'as pas besoin
      # de le déclarer ici. Si tu portes ce projet vers le vrai AWS, retire
      # toute référence à AWS_ENDPOINT_URL côté code.
    }
  }
}

# ---------------------------------------------------------------------------
# 5. S3 -> Lambda permission + event notification
# ---------------------------------------------------------------------------

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.sobral_ingest.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.deliveries.arn
}

resource "aws_s3_bucket_notification" "incoming_csv" {
  bucket = aws_s3_bucket.deliveries.id

  # TODO : déclenche la Lambda quand un fichier `.csv` est déposé sous le
  #        préfixe `incoming/`. L'évènement S3 attendu est ObjectCreated
  #        (tous les modes : Put, Post, Copy, etc.). Cherche dans la doc de
  #        `aws_s3_bucket_notification` le bloc qui relie un bucket à une
  #        Lambda function avec un filtre de préfixe ET un filtre de
  #        suffixe. Référence la Lambda via `aws_lambda_function.sobral_ingest.arn`.

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}

# ---------------------------------------------------------------------------
# 6. Outputs — used by the test harness
# ---------------------------------------------------------------------------

output "bucket_name" {
  value = aws_s3_bucket.deliveries.bucket
}

output "ddb_table_name" {
  value = aws_dynamodb_table.delivery_events.name
}

output "lambda_function_name" {
  value = aws_lambda_function.sobral_ingest.function_name
}
