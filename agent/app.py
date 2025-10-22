import os
from http.client import HTTPException
from time import time

import boto3
import stripe
from typing_extensions import Annotated
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.event_handler import BedrockAgentResolver
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.event_handler.openapi.params import Body, Query
from utilities.utils import get_stripe_key, parse_raw_items

tracer = Tracer()
logger = Logger()
app = BedrockAgentResolver()
dynamodb = boto3.resource("dynamodb")


table_name = os.environ.get("ECOMMERCE_TABLE_NAME")


table = dynamodb.Table(table_name)
# Set your Stripe API key


metrics = Metrics(namespace="grocery_agent_metrics")
stripe_key = get_stripe_key()
if stripe_key is None:
    logger.info("Stripe API key not set")
    raise HTTPException()
# set stripe key
stripe.api_key = stripe_key


@tracer.capture_method
@app.get(
    "/payment_link",
    description="Creates a stripe payment link when given a list of products,their quantities and units",
)
def payment_link(
    products: Annotated[
        list,
        Query(
            description="a list of products and quantities",
        ),
    ],
) -> Annotated[str, Body(description="Stripe payment link")]:
    # append correlation data to all generated logs
    logger.append_keys(
        session_id=app.current_event.session_id,
        action_group=app.current_event.action_group,
        input_text=app.current_event.input_text,
    )

    """
        Create a payment link for multiple products.

        Args:
            products: A list of dictionaries containing 'product_name' and 'qty' and unit.

        Returns:
            str: The payment link URL.
        """
    try:
        line_items = []
        logger.info("we're here")
        print("products", products)
        parsed_items = parse_raw_items(products)
        print("parsed_items:", parsed_items.products[0].name)
        logger.info(parsed_items.products[0].name)
        # Iterate through the list of products
        for product_info in parsed_items.products:
            logger.info(f"Product info {product_info.name}")
            product_name = product_info.name
            qty = product_info.quantity

            if not product_name or not qty:
                logger.error("Invalid product info:", product_info)
                raise HTTPException()

            logger.info(f"Processing product: {product_name}, Quantity:{qty} ")

            # Step 1: Retrieve the product by name
            products_list = stripe.Product.list(limit=100)

            product = None

            # Filter products by name
            for p in products_list.auto_paging_iter():
                if p.name.lower() == product_name.lower():
                    product = p
                    break

            if not product:
                logger.error(f"No product found with name: {product_name}")
                raise HTTPException()

            logger.info(f"Product found! ID: {product.id}")

            # Step 2: Retrieve the price for the product
            prices = stripe.Price.list(
                product=product.id, limit=1
            )  # Get the first price
            if not prices.data:
                logger.error("No price found for product ID:", product.id)
                raise HTTPException()

            price = prices.data[0]  # Get the first price in the list
            logger.debug(
                f"Price found! ID: {price.id}, Amount: {price.unit_amount / 100} {price.currency.upper()}"
            )
            logger.info(f"line_items 1:{line_items}")
            # Add the product to the line items
            line_items.append(
                {
                    "price": price.id,
                    "quantity": qty,
                }
            )
        logger.debug(f"line_items 2: {line_items}")

        # Step 3: Create a payment link with all line items
        payment_link = stripe.PaymentLink.create(
            line_items=line_items,
        )
        logger.info(f"Payment Link URL: {payment_link.url}")
        return f"Payment Link URL: {payment_link.url}"

    except stripe.error.StripeError as e:
        logger.error("Stripe Error: ", {e.user_message})
        raise HTTPException()
    except Exception as e:
        logger.exception("An unexpected error occurred", log=e)

        raise HTTPException()


@app.get("/current_time", description="Gets the current time in seconds")
@tracer.capture_method
def current_time() -> int:
    return int(time())


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def lambda_handler(event: dict, context: LambdaContext):
    return app.resolve(event, context)


if __name__ == "__main__":
    print(app.get_openapi_json_schema())
