from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paintai.agent import PainterAgent
from paintai.api import create_app
from paintai.config import Settings
from paintai.curriculum.spec import CurriculumSpec, generate_tasks, load_spec
from paintai.learning import Learning, LearningError
from paintai.memory import Memory
from paintai.models import Painter
from paintai.providers import MockProvider
from paintai.storage import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    store.add_painter(Painter(painter_id='alice', name='Alice'))
    store.add_painter(Painter(painter_id='bob', name='Bob'))
    mock = MockProvider()
    return store, PainterAgent(store, Settings(data_dir=tmp_path), mock, mock, mock)


def test_default_spec_tasks_and_invalid_definitions():
    spec = load_spec()
    assert len(spec.stages) == 2
    for stage in spec.stages:
        for lesson in stage.lessons:
            tasks = generate_tasks(lesson)
            assert len(set(tasks)) == 3
            assert all(all(skill in topic for skill in lesson.skills) for topic in tasks)
    for mutate in ('duplicate_stage', 'duplicate_lesson', 'missing_dependency', 'cycle', 'score'):
        value = spec.model_dump()
        lessons = value['stages'][0]['lessons']
        if mutate == 'duplicate_stage':
            value['stages'][1]['id'] = value['stages'][0]['id']
        elif mutate == 'duplicate_lesson':
            lessons[1]['id'] = lessons[0]['id']
        elif mutate == 'missing_dependency':
            lessons[0]['depends_on'] = ['missing']
        elif mutate == 'cycle':
            lessons[0]['depends_on'] = [lessons[1]['id']]
        else:
            lessons[0]['pass_score'] = 11
        with pytest.raises(ValidationError):
            CurriculumSpec.model_validate(value)


class EvidenceAgent:
    """Synthetic evidence for engine tests; never used by the runtime."""
    def __init__(self, score=8, fail_at=None):
        self.score, self.fail_at, self.count = score, fail_at, 0

    def create(self, topic, painter_id, **kwargs):
        self.count += 1
        if self.count == self.fail_at:
            raise RuntimeError('fixture failure')
        return {'id': f'fixture-{self.count}', 'critique': {'overall': self.score}, 'simulated': False}


def test_three_results_gate_advancement_restart_and_isolation(setup):
    store, _ = setup
    learning = Learning(store)
    assert learning.progress('alice')['current']['lesson_id'] == 'simple_shapes'
    with pytest.raises(LearningError):
        learning.snapshot('alice', 'depth_layers')
    learning.run(EvidenceAgent(score=4), 'alice')
    assert learning.progress('alice')['completed'] == []
    for _ in range(3):
        learning.run(EvidenceAgent(), 'alice')
    reopened = Learning(Store(store.root))
    progress = reopened.progress('alice')
    assert progress['completed'] == ['simple_shapes', 'light_shadow', 'limited_palette']
    assert progress['current']['lesson_id'] == 'focal_point'
    assert progress['percent'] == 50
    assert reopened.progress('bob')['percent'] == 0
    learning.run(EvidenceAgent(score=3), 'alice', learning.snapshot('alice', 'simple_shapes'))
    assert learning.progress('alice')['percent'] == 50  # A failed review does not erase prior pass.


def test_failed_and_simulated_rounds_never_pass(setup):
    store, agent = setup
    learning = Learning(store)
    learning.run(agent, 'alice')
    record = learning.rounds('alice')[0]
    assert record['status'] == 'complete' and record['simulated'] and not record['passed']
    assert len(record['results']) == 3
    with pytest.raises(RuntimeError):
        learning.run(EvidenceAgent(fail_at=2), 'alice')
    latest = learning.rounds('alice')[0]
    assert latest['status'] == 'failed' and len(latest['results']) == 1
    assert learning.progress('alice')['percent'] == 0
    latest['status'] = 'running'
    learning.save_round(latest)
    learning.recover()
    assert learning.rounds('alice')[0]['status'] == 'interrupted'


def test_stable_ids_preserve_progress_but_changed_requirements_do_not(setup):
    store, _ = setup
    learning = Learning(store)
    spec = load_spec()
    learning.assign('alice', spec)
    learning.run(EvidenceAgent(), 'alice')
    changed = deepcopy(spec)
    changed.title = 'Renamed curriculum'
    learning.assign('alice', changed)
    assert learning.progress('alice')['percent'] == 17
    changed.stages[0].lessons[0].pass_score = 9
    learning.assign('alice', changed)
    assert learning.progress('alice')['percent'] == 0
    assert len(learning.rounds('alice')) == 1
    learning.assign('alice', spec)
    assert learning.progress('alice')['percent'] == 17


