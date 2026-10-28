import io
import json
from pathlib import Path

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash
from app import create_app, classify


@pytest.fixture
def client(tmp_path):
    app = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': 'test-session-secret',
                      'ADMIN_PASSWORD_HASH': generate_password_hash('test-password'), 'SESSION_COOKIE_SECURE': False})
    return app.test_client()


def image(color='green'):
    stream = io.BytesIO()
    Image.new('RGB', (960, 960), color).save(stream, 'PNG')
    stream.seek(0)
    return stream


def login(client):
    result = client.post('/api/login', json={'password': 'test-password'})
    assert result.status_code == 200
    assert 'HttpOnly' in result.headers['Set-Cookie']
    return {'X-CSRF-Token': result.json['csrf']}


@pytest.mark.parametrize('name,expected', [
    ('demo_user_10001.png', 'demo_user'), ('sample-user_10002.png', 'sample-user'),
    ('my_mcid_001.JPG', 'my_mcid'), ('newPlayer_123.png', 'newPlayer'),
    ('example_user_10003_negative_film.png', 'example_user'), ('example_user_10004_aged.png', 'example_user'),
    ('example_user_10004_negative.png', 'example_user'), ('holiday.png', None),
    ('player_123_other.png', None), ('player_.png', None), ('player_123.svg', None),
])
def test_classification(name, expected):
    assert classify(name) == expected


def test_public_read_and_admin_boundary(client):
    assert client.get('/').status_code == 200
    assert client.get('/catalog.json').json == {'photos': [], 'aliases': {}, 'titles': {}}
    assert client.get('/api/session').json['authenticated'] is False
    assert client.post('/api/photos', data={'file': (image(), 'player_1.png')}).status_code == 401
    assert client.delete('/api/photos/abc').status_code == 401
    headers = login(client)
    assert client.post('/api/photos', data={'file': (image(), 'player_1.png')}).status_code == 403
    assert client.post('/api/logout', headers=headers).status_code == 200
    assert client.post('/api/photos', headers=headers).status_code == 401


def test_upload_thumbnail_catalog_duplicate_and_delete(client):
    headers = login(client)
    result = client.post('/api/photos', data={'file': (image(), 'demo_user_10001.png')}, headers=headers)
    assert result.status_code == 201
    duplicate = client.post('/api/photos', data={'file': (image(), 'demo_user_10001.png')}, headers=headers)
    assert duplicate.json['duplicate'] is True
    catalog = client.get('/catalog.json')
    assert 's-maxage=60' in catalog.headers['Cache-Control']
    assert 'Set-Cookie' not in catalog.headers
    photo = catalog.json['photos'][0]
    assert photo['album'] == 'demo_user'
    thumb = client.get('/media/' + photo['thumb'])
    assert 's-maxage=60' in thumb.headers['Cache-Control']
    im = Image.open(io.BytesIO(thumb.data))
    assert im.size == (320, 320) and im.format == 'WEBP'
    original = client.get('/media/' + photo['original'])
    assert Image.open(io.BytesIO(original.data)).size == (960, 960)
    assert client.delete('/api/photos/' + photo['id']).status_code == 403
    deleted = client.delete('/api/photos/' + photo['id'], headers=headers)
    assert deleted.status_code == 200 and len(deleted.json['purge_paths']) == 3
    assert client.get('/catalog.json').json['photos'] == []
    assert client.get('/media/' + photo['thumb']).status_code == 404


def test_unclassified_is_accepted_and_invalid_rejected(client):
    headers = login(client)
    assert client.post('/api/photos', data={'file': (image(), '旅行.png')}, headers=headers).status_code == 201
    assert client.get('/catalog.json').json['photos'][0]['album'] is None
    assert client.post('/api/photos', data={'file': (io.BytesIO(b'not an image'), 'fake.png')}, headers=headers).status_code == 400
    assert client.post('/api/photos', data={'file': (io.BytesIO(b'<svg></svg>'), 'image.svg')}, headers=headers).status_code == 400
    assert client.get('/media/../gallery.db').status_code == 404


def test_login_protection_and_cache(client):
    assert client.post('/api/login', json={'password': 'test-password'}, headers={'Origin': 'https://other.example'}).status_code == 403
    for _ in range(10):
        response = client.post('/api/login', json={'password': 'wrong'})
        assert response.status_code == 401
        assert response.headers['Cache-Control'] == 'private, no-store'
    assert client.post('/api/login', json={'password': 'test-password'}).status_code == 429


