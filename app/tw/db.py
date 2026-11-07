import datetime as dt

from sqlalchemy import JSON, ForeignKey, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now():
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tin: Mapped[str] = mapped_column(String(20), default="")
    trial_until: Mapped[str] = mapped_column(String(10), default="")
    paid_until: Mapped[str] = mapped_column(String(10), default="")
    interests: Mapped[str] = mapped_column(Text, default="")  # JSON: {"categories": [], "regions": [], "keywords": []}
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin, reviewer, collector, member
    pw_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True)
    session_ver: Mapped[int] = mapped_column(default=0)  # bumped on sign-out: old cookies stop working
    telegram_chat: Mapped[str] = mapped_column(String(20), default="")  # alerts go here when set
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)
    company: Mapped[Company | None] = relationship()


class Tender(Base):
    __tablename__ = "tenders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending, published, rejected
    buyer: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(20), default="other", index=True)
    region: Mapped[str] = mapped_column(String(60), default="")
    place: Mapped[str] = mapped_column(String(120), default="")
    grade: Mapped[str] = mapped_column(String(20), default="")
    closes: Mapped[str] = mapped_column(String(10), default="", index=True)  # ISO date, Gregorian
    closes_raw: Mapped[str] = mapped_column(String(120), default="")
    time: Mapped[str] = mapped_column(String(5), default="")
    type: Mapped[str] = mapped_column(String(10), default="gov")
    bid_security: Mapped[str] = mapped_column(String(200), default="")
    document_fee: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(200), default="")  # newspaper and date, notice board, website
    reference: Mapped[str] = mapped_column(String(120), default="")
    notice: Mapped[str] = mapped_column(Text, default="")
    photo: Mapped[str] = mapped_column(String(80), default="")
    url: Mapped[str] = mapped_column(String(500), default="")  # buyer's own page or official feed, if any
    doubts: Mapped[str] = mapped_column(Text, default="")
    collector_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)
    published_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    extract: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    collector: Mapped[User | None] = relationship(foreign_keys=[collector_id])


class AlertSent(Base):
    __tablename__ = "alerts_sent"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    tender_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(default=now)


class LoginFailure(Base):
    __tablename__ = "login_failures"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), index=True)  # "ip:..." or "phone:..."
    at: Mapped[dt.datetime] = mapped_column(default=now, index=True)


class Payment(Base):
    """One subscription payment: a Chapa checkout (pending until Chapa confirms it) or a payment
    recorded by an admin (Telebirr or bank transfer to Sonador Systems)."""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    tx_ref: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    plan: Mapped[str] = mapped_column(String(10))  # month, year
    amount: Mapped[int] = mapped_column()  # ETB
    days: Mapped[int] = mapped_column()
    method: Mapped[str] = mapped_column(String(12), default="chapa")  # chapa, telebirr, bank
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending, paid, expired
    reference: Mapped[str] = mapped_column(String(120), default="")  # Telebirr or bank transaction id
    paid_until: Mapped[str] = mapped_column(String(10), default="")  # subscription end this payment gave
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now)
    paid_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    checked_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    company: Mapped[Company] = relationship()


# Columns added after the first deploy: (table, column, SQL type and default).
ADDED_COLUMNS = [("users", "session_ver", "INTEGER NOT NULL DEFAULT 0"),
                 ("companies", "interests", "TEXT NOT NULL DEFAULT ''"),
                 ("users", "telegram_chat", "VARCHAR(20) NOT NULL DEFAULT ''")]


def upgrade(engine):
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, col, ddl in ADDED_COLUMNS:
            if col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def make_session(url):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kw)
    if engine.dialect.name == "postgresql":
        # Several workers start at once; let one create tables and add columns at a time.
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(87001)"))
            try:
                Base.metadata.create_all(conn)
                conn.commit()
                upgrade(conn.engine)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(87001)"))
                conn.commit()
    else:
        Base.metadata.create_all(engine)
        upgrade(engine)
    return sessionmaker(engine, expire_on_commit=False)
