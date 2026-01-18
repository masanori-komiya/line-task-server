import uuid
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    script_key: str = Field(min_length=1, max_length=80)
    schedule_value: str = Field(min_length=4, max_length=5)  # "HH:MM"
    notes: str | None = Field(default=None, max_length=2000)


class TaskUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    script_key: str | None = Field(default=None, max_length=80)
    schedule_value: str | None = Field(default=None, max_length=5)
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
