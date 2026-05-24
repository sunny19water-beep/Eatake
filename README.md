# Eatake

Eat + Take（撮影）から名付けた、 
「食事を撮るだけで記録できる」AI食事管理アプリです。

写真を撮るだけで、AIがカロリー・PFCバランス・糖質・食物繊維・塩分を推定し、自動で記録します。

---

## 概要

既存の食事記録アプリでは、栄養成分を毎回手入力する手間が大きく、継続が難しいと感じていました。

特に、自分自身が食事管理を行う中で、
食品検索、栄養成分入力や細かい記録作業の負担が大きく、「もっと簡単に続けられる方法が欲しい」と考えたことが、このアプリを作ったきっかけです。
そこで写真を撮るだけで食事記録を完了できるアプリをテーマに開発を行いました。
食事写真を Gemini API で解析し、栄養情報を推定して保存します。  
ローカル開発では SQLite、本番の Cloud Run では Firestore と LINEログインを使います。
また、栄養成分表示の写真を読み取るモードも実装し、パッケージ食品などでは表示値を優先して記録できるようにしています。

## アプリ画面

### 撮影画面

食事写真または栄養成分表示を撮影し、AIが栄養情報を推定します。

![撮影画面](./images/camera.png)

---

### 1日ログ画面

1日の合計カロリーやPFCバランスを自動集計します。  
AIレビュー機能も搭載しています。

![1日ログ画面](./images/daylog.png)

---

### 設定画面

年齢・身長・体重・目的を保存し、AIレビューに反映します。

![設定画面](./images/settings.png)

---

## 主な機能

### 食事撮影
- 食事写真からAIが栄養情報を推定
- カロリー / PFC / 糖質 / 食物繊維 / 塩分を自動算出

### 栄養成分表示読み取り
- パッケージ食品の栄養成分表示をOCRで読み取り
- 記載値を優先して保存

### 日別ログ管理
- 1日の栄養情報を自動集計
- 食べたもの一覧を表示
- カレンダーから過去ログ閲覧可能
- 直近3か月分だけ保持

### 継続サポート
- 連続記録ストリークを表示
- 月曜日に先週の一言サマリーを表示
- AIレビューは数値のダメ出しではなく、記録した行動を褒める方向で生成
- 写真がない時は「ラーメン食べた」などのテキストだけで、てきとう記録が可能

### 1週間ログ
- 直近7日間の合計を表示
- 各日を押すと、その日の記録へ移動
- 数字だけでなく、継続を褒める一言を表示

### AIレビュー
登録したプロフィール情報をもとに、AIが食事内容をレビュー。

- 年齢
- 身長
- 体重
- 性別
- 目的（減量・維持など）
を考慮してコメントを生成します。

## 技術構成

### Backend
- Python
- FastAPI
- SQLite（ローカル）
- Firestore（Cloud Run本番）
- LINEログイン

### Frontend
- React
- HTML / CSS / JavaScript

### AI / API
- Gemini API

## ローカル起動

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

`.env` はローカルなら次の形で動きます。

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
AI_DAILY_LIMIT=10
STORAGE_BACKEND=sqlite
APP_BASE_URL=http://127.0.0.1:8000
```

## Cloud Run デプロイ

このPCには現時点で `gcloud` が入っていないため、こちらから直接デプロイはまだできません。Google Cloud SDK を入れてログインしたら、プロジェクト直下で次を実行します。

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com secretmanager.googleapis.com
gcloud firestore databases create --database="(default)" --location=asia-northeast1
```

APIキーやLINEシークレットは、Cloud Runの環境変数に直書きせず Secret Manager に入れるのがおすすめです。

```powershell
gcloud secrets create gemini-api-key --replication-policy=automatic
gcloud secrets versions add gemini-api-key --data-file=YOUR_GEMINI_KEY_TEXT_FILE
gcloud secrets create line-channel-id --replication-policy=automatic
gcloud secrets versions add line-channel-id --data-file=YOUR_LINE_CHANNEL_ID_TEXT_FILE
gcloud secrets create line-channel-secret --replication-policy=automatic
gcloud secrets versions add line-channel-secret --data-file=YOUR_LINE_CHANNEL_SECRET_TEXT_FILE
```

まず1回目は仮の `APP_BASE_URL` でデプロイして Cloud Run のURLを取得します。

