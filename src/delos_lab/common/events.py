from pydantic import BaseModel, ConfigDict

EventValue = str | int | float | bool


class LabEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: float
    component: str
    kind: str
    details: dict[str, EventValue]