def test_memory_deduplication_simulation_isolation_and_actual_injection(setup):
    store, agent = setup
    entry = agent.create('A red apple on a table', 'alice')
    memory = Memory(store)
    assert len(memory.list('alice')) == 1
    assert memory.search('alice', 'red apple', []) == []
    # Synthetic non-simulated evidence enables retrieval without a real model in unit tests.
    entry['simulated'] = False
    store.save(entry)
    memory.record(entry)
    memory.record(entry)
    assert len(memory.list('alice')) == 1
    assert memory.search('bob', 'red apple', []) == []
    assert memory.search('alice', 'spaceship', []) == []
    assert memory.search('alice', 'red apple', [])[0]['entry_id'] == entry['id']
    class SpyPlanner(MockProvider):
        def plan(self, topic, painter, *, context=None):
            self.context = context
            return super().plan(topic, painter)
    spy = SpyPlanner()
    agent.planner = spy
    second = agent.create('A red apple on a table at sunset', 'alice')
    assert spy.context[0]['entry_id'] == entry['id']
    assert second['memory_used'] == spy.context
    assert 'score' not in spy.context[0]  # Critic scores are not planning instructions.
    agent.evaluate('alice', second['id'], force=True)
    assert len(memory.list('alice')) == 2
    assert memory.list('alice')[0]['evaluation_count'] == 2


def test_training_http_queue_idempotency_locked_lessons_and_legacy_jobs(setup):
    store, _ = setup
    app = create_app(Settings(data_dir=store.root), start_worker=False)
    with TestClient(app) as client:
        base = '/api/v1/painters/alice'
        header = {'Idempotency-Key': 'training-key-001'}
        response = client.post(base + '/train', json={}, headers=header)
        assert response.status_code == 202
        assert response.json()['training']['total'] == 3
        assert client.post(base + '/train', json={}, headers=header).json()['id'] == response.json()['id']
        assert client.post(base + '/curriculum', json=load_spec().model_dump()).status_code == 409
        app.state.worker.run_one()
        assert client.get('/api/v1/jobs/' + response.json()['id']).json()['status'] == 'succeeded'
        assert client.post(base + '/train', json={}, headers=header).json()['id'] == response.json()['id']
        progress = client.get(base + '/learning').json()
        assert progress['percent'] == 0 and len(progress['history'][0]['results']) == 3
        assert len(client.get(base + '/memory').json()['items']) == 3
        assert client.post(base + '/memory/sync', json={}).json()['processed'] == 3
        assert len(client.get(base + '/memory').json()['items']) == 3
        assert client.post(base + '/train', json={'lesson_id':'depth_layers'},
                           headers={'Idempotency-Key':'locked-lesson-1'}).status_code == 409
        invalid = load_spec().model_dump()
        invalid['stages'][0]['lessons'][0]['pass_score'] = 20
        assert client.post(base + '/curriculum', json=invalid).status_code == 422
        detail = client.get(base + '/creations/' + progress['history'][0]['results'][0]['entry_id']).json()
        assert detail['learning']['exercise'] == 1


def test_curriculum_file_packaged_and_cli_mock_training(tmp_path, capsys):
    from paintai.cli import main
    args = ['--offline', '--data-dir', str(tmp_path)]
    assert main([*args, 'painter', 'add', 'demo', '--name', 'Demo']) == 0
    assert main([*args, 'train', '--painter', 'demo', '--spec', 'default_curriculum', '--lessons', '2']) == 0
    assert len(Learning(Store(tmp_path)).rounds('demo')) == 1  # Stops on unpassed lesson.
    assert main([*args, 'progress', '--painter', 'demo']) == 0


def test_memory_can_be_disabled_and_injection_is_bounded(setup):
    store, agent = setup
    entry = agent.create('apple', 'alice')
    entry['simulated'] = False
    entry['critique']['improvements'] = ['A' * 1000] * 4
    entry['topic'] = 'apple ' * 500
    agent.memory.record(entry)
    second = agent.create('apple', 'alice')
    assert len(second['memory_used']) == 1
    assert len(second['memory_used'][0]['topic']) <= 160
    assert len(second['memory_used'][0]['rules']) == 2
    assert all(len(rule) <= 160 for rule in second['memory_used'][0]['rules'])
    agent.settings.memory_top_k = 0
    assert agent.create('apple', 'alice')['memory_used'] == []


def test_m0_jobs_database_migrates_without_losing_existing_jobs(setup):
    store, _ = setup
    from paintai.jobs import Jobs
    with store.connect() as db:
        db.execute('''CREATE TABLE jobs (
            id TEXT PRIMARY KEY, painter_id TEXT NOT NULL, kind TEXT NOT NULL,
            topic TEXT NOT NULL, entry_id TEXT, status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            request_key TEXT UNIQUE NOT NULL, settings TEXT NOT NULL)''')
        db.execute("INSERT INTO jobs VALUES ('old','alice','create','x',NULL,'succeeded',NULL,"
                   "'2026','2026','old-key',?)", (Settings(data_dir=store.root).model_dump_json(),))
    jobs = Jobs(store, Settings(data_dir=store.root))
    assert jobs.get('old')['payload'] == '{}'
    assert jobs.get('old')['status'] == 'succeeded'
    assert jobs.submit('alice', 'train', '', 'new-train-key')['kind'] == 'train'


def test_failed_round_links_failed_artwork(setup):
    from paintai.agent import CreationFailed
    store, agent = setup
    class BrokenGenerator:
        def generate(self, plan):
            raise RuntimeError('fixture')
    agent.generator = BrokenGenerator()
    learning = Learning(store)
    with pytest.raises(CreationFailed):
        learning.run(agent, 'alice')
    record = learning.rounds('alice')[0]
    assert store.get('alice', record['failed_entry_id'])['status'] == 'creation_failed'
    assert not record['passed'] and record['results'] == []
