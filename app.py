import json
import os
import secrets
import sqlite3
import base64
import hashlib
import hmac
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv
import httpx
from fastapi import Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore
from google import genai
from google.genai import types
from google.oauth2 import service_account
from PIL import Image


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "pfc_camera.sqlite3"
LOG_RETENTION_DAYS = 90

load_dotenv(BASE_DIR / ".env")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sqlite").lower()
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "false").lower() == "true"
PROTOTYPE_USER_ID = os.getenv("PROTOTYPE_USER_ID", "prototype")
APP_BASE_URL = (
    os.getenv("APP_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or "http://127.0.0.1:8000"
).rstrip("/")
SESSION_COOKIE = "eatake_session"
SESSION_DAYS = 30
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "10"))
SESSION_SECRET = os.getenv("SESSION_SECRET") or os.getenv("LINE_CHANNEL_SECRET") or APP_BASE_URL
LINE_REQUEST_EMAIL = os.getenv("LINE_REQUEST_EMAIL", "false").lower() == "true"

app = FastAPI(title="PFC Camera Logger")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
    if request.url.path.startswith("/api/") or request.url.path.startswith("/auth/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

PURPOSES = ["ダイエット", "増量", "健康維持", "減量"]
PURPOSE_SET = set(PURPOSES)
REVIEW_TONES = ["甘目", "普通", "厳しめ"]
REVIEW_TONE_SET = set(REVIEW_TONES)
DEFAULT_SETTINGS = {
    "age": "",
    "weight": "",
    "height": "",
    "sex": "",
    "basal_metabolism": "",
    "exercise_per_week": "",
    "target_weight": "",
    "target_weeks": "",
    "purpose": "健康維持",
    "review_tone": "甘目",
}

_firestore_client: firestore.Client | None = None


def fs() -> firestore.Client:
    global _firestore_client
    if _firestore_client is None:
        credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
        if credentials_json:
            credentials_info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(credentials_info)
            _firestore_client = firestore.Client(
                project=credentials_info.get("project_id"),
                credentials=credentials,
            )
        else:
            _firestore_client = firestore.Client()
    return _firestore_client


def now_key() -> str:
    return datetime.now().isoformat(timespec="seconds")


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": "line",
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
        "provider": user.get("provider", ""),
    }


def session_doc_id(token: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def cookie_options() -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": APP_BASE_URL.startswith("https://"),
        "samesite": "lax",
        "path": "/",
    }


def require_user(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="LINEログインが必要です。")
    return user


def current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    if STORAGE_BACKEND == "firestore":
        snap = fs().collection("sessions").document(session_doc_id(token)).get()
        if not snap.exists:
            return None
        session = snap.to_dict() or {}
        if session.get("expires_at", "") <= now_key():
            fs().collection("sessions").document(session_doc_id(token)).delete()
            return None
        user_snap = fs().collection("users").document(str(session.get("user_id"))).get()
        if not user_snap.exists:
            return None
        return user_snap.to_dict()
    return None


