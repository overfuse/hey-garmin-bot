from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

PACE = r"^[0-9]+:[0-5][0-9]$"  # mm:ss per km


class RunStep(BaseModel):
    """A running segment. Omit 'pace' to model an easy recovery jog."""

    type: Literal["run"]
    distance: int = Field(ge=1, description="Distance in metres")
    pace: Optional[str] = Field(None, pattern=PACE, description="Target pace (min:sec per km)")


class RestStep(BaseModel):
    """Passive or standing rest."""

    type: Literal["rest"]
    rest: int = Field(ge=1, description="Rest duration in seconds")


class RecoveryStep(BaseModel):
    """An active recovery jog segment without a target pace."""

    type: Literal["recovery"]
    distance: int = Field(ge=1, description="Distance in metres")


class RepeatGroup(BaseModel):
    """A grouping that repeats its internal steps in order."""

    type: Literal["repeat"]
    repeat: int = Field(ge=1, description="Number of repetitions")
    steps: List["Element"] = Field(description="Ordered list of steps to repeat")


# Plain union -> JSON Schema `anyOf` (OpenAI structured outputs rejects the
# `oneOf` that a Pydantic discriminated union would emit). The distinct
# Literal `type` tags still let Pydantic select the right variant on parse.
Element = Union[RunStep, RestStep, RecoveryStep, RepeatGroup]


class Segment(BaseModel):
    """Optional easy running before/after the main set (lap-based if distance omitted)."""

    distance: Optional[int] = Field(None, ge=1, description="Distance in metres")
    pace: Optional[str] = Field(None, pattern=PACE, description="Optional target pace (min:sec per km)")


class Workout(BaseModel):
    name: str = Field(description="Friendly workout name (e.g. '10×300/100/200 + rest')")
    warmup: Optional[Segment] = None
    intervals: List[Element] = Field(description="Main workout segment of run/rest steps or repeat groups")
    cooldown: Optional[Segment] = None


RepeatGroup.model_rebuild()  # resolve the forward reference to Element
