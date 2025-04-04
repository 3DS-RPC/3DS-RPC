import datetime
from typing import Optional

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, Session
from sqlalchemy.types import DateTime, Integer, TypeDecorator, BigInteger
import sys

from api.networks import NetworkType
from api.private import DB_URL


class Base(DeclarativeBase):
    pass


class Config(Base):
    __tablename__ = "config"

    network: Mapped[NetworkType] = mapped_column("network", Integer(), primary_key=True)
    backend_uptime: Mapped[Optional[datetime.datetime]] = mapped_column("backend_uptime", DateTime())


class NetworkTypeValue(TypeDecorator):
    impl = Integer

    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        if isinstance(value, NetworkType):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return NetworkType(value)
        return value


class Friend(Base):
    __tablename__ = "friends"

    friend_code: Mapped[str] = mapped_column("friend_code", primary_key=True, nullable=False, unique=True)
    network: Mapped[NetworkType] = mapped_column("network", NetworkTypeValue(), index=True)
    online: Mapped[bool]
    title_id: Mapped[str]
    upd_id: Mapped[str]
    last_accessed: Mapped[int] = mapped_column("last_accessed", BigInteger(), nullable=False)
    account_creation: Mapped[int] = mapped_column("account_creation", BigInteger(), nullable=False)
    username: Mapped[Optional[str]]
    message: Mapped[Optional[str]]
    mii: Mapped[Optional[str]]
    joinable: Mapped[Optional[bool]]
    game_description: Mapped[Optional[str]]
    last_online: Mapped[int] = mapped_column("last_online", BigInteger(), nullable=False)
    favorite_game: Mapped[int] = mapped_column("favorite_game", BigInteger(), nullable=False)


class DiscordFriends(Base):
    __tablename__ = "discord_friends"

    id: Mapped[int] = mapped_column(BigInteger(), primary_key=True)
    friend_code: Mapped[str] = mapped_column("friend_code", primary_key=True, nullable=False)
    network: Mapped[NetworkType] = mapped_column("network", NetworkTypeValue())
    active: Mapped[bool]


class Discord(Base):
    __tablename__ = "discord"

    id: Mapped[int] = mapped_column("id", BigInteger(), primary_key=True, nullable=False, unique=True)
    refresh_token: Mapped[str]
    bearer_token: Mapped[str]
    rpc_session_token: Mapped[Optional[str]]
    site_session_token: Mapped[Optional[str]] = mapped_column("site_session_token", unique=True)
    last_accessed: Mapped[int] = mapped_column("last_accessed", BigInteger(), nullable=False)
    generation_date: Mapped[int] = mapped_column("generation_date", BigInteger(), nullable=False)
    show_profile_button: Mapped[bool] = mapped_column("show_profile_button", nullable=False, default=True)
    show_small_image: Mapped[bool] = mapped_column("show_small_image", nullable=False, default=True)


def start_db_time(uptime: Optional[datetime.datetime], network_type: NetworkType):
    """Updates the database to track the starting time for the specific backend."""
    engine = create_engine(get_db_url())
    with Session(engine) as session:
        # TODO: This should be an upsert, not a deletion and insertion.
        session.execute(delete(Config).where(Config.network == network_type))
        new_time = Config(network=network_type, backend_uptime=uptime)
        session.add(new_time)
        session.commit()


def get_db_url() -> str:
    return DB_URL
