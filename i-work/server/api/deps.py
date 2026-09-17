from uuid import UUID

from fastapi import Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncSession:
    """Yield 一个 async DB session，供 request handler 使用。"""
    factory = request.app.state.db_session_factory
    async with factory() as db:
        yield db


def get_default_user_id(request: Request) -> UUID:
    """从 app state 获取 default-user UUID。"""
    return request.app.state.default_user_id


def get_engine_manager(request: Request):
    """FastAPI 依赖注入：从 app state 获取 EngineManager。"""
    return request.app.state.engine_manager
