from fastapi.testclient import TestClient

from demo.account_store import AccountStore
import demo.web as web


def test_accounts_are_isolated(tmp_path, monkeypatch):
    original = (dict(web.datasets), dict(web.workflows), dict(web.owners), set(web.loaded_users), web.accounts, web.AUTH_ENABLED)
    web.datasets.clear(); web.workflows.clear(); web.owners.clear(); web.loaded_users.clear()
    monkeypatch.setattr(web, 'accounts', AccountStore(f'sqlite:///{tmp_path / "accounts.db"}'))
    monkeypatch.setattr(web, 'AUTH_ENABLED', True)
    monkeypatch.setenv('COOKIE_SECURE', '0')
    try:
        with TestClient(web.app) as first, TestClient(web.app) as second:
            assert first.get('/workspace').status_code == 401
            assert first.post('/auth/register', json={'email': 'first@example.com', 'password': 'password-one'}).status_code == 204
            loaded = first.post('/sample?profile=boundary').json()
            assert first.get('/workspace').json()['dataset']['dataset_id'] == loaded['dataset_id']

            assert second.post('/auth/register', json={'email': 'second@example.com', 'password': 'password-two'}).status_code == 204
            assert second.get('/workspace').json()['dataset'] is None
            assert second.get(f'/planning/{loaded["dataset_id"]}').status_code == 404

            assert first.post('/auth/logout').status_code == 204
            assert first.get('/workspace').status_code == 401
    finally:
        web.datasets.clear(); web.datasets.update(original[0])
        web.workflows.clear(); web.workflows.update(original[1])
        web.owners.clear(); web.owners.update(original[2])
        web.loaded_users.clear(); web.loaded_users.update(original[3])
        web.accounts, web.AUTH_ENABLED = original[4], original[5]


def test_password_and_workspace_round_trip(tmp_path):
    store = AccountStore(f'sqlite:///{tmp_path / "store.db"}')
    user = store.register('USER@example.com', 'long-enough-password')
    assert store.authenticate('user@example.com', 'wrong-password') is None
    assert store.authenticate('user@example.com', 'long-enough-password')['id'] == user['id']
    token = store.create_session(user['id'])
    assert store.session_user(token)['email'] == 'user@example.com'
    store.save_workspace(user['id'], '{"datasets":{},"workflows":{}}')
    assert store.load_workspace(user['id']).startswith('{"datasets"')
    store.delete_session(token)
    assert store.session_user(token) is None
