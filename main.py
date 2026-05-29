from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from urllib.parse import quote
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, desc, func
from sqlalchemy.orm import sessionmaker, Session, declarative_base, relationship
import os
import hmac
import hashlib
import time
import secrets

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
    location = Column(String, default="")
    description = Column(String, default="")
    game = relationship("Game")


Base.metadata.create_all(bind=engine)

app = FastAPI()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)


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
    return render_template("index.html", request=request, games=games)


@app.get("/olympiad", response_class=HTMLResponse)
def read_olympiad(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    return render_template("index.html", request=request, games=games)


@app.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
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
    return render_template(
        "schedule.html",
        request=request,
        games=games,
        schedule_items=schedule_items,
        team_totals=team_totals,
        participant_totals=participant_totals,
    )


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render_template("register.html", request=request, message=None)


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
    schedule_items = db.query(ScheduleItem).filter(ScheduleItem.game_id == game.id).order_by(ScheduleItem.time, ScheduleItem.id).all()
    return render_template(
        "game.html",
        request=request,
        game=game,
        scores=scores,
        schedule_items=schedule_items,
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
    schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time, ScheduleItem.id).all()
    # collect distinct stages and match titles to suggest in admin form
    stages_q = db.query(Score.stage).filter(Score.stage != None, Score.stage != "").distinct().all()
    match_titles_q = db.query(Score.match_title).filter(Score.match_title != None, Score.match_title != "").distinct().all()
    stages = [s[0] for s in stages_q]
    match_titles = [m[0] for m in match_titles_q]
    user = cookie_user
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
    )


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
def add_team(team_name: str = Form(...), db: Session = Depends(get_db)):
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
def add_participant(team_id: int = Form(...), participant_name: str = Form(...), db: Session = Depends(get_db)):
    if participant_name.strip():
        participant = Participant(name=participant_name.strip(), team_id=team_id)
        db.add(participant)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/add_score")
def add_score(
    game_id: int = Form(...),
    stage: str = Form(""),
    match_title: str = Form(""),
    team_id: int = Form(0),
    participant_id: int = Form(0),
    score: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    new_score = Score(
        game_id=game_id,
        stage=stage.strip(),
        match_title=match_title.strip(),
        team_id=team_id if team_id else None,
        participant_id=participant_id if participant_id else None,
        score=score,
        notes=notes.strip(),
    )
    db.add(new_score)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_score")
def update_score(score_id: int = Form(...), score: int = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
    record = db.query(Score).filter(Score.id == score_id).first()
    if record:
        record.score = score
        record.notes = notes.strip()
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_score")
def delete_score(score_id: int = Form(...), db: Session = Depends(get_db)):
    record = db.query(Score).filter(Score.id == score_id).first()
    if record:
        db.delete(record)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/add_schedule")
def add_schedule(
    game_id: int = Form(...),
    title: str = Form(...),
    time: str = Form(...),
    location: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if title.strip() and time.strip():
        schedule_item = ScheduleItem(
            game_id=game_id,
            title=title.strip(),
            time=time.strip(),
            location=location.strip(),
            description=description.strip(),
        )
        db.add(schedule_item)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_schedule")
def delete_schedule(schedule_id: int = Form(...), db: Session = Depends(get_db)):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == schedule_id).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_schedule")
def update_schedule(
    schedule_id: int = Form(...),
    game_id: int = Form(...),
    title: str = Form(...),
    time: str = Form(...),
    location: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    item = db.query(ScheduleItem).filter(ScheduleItem.id == schedule_id).first()
    if item:
        item.game_id = game_id
        item.title = title.strip()
        item.time = time.strip()
        item.location = location.strip()
        item.description = description.strip()
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_team")
def delete_team(team_id: int = Form(...), db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if team:
        db.delete(team)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_team")
def update_team(team_id: int = Form(...), name: str = Form(...), db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == team_id).first()
    if team and name.strip():
        team.name = name.strip()
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/delete_participant")
def delete_participant(participant_id: int = Form(...), db: Session = Depends(get_db)):
    p = db.query(Participant).filter(Participant.id == participant_id).first()
    if p:
        db.delete(p)
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/update_participant")
def update_participant(participant_id: int = Form(...), name: str = Form(...), team_id: int = Form(0), db: Session = Depends(get_db)):
    p = db.query(Participant).filter(Participant.id == participant_id).first()
    if p and name.strip():
        p.name = name.strip()
        p.team_id = team_id if team_id else None
        db.commit()
    return RedirectResponse(url="/admin", status_code=303)
