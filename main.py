from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, StreamingResponse, JSONResponse
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

# create tables
Base.metadata.create_all(bind=engine)

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
    return render_template("index.html", request=request, games=games, game_assets=GAME_ASSETS)


@app.get("/olympiad", response_class=HTMLResponse)
def read_olympiad(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    return render_template("index.html", request=request, games=games, game_assets=GAME_ASSETS)


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
    stages = [s[0] for s in stages_q if s[0] and s[0].strip().lower() not in ('круговик',)]
    match_titles = []
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
    team_id: int = Form(0),
    participant_id: int = Form(0),
    score: int = Form(0),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
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
def update_score(score_id: int = Form(...), score: int = Form(...), notes: str = Form(""), db: Session = Depends(get_db)):
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
    # debug logging to help verify saved values
    try:
        saved = db.query(RoundRobinMatch).filter(RoundRobinMatch.game_id == game_id, RoundRobinMatch.match_key == match_key).first()
        print(f"[RR_SAVE] game={game_id} key={match_key} team_a={team_a_id} team_b={team_b_id} score_a={score_a_val} score_b={score_b_val} -> saved id={getattr(saved,'id',None)} team_a={getattr(saved,'team_a_id',None)} team_b={getattr(saved,'team_b_id',None)} score_a={getattr(saved,'score_a',None)} score_b={getattr(saved,'score_b',None)}")
    except Exception as e:
        print("[RR_SAVE] logging failed:", e)
    return {"status": "ok"}


@app.get("/admin/download_all")
def download_all(request: Request, db: Session = Depends(get_db)):
    # require admin cookie/token
    cookie_user = request.cookies.get("admin_user")
    cookie_token = request.cookies.get("admin_token")
    if not cookie_user or not cookie_token or not _verify_admin_token(cookie_token, cookie_user):
        return RedirectResponse(url="/login", status_code=303)

    # Create CSV content with all data
    output = io.StringIO()
    
    # Write teams
    output.write("=== КОМАНДЫ ===\n")
    output.write("ID,Название\n")
    teams = db.query(Team).order_by(Team.name).all()
    for team in teams:
        output.write(f"{team.id},{team.name}\n")
    output.write("\n")
    
    # Write participants
    output.write("=== УЧАСТНИКИ ===\n")
    output.write("ID,Имя,Команда\n")
    participants = db.query(Participant).order_by(Participant.name).all()
    for p in participants:
        team_name = p.team.name if p.team else "—"
        output.write(f"{p.id},{p.name},{team_name}\n")
    output.write("\n")
    
    # Write scores
    output.write("=== РЕЗУЛЬТАТЫ ===\n")
    output.write("Игра,Этап,Команда,Участник,Очки,Примечание\n")
    scores = db.query(Score).order_by(Score.game_id, Score.stage, Score.match_title).all()
    for score in scores:
        game_name = score.game.name if score.game else "—"
        stage = score.stage or "Общий"
        team_name = score.team.name if score.team else "—"
        participant_name = score.participant.name if score.participant else "—"
        notes = (score.notes or "").replace("\n", " ")
        # Escape commas and quotes in CSV
        output.write(f'"{game_name}","{stage}","{team_name}","{participant_name}",{score.score},"{notes}"\n')
    output.write("\n")
    
    # Write schedule
    output.write("=== РАСПИСАНИЕ ===\n")
    output.write("Игра,Время,Матч,Локация,Описание\n")
    schedule_items = db.query(ScheduleItem).order_by(ScheduleItem.game_id, ScheduleItem.time).all()
    for item in schedule_items:
        game_name = item.game.name if item.game else "—"
        location = item.location or "—"
        description = (item.description or "").replace("\n", " ")
        output.write(f'"{game_name}","{item.time}","{item.title}","{location}","{description}"\n')
    output.write("\n")
    
    # Write round robin matches
    output.write("=== КРУГОВИК ===\n")
    output.write("Игра,Команда A,Очки A,Команда B,Очки B\n")
    rr_matches = db.query(RoundRobinMatch).order_by(RoundRobinMatch.game_id).all()
    for match in rr_matches:
        game_name = match.game.name if match.game else "—"
        team_a_name = match.team_a.name if match.team_a else "—"
        team_b_name = match.team_b.name if match.team_b else "—"
        score_a = match.score_a if match.score_a is not None else "—"
        score_b = match.score_b if match.score_b is not None else "—"
        output.write(f'"{game_name}","{team_a_name}",{score_a},"{team_b_name}",{score_b}\n')
    
    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"olympiad_export_{timestamp}.csv"
    
    # Return as file download
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
