import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int


class Msg(BaseModel):
    detail: str
