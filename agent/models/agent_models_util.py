from datetime import datetime
from typing import List
from pydantic import BaseModel


class Package(BaseModel):
    height: int
    length: int
    weight: int
    width: int


class Product(BaseModel):
    productId: str
    category: str
    createdDate: datetime
    description: str
    modifiedDate: datetime
    name: str
    package: Package
    pictures: List[str]  # Changed from HttpUrl to str for simplicity
    price: int
    tags: List[str]