def test_without_admin_configuration(tmp_path):
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': None, 'ADMIN_PASSWORD_HASH': None}).test_client()
    assert client.get('/').status_code == 200
    assert client.post('/api/login', json={'password': 'anything'}).status_code == 503


def test_saved_admin_configuration_and_secure_cookie(tmp_path):
    (tmp_path / 'admin.json').write_text(json.dumps({
        'password_hash': generate_password_hash('saved-password'),
        'session_secret': 'saved-test-secret'
    }))
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path),
                         'SECRET_KEY': None, 'ADMIN_PASSWORD_HASH': None,
                         'SESSION_COOKIE_SECURE': True}).test_client()
    assert client.get('/api/session').json['configured'] is True
    response = client.post('/api/login', json={'password': 'saved-password'}, base_url='https://example.test')
    assert response.status_code == 200
    assert 'Secure' in response.headers['Set-Cookie']


def test_malformed_login(client):
    assert client.post('/api/login', json=['invalid']).status_code == 400


def test_first_setup_and_restart(tmp_path):
    config = {'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': None,
              'ADMIN_PASSWORD_HASH': None, 'SESSION_COOKIE_SECURE': False}
    first = create_app(config).test_client()
    other_worker = create_app(config).test_client()
    token = (tmp_path / '.setup-token').read_text()
    assert first.get('/api/session').json['configured'] is False
    assert first.post('/api/setup', json={'token': 'incorrect', 'password': 'new-test-password'}).status_code == 403
    assert first.post('/api/setup', json={'token': token, 'password': 'short'}).status_code == 400
    assert first.post('/api/setup', json={'token': token, 'password': 'new-test-password'}, headers={'Origin': 'https://other.example'}).status_code == 403
    result = first.post('/api/setup', json={'token': token, 'password': 'new-test-password'})
    assert result.status_code == 201
    assert result.headers['Cache-Control'] == 'private, no-store'
    assert first.get('/api/session').json['authenticated'] is True
    assert not (tmp_path / '.setup-token').exists()
    assert (tmp_path / 'admin.json').stat().st_mode & 0o777 == 0o600
    assert 'new-test-password' not in (tmp_path / 'admin.json').read_text()
    assert other_worker.get('/api/session').json['configured'] is True
    assert other_worker.post('/api/setup', json={'token': token, 'password': 'overwrite-password'}).status_code == 409
    assert other_worker.post('/api/login', json={'password': 'new-test-password'}).status_code == 200
    restarted = create_app(config).test_client()
    assert restarted.get('/api/session').json['configured'] is True
    assert restarted.post('/api/login', json={'password': 'new-test-password'}).status_code == 200
    assert not (tmp_path / '.setup-token').exists()


def test_concurrent_setup_only_one_wins(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    config = {'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': None,
              'ADMIN_PASSWORD_HASH': None, 'SESSION_COOKIE_SECURE': False}
    clients = [create_app(config).test_client(), create_app(config).test_client()]
    token = (tmp_path / '.setup-token').read_text()
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda client: client.post('/api/setup', json={
            'token': token, 'password': 'new-test-password'}).status_code, clients))
    assert sorted(results) == [201, 409]


def test_merge_requires_auth_and_csrf(client):
    body = {'source': 'old_demo', 'target': 'new_demo'}
    assert client.post('/api/albums/merge', json=body).status_code == 401
    login(client)
    assert client.post('/api/albums/merge', json=body).status_code == 403
    assert client.post('/api/albums/merge', json=body, headers={'X-CSRF-Token': 'é'}).status_code == 403


