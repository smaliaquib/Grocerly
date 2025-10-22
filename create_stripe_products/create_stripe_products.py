import json
import os
import boto3
from botocore.exceptions import ClientError
from aws_lambda_powertools import Logger, Tracer
import stripe
from stripe import StripeError

from utilities.utils import get_stripe_key

dynamodb = boto3.resource("dynamodb")
table_name = os.environ.get("ECOMMERCE_TABLE_NAME")
table = dynamodb.Table(table_name)


# Load product list from JSON file
with open("product_list.json", "r") as product_list_file:
    product_list = json.load(product_list_file)

logger = Logger(service="create_stripe_products")
tracer = Tracer(service="create_stripe_products_service")


def bulk_add_products_to_dynamodb(products):
    """
    Bulk add products to DynamoDB.
    Each product will have:
    - PK: productId
    - SK: stripe_price_id
    - stripe: stripe_product_id
    """
    failed_items = []
    try:
        with table.batch_writer() as batch:
            for product in products:
                try:
                    item = {
                        # Partition Key
                        "PK": product["stripe_product_id"],  # Partition Key
                        "SK": product["stripe_price_id"],  # Sort Key (Stripe Price ID)
                        "name": product["name"],
                        "description": product["description"],
                        "category": product["category"],
                        "createdDate": product["createdDate"],
                        "modifiedDate": product["modifiedDate"],
                        "tags": product["tags"],
                        "package": product["package"],
                        "pictures": product["pictures"],
                        "price": product["price"],
                        "stripeProductId": product[
                            "stripe_product_id"
                        ],  # Stripe Product ID
                        "stripePriceId": product[
                            "stripe_price_id"
                        ],  # Stripe Product ID
                    }
                    batch.put_item(Item=item)
                except ClientError as e:
                    logger.error(
                        f"Failed to add product {product['productId']} to DynamoDB: {e}"
                    )
                    failed_items.append(product)
        if failed_items:
            logger.error(f"Failed to add {len(failed_items)} items to DynamoDB")
        else:
            logger.info("Successfully bulk added products to DynamoDB")
    except Exception as e:
        logger.error(f"Unexpected error during bulk insert: {e}")
        raise


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event, context):
    stripe_key = get_stripe_key()
    if stripe_key is None:
        logger.error("Stripe API key not set")
        raise ValueError("Stripe API key not set")

    # Set Stripe key
    stripe.api_key = stripe_key
    logger.info("Retrieving all products: %s", product_list)

    products_to_insert = []

    # Iterate through the list and create products and prices
    for product_data in product_list:
        try:
            # Create a product in Stripe
            product = stripe.Product.create(
                name=product_data["name"],
                description=product_data["description"],
                metadata={
                    "category": product_data["category"],
                    "createdDate": product_data["createdDate"],
                    "modifiedDate": product_data["modifiedDate"],
                    "productId": product_data["productId"],
                    "tags": ", ".join(product_data["tags"]),
                    "package": json.dumps(product_data["package"]),
                },
                images=product_data["pictures"],
            )
            logger.info(f"Product created: {product.name} (ID: {product.id})")

            # Create a price for the product in Stripe
            price = stripe.Price.create(
                unit_amount=product_data["price"],  # Price in cents
                currency="usd",  # Currency code
                product=product.id,  # Link to the product
            )
            logger.info(
                f"Price created: {price.unit_amount / 100} {price.currency} (ID: {price.id})"
            )

            # Prepare product data for DynamoDB
            product_data["stripe_product_id"] = product.id  # Stripe Product ID
            product_data["stripe_price_id"] = price.id  # Stripe Price ID
            products_to_insert.append(product_data)

        except StripeError as e:
            logger.error(
                f"Error creating product or price for {product_data['name']}: {e.user_message}"
            )
            continue  # Skip this product and continue with the next one

    # Bulk add products to DynamoDB
    try:
        bulk_add_products_to_dynamodb(products_to_insert)
    except Exception as e:
        logger.error(f"Failed to bulk add products to DynamoDB: {e}")
        raise

    return "Products and prices created successfully!"
