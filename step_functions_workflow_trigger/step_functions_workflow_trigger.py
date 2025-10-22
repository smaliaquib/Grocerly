import json
import boto3
import os
from urllib.parse import unquote_plus
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes import event_source, S3Event

# Initialize clients
textract = boto3.client("textract", region_name="us-east-1")
s3_client = boto3.client("s3", region_name="us-east-1")
sqs_client = boto3.client("sqs", region_name="us-east-1")
stepfunctions_client = boto3.client(
    "stepfunctions", region_name="us-east-1"
)  # Step Functions client

# Get the Step Functions state machine ARN from environment variables
state_machine_arn = os.environ["STATE_MACHINE_ARN"]

logger = Logger()


@event_source(data_class=S3Event)
@logger.inject_lambda_context(log_event=True)
def handler(event: S3Event, context):
    logger.info(f"Received S3 event: {event}")

    for record in event.records:
        logger.info(f"Record: {record}")
        bucket_name = record.s3.bucket.name
        object_key = unquote_plus(record.s3.get_object.key)

        # Allowed file extensions
        allowed_extensions = (".pdf", ".png", ".jpg", ".jpeg")

        logger.info(f"Processing file from bucket: {bucket_name}, key: {object_key}")

        # Get the file type
        file_extension = object_key.split(".")[-1].lower()

        # Check if the file has an allowed extension
        if not object_key.lower().endswith(allowed_extensions):
            logger.info(f"Skipping file: {object_key} (Not a supported format)")
            return {"statusCode": 400, "body": "Unsupported file type"}

        # Prepare the input for the Step Functions workflow
        stepfunctions_input = {
            "bucket_name": bucket_name,
            "file_extension": file_extension,
            "object_key": object_key,
        }

        logger.info("stepfunctions input is: " + str(stepfunctions_input))

        # Start the Step Functions workflow
        try:
            response = stepfunctions_client.start_execution(
                stateMachineArn=state_machine_arn, input=json.dumps(stepfunctions_input)
            )
            logger.info(f"Started Step Functions execution: {response['executionArn']}")
        except Exception as e:
            logger.error(f"Failed to start Step Functions execution: {str(e)}")
            raise e

    return "Successfully started the Step Functions workflow"