def test_merge_aliases_future_uploads_and_chain(client):
    headers = login(client)
    for filename in ['old_demo_1.png', 'new_demo_2.png']:
        assert client.post('/api/photos', data={'file': (image(), filename)}, headers=headers).status_code == 201
    result = client.post('/api/albums/merge', json={'source': 'old_demo', 'target': 'new_demo'}, headers=headers)
    assert result.status_code == 200 and result.json['moved'] == 1
    assert client.post('/api/photos', data={'file': (image(), 'old_demo_3.png')}, headers=headers).status_code == 201
    catalog = client.get('/catalog.json').json
    assert {p['album'] for p in catalog['photos']} == {'new_demo'}
    assert catalog['aliases'] == {'old_demo': 'new_demo'}
    result = client.post('/api/albums/merge', json={'source': 'new_demo', 'target': 'final_demo'}, headers=headers)
    assert result.status_code == 200 and result.json['moved'] == 3
    catalog = client.get('/api/catalog')
    assert catalog.headers['Cache-Control'] == 'private, no-store'
    assert catalog.json['aliases'] == {'old_demo': 'final_demo', 'new_demo': 'final_demo'}
    assert client.post('/api/photos', data={'file': (image(), 'old_demo_4.png')}, headers=headers).status_code == 201
    assert {p['album'] for p in client.get('/catalog.json').json['photos']} == {'final_demo'}
    # Aliases cannot form a cycle or move photos back into a former name.
    assert client.post('/api/albums/merge', json={'source': 'final_demo', 'target': 'old_demo'}, headers=headers).status_code == 409
    assert client.post('/api/albums/merge', json={'source': 'missing', 'target': 'final_demo'}, headers=headers).status_code == 404
    assert client.post('/api/albums/merge', json={'source': 'final_demo', 'target': "x'; DROP TABLE photos;--"}, headers=headers).status_code == 400
    assert len(client.get('/catalog.json').json['photos']) == 4


def test_public_origin_and_request_limits(tmp_path, monkeypatch):
    monkeypatch.setenv('TRUST_PROXY', '1')
    app = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': 'test',
                      'ADMIN_PASSWORD_HASH': generate_password_hash('test-password'),
                      'PUBLIC_URL': 'https://photos.example.com', 'SESSION_COOKIE_SECURE': True})
    client = app.test_client()
    assert client.get('/', base_url='http://evil.example').status_code == 400
    result = client.post('/api/login', base_url='http://photos.example.com',
                         headers={'Origin': 'https://photos.example.com', 'X-Forwarded-Proto': 'https'},
                         json={'password': 'test-password'})
    assert result.status_code == 200
    assert 'Secure' in result.headers['Set-Cookie']
    assert client.post('/api/login', base_url='https://photos.example.com', json={'password': 'x' * 17000}).status_code == 413
    assert client.get('/api/session').status_code == 200  # local health check
    assert client.get('/api/catalog').status_code == 401


def test_untrusted_upload_and_unicode_setup(client, tmp_path):
    headers = login(client)
    payload = io.BytesIO(b'<script>alert(1)</script>')
    assert client.post('/api/photos', data={'file': (payload, 'image.png')}, headers=headers).status_code == 400
    assert client.get('/media/../../admin.json').status_code == 404
    assert client.get('/static/../app.py').status_code == 404
    fresh = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path / 'fresh'), 'SECRET_KEY': None, 'ADMIN_PASSWORD_HASH': None}).test_client()
    assert fresh.post('/api/setup', json={'token': '日本語', 'password': 'new-password-test'}).status_code == 403


def test_visibility_never_exposes_private_files_or_metadata(client):
    headers = login(client)
    client.post('/api/photos', data={'file': (image(), 'private_demo_1.png')}, headers=headers)
    photo = client.get('/api/catalog').json['photos'][0]
    response = client.post('/api/photos/batch', json={'ids': [photo['id']], 'action': 'hide'}, headers=headers)
    assert response.status_code == 200
    assert client.get('/catalog.json').json == {'photos': [], 'aliases': {}, 'titles': {}}
    # Even administrators use the separate non-cacheable endpoint for hidden media.
    assert client.get('/media/' + photo['original']).status_code == 404
    private = client.get('/api/media/' + photo['original'])
    assert private.status_code == 200 and private.headers['Cache-Control'] == 'private, no-store'
    assert client.get('/api/catalog').json['photos'][0]['hidden'] == 1
    client.post('/api/logout', headers=headers)
    for key in ('original', 'thumb'):
        assert client.get('/media/' + photo[key]).status_code == 404
        assert client.get('/api/media/' + photo[key]).status_code == 401
    headers = login(client)
    assert client.post('/api/photos/batch', json={'ids': [photo['id']], 'action': 'show'}, headers=headers).status_code == 200
    assert client.get('/media/' + photo['thumb']).status_code == 200
    assert len(client.get('/catalog.json').json['photos']) == 1


