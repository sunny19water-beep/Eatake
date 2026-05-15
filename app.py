import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "pfc_camera.sqlite3"
LOG_RETENTION_DAYS = 90

load_dotenv(BASE_DIR / ".env")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

app = FastAPI(title="PFC Camera Logger")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

PURPOSES = ["ダイエット", "増量", "健康維持", "減量"]
PURPOSE_SET = set(PURPOSES)
DEFAULT_SETTINGS = {
    "age": "",
    "weight": "",
    "height": "",
    "sex": "",
    "purpose": "健康維持",
}


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eaten_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                calories REAL NOT NULL DEFAULT 0,
                protein REAL NOT NULL DEFAULT 0,
                fat REAL NOT NULL DEFAULT 0,
                carbs REAL NOT NULL DEFAULT 0,
                fiber REAL NOT NULL DEFAULT 0,
                sugar REAL NOT NULL DEFAULT 0,
                salt REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(eaten_date)")
        ensure_meal_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reviews (
                eaten_date TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                review TEXT NOT NULL
            )
            """
        )
        purge_old_logs(conn)


@app.on_event("startup")
def startup() -> None:
    init_db()


def today_key() -> str:
    return date.today().isoformat()


def oldest_kept_day() -> str:
    return (date.today() - timedelta(days=LOG_RETENTION_DAYS - 1)).isoformat()


def purge_old_logs(conn: sqlite3.Connection) -> None:
    cutoff = oldest_kept_day()
    conn.execute("DELETE FROM meals WHERE eaten_date < ?", (cutoff,))
    conn.execute("DELETE FROM daily_reviews WHERE eaten_date < ?", (cutoff,))
    conn.commit()


def ensure_meal_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(meals)").fetchall()}
    for column in ("fiber", "sugar"):
        if column not in columns:
            conn.execute(f"ALTER TABLE meals ADD COLUMN {column} REAL NOT NULL DEFAULT 0")


init_db()


def normalize_setting_text(value: Any) -> str:
    return str(value or "").strip()


def get_settings(conn: sqlite3.Connection | None = None) -> dict[str, str]:
    owns_connection = conn is None
    if conn is None:
        conn = get_db()
    rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
    if owns_connection:
        conn.close()

    settings = DEFAULT_SETTINGS.copy()
    settings.update({row["key"]: row["value"] for row in rows})
    return settings


def save_settings(payload: dict[str, Any]) -> dict[str, str]:
    settings = {
        "age": normalize_setting_text(payload.get("age")),
        "weight": normalize_setting_text(payload.get("weight")),
        "height": normalize_setting_text(payload.get("height")),
        "sex": normalize_setting_text(payload.get("sex")),
        "purpose": normalize_setting_text(payload.get("purpose")) or DEFAULT_SETTINGS["purpose"],
    }
    if settings["purpose"] not in PURPOSE_SET:
        raise HTTPException(status_code=400, detail="目的は指定された4択から選んでください。")

    for key in ("age", "weight", "height"):
        if settings[key] and clamp_number(settings[key], maximum=400) <= 0:
            raise HTTPException(status_code=400, detail=f"{key} は正の数値で入力してください。")

    with get_db() as conn:
        for key, value in settings.items():
            conn.execute(
                """
                INSERT INTO user_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
    return settings


def row_to_meal(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["eaten_date"],
        "created_at": row["created_at"],
        "dish_name": row["dish_name"],
        "calories": round(float(row["calories"])),
        "protein": round(float(row["protein"]), 1),
        "fat": round(float(row["fat"]), 1),
        "carbs": round(float(row["carbs"]), 1),
        "fiber": round(float(row["fiber"]), 1),
        "sugar": round(float(row["sugar"]), 1),
        "salt": round(float(row["salt"]), 1),
        "confidence": round(float(row["confidence"]), 2),
        "notes": row["notes"],
    }


def empty_totals() -> dict[str, float]:
    return {
        "calories": 0,
        "protein": 0,
        "fat": 0,
        "carbs": 0,
        "fiber": 0,
        "sugar": 0,
        "salt": 0,
    }


