from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


def create_engine(database_url: str):
    """创建 async engine + session factory。"""
    engine = create_async_engine(database_url, pool_size=10, max_overflow=20)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory
