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

  # TODO: déclare les deux attributs ci-dessous (truck_id en "N", delivery_id en "S").
  #       Bloc `attribute { name = ..., type = ... }` à répéter.

  # attribute {
  #   name = "truck_id"
  #   type = "N"
  # }
  # attribute {
  #   name = "delivery_id"
  #   type = "S"
  # }
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

# TODO: rédige une policy explicite (PAS de "*" sur Action ou Resource).
#       La Lambda a besoin de :
#         - s3:GetObject sur incoming/* du bucket
#         - s3:PutObject sur dead-letter/* du bucket
#         - dynamodb:BatchWriteItem + PutItem sur la table delivery_events
#         - logs:CreateLogGroup / CreateLogStream / PutLogEvents
#       Référence les ARNs via les attributs des resources (aws_s3_bucket.deliveries.arn,
#       aws_dynamodb_table.delivery_events.arn).
#
# data "aws_iam_policy_document" "lambda_inline" {
#   statement {
#     sid     = "ReadIncomingObjects"
#     actions = ["s3:GetObject"]
#     resources = ["${aws_s3_bucket.deliveries.arn}/incoming/*"]
#   }
#   statement {
#     sid     = "WriteDeadLetterObjects"
#     actions = ["s3:PutObject"]
#     resources = ["${aws_s3_bucket.deliveries.arn}/dead-letter/*"]
#   }
#   statement {
#     sid     = "WriteDynamoDB"
#     actions = ["dynamodb:BatchWriteItem", "dynamodb:PutItem"]
#     resources = [aws_dynamodb_table.delivery_events.arn]
#   }
#   statement {
#     sid     = "WriteLogs"
#     actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
#     resources = ["arn:aws:logs:*:*:*"]
#   }
# }
#
# resource "aws_iam_role_policy" "lambda_inline" {
#   role   = aws_iam_role.lambda_exec.id
#   policy = data.aws_iam_policy_document.lambda_inline.json
# }

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

  # TODO: déclenche la Lambda sur s3:ObjectCreated:* avec filter_prefix = "incoming/"
  #       et filter_suffix = ".csv". Le bloc `lambda_function` ci-dessous est
  #       à compléter.
  #
  # lambda_function {
  #   lambda_function_arn = aws_lambda_function.sobral_ingest.arn
  #   events              = ["s3:ObjectCreated:*"]
  #   filter_prefix       = "incoming/"
  #   filter_suffix       = ".csv"
  # }

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