def totals_for_day(conn: sqlite3.Connection, day: str) -> dict[str, float]:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(calories), 0) AS calories,
            COALESCE(SUM(protein), 0) AS protein,
            COALESCE(SUM(fat), 0) AS fat,
            COALESCE(SUM(carbs), 0) AS carbs,
            COALESCE(SUM(fiber), 0) AS fiber,
            COALESCE(SUM(sugar), 0) AS sugar,
            COALESCE(SUM(salt), 0) AS salt
        FROM meals
        WHERE eaten_date = ?
        """,
        (day,),
    ).fetchone()
    if row is None:
        return empty_totals()
    return {
        "calories": round(float(row["calories"])),
        "protein": round(float(row["protein"]), 1),
        "fat": round(float(row["fat"]), 1),
        "carbs": round(float(row["carbs"]), 1),
        "fiber": round(float(row["fiber"]), 1),
        "sugar": round(float(row["sugar"]), 1),
        "salt": round(float(row["salt"]), 1),
    }


def meals_for_day(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM meals
        WHERE eaten_date = ?
        ORDER BY created_at DESC, id DESC
        """,
        (day,),
    ).fetchall()
    return [row_to_meal(row) for row in rows]


def day_payload(day: str) -> dict[str, Any]:
    with get_db() as conn:
        purge_old_logs(conn)
        review_row = conn.execute(
            "SELECT review, created_at FROM daily_reviews WHERE eaten_date = ?",
            (day,),
        ).fetchone()
        return {
            "date": day,
            "totals": totals_for_day(conn, day),
            "meals": meals_for_day(conn, day),
            "review": None
            if review_row is None
            else {"text": review_row["review"], "created_at": review_row["created_at"]},
        }