```powershell
gcloud run deploy eatake `
  --source . `
  --region asia-northeast1 `
  --allow-unauthenticated `
  --set-env-vars STORAGE_BACKEND=firestore,GEMINI_MODEL=gemini-2.5-flash,AI_DAILY_LIMIT=10,APP_BASE_URL=https://TEMP.example.com `
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,LINE_CHANNEL_ID=line-channel-id:latest,LINE_CHANNEL_SECRET=line-channel-secret:latest
```

## Renderで自動デプロイ

Renderで一度URLを作ってスマホに入れるなら、この方法だけ見ればOKです。このリポジトリをGitHubにpushして、RenderのBlueprintとして `render.yaml` を読み込ませます。GitHub連携後はpushするたびに自動デプロイされます。

1. GitHubにこのプロジェクトをpush
2. Render Dashboardで `New` → `Blueprint`
3. GitHubリポジトリを選択
4. `render.yaml` を使って作成
5. Renderに聞かれる環境変数へ以下を設定

```txt
GEMINI_API_KEY=あなたのGemini APIキー
LINE_CHANNEL_ID=LINEのChannel ID
LINE_CHANNEL_SECRET=LINEのChannel Secret
GOOGLE_APPLICATION_CREDENTIALS_JSON=Firestore用サービスアカウントJSONを1行で貼り付け
```

`APP_BASE_URL` はRenderの `RENDER_EXTERNAL_URL` を自動利用します。独自ドメインを使う時だけ、Environmentで `APP_BASE_URL=https://独自ドメイン` を追加してください。

LINE Developers ConsoleのCallback URLには次を入れます。

```txt
https://あなたのrender-url.onrender.com/auth/line/callback
```

RenderでURLが発行されたあと、LINE Developers ConsoleにCallback URLを追加し、Renderで `Manual Deploy` → `Deploy latest commit` を押してください。

Renderの無料Web Serviceはローカルファイルが再起動や再デプロイで消えるため、SQLite保存のまま本番運用するのは危険です。この設定では `STORAGE_BACKEND=firestore` にして、食事ログや回数制限をFirestoreへ保存します。

## スマホアプリ化

まずはPWAとして使えます。RenderのURLをスマホで開いて、ブラウザの「ホーム画面に追加」を押してください。アプリ風に全画面で起動します。

ストア配布したくなったら、同じURL/フロントをCapacitorで包んで iOS / Android アプリにできます。

表示された Cloud Run URL を LINE Developers Console の Callback URL に設定します。

```txt
https://YOUR_CLOUD_RUN_URL/auth/line/callback
```

その後、`APP_BASE_URL` を本物のURLにして再デプロイします。

```powershell
gcloud run deploy eatake `
  --source . `
  --region asia-northeast1 `
  --allow-unauthenticated `
  --set-env-vars STORAGE_BACKEND=firestore,GEMINI_MODEL=gemini-2.5-flash,AI_DAILY_LIMIT=10,APP_BASE_URL=https://YOUR_CLOUD_RUN_URL `
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,LINE_CHANNEL_ID=line-channel-id:latest,LINE_CHANNEL_SECRET=line-channel-secret:latest
```


## AIコーディングについて

本アプリはAIコーディングを活用して開発しました。

FastAPIやReactなど未経験技術も含まれていたため、

- 必要機能の整理
- API設計
- UI設計
- 動作確認
- エラー修正
- ユーザー目線での改善
を繰り返しながら開発を進めています。
単にコード生成を行うだけではなく、
- 「どのような機能が必要か」
- 「ユーザーがどこで面倒に感じるか」
- 「どのようなUIなら継続しやすいか」
を考えながら改善を続けています。現在も自分で利用しながら継続的に改良しています。



http://127.0.0.1:8000
```

---

## ファイル構成

```txt
app.py
├ FastAPI
├ Gemini API連携
├ SQLite保存
├ AIレビューAPI

static/
├ index.html
├ app.js
└ styles.css

data/
└ pfc_camera.sqlite3
```

---

## 今後の改善予定

- 栄養推定精度向上
- グラフ表示
- クラウド保存
- ユーザー認証
- PWA対応
- 食事提案機能
- モバイルUI改善
- デプロイ完遂

---

## 作成背景

食事管理は「続けること」が最も難しいと感じています。
そのため、「入力の手間を減らし、できるだけ自然に継続できること」を重視して設計しました。
今後も、自分自身で使いながら改善を続けていく予定です。