def test_rename_preserves_classification_and_delete_cleans_album(client):
    headers = login(client)
    for name in ['old_demo_1.png', 'new_demo_2.png']:
        client.post('/api/photos', data={'file': (image(), name)}, headers=headers)
    client.post('/api/albums/merge', json={'source': 'old_demo', 'target': 'new_demo'}, headers=headers)
    assert client.post('/api/albums/rename', json={'album': 'new_demo', 'title': '共有アルバム'}, headers=headers).status_code == 200
    client.post('/api/photos', data={'file': (image(), 'old_demo_3.png')}, headers=headers)
    catalog = client.get('/api/catalog').json
    assert catalog['titles'] == {'new_demo': '共有アルバム'}
    assert {p['album'] for p in catalog['photos']} == {'new_demo'}
    client.post('/api/photos/batch', json={'ids': [catalog['photos'][0]['id']], 'action': 'hide'}, headers=headers)
    result = client.post('/api/albums/delete', json={'album': 'new_demo'}, headers=headers)
    assert result.status_code == 200 and result.json['deleted'] == 3
    assert client.get('/api/catalog').json == {'photos': [], 'aliases': {}, 'titles': {}}
    for photo in catalog['photos']:
        assert client.get('/api/media/' + photo['original']).status_code == 404


def test_batch_delete_all_or_nothing_and_admin_boundary(client):
    assert client.post('/api/photos/batch', json={'ids': [], 'action': 'delete'}).status_code == 401
    assert client.post('/api/albums/delete', json={'album': 'demo'}).status_code == 401
    assert client.post('/api/albums/rename', json={'album': 'demo', 'title': 'title'}).status_code == 401
    headers = login(client)
    client.post('/api/photos', data={'file': (image(), 'demo_1.png')}, headers=headers)
    photo = client.get('/api/catalog').json['photos'][0]
    assert client.post('/api/photos/batch', json={'ids': [photo['id'], '0' * 64], 'action': 'delete'}, headers=headers).status_code == 409
    assert len(client.get('/api/catalog').json['photos']) == 1
    assert client.post('/api/photos/batch', json={'ids': [photo['id']], 'action': 'delete'}, headers=headers).status_code == 200
    assert client.get('/api/catalog').json['photos'] == []


def test_icon_auth_validation_and_persistence(client):
    assert client.post('/api/icon', data={'file': (image(), 'icon.png')}).status_code == 401
    headers = login(client)
    assert client.post('/api/icon', data={'file': (io.BytesIO(b'<svg onload="alert(1)"></svg>'), 'icon.svg')}, headers=headers).status_code == 400
    result = client.post('/api/icon', data={'file': (image(), 'icon.png')}, headers=headers)
    assert result.status_code == 200
    response = client.get('/site-icon.png')
    icon = Image.open(io.BytesIO(response.data))
    assert icon.size == (128, 128) and icon.format == 'PNG'
    assert response.headers['Cache-Control'] == 'no-cache'
    assert client.get('/favicon.ico').data == response.data
    assert client.get('/catalog.json').json['photos'] == []


def test_custom_media_directory(tmp_path):
    media_path = tmp_path / 'custom-images'
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path / 'config'), 'MEDIA_DIR': str(media_path),
                         'SECRET_KEY': 'test', 'ADMIN_PASSWORD_HASH': generate_password_hash('test-password'),
                         'SESSION_COOKIE_SECURE': False}).test_client()
    headers = login(client)
    client.post('/api/photos', data={'file': (image(), 'demo_1.png')}, headers=headers)
    photo = client.get('/api/catalog').json['photos'][0]
    assert (media_path / photo['original']).exists()
    assert not (tmp_path / 'config/media').exists()
    assert client.get('/media/' + photo['original']).status_code == 200


def test_existing_database_migrates_without_losing_photos(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / 'gallery.db') as conn:
        conn.execute('CREATE TABLE photos (id TEXT PRIMARY KEY, filename TEXT, album TEXT, original TEXT, thumb TEXT, width INTEGER, height INTEGER, created INTEGER)')
        conn.execute("INSERT INTO photos VALUES ('legacy', 'demo_1.png', 'demo', 'legacy.png', 'legacy.webp', 960, 960, 1)")
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path)}).test_client()
    photo = client.get('/catalog.json').json['photos'][0]
    assert photo['id'] == 'legacy' and photo['hidden'] == 0


