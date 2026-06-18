from urllib import request

from fastapi import FastAPI, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from urllib.parse import quote
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, desc, func, or_
from sqlalchemy.orm import sessionmaker, Session, declarative_base, relationship
from typing import Optional
import os
import hmac
import hashlib
import time
import secrets
import csv
import io
from datetime import datetime
import traceback
from sqlalchemy.exc import IntegrityError, OperationalError

SQLALCHEMY_DATABASE_URL = "sqlite:///./olympiad.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

default_games = [
    {
        "slug": "geoguessr",
        "name": "еогессер",
        "description": "гадай место на карте по панораме. окажи, что знаешь этот мир лучше остальных!",
    },
    {
        "slug": "minecraft",
        "name": "айнкрафт",
        "description": "Соревнования по строительству и выживанию. Твой кубический талант приведет тебя к победе.",
    },
    {
        "slug": "monopoly",
        "name": "онополия",
        "description": "кономическая стратегия. ахвати все предприятия и обанкроть своих противников.",
    },
    {
        "slug": "quiz",
        "name": "виз",
        "description": "нтеллектуальная битва. твечай на вопросы быстрее всех!",
    },
    {
        "slug": "tictactoe",
        "name": "рестики-нолики",
        "description": "лассика в новом формате. ыстрой свою линию и не дай шанса врагу.",
    },
]

GAME_ASSETS = {
    "geoguessr": "Карточки_0000_Геогессер.png",
    "minecraft": "Карточки_0001_Майнкрафт.png",
    "monopoly": "Карточки_0002_Монополия.png",
    "quiz": "Карточки_0003_Квиз.png",
    "tictactoe": "Карточки_0004_Крестики.png",
}

# alternate ("cross") variants with 'Крест' prefix in filename when available
GAME_ASSETS_CROSS = {
    "geoguessr": "Карточки_0000_Крест_Геогессер.png",
    "minecraft": "Карточки_0001_Крест_Майнкрафт.png",
    "monopoly": "Карточки_0002_Крест_Моноплия.png",
    "quiz": "Карточки_0003_Крест_Квиз.png",
    "tictactoe": "Карточки_0004_Крест_Крестики.png",
}


class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True)
    name = Column(String)
    description = Column(String)


class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    participants = relationship("Participant", back_populates="team", cascade="all, delete-orphan")
    scores = relationship("Score", back_populates="team", cascade="all, delete-orphan")


class Participant(Base):
    __tablename__ = "participants"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team = relationship("Team", back_populates="participants")
    scores = relationship("Score", back_populates="participant", cascade="all, delete-orphan")


class Score(Base):
    __tablename__ = "scores"
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"))
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    participant_id = Column(Integer, ForeignKey("participants.id"), nullable=True)
    stage = Column(String, default="")
    match_title = Column(String, default="")
    score = Column(Integer, default=0)
    notes = Column(String, default="")
    game = relationship("Game")
    team = relationship("Team", back_populates="scores")
    participant = relationship("Participant", back_populates="scores")


class ScheduleItem(Base):
    __tablename__ = "schedule_items"
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"))
    title = Column(String)
    time = Column(String)
    date = Column(String, default="")
    # legacy `location` removed in favor of `date`
    description = Column(String, default="")
    game = relationship("Game")


class RoundRobinMatch(Base):
    __tablename__ = "round_robin_matches"
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"))
    team_a_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    team_b_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    score_a = Column(Integer, default=0)
    score_b = Column(Integer, default=0)
    match_key = Column(String, default="")
    game = relationship("Game")
    team_a = relationship("Team", foreign_keys=[team_a_id])
    team_b = relationship("Team", foreign_keys=[team_b_id])

class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(String, default="")
    
class GameImage(Base):
    __tablename__ = "game_images"
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), unique=True)
    image_path = Column(String, default="")
    game = relationship("Game")

# create tables
Base.metadata.create_all(bind=engine)

# Ensure new `date` column exists in SQLite table (migration helper)
try:
    with engine.connect() as conn:
        res = conn.execute("PRAGMA table_info('schedule_items')")
        cols = [r[1] for r in res.fetchall()]
        if 'date' not in cols:
            try:
                conn.execute("ALTER TABLE schedule_items ADD COLUMN date TEXT DEFAULT ''")
            except Exception:
                pass
except Exception:
    pass


def ensure_schedule_date_column():
    try:
        with engine.connect() as conn:
            res = conn.execute("PRAGMA table_info('schedule_items')")
            cols = [r[1] for r in res.fetchall()]
            if 'date' in cols:
                return
            # Try simple ALTER TABLE first
            try:
                conn.execute("ALTER TABLE schedule_items ADD COLUMN date TEXT DEFAULT ''")
                return
            except Exception:
                # fallback: rebuild the table with the new schema and copy data
                try:
                    with conn.begin():
                        conn.execute("CREATE TABLE IF NOT EXISTS schedule_items_new (id INTEGER PRIMARY KEY, game_id INTEGER, title TEXT, time TEXT, date TEXT DEFAULT '', description TEXT)")
                        conn.execute("INSERT INTO schedule_items_new (id, game_id, title, time, date, description) SELECT id, game_id, title, time, '', description FROM schedule_items")
                        conn.execute("DROP TABLE schedule_items")
                        conn.execute("ALTER TABLE schedule_items_new RENAME TO schedule_items")
                    return
                except Exception:
                    return
    except Exception:
        return

app = FastAPI()

# serve local image assets from the картинка folder
static_dir = Path(__file__).resolve().parent / "картинки"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)

BASE_DIR = Path(__file__).resolve().parent


