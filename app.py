import aws_cdk as cdk

from grocery_ai_agent_cdk.ai_agent_stack import AiAgentStack
from grocery_ai_agent_cdk.api_lambda_s3_sfn_stack import ApiLambdaS3SfnStack

from grocery_ai_agent_cdk.database_stack import DatabaseStack
from grocery_ai_agent_cdk.pipes_eb_stack import PipesAndEventbridgeStack
from grocery_ai_agent_cdk.sqs_stack import SQSStack

app = cdk.App()

sqs_stack = SQSStack(app, "SQSStack")
# Create the database stack
db_stack = DatabaseStack(app, "DatabaseStack")

# Create the API and Lambda stack, passing the DynamoDB table
api_lambda_stack = ApiLambdaS3SfnStack(
    app,
    "ApiLambdaS3SfnStack",
    sqs_queue=sqs_stack.sqs_queue,
    ecommerce_table=db_stack.ecommerce_table,
)

pipes_eb_stack = PipesAndEventbridgeStack(
    app,
    construct_id="PipesAndEventbridgeStack",
    target_dlq=sqs_stack.target_dlq,
    pipe_dlq=sqs_stack.pipe_dlq,
    appsync_api=api_lambda_stack.appsync_api,
    ecommerce_table=db_stack.ecommerce_table,
)

# Create the AI Agent stack
ai_agent_stack = AiAgentStack(
    app,
    "AiAgentStack",
    secret=api_lambda_stack.secret,
    invoke_agent_lambda=api_lambda_stack.invoke_agent_lambda,
    ecommerce_table=db_stack.ecommerce_table,
)

app.synth()
