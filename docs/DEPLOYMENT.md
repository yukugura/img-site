# Nginx Proxy Manager + Cloudflare で公開

接続経路は **ブラウザー → Cloudflare（HTTPS）→ NPM（HTTPS）→ アプリ（HTTP）** です。公開するのはNPMの443番で、アプリの8088番やDocker内部の8000番をインターネットへ直接公開しません。

## 1. アプリの設定

`.env.example` を `.env` にコピーし、自分のドメインに置き換えます。

```dotenv
PORT=8088
BIND_ADDRESS=0.0.0.0
COOKIE_SECURE=1
TRUST_PROXY=1
PUBLIC_URL=https://photos.example.com
```

```sh
docker compose pull
docker compose up -d
```

- `PORT` は**サーバー側の待受ポート**です。内部のGunicornは8000番で固定です。9090等に変える場合はNPMの転送先ポートも合わせます。
- `PUBLIC_URL` は末尾のパスを含まない公開URLです。Hostヘッダーと管理操作のOrigin検証に使用します。設定後は管理操作をこのURLから行ってください。
- NPMが別マシン・別コンテナなら、転送先は**アプリを動かすホストのLAN IPと8088番**です。NPMコンテナの `127.0.0.1` はNPM自身なので使えません。
- NPMがホストで直接動いている場合のみ `BIND_ADDRESS=127.0.0.1` にできます。同じDockerネットワークで直接接続する構成なら転送先はアプリのサービス名と8000番です。
- 同じLANでも、ファイアウォール等で8088番へ接続できる送信元をNPMに限定してください。Dockerの公開ポートは通常のUFWルールを迂回する場合があるため、ルーター側とDockerの転送ルールも確認します。

すでに `.env` に `PORT=8080` がある場合は設定が優先されます。8088へ変更したい場合はその行も更新してください。以前の `data` ボリュームはそのまま引き継ぎます。

## 2. NPMのProxy Host

「Hosts → Proxy Hosts → Add Proxy Host」で次を設定します。

| 項目 | 設定例 |
|---|---|
| Domain Names | `photos.example.com` |
| Scheme | `http` |
| Forward Hostname / IP | アプリを動かすホストのLAN IP |
| Forward Port | `8088`（`.env` のPORTと一致） |
| Access List | Publicly Accessible（一般閲覧に認証を要求しない） |
| Cache Assets | オフ（Cloudflare側で明示的に設定） |
| Websockets Support | 不要 |

SSLタブで対象ドメインの有効な証明書を設定し、Force SSLを有効にします。Let's EncryptはCloudflareのDNSチャレンジを使うと80番の外部開放なしで取得できます。証明書取得に使用するDNSトークンはNPMの設定画面にだけ保存し、Gitに書きません。

Advancedには画像のアップロード用に次を設定します。1枚20MiBの上限にmultipartのヘッダー分を含めるため21mにしています。アプリ自身もリクエスト全体を20MiBに制限します。

```nginx
client_max_body_size 21m;
proxy_read_timeout 120s;
proxy_send_timeout 120s;
```

NPM標準の転送設定でHostと `X-Forwarded-Proto` を保持・上書きします。カスタム設定で壊さないでください。CloudflareからNPMまでHTTPSにすることで `X-Forwarded-Proto: https` となり、Secure Cookieでログインできます。管理画面を開くこと自体は認証ではなく、アップロード・削除・統合はアプリ側で必ず認証とCSRFを検証します。

## 3. Cloudflare

