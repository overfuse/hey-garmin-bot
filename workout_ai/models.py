from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

PACE = r"^[0-9]+:[0-5][0-9]$"  # mm:ss per km

# Bounds are semantic, not just structural. The schema guarantees an int; it does
# not guarantee a *runnable* workout. Without these, `repeat: 100000` builds a
# payload with 100k steps and `pace: "0:00"` divides by zero in the converter's
# pace window. Structured output protects the shape; these protect the meaning.
MAX_DISTANCE_M = 100_000  # 100 km — beyond any single interval step
MAX_REST_S = 3_600
MAX_DURATION_S = 6 * 3_600  # a timed step longer than 6 h is a parse error
MAX_REPEAT = 100
MIN_PACE_S = 90    # 1:30/km — faster than the world record
MAX_PACE_S = 1200  # 20:00/km — slower than walking


def _pad_pace(value: Optional[str]) -> Optional[str]:
    """Zero-pad the minutes so pace is always mm:ss (e.g. '4:20' -> '04:20')."""
    if value is None:
        return None
    minutes, seconds = value.split(":")
    total = int(minutes) * 60 + int(seconds)
    if not MIN_PACE_S <= total <= MAX_PACE_S:
        raise ValueError(f"pace {value} is outside {MIN_PACE_S}-{MAX_PACE_S} s/km")
    return f"{int(minutes):02d}:{seconds}"


def _check_pace_range(step):
    """`pace_slow` is the slow end of a stated range ("4:25-4:30"): it needs a
    `pace` to pair with and must not be faster than it."""
    if step.pace_slow is not None:
        if step.pace is None:
            raise ValueError("pace_slow requires pace")
        if _pace_s(step.pace_slow) < _pace_s(step.pace):
            raise ValueError(f"pace_slow {step.pace_slow} is faster than pace {step.pace}")
    return step


def _pace_s(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


PACE_DESC = "Target pace (min:sec per km)"
PACE_SLOW_DESC = "Slow end of a stated pace range (min:sec per km); 'pace' is then the fast end"


# Distance and time are separate step classes, each with its field REQUIRED,
# rather than one class with both optional. Structured output enforces the
# schema at decode time but not a post-parse validator: with both optional,
# haiku-5.5 emitted `{"type": "recovery"}` with neither, and the whole parse
# failed. The variants share the `type` tag; Pydantic's smart union picks the
# one whose required field is present.
#
# Field ORDER is part of the decoding grammar: keep distance/duration before
# pace. With pace first (via a shared base class), haiku wrote the distance
# and then had no slot left for the pace — every paced run came back paceless.
class RunStep(BaseModel):
    """A running segment by distance. Omit 'pace' to model an easy recovery jog."""

    type: Literal["run"]
    distance: int = Field(ge=1, le=MAX_DISTANCE_M, description="Distance in metres")
    pace: Optional[str] = Field(None, pattern=PACE, description=PACE_DESC)
    pace_slow: Optional[str] = Field(None, pattern=PACE, description=PACE_SLOW_DESC)

    _pad = field_validator("pace", "pace_slow")(_pad_pace)
    _range = model_validator(mode="after")(_check_pace_range)


class TimedRunStep(BaseModel):
    """A running segment given by time ("15 мин"); never converted to distance."""

    type: Literal["run"]
    duration: int = Field(ge=1, le=MAX_DURATION_S, description="Duration in seconds")
    pace: Optional[str] = Field(None, pattern=PACE, description=PACE_DESC)
    pace_slow: Optional[str] = Field(None, pattern=PACE, description=PACE_SLOW_DESC)

    _pad = field_validator("pace", "pace_slow")(_pad_pace)
    _range = model_validator(mode="after")(_check_pace_range)


class RestStep(BaseModel):
    """Passive or standing rest."""

    type: Literal["rest"]
    rest: int = Field(ge=1, le=MAX_REST_S, description="Rest duration in seconds")


class RecoveryStep(BaseModel):
    """An active recovery jog segment without a target pace."""

    type: Literal["recovery"]
    distance: int = Field(ge=1, le=MAX_DISTANCE_M, description="Distance in metres")


class TimedRecoveryStep(BaseModel):
    """An active recovery jog given by time, without a target pace."""

    type: Literal["recovery"]
    duration: int = Field(ge=1, le=MAX_DURATION_S, description="Duration in seconds")


class BreakStep(BaseModel):
    """A non-running drill done in place between runs (strength, plyometrics,
    mobility). It has no distance and no reliable duration, so on the watch it
    ends on the lap button; `name` is what the athlete sees on screen."""

    type: Literal["break"]
    name: str = Field(max_length=100, description="The exercise, e.g. '30 frog jumps'")


# A repeat group only ever holds leaf steps — workouts never nest a repeat inside
# a repeat. Keeping `steps` non-recursive is also required by Anthropic structured
# outputs, which reject self-referencing schemas (`RepeatGroup -> RepeatGroup`).
LeafElement = Union[RunStep, TimedRunStep, RestStep, RecoveryStep, TimedRecoveryStep, BreakStep]


class RepeatGroup(BaseModel):
    """A grouping that repeats its internal steps in order."""

    type: Literal["repeat"]
    repeat: int = Field(ge=1, le=MAX_REPEAT, description="Number of repetitions")
    steps: List[LeafElement] = Field(description="Ordered list of steps to repeat")


# Plain union -> JSON Schema `anyOf` (OpenAI structured outputs rejects the
# `oneOf` that a Pydantic discriminated union would emit). The distinct
# Literal `type` tags still let Pydantic select the right variant on parse.
Element = Union[RunStep, TimedRunStep, RestStep, RecoveryStep, TimedRecoveryStep, BreakStep, RepeatGroup]


class Segment(BaseModel):
    """Optional easy running before/after the main set (lap-based if distance and duration omitted)."""

    distance: Optional[int] = Field(None, ge=1, le=MAX_DISTANCE_M, description="Distance in metres")
    duration: Optional[int] = Field(None, ge=1, le=MAX_DURATION_S, description="Duration in seconds (for a section given by time)")
    pace: Optional[str] = Field(None, pattern=PACE, description="Optional target pace (min:sec per km)")
    pace_slow: Optional[str] = Field(None, pattern=PACE, description=PACE_SLOW_DESC)

    _pad = field_validator("pace", "pace_slow")(_pad_pace)
    _range = model_validator(mode="after")(_check_pace_range)


class Workout(BaseModel):
    name: str = Field(max_length=100, description="Friendly workout name (e.g. '10×300/100/200 + rest')")
    warmup: Optional[Segment] = None
    intervals: List[Element] = Field(description="Main workout segment of run/rest steps or repeat groups")
    cooldown: Optional[Segment] = None