def create_line_session(response: Response, profile: dict[str, Any]) -> None:
    user_id = str(profile.get("sub") or profile.get("userId") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="LINEユーザーIDを取得できませんでした。")
    user = {
        "id": user_id,
        "provider": "line",
        "name": profile.get("name") or profile.get("displayName") or "",
        "picture": profile.get("picture") or profile.get("pictureUrl") or "",
        "updated_at": now_key(),
    }
    if profile.get("email"):
        user["email"] = profile.get("email", "")
    if STORAGE_BACKEND == "firestore":
        fs().collection("users").document(user_id).set(user, merge=True)
        token = secrets.token_urlsafe(32)
        fs().collection("sessions").document(session_doc_id(token)).set(
            {
                "user_id": user_id,
                "created_at": now_key(),
                "expires_at": (datetime.now() + timedelta(days=30)).isoformat(timespec="seconds"),
                "rotated_at": now_key(),
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=60 * 60 * 24 * SESSION_DAYS,
            **cookie_options(),
        )


def clear_session(response: Response, request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and STORAGE_BACKEND == "firestore":
        fs().collection("sessions").document(session_doc_id(token)).delete()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", secure=APP_BASE_URL.startswith("https://"))


def remember_line_state() -> tuple[str, str]:
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    if STORAGE_BACKEND == "firestore":
        fs().collection("auth_states").document(state).set(
            {
                "provider": "line",
                "nonce": nonce,
                "created_at": now_key(),
                "expires_at": (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds"),
            }
        )
    return state, nonce


def consume_line_state(state: str) -> str:
    if STORAGE_BACKEND != "firestore":
        return ""
    ref = fs().collection("auth_states").document(state)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=400, detail="LINEログイン状態の確認に失敗しました。")
    state_data = snap.to_dict() or {}
    expires_at = state_data.get("expires_at") or state_data.get("created_at", "")
    if expires_at <= now_key():
        ref.delete()
        raise HTTPException(status_code=400, detail="LINEログインの有効期限が切れました。")
    ref.delete()
    return str(state_data.get("nonce") or "")


def scoped_user_id(request: Request) -> str | None:
    if STORAGE_BACKEND != "firestore":
        return None
    if not AUTH_REQUIRED:
        return PROTOTYPE_USER_ID
    return require_user(request)["id"]


def ai_limit_detail() -> str:
    return f"今日のAI解析は{AI_DAILY_LIMIT}回までです。明日また使えます。"


def usage_status(user_id: str | None = None) -> dict[str, int]:
    day = today_key()
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            return {"limit": AI_DAILY_LIMIT, "used": 0, "remaining": AI_DAILY_LIMIT}
        snap = user_doc(user_id).collection("usage").document(day).get()
        used = int((snap.to_dict() or {}).get("ai_count", 0)) if snap.exists else 0
    else:
        with get_db() as conn:
            row = conn.execute(
                "SELECT count FROM ai_usage WHERE scope = ? AND usage_date = ?",
                ("local", day),
            ).fetchone()
            used = int(row["count"]) if row else 0
    return {"limit": AI_DAILY_LIMIT, "used": used, "remaining": max(0, AI_DAILY_LIMIT - used)}


def consume_ai_quota(user_id: str | None = None) -> dict[str, int]:
    day = today_key()
    if AI_DAILY_LIMIT <= 0:
        return {"limit": AI_DAILY_LIMIT, "used": 0, "remaining": 0}

    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        doc_ref = user_doc(user_id).collection("usage").document(day)
        transaction = fs().transaction()

        @firestore.transactional
        def update_in_transaction(txn, ref):
            snap = ref.get(transaction=txn)
            used = int((snap.to_dict() or {}).get("ai_count", 0)) if snap.exists else 0
            if used >= AI_DAILY_LIMIT:
                raise HTTPException(status_code=429, detail=ai_limit_detail())
            next_used = used + 1
            txn.set(ref, {"date": day, "ai_count": next_used, "updated_at": now_key()}, merge=True)
            return {"limit": AI_DAILY_LIMIT, "used": next_used, "remaining": AI_DAILY_LIMIT - next_used}

        return update_in_transaction(transaction, doc_ref)

    with get_db() as conn:
        row = conn.execute(
            "SELECT count FROM ai_usage WHERE scope = ? AND usage_date = ?",
            ("local", day),
        ).fetchone()
        used = int(row["count"]) if row else 0
        if used >= AI_DAILY_LIMIT:
            raise HTTPException(status_code=429, detail=ai_limit_detail())
        next_used = used + 1
        conn.execute(
            """
            INSERT INTO ai_usage (scope, usage_date, count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope, usage_date) DO UPDATE SET
                count = excluded.count,
                updated_at = excluded.updated_at
            """,
            ("local", day, next_used, now_key()),
        )
        return {"limit": AI_DAILY_LIMIT, "used": next_used, "remaining": AI_DAILY_LIMIT - next_used}


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    if STORAGE_BACKEND == "firestore":
        return
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
                image_data TEXT NOT NULL DEFAULT '',
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_diaries (
                eaten_date TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                diary TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_usage (
                scope TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope, usage_date)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        purge_old_logs(conn)


@app.on_event("startup")
def startup() -> None:
    init_db()


def today_key() -> str:
    return date.today().isoformat()


def normalize_day(value: Any | None = None) -> str:
    day = normalize_setting_text(value) if value is not None else ""
    if not day:
        return today_key()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日付はYYYY-MM-DDで指定してください。")
    return day


def oldest_kept_day() -> str:
    return (date.today() - timedelta(days=LOG_RETENTION_DAYS - 1)).isoformat()


def purge_old_logs(conn: sqlite3.Connection) -> None:
    cutoff = oldest_kept_day()
    conn.execute("DELETE FROM meals WHERE eaten_date < ?", (cutoff,))
    conn.execute("DELETE FROM daily_reviews WHERE eaten_date < ?", (cutoff,))
    conn.execute("DELETE FROM daily_diaries WHERE eaten_date < ?", (cutoff,))
    conn.commit()


def ensure_meal_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(meals)").fetchall()}
    for column in ("fiber", "sugar"):
        if column not in columns:
            conn.execute(f"ALTER TABLE meals ADD COLUMN {column} REAL NOT NULL DEFAULT 0")
    if "image_data" not in columns:
        conn.execute("ALTER TABLE meals ADD COLUMN image_data TEXT NOT NULL DEFAULT ''")


init_db()


def normalize_setting_text(value: Any) -> str:
    return str(value or "").strip()


def get_settings(conn: sqlite3.Connection | None = None, user_id: str | None = None) -> dict[str, str]:
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        snap = fs().collection("users").document(user_id).collection("meta").document("settings").get()
        settings = DEFAULT_SETTINGS.copy()
        if snap.exists:
            settings.update(snap.to_dict() or {})
        return settings

    owns_connection = conn is None
    if conn is None:
        conn = get_db()
    rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
    if owns_connection:
        conn.close()

    settings = DEFAULT_SETTINGS.copy()
    settings.update({row["key"]: row["value"] for row in rows})
    return settings


def save_settings(payload: dict[str, Any], user_id: str | None = None) -> dict[str, str]:
    settings = {
        "age": normalize_setting_text(payload.get("age")),
        "weight": normalize_setting_text(payload.get("weight")),
        "height": normalize_setting_text(payload.get("height")),
        "sex": normalize_setting_text(payload.get("sex")),
        "basal_metabolism": normalize_setting_text(payload.get("basal_metabolism")),
        "exercise_per_week": normalize_setting_text(payload.get("exercise_per_week")),
        "target_weight": normalize_setting_text(payload.get("target_weight")),
        "target_weeks": normalize_setting_text(payload.get("target_weeks")),
        "purpose": normalize_setting_text(payload.get("purpose")) or DEFAULT_SETTINGS["purpose"],
        "review_tone": normalize_setting_text(payload.get("review_tone")) or DEFAULT_SETTINGS["review_tone"],
    }
    if settings["purpose"] not in PURPOSE_SET:
        raise HTTPException(status_code=400, detail="目的は指定された4択から選んでください。")
    if settings["review_tone"] not in REVIEW_TONE_SET:
        raise HTTPException(status_code=400, detail="AIレビューの口調は指定された選択肢から選んでください。")

    for key in ("age", "weight", "height"):
        if settings[key] and clamp_number(settings[key], maximum=400) <= 0:
            raise HTTPException(status_code=400, detail=f"{key} は正の数値で入力してください。")
    if settings["basal_metabolism"] and clamp_number(settings["basal_metabolism"], maximum=5000) <= 0:
        raise HTTPException(status_code=400, detail="基礎代謝は正の数値で入力してください。")
    if settings["exercise_per_week"] and clamp_number(settings["exercise_per_week"], maximum=14) < 0:
        raise HTTPException(status_code=400, detail="運動回数は0以上の数値で入力してください。")
    if settings["target_weight"] and clamp_number(settings["target_weight"], maximum=400) <= 0:
        raise HTTPException(status_code=400, detail="目標体重は正の数値で入力してください。")
    if settings["target_weeks"] and clamp_number(settings["target_weeks"], maximum=260) <= 0:
        raise HTTPException(status_code=400, detail="目標週数は正の数値で入力してください。")

    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        fs().collection("users").document(user_id).collection("meta").document("settings").set(settings)
        return settings

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
        "image_data": row["image_data"],
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


def activity_factor(exercise_per_week: Any) -> float:
    weekly = clamp_number(exercise_per_week, minimum=0, maximum=14)
    if weekly <= 0:
        return 1.2
    if weekly <= 2:
        return 1.375
    if weekly <= 4:
        return 1.55
    if weekly <= 6:
        return 1.725
    return 1.9


def calculate_tdee(settings: dict[str, str]) -> dict[str, Any]:
    age = clamp_number(settings.get("age"), maximum=120)
    weight = clamp_number(settings.get("weight"), maximum=400)
    height = clamp_number(settings.get("height"), maximum=260)
    basal_metabolism = clamp_number(settings.get("basal_metabolism"), maximum=5000)
    sex = settings.get("sex", "")
    factor = activity_factor(settings.get("exercise_per_week"))
    if basal_metabolism > 0:
        return {
            "ready": True,
            "bmr": round(basal_metabolism),
            "tdee": round(basal_metabolism * factor),
            "activity_factor": factor,
            "message": "入力された基礎代謝から推定TDEEを計算しました。",
        }

    if age <= 0 or weight <= 0 or height <= 0 or sex not in {"男性", "女性"}:
        return {
            "ready": False,
            "bmr": 0,
            "tdee": 0,
            "activity_factor": factor,
            "message": "基礎代謝、または年齢・体重・身長・性別を設定するとTDEEを表示します。",
        }

    if sex == "男性":
        bmr = (13.397 * weight) + (4.799 * height) - (5.677 * age) + 88.362
    else:
        bmr = (9.247 * weight) + (3.098 * height) - (4.33 * age) + 447.593
    tdee = bmr * factor
    return {
        "ready": True,
        "bmr": round(bmr),
        "tdee": round(tdee),
        "activity_factor": factor,
        "message": "推定TDEEを計算しました。",
    }


def bmi_category(bmi: float) -> str:
    if bmi < 18.5:
        return "低体重"
    if bmi < 25:
        return "普通体重"
    if bmi < 30:
        return "肥満1度"
    if bmi < 35:
        return "肥満2度"
    if bmi < 40:
        return "肥満3度"
    return "肥満4度"


def profile_metrics(settings: dict[str, str]) -> dict[str, Any]:
    weight = clamp_number(settings.get("weight"), maximum=400)
    height = clamp_number(settings.get("height"), maximum=260)
    target_weight = clamp_number(settings.get("target_weight"), maximum=400)
    target_weeks = clamp_number(settings.get("target_weeks"), maximum=260)
    tdee = calculate_tdee(settings)

    bmi = 0.0
    category = "未設定"
    if weight > 0 and height > 0:
        bmi = weight / ((height / 100) ** 2)
        category = bmi_category(bmi)

    kg_to_lose = max(0, weight - target_weight) if weight > 0 and target_weight > 0 else 0
    target_daily_deficit = 0
    if kg_to_lose > 0 and target_weeks > 0:
        target_daily_deficit = round((kg_to_lose * 7700) / (target_weeks * 7))

    return {
        "ready": weight > 0 and height > 0,
        "bmi": round(bmi, 1) if bmi else 0,
        "category": category,
        "target_weight": round(target_weight, 1) if target_weight else 0,
        "target_weeks": round(target_weeks) if target_weeks else 0,
        "kg_to_lose": round(kg_to_lose, 1),
        "target_daily_deficit": target_daily_deficit,
        "bmr": tdee["bmr"],
        "tdee": tdee["tdee"],
        "message": "BMIと目標赤字を計算しました。" if bmi else "体重と身長を設定するとBMIを表示します。",
    }


def energy_summary(totals: dict[str, float], settings: dict[str, str]) -> dict[str, Any]:
    tdee = calculate_tdee(settings)
    profile = profile_metrics(settings)
    calories = round(float(totals.get("calories", 0)))
    balance = calories - int(tdee["tdee"])
    if not tdee["ready"]:
        balance = 0
    return {
        **tdee,
        "calories": calories,
        "balance": balance,
        "deficit": max(0, -balance),
        "surplus": max(0, balance),
        "profile": profile,
        "target_daily_deficit": profile["target_daily_deficit"],
        "target_pfc": target_pfc(settings, tdee, profile),
    }


def target_pfc(settings: dict[str, str], tdee: dict[str, Any] | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    if tdee is None:
        tdee = calculate_tdee(settings)
    if profile is None:
        profile = profile_metrics(settings)
    if not tdee.get("ready"):
        return {"ready": False, "calories": 0, "protein": 0, "fat": 0, "carbs": 0, "ratio": ""}

    purpose = settings.get("purpose") or "健康維持"
    base = int(tdee["tdee"])
    if purpose in {"ダイエット", "減量"}:
        deficit = int(profile.get("target_daily_deficit") or 500)
        calories = max(1200, base - min(deficit, 900))
        ratio = (0.30, 0.25, 0.45)
    elif purpose == "増量":
        calories = base + 300
        ratio = (0.25, 0.25, 0.50)
    else:
        calories = base
        ratio = (0.20, 0.25, 0.55)

    protein = round((calories * ratio[0]) / 4, 1)
    fat = round((calories * ratio[1]) / 9, 1)
    carbs = round((calories * ratio[2]) / 4, 1)
    return {
        "ready": True,
        "calories": round(calories),
        "protein": protein,
        "fat": fat,
        "carbs": carbs,
        "ratio": f"P{round(ratio[0] * 100)} F{round(ratio[1] * 100)} C{round(ratio[2] * 100)}",
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


def totals_between(conn: sqlite3.Connection, start_day: str, end_day: str) -> dict[str, float]:
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
        WHERE eaten_date BETWEEN ? AND ?
        """,
        (start_day, end_day),
    ).fetchone()
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


def empty_diary() -> dict[str, str]:
    return {"text": "", "created_at": "", "updated_at": ""}


def diary_for_day(conn: sqlite3.Connection, day: str) -> dict[str, str]:
    row = conn.execute(
        "SELECT diary, created_at, updated_at FROM daily_diaries WHERE eaten_date = ?",
        (day,),
    ).fetchone()
    if row is None:
        return empty_diary()
    return {"text": row["diary"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def day_payload(day: str, user_id: str | None = None) -> dict[str, Any]:
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        return firestore_day_payload(user_id, day)

    with get_db() as conn:
        purge_old_logs(conn)
        review_row = conn.execute(
            "SELECT review, created_at FROM daily_reviews WHERE eaten_date = ?",
            (day,),
        ).fetchone()
        totals = totals_for_day(conn, day)
        return {
            "date": day,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(conn)),
            "meals": meals_for_day(conn, day),
            "diary": diary_for_day(conn, day),
            "review": None
            if review_row is None
            else {"text": review_row["review"], "created_at": review_row["created_at"]},
        }


def available_days(conn: sqlite3.Connection | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        return firestore_available_days(user_id)

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


def streak_status(user_id: str | None = None) -> dict[str, Any]:
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        return firestore_streak_status(user_id)

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT eaten_date
            FROM meals
            WHERE eaten_date >= ?
            GROUP BY eaten_date
            ORDER BY eaten_date DESC
            """,
            (oldest_kept_day(),),
        ).fetchall()
    recorded = {row["eaten_date"] for row in rows}
    today = date.today()
    streak = 0
    cursor = today
    while cursor.isoformat() in recorded:
        streak += 1
        cursor -= timedelta(days=1)

    yesterday_recorded = (today - timedelta(days=1)).isoformat() in recorded
    today_recorded = today.isoformat() in recorded
    if streak >= 3:
        message = f"{streak}日連続記録、えらすぎ。今日も続いてる。"
    elif today_recorded:
        message = "今日も撮ってくれてありがとう。小さく続いてます。"
    elif yesterday_recorded:
        message = "昨日は記録できてる。今日は1枚だけ撮れたら十分。"
    else:
        message = "昨日は休憩日。今日からまた軽く再スタート。"
    return {"streak": streak, "today_recorded": today_recorded, "message": message}


def weekly_summary(user_id: str | None = None) -> dict[str, Any]:
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        return firestore_weekly_summary(user_id)

    today = date.today()
    start_this_week = today - timedelta(days=today.weekday())
    start_last_week = start_this_week - timedelta(days=7)
    end_last_week = start_this_week - timedelta(days=1)
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT eaten_date, COUNT(*) AS meal_count
            FROM meals
            WHERE eaten_date BETWEEN ? AND ?
            GROUP BY eaten_date
            """,
            (start_last_week.isoformat(), end_last_week.isoformat()),
        ).fetchall()
    days_recorded = len(rows)
    meals_count = sum(int(row["meal_count"]) for row in rows)
    if days_recorded >= 5:
        text = "先週のあなたは、ちゃんと戻ってこられる人でした。"
    elif meals_count > 0:
        text = "先週のあなたは、完璧じゃなくても記録を残せた人でした。"
    else:
        text = "先週は休憩週。今週は1枚だけで十分です。"
    return {
        "show": today.weekday() == 0,
        "text": text,
        "start": start_last_week.isoformat(),
        "end": end_last_week.isoformat(),
    }


def current_week_payload(user_id: str | None = None, days_count: int = 7) -> dict[str, Any]:
    days_count = max(1, min(31, int(days_count or 7)))
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        return firestore_current_week_payload(user_id, days_count)

    today = date.today()
    start = today - timedelta(days=days_count - 1)
    days = []
    previous_start = start - timedelta(days=days_count)
    previous_end = start - timedelta(days=1)
    with get_db() as conn:
        purge_old_logs(conn)
        for offset in range(days_count):
            current = start + timedelta(days=offset)
            key = current.isoformat()
            meals = meals_for_day(conn, key)
            days.append(
                {
                    "date": key,
                    "totals": totals_for_day(conn, key),
                    "meal_count": len(meals),
                }
            )
        totals = totals_between(conn, start.isoformat(), today.isoformat())
        previous_totals = totals_between(conn, previous_start.isoformat(), previous_end.isoformat())
        previous_recorded_days = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT eaten_date) AS count
                FROM meals
                WHERE eaten_date BETWEEN ? AND ?
                """,
                (previous_start.isoformat(), previous_end.isoformat()),
            ).fetchone()["count"]
        )
    averages = {key: round(value / days_count, 1) for key, value in totals.items()}
    averages["calories"] = round(totals["calories"] / days_count)
    recorded_days = sum(1 for item in days if item["meal_count"] > 0)
    previous_averages = {key: round(value / days_count, 1) for key, value in previous_totals.items()}
    previous_averages["calories"] = round(previous_totals["calories"] / days_count)
    if recorded_days >= 5:
        message = "この1週間、かなり戻ってこられてる。続ける力が育ってます。"
    elif recorded_days > 0:
        message = "今週も記録を残せた日がある。それだけで次につながってます。"
    else:
        message = "今週はここからでOK。まず1回だけ記録してみよう。"
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "days_count": days_count,
        "totals": totals,
        "averages": averages,
        "previous_averages": previous_averages,
        "days": days,
        "comparison": average_comparison(recorded_days, previous_recorded_days, days_count),
        "message": message,
    }


def average_comparison(recorded_days: int, previous_recorded_days: int, days_count: int) -> dict[str, Any]:
    if recorded_days > previous_recorded_days:
        return {
            "tone": "good",
            "text": f"前の{days_count}日間より記録できた日が増えています。これはかなり良い流れです。",
        }
    if recorded_days == previous_recorded_days and recorded_days > 0:
        return {
            "tone": "good",
            "text": "前の期間と同じペースで戻ってこられています。継続できているのが強いです。",
        }
    if recorded_days > 0:
        return {
            "tone": "soft",
            "text": "前の期間より少なめでも、記録できた日があります。次は1回だけ増やせたら十分です。",
        }
    return {
        "tone": "soft",
        "text": "今回は休憩気味でした。今日ここから1つ残せば、また流れを作れます。",
    }


def user_doc(user_id: str):
    return fs().collection("users").document(user_id)


def meals_collection(user_id: str):
    return user_doc(user_id).collection("meals")


def reviews_collection(user_id: str):
    return user_doc(user_id).collection("daily_reviews")


def diaries_collection(user_id: str):
    return user_doc(user_id).collection("daily_diaries")


def firestore_meal_to_dict(doc) -> dict[str, Any]:
    data = doc.to_dict() or {}
    return {
        "id": doc.id,
        "date": data.get("eaten_date", ""),
        "created_at": data.get("created_at", ""),
        "dish_name": data.get("dish_name", "食事"),
        "calories": round(float(data.get("calories", 0))),
        "protein": round(float(data.get("protein", 0)), 1),
        "fat": round(float(data.get("fat", 0)), 1),
        "carbs": round(float(data.get("carbs", 0)), 1),
        "fiber": round(float(data.get("fiber", 0)), 1),
        "sugar": round(float(data.get("sugar", 0)), 1),
        "salt": round(float(data.get("salt", 0)), 1),
        "confidence": round(float(data.get("confidence", 0)), 2),
        "image_data": data.get("image_data", ""),
        "notes": data.get("notes", ""),
    }


def firestore_purge_old_logs(user_id: str) -> None:
    cutoff = oldest_kept_day()
    for doc in meals_collection(user_id).where("eaten_date", "<", cutoff).stream():
        doc.reference.delete()
    for doc in reviews_collection(user_id).where("eaten_date", "<", cutoff).stream():
        doc.reference.delete()
    for doc in diaries_collection(user_id).where("eaten_date", "<", cutoff).stream():
        doc.reference.delete()


def firestore_meals_for_day(user_id: str, day: str) -> list[dict[str, Any]]:
    docs = meals_collection(user_id).where("eaten_date", "==", day).stream()
    meals = [firestore_meal_to_dict(doc) for doc in docs]
    return sorted(meals, key=lambda meal: meal.get("created_at", ""), reverse=True)


def sum_meals(meals: list[dict[str, Any]]) -> dict[str, float]:
    totals = empty_totals()
    for meal in meals:
        for key in totals:
            totals[key] += float(meal.get(key, 0))
    return {
        "calories": round(totals["calories"]),
        "protein": round(totals["protein"], 1),
        "fat": round(totals["fat"], 1),
        "carbs": round(totals["carbs"], 1),
        "fiber": round(totals["fiber"], 1),
        "sugar": round(totals["sugar"], 1),
        "salt": round(totals["salt"], 1),
    }


def firestore_totals_for_day(user_id: str, day: str) -> dict[str, float]:
    return sum_meals(firestore_meals_for_day(user_id, day))


def firestore_meals_between(user_id: str, start_day: str, end_day: str) -> list[dict[str, Any]]:
    docs = (
        meals_collection(user_id)
        .where("eaten_date", ">=", start_day)
        .where("eaten_date", "<=", end_day)
        .stream()
    )
    return [firestore_meal_to_dict(doc) for doc in docs]


def firestore_day_payload(user_id: str, day: str) -> dict[str, Any]:
    firestore_purge_old_logs(user_id)
    review_snap = reviews_collection(user_id).document(day).get()
    review_data = review_snap.to_dict() if review_snap.exists else None
    diary_snap = diaries_collection(user_id).document(day).get()
    diary_data = diary_snap.to_dict() if diary_snap.exists else None
    meals = firestore_meals_for_day(user_id, day)
    totals = sum_meals(meals)
    return {
        "date": day,
        "totals": totals,
        "energy": energy_summary(totals, get_settings(user_id=user_id)),
        "meals": meals,
        "diary": empty_diary()
        if not diary_data
        else {
            "text": diary_data.get("diary", ""),
            "created_at": diary_data.get("created_at", ""),
            "updated_at": diary_data.get("updated_at", ""),
        },
        "review": None
        if not review_data
        else {"text": review_data.get("review", ""), "created_at": review_data.get("created_at", "")},
    }


def firestore_available_days(user_id: str) -> list[dict[str, Any]]:
    firestore_purge_old_logs(user_id)
    docs = meals_collection(user_id).where("eaten_date", ">=", oldest_kept_day()).stream()
    day_map: dict[str, dict[str, Any]] = {}
    for doc in docs:
        meal = firestore_meal_to_dict(doc)
        day = meal["date"]
        item = day_map.setdefault(day, {"date": day, "meal_count": 0, "calories": 0})
        item["meal_count"] += 1
        item["calories"] += meal["calories"]
    today = date.today()
    for offset in range(LOG_RETENTION_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        day_map.setdefault(day, {"date": day, "meal_count": 0, "calories": 0})
    for item in day_map.values():
        item["calories"] = round(float(item["calories"]))
    return [day_map[key] for key in sorted(day_map.keys(), reverse=True)]


def firestore_streak_status(user_id: str) -> dict[str, Any]:
    docs = meals_collection(user_id).where("eaten_date", ">=", oldest_kept_day()).stream()
    recorded = {(doc.to_dict() or {}).get("eaten_date") for doc in docs}
    today = date.today()
    streak = 0
    cursor = today
    while cursor.isoformat() in recorded:
        streak += 1
        cursor -= timedelta(days=1)

    yesterday_recorded = (today - timedelta(days=1)).isoformat() in recorded
    today_recorded = today.isoformat() in recorded
    if streak >= 3:
        message = f"{streak}日連続記録、えらすぎ。今日も続いてる。"
    elif today_recorded:
        message = "今日も撮ってくれてありがとう。小さく続いてます。"
    elif yesterday_recorded:
        message = "昨日は記録できてる。今日は1枚だけ撮れたら十分。"
    else:
        message = "昨日は休憩日。今日からまた軽く再スタート。"
    return {"streak": streak, "today_recorded": today_recorded, "message": message}


def firestore_weekly_summary(user_id: str) -> dict[str, Any]:
    today = date.today()
    start_this_week = today - timedelta(days=today.weekday())
    start_last_week = start_this_week - timedelta(days=7)
    end_last_week = start_this_week - timedelta(days=1)
    meals = firestore_meals_between(user_id, start_last_week.isoformat(), end_last_week.isoformat())
    days_recorded = len({meal["date"] for meal in meals})
    if days_recorded >= 5:
        text = "先週のあなたは、ちゃんと戻ってこられる人でした。"
    elif meals:
        text = "先週のあなたは、完璧じゃなくても記録を残せた人でした。"
    else:
        text = "先週は休憩週。今週は1枚だけで十分です。"
    return {
        "show": today.weekday() == 0,
        "text": text,
        "start": start_last_week.isoformat(),
        "end": end_last_week.isoformat(),
    }


def firestore_current_week_payload(user_id: str, days_count: int = 7) -> dict[str, Any]:
    days_count = max(1, min(31, int(days_count or 7)))
    today = date.today()
    start = today - timedelta(days=days_count - 1)
    previous_start = start - timedelta(days=days_count)
    previous_end = start - timedelta(days=1)
    all_meals = firestore_meals_between(user_id, start.isoformat(), today.isoformat())
    previous_meals = firestore_meals_between(user_id, previous_start.isoformat(), previous_end.isoformat())
    by_day: dict[str, list[dict[str, Any]]] = {}
    for meal in all_meals:
        by_day.setdefault(meal["date"], []).append(meal)
    days = []
    for offset in range(days_count):
        current = start + timedelta(days=offset)
        key = current.isoformat()
        meals = by_day.get(key, [])
        days.append({"date": key, "totals": sum_meals(meals), "meal_count": len(meals)})
    recorded_days = sum(1 for item in days if item["meal_count"] > 0)
    previous_recorded_days = len({meal["date"] for meal in previous_meals})
    if recorded_days >= 5:
        message = "この1週間、かなり戻ってこられてる。続ける力が育ってます。"
    elif recorded_days > 0:
        message = "今週も記録を残せた日がある。それだけで次につながってます。"
    else:
        message = "今週はここからでOK。まず1回だけ記録してみよう。"
    totals = sum_meals(all_meals)
    previous_totals = sum_meals(previous_meals)
    averages = {key: round(value / days_count, 1) for key, value in totals.items()}
    averages["calories"] = round(totals["calories"] / days_count)
    previous_averages = {key: round(value / days_count, 1) for key, value in previous_totals.items()}
    previous_averages["calories"] = round(previous_totals["calories"] / days_count)
    return {
        "start": start.isoformat(),
        "end": today.isoformat(),
        "days_count": days_count,
        "totals": totals,
        "averages": averages,
        "previous_averages": previous_averages,
        "days": days,
        "comparison": average_comparison(recorded_days, previous_recorded_days, days_count),
        "message": message,
    }


def profile_summary(settings: dict[str, str]) -> str:
    return (
        f"年齢: {settings.get('age') or '未設定'}、"
        f"体重: {settings.get('weight') or '未設定'}kg、"
        f"身長: {settings.get('height') or '未設定'}cm、"
        f"性別: {settings.get('sex') or '未設定'}、"
        f"運動: 週{settings.get('exercise_per_week') or '未設定'}回、"
        f"目標体重: {settings.get('target_weight') or '未設定'}kg、"
        f"目標期間: {settings.get('target_weeks') or '未設定'}週間、"
        f"目的: {settings.get('purpose') or '未設定'}"
    )


def review_tone_instruction(tone: str) -> str:
    if tone == "厳しめ":
        return (
            "口調は厳しめ。ただし人格否定や責める言葉は禁止。"
            "改善点をはっきり言い、次の1アクションを具体的に示す。"
        )
    if tone == "普通":
        return (
            "口調は普通。褒めと改善提案を半々にし、落ち着いたコーチのように話す。"
        )
    return (
        "口調は甘目。まず行動をしっかり褒め、改善点はやわらかく言う。"
        "完璧を求めず、続けられることを最優先にする。"
    )


def build_review_text(
    day: str,
    totals: dict[str, float],
    meals: list[dict[str, Any]],
    settings: dict[str, str],
    user_id: str | None = None,
) -> str:
    energy = energy_summary(totals, settings)
    target = energy["target_pfc"]
    streak = streak_status(user_id)
    tone = settings.get("review_tone") or DEFAULT_SETTINGS["review_tone"]
    meal_lines = "\n".join(
        f"- {meal['dish_name']}: {meal['calories']}kcal P{meal['protein']}g F{meal['fat']}g C{meal['carbs']}g 糖質{meal['sugar']}g 食物繊維{meal['fiber']}g 塩分{meal['salt']}g"
        for meal in meals
    ) or "- 記録なし"
    if target["ready"]:
        pfc_gap = (
            f"目標PFC: {target['calories']}kcal / P{target['protein']}g F{target['fat']}g C{target['carbs']}g。"
            f"差分: P{round(target['protein'] - totals['protein'], 1)}g、"
            f"F{round(target['fat'] - totals['fat'], 1)}g、"
            f"C{round(target['carbs'] - totals['carbs'], 1)}g。"
        )
    else:
        pfc_gap = "目標PFCは未設定。設定が足りない場合は、設定を埋めると精度が上がると軽く伝える。"
    return f"""
あなたはライト層向けの食事記録アプリ Eatake のAIレビュー担当です。
ユーザーは継続が苦手でも続けられる体験を求めています。
レビューは日本語で、少し長めに、読みやすい段落で返してください。
デフォルトは甘目で、記録した行動そのものを褒めます。
医学的な断定、過度な危機感、人格否定、強い説教は禁止です。

口調設定: {tone}
口調ルール: {review_tone_instruction(tone)}

日付: {day}
ユーザー設定: {profile_summary(settings)}
連続記録:
- 現在のストリーク: {streak['streak']}日
- 今日記録済み: {streak['today_recorded']}
目標PFC:
- {pfc_gap}
推定消費:
- TDEE: {energy['tdee']}kcal
- 摂取との差分: {energy['balance']}kcal
- 目標に必要な1日赤字: {energy['target_daily_deficit']}kcal
- BMI: {energy['profile']['bmi']} ({energy['profile']['category']})
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

必ず次の順番で書いてください。
次の見出しを必ずこの表記で使ってください。

今日の振り返り
（今日の食事内容やPFCの傾向をやさしく振り返る）

よかった点
（記録したこと自体を褒める）

継続のこと
（連続記録している場合はその継続を褒める。連続でない場合は責めずに再スタートを応援）

次の一手
（次につながる小さな行動を1つだけ提案する）

PFCは、足りない/多いを必要なら具体的なgで触れてください。
ただし甘目では「あと少し足せると良さそう」のようにやわらかく。
返答は280〜420文字くらい。箇条書きではなく、自然な文章で返してください。
"""


def generate_ai_review(
    day: str,
    totals: dict[str, float],
    meals: list[dict[str, Any]],
    settings: dict[str, str],
    user_id: str | None = None,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return template_review(day, totals, settings)

    client = genai.Client(api_key=api_key)
    prompt = build_review_text(day, totals, meals, settings, user_id)
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[prompt],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")

    text = (response.text or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Gemini review response was empty.")
    return text


def save_review(day: str, review: str, user_id: str | None = None) -> dict[str, str]:
    created_at = datetime.now().isoformat(timespec="seconds")
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        reviews_collection(user_id).document(day).set(
            {"eaten_date": day, "created_at": created_at, "review": review}
        )
        return {"text": review, "created_at": created_at}

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


def save_diary(day: str, diary: str, user_id: str | None = None) -> dict[str, str]:
    now = datetime.now().isoformat(timespec="seconds")
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        ref = diaries_collection(user_id).document(day)
        snap = ref.get()
        created_at = (snap.to_dict() or {}).get("created_at", now) if snap.exists else now
        ref.set({"eaten_date": day, "created_at": created_at, "updated_at": now, "diary": diary})
        return {"text": diary, "created_at": created_at, "updated_at": now}

    with get_db() as conn:
        row = conn.execute(
            "SELECT created_at FROM daily_diaries WHERE eaten_date = ?",
            (day,),
        ).fetchone()
        created_at = row["created_at"] if row else now
        conn.execute(
            """
            INSERT INTO daily_diaries (eaten_date, created_at, updated_at, diary)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(eaten_date) DO UPDATE SET
                updated_at = excluded.updated_at,
                diary = excluded.diary
            """,
            (day, created_at, now, diary),
        )
    return {"text": diary, "created_at": created_at, "updated_at": now}


def demo_review(day: str, totals: dict[str, float], settings: dict[str, str]) -> str:
    purpose = settings.get("purpose") or "健康維持"
    if totals["calories"] == 0:
        return f"今日はまだ白紙。ここから1枚撮れたらそれだけで前進です。{purpose}も軽く続けていこう。"
    return (
        f"今日も記録できてるのがまず強い。{purpose}に向けて、撮った分だけ自分を見られてます。次も1枚だけでOK。"
    )


def template_review(day: str, totals: dict[str, float], settings: dict[str, str]) -> str:
    energy = energy_summary(totals, settings)
    target = energy["target_pfc"]
    if totals["calories"] == 0:
        return "今日はまだ白紙。アプリを開けた時点で前進です。まず1枚だけ撮れたら十分。"
    if not target["ready"]:
        return "今日も記録できてるのがまず強い。設定を少し埋めると、PFCの目安も一緒に見られます。"

    gaps = [
        ("タンパク質", round(target["protein"] - totals["protein"], 1), "g"),
        ("脂質", round(target["fat"] - totals["fat"], 1), "g"),
        ("炭水化物", round(target["carbs"] - totals["carbs"], 1), "g"),
    ]
    shortage = [f"{name}あと{amount}{unit}" for name, amount, unit in gaps if amount > 0]
    if shortage:
        pfc_text = "、".join(shortage[:2])
        return f"今日も記録できてえらい。目標PFCまでは{pfc_text}くらい。次の1食で少し足せたら十分です。"
    return "今日のPFCはかなり目標に近いです。ここまで記録できているのが強いので、明日も1枚だけでOK。"


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
  "is_food": true,
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
        "is_food": bool(result.get("is_food", True)),
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
  "is_nutrition_label": true,
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
        "is_nutrition_label": bool(result.get("is_nutrition_label", True)),
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


def normalize_estimate(result: dict[str, Any], fallback_name: str) -> dict[str, Any]:
    carbs = clamp_number(result.get("carbs"), maximum=1000)
    fiber = clamp_number(result.get("fiber"), maximum=300)
    sugar = clamp_number(result.get("sugar"), maximum=1000)
    if sugar == 0 and carbs > 0:
        sugar = max(0, carbs - fiber)
    return {
        "dish_name": str(result.get("dish_name") or fallback_name),
        "calories": round(clamp_number(result.get("calories"))),
        "protein": round(clamp_number(result.get("protein"), maximum=500), 1),
        "fat": round(clamp_number(result.get("fat"), maximum=500), 1),
        "carbs": round(carbs, 1),
        "fiber": round(fiber, 1),
        "sugar": round(sugar, 1),
        "salt": round(clamp_number(result.get("salt"), maximum=100), 1),
        "confidence": round(clamp_number(result.get("confidence"), maximum=1), 2),
        "notes": str(result.get("notes") or ""),
    }


def reject_if_not_food(estimate: dict[str, Any]) -> None:
    dish_name = str(estimate.get("dish_name") or "")
    notes = str(estimate.get("notes") or "")
    looks_empty = estimate.get("calories", 0) <= 0 and estimate.get("protein", 0) <= 0 and estimate.get("carbs", 0) <= 0
    says_no_food = any(word in f"{dish_name} {notes}" for word in ("料理なし", "食事なし", "食品なし", "食べ物なし"))
    if estimate.get("is_food") is False or says_no_food or looks_empty:
        raise HTTPException(status_code=400, detail="食事として識別できませんでした。料理が写っている写真で撮り直してください。")


def reject_if_not_label(estimate: dict[str, Any]) -> None:
    looks_empty = estimate.get("calories", 0) <= 0 and estimate.get("protein", 0) <= 0 and estimate.get("carbs", 0) <= 0
    if estimate.get("is_nutrition_label") is False or looks_empty:
        raise HTTPException(status_code=400, detail="栄養成分表示として読み取れませんでした。表示全体が写るように撮り直してください。")


def estimate_text_meal(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="食べたものを入力してください。")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {
            "dish_name": text[:40],
            "calories": 300,
            "protein": 8,
            "fat": 6,
            "carbs": 50,
            "fiber": 2,
            "sugar": 48,
            "salt": 1.2,
            "confidence": 0.3,
            "notes": "GEMINI_API_KEY 未設定のためデモ値です。",
        }

    prompt = f"""
食事記録アプリの「てきとう記録」です。
ユーザーが写真なしで入力した食事を、1食分としてざっくり栄養推定してください。
完璧さより、記録を続けるための妥当な中央値を返してください。
入力: {text}
JSONだけで返してください。
{{
  "dish_name": "食事名",
  "calories": 0,
  "protein": 0,
  "fat": 0,
  "carbs": 0,
  "fiber": 0,
  "sugar": 0,
  "salt": 0,
  "confidence": 0.0,
  "notes": "ざっくり推定"
}}
"""
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}")
    try:
        result = parse_json_response(response.text or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini response parse error: {exc}")
    return normalize_estimate(result, text[:40])


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": STORAGE_BACKEND}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    user = current_user(request)
    user_id = user["id"] if user and STORAGE_BACKEND == "firestore" else (PROTOTYPE_USER_ID if STORAGE_BACKEND == "firestore" and not AUTH_REQUIRED else None)
    return {
        "auth_required": STORAGE_BACKEND == "firestore" and AUTH_REQUIRED,
        "user": public_user(user),
        "provider": "line",
        "ai_usage": usage_status(user_id),
    }


@app.get("/auth/line/login")
def line_login() -> RedirectResponse:
    if STORAGE_BACKEND != "firestore" or not AUTH_REQUIRED:
        return RedirectResponse("/")

    channel_id = os.getenv("LINE_CHANNEL_ID")
    if not channel_id:
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ID が未設定です。")

    state, nonce = remember_line_state()
    scopes = ["profile", "openid"]
    if LINE_REQUEST_EMAIL:
        scopes.append("email")
    params = {
        "response_type": "code",
        "client_id": channel_id,
        "redirect_uri": f"{APP_BASE_URL}/auth/line/callback",
        "state": state,
        "scope": " ".join(scopes),
        "nonce": nonce,
    }
    return RedirectResponse(f"https://access.line.me/oauth2/v2.1/authorize?{urlencode(params)}")


@app.get("/auth/line/callback")
async def line_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"LINEログインがキャンセルされました: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="LINEログインに必要な情報が不足しています。")
    nonce = consume_line_state(state)

    channel_id = os.getenv("LINE_CHANNEL_ID")
    channel_secret = os.getenv("LINE_CHANNEL_SECRET")
    if not channel_id or not channel_secret:
        raise HTTPException(status_code=500, detail="LINE_CHANNEL_ID / LINE_CHANNEL_SECRET が未設定です。")

    async with httpx.AsyncClient(timeout=15) as client:
        token_res = await client.post(
            "https://api.line.me/oauth2/v2.1/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{APP_BASE_URL}/auth/line/callback",
                "client_id": channel_id,
                "client_secret": channel_secret,
            },
        )
        if token_res.status_code >= 400:
            detail = "LINEトークン取得に失敗しました。Callback URL、Channel ID/Secret、APP_BASE_URLを確認してください。"
            try:
                token_error = token_res.json()
                if token_error.get("error_description"):
                    detail = f"{detail} LINE: {token_error.get('error_description')}"
            except Exception:
                pass
            raise HTTPException(status_code=502, detail=detail)
        token_data = token_res.json()
        id_token = token_data.get("id_token")
        if not id_token:
            raise HTTPException(status_code=502, detail="LINE IDトークンを取得できませんでした。")

        verify_res = await client.post(
            "https://api.line.me/oauth2/v2.1/verify",
            data={"id_token": id_token, "client_id": channel_id, "nonce": nonce},
        )
        if verify_res.status_code >= 400:
            raise HTTPException(status_code=502, detail="LINE IDトークン検証に失敗しました。")
        profile = verify_res.json()

    response = RedirectResponse("/")
    create_line_session(response, profile)
    return response


@app.post("/api/auth/logout")
def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/")
    clear_session(response, request)
    return response


@app.get("/api/today")
def get_today(request: Request) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    payload = day_payload(today_key(), user_id)
    payload["streak"] = streak_status(user_id)
    payload["weekly_summary"] = weekly_summary(user_id)
    payload["ai_usage"] = usage_status(user_id)
    return payload


@app.get("/api/days")
def get_days(request: Request) -> dict[str, Any]:
    return {"days": available_days(user_id=scoped_user_id(request))}


@app.get("/api/week")
def get_week(request: Request, days: int = 7) -> dict[str, Any]:
    return current_week_payload(scoped_user_id(request), days)


@app.get("/api/days/{day}")
def get_day(day: str, request: Request) -> dict[str, Any]:
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日付はYYYY-MM-DDで指定してください。")
    return day_payload(day, scoped_user_id(request))


@app.put("/api/days/{day}/diary")
def update_day_diary(day: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    day = normalize_day(day)
    text = normalize_setting_text(payload.get("text"))[:2000]
    diary = save_diary(day, text, scoped_user_id(request))
    return {"date": day, "diary": diary}


@app.get("/api/settings")
def read_settings(request: Request) -> dict[str, Any]:
    settings = get_settings(user_id=scoped_user_id(request))
    return {"settings": settings, "purposes": PURPOSES, "review_tones": REVIEW_TONES, "profile": profile_metrics(settings)}


@app.put("/api/settings")
def update_settings(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    settings = save_settings(payload, user_id)
    return {"settings": settings, "purposes": PURPOSES, "review_tones": REVIEW_TONES, "profile": profile_metrics(settings)}


@app.post("/api/days/{day}/review")
def create_daily_review(day: str, request: Request) -> dict[str, Any]:
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日付はYYYY-MM-DDで指定してください。")

    user_id = scoped_user_id(request)
    payload = day_payload(day, user_id)
    settings = get_settings(user_id=user_id)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    text = generate_ai_review(day, payload["totals"], payload["meals"], settings, user_id)
    return {"review": save_review(day, text, user_id), "ai_usage": quota}


@app.post("/api/analyze-text")
def analyze_text(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    estimate = estimate_text_meal(str(payload.get("text") or ""))
    result = save_meal_estimate(estimate, user_id, eaten_date=normalize_day(payload.get("date")))
    result["ai_usage"] = quota
    return result


async def read_image(file: UploadFile) -> bytes:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="画像ファイルを送ってください。")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="画像が空です。")
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="画像は8MB以下にしてください。")
    return image_bytes


def make_image_data_url(image_bytes: bytes) -> str:
    try:
        image = Image.open(BytesIO(image_bytes))
        image = image.convert("RGB")
        image.thumbnail((360, 360))
        output = BytesIO()
        image.save(output, format="JPEG", quality=68, optimize=True)
    except Exception:
        return ""
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def normalize_meal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    dish_name = normalize_setting_text(payload.get("dish_name")) or "食事"
    notes = normalize_setting_text(payload.get("notes"))
    carbs = round(clamp_number(payload.get("carbs"), maximum=1000), 1)
    fiber = round(clamp_number(payload.get("fiber"), maximum=300), 1)
    sugar = round(clamp_number(payload.get("sugar"), maximum=1000), 1)
    if sugar == 0 and carbs > 0:
        sugar = max(0, round(carbs - fiber, 1))
    return {
        "dish_name": dish_name[:80],
        "calories": round(clamp_number(payload.get("calories"))),
        "protein": round(clamp_number(payload.get("protein"), maximum=500), 1),
        "fat": round(clamp_number(payload.get("fat"), maximum=500), 1),
        "carbs": carbs,
        "fiber": fiber,
        "sugar": sugar,
        "salt": round(clamp_number(payload.get("salt"), maximum=100), 1),
        "confidence": round(clamp_number(payload.get("confidence"), maximum=1), 2),
        "notes": notes[:240],
    }


def save_meal_estimate(
    estimate: dict[str, Any],
    user_id: str | None = None,
    image_data: str = "",
    eaten_date: str | None = None,
) -> dict[str, Any]:
    created_at = datetime.now().isoformat(timespec="seconds")
    day = normalize_day(eaten_date)

    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        doc_ref = meals_collection(user_id).document()
        doc_ref.set(
            {
                "eaten_date": day,
                "created_at": created_at,
                "dish_name": estimate["dish_name"],
                "calories": estimate["calories"],
                "protein": estimate["protein"],
                "fat": estimate["fat"],
                "carbs": estimate["carbs"],
                "fiber": estimate["fiber"],
                "sugar": estimate["sugar"],
                "salt": estimate["salt"],
                "confidence": estimate["confidence"],
                "image_data": image_data,
                "notes": estimate["notes"],
            }
        )
        meal = firestore_meal_to_dict(doc_ref.get())
        totals = firestore_totals_for_day(user_id, day)
        return {
            "meal": meal,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(user_id=user_id)),
            "days": firestore_available_days(user_id),
        }

    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meals (
                eaten_date, created_at, dish_name, calories, protein, fat,
                carbs, fiber, sugar, salt, confidence, image_data, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                image_data,
                estimate["notes"],
            ),
        )
        row = conn.execute("SELECT * FROM meals WHERE id = ?", (cursor.lastrowid,)).fetchone()
        meal = row_to_meal(row)
        totals = totals_for_day(conn, day)
        payload = {
            "meal": meal,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(conn)),
            "days": available_days(conn),
        }
    return payload


