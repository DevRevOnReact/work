"""Pull-based VPS deployment of the latest main commit after successful GitHub Verify."""
import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

REPOSITORY = "DevRevOnReact/work"
ROOT = Path.home() / "apps/jobpilot"
STATE = ROOT / "deploy-state"
PUBLIC = "https://jobs.blyadota.dev"


def github(path: str) -> dict:
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "JobPilot-CD"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verified_revision() -> str | None:
    sha = github("/commits/main")["sha"]
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise RuntimeError("Invalid GitHub revision")
    runs = github("/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=" + sha)["workflow_runs"]
    if not runs:
        return None
    latest = max(runs, key=lambda run: run["id"])
    if (latest["head_sha"] == sha and latest["head_branch"] == "main"
            and latest["event"] == "push" and latest["status"] == "completed" and latest["conclusion"] == "success"):
        return sha
    return None


def command(args: list[str], *, env: dict | None = None) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True, env=env)
    return result.stdout.strip()


def compose(release: Path, env: dict, *args: str) -> str:
    return command(["docker", "compose", "--project-name", "jobpilot-vps", "--env-file", str(ROOT / ".env.vps"),
                    "-f", str(release / "compose.vps.yaml"), "-f", str(release / "compose.vps-proxy.yaml"), *args], env=env)


def idle() -> bool:
    code = ("from sqlalchemy import select,func; from jobpilot.db import SessionLocal; "
            "from jobpilot.models import Application,ReplyDraft; "
            "db=SessionLocal(); "
            "busy=db.scalar(select(func.count()).select_from(Application).where(Application.state=='APPLYING')) "
            "+ db.scalar(select(func.count()).select_from(ReplyDraft).where(ReplyDraft.status=='working')); "
            "print(busy);db.close()")
    return command(["docker", "exec", "jobpilot-vps-app-1", "python", "-c", code]) == "0"


def stage(sha: str) -> Path:
    release = STATE / "releases" / sha
    if release.exists():
        return release
    request = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + "/tarball/" + sha,
                                     headers={"User-Agent": "JobPilot-CD"})
    with urllib.request.urlopen(request, timeout=90) as response:
        content = response.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise RuntimeError("Source archive exceeds deployment limit")
    pending = STATE / "releases" / (sha + ".pending")
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir(parents=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 100 * 1024 * 1024:
            raise RuntimeError("Expanded source archive exceeds limit")
        archive.extractall(pending, filter="data")
    folders = list(pending.iterdir())
    if len(folders) != 1 or not folders[0].is_dir():
        raise RuntimeError("Unexpected source archive")
    folders[0].rename(release)
    pending.rmdir()
    shutil.copy2(ROOT / ".env.vps", release / ".env.vps")
    (release / ".env.vps").chmod(0o600)
    apk = ROOT / "frontend/public/downloads/jobpilot-assistant.apk"
    target = release / "frontend/public/downloads/jobpilot-assistant.apk"
    target.parent.mkdir(parents=True, exist_ok=True)
    if apk.exists():
        shutil.copy2(apk, target)
    return release


def deploy(sha: str) -> None:
    state_file = STATE / "current.json"
    previous = json.loads(state_file.read_text()) if state_file.exists() else {"release": str(ROOT), "sha": "vps"}
    if previous["sha"] == sha:
        print("Already deployed " + sha)
        return
    release = stage(sha)
    environment = {**os.environ, "JOBPILOT_COMMIT": sha}
    running_image = command(["docker", "inspect", "--format", "{{.Image}}", "jobpilot-vps-app-1"])
    command(["docker", "image", "tag", running_image, "jobpilot-vps-app:rollback"])
    print("Building " + sha, flush=True)
    compose(release, environment, "build", "app")
    if not idle():
        print("Active user work, deferring switch", flush=True)
        return
    try:
        compose(release, environment, "up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout", "150", "app")
        with urllib.request.urlopen(PUBLIC + "/health", timeout=20) as response:
            health = json.load(response)
        if health.get("status") != "ready" or health.get("commit") != sha:
            raise RuntimeError("Public health revision mismatch")
    except Exception:
        override = STATE / "rollback.yaml"
        override.write_text("services:\n  app:\n    image: jobpilot-vps-app:rollback\n")
        old_env = {**os.environ, "JOBPILOT_COMMIT": previous["sha"]}
        command(["docker", "compose", "--project-name", "jobpilot-vps", "--env-file", str(ROOT / ".env.vps"),
                 "-f", str(Path(previous["release"]) / "compose.vps.yaml"),
                 "-f", str(Path(previous["release"]) / "compose.vps-proxy.yaml"),
                 "-f", str(override), "up", "-d", "--no-deps", "--no-build", "--wait", "app"], env=old_env)
        raise
    pending = STATE / "current.pending"
    pending.write_text(json.dumps({"sha": sha, "release": str(release), "deployed_at": time.time()}))
    pending.replace(state_file)
    print("Verified deployment " + sha, flush=True)


def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / "deploy.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Deployment already running")
            return
        sha = verified_revision()
        if sha:
            deploy(sha)
        else:
            print("Waiting for successful Verify on latest main")


if __name__ == "__main__":
    main()