def test_image_pixel_limit(client, monkeypatch):
    headers = login(client)
    stream = image()
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 100)
    assert client.post('/api/photos', data={'file': (stream, 'oversized.png')}, headers=headers).status_code == 400


def test_native_login_form_and_failures(client):
    response = client.post('/auth/login', data={'username': 'admin', 'password': 'test-password'})
    assert response.status_code == 303 and response.headers['Location'] == '/admin'
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert client.get('/api/session').json['authenticated'] is True
    failed = client.post('/auth/login', data={'username': 'admin', 'password': 'incorrect'})
    assert failed.status_code == 303 and failed.headers['Location'] == '/admin?auth=password'
    assert 'incorrect' not in failed.headers['Location']
    rejected = client.post('/auth/login', data={'password': 'test-password'}, headers={'Origin': 'https://other.example'})
    assert rejected.headers['Location'] == '/admin?auth=origin'


def test_native_setup(tmp_path):
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SESSION_COOKIE_SECURE': False}).test_client()
    token = (tmp_path / '.setup-token').read_text()
    response = client.post('/auth/setup', data={'username': 'admin', 'password': 'new-test-password', 'setup_code': token})
    assert response.status_code == 303 and response.headers['Location'] == '/admin'
    assert client.get('/api/session').json['authenticated'] is True


def test_reset_password_refreshes_workers_revokes_sessions_and_keeps_data(tmp_path):
    from manage import save_password
    save_password(tmp_path, 'original-test-password')
    config = {'TESTING': True, 'DATA_DIR': str(tmp_path), 'SESSION_COOKIE_SECURE': False}
    worker_one = create_app(config).test_client()
    worker_two = create_app(config).test_client()
    for client in (worker_one, worker_two):
        assert client.post('/api/login', json={'password': 'original-test-password'}).status_code == 200
    csrf = worker_one.get('/api/session').json['csrf']
    worker_one.post('/api/photos', data={'file': (image(), 'demo_1.png')}, headers={'X-CSRF-Token': csrf})
    before = worker_one.get('/api/catalog').json
    save_password(tmp_path, 'replacement-test-password', reset=True)
    for client in (worker_one, worker_two):
        assert client.get('/api/session').json['authenticated'] is False
        assert client.post('/api/login', json={'password': 'original-test-password'}).status_code == 401
        assert client.post('/api/login', json={'password': 'replacement-test-password'}).status_code == 200
        assert client.get('/api/catalog').json == before
    assert create_app(config).test_client().post('/api/login', json={'password': 'replacement-test-password'}).status_code == 200


def test_reset_password_clears_rate_limit(tmp_path):
    from manage import save_password
    save_password(tmp_path, 'original-test-password')
    client = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SESSION_COOKIE_SECURE': False}).test_client()
    for _ in range(10):
        assert client.post('/api/login', json={'password': 'incorrect'}).status_code == 401
    assert client.post('/api/login', json={'password': 'original-test-password'}).status_code == 429
    save_password(tmp_path, 'replacement-test-password', reset=True)
    assert client.post('/api/login', json={'password': 'replacement-test-password'}).status_code == 200


def test_upload_photo_larger_than_multipart_chunk(client):
    import random
    stream = io.BytesIO()
    Image.frombytes('RGB', (960, 960), random.Random(42).randbytes(960 * 960 * 3)).save(stream, 'PNG')
    assert 65536 < stream.tell() < 20 * 1024 * 1024
    stream.seek(0)
    response = client.post('/api/photos', data={'file': (stream, 'example_123.png')}, headers=login(client))
    assert response.status_code == 201, response.json


def test_versioned_frontend_assets(client):
    import re
    for route in ('/', '/admin', '/setup'):
        html = client.get(route).text
        paths = re.findall(r'/assets/[a-f0-9]{16}/(?:app.js|style.css)', html)
        assert len(paths) == 2
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200
            name = path.rsplit('/', 1)[1]
            assert response.data == client.get('/static/' + name).data
    assert client.get('/assets/invalid/app.js').status_code == 404
    assert client.get('/assets/invalid/app.py').status_code == 404
