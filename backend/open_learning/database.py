from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def session_factory(database_url: str):
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
