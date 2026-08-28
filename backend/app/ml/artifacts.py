"""Model artifact persistence (§10.5).

Artifacts are joblib files under a configured directory, named `name/version.joblib`.
Deliberately not S3, not MLflow: §10.5 permits "a versioned S3/local directory + a
models table in Postgres for the MVP", and an experiment tracker for one developer
running scripted jobs is infrastructure to operate rather than leverage.

Every artifact is checksummed. A file replaced underneath its registry row otherwise
serves predictions that the stored metrics never described — and on a shared volume
that is a plausible accident, not a hypothetical one.
"""

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import joblib

from app.core.config import get_settings


def artifact_root() -> Path:
    root = Path(get_settings().model_artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_path(name: str, version: str) -> Path:
    return artifact_root() / name / f"{version}.joblib"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(payload: Any, name: str, version: str) -> tuple[Path, str]:
    """Persist an artifact; returns its path and checksum.

    The payload must carry fitted parameters only — never training rows. Profile
    data is financial PII (§11.2), and an artifact is a file that gets copied
    around far more casually than a database is.
    """
    path = artifact_path(name, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, path, compress=3)
    return path, checksum(path)


def load(path: str | Path, expected_checksum: str | None = None) -> Any:
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(
            f"Model artifact missing at {resolved}. The registry row and the artifact "
            "directory have diverged; re-train or restore the file."
        )

    if expected_checksum:
        actual = checksum(resolved)
        if actual != expected_checksum:
            raise ValueError(
                f"Model artifact at {resolved} does not match its registry checksum. "
                "The file has been modified or replaced; refusing to serve it."
            )

    return joblib.load(resolved)


def git_commit() -> str | None:
    """The current commit, for §10.5's version identity.

    Best-effort: a container built without the .git directory, or a source tarball,
    both legitimately have no commit to report. That is worth a null column, not a
    failed training run.
    """
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None
