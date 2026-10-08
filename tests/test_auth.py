import pytest
from pydantic import ValidationError

from backend.schemas import PasswordIn, UserIn


@pytest.mark.parametrize('length,valid', [(9, False), (10, True), (200, True), (201, False)])
@pytest.mark.parametrize('model', ['change', 'create', 'bootstrap'])
def test_password_length_boundaries(model, length, valid, monkeypatch):
    values = {'password': 'a' * length}
    if model == 'change':
        build = lambda: PasswordIn(current_password='old-password', **values)
    elif model == 'create':
        build = lambda: UserIn(username='test-user', role='viewer', **values)
    else:
        # Import the settings module without needing a deployment .env in CI.
        for name, value in {'DB_PASSWORD': 'test', 'ENCRYPTION_KEY': 'test',
                            'SESSION_SECRET': 's' * 32, 'ADMIN_PASSWORD': 'b' * 10}.items():
            monkeypatch.setenv(name, value)
        from backend.settings import Settings
        build = lambda: Settings(_env_file=None, db_password='test', encryption_key='test',
                                 session_secret='s' * 32, admin_password=values['password'])
    if valid:
        build()
    else:
        with pytest.raises(ValidationError):
            build()
