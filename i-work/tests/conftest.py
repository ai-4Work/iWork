import pytest
import pytest_asyncio
from server.models.session import Session, ClientTool
from server.storage.memory import InMemorySessionRepo, InMemoryMessageRepo
from server.llm.client import FakeLLMClient
from server.engine.query_loop import EngineManager


@pytest.fixture
def session_repo():
    return InMemorySessionRepo()


@pytest.fixture
def message_repo():
    return InMemoryMessageRepo()


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


@pytest.fixture
def engine_manager(session_repo, message_repo, fake_llm):
    return EngineManager(session_repo, message_repo, fake_llm)


@pytest.fixture
def sample_client_tools():
    return [
        ClientTool(
            name="bash",
            description="Execute a shell command.",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        ),
        ClientTool(
            name="read_file",
            description="Read a file from the workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
    ]


@pytest_asyncio.fixture
async def active_session(session_repo, sample_client_tools):
    s = Session(
        user_id="test-user",
        mode="ask",
        workspace="/tmp/test",
        model="claude-sonnet-4-6",
        client_tools=sample_client_tools,
    )
    await session_repo.create(s)
    return s
