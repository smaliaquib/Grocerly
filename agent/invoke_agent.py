import json
import os

import boto3
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.data_classes.appsync import scalar_types_utils

# Initialize Clients
bedrock_agent_runtime_client = boto3.client(
    "bedrock-agent-runtime", region_name="us-east-1"
)
logger = Logger(service="invoke_agent_lambda")
tracer = Tracer(service="invoke_agent_lambda")
agent_id = os.environ.get("AGENT_ID")
agent_alias = os.environ.get("AGENT_ALIAS")
dynamodb = boto3.resource("dynamodb")


table_name = os.environ.get("ECOMMERCE_TABLE_NAME")

table = dynamodb.Table(table_name)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event, context):
    try:
        logger.info(f"Received event: {json.dumps(event, indent=2)}")

        # Parse the event body
        grocery_list = event["grocery_list"]
        logger.info(f"Received event body: {grocery_list}")

        # Extract grocery_list and validate
        # grocery_list = event_body.get("grocery_list")
        if not grocery_list:
            raise ValueError("Error: `grocery_list` is missing or empty.")

        # Create query string
        query = f"Create and return a single Stripe payment link with the list of products: {grocery_list}"

        # Generate a unique session ID
        session_id = scalar_types_utils.make_id()

        # Invoke the Bedrock Agent
        agent_response = bedrock_agent_runtime_client.invoke_agent(
            inputText=query,
            agentId=agent_id,
            agentAliasId=agent_alias,
            sessionId=session_id,
            enableTrace=True,
        )

        # Ensure the response contains the event stream
        if "completion" not in agent_response:
            raise Exception("Agent response is missing `completion` field.")

        event_stream = agent_response["completion"]

        # Collect all chunks from the stream
        chunks = []
        for event in event_stream:
            chunk = event.get("chunk")
            if chunk:
                decoded_bytes = chunk.get("bytes").decode()
                print("bytes: ", decoded_bytes)
                chunks.append(decoded_bytes)
        completion = " ".join(chunks)

        print(f"Completion: {completion}")

        # save result to database
        stripe_response = {
            "PK": "PAYMENLINK",
            "SK": f"USERID#{session_id}",
            "payment_link": completion.replace("\n", ""),
        }
        table.put_item(Item=stripe_response)

        return completion

        # Return the final response to API Gateway

    except Exception as e:
        logger.error(f"Unhandled error: {e}")
        return "an error occured"
