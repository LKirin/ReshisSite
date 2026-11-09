import re
import json
import uuid
import hashlib
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

app = FastAPI(title="Решись Авиа")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"
FORECAST_FILE = DATA_DIR / "my_forecast.csv"
ROUTES_FILE = DATA_DIR / "routes.csv"
FAVICON_ICO_FILE = BASE_DIR / "favicon.ico"
FAVICON_DATA_ICO_FILE = DATA_DIR / "favicon.ico"

ROUTES_URL = (
    "https://gist.githubusercontent.com/Murkat-git/"
    "3ddc868889d3e0d37542d786c8b1aa74/raw/"
    "a934b1220da2b20d7f3eb397a7459fe78ea168ef/routes.csv"
)

CITY_NAMES_RU = {
    "MOW": "Москва",
    "AER": "Сочи",
    "LED": "Санкт-Петербург",
    "KZN": "Казань",
    "SVX": "Екатеринбург",
    "OVB": "Новосибирск",
}

MONTHS_RU_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

MONTH_NAME_TO_NUM = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4,
    "май": 5, "мая": 5, "мае": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10,
    "ноя": 11, "дек": 12,
}

QUANTILES = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]
SESSIONS = {}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def load_users() -> dict:
    if not USERS_FILE.exists():
        default_users = {
            "admin": {
                "username": "admin",
                "password_hash": hash_password("admin123"),
                "avatar_id": 1,
            }
        }
        USERS_FILE.write_text(
            json.dumps(default_users, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return default_users
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        for u in data.values():
            if "avatar_id" not in u:
                u["avatar_id"] = 1
        return data
    except Exception:
        return {}


def save_users(users: dict) -> None:
    USERS_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


FORECAST_DF = None
ROUTES_DF = None


def get_dataframes():
    global FORECAST_DF, ROUTES_DF
    if FORECAST_DF is None:
        if not FORECAST_FILE.exists():
            raise RuntimeError("Положите файл my_forecast.csv в папку data/")
        df = pd.read_csv(FORECAST_FILE)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        FORECAST_DF = df

    if ROUTES_DF is None:
        if not ROUTES_FILE.exists():
            urllib.request.urlretrieve(ROUTES_URL, ROUTES_FILE)
        ROUTES_DF = pd.read_csv(ROUTES_FILE)

    return FORECAST_DF, ROUTES_DF


def format_date_ru(date_str: str, include_year: bool = False) -> str:
    dt = pd.to_datetime(date_str)
    s = f"{dt.day} {MONTHS_RU_GEN[dt.month]}"
    if include_year:
        s += f" {dt.year}"
    return s


def build_date_range_info(dates_list: list) -> dict:
    sorted_dates = sorted(dates_list)
    min_d = sorted_dates[0]
    max_d = sorted_dates[-1]
    years = sorted({int(d.split("-")[0]) for d in sorted_dates})
    single_year = len(years) == 1
    label = f"{format_date_ru(min_d, not single_year)} – {format_date_ru(max_d, not single_year)}"
    preferred = "2026-06-12" if "2026-06-12" in sorted_dates else min_d
    default_input = format_date_ru(preferred, not single_year)
    return {
        "min_date": min_d,
        "max_date": max_d,
        "single_year": single_year,
        "label": label,
        "default_input": default_input,
    }


def resolve_user_date(raw_date: str, available_dates: list) -> str:
    s = raw_date.strip().lower()
    if not s:
        raise ValueError("Введите дату вылета.")

    if s in available_dates:
        return s

    years = sorted({int(d.split("-")[0]) for d in available_dates})
    default_year = years[0] if years else 2026

    m_text = re.match(r"^(\d{1,2})\s+([а-яёa-z]+)(?:\s+(\d{4}))?$", s)
    if m_text:
        day = int(m_text.group(1))
        mon_word = m_text.group(2)
        year = int(m_text.group(3)) if m_text.group(3) else default_year
        month = next((v for k, v in MONTH_NAME_TO_NUM.items() if mon_word.startswith(k)), None)
        if month is not None:
            candidate = f"{year:04d}-{month:02d}-{day:02d}"
            if candidate in available_dates:
                return candidate

    m_num = re.match(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$", s)
    if m_num:
        d1, d2, y_raw = int(m_num.group(1)), int(m_num.group(2)), m_num.group(3)
        year = int(y_raw) if y_raw else default_year
        if year < 100:
            year += 2000
        for cand in [f"{year:04d}-{d2:02d}-{d1:02d}", f"{year:04d}-{d1:02d}-{d2:02d}"]:
            if cand in available_dates:
                return cand

    info = build_date_range_info(available_dates)
    raise ValueError(f"Введите дату из диапазона {info['label']} (например, {info['default_input']}).")


def parse_route_cities(row: pd.Series) -> dict:
    route_code = str(row["route"]).strip()
    parts = route_code.split("-")
    orig_code = parts[0] if len(parts) > 0 else route_code
    dest_code = parts[1] if len(parts) > 1 else ""
    return {
        "route": route_code,
        "origin_code": orig_code,
        "destination_code": dest_code,
        "origin_name": CITY_NAMES_RU.get(orig_code, orig_code),
        "destination_name": CITY_NAMES_RU.get(dest_code, dest_code),
        "base_price": float(row["base_price"]),
        "price_sensitivity": float(row["price_sensitivity"]),
        "cost_per_passenger": float(row.get("cost_per_passenger", 0.0)),
        "price_min": float(row.get("price_min", 1000.0)),
        "price_max": float(row.get("price_max", 50000.0)),
    }


def find_route_and_flight(origin: str, destination: str, resolved_date: str, routes_df: pd.DataFrame, forecast_df: pd.DataFrame):
    orig = origin.strip().upper()
    dest = destination.strip().upper()
    if orig == dest:
        raise HTTPException(status_code=400, detail="Пункт отправления и пункт назначения должны отличаться.")

    direct_code = f"{orig}-{dest}"
    reverse_code = f"{dest}-{orig}"

    route_matches = routes_df[routes_df["route"] == direct_code]
    lookup_code = direct_code

    if route_matches.empty:
        rev_matches = routes_df[routes_df["route"] == reverse_code]
        if not rev_matches.empty:
            route_matches = rev_matches
            lookup_code = reverse_code
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Маршрут между {CITY_NAMES_RU.get(orig, orig)} и {CITY_NAMES_RU.get(dest, dest)} не выполняется (прямые рейсы выполняются через Москву).",
            )

    base_info = parse_route_cities(route_matches.iloc[0])
    route_info = {
        **base_info,
        "route": direct_code,
        "origin_code": orig,
        "destination_code": dest,
        "origin_name": CITY_NAMES_RU.get(orig, orig),
        "destination_name": CITY_NAMES_RU.get(dest, dest),
    }

    flight_matches = forecast_df[
        (forecast_df["route"] == direct_code) & (forecast_df["date"] == resolved_date)
    ]
    if flight_matches.empty and lookup_code != direct_code:
        flight_matches = forecast_df[
            (forecast_df["route"] == lookup_code) & (forecast_df["date"] == resolved_date)
        ]

    if flight_matches.empty:
        raise HTTPException(status_code=404, detail="Рейс на выбранную дату не найден.")

    return route_info, flight_matches.iloc[0]


def require_auth_user(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Требуется авторизация.")
    token = authorization.split(" ", 1)[1]
    username = SESSIONS.get(token)
    users = load_users()
    if not username or username not in users:
        raise HTTPException(status_code=401, detail="Сессия истекла.")
    return {
        "username": username,
        "avatar_id": users[username].get("avatar_id", 1),
    }


class AuthRequest(BaseModel):
    username: str
    password: str
    avatar_id: Optional[int] = 1


class AvatarUpdateRequest(BaseModel):
    avatar_id: int


class ForecastRequest(BaseModel):
    origin: str
    destination: str
    date: str
    price: float


@app.get("/favicon.ico")
def get_favicon():
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    if FAVICON_ICO_FILE.exists():
        return FileResponse(FAVICON_ICO_FILE, media_type="image/x-icon", headers=headers)
    if FAVICON_DATA_ICO_FILE.exists():
        return FileResponse(FAVICON_DATA_ICO_FILE, media_type="image/x-icon", headers=headers)
    return Response(status_code=404)


@app.post("/api/register")
def register_user(req: AuthRequest):
    username = req.username.strip()
    if not username or len(req.password) < 3:
        raise HTTPException(status_code=400, detail="Минимум 3 символа для пароля.")
    users = load_users()
    if username in users:
        raise HTTPException(status_code=400, detail="Логин уже занят.")

    avatar_id = req.avatar_id if req.avatar_id in [1, 2, 3, 4, 5] else 1
    users[username] = {
        "username": username,
        "password_hash": hash_password(req.password),
        "avatar_id": avatar_id,
    }
    save_users(users)

    token = str(uuid.uuid4())
    SESSIONS[token] = username
    return {"token": token, "user": {"username": username, "avatar_id": avatar_id}}


@app.post("/api/login")
def login_user(req: AuthRequest):
    username = req.username.strip()
    users = load_users()
    user = users.get(username)
    if not user or user.get("password_hash") != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")

    token = str(uuid.uuid4())
    SESSIONS[token] = username
    return {
        "token": token,
        "user": {"username": username, "avatar_id": user.get("avatar_id", 1)},
    }


@app.post("/api/logout")
def logout_user(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        SESSIONS.pop(authorization.split(" ", 1)[1], None)
    return {"status": "ok"}


@app.get("/api/me")
def get_current_user(authorization: Optional[str] = Header(default=None)):
    return require_auth_user(authorization)


@app.post("/api/avatar")
def update_avatar(req: AvatarUpdateRequest, authorization: Optional[str] = Header(default=None)):
    user = require_auth_user(authorization)
    if req.avatar_id not in [1, 2, 3, 4, 5]:
        raise HTTPException(status_code=400, detail="Некорректный аватар.")
    users = load_users()
    users[user["username"]]["avatar_id"] = req.avatar_id
    save_users(users)
    return {"username": user["username"], "avatar_id": req.avatar_id}


@app.get("/api/metadata")
def get_metadata():
    forecast_df, routes_df = get_dataframes()
    base_routes = [parse_route_cities(row) for _, row in routes_df.iterrows()]
    dates_list = sorted(forecast_df["date"].unique().tolist())

    all_routes = []
    seen_route_codes = set()
    for r in base_routes:
        if r["route"] not in seen_route_codes:
            seen_route_codes.add(r["route"])
            all_routes.append(r)
        rev_code = f"{r['destination_code']}-{r['origin_code']}"
        if rev_code not in seen_route_codes:
            seen_route_codes.add(rev_code)
            all_routes.append({
                **r,
                "route": rev_code,
                "origin_code": r["destination_code"],
                "destination_code": r["origin_code"],
                "origin_name": r["destination_name"],
                "destination_name": r["origin_name"],
            })

    public_schedule = []
    for r in base_routes:
        r_df = forecast_df[forecast_df["route"] == r["route"]]
        seats_min = int(r_df["seats"].min()) if not r_df.empty else 0
        seats_max = int(r_df["seats"].max()) if not r_df.empty else 0
        public_schedule.append({
            **r,
            "seats_summary": f"{seats_min}" if seats_min == seats_max else f"{seats_min} – {seats_max}",
        })

    cities = []
    seen_cities = set()
    for r in all_routes:
        for code, name in [(r["origin_code"], r["origin_name"]), (r["destination_code"], r["destination_name"])]:
            if code not in seen_cities:
                seen_cities.add(code)
                cities.append({"code": code, "name": name})

    return {
        "routes": all_routes,
        "public_schedule": public_schedule,
        "cities": cities,
        "origins": cities,
        "destinations": cities,
        "date_range": build_date_range_info(dates_list),
    }


@app.post("/api/calculate")
def calculate_forecast(
    req: ForecastRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_auth_user(authorization)
    forecast_df, routes_df = get_dataframes()
    available_dates = sorted(forecast_df["date"].unique().tolist())

    try:
        resolved_date = resolve_user_date(req.date, available_dates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    route_info, flight_row = find_route_and_flight(
        req.origin, req.destination, resolved_date, routes_df, forecast_df
    )

    seats = int(flight_row["seats"])
    base_price = route_info["base_price"]
    sensitivity = route_info["price_sensitivity"]
    cost = route_info["cost_per_passenger"]
    price = round(float(req.price))

    base_demand_05 = float(flight_row["0.5_base_demand"])
    willing_05 = max(0.0, base_demand_05 - sensitivity * (price - base_price))
    willing_int = round(willing_05)
    sold_int = min(seats, max(0, willing_int))
    excess_int = max(0, willing_int - seats)
    free_seats = max(0, seats - sold_int)

    date_info = build_date_range_info(available_dates)
    display_date = format_date_ru(resolved_date, not date_info["single_year"])

    scenarios = []
    for q in QUANTILES:
        q_base = float(flight_row[f"{q}_base_demand"])
        q_willing = max(0.0, q_base - sensitivity * (price - base_price))
        q_willing_round = round(q_willing)
        q_sold_round = min(seats, max(0, q_willing_round))
        q_excess = max(0, q_willing_round - seats)
        q_free = max(0, seats - q_sold_round)
        q_opt_price = float(flight_row[f"{q}_price"])
        q_profit = q_sold_round * (price - cost)

        scenarios.append({
            "quantile": q,
            "base_demand": round(q_base),
            "willing": q_willing_round,
            "sold": q_sold_round,
            "excess": q_excess,
            "free_seats": q_free,
            "capped_by_seats": q_willing_round >= seats,
            "optimal_price": round(q_opt_price),
            "profit_mln": round(q_profit / 1e6, 3),
        })

    return {
        "route": route_info["route"],
        "origin_name": route_info["origin_name"],
        "destination_name": route_info["destination_name"],
        "date": resolved_date,
        "display_date": display_date,
        "price": price,
        "seats": seats,
        "base_price": base_price,
        "price_sensitivity": sensitivity,
        "price_min": route_info["price_min"],
        "price_max": route_info["price_max"],
        "median_base_demand": round(base_demand_05),
        "willing": willing_int,
        "sold": sold_int,
        "excess": excess_int,
        "free_seats": free_seats,
        "load_factor_pct": round((sold_int / seats) * 100, 1) if seats > 0 else 0.0,
        "capped_by_seats": willing_int >= seats,
        "optimal_price_median": round(float(flight_row["0.5_price"])),
        "revenue_mln": round((sold_int * price) / 1e6, 3),
        "profit_mln": round((sold_int * (price - cost)) / 1e6, 3),
        "scenarios": scenarios,
    }


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = BASE_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Файл index.html не найден</h1>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
