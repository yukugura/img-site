import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import time
import warnings
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_from_directory, session, redirect
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash
from bootstrap import initialize, setup_lock, write_private
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
PATTERN = re.compile(r'([A-Za-z0-9_-]+?)_\d+(?:_(?:negative_film|negative|aged))?\.(?:png|jpe?g|webp)', re.I)
Image.MAX_IMAGE_PIXELS = 12_000_000


def classify(name):
    match = PATTERN.fullmatch(name)
    return match[1] if match else None


def create_app(test_config=None):
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config.update(
        DATA_DIR=os.environ.get('DATA_DIR', str(ROOT / 'data')),
        MEDIA_DIR=os.environ.get('MEDIA_DIR'),
        SECRET_KEY=os.environ.get('SESSION_SECRET'),
        ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH'),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        MAX_FORM_MEMORY_SIZE=64 * 1024, MAX_FORM_PARTS=4,
        PUBLIC_URL=os.environ.get('PUBLIC_URL', '').rstrip('/'),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_REFRESH_EACH_REQUEST=False,
        SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1') != '0',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if test_config:
        app.config.update(test_config)
    password_from_config = bool(app.config['ADMIN_PASSWORD_HASH'])
    if app.config['PUBLIC_URL']:
        parsed = urlsplit(app.config['PUBLIC_URL'])
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('PUBLIC_URL must be an http(s) origin without a path or credentials')
        app.config['TRUSTED_HOSTS'] = [parsed.hostname, '127.0.0.1', 'localhost']
    if os.environ.get('TRUST_PROXY') == '1':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0)
    data = Path(app.config['DATA_DIR'])
    durable_secret = initialize(data, bool(app.config['ADMIN_PASSWORD_HASH']))
    if not app.secret_key:
        app.secret_key = durable_secret
    settings = data / 'admin.json'
    if settings.exists():
        credentials = json.loads(settings.read_text())
        if not app.config['ADMIN_PASSWORD_HASH']:
            app.config['ADMIN_PASSWORD_HASH'] = credentials['password_hash']
        if not app.secret_key:
            app.secret_key = credentials['session_secret']
    media = Path(app.config['MEDIA_DIR']) if app.config['MEDIA_DIR'] else data / 'media'
    branding = data / 'branding'
    branding.mkdir(parents=True, exist_ok=True)
    media.mkdir(parents=True, exist_ok=True)

    def db():
        conn = sqlite3.connect(data / 'gallery.db', timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS photos (
          id TEXT PRIMARY KEY, filename TEXT NOT NULL, album TEXT,
          original TEXT NOT NULL, thumb TEXT NOT NULL, width INTEGER, height INTEGER,
          created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (time REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS album_titles (album TEXT PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS album_aliases (alias TEXT PRIMARY KEY, target TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS photos_album ON photos(album);
        ''')

    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if 'hidden' not in {row['name'] for row in conn.execute('PRAGMA table_info(photos)')}:
            conn.execute('ALTER TABLE photos ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0')
        conn.execute('CREATE INDEX IF NOT EXISTS photos_original ON photos(original)')
        conn.execute('CREATE INDEX IF NOT EXISTS photos_thumb ON photos(thumb)')

    @app.before_request
    def refresh_admin():
        if request.path in {'/api/photos', '/api/icon'} and request.method == 'POST':
            # Multipart parsing needs room for a 64 KiB chunk plus boundary leftovers.
            # Keep total request limits and part counts enforced independently.
            request.max_form_memory_size = 512 * 1024
        if request.path.startswith(('/api/', '/auth/')) and request.path not in {'/api/photos', '/api/icon'}:
            request.max_content_length = 64 * 1024 if request.path == '/api/photos/batch' else 16 * 1024
        if request.path == '/api/icon':
            request.max_content_length = 2 * 1024 * 1024
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            expected = app.config['PUBLIC_URL'] or request.host_url.rstrip('/')
            if request.headers.get('Origin') and request.headers['Origin'] != expected:
                abort(403)
        # Reload atomically written credentials in every worker, including password resets.
        if not password_from_config:
            app.config['ADMIN_PASSWORD_HASH'] = json.loads(settings.read_text())['password_hash'] if settings.exists() else None
        if request.path.startswith(('/api/', '/auth/')) and session.get('admin') and session.get('auth_version') != auth_version():
            session.clear()

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path in {'/static/logo.svg', '/site-icon.png', '/favicon.ico'}:
            response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        if request.path.startswith(('/api/', '/auth/')):
            response.headers['Cache-Control'] = 'private, no-store'
        elif request.path.startswith('/media/') and response.status_code < 400:
            response.headers['Cache-Control'] = 'public, max-age=0, s-maxage=60, must-revalidate'
        elif request.path == '/catalog.json':
            response.headers['Cache-Control'] = 'public, max-age=0, s-maxage=60, must-revalidate'
        else:
            response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='リクエストが大きすぎます。画像は1枚20MB以内にしてください。'), 413

    def auth_version():
        return hashlib.sha256((app.config['ADMIN_PASSWORD_HASH'] or '').encode()).hexdigest()

    def auth_reply(payload, status=200):
        if request.path.startswith('/auth/'):
            if status < 400:
                return redirect('/admin', code=303)
            reason = {400: 'input', 401: 'password', 409: 'configured', 429: 'limited', 503: 'unconfigured'}.get(status, 'server')
            return redirect('/admin?auth=' + reason, code=303)
        return jsonify(payload), status

    @app.errorhandler(403)
    def forbidden(_):
        if request.path.startswith('/auth/'):
            return redirect('/admin?auth=origin', code=303)
        return jsonify(error='認証または公開URLの設定を確認してください。'), 403

    def require_admin():
        if not session.get('admin'):
            abort(401)
        if not secrets.compare_digest(request.headers.get('X-CSRF-Token', '').encode(), session.get('csrf', 'missing').encode()):
            abort(403)

    asset_versions = {name: hashlib.sha256((ROOT / 'static' / name).read_bytes()).hexdigest()[:16]
                      for name in ('app.js', 'style.css')}

    @app.get('/assets/<version>/<name>')
    def versioned_asset(version, name):
        if asset_versions.get(name) != version:
            abort(404)
        return send_from_directory(ROOT / 'static', name)

    @app.get('/')
    @app.get('/admin')
    @app.get('/setup')
    def index():
        html = (ROOT / 'static' / 'index.html').read_text()
        for name, version in asset_versions.items():
            html = html.replace(f'/static/{name}', f'/assets/{version}/{name}')
        return html

    @app.get('/media/<path:name>')
    @app.get('/api/media/<path:name>')
    def get_media(name):
        private = request.path.startswith('/api/')
        if private and not session.get('admin'):
            abort(401)
        with db() as conn:
            row = conn.execute('SELECT hidden FROM photos WHERE original=? OR thumb=?', (name, name)).fetchone()
        if not row or (row['hidden'] and not private):
            abort(404)
        return send_from_directory(media, name)

    @app.get('/site-icon.png')
    @app.get('/favicon.ico')
    def site_icon():
        if (branding / 'icon.png').exists():
            return send_from_directory(branding, 'icon.png')
        # The supplied logo is a safe bundled default until an icon is uploaded.
        return send_from_directory(ROOT / 'static', 'logo.svg')

    @app.post('/api/icon')
    def upload_icon():
        require_admin()
        file = request.files.get('file')
        if not file:
            abort(400)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(file.stream, formats=['PNG', 'JPEG', 'WEBP', 'ICO']) as source:
                    if source.width * source.height > 1_000_000:
                        raise ValueError('size')
                    source.load()
                    icon = ImageOps.exif_transpose(source).convert('RGBA')
                    icon.thumbnail((128, 128), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    icon.save(output, format='PNG')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return jsonify(error='100万画素以内のPNG・JPEG・WebP・ICOを選択してください。'), 400
        with setup_lock(data):
            temp = branding / 'icon.tmp'
            temp.write_bytes(output.getvalue())
            os.replace(temp, branding / 'icon.png')
        return jsonify(ok=True)


    @app.get('/catalog.json')
    @app.get('/api/catalog')
    def catalog():
        if request.path.startswith('/api/') and not session.get('admin'):
            abort(401)
        with db() as conn:
            admin_view = request.path.startswith('/api/')
            query = 'SELECT * FROM photos' + ('' if admin_view else ' WHERE hidden=0') + ' ORDER BY created DESC, id DESC'
            rows = [dict(row) for row in conn.execute(query)]
            aliases = {row['alias']: row['target'] for row in conn.execute('SELECT * FROM album_aliases')}
            visible_albums = {row['album'] for row in rows}
            aliases = {alias: target for alias, target in aliases.items() if target in visible_albums}
            titles = {row['album']: row['title'] for row in conn.execute('SELECT * FROM album_titles') if row['album'] in visible_albums}
        return jsonify(photos=rows, aliases=aliases, titles=titles)

    @app.get('/api/session')
    def status():
        return jsonify(authenticated=bool(session.get('admin')), csrf=session.get('csrf'), public_url=app.config['PUBLIC_URL'],
                       configured=bool(app.secret_key and app.config['ADMIN_PASSWORD_HASH']))

    @app.post('/auth/setup')
    @app.post('/api/setup')
    def setup():
        body = request.form.to_dict() if request.path.startswith('/auth/') else request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        token, password = body.get('token', body.get('setup_code')), body.get('password')
        if not isinstance(token, str) or not isinstance(password, str):
            abort(400)
        if not 12 <= len(password) <= 1024:
            return auth_reply({'error': 'パスワードは12〜1024文字で設定してください。'}, 400)
        with setup_lock(data):
            if app.config['ADMIN_PASSWORD_HASH'] or settings.exists():
                return auth_reply({'error': '初回セットアップは完了しています。ログインしてください。'}, 409)
            token_file = data / '.setup-token'
            if not token_file.exists() or not secrets.compare_digest(token.encode(), token_file.read_text().encode()):
                return redirect('/admin?auth=code', code=303) if request.path.startswith('/auth/') else (jsonify(error='設定コードが違います。サーバーの起動ログを確認してください。'), 403)
            password_hash = generate_password_hash(password)
            write_private(settings, json.dumps({'password_hash': password_hash, 'session_secret': app.secret_key}))
            app.config['ADMIN_PASSWORD_HASH'] = password_hash
            token_file.unlink(missing_ok=True)
        session.clear()
        session.permanent = True
        session.update(admin=True, csrf=secrets.token_hex(32), auth_version=hashlib.sha256(password_hash.encode()).hexdigest())
        return auth_reply({'csrf': session['csrf']}, 201)

    @app.post('/auth/login')
    @app.post('/api/login')
    def login():
        if not app.secret_key or not app.config['ADMIN_PASSWORD_HASH']:
            return auth_reply({'error': '管理者のパスワードとセッション鍵が未設定です。'}, 503)
        body = request.form.to_dict() if request.path.startswith('/auth/') else request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            abort(400)
        password = body.get('password', '')
        if not isinstance(password, str) or len(password) > 1024:
            abort(400)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM login_attempts WHERE time < ?', (time.time() - 300,))
            if conn.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0] >= 10:
                return auth_reply({'error': '試行回数が多すぎます。5分後に再試行してください。'}, 429)
            conn.execute('INSERT INTO login_attempts VALUES (?)', (time.time(),))
        password_hash = app.config['ADMIN_PASSWORD_HASH']
        if not check_password_hash(password_hash, password):
            return auth_reply({'error': 'パスワードが違います。'}, 401)
        session.clear()
        session.permanent = True
        session.update(admin=True, csrf=secrets.token_hex(32), auth_version=hashlib.sha256(password_hash.encode()).hexdigest())
        return auth_reply({'csrf': session['csrf']})

    @app.post('/api/logout')
    def logout():
        require_admin()
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/photos')
    def upload():
        require_admin()
        file = request.files.get('file')
        if not file or not file.filename:
            return jsonify(error='画像を選択してください。'), 400
        filename = file.filename.replace('\\', '/').split('/')[-1]
        if len(filename) > 255:
            return jsonify(error='ファイル名が長すぎます。'), 400
        raw = file.read()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw), formats=['PNG', 'JPEG', 'WEBP']) as source:
                    if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                        raise ValueError('format')
                    source.load()
                    normalized = ImageOps.exif_transpose(source).convert('RGBA' if 'A' in source.getbands() else 'RGB')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return jsonify(error='PNG・JPEG・WebPの有効な画像を選択してください。'), 400
        # Include filename so identical pixels may belong to different albums.
        ident = hashlib.sha256(filename.encode() + b'\0' + raw).hexdigest()
        original, thumb = f'{ident}.png', f'{ident}-320.webp'
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM photos WHERE id=?', (ident,)).fetchone():
                return jsonify(id=ident, duplicate=True)
            normalized.save(media / original, format='PNG')
            width, height = normalized.size
            normalized.thumbnail((320, 320), Image.Resampling.LANCZOS)
            normalized.save(media / thumb, format='WEBP', quality=78, method=4)
            album = classify(filename)
            mapped = conn.execute('SELECT target FROM album_aliases WHERE alias=?', (album,)).fetchone()
            album = mapped['target'] if mapped else album
            conn.execute('INSERT INTO photos(id,filename,album,original,thumb,width,height,created) VALUES (?,?,?,?,?,?,?,?)',
                         (ident, filename, album,
                          original, thumb, width, height, time.time_ns()))
        return jsonify(id=ident, duplicate=False), 201

    @app.post('/api/albums/merge')
    def merge_albums():
        require_admin()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        source, target = body.get('source'), body.get('target')
        if not all(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) for name in (source, target)):
            return jsonify(error='MCIDは英数字・_・-の1〜64文字で指定してください。'), 400
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            aliases = {row['alias']: row['target'] for row in conn.execute('SELECT * FROM album_aliases')}
            source, target = aliases.get(source, source), aliases.get(target, target)
            if source == target:
                return jsonify(error='同じアルバムには統合できません。'), 409
            count = conn.execute('SELECT COUNT(*) FROM photos WHERE album=?', (source,)).fetchone()[0]
            if not count:
                return jsonify(error='統合元のアルバムが見つかりません。再読み込みしてください。'), 404
            conn.execute('UPDATE photos SET album=? WHERE album=?', (target, source))
            old_title = conn.execute('SELECT title FROM album_titles WHERE album=?', (source,)).fetchone()
            if old_title:
                conn.execute('INSERT OR IGNORE INTO album_titles VALUES (?,?)', (target, old_title['title']))
            conn.execute('DELETE FROM album_titles WHERE album=?', (source,))
            # Keep all historical aliases pointing directly to the final album.
            conn.execute('UPDATE album_aliases SET target=? WHERE target=?', (target, source))
            conn.execute('INSERT INTO album_aliases(alias,target) VALUES (?,?)', (source, target))
        return jsonify(source=source, target=target, moved=count)

    def purge_paths(rows):
        return ['/catalog.json'] + [f'/media/{row[key]}' for row in rows for key in ('original', 'thumb')]

    def remove_files(rows):
        for row in rows:
            for key in ('original', 'thumb'):
                (media / row[key]).unlink(missing_ok=True)

    @app.post('/api/albums/rename')
    def rename_album():
        require_admin()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        album, title = body.get('album'), body.get('title')
        if not isinstance(album, str) or not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
            return jsonify(error='表示名を1〜80文字で入力してください。'), 400
        title = title.strip()
        if any(ord(char) < 32 for char in title):
            abort(400)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM photos WHERE album=?', (album,)).fetchone():
                abort(404)
            conn.execute('INSERT INTO album_titles VALUES (?,?) ON CONFLICT(album) DO UPDATE SET title=excluded.title', (album, title))
        return jsonify(ok=True)

    @app.post('/api/albums/delete')
    def delete_album():
        require_admin()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or 'album' not in body or not (body['album'] is None or isinstance(body['album'], str)):
            abort(400)
        album = body['album']
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = list(conn.execute('SELECT * FROM photos WHERE album IS ?', (album,)))
            if not rows:
                abort(404)
            conn.execute('DELETE FROM photos WHERE album IS ?', (album,))
            conn.execute('DELETE FROM album_titles WHERE album IS ?', (album,))
            conn.execute('DELETE FROM album_aliases WHERE target IS ? OR alias IS ?', (album, album))
            remove_files(rows)
        return jsonify(deleted=len(rows), purge_paths=purge_paths(rows))

    @app.post('/api/photos/batch')
    def batch_photos():
        require_admin()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        ids, action = body.get('ids'), body.get('action')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or not all(isinstance(ident, str) and re.fullmatch(r'[0-9a-f]{64}', ident) for ident in ids):
            abort(400)
        if action not in {'delete', 'hide', 'show'}:
            abort(400)
        ids = list(set(ids))
        placeholders = ','.join('?' for _ in ids)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = list(conn.execute(f'SELECT * FROM photos WHERE id IN ({placeholders})', ids))
            if len(rows) != len(ids):
                return jsonify(error='選択した画像が変更されています。一覧を再読み込みしてください。'), 409
            if action == 'delete':
                conn.execute(f'DELETE FROM photos WHERE id IN ({placeholders})', ids)
                remove_files(rows)
            else:
                conn.execute(f'UPDATE photos SET hidden=? WHERE id IN ({placeholders})', [int(action == 'hide'), *ids])
        return jsonify(changed=len(rows), purge_paths=purge_paths(rows))

    @app.delete('/api/photos/<ident>')
    def delete(ident):
        require_admin()
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM photos WHERE id=?', (ident,)).fetchone()
            if not row:
                abort(404)
            conn.execute('DELETE FROM photos WHERE id=?', (ident,))
            remove_files([row])
        return jsonify(ok=True, purge_paths=purge_paths([row]))

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=8000)