1. DNSに `photos.example.com` のA/AAAAレコードを作り、NPMへ到達できる公開IPを指定します。使えないAAAAは登録しません。ルーターの443番転送先はNPMです。
2. プロキシをオン（オレンジ雲）にします。
3. SSL/TLSを **Full (strict)** にします。Flexibleは使いません。
4. [READMEのCache Rules](../README.md#cloudflare-cache-rules)を設定します。`/api/*`・`/auth/*`・`/admin`・`/setup` はバイパスし、ログイン情報をキャッシュさせないでください。
5. 必要に応じてWAF・レート制限を有効にします。特に `/api/login` と `/api/setup` が対象です。Cloudflareを迂回する直接アクセスを防ぐには、公開側のNPMへの443番通信をCloudflareの公式IP範囲に限定する等のオリジン保護も行います。

Cloudflareの無料枠・契約プランによってWAFやレート制限の設定範囲は異なります。NPMの管理画面（通常81番）やDocker APIは公開しないでください。

## 4. 一般ユーザーへのURL

URL発行やユーザー登録は不要です。DNS・NPM・HTTPSの設定後に、次をそのまま共有できます。

- 写真一覧: `https://photos.example.com/`
- アルバム一覧: `https://photos.example.com/#albums`
- 特定アルバム: アルバムを開いたときのアドレスバーのURL（例: `https://photos.example.com/#album/demo_user`）

ここに書いたドメインとIDは架空の例です。MCIDが入ったアルバムURLやファイル名は公開情報になります。URLを知っている人だけに限定する認証機能ではありません。

統合前のアルバムURLも統合先に表示を切り替えます。画像や一覧がCloudflareにキャッシュされている場合、統合は最大60秒後に反映されます。管理画面はキャッシュしない一覧を使用するため即時確認できます。

## 公開前の動作確認

別のブラウザーの未ログイン状態でトップとアルバムが表示され、管理操作ができないことを確認します。管理者ログイン後に画像を追加してHTTPS経由でセッションが維持されることも確認してください。ログインループなら `COOKIE_SECURE`・公開URL・NPMのHTTPS転送を、アップロードの413エラーならNPMとアプリのサイズ上限を確認します。

## 画像の保存先を変更する

`.env` の `MEDIA_STORAGE` で、Dockerホスト側の保存先ルートを指定できます。初期値 `gallery-data` は従来と同じ名前付きボリュームで、既存画像の場所は変わりません。例えば次の場合、画像はホストの `/srv/hnws-images/media/` に保存します。DB・管理者設定・サイトアイコンは従来の `gallery-data` に保持します。

```dotenv
MEDIA_STORAGE=/srv/hnws-images
```

外部フォルダーを使う場合は、コンテナのUID/GID 10001が書き込めるよう事前に作成します。

```sh
sudo install -d -m 0750 -o 10001 -g 10001 /srv/hnws-images/media
```

**既存画像は自動移動しません。** 使用中の保存先を変える場合は、停止・コピー・設定変更の順で行ってください。

```sh
docker compose stop web
mkdir -p media-transfer
docker compose cp web:/storage/media/. ./media-transfer/
sudo cp -a ./media-transfer/. /srv/hnws-images/media/
sudo chown -R 10001:10001 /srv/hnws-images
# ここで.envのMEDIA_STORAGEを変更
docker compose up -d
```

初回の旧版からの更新で `/storage/media` がまだ存在しない場合、コピー元は `web:/data/media/.` です。新しい保存先で表示を確認するまで、コピー元やバックアップを削除しないでください。Pythonで直接起動する場合は `MEDIA_DIR` が画像ディレクトリの指定です。

## 非表示機能とキャッシュ（旧版からの更新時に必須）

この版では `/media/*` のヘッダーを **ブラウザー再検証・Cloudflare最大60秒** に変更しています。管理者は別の `/api/media/*` から閲覧し、ここは認証必須かつ保存禁止です。未ログインの一覧・アルバムサムネイル・画像URLには非表示画像を配信しません。

**旧版の1年キャッシュが残っている場合、更新時にCloudflareの旧 `/media/*` キャッシュを全てパージしてください。** Cache RulesもOrigin Cache-Controlを尊重する設定へ変更し、長いEdge TTLの上書きや、エラー時に古い画像を配信し続ける設定は外してください。`/api/*` は必ずキャッシュをバイパスします。

非表示・削除の操作後は、管理画面にパージ対象のURL一覧を表示します。即時の取り下げが必要ならCloudflareでURL指定のパージを実施します。パージしない場合、一覧と公開画像がエッジのキャッシュ期限まで最大60秒残る可能性があります。以前ダウンロードされた画像や、旧設定で閲覧者のブラウザーに保存されたコピーは回収できません。最初から公開したくない機密画像の保管場所には使用しないでください。

## ログイン失敗の切り分け

1. `sudo docker compose ps` で起動・healthを確認します。`PUBLIC_URL` の形式エラーなどで停止している場合は、パスワードの問題ではありません。
2. `sudo docker compose exec web python manage.py check-password` で端末から一致を確認します。パスワードはチャットやログに貼り付けないでください。
3. 管理画面で「保存されているパスワードと一致しません」と表示される場合、認証APIの401相当の応答です。ブラウザーが古いパスワードを自動入力していないか、Caps Lockや前後の空白も確認します。
4. 「公開URLまたはNPMのHTTPS転送設定が一致していません」はOrigin検証の403です。`PUBLIC_URL` を実際の公開URLに合わせ、NPMがHostと `X-Forwarded-Proto: https` を正しく転送するか確認します。
5. 5分間の試行制限は429相当です。待つか、サーバー上でパスワードを再設定して解除します。
6. 正しいパスワードなのにログイン直後また未ログインになる場合、HTTPSで開いているか、Cookieがブラウザーで拒否されていないか、`/api/*`・`/auth/*` がCloudflareでキャッシュされていないか確認します。

認証フォームの失敗はパスワードを含めず `/admin?auth=...` に戻して表示します。API利用時は401/403/429等のステータスを返します。ネイティブフォームの送信先 `/auth/*` もキャッシュをバイパスしてください。

以前の実装では、設定ファイルを手動変更しても既存ワーカーが古いパスワードハッシュを保持していました。更新版ではファイルの変更を読み直し、パスワード変更時に既存セッションも失効させます。旧版のまま手動で変更した場合は、コンテナの再起動も必要です。
