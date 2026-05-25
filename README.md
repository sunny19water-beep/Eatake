# Eatake

写真を撮るだけで、食事のカロリー・PFC・糖質・食物繊維・塩分をざっくり記録できる食事管理アプリです。

Eatake は、完璧な栄養管理よりも「記録を続けやすいこと」を重視したプロトタイプです。AI推定なので数値は目安ですが、毎日の食事を軽く振り返るきっかけを作ります。

## 主な機能

- 食事写真からAIで栄養推定
- 栄養成分表示の読み取り
- 写真がない時の文面記録
- 記録前のAI推定結果確認
- 1日・1週間・カレンダー別の記録表示
- 目標体重、TDEE、目標PFCの表示
- AIレビュー
- 継続ストリークと励まし表示
- Tips表示
- 食事記録の編集・削除
- 全データ削除
- フィードバック送信
- PWA対応

## 現在の公開状態

現在はプロトタイプ版として、ログインなしで使える設定にしています。

```text
AUTH_REQUIRED=false
```

この状態では、Firestoreを使う場合も全ユーザーが共通の `prototype` データ領域を使います。公開テストには便利ですが、個人情報を含む写真やメモを入れすぎないようにしてください。

本公開時は、認証を有効化してユーザーごとにデータを分ける想定です。

```text
AUTH_REQUIRED=true
```

## 使い方

1. `記録` 画面を開く
2. `すぐ撮る` で食事写真を撮る
3. AI推定結果を確認する
4. `これで記録` を押す
5. `レビュー` 画面で今日の振り返りを見る

写真がない場合は、`文面記録` から「おにぎり1個」「ラーメン食べた」のように入力できます。日付も選べるので、昨日の食事も後から記録できます。

## AIレビュー

AIレビューでは、以下の流れで食事を振り返ります。

- 今日の振り返り
- よかった点
- 継続のこと
- 次の一手

設定からレビューの口調を変更できます。

- 甘目
- 普通
- 厳しめ

デフォルトは `甘目` です。

## 栄養推定について

画像認識とAI推定による概算です。実際の食材量、調理方法、商品表示によって数値は変わります。

正確な数値が必要な場合は、栄養成分表示を撮影するか、記録後に編集してください。

## データとプライバシー

Eatakeでは、以下の情報を扱います。

- 食事写真
- 食事名
- カロリー、PFC、糖質、食物繊維、塩分
- 体重、身長、年齢、目的などの設定
- AIレビュー
- フィードバック内容

写真は表示用に圧縮して保存します。プロトタイプ版ではログインなしの共通領域に保存されるため、個人情報が写る写真の利用は避けてください。

設定画面から、すべての記録を削除できます。

## Render環境変数

Renderで動かす場合は、Environment Variables に以下を設定します。

```text
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
AI_DAILY_LIMIT=10

STORAGE_BACKEND=firestore
AUTH_REQUIRED=false
PROTOTYPE_USER_ID=prototype

APP_BASE_URL=https://your-render-url.onrender.com
SESSION_SECRET=long_random_secret

LINE_REQUEST_EMAIL=false
LINE_CHANNEL_ID=your_line_channel_id
LINE_CHANNEL_SECRET=your_line_channel_secret

GOOGLE_APPLICATION_CREDENTIALS_JSON=your_service_account_json
```

プロトタイプとして出すだけなら、`AUTH_REQUIRED=false` のままで使えます。

LINE認証を有効化する場合は、`AUTH_REQUIRED=true` に変更し、LINE Developers側のCallback URLを以下に設定します。

```text
https://your-render-url.onrender.com/auth/line/callback
```

## 技術構成

- Backend: FastAPI
- Frontend: React / HTML / CSS / JavaScript
- AI: Gemini API
- Database: SQLite / Firestore
- Deploy: Render
- App形式: PWA

## 今後の予定

- 広告枠の追加
- DB設計の整理
- 認証あり本番モードへの切り替え
- PWA配布導線の整理
- 必要に応じてCapacitorでスマホアプリ化
