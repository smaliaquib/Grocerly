import json

from aws_cdk import (
    Stack,
    aws_lambda,
    aws_appsync,
    aws_lambda_event_sources as lambda_event_sources,
    CfnOutput,
    aws_secretsmanager as secretsmanager,
    Duration,
    aws_s3 as s3,
    aws_stepfunctions as sfn,
    aws_iam as iam,
    aws_s3,
    aws_s3_notifications,
)
from aws_cdk.aws_dynamodb import Table
from aws_cdk.aws_lambda import (
    Runtime,
)
from aws_cdk.aws_sqs import Queue
from constructs import Construct
from aws_cdk.aws_lambda_python_alpha import PythonFunction


class ApiLambdaS3SfnStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        sqs_queue: Queue,
        ecommerce_table: Table,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Step 1: Define the secret (if it doesn't already exist)
        secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "ExistingStripeSecret",
            secret_name="dev/stripe-secret",  # Name of the existing secret
        )

        grocery_list_bucket = s3.Bucket(
            self,
            "grocery-list-bucket",
            versioned=False,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # AppSync API
        api = aws_appsync.GraphqlApi(
            self,
            "GroceryListAgentApi",
            name="grocery-list-agents-api",
            definition=aws_appsync.Definition.from_schema(
                aws_appsync.SchemaFile.from_asset("graphql/schema.graphql"),
            ),
            log_config=aws_appsync.LogConfig(
                field_log_level=aws_appsync.FieldLogLevel.ALL
            ),
            authorization_config=aws_appsync.AuthorizationConfig(
                default_authorization=aws_appsync.AuthorizationMode(
                    authorization_type=aws_appsync.AuthorizationType.API_KEY
                ),
                additional_authorization_modes=[
                    aws_appsync.AuthorizationMode(
                        authorization_type=aws_appsync.AuthorizationType.IAM  # IAM Auth
                    )
                ],
            ),
        )

        # Lambda functions
        batch_upload_products_lambda = PythonFunction(
            self,
            "BatchUploadProductsLambda",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./batch_upload_products",
            index="batch_upload_products.py",
            handler="handler",
        )

        create_stripe_products_lambda = PythonFunction(
            self,
            "CreateStripeProductsLambda",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            entry="./create_stripe_products",
            index="create_stripe_products.py",
            handler="handler",
        )

        # Grant permissions
        ecommerce_table.grant_write_data(batch_upload_products_lambda)
        ecommerce_table.grant_write_data(create_stripe_products_lambda)
        secret.grant_read(create_stripe_products_lambda)
        create_stripe_products_lambda.add_environment(
            "ECOMMERCE_TABLE_NAME", ecommerce_table.table_name
        )

        # create products in stripe lambda Function for Resolver
        trigger_step_function_products_lambda_function = PythonFunction(
            self,
            "TriggerStepFunctionsWorkflow",
            runtime=Runtime.PYTHON_3_11,
            entry="./step_functions_workflow_trigger",
            index="step_functions_workflow_trigger.py",
            handler="handler",
        )
        # create products in stripe lambda Function for Resolver
        invoke_agent_lambda = PythonFunction(
            self,
            "InvokeGroceryListAgent",
            runtime=Runtime.PYTHON_3_11,
            entry="./agent",
            index="invoke_agent.py",
            handler="handler",
            timeout=Duration.minutes(2),
            memory_size=512,
        )
        sqs_poller_lambda = PythonFunction(
            self,
            "LambdaSQSPoller",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            handler="handler",
            index="lambda_sqs_poller.py",
            entry="./sqs_poller",
            timeout=Duration.seconds(30),
        )

        # Step 11: Grant the second Lambda function permissions to poll the SQS queue
        sqs_queue.grant_consume_messages(sqs_poller_lambda)
        """
        invoke_agent_lambda_url = invoke_agent_lambda.add_function_url(
            auth_type=FunctionUrlAuthType.NONE,  # Public access
            invoke_mode=InvokeMode.RESPONSE_STREAM,
            cors=FunctionUrlCorsOptions(
                allowed_origins=["*"],  # Allow all origins
                allowed_methods=[HttpMethod.GET],  # Allow GET requests
            ),
        )
        """
        invoke_agent_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeAgent",
                    "bedrock:RetrieveAndGenerate",
                    "bedrock:Retrieve",
                    "bedrock:ListAgents",
                    "bedrock:GetAgent",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
                # Grant access to all Bedrock models
            )
        )
        ecommerce_table.grant_write_data(invoke_agent_lambda)

        # Add Lambda as a DataSource for AppSync
        lambda_ds = api.add_lambda_data_source(
            "LambdaDataSource", batch_upload_products_lambda
        )

        # Define None DataSource
        none_data_source = aws_appsync.CfnDataSource(
            self,
            "GroceryAppTableDataSource",
            api_id=api.api_id,
            name="NoneDataSource",
            type="NONE",
            description="None",
        )

        # Define Mutation Resolver
        mutation_resolver = aws_appsync.CfnResolver(
            self,
            "GroceryAppMutationResolver",
            api_id=api.api_id,
            type_name="Mutation",
            field_name="publish",
            data_source_name=none_data_source.name,
            request_mapping_template="""
                       {
                         "version": "2017-02-28",
                         "payload": {
                             "id": "$context.arguments.id",
                             "source": "$context.arguments.source",
                             "account": "$context.arguments.account",
                             "time": "$context.arguments.time",
                             "region": "$context.arguments.region",
                             "detailType": "$context.arguments.detailType",
                             "data": "$context.arguments.data"
                         }
                       }
                   """,
            response_mapping_template="$util.toJson($context.result)",
        )

        # Ensure the resolver depends on the DataSource
        mutation_resolver.add_dependency(none_data_source)

        # Define Resolvers
        lambda_ds.create_resolver(
            id="BatchUploadProductsResolver",
            type_name="Mutation",
            field_name="batchUploadProducts",
            request_mapping_template=aws_appsync.MappingTemplate.lambda_request(),
            response_mapping_template=aws_appsync.MappingTemplate.lambda_result(),
        )

        # Step 3: Grant the Lambda function permissions to read from the S3 bucket
        grocery_list_bucket.grant_read(trigger_step_function_products_lambda_function)
        # Add an S3 event notification to trigger the Lambda function

        # Step 5: Add an S3 event trigger to invoke the Lambda function
        notification = aws_s3_notifications.LambdaDestination(
            trigger_step_function_products_lambda_function
        )

        grocery_list_bucket.add_event_notification(
            aws_s3.EventType.OBJECT_CREATED, notification
        )

        sqs_poller_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["*"],  # Grant access to all Bedrock models
            )
        )

        # Load the ASL definition from the JSON file
        with open("./state_machine/state_machine_definition.asl.json", "r") as file:
            state_machine_definition = json.load(file)

        # Create the Step Functions state machine using the ASL definition
        state_machine = sfn.StateMachine(
            self,
            "GroceryDocumentTextractStateMachine",
            definition_body=sfn.DefinitionBody.from_string(
                json.dumps(state_machine_definition)
            ),
            definition_substitutions={
                "SQS_QUEUE_URL": sqs_queue.queue_url,
                "INVOKE_LAMBDA_FUNCTION_ARN": invoke_agent_lambda.function_arn,
            },
            # Use definition_body
            state_machine_type=sfn.StateMachineType.STANDARD,
        )

        # Grant the Lambda function permissions to send task success/failure
        state_machine.grant_task_response(sqs_poller_lambda)
        invoke_agent_lambda.grant_invoke(state_machine)
        invoke_agent_lambda.add_environment(
            "ECOMMERCE_TABLE_NAME", ecommerce_table.table_name
        )

        trigger_step_function_products_lambda_function.add_environment(
            "STATE_MACHINE_ARN", state_machine.state_machine_arn
        )

        # Optionally, grant the Lambda function permissions to start executions
        state_machine.grant_start_execution(sqs_poller_lambda)

        state_machine.grant_start_execution(
            trigger_step_function_products_lambda_function
        )

        # Grant the state machine permissions to interact with S3
        grocery_list_bucket.grant_read_write(state_machine)

        # Grant the state machine permissions to use Textract
        state_machine.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:StartDocumentTextDetection",
                    "textract:GetDocumentTextDetection",
                    "textract:DetectDocumentText",
                ],
                resources=["*"],  # Textract does not support resource-level permissions
            )
        )

        # Grant the state machine permissions to send messages to the SQS queue
        sqs_queue.grant_send_messages(state_machine)
        # Outputs

        # Step 11: Add an SQS event source mapping to trigger the Lambda function
        sqs_event_source = lambda_event_sources.SqsEventSource(sqs_queue)
        sqs_poller_lambda.add_event_source(sqs_event_source)

        sqs_poller_lambda.add_environment("SQS_QUEUE_URL", sqs_queue.queue_url)

        self.sqs_poller_lambda = sqs_poller_lambda
        self.invoke_agent_lambda = invoke_agent_lambda
        self.secret = secret
        self.trigger_step_function_products_lambda = (
            trigger_step_function_products_lambda_function
        )
        self.appsync_api = api
        self.sqs_queue = sqs_queue
        self.grocery_list_bucket = grocery_list_bucket

        # Output the API endpoint
        CfnOutput(self, "GraphQLEndpoint", value=api.graphql_url)
        (CfnOutput(self, "GraphQLApiKey", value=api.api_key),)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)

        # CfnOutput(self, "InvokeAgentFunctionUrl", value=invoke_agent_lambda_url.url)
