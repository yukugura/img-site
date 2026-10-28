"""Local administrator recovery; passwords are read from the terminal only."""
import argparse
import getpass
import json
import os
import sqlite3
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
from bootstrap import initialize, setup_lock, write_private


def load_credentials(directory):
    target = directory / 'admin.json'
    return json.loads(target.read_text()) if target.exists() else None


def save_password(directory, password, reset=False):
    if os.environ.get('ADMIN_PASSWORD_HASH'):
        raise ValueError('ADMIN_PASSWORD_HASHが設定されています。環境変数側を変更してコンテナを再作成してください。')
    if not 12 <= len(password) <= 1024:
        raise ValueError('パスワードは12〜1024文字で設定してください。')
    secret = initialize(directory)
    with setup_lock(directory):
        credentials = load_credentials(directory)
        if credentials and not reset:
            raise ValueError('管理者は設定済みです。reset-passwordを使用してください。')
        if not credentials and reset:
            raise ValueError('初回設定が未完了です。ブラウザーで設定するかcreate-adminを使用してください。')
        # Cookie signing key stays stable; password-hash version revokes all old sessions.
        write_private(directory / 'admin.json', json.dumps({
            'password_hash': generate_password_hash(password), 'session_secret': secret}))
        (directory / '.setup-token').unlink(missing_ok=True)
        database = directory / 'gallery.db'
        if database.exists():
            with sqlite3.connect(database, timeout=30) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='login_attempts'").fetchone():
                    conn.execute('DELETE FROM login_attempts')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='create-admin', choices=['create-admin', 'reset-password', 'check-password', 'status'])
    args = parser.parse_args()
    directory = Path(os.environ.get('DATA_DIR', Path(__file__).resolve().parent / 'data'))
    if args.command == 'status':
        credentials = load_credentials(directory)
        print('管理者設定: ' + ('設定済み' if credentials else '未設定'))
        print('環境変数によるパスワード上書き: ' + ('あり' if os.environ.get('ADMIN_PASSWORD_HASH') else 'なし'))
        print('PUBLIC_URL: ' + ('設定あり' if os.environ.get('PUBLIC_URL') else '未指定'))
        print('Secure Cookie: ' + ('有効' if os.environ.get('COOKIE_SECURE', '1') != '0' else '無効'))
        print('転送ヘッダーの信頼: ' + ('有効' if os.environ.get('TRUST_PROXY') == '1' else '無効'))
        return
    if args.command == 'check-password':
        credentials = load_credentials(directory) or {}
        password_hash = os.environ.get('ADMIN_PASSWORD_HASH') or credentials.get('password_hash')
        if not password_hash:
            raise ValueError('管理者が未設定です。')
        candidate = getpass.getpass('確認するパスワード（非表示）: ')
        if len(candidate) <= 1024 and check_password_hash(password_hash, candidate):
            print('一致しています。サイトで失敗する場合は公開URL・Cookie・転送設定を確認してください。')
            return
        raise ValueError('現在の保存済みパスワードと一致しません。reset-passwordで再設定できます。')
    password = getpass.getpass('新しい管理者パスワード（12文字以上、非表示）: ')
    if password != getpass.getpass('確認（非表示）: '):
        raise ValueError('パスワードが一致しません。変更していません。')
    save_password(directory, password, reset=args.command == 'reset-password')
    print('保存しました。更新版では再起動不要で反映され、古いログインは失効します。画像とアルバムは保持されます。')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(str(error))