def render_template(name: str, **context):
    template = env.get_template(name)
    return HTMLResponse(template.render(**context))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Simple admin auth helpers (cookie + HMAC token) ---
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    # try to persist a generated secret for stability
    sk_path = Path(__file__).resolve().parent / "secret.key"
    try:
        if sk_path.exists():
            SECRET_KEY = sk_path.read_bytes()
        else:
            key = secrets.token_bytes(32)
            sk_path.write_bytes(key)
            SECRET_KEY = key
    except Exception:
        # fallback to an in-memory key (not persistent)
        SECRET_KEY = secrets.token_bytes(32)
elif isinstance(SECRET_KEY, str):
    SECRET_KEY = SECRET_KEY.encode()

# Admin password (change via env var ADMIN_PASSWORD in production)
ADMIN_PASSWORD = "admin1243"  # default password, override with env var


def _make_admin_token(username: str) -> str:
    ts = str(int(time.time()))
    msg = f"{username}|{ts}".encode()
    sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()
    return f"{sig}:{ts}"


def _verify_admin_token(token: str, username: str, max_age: int = 86400) -> bool:
    try:
        sig, ts = token.split(":", 1)
        msg = f"{username}|{ts}".encode()
        expected = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        now = int(time.time())
        if now - int(ts) > max_age:
            return False
        return True
    except Exception:
        return False


@app.on_event("startup")
def startup_event():
    # ensure DB schema migrations run before handling requests
    ensure_schedule_date_column()

    db = SessionLocal()
    try:
        if not db.query(Game).first():
            for game_data in default_games:
                game = Game(**game_data)
                db.add(game)
            db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    # build game_assets mapping using admin flags (card_cross_<slug>)
    game_assets = {}
    for g in games:
        key = f"card_cross_{g.slug}"
        setting = db.query(Settings).filter(Settings.key == key).first()
        use_cross = setting.value == "1" if setting else False
        game_assets[g.slug] = GAME_ASSETS_CROSS.get(g.slug) if use_cross else GAME_ASSETS.get(g.slug)
    return render_template("index.html", request=request, games=games, game_assets=game_assets)


@app.get("/olympiad", response_class=HTMLResponse)
def read_olympiad(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    game_assets = {}
    for g in games:
        key = f"card_cross_{g.slug}"
        setting = db.query(Settings).filter(Settings.key == key).first()
        use_cross = setting.value == "1" if setting else False
        game_assets[g.slug] = GAME_ASSETS_CROSS.get(g.slug) if use_cross else GAME_ASSETS.get(g.slug)
    return render_template("index.html", request=request, games=games, game_assets=game_assets)


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    try:
        schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
    except OperationalError:
        # likely missing `date` column; try to add it and retry
        try:
            ensure_schedule_date_column()
            schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
        except OperationalError:
            # final fallback: raw SQL selecting known columns (omit `date`) and build dicts
            try:
                with engine.connect() as conn:
                    res = conn.execute("SELECT id, game_id, title, time, description FROM schedule_items ORDER BY game_id, time, id")
                    rows = res.fetchall()
                    schedule_items = [
                        {"id": r[0], "game_id": r[1], "title": r[2], "time": r[3], "date": "", "description": r[4]}
                        for r in rows
                    ]
            except Exception:
                schedule_items = []
    team_totals = (
        db.query(
            Team.name.label("team_name"),
            func.coalesce(func.sum(Score.score), 0).label("total"),
        )
        .outerjoin(Score)
        .group_by(Team.id, Team.name)
        .order_by(desc("total"))
        .all()
    )
    participant_totals = (
        db.query(
            Participant.name.label("participant_name"),
            Team.name.label("team_name"),
            func.coalesce(func.sum(Score.score), 0).label("total"),
        )
        .select_from(Participant)
        .join(Team, Participant.team_id == Team.id)
        .outerjoin(Score, Score.participant_id == Participant.id)
        .group_by(Participant.id, Participant.name, Team.name)
        .order_by(desc("total"))
        .all()
    )
    # build mapping for tooltips
    teams_all = db.query(Team).all()
    team_participants = {t.name: ', '.join([p.name for p in t.participants]) for t in teams_all}
    return render_template(
        "schedule.html",
        request=request,
        games=games,
        schedule_items=schedule_items,
        team_totals=team_totals,
        participant_totals=participant_totals,
        team_participants=team_participants,
    )


@app.get("/results", response_class=HTMLResponse)
def results_page(request: Request, db: Session = Depends(get_db)):
    team_totals = (
        db.query(
            Team.name.label("team_name"),
            func.coalesce(func.sum(Score.score), 0).label("total"),
        )
        .outerjoin(Score)
        .group_by(Team.id, Team.name)
        .order_by(desc("total"))
        .all()
    )
    participant_totals = (
        db.query(
            Participant.name.label("participant_name"),
            Team.name.label("team_name"),
            func.coalesce(func.sum(Score.score), 0).label("total"),
        )
        .select_from(Participant)
        .join(Team, Participant.team_id == Team.id)
        .outerjoin(Score, Score.participant_id == Participant.id)
        .group_by(Participant.id, Participant.name, Team.name)
        .order_by(desc("total"))
        .all()
    )
    # Get show_podium setting
    show_podium_setting = db.query(Settings).filter(Settings.key == "show_podium").first()
    show_podium = show_podium_setting.value == "1" if show_podium_setting else True
    # build mapping of team name -> comma-separated participants for tooltips
    teams = db.query(Team).all()
    team_participants = {t.name: ', '.join([p.name for p in t.participants]) for t in teams}
    return render_template(
        "results.html",
        request=request,
        team_totals=team_totals,
        participant_totals=participant_totals,
        show_podium=show_podium,
        team_participants=team_participants,
    )


@app.post("/admin/toggle_podium")
def toggle_podium(request: Request, db: Session = Depends(get_db)):
    # Verify admin
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    setting = db.query(Settings).filter(Settings.key == "show_podium").first()
    if not setting:
        setting = Settings(key="show_podium", value="1")
        db.add(setting)
    else:
        setting.value = "0" if setting.value == "1" else "1"
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/toggle_card")
def toggle_card(request: Request, game_slug: str = Form(...), db: Session = Depends(get_db)):
    # Verify admin
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)

    # toggle setting key 'card_cross_<slug>'
    key = f"card_cross_{game_slug}"
    setting = db.query(Settings).filter(Settings.key == key).first()
    if not setting:
        setting = Settings(key=key, value="1")
        db.add(setting)
    else:
        setting.value = "0" if setting.value == "1" else "1"
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return RedirectResponse(url="https://forms.yandex.ru/u/6a0cbcd884227c3869ab7608", status_code=303)