@app.post("/api/analyze")
async def analyze_image(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    user_id = scoped_user_id(request)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    estimate = await estimate_nutrition(file, image_bytes)
    reject_if_not_food(estimate)
    result = save_meal_estimate(estimate, user_id, make_image_data_url(image_bytes))
    result["ai_usage"] = quota
    return result


@app.post("/api/estimate")
async def estimate_image(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    user_id = scoped_user_id(request)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    estimate = await estimate_nutrition(file, image_bytes)
    reject_if_not_food(estimate)
    return {"estimate": estimate, "image_data": make_image_data_url(image_bytes), "ai_usage": quota}


@app.post("/api/analyze-label")
async def analyze_label(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    user_id = scoped_user_id(request)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    estimate = await estimate_nutrition_label(file, image_bytes)
    reject_if_not_label(estimate)
    result = save_meal_estimate(estimate, user_id, make_image_data_url(image_bytes))
    result["ai_usage"] = quota
    return result


@app.post("/api/estimate-label")
async def estimate_label(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    image_bytes = await read_image(file)
    user_id = scoped_user_id(request)
    quota = consume_ai_quota(user_id) if os.getenv("GEMINI_API_KEY") else usage_status(user_id)
    estimate = await estimate_nutrition_label(file, image_bytes)
    reject_if_not_label(estimate)
    return {"estimate": estimate, "image_data": make_image_data_url(image_bytes), "ai_usage": quota}


@app.post("/api/meals")
def create_meal(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    estimate = normalize_meal_payload(payload)
    image_data = str(payload.get("image_data") or "")
    result = save_meal_estimate(estimate, user_id, image_data=image_data, eaten_date=normalize_day(payload.get("date")))
    result["ai_usage"] = usage_status(user_id)
    return result


@app.delete("/api/meals/{meal_id}")
def delete_meal(meal_id: str, request: Request) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        doc_ref = meals_collection(user_id).document(meal_id)
        snap = doc_ref.get()
        if not snap.exists:
            raise HTTPException(status_code=404, detail="記録が見つかりません。")
        day = (snap.to_dict() or {}).get("eaten_date", today_key())
        doc_ref.delete()
        totals = firestore_totals_for_day(user_id, day)
        return {
            "date": day,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(user_id=user_id)),
            "meals": firestore_meals_for_day(user_id, day),
            "days": firestore_available_days(user_id),
        }

    with get_db() as conn:
        row = conn.execute("SELECT eaten_date FROM meals WHERE id = ?", (int(meal_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="記録が見つかりません。")
        day = row["eaten_date"]
        conn.execute("DELETE FROM meals WHERE id = ?", (int(meal_id),))
        totals = totals_for_day(conn, day)
        return {
            "date": day,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(conn)),
            "meals": meals_for_day(conn, day),
            "days": available_days(conn),
        }


@app.put("/api/meals/{meal_id}")
def update_meal(meal_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    update = normalize_meal_payload(payload)
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        doc_ref = meals_collection(user_id).document(meal_id)
        snap = doc_ref.get()
        if not snap.exists:
            raise HTTPException(status_code=404, detail="記録が見つかりません。")
        day = (snap.to_dict() or {}).get("eaten_date", today_key())
        doc_ref.set({**update, "updated_at": now_key()}, merge=True)
        totals = firestore_totals_for_day(user_id, day)
        return {
            "date": day,
            "meal": firestore_meal_to_dict(doc_ref.get()),
            "totals": totals,
            "energy": energy_summary(totals, get_settings(user_id=user_id)),
            "meals": firestore_meals_for_day(user_id, day),
            "days": firestore_available_days(user_id),
        }

    with get_db() as conn:
        row = conn.execute("SELECT eaten_date FROM meals WHERE id = ?", (int(meal_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="記録が見つかりません。")
        day = row["eaten_date"]
        conn.execute(
            """
            UPDATE meals
            SET dish_name = ?, calories = ?, protein = ?, fat = ?, carbs = ?,
                fiber = ?, sugar = ?, salt = ?, notes = ?
            WHERE id = ?
            """,
            (
                update["dish_name"],
                update["calories"],
                update["protein"],
                update["fat"],
                update["carbs"],
                update["fiber"],
                update["sugar"],
                update["salt"],
                update["notes"],
                int(meal_id),
            ),
        )
        totals = totals_for_day(conn, day)
        updated = row_to_meal(conn.execute("SELECT * FROM meals WHERE id = ?", (int(meal_id),)).fetchone())
        return {
            "date": day,
            "meal": updated,
            "totals": totals,
            "energy": energy_summary(totals, get_settings(conn)),
            "meals": meals_for_day(conn, day),
            "days": available_days(conn),
        }


@app.delete("/api/meals")
def delete_all_meals(request: Request) -> dict[str, Any]:
    user_id = scoped_user_id(request)
    if STORAGE_BACKEND == "firestore":
        if not user_id:
            raise HTTPException(status_code=401, detail="LINEログインが必要です。")
        for collection_getter in (meals_collection, reviews_collection, diaries_collection):
            for doc in collection_getter(user_id).stream():
                doc.reference.delete()
        user_doc(user_id).collection("usage").document(today_key()).delete()
        totals = empty_totals()
        return {
            "date": today_key(),
            "totals": totals,
            "energy": energy_summary(totals, get_settings(user_id=user_id)),
            "meals": [],
            "days": [],
            "ai_usage": usage_status(user_id),
        }

    with get_db() as conn:
        conn.execute("DELETE FROM meals")
        conn.execute("DELETE FROM daily_reviews")
        conn.execute("DELETE FROM daily_diaries")
        conn.execute("DELETE FROM ai_usage")
        totals = empty_totals()
        return {
            "date": today_key(),
            "totals": totals,
            "energy": energy_summary(totals, get_settings(conn)),
            "meals": [],
            "days": [],
            "ai_usage": usage_status(),
        }


@app.post("/api/feedback")
def submit_feedback(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    message = normalize_setting_text(payload.get("message"))
    if len(message) < 3:
        raise HTTPException(status_code=400, detail="フィードバックを3文字以上で入力してください。")
    message = message[:1200]
    created_at = now_key()
    user_id = scoped_user_id(request)
    if STORAGE_BACKEND == "firestore":
        fs().collection("feedback").document().set(
            {
                "created_at": created_at,
                "message": message,
                "user_id": user_id or PROTOTYPE_USER_ID,
                "auth_required": AUTH_REQUIRED,
            }
        )
    else:
        with get_db() as conn:
            conn.execute("INSERT INTO feedback (created_at, message) VALUES (?, ?)", (created_at, message))
    return {"status": "ok", "message": "送信しました。ありがとう。"}


@app.get("/api/feedback")
def list_feedback(request: Request) -> dict[str, Any]:
    scoped_user_id(request)
    if STORAGE_BACKEND == "firestore":
        items = []
        for doc in fs().collection("feedback").stream():
            data = doc.to_dict() or {}
            items.append(
                {
                    "id": doc.id,
                    "created_at": data.get("created_at", ""),
                    "message": data.get("message", ""),
                    "user_id": data.get("user_id", ""),
                }
            )
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return {"feedback": items[:50]}

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, message
            FROM feedback
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """
        ).fetchall()
    return {
        "feedback": [
            {"id": row["id"], "created_at": row["created_at"], "message": row["message"], "user_id": PROTOTYPE_USER_ID}
            for row in rows
        ]
    }
