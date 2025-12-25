from fastapi import FastAPI, Request, Form, Depends, Response, Cookie
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import csv
import aiohttp
from datetime import timedelta
import asyncio
from sqlalchemy import Boolean
from passlib.context import CryptContext
from typing import Optional
from sqlalchemy import ForeignKey

DATABASE_URL = "sqlite:///./cities.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


class City(Base):
    __tablename__ = "cities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    temperature = Column(Float, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

class DefaultCity(Base):
    __tablename__ = "default_cities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True)
    latitude = Column(Float)
    longitude = Column(Float)

Base.metadata.create_all(bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    user_id: Optional[int] = Cookie(default=None),
    db: SessionLocal = Depends(get_db)
):
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


async def fetch_weather(session: aiohttp.ClientSession, latitude: float, longitude: float):
    try:
        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current_weather": "true"
            }
        ) as response:
            if response.status != 200:
                return None

            data = await response.json()
            return data.get("current_weather", {}).get("temperature")
    except Exception:
        return None


@app.get("/")
async def read_root(
    request: Request,
    db: SessionLocal = Depends(get_db),
    user: User = Depends(get_current_user)
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    cities = db.query(City).filter(
        City.user_id == user.id
    ).order_by(
        City.temperature.desc().nullslast()
    ).all()

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "cities": cities, "user": user}
    )


@app.post("/cities/add")
async def add_city(
    name: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    db: SessionLocal = Depends(get_db),
    user: User = Depends(get_current_user)
):
    existing = db.query(City).filter(
        City.name == name,
        City.user_id == user.id
    ).first()

    if existing:
        return RedirectResponse("/", status_code=303)

    city = City(
        name=name,
        latitude=latitude,
        longitude=longitude,
        user_id=user.id
    )
    db.add(city)
    db.commit()

    return RedirectResponse("/", status_code=303)


@app.post("/cities/remove/{city_id}")
async def remove_city(
    city_id: int,
    db: SessionLocal = Depends(get_db),
    user: User = Depends(get_current_user)
):
    city = db.query(City).filter(
        City.id == city_id,
        City.user_id == user.id
    ).first()

    if city:
        db.delete(city)
        db.commit()

    return RedirectResponse("/", status_code=303)


@app.post("/cities/reset")
async def reset_cities(
    db: SessionLocal = Depends(get_db),
    user: User = Depends(get_current_user)
):
    db.query(City).filter(City.user_id == user.id).delete()

    default_cities = db.query(DefaultCity).all()
    for default in default_cities:
        db.add(City(
            name=default.name,
            latitude=default.latitude,
            longitude=default.longitude,
            user_id=user.id
        ))

    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/cities/update")
async def update_weather(
    db: SessionLocal = Depends(get_db),
    user: User = Depends(get_current_user)
):
    cities = db.query(City).filter(City.user_id == user.id).all()
    now = datetime.utcnow()

    async with aiohttp.ClientSession() as session:

        async def update_city(city: City):
            if not city.updated_at or now - city.updated_at > timedelta(minutes=15):
                city.temperature = await fetch_weather(
                    session,
                    city.latitude,
                    city.longitude
                )
                city.updated_at = now

        await asyncio.gather(*(update_city(city) for city in cities))

    db.commit()
    return RedirectResponse("/", status_code=303)


@app.on_event("startup")
async def populate_default_cities():
    db = SessionLocal()
    try:
        if not db.query(DefaultCity).first():
            with open("cities.csv", "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    db.add(DefaultCity(
                        name=row["city"],
                        latitude=float(row["latitude"]),
                        longitude=float(row["longitude"])
                    ))
            db.commit()
    finally:
        db.close()


@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        "register.html",
        {"request": request}
    )


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: SessionLocal = Depends(get_db)
):
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": f"Пользователь с логином '{username}' уже существует",
                "username": username,
                "password": password
            }
        )

    user = User(
        username=username,
        password_hash=hash_password(password)
    )
    db.add(user)
    db.commit()

    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html", {"request": request}
    )


@app.post("/login")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: SessionLocal = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Неверный логин или пароль",
                "username": username,
                "password": password
            }
        )

    if not db.query(City).filter(City.user_id == user.id).first():
        defaults = db.query(DefaultCity).all()
        for default in defaults:
            db.add(City(
                name=default.name,
                latitude=default.latitude,
                longitude=default.longitude,
                user_id=user.id
            ))
        db.commit()

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(key="user_id", value=str(user.id), httponly=True)
    return response


@app.post("/logout")
def logout(response: Response):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("user_id")
    return response