@app.post("/register")
def register_submit(
    request: Request,
    team_name: str = Form(...),
    participant_name: str = Form(...),
    db: Session = Depends(get_db),
):
    team_name = team_name.strip()
    participant_name = participant_name.strip()
    if not team_name or not participant_name:
        return render_template("register.html", request=request, message="аполните имя команды и участника.")

    team = db.query(Team).filter(Team.name == team_name).first()
    if not team:
        team = Team(name=team_name)
        db.add(team)
        db.commit()
        db.refresh(team)

    participant = Participant(name=participant_name, team_id=team.id)
    db.add(participant)
    db.commit()
    return render_template(
        "register.html",
        request=request,
        message=f"оманда '{team_name}' и участник '{participant_name}' добавлены. Теперь администратор может занести вам результаты.",
    )


@app.get("/game/{slug}", response_class=HTMLResponse)
def read_game(request: Request, slug: str, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.slug == slug).first()
    if not game:
        return RedirectResponse(url="/")
    scores = db.query(Score).filter(Score.game_id == game.id).order_by(Score.stage, Score.match_title, desc(Score.score)).all()
    try:
        schedule_items = db.query(ScheduleItem).filter(ScheduleItem.game_id == game.id).order_by(ScheduleItem.time, ScheduleItem.id).all()
    except OperationalError:
        try:
            ensure_schedule_date_column()
            schedule_items = db.query(ScheduleItem).filter(ScheduleItem.game_id == game.id).order_by(ScheduleItem.time, ScheduleItem.id).all()
        except OperationalError:
            try:
                with engine.connect() as conn:
                    res = conn.execute(f"SELECT id, game_id, title, time, description FROM schedule_items WHERE game_id = {int(game.id)} ORDER BY time, id")
                    rows = res.fetchall()
                    schedule_items = [
                        {"id": r[0], "game_id": r[1], "title": r[2], "time": r[3], "date": "", "description": r[4]}
                        for r in rows
                    ]
            except Exception:
                schedule_items = []
    rr_matches = db.query(RoundRobinMatch).filter(RoundRobinMatch.game_id == game.id).all()
    rr_teams = {}
    for match in rr_matches:
        if match.team_a and match.team_a.id not in rr_teams:
            rr_teams[match.team_a.id] = match.team_a
        if match.team_b and match.team_b.id not in rr_teams:
            rr_teams[match.team_b.id] = match.team_b
    rr_teams = sorted(rr_teams.values(), key=lambda team: team.name)
    rr_matrix = {f"{m.team_a_id}_{m.team_b_id}": m for m in rr_matches}
    # pass teams/participants for in-page editor and mark admin status
    teams = db.query(Team).order_by(Team.name).all()
    participants = db.query(Participant).order_by(Participant.name).all()
    # build mapping for tooltips
    team_participants = {t.name: ', '.join([p.name for p in t.participants]) for t in teams}
    tictactoe_image = db.query(GameImage).filter(GameImage.game_id == game.id).first()
    tictactoe_image_url = f"/static/{tictactoe_image.image_path}" if tictactoe_image and tictactoe_image.image_path else None
    tictactoe_placements = {}
    if game.slug == 'tictactoe':
        placements = db.query(Score).filter(Score.game_id == game.id, Score.stage.in_(['1 место', '2 место', '3 место'])).all()
        tictactoe_placements = {placement.stage: placement for placement in placements}
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    is_admin = bool(cookie_user and cookie_token and _verify_admin_token(cookie_token, cookie_user))
    return render_template(
        "game.html",
        request=request,
        game=game,
        scores=scores,
        schedule_items=schedule_items,
        rr_teams=rr_teams,
        rr_matrix=rr_matrix,
        teams=teams,
        participants=participants,
        is_admin=is_admin,
        tictactoe_image_url=tictactoe_image_url,
        tictactoe_placements=tictactoe_placements,
        team_participants=team_participants,
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    # require admin cookie/token
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)

    games = db.query(Game).all()
    teams = db.query(Team).order_by(Team.name).all()
    participants = db.query(Participant).order_by(Participant.name).all()
    scores = db.query(Score).order_by(Score.game_id, Score.stage, Score.match_title).all()
    try:
        schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
    except OperationalError:
        try:
            ensure_schedule_date_column()
            schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
        except OperationalError:
            try:
                with engine.connect() as conn:
                    res = conn.execute("SELECT id, game_id, title, time, description FROM schedule_items ORDER BY game_id, time, id")
                    rows = res.fetchall()
                    schedule_items = [
                        {"id": r[0], "game_id": r[1], "title": r[2], "time": r[3], "date": "", "description": r[4]}
                        for r in rows
                    ]
            except Exception:
                schedule_items = []
    # collect distinct stages and match titles to suggest in admin form
    stages_q = db.query(Score.stage).filter(Score.stage != None, Score.stage != "").distinct().all()
    stages = [s[0] for s in stages_q if s[0] and s[0].strip().lower() not in ('круговик',)]
    match_titles = []
    user = cookie_user
    # Get show_podium setting
    show_podium_setting = db.query(Settings).filter(Settings.key == "show_podium").first()
    show_podium = show_podium_setting.value == "1" if show_podium_setting else True
    # load card_cross flags for each game
    card_flags = {}
    for g in games:
        key = f"card_cross_{g.slug}"
        s = db.query(Settings).filter(Settings.key == key).first()
        card_flags[g.slug] = s.value == "1" if s else False
    return render_template(
        "admin.html",
        request=request,
        games=games,
        teams=teams,
        participants=participants,
        scores=scores,
        schedule_items=schedule_items,
        stages=stages,
        match_titles=match_titles,
        user=user,
        show_podium=show_podium,
        card_flags=card_flags,
    )


