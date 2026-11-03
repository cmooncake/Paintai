import pytest
from fastapi.testclient import TestClient

from paintai.api import create_app
from paintai.config import Settings
from paintai.jobs import Jobs, Worker


@pytest.fixture
def api(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), start_worker=False)
    with TestClient(app) as client:
        painter = client.post('/api/v1/painters', json={'name': '网页画师'}).json()
        yield client, app, painter['painter_id']


def submit(client, painter, key='request-0001', topic='A golden forest'):
    return client.post('/api/v1/creations', json={'painter_id': painter, 'topic': topic},
                       headers={'Idempotency-Key': key})


def test_http_creation_image_and_reevaluation(api):
    client, app, painter = api
    response = submit(client, painter)
    assert response.status_code == 202
    job = response.json()
    assert job['status'] == 'queued'
    assert 'settings' not in job and 'request_key' not in job
    assert app.state.worker.run_one()
    completed = client.get('/api/v1/jobs/' + job['id']).json()
    assert completed['status'] == 'succeeded'
    url = f"/api/v1/painters/{painter}/creations/{completed['entry_id']}"
    entry = client.get(url).json()
    assert entry['status'] == 'complete'
    assert entry['simulated']
    assert entry['critique']['overall'] == 5
    assert not {'config', 'image_path', 'error'} & entry.keys()
    assert not {'config', 'image_sha256'} & entry['evaluation_history'][0].keys()
    image = client.get(entry['image_url'])
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert client.post(url + '/evaluate', headers={'Idempotency-Key': 'reevaluate-001'}).status_code == 202
    assert app.state.worker.run_one()
    again = client.get(url).json()
    assert len(again['evaluation_history']) == 2
    assert client.get(again['image_url']).content == image.content
    assert client.get(f'/api/v1/painters/{painter}/creations?status=complete').json()['total'] == 1
    assert client.get(f'/api/v1/painters/{painter}/creations?status=failed').json()['total'] == 0
    assert client.get(f'/api/v1/painters/{painter}/creations?offset=1').json()['items'] == []


def test_idempotency_before_and_after_completion_and_persistence(api):
    client, app, painter = api
    first = submit(client, painter).json()
    assert submit(client, painter).json()['id'] == first['id']
    assert submit(client, painter, topic='Different topic').status_code == 409
    assert submit(client, painter, key='another-request').status_code == 409
    app.state.worker.run_one()
    assert submit(client, painter).json()['id'] == first['id']
    assert len(app.state.store.gallery(painter)) == 1
    restored = create_app(Settings(data_dir=app.state.store.root), start_worker=False)
    with TestClient(restored) as reopened:
        assert reopened.get('/api/v1/jobs/' + first['id']).json()['status'] == 'succeeded'
        assert submit(reopened, painter).json()['id'] == first['id']


def test_validation_origin_and_painter_isolation(api):
    client, app, painter = api
    assert client.post('/api/v1/painters', json={'name': '  '}).status_code == 422
    assert client.post('/api/v1/painters', json={'name': 'Blocked'},
                       headers={'Origin': 'https://other.example'}).status_code == 403
    assert client.post('/api/v1/creations', json={'painter_id': painter, 'topic': 'x'}).status_code == 422
    assert submit(client, painter, topic=' ').status_code == 422
    assert submit(client, 'missing').status_code == 404
    submit(client, painter)
    app.state.worker.run_one()
    other = client.post('/api/v1/painters', json={'name': 'Other'}).json()['painter_id']
    entry = app.state.store.gallery(painter)[0]
    assert client.get(f"/api/v1/painters/{other}/creations/{entry['id']}/image").status_code == 404
    assert client.get(f'/api/v1/painters/{other}/creations').json()['items'] == []
    entry['image_path'] = '../outside.png'
    app.state.store.save(entry)
    assert client.get(f"/api/v1/painters/{painter}/creations/{entry['id']}/image").status_code == 404
    assert client.get('/api/v1/system').json()['ready'] is True


def test_restart_interrupts_running_but_retains_queued(api):
    client, app, painter = api
    first = submit(client, painter).json()
    assert app.state.jobs.claim()['id'] == first['id']
    other = client.post('/api/v1/painters', json={'name': 'Other'}).json()['painter_id']
    queued = submit(client, other, key='queued-other').json()
    reopened = Jobs(app.state.store, Settings(data_dir=app.state.store.root))
    reopened.recover()
    assert reopened.get(first['id'])['status'] == 'interrupted'
    assert reopened.get(queued['id'])['status'] == 'queued'
    assert Worker(reopened).run_one()
    assert app.state.store.gallery(painter) == []


def test_one_worker_per_data_directory(api):
    _, app, _ = api
    worker = app.state.worker
    worker.start()
    try:
        with pytest.raises(RuntimeError, match='Another Paintai API'):
            Worker(app.state.jobs).start()
    finally:
        worker.stop()


def test_provider_failure_has_recoverable_job_and_safe_error(api, monkeypatch):
    client, app, painter = api
    def fail(*args):
        raise RuntimeError('secret-file-path-and-token')
    monkeypatch.setattr('paintai.jobs.make_provider', fail)
    job = submit(client, painter).json()
    app.state.worker.run_one()
    result = client.get('/api/v1/jobs/' + job['id']).json()
    assert result['status'] == 'failed'
    assert 'secret' not in result['error']
    assert submit(client, painter, key='retry-after-fail').status_code == 202
