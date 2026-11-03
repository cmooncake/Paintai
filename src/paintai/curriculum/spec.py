import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from ..models import Model, PainterId, Text


class Lesson(Model):
    id: PainterId
    name: Text
    task: Text = Field(max_length=600)
    skills: list[Text] = Field(min_length=1, max_length=8)
    pass_score: float = Field(default=6.5, ge=1, le=10, allow_inf_nan=False)
    depends_on: list[PainterId] = Field(default_factory=list)


class Stage(Model):
    id: PainterId
    name: Text
    goals: list[Text] = Field(default_factory=list, max_length=8)
    lessons: list[Lesson] = Field(min_length=1, max_length=20)


class CurriculumSpec(Model):
    schema_version: Literal[1] = 1
    id: PainterId
    title: Text
    stages: list[Stage] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def validate_ids(self):
        stages, lessons = set(), set()
        for stage in self.stages:
            if stage.id in stages:
                raise ValueError(f'Duplicate stage id: {stage.id}')
            stages.add(stage.id)
            for lesson in stage.lessons:
                if lesson.id in lessons:
                    raise ValueError(f'Duplicate lesson id: {lesson.id}')
                # Sequential curriculum: dependencies must already be reachable.
                if not set(lesson.depends_on).issubset(lessons):
                    raise ValueError(f'{lesson.id}: dependencies must name earlier lessons')
                lessons.add(lesson.id)
        return self


def load_spec(path: Path | None = None) -> CurriculumSpec:
    path = path or Path(__file__).with_name('default_curriculum.yaml')
    if path.stat().st_size > 100_000:
        raise ValueError('Curriculum file exceeds 100 KB')
    try:
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        raise ValueError('Invalid curriculum YAML syntax') from None
    return CurriculumSpec.model_validate(value)


def lesson_key(spec: CurriculumSpec, stage: Stage, lesson: Lesson) -> str:
    # Reordering retains progress; changed requirements require fresh evidence.
    value = [spec.id, stage.id, stage.goals, lesson.model_dump()]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def generate_tasks(lesson: Lesson, count: int = 3, *, goal: str = '', practice=None) -> list[str]:
    variations = ['Use a centered focal point.', 'Use an off-center focal point.',
                  'Use a close view with a simple background.', 'Use a wider view.',
                  'Explore a different light direction.', 'Try a balanced asymmetric composition.']
    default_example = any(item.id == lesson.id and item.task == lesson.task
                          for stage in load_spec().stages for item in stage.lessons)
    base = lesson.task
    if goal:
        base = (f'Choose a scene for this artistic direction: {goal}.' if default_example
                else f'{lesson.task} Artistic growth direction: {goal}.')
    requirements = ''
    if practice:
        difficulty = {'gentle': 'Use a simple subject and a minimal background.',
                      'standard': '', 'challenge': 'Use more complex spatial layers and lighting.'}
        requirements = ' '.join(filter(None, [practice.focus, practice.instructions,
                                              difficulty[practice.difficulty]]))
    return [f'{base} Practice: {", ".join(lesson.skills)}. {variation}'
            + (f' Additional art requirements: {requirements}' if requirements else '')
            for variation in variations[:count]]


def direction_curriculum(goal: str) -> CurriculumSpec:
    """Inspectable goal-based exercise template, no hidden model call or auto-save."""
    from ..models import Painter
    goal = Painter(painter_id='preview', name='preview', growth_goal=goal).growth_goal
    if not goal:
        raise ValueError('请先填写长期成长目标。')
    spec = load_spec()
    spec.id = 'direction_' + hashlib.sha256(goal.encode()).hexdigest()[:12]
    spec.title = '我的成长课程'
    for stage in spec.stages:
        stage.goals = [*stage.goals, goal]
        for lesson in stage.lessons:
            lesson.task = f'Create a painting for this artistic direction: {goal}. Focus on {", ".join(lesson.skills)}.'
    return CurriculumSpec.model_validate(spec.model_dump())
