"""Common base for domain models: immutable, and unknown fields are an error."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
