from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

PACE = r"^[0-9]+:[0-5][0-9]$"  # mm:ss per km


def _pad_pace(value: Optional[str]) -> Optional[str]:
    """Zero-pad the minutes so pace is always mm:ss (e.g. '4:20' -> '04:20')."""
    if value is None:
        return None
    minutes, seconds = value.split(":")
    return f"{int(minutes):02d}:{seconds}"


class RunStep(BaseModel):
    """A running segment. Omit 'pace' to model an easy recovery jog."""

    type: Literal["run"]
    distance: int = Field(ge=1, description="Distance in metres")
    pace: Optional[str] = Field(None, pattern=PACE, description="Target pace (min:sec per km)")

    _pad = field_validator("pace")(_pad_pace)


class RestStep(BaseModel):
    """Passive or standing rest."""

    type: Literal["rest"]
    rest: int = Field(ge=1, description="Rest duration in seconds")


class RecoveryStep(BaseModel):
    """An active recovery jog segment without a target pace."""

    type: Literal["recovery"]
    distance: int = Field(ge=1, description="Distance in metres")


# A repeat group only ever holds leaf steps — workouts never nest a repeat inside
# a repeat. Keeping `steps` non-recursive is also required by Anthropic structured
# outputs, which reject self-referencing schemas (`RepeatGroup -> RepeatGroup`).
LeafElement = Union[RunStep, RestStep, RecoveryStep]


class RepeatGroup(BaseModel):
    """A grouping that repeats its internal steps in order."""

    type: Literal["repeat"]
    repeat: int = Field(ge=1, description="Number of repetitions")
    steps: List[LeafElement] = Field(description="Ordered list of steps to repeat")


# Plain union -> JSON Schema `anyOf` (OpenAI structured outputs rejects the
# `oneOf` that a Pydantic discriminated union would emit). The distinct
# Literal `type` tags still let Pydantic select the right variant on parse.
Element = Union[RunStep, RestStep, RecoveryStep, RepeatGroup]


class Segment(BaseModel):
    """Optional easy running before/after the main set (lap-based if distance omitted)."""

    distance: Optional[int] = Field(None, ge=1, description="Distance in metres")
    pace: Optional[str] = Field(None, pattern=PACE, description="Optional target pace (min:sec per km)")

    _pad = field_validator("pace")(_pad_pace)


class Workout(BaseModel):
    name: str = Field(description="Friendly workout name (e.g. '10×300/100/200 + rest')")
    warmup: Optional[Segment] = None
    intervals: List[Element] = Field(description="Main workout segment of run/rest steps or repeat groups")
    cooldown: Optional[Segment] = None
