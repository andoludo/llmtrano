"""Download the pinned GGUF models into /models and write /models/MANIFEST.json.

Runs inside the model-fetch container. For each model it reads <PREFIX>_REPO, <PREFIX>_FILE
(a glob) and <PREFIX>_REVISION (a commit sha) from the environment, calls `hf download`, then
checks every file's size and sha256 against the Hub's LFS metadata at that revision.

Idempotent: when the manifest already matches the requested repo, pattern and revision and every
listed file is present with the recorded size, it exits without downloading or hashing.
"""

import fnmatch
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models"))
MANIFEST = MODELS_DIR / "MANIFEST.json"

log = logging.getLogger("model_fetch")


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    pattern: str
    revision: str


def specs_from_env() -> list[ModelSpec]:
    specs = []
    for name, prefix in (("qwen3.5-9b", "MODEL_9B"), ("qwen3.6-35b-a3b", "MODEL_MOE")):
        specs.append(
            ModelSpec(
                name=name,
                repo=os.environ[f"{prefix}_REPO"],
                pattern=os.environ[f"{prefix}_FILE"],
                revision=os.environ[f"{prefix}_REVISION"],
            )
        )
    return specs


def manifest_is_current(specs: list[ModelSpec]) -> bool:
    if not MANIFEST.exists():
        return False
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text())
    for spec in specs:
        entry = manifest.get("models", {}).get(spec.name)
        if entry is None or (entry["repo"], entry["pattern"], entry["revision"]) != (
            spec.repo,
            spec.pattern,
            spec.revision,
        ):
            return False
        for f in entry["files"]:
            path = MODELS_DIR / spec.name / f["file"]
            if not path.exists() or path.stat().st_size != f["size"]:
                return False
    return True


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(spec: ModelSpec, api: HfApi) -> dict[str, Any]:
    info = api.model_info(spec.repo, revision=spec.revision, files_metadata=True)
    expected = {
        s.rfilename: s
        for s in info.siblings or []
        if fnmatch.fnmatch(s.rfilename, spec.pattern) and s.lfs is not None
    }
    if not expected:
        raise SystemExit(f"no file matches {spec.pattern!r} in {spec.repo}@{spec.revision}")

    local_dir = MODELS_DIR / spec.name
    log.info(
        "download model=%s repo=%s revision=%s files=%s",
        spec.name,
        spec.repo,
        spec.revision,
        sorted(expected),
    )
    subprocess.run(
        [
            "hf",
            "download",
            spec.repo,
            "--include",
            spec.pattern,
            "--revision",
            spec.revision,
            "--local-dir",
            str(local_dir),
            "--quiet",
        ],
        check=True,
    )

    files = []
    for rfilename, sibling in sorted(expected.items()):
        assert sibling.lfs is not None
        path = local_dir / rfilename
        size = path.stat().st_size
        log.info("verify model=%s file=%s size=%d", spec.name, rfilename, size)
        sha = sha256_of(path)
        if size != sibling.lfs.size or sha != sibling.lfs.sha256:
            raise SystemExit(f"checksum mismatch for {path}: size={size} sha256={sha}")
        files.append({"file": rfilename, "size": size, "sha256": sha})
    return {"repo": spec.repo, "pattern": spec.pattern, "revision": spec.revision, "files": files}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    specs = specs_from_env()
    if manifest_is_current(specs):
        log.info("manifest current, nothing to do path=%s", MANIFEST)
        return

    api = HfApi()
    manifest = {"models": {spec.name: fetch(spec, api) for spec in specs}}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    log.info("manifest written path=%s", MANIFEST)


if __name__ == "__main__":
    sys.exit(main())
