import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Never let tests touch ~/.linkedin-mcp."""
    monkeypatch.setenv("LINKEDIN_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LINKEDIN_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINKEDIN_CLIENT_SECRET", raising=False)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()