@app.get("/admin/migrate_schedule")
def migrate_schedule(request: Request, db: Session = Depends(get_db)):
    """Admin-only route to perform one-time DB migration: add `date` column, copy location->date, drop location"""
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    result = {"status": "starting", "steps": []}
    try:
        with engine.connect() as conn:
            # Check current schema
            res = conn.execute("PRAGMA table_info('schedule_items')")
            cols = {r[1]: r[2] for r in res.fetchall()}
            result["steps"].append(f"Current columns: {list(cols.keys())}")
            
            # Step 1: Add `date` column if missing
            if 'date' not in cols:
                try:
                    with conn.begin():
                        conn.execute("ALTER TABLE schedule_items ADD COLUMN date TEXT DEFAULT ''")
                    result["steps"].append("✓ Added `date` column")
                except Exception as e:
                    result["steps"].append(f"ℹ `date` column add failed (may already exist): {e}")
            else:
                result["steps"].append("✓ `date` column already exists")
            
            # Step 2: Copy location -> date
            if 'location' in cols:
                with conn.begin():
                    cursor = conn.execute("UPDATE schedule_items SET date = location WHERE date = '' AND location IS NOT NULL AND location != ''")
                    affected = cursor.rowcount
                    result["steps"].append(f"✓ Copied {affected} rows from `location` to `date`")
                    
                    # Step 3: Recreate table without location
                    result["steps"].append("Recreating table without `location`...")
                    conn.execute("""
                        CREATE TABLE schedule_items_new (
                            id INTEGER PRIMARY KEY,
                            game_id INTEGER,
                            title TEXT,
                            time TEXT,
                            date TEXT DEFAULT '',
                            description TEXT
                        )
                    """)
                    conn.execute("""
                        INSERT INTO schedule_items_new (id, game_id, title, time, date, description)
                        SELECT id, game_id, title, time, date, description FROM schedule_items
                    """)
                    conn.execute("DROP TABLE schedule_items")
                    conn.execute("ALTER TABLE schedule_items_new RENAME TO schedule_items")
                    result["steps"].append("✓ Removed `location` column and recreated table")
            else:
                result["steps"].append("ℹ `location` column not found (already removed)")
            
            # Verify final schema
            res = conn.execute("PRAGMA table_info('schedule_items')")
            final_cols = [r[1] for r in res.fetchall()]
            res = conn.execute("SELECT COUNT(*) FROM schedule_items")
            count = res.fetchone()[0]
            
            result["steps"].append(f"✓ Final schema: {final_cols}")
            result["steps"].append(f"✓ Total schedule items: {count}")
            result["status"] = "success"
    except Exception as e:
        result["status"] = "error"
        result["steps"].append(f"✗ Migration failed: {str(e)}")
        import traceback
        result["traceback"] = traceback.format_exc()
    
    return JSONResponse(result)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render_template("login.html", request=request, message=None)


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    print(f"Login attempt: username='{username}', password='{password}'")
    uname = username.strip()
    pwd = password or ""
    if not uname:
        return render_template("login.html", request=request, message="введите имя пользователя")

    # check password
    print(ADMIN_PASSWORD)
    if pwd.strip() != ADMIN_PASSWORD:
        return render_template("login.html", request=request, message="Неверные учетные данные")

    safe_user = quote(uname)
    token = _make_admin_token(safe_user)
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.set_cookie("admin_user", safe_user, httponly=True, samesite="lax")
    resp.set_cookie("admin_token", token, httponly=True, samesite="lax")
    return resp


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return PlainTextResponse("", status_code=204)


