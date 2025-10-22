import json
import boto3
import os
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.data_classes import event_source, SQSEvent

# Initialize AWS clients
sqs_client = boto3.client("sqs")
bedrock_client = boto3.client("bedrock-runtime")
stepfunctions_client = boto3.client("stepfunctions")  # Step Functions client

# Get the SQS queue URL from environment variables
sqs_queue_url = os.environ["SQS_QUEUE_URL"]

logger = Logger(service="sqs_poller")


@event_source(data_class=SQSEvent)
@logger.inject_lambda_context(log_event=True)
def handler(event: SQSEvent, context):
    # Log the event for debugging
    logger.info(f"Received event: {event}")

    for record in event.records:  # Ensure we handle multiple SQS messages
        try:
            logger.info(f"Processing record: {record}")
            event_body = json.loads(record.body)
            logger.info(f"Received event body: {event_body}")

            # Extract the input data
            input_text = event_body["input"]["text"]
            task_token = event_body["taskToken"]
            logger.info(f"Extracted Data - Text: {input_text}, TaskToken: {task_token}")

            # Use the Bedrock foundation model to process the text
            prompt = f"""You are a helpful assistant that extracts grocery items alongside their quantities and unit from text.
            If the text contains a grocery list, respond with ONLY the list of items alongside their quantity and unit in this format:
            - Item 1, kg
            - Item 2, kg
            - Item 3, kg

            If the text does NOT contain a grocery list, respond with: "No grocery list found."

            Here is the text:
            {input_text}"""

            # Call the Bedrock AI model
            response = bedrock_client.invoke_model(
                modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
                body=json.dumps(
                    {
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 300,
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "anthropic_version": "bedrock-2023-05-31",
                    }
                ),
            )

            # Parse the response from Bedrock
            response_body = json.loads(response["body"].read())
            manipulated_text = response_body.get("content", [{}])[0].get("text", "")

            # Log and process response
            if "No grocery list found." in manipulated_text:
                logger.info("No grocery list found in the extracted text.")
                # Send task failure to Step Functions
                stepfunctions_client.send_task_failure(
                    taskToken=task_token,
                    error="NoGroceryListFound",
                    cause="The input text does not contain a grocery list.",
                )
            else:
                logger.info(f"Grocery List:\n{manipulated_text}")
                # Send task success to Step Functions
                stepfunctions_client.send_task_success(
                    taskToken=task_token,
                    output=json.dumps(
                        {"status": "SUCCESS", "grocery_list": manipulated_text}
                    ),
                )

            # Delete the processed message from the queue
            receipt_handle = record.receipt_handle
            sqs_client.delete_message(
                QueueUrl=sqs_queue_url, ReceiptHandle=receipt_handle
            )

        except Exception as e:
            logger.error(f"Error processing SQS message: {str(e)}")
            # Send task failure to Step Functions
            stepfunctions_client.send_task_failure(
                taskToken=task_token, error="ProcessingError", cause=str(e)
            )
