"""
Disables Langfuse for the entire test session, regardless of what's configured in the real
.env file. Found live 2026-08-31: without this, running the test suite actually sent real traces
to the real Langfuse project under fake case ids (e.g. "PMT-A", the test fixtures' shared dummy
id) -- because pydantic_agents.py's _LANGFUSE_ENABLED check only looks at whether
LANGFUSE_PUBLIC_KEY is set, and the tests never mock that out, so a developer's real, working
Langfuse credentials in .env silently made every test run a real data-pollution event.

This must run BEFORE pydantic_agents.py is ever imported by any test module -- pytest imports
conftest.py first for files in this directory, which is what makes this reliable. Setting the env
var to an empty string (not deleting it) is deliberate: python-dotenv's load_dotenv() defaults to
override=False, so a key already present in os.environ (even as "") is left alone rather than
being reloaded from .env.
"""

import os

os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
