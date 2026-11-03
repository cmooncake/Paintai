from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PainterId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]
Score = Annotated[float, Field(ge=1, le=10, allow_inf_nan=False)]
DirectionText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Painter(Model):
    painter_id: PainterId
    name: Text
    style: Text = "自由风格"
    growth_goal: DirectionText = ''
    schema_version: int = 1


class PracticeOptions(Model):
    instructions: DirectionText = ''
    focus: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ''
    difficulty: Literal['gentle', 'standard', 'challenge'] = 'standard'
    count: int = Field(default=3, ge=1, le=6)

    @property
    def assessment(self):
        return self.count == 3 and self.difficulty == 'standard' and not self.instructions and not self.focus


class Perception(Model):
    subject: Text
    scene: Text = Field(description="Physical setting and surroundings, not the art style")
    mood: Text
    constraints: list[Text]


class Plan(Model):
    perception: Perception
    composition: Text = Field(
        description="Concrete placement of foreground, background and focal point; not just a style name"
    )
    palette: Text = Field(
        description="Specific colors or ink tones, including light and dark contrast"
    )
    prompt: Text = Field(
        description="Executable image prompt combining subject, setting, layout, palette and target style"
    )


class Scores(Model):
    topic: Score
    technique: Score
    composition: Score
    color: Score
    style: Score
    creativity: Score


class Critique(Model):
    scores: Scores
    feedback: Text
    improvements: list[Text] = Field(min_length=1)

    @computed_field
    @property
    def overall(self) -> float:
        """M0 rubric v1: all six dimensions active, equally weighted."""
        return round(sum(self.scores.model_dump().values()) / 6, 3)