@app.post("/admin/add_team")
def add_team(request: Request, team_name: str = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    if team_name.strip():
        team = Team(name=team_name.strip())
        db.add(team)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("admin_user")
    resp.delete_cookie("admin_token")
    return resp


@app.post("/admin/add_participant")
def add_participant(request: Request, team_id: int = Form(...), participant_name: str = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    if participant_name.strip():
        participant = Participant(name=participant_name.strip(), team_id=team_id)
        db.add(participant)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/add_score")
def add_score(
    request: Request,
    game_id: int = Form(...),
    stage: str = Form(""),
    team_id: int = Form(0),
    participant_id: int = Form(0),
    score: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    new_score = Score(
        game_id=game_id,
        stage=stage.strip(),
        match_title="",
        team_id=team_id if team_id else None,
        participant_id=participant_id if participant_id else None,
        score=score,
        notes=notes.strip(),
    )
    db.add(new_score)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_score")
def update_score(request: Request, score_id: int = Form(...), score: int = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    record = db.query(Score).filter(Score.id == score_id).first()
    if record:
        record.score = score
        record.notes = notes.strip()
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_scores_bulk")
async def update_scores_bulk(request: Request, db: Session = Depends(get_db)):
    # verify admin
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    items = payload.get("scores", [])
    updated = 0
    for it in items:
        try:
            sid = int(it.get("id"))
        except Exception:
            continue
        rec = db.query(Score).filter(Score.id == sid).first()
        if rec:
            if "score" in it and it.get("score") is not None and it.get("score") != "":
                try:
                    rec.score = int(it.get("score"))
                except Exception:
                    pass
            if "notes" in it:
                rec.notes = str(it.get("notes") or "").strip()
            updated += 1
    if updated:
        db.commit()
    return JSONResponse({"status": "ok", "updated": updated})


@app.post("/admin/add_score_inline")
async def add_score_inline(request: Request, db: Session = Depends(get_db)):
    # admin-only
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    game_id = payload.get('game_id')
    if not game_id:
        return JSONResponse({"error": "missing_game_id"}, status_code=400)

    # determine or create team
    team_id = payload.get('team_id')
    team_name = payload.get('team_name')
    if team_id:
        try:
            team_id = int(team_id)
        except Exception:
            team_id = None

    if not team_id and team_name:
        team_name = team_name.strip()
        if team_name:
            team = db.query(Team).filter(func.lower(Team.name) == team_name.lower()).first()
            if not team:
                team = Team(name=team_name)
                db.add(team)
                db.commit()
                db.refresh(team)
            team_id = team.id

    # determine or create participant
    participant_id = payload.get('participant_id')
    participant_name = payload.get('participant_name')
    if participant_id:
        try:
            participant_id = int(participant_id)
        except Exception:
            participant_id = None

    if not participant_id and participant_name:
        participant_name = participant_name.strip()
        if participant_name:
            participant = db.query(Participant).filter(Participant.name == participant_name).first()
            if not participant:
                participant = Participant(name=participant_name, team_id=team_id if team_id else None)
                db.add(participant)
                db.commit()
                db.refresh(participant)
            participant_id = participant.id

    # score
    try:
        score_val = int(payload.get('score', 0))
    except Exception:
        score_val = 0

    notes = payload.get('notes') or ''

    rec = Score(game_id=game_id, stage='', match_title='', team_id=team_id if team_id else None, participant_id=participant_id if participant_id else None, score=score_val, notes=notes.strip())
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return JSONResponse({"status": "ok", "score_id": rec.id})


@app.post("/admin/upload_game_image")
async def upload_game_image(request: Request, game_slug: str = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({"status": "error", "error": "Unauthorized"}, status_code=401)

    game = db.query(Game).filter(Game.slug == game_slug).first()
    if not game:
        return JSONResponse({"status": "error", "error": "Game not found"}, status_code=404)

    contents = await file.read()
    if not contents:
        return JSONResponse({"status": "error", "error": "Empty file"}, status_code=400)

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        ext = '.png'

    upload_dir = BASE_DIR / 'картинки' / 'uploads'
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{game.slug}_{int(time.time())}{ext}"
    target_path = upload_dir / filename
    target_path.write_bytes(contents)

    game_image = db.query(GameImage).filter(GameImage.game_id == game.id).first()
    if game_image:
        old_path = BASE_DIR / 'картинки' / game_image.image_path
        try:
            if old_path.exists():
                old_path.unlink()
        except Exception:
            pass
        game_image.image_path = f"uploads/{filename}"
    else:
        game_image = GameImage(game_id=game.id, image_path=f"uploads/{filename}")
        db.add(game_image)

    db.commit()
    return JSONResponse({"status": "ok", "image_url": f"/static/{game_image.image_path}"})


@app.post('/admin/save_tictactoe_placements')
async def save_tictactoe_placements(request: Request, db: Session = Depends(get_db)):
    cookie_user = request.cookies.get('admin_user')
    cookie_token = request.cookies.get('admin_token')
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({'status': 'error', 'error': 'Unauthorized'}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({'status': 'error', 'error': 'invalid_json'}, status_code=400)

    game_slug = payload.get('game_slug')
    if not game_slug:
        return JSONResponse({'status': 'error', 'error': 'missing_game_slug'}, status_code=400)

    game = db.query(Game).filter(Game.slug == game_slug).first()
    if not game:
        return JSONResponse({'status': 'error', 'error': 'Game not found'}, status_code=404)

    placements = payload.get('placements') or []
    if not isinstance(placements, list):
        return JSONResponse({'status': 'error', 'error': 'invalid_placements'}, status_code=400)

    existing = {
        score.stage: score
        for score in db.query(Score).filter(Score.game_id == game.id, Score.stage.in_(['1 место', '2 место', '3 место'])).all()
    }

    for item in placements:
        rank = item.get('rank')
        if rank not in [1, 2, 3]:
            continue
        stage = f'{rank} место'
        try:
            points = int(item.get('points', 0))
        except Exception:
            points = 0
        team_id = item.get('team_id')
        participant_id = item.get('participant_id')
        try:
            team_id = int(team_id) if team_id not in (None, '', 0, '0') else None
        except Exception:
            team_id = None
        try:
            participant_id = int(participant_id) if participant_id not in (None, '', 0, '0') else None
        except Exception:
            participant_id = None

        if not team_id and not participant_id and points == 0:
            if stage in existing:
                db.delete(existing[stage])
            continue

        if stage in existing:
            record = existing[stage]
            record.score = points
            record.team_id = team_id
            record.participant_id = participant_id
            record.match_title = ''
            record.notes = ''
        else:
            record = Score(
                game_id=game.id,
                stage=stage,
                match_title='',
                team_id=team_id if team_id else None,
                participant_id=participant_id if participant_id else None,
                score=points,
                notes='',
            )
            db.add(record)
            existing[stage] = record

    db.commit()
    return JSONResponse({'status': 'ok'})


@app.post("/admin/scores_batch_minecraft")
async def scores_batch_minecraft(request: Request, db: Session = Depends(get_db)):

    # admin-only
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse({"error": "invalid_json", "detail": str(e)}, status_code=400)

    game_id = payload.get('game_id')
    if not game_id:
        return JSONResponse({"error": "missing_game_id"}, status_code=400)

    ops = payload.get('ops', [])
    if not isinstance(ops, list):
        return JSONResponse({"error": "invalid_ops"}, status_code=400)

    added = 0
    updated = 0
    deleted = 0

    try:
        op_results = []
        for idx, it in enumerate(ops):
            typ = (it.get('op') or '').strip().lower()
            stage = (it.get('stage') or '').strip()
            score_val = it.get('score')
            try:
                score_val = int(score_val)
            except Exception:
                score_val = 0

            team_id = it.get('team_id')
            team_name = (it.get('team_name') or '').strip()

            # resolve or create team if necessary
            resolved_team_id = None
            if team_id:
                try:
                    resolved_team_id = int(team_id)
                    t = db.query(Team).filter(Team.id == resolved_team_id).first()
                    if not t:
                        resolved_team_id = None
                except Exception:
                    resolved_team_id = None

            if not resolved_team_id and team_name:
                all_teams = db.query(Team).all()
                t = db.query(Team).filter(Team.name == team_name).first()
                if not t:
                    print(f"    Team not found, creating...")
                    try:
                        t = Team(name=team_name)
                        db.add(t)
                        db.commit()
                        db.refresh(t)
                        print(f"    Created team: id={t.id}, name={t.name}")
                    except IntegrityError:
                        db.rollback()
                        t = db.query(Team).filter(Team.name == team_name).first()
                        print(f"    IntegrityError caught, re-queried: {t}")
                resolved_team_id = t.id if t else None
            

            if typ == 'add':
                rec = Score(game_id=game_id, stage=stage, match_title='', team_id=resolved_team_id if resolved_team_id else None, participant_id=None, score=score_val, notes='')
                db.add(rec)
                added += 1
                op_results.append({'op': 'add', 'team_id': resolved_team_id, 'team_name': team_name, 'score': score_val})

            elif typ == 'update':
                # normalize: update/create a single team-scoped score for this stage
                if not resolved_team_id and team_name:
                    # create team if missing
                    t = db.query(Team).filter(Team.name == team_name).first()
                    if not t:
                        try:
                            t = Team(name=team_name)
                            db.add(t)
                            db.commit()
                            db.refresh(t)
                        except IntegrityError:
                            db.rollback()
                            t = db.query(Team).filter(Team.name == team_name).first()
                    resolved_team_id = t.id if t else None

                if resolved_team_id:
                    rec = db.query(Score).filter(Score.game_id == game_id, Score.team_id == resolved_team_id, Score.stage == stage, Score.participant_id == None).first()
                    if rec:
                        rec.score = score_val
                        updated += 1
                        op_results.append({'op': 'update', 'team_id': resolved_team_id, 'team_name': team_name, 'score': score_val})
                    else:
                        rec = Score(game_id=game_id, stage=stage, match_title='', team_id=resolved_team_id, participant_id=None, score=score_val, notes='')
                        db.add(rec)
                        added += 1
                        op_results.append({'op': 'add_from_update', 'team_id': resolved_team_id, 'team_name': team_name, 'score': score_val})

            elif typ == 'delete':
                # delete scores for this team/stage — include team-scoped and participant-scoped scores
                target_team_id = resolved_team_id
                if not target_team_id and team_name:
                    t = db.query(Team).filter(Team.name == team_name).first()
                    if t:
                        target_team_id = t.id

                if target_team_id:
                    print(f"    Searching Scores for delete: game_id={game_id}, target_team_id={target_team_id}, stage={stage}")
                    # delete Score rows where Score.team_id == target_team_id OR Score.participant_id refers to a participant with that team_id
                    rows = (
                        db.query(Score)
                        .outerjoin(Participant, Score.participant_id == Participant.id)
                        .filter(
                            Score.game_id == game_id,
                            Score.stage == stage,
                            or_(Score.team_id == target_team_id, Participant.team_id == target_team_id),
                        )
                        .all()
                    )
                    print(f"    Found {len(rows)} rows to delete")
                else:
                    print(f"    No target_team_id found for delete")
                    rows = []

                for r in rows:
                    db.delete(r)
                    deleted += 1
                    op_results.append({'op': 'delete', 'score_id': r.id, 'team_id': r.team_id, 'team_name': (r.team.name if r.team else None), 'participant_id': r.participant_id})

        if added or updated or deleted:
            db.commit()

        return JSONResponse({"status": "ok", "added": added, "updated": updated, "deleted": deleted, "op_results": op_results})
    except Exception:
        tb = traceback.format_exc()
        print("scores_batch_minecraft error:\n", tb)
        return JSONResponse({"error": "server_exception", "detail": tb}, status_code=500)


@app.post("/admin/scores_batch_minecraft")
async def scores_batch_minecraft(request: Request, db: Session = Depends(get_db)):
    # admin-only
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    game_id = payload.get('game_id')
    if not game_id:
        return JSONResponse({"error": "missing_game_id"}, status_code=400)

    ops = payload.get('ops', [])
    added = 0
    updated = 0
    deleted = 0

    for it in ops:
        typ = (it.get('op') or '').strip().lower()
        stage = (it.get('stage') or '').strip()
        score_val = it.get('score')
        try:
            score_val = int(score_val)
        except Exception:
            score_val = 0

        team_id = it.get('team_id')
        team_name = (it.get('team_name') or '').strip()

        # resolve or create team if necessary
        resolved_team_id = None
        if team_id:
            try:
                resolved_team_id = int(team_id)
                t = db.query(Team).filter(Team.id == resolved_team_id).first()
                if not t:
                    resolved_team_id = None
            except Exception:
                resolved_team_id = None

        if not resolved_team_id and team_name:
            t = db.query(Team).filter(func.lower(Team.name) == team_name.lower()).first()
            if not t:
                t = Team(name=team_name)
                db.add(t)
                db.commit()
                db.refresh(t)
            resolved_team_id = t.id

        if typ == 'add':
            rec = Score(game_id=game_id, stage=stage, match_title='', team_id=resolved_team_id if resolved_team_id else None, participant_id=None, score=score_val, notes='')
            db.add(rec)
            added += 1

        elif typ == 'update':
            # normalize: update/create a single team-scoped score for this stage
            if not resolved_team_id and team_name:
                # create team if missing
                t = db.query(Team).filter(func.lower(Team.name) == team_name.lower()).first()
                if not t:
                    t = Team(name=team_name)
                    db.add(t)
                    db.commit()
                    db.refresh(t)
                resolved_team_id = t.id

            if resolved_team_id:
                rec = db.query(Score).filter(Score.game_id == game_id, Score.team_id == resolved_team_id, Score.stage == stage, Score.participant_id == None).first()
                if rec:
                    rec.score = score_val
                    updated += 1
                else:
                    rec = Score(game_id=game_id, stage=stage, match_title='', team_id=resolved_team_id, participant_id=None, score=score_val, notes='')
                    db.add(rec)
                    added += 1

        elif typ == 'delete':
            # delete team-scoped scores for this stage
            q = db.query(Score).filter(Score.game_id == game_id, Score.stage == stage, Score.participant_id == None)
            if resolved_team_id:
                q = q.filter(Score.team_id == resolved_team_id)
            elif team_name:
                # find team ids matching name
                t = db.query(Team).filter(func.lower(Team.name) == team_name.lower()).first()
                if t:
                    q = q.filter(Score.team_id == t.id)
                else:
                    q = q.filter(Score.id == None)  # no-op
            rows = q.all()
            for r in rows:
                db.delete(r)
                deleted += 1

    if added or updated or deleted:
        db.commit()

    return JSONResponse({"status": "ok", "added": added, "updated": updated, "deleted": deleted})


@app.post("/admin/delete_score")
def delete_score(request: Request, score_id: int = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    record = db.query(Score).filter(Score.id == score_id).first()
    if record:
        db.delete(record)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/add_schedule")
def add_schedule(
    request: Request,
    game_id: int = Form(...),
    title: str = Form(...),
    date: str = Form("") ,
    time: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    if title.strip() and time.strip():
        # ensure schema has `date` column before inserting
        ensure_schedule_date_column()
        schedule_item = ScheduleItem(
            game_id=game_id,
            title=title.strip(),
            date=date.strip(),
            time=time.strip(),
            description=description.strip(),
        )
        try:
            db.add(schedule_item)
            db.commit()
        except OperationalError:
            # try to ensure column and retry once
            ensure_schedule_date_column()
            try:
                db.add(schedule_item)
                db.commit()
            except Exception:
                db.rollback()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_schedule")
def delete_schedule(request: Request, schedule_id: int = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    item = db.query(ScheduleItem).filter(ScheduleItem.id == schedule_id).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_schedule")
def update_schedule(
    request: Request,
    schedule_id: int = Form(...),
    game_id: int = Form(...),
    title: str = Form(...),
    date: str = Form(""),
    time: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    item = db.query(ScheduleItem).filter(ScheduleItem.id == schedule_id).first()
    if item:
        # ensure schema has `date` column before updating
        ensure_schedule_date_column()
        item.game_id = game_id
        item.title = title.strip()
        item.date = date.strip()
        item.time = time.strip()
        item.description = description.strip()
        try:
            db.commit()
        except OperationalError:
            ensure_schedule_date_column()
            try:
                db.commit()
            except Exception:
                db.rollback()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_team")
def delete_team(request: Request, team_id: int = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        db.delete(team)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_team")
def update_team(request: Request, team_id: int = Form(...), name: str = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    team = db.query(Team).filter(Team.id == team_id).first()
    if team and name.strip():
        team.name = name.strip()
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_participant")
def delete_participant(request: Request, participant_id: int = Form(...), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    p = db.query(Participant).filter(Participant.id == participant_id).first()
    if p:
        db.delete(p)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_participant")
def update_participant(request: Request, participant_id: int = Form(...), name: str = Form(...), team_id: int = Form(0), db: Session = Depends(get_db)):
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)
    
    p = db.query(Participant).filter(Participant.id == participant_id).first()
    if p and name.strip():
        p.name = name.strip()
        p.team_id = team_id if team_id else None
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/round_robin", response_class=HTMLResponse)
def admin_round_robin(request: Request, game_id: int = None, n_teams: int = 4, db: Session = Depends(get_db)):
    # require admin
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)

    games = db.query(Game).filter(Game.slug == 'geoguessr').all()
    teams = db.query(Team).order_by(Team.name).all()
    # default to geoguessr only
    if not games:
        games = db.query(Game).all()
    if game_id is None and games:
        game_id = games[0].id
    matches = db.query(RoundRobinMatch).filter(RoundRobinMatch.game_id == game_id).all() if game_id else []
    matrix = {m.match_key: m for m in matches}
    round_robin_team_ids = []
    if matches:
        team_map = {team.id: team for team in teams}
        for m in matches:
            if m.team_a_id and m.team_a_id not in round_robin_team_ids:
                round_robin_team_ids.append(m.team_a_id)
            if m.team_b_id and m.team_b_id not in round_robin_team_ids:
                round_robin_team_ids.append(m.team_b_id)
        for t in teams:
            if len(round_robin_team_ids) >= n_teams:
                break
            if t.id not in round_robin_team_ids:
                round_robin_team_ids.append(t.id)
    else:
        round_robin_team_ids = [t.id for t in teams[:n_teams]]
    round_robin_teams = [team for team in teams if team.id in round_robin_team_ids]
    if len(round_robin_teams) < n_teams:
        for t in teams:
            if t.id not in round_robin_team_ids:
                round_robin_teams.append(t)
                if len(round_robin_teams) >= n_teams:
                    break
    return render_template(
        "round_robin_admin.html",
        request=request,
        games=games,
        teams=teams,
        game_id=game_id,
        n_teams=n_teams,
        matrix=matrix,
        round_robin_teams=round_robin_teams,
    )


@app.post("/admin/round_robin_save")
def admin_round_robin_save(game_id: int = Form(...), match_key: str = Form(...), team_a_id: int = Form(0), team_b_id: int = Form(0), score_a: Optional[str] = Form(None), score_b: Optional[str] = Form(None), db: Session = Depends(get_db)):
    # score_a/score_b may be sent as strings 'null' or empty; normalize to Optional[int]
    def parse_score(s: Optional[str]) -> Optional[int]:
        if s is None:
            return None
        s2 = str(s).strip()
        if s2 == '' or s2.lower() == 'null':
            return None
        try:
            return int(s2)
        except Exception:
            return None

    score_a_val = parse_score(score_a)
    score_b_val = parse_score(score_b)

    # normalize team order so match_key is always min_max
    if team_a_id and team_b_id and team_a_id > team_b_id:
        team_a_id, team_b_id = team_b_id, team_a_id
        score_a_val, score_b_val = score_b_val, score_a_val
        match_key = f"{team_a_id}_{team_b_id}"
    alt_key = None
    if team_a_id and team_b_id:
        alt_key = f"{team_b_id}_{team_a_id}"
    query = db.query(RoundRobinMatch).filter(RoundRobinMatch.game_id == game_id)
    if alt_key:
        query = query.filter(or_(RoundRobinMatch.match_key == match_key, RoundRobinMatch.match_key == alt_key))
    else:
        query = query.filter(RoundRobinMatch.match_key == match_key)
    rec = query.first()
    if rec:
        rec.match_key = match_key
        rec.team_a_id = team_a_id if team_a_id else None
        rec.team_b_id = team_b_id if team_b_id else None
        rec.score_a = score_a_val
        rec.score_b = score_b_val
    else:
        rec = RoundRobinMatch(game_id=game_id, match_key=match_key, team_a_id=team_a_id if team_a_id else None, team_b_id=team_b_id if team_b_id else None, score_a=score_a_val, score_b=score_b_val)
        db.add(rec)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/add_round_match")
def add_round_match(round_id: int = Form(...), player_a_id: int = Form(...), player_b_id: int = Form(...), score_a: int = Form(0), score_b: int = Form(0), notes: str = Form(""), db: Session = Depends(get_db)):
    # create match record
    rm = RoundMatch(round_id=round_id, player_a_id=player_a_id, player_b_id=player_b_id, score_a=score_a, score_b=score_b, notes=notes.strip())
    db.add(rm)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/round/{round_id}", response_class=HTMLResponse)
def view_round(request: Request, round_id: int, db: Session = Depends(get_db)):
    r = db.query(Round).filter(Round.id == round_id).first()
    if not r:
        return RedirectResponse(url="/admin", status_code=303)
    # get players
    players = db.query(RoundPlayer).filter(RoundPlayer.round_id == round_id).all()
    matches = db.query(RoundMatch).filter(RoundMatch.round_id == round_id).all()

    # build matrix dictionary for quick lookup
    matrix = {}
    for m in matches:
        a = m.player_a_id
        b = m.player_b_id
        matrix_key = f"{min(a,b)}_{max(a,b)}"
        matrix[matrix_key] = m

    return render_template("round.html", request=request, round=r, players=players, matrix=matrix)

@app.get("/watch")
async def watch_video_page(request: Request):
    return render_template("video.html", request=request)

# 2. Эндпоинт, который стримит сам видеофайл
@app.get("/api/video")
async def get_video_stream():
    # Укажи правильный путь к своему видеофайлу в папке static
    video_path = BASE_DIR / "картинки" / "main" / "mult.mp4"
    
    # Небольшая проверка для подстраховки (выведет точный путь в консоль, если файла нет)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Видео не найдено по пути: {video_path}")
    
    # FileResponse в FastAPI автоматически поддерживает Range-запросы для перемотки
    return FileResponse(str(video_path), media_type="video/mp4")
