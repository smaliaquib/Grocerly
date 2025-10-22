from aws_cdk import (
    Stack,
    aws_events as events,
    aws_logs as logs,
    aws_iam as iam,
    aws_pipes as pipes,
    aws_appsync as appsync,
)
from aws_cdk.aws_appsync import GraphqlApi
from aws_cdk.aws_dynamodb import Table
from aws_cdk.aws_sqs import Queue
from constructs import Construct


class PipesAndEventbridgeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        appsync_api: GraphqlApi,
        pipe_dlq: Queue,
        target_dlq: Queue,
        ecommerce_table: Table,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create an EventBridge Bus
        event_bus = events.EventBus(
            self,
            "GroceryAppEventBus",
            event_bus_name="GroceryAppEventBus",
        )

        # Create a CloudWatch Log Group for logging events
        log_group = logs.LogGroup(
            self,
            "GroceryAppEventLogs",
            log_group_name="/aws/events/GroceryAppLEventLogs",
            retention=logs.RetentionDays.ONE_WEEK,  # Adjust retention as needed
        )

        # Create an IAM Role for the EventBridge Pipe
        pipe_role = iam.Role(
            self,
            "GroceryAppPipeRole",
            assumed_by=iam.ServicePrincipal("pipes.amazonaws.com"),
        )

        # Grant the Pipe Role permissions to read from the DynamoDB Stream
        ecommerce_table.grant_stream_read(pipe_role)

        # Grant the Pipe Role permissions to send messages to the DLQ
        pipe_dlq.grant_send_messages(pipe_role)

        # Grant the Pipe Role permissions to put events to the EventBridge Bus
        event_bus.grant_put_events_to(pipe_role)

        # Create the EventBridge Pipe
        pipes.CfnPipe(
            self,
            "GroceryAppPipe",
            name="grocery-app-to-eventbridge",
            role_arn=pipe_role.role_arn,
            source=ecommerce_table.table_stream_arn,
            source_parameters=pipes.CfnPipe.PipeSourceParametersProperty(
                dynamo_db_stream_parameters=pipes.CfnPipe.PipeSourceDynamoDBStreamParametersProperty(
                    starting_position="LATEST",
                    batch_size=1,
                    dead_letter_config=pipes.CfnPipe.DeadLetterConfigProperty(
                        arn=pipe_dlq.queue_arn,
                    ),
                ),
            ),
            target=event_bus.event_bus_arn,
            target_parameters=pipes.CfnPipe.PipeTargetParametersProperty(
                event_bridge_event_bus_parameters=pipes.CfnPipe.PipeTargetEventBridgeEventBusParametersProperty(
                    detail_type="payment-link-created",
                    source="grocery.app",
                ),
            ),
            log_configuration=pipes.CfnPipe.PipeLogConfigurationProperty(
                cloudwatch_logs_log_destination=pipes.CfnPipe.CloudwatchLogsLogDestinationProperty(
                    log_group_arn=log_group.log_group_arn,
                ),
                level="TRACE",
                include_execution_data=["ALL"],
            ),
        )

        # Create an IAM Role for invoking the AppSync API
        appsync_invocation_role = iam.Role(
            self,
            "AppSyncInvocationRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com"),
        )
        api_arn: appsync.CfnGraphQLApi = appsync_api.node.default_child

        # Grant the AppSync Invocation Role permissions to invoke the AppSync API
        appsync_invocation_role.add_to_policy(
            iam.PolicyStatement(
                actions=["appsync:GraphQL"],
                resources=[
                    f"{appsync_api.arn}/types/Mutation/*"
                ],  # Replace with your AppSync API ARN
            )
        )

        # Create an EventBridge Rule using CfnRule
        events.CfnRule(
            self,
            "GroceryAppEventRule",
            event_bus_name=event_bus.event_bus_name,
            event_pattern={
                "source": ["grocery.app"],
                "detail-type": ["payment-link-created"],
            },
            targets=[
                # CloudWatch Logs target
                events.CfnRule.TargetProperty(
                    id="GroceryAppRuleCloudWatchLogs",
                    arn=log_group.log_group_arn,
                ),
                # AppSync target
                events.CfnRule.TargetProperty(
                    id="AppsyncTarget",
                    arn=api_arn.attr_graph_ql_endpoint_arn,  # Replace with your AppSync API ARN
                    role_arn=appsync_invocation_role.role_arn,
                    dead_letter_config=events.CfnRule.DeadLetterConfigProperty(
                        arn=target_dlq.queue_arn,
                    ),
                    input_transformer=events.CfnRule.InputTransformerProperty(
                        input_paths_map={
                            "id": "$.id",
                            "source": "$.source",
                            "account": "$.account",
                            "time": "$.time",
                            "region": "$.region",
                            "data": "$.detail.dynamodb.NewImage",
                            "detailType": "$.detail-type",
                        },
                        input_template='{"data": <data>, "detailType": <detailType>, "id": <id>, "source": <source>, '
                        '"account": <account>, "time": <time>, "region": <region>}',
                    ),
                    app_sync_parameters=events.CfnRule.AppSyncParametersProperty(
                        graph_ql_operation="mutation Publish($data:String!,$detailType:String!,$id:String!,"
                        "$source:String!,$account:String!,$time:String!,$region:String!){publish("
                        "data:$data,detailType:$detailType,id:$id,source:$source,account:$account,"
                        "time:$time,region:$region){data detailType id source account time region}}",
                    ),
                ),
            ],
        )
