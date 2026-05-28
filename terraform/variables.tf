variable "localstack_endpoint" {
  type        = string
  description = "Edge URL where every AWS service is faked. http://localhost:4566 locally, http://localstack:4566 inside CI service-container networks."
  default     = "http://localhost:4566"
}

variable "aws_region" {
  type        = string
  description = "Logical region. LocalStack doesn't care, but boto3 and AWS CLI do."
  default     = "us-east-1"
}

variable "bucket_name" {
  type        = string
  description = "S3 bucket that receives the CSV uploads."
  default     = "sobral-deliveries"
}

variable "ddb_table_name" {
  type        = string
  description = "DynamoDB target table — partition key truck_id, sort key delivery_id."
  default     = "delivery_events"
}

variable "lambda_function_name" {
  type        = string
  description = "Name used in CloudWatch logs and the AWS console."
  default     = "sobral_ingest"
}

variable "lambda_zip_path" {
  type        = string
  description = "Absolute path to the Lambda deployment package (built by terraform/build_lambda.sh)."
  default     = "../build/lambda_package.zip"
}

variable "lambda_source_dir" {
  type        = string
  description = "Directory containing lambda_handler.py — used by the archive_file data source."
  default     = "../src"
}