def available_days(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    owns_connection = conn is None
    if conn is None:
        conn = get_db()
    purge_old_logs(conn)
    rows = conn.execute(
        """
        SELECT eaten_date, COUNT(*) AS meal_count, COALESCE(SUM(calories), 0) AS calories
        FROM meals
        GROUP BY eaten_date
        ORDER BY eaten_date DESC
        """
    ).fetchall()
    if owns_connection:
        conn.close()
    day_map = {
        row["eaten_date"]: {
            "date": row["eaten_date"],
            "meal_count": int(row["meal_count"]),
            "calories": round(float(row["calories"])),
        }
        for row in rows
    }
    today = date.today()
    for offset in range(LOG_RETENTION_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        day_map.setdefault(day, {"date": day, "meal_count": 0, "calories": 0})
    return [day_map[key] for key in sorted(day_map.keys(), reverse=True)]


def profile_summary(settings: dict[str, str]) -> str:
    return (
        f"年齢: {settings.get('age') or '未設定'}、"
        f"体重: {settings.get('weight') or '未設定'}kg、"
        f"身長: {settings.get('height') or '未設定'}cm、"
        f"性別: {settings.get('sex') or '未設定'}、"
        f"目的: {settings.get('purpose') or '未設定'}"
    )


def build_review_text(day: str, totals: dict[str, float], meals: list[dict[str, Any]], settings: dict[str, str]) -> str:
    meal_lines = "\n".join(
        f"- {meal['dish_name']}: {meal['calories']}kcal P{meal['protein']}g F{meal['fat']}g C{meal['carbs']}g 糖質{meal['sugar']}g 食物繊維{meal['fiber']}g 塩分{meal['salt']}g"
        for meal in meals
    ) or "- 記録なし"
    return f"""
あなたは栄養管理アプリの食事レビューAIです。
ユーザーの設定と1日の食事ログを見て、目的に合わせた短いレビューを日本語で返してください。
医学的な断定は避け、食事改善のヒントとして書いてください。

日付: {day}
ユーザー設定: {profile_summary(settings)}
1日の合計:
- カロリー: {totals['calories']}kcal
- タンパク質: {totals['protein']}g
- 脂質: {totals['fat']}g
- 炭水化物: {totals['carbs']}g
- 糖質: {totals['sugar']}g
- 食物繊維: {totals['fiber']}g
- 塩分: {totals['salt']}g
食事ログ:
{meal_lines}

返答は180文字以内。良かった点、気をつける点、次の一手を含めてください。
"""


def save_review(day: str, review: str) -> dict[str, str]:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO daily_reviews (eaten_date, created_at, review)
            VALUES (?, ?, ?)
            ON CONFLICT(eaten_date) DO UPDATE SET
                created_at = excluded.created_at,
                review = excluded.review
            """,
            (day, created_at, review),
        )
    return {"text": review, "created_at": created_at}


def demo_review(day: str, totals: dict[str, float], settings: dict[str, str]) -> str:
    purpose = settings.get("purpose") or "健康維持"
    if totals["calories"] == 0:
        return f"{day}はまだ食事記録がありません。{purpose}の判断には、まず1食だけでも撮影して記録を増やしましょう。"
    return (
        f"{purpose}向けに見ると、今日はP{totals['protein']}g、糖質{totals['sugar']}g、"
        f"食物繊維{totals['fiber']}g、塩分{totals['salt']}gです。次の食事は野菜と水分を足すと整えやすいです。"
    )


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Gemini did not return JSON.")
    return json.loads(cleaned[start : end + 1])


def clamp_number(value: Any, minimum: float = 0, maximum: float = 5000) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))


async def estimate_nutrition(image: UploadFile, image_bytes: bytes) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {
            "dish_name": "デモ解析: 食事",
            "calories": 520,
            "protein": 28,
            "fat": 18,
            "carbs": 62,
            "fiber": 4,
            "sugar": 58,
            "salt": 2.1,
            "confidence": 0.35,
            "notes": "GEMINI_API_KEY 未設定のためデモ値です。",
        }

    client = genai.Client(api_key=api_key)
    mime_type = image.content_type or "image/jpeg"
    prompt = """
あなたは栄養記録アプリの画像解析エンジンです。
写真の食事を1食分として推定し、カロリー、PFC、炭水化物、糖質、食物繊維、塩分を概算してください。
見えない材料は一般的な外食・家庭料理の中央値で推定してください。
食品が写っていない場合は料理名を「料理なし」にして数値は0にしてください。
返答は次のJSONだけにしてください。
{
  "dish_name": "料理名",
  "calories": 0,
  "protein": 0,
  "fat": 0,
  "carbs": 0,
  "fiber": 0,
  "sugar": 0,
  "salt": 0,
  "confidence": 0.0,
  "notes": "短い注意点"
}
caloriesはkcal、protein/fat/carbs/fiber/sugar/saltはg、confidenceは0.0から1.0です。
糖質は、推定できる場合は炭水化物から食物繊維を除いた量として返してください。
"""
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")

    try:
        result = parse_json_response(response.text or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini response parse error: {exc}")

    return {
        "dish_name": str(result.get("dish_name") or "食事"),
        "calories": round(clamp_number(result.get("calories"))),
        "protein": round(clamp_number(result.get("protein"), maximum=500), 1),
        "fat": round(clamp_number(result.get("fat"), maximum=500), 1),
        "carbs": round(clamp_number(result.get("carbs"), maximum=1000), 1),
        "fiber": round(clamp_number(result.get("fiber"), maximum=300), 1),
        "sugar": round(clamp_number(result.get("sugar"), maximum=1000), 1),
        "salt": round(clamp_number(result.get("salt"), maximum=100), 1),
        "confidence": round(clamp_number(result.get("confidence"), maximum=1), 2),
        "notes": str(result.get("notes") or ""),
    }


async def estimate_nutrition_label(image: UploadFile, image_bytes: bytes) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {
            "dish_name": "デモ解析: 栄養成分表示",
            "calories": 210,
            "protein": 8,
            "fat": 6,
            "carbs": 32,
            "fiber": 3,
            "sugar": 29,
            "salt": 1.4,
            "confidence": 0.35,
            "notes": "GEMINI_API_KEY 未設定のためデモ値です。",
        }

    client = genai.Client(api_key=api_key)
    mime_type = image.content_type or "image/jpeg"
    prompt = """
あなたは栄養成分表示を読み取るOCRエンジンです。
写真に写っている栄養成分表示の数値を読み取り、書かれている数値を優先して記録してください。
「1食あたり」「1包装あたり」「1個あたり」があればその単位の数値を採用してください。
「100gあたり」しかない場合も、その数値をそのまま採用し、notesに「100gあたり」と書いてください。
糖質が書かれていない場合は、炭水化物 - 食物繊維で推定してください。
食物繊維が書かれていない場合は0にしてください。
食塩相当量を salt として返してください。ナトリウムだけの場合は食塩相当量へ換算してください。
返答は次のJSONだけにしてください。
{
  "dish_name": "商品名または栄養成分表示",
  "calories": 0,
  "protein": 0,
  "fat": 0,
  "carbs": 0,
  "fiber": 0,
  "sugar": 0,
  "salt": 0,
  "confidence": 0.0,
  "notes": "採用した単位や読取注意点"
}
caloriesはkcal、protein/fat/carbs/fiber/sugar/saltはgです。
"""
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                prompt,
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")

    try:
        result = parse_json_response(response.text or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini response parse error: {exc}")

    carbs = clamp_number(result.get("carbs"), maximum=1000)
    fiber = clamp_number(result.get("fiber"), maximum=300)
    sugar = clamp_number(result.get("sugar"), maximum=1000)
    if sugar == 0 and carbs > 0:
        sugar = max(0, carbs - fiber)

    return {
        "dish_name": str(result.get("dish_name") or "栄養成分表示"),
        "calories": round(clamp_number(result.get("calories"))),
        "protein": round(clamp_number(result.get("protein"), maximum=500), 1),
        "fat": round(clamp_number(result.get("fat"), maximum=500), 1),
        "carbs": round(carbs, 1),
        "fiber": round(fiber, 1),
        "sugar": round(sugar, 1),
        "salt": round(clamp_number(result.get("salt"), maximum=100), 1),
        "confidence": round(clamp_number(result.get("confidence"), maximum=1), 2),
        "notes": str(result.get("notes") or "栄養成分表示から採用"),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/today")
def get_today() -> dict[str, Any]:
    return day_payload(today_key())


@app.get("/api/days")
def get_days() -> dict[str, Any]:
    return {"days": available_days()}


@app.get("/api/days/{day}")
def get_day(day: str) -> dict[str, Any]:
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日付はYYYY-MM-DDで指定してください。")
    return day_payload(day)


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    return {"settings": get_settings(), "purposes": PURPOSES}


@app.put("/api/settings")
def update_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"settings": save_settings(payload), "purposes": PURPOSES}


@app.post("/api/days/{day}/review")
def review_day(day: str) -> dict[str, Any]:
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日付はYYYY-MM-DDで指定してください。")

    with get_db() as conn:
        totals = totals_for_day(conn, day)
        meals = meals_for_day(conn, day)
        settings = get_settings(conn)

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        review = demo_review(day, totals, settings)
        return {"date": day, "review": save_review(day, review)}

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[build_review_text(day, totals, meals, settings)],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")

    review = (response.text or "").strip()
    if not review:
        raise HTTPException(status_code=502, detail="Gemini review was empty.")
    return {"date": day, "review": save_review(day, review[:500])}


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    estimate = await estimate_nutrition(file, image_bytes)
    return save_meal_estimate(estimate)


@app.post("/api/analyze-label")
async def analyze_label(file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    estimate = await estimate_nutrition_label(file, image_bytes)
    return save_meal_estimate(estimate)


async def read_image(file: UploadFile) -> bytes:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="画像ファイルを送ってください。")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="画像が空です。")
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="画像は8MB以下にしてください。")
    return image_bytes


def save_meal_estimate(estimate: dict[str, Any]) -> dict[str, Any]:
    created_at = datetime.now().isoformat(timespec="seconds")
    day = today_key()

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meals (
                eaten_date, created_at, dish_name, calories, protein, fat,
                carbs, fiber, sugar, salt, confidence, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                created_at,
                estimate["dish_name"],
                estimate["calories"],
                estimate["protein"],
                estimate["fat"],
                estimate["carbs"],
                estimate["fiber"],
                estimate["sugar"],
                estimate["salt"],
                estimate["confidence"],
                estimate["notes"],
            ),
        )
        row = conn.execute("SELECT * FROM meals WHERE id = ?", (cursor.lastrowid,)).fetchone()
        meal = row_to_meal(row)
        payload = {
            "meal": meal,
            "totals": totals_for_day(conn, day),
            "days": available_days(conn),
        }
    return payload


@app.delete("/api/meals/{meal_id}")
def delete_meal(meal_id: int) -> dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT eaten_date FROM meals WHERE id = ?", (meal_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="記録が見つかりません。")
        day = row["eaten_date"]
        conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
        return {
            "date": day,
            "totals": totals_for_day(conn, day),
            "meals": meals_for_day(conn, day),
            "days": available_days(conn),
        }
