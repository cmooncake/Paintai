import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paintai.agent import PainterAgent
from paintai.api import create_app
from paintai.config import Settings
from paintai.curriculum.spec import direction_curriculum, generate_tasks, load_spec
from paintai.jobs import Jobs, QueueConflict
from paintai.learning import Learning
from paintai.memory import Memory
from paintai.models import Painter, PracticeOptions
from paintai.providers import MockProvider
from paintai.storage import Store


def test_requirement_snapshot_reaches_both_models_and_memory(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id='artist', name='Artist', growth_goal='水彩山水'))

    class Spy(MockProvider):
        def plan(self, topic, painter, *, context=None, requirements=None):
            self.planned = requirements
            return super().plan(topic, painter)

        def evaluate(self, image, topic, painter, plan, *, requirements=None):
            self.evaluated = requirements
            self.painter = painter
            return super().evaluate(image, topic, painter, plan)

    spy = Spy()
    agent = PainterAgent(store, Settings(data_dir=tmp_path), spy, spy, spy)
    learning = Learning(store)
    snapshot = learning.snapshot('artist', options=PracticeOptions(count=1, instructions='远山冷色', focus='空间层次'))
    assert '水彩山水' in snapshot['tasks'][0] and '远山冷色' in snapshot['tasks'][0]
    result = learning.run(agent, 'artist', snapshot)
    brief = snapshot['art_brief']
    assert spy.planned == spy.evaluated == brief == result['art_brief']
    assert Memory(store).list('artist')[0]['art_brief'] == brief
    record = learning.rounds('artist')[0]
    assert record['average'] == result['critique']['overall'] and not record['passed']
    store.update_goal('artist', '油画人像')
    agent.evaluate('artist', result['id'], force=True)
    assert spy.evaluated == brief and spy.painter.growth_goal == '水彩山水'
    assert Memory(store).list('artist')[0]['art_brief'] == brief


def test_goal_changes_progress_without_erasing_history(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id='artist', name='Artist'))
    learning = Learning(store)

    class Evidence:
        def create(self, *args, **kwargs):
            return {'id': 'test', 'critique': {'overall': 9}, 'simulated': False}

    learning.run(Evidence(), 'artist')
    assert learning.progress('artist')['percent'] > 0
    store.update_goal('artist', 'mountains')
    assert learning.progress('artist')['percent'] == 0
    learning.run(Evidence(), 'artist', learning.snapshot('artist', options=PracticeOptions(count=2)))
    assert learning.progress('artist')['percent'] == 0
    assert len(learning.rounds('artist')) == 2
    store.update_goal('artist', '')
    assert learning.progress('artist')['percent'] > 0


def test_api_preview_validation_and_idempotency(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    with TestClient(app) as client:
        painter = client.post('/api/v1/painters', json={'name':'test', 'growth_goal':'水彩山水'}).json()
        root = '/api/v1/painters/' + painter['painter_id']
        options = {'count':1, 'instructions':'近景暖色', 'difficulty':'gentle'}
        preview = client.post(root+'/train/preview', json=options)
        assert preview.status_code == 200 and len(preview.json()['tasks']) == 1
        assert client.get('/api/v1/jobs').json() == []
        assert client.post(root+'/train/preview', json={'count':7}).status_code == 422
        assert client.post(root+'/goal', json={'growth_goal':'x'*401}).status_code == 422
        headers = {'Idempotency-Key':'direction-test-1'}
        queued = client.post(root+'/train', json=options, headers=headers)
        assert queued.status_code == 202 and queued.json()['training']['total'] == 1
        assert client.post(root+'/train', json=options, headers=headers).json()['id'] == queued.json()['id']
        assert client.post(root+'/train', json={'count':2}, headers=headers).status_code == 409
        assert client.post(root+'/goal', json={'growth_goal':'changed'}).status_code == 409


def test_course_draft_and_create_freeze(tmp_path):
    lesson = load_spec().stages[0].lessons[0]
    lesson.task = 'Use exactly two colors and show a bridge'
    assert lesson.task in generate_tasks(lesson, goal='watercolor')[0]
    spec = direction_curriculum('学习山水')
    assert all('学习山水' in lesson.task for stage in spec.stages for lesson in stage.lessons)
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id='artist', name='Artist', growth_goal='mountains'))
    jobs = Jobs(store, Settings(data_dir=tmp_path))
    job = jobs.submit('artist', 'create', 'scene', 'create-key')
    assert json.loads(job['payload'])['painter_snapshot']['growth_goal'] == 'mountains'
    with pytest.raises(QueueConflict):
        jobs.submit('artist', 'create', 'different', 'create-key')
    with pytest.raises(ValidationError):
        PracticeOptions(count=0)
