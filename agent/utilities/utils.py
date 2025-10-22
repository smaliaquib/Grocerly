import re

import boto3
import json
from typing import List, Optional
from pydantic import BaseModel


def get_stripe_key() -> str:
    """
    Fetch Stripe secret key from AWS Secrets Manager.
    Adjust the SecretId and region name based on your setup.
    """
    secret_name = "dev/stripe-secret"  # Replace with your actual secret name for Stripe
    region_name = "us-east-1"  # Replace with your secrets region

    # Create a session and Secrets Manager client
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)

    try:
        # Retrieve the secret value
        response = client.get_secret_value(SecretId=secret_name)
        secret_string = response[
            "SecretString"
        ]  # e.g., '{"STRIPE_SECRET_KEY": "sk_test_123..."}'
        secret_dict = json.loads(secret_string)

        # Adjust the key used here to match your secret's JSON structure
        return secret_dict.get("STRIPE_SECRET_KEY", "")
    except Exception as e:
        print(f"Error retrieving Stripe secret key: {e}")
        return ""


class Item(BaseModel):
    name: str
    quantity: int
    unit: Optional[str] = None  # Make unit optional


class ItemList(BaseModel):
    products: List[Item]


def parse_raw_items(raw_data: List[str]) -> ItemList:
    # Join the list into a single string and normalize spacing
    raw_string = " ".join(raw_data)
    raw_string = re.sub(r"\s+", " ", raw_string).strip()

    # Update regex to make unit optional
    matches = re.findall(
        r"name=([^,]+?)\s+quantity=(\d+)(?:\s+unit=([^}]+))?", raw_string
    )

    # Convert to structured data
    items = [
        Item(
            name=name.strip(),
            quantity=int(quantity),
            unit=unit.strip() if unit else None,
        )
        for name, quantity, unit in matches
    ]

    return ItemList(products=items)


"""data = ['[{name=Fresh Smoothies', ' quantity=2}', ' {name=fresh strawberries', ' quantity=3}', ' {name=mixed fruits', ' quantity=4}', ' {name=packaged fruits', ' quantity=2}', ' {name=Pineapples', ' quantity=5}]']
result = parse_raw_items(data)
print("parsed_items:", result.products[0].name)"""
