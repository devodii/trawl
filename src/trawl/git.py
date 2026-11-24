import asyncio
import base64
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

RENAME = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")
RECORD = "\x1e"
FIELD = "\x1f"


class GitSettings(BaseModel):
    """How git is called. Every field can be replaced and extra fields are kept."""

    model_config = ConfigDict(extra="allow")

    remote: str = "https://github.com/{full_name}.git"
    noreply: str = r"^(?:\d+\+)?(?P<login>[^@]+)@users\.noreply\.github\.com$"
    clone_timeout: int = 900
    log_timeout: int = 300
    max_patch_lines: int = 40000
    batch: int = 40
    patch_pathspec: list[str] = Field(default_factory=lambda: ["*.rs"])
    auth_user: str = "x-access-token"


DEFAULT = GitSettings()


class GitFailed(Exception):
    pass


class TreeEntry(BaseModel):
    path: str
    blob_sha: str
    size: int


class FileChange(BaseModel):
    path: str
    status: str = "M"
    additions: int = 0
    deletions: int = 0
    previous_path: str | None = None


class CommitRecord(BaseModel):
    sha: str
    author_login: str | None
    author_name: str
    committed_at: datetime
    message: str
    parents: int
    files: list[FileChange] = Field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)


def environment(token: str | None, settings: GitSettings = DEFAULT) -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if token:
        basic = base64.b64encode(f"{settings.auth_user}:{token}".encode()).decode()
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.extraHeader",
            GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}",
        )
    return env


async def run(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT.log_timeout,
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "core.commitGraph=false",
        "-c",
        "gc.auto=0",
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        raise GitFailed("timed out") from None
    if process.returncode != 0:
        raise GitFailed(err.decode(errors="replace").strip()[-400:])
    return out


async def clone(
    full_name: str,
    destination: Path,
    since: str,
    token: str | None,
    patterns: list[str],
    settings: GitSettings = DEFAULT,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    env = environment(token, settings)
    url = settings.remote.format(full_name=full_name)
    base = [
        "clone",
        "--quiet",
        "--no-tags",
        "--single-branch",
        "--filter=tree:0",
        "--no-checkout",
    ]
    try:
        await run(
            [*base, f"--shallow-since={since}", url, str(destination)],
            env=env,
            timeout=settings.clone_timeout,
        )
        await run(
            ["fetch", "--quiet", "--deepen=1"], destination, env, settings.clone_timeout
        )
    except GitFailed as first:
        if destination.exists():
            shutil.rmtree(destination)
        if "not found" in str(first).lower():
            raise
        await run(
            [*base, "--depth=1", url, str(destination)],
            env=env,
            timeout=settings.clone_timeout,
        )
    await run(
        ["sparse-checkout", "set", "--no-cone", *patterns],
        destination,
        env,
        settings.clone_timeout,
    )
    await run(["checkout", "--quiet", "HEAD"], destination, env, settings.clone_timeout)


async def tracked_files(root: Path) -> list[TreeEntry]:
    out = await run(["ls-files", "-s", "-z"], root)
    entries: list[TreeEntry] = []
    for item in out.decode(errors="replace").split("\0"):
        if not item:
            continue
        meta, _, path = item.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[0] == "160000":
            continue
        try:
            size = (root / path).stat().st_size
        except OSError:
            continue
        entries.append(TreeEntry(path=path, blob_sha=parts[1], size=size))
    return entries


def login_of(email: str, pattern: str = DEFAULT.noreply) -> str | None:
    match = re.match(pattern, email.strip())
    return match["login"] if match else None


def target_path(path: str) -> tuple[str, str | None]:
    if "=>" not in path:
        return path, None
    braces = RENAME.match(path)
    if braces:
        before, old, new, after = braces.groups()
        return (
            f"{before}{new}{after}".replace("//", "/"),
            f"{before}{old}{after}".replace("//", "/"),
        )
    old, _, new = path.partition(" => ")
    return new, old


def parse_log(raw: str, settings: GitSettings = DEFAULT) -> list[CommitRecord]:
    commits: list[CommitRecord] = []
    for record in raw.split(RECORD):
        if not record.strip():
            continue
        header, _, stats = record.partition(f"{FIELD}\n")
        fields = header.split(FIELD)
        if len(fields) < 6:
            continue
        sha, name, email, stamp, parents, message = fields[:6]
        files: list[FileChange] = []
        for line in stats.splitlines():
            cells = line.split("\t")
            if len(cells) < 2 or not cells[0][:1].isalpha():
                continue
            files.append(
                FileChange(
                    path=cells[-1],
                    status=cells[0][:1],
                    previous_path=cells[1] if len(cells) == 3 else None,
                )
            )
        commits.append(
            CommitRecord(
                sha=sha.strip(),
                author_login=login_of(email, settings.noreply),
                author_name=name.replace("\x00", ""),
                committed_at=datetime.fromisoformat(stamp),
                message=message.replace("\x00", "").strip()[:8000],
                parents=len(parents.split()),
                files=files,
            )
        )
    return commits


async def commit_log(
    root: Path, since: str, until: str, settings: GitSettings = DEFAULT
) -> list[CommitRecord]:
    fmt = f"{RECORD}%H{FIELD}%an{FIELD}%ae{FIELD}%aI{FIELD}%P{FIELD}%B"
    out = await run(
        [
            "log",
            f"--since={since}",
            f"--until={until}T23:59:59",
            f"--format={fmt}{FIELD}",
        ],
        root,
        timeout=settings.log_timeout,
    )
    return parse_log(out.decode(errors="replace"), settings)


async def patches(
    root: Path, shas: list[str], settings: GitSettings = DEFAULT
) -> dict[str, list[str]]:
    """The added and removed lines of each commit, limited to `patch_pathspec`."""
    found: dict[str, list[str]] = {}
    for offset in range(0, len(shas), settings.batch):
        chunk = shas[offset : offset + settings.batch]
        out = await run(
            [
                "show",
                "--no-color",
                "-U0",
                "--no-renames",
                f"--format={RECORD}%H",
                *chunk,
                "--",
                *settings.patch_pathspec,
            ],
            root,
            timeout=settings.log_timeout,
        )
        for record in out.decode(errors="replace").split(RECORD):
            sha, _, body = record.partition("\n")
            if sha.strip() not in chunk:
                continue
            lines = [
                line
                for line in body.splitlines()
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            ]
            found[sha.strip()] = lines[: settings.max_patch_lines]
    return found


async def commit_files(
    root: Path, shas: list[str], settings: GitSettings = DEFAULT
) -> dict[str, list[FileChange]]:
    found: dict[str, list[FileChange]] = {}
    for offset in range(0, len(shas), settings.batch):
        out = await run(
            [
                "show",
                "--name-status",
                "--no-renames",
                f"--format={RECORD}%H",
                *shas[offset : offset + settings.batch],
            ],
            root,
            timeout=settings.log_timeout,
        )
        for record in out.decode(errors="replace").split(RECORD):
            sha, _, body = record.partition("\n")
            if not sha.strip():
                continue
            found[sha.strip()] = [
                FileChange(path=cells[-1], status=cells[0][:1])
                for cells in (line.split("\t") for line in body.splitlines())
                if len(cells) >= 2 and cells[0][:1].isalpha()
            ]
    return found
