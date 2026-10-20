"""What trano is installed: its version, stored with every run and confirmed Brief."""

import importlib.metadata
import json
from functools import cache


@cache
def trano_version() -> str:
    """trano's version, e.g. ``0.56.0``, plus the commit when installed from git, e.g. ``0.52.0+4220d3c``."""
    distribution = importlib.metadata.distribution("trano")
    direct_url = distribution.read_text("direct_url.json")
    commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id", "") if direct_url else ""
    return f"{distribution.version}+{commit[:7]}" if commit else distribution.version
