import datetime as dt

from sqlalchemy import JSON, ForeignKey, String, Text, create_engine
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
    created_at: Mapped[dt.datetime] = mapped_column(default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # admin, reviewer, collector, member
    pw_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(default=True)
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


def make_session(url):
    kw = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    engine = create_engine(url, **kw)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
