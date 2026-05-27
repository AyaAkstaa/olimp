from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from urllib.parse import quote
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, desc
from sqlalchemy.orm import sessionmaker, Session, declarative_base

SQLALCHEMY_DATABASE_URL = "sqlite:///./olympiad.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

default_games = [
    {
        "slug": "geoguessr",
        "name": "Геогессер",
        "description": "Угадай место на карте по панораме. Докажи, что знаешь этот мир лучше остальных!",
    },
    {
        "slug": "minecraft",
        "name": "Майнкрафт",
        "description": "Соревнования по строительству и выживанию. Твой кубический талант приведет тебя к победе.",
    },
    {
        "slug": "monopoly",
        "name": "Монополия",
        "description": "Экономическая стратегия. Захвати все предприятия и обанкроть своих противников.",
    },
    {
        "slug": "quiz",
        "name": "Квиз",
        "description": "Интеллектуальная битва. Отвечай на вопросы быстрее всех!",
    },
    {
        "slug": "tictactoe",
        "name": "Крестики-нолики",
        "description": "Классика в новом формате. Выстрой свою линию и не дай шанса врагу.",
    },
]

class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True)
    name = Column(String)
    description = Column(String)


class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"))
    player_name = Column(String)
    score = Column(Integer)


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


@app.get("/game/{slug}", response_class=HTMLResponse)
def read_game(request: Request, slug: str, db: Session = Depends(get_db)):
    game = db.query(Game).filter(Game.slug == slug).first()
    if not game:
        return RedirectResponse(url="/")
    results = db.query(Result).filter(Result.game_id == game.id).order_by(desc(Result.score)).all()
    return render_template("game.html", request=request, game=game, results=results)


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    games = db.query(Game).all()
    user = request.query_params.get("user")
    return render_template("admin.html", request=request, games=games, user=user)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render_template("login.html", request=request, message=None)


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username.strip():
        safe_user = quote(username.strip())
        return RedirectResponse(url=f"/admin?user={safe_user}", status_code=303)
    return render_template("login.html", request=request, message="Введите имя участника и пароль")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return PlainTextResponse("", status_code=204)


@app.post("/admin/add_result")
def add_result(
    game_id: int = Form(...),
    player_name: str = Form(...),
    score: int = Form(...),
    db: Session = Depends(get_db),
):
    new_result = Result(game_id=game_id, player_name=player_name, score=score)
    db.add(new_result)
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)
