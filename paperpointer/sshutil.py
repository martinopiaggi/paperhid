from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

import paramiko

DEFAULT_HOST = "10.11.99.1"
DEFAULT_USER = "root"
REMOTE_HOME = "/home/root/.paperpointer"
UNIT_NAME = "paperpointer.service"
UNIT_USR = f"/usr/lib/systemd/system/{UNIT_NAME}"
UNIT_ETC = f"/etc/systemd/system/{UNIT_NAME}"
ENABLE_LINK = f"/usr/lib/systemd/system/multi-user.target.wants/{UNIT_NAME}"
ENABLE_LINK_ETC = f"/etc/systemd/system/multi-user.target.wants/{UNIT_NAME}"


def password_from_env(cli_password: str | None = None) -> str:
    """Resolve SSH password (CLI first; see core.credentials)."""
    try:
        from core.credentials import require_password

        return require_password(cli_password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    except ImportError:
        if cli_password:
            return cli_password
        for key in (
            "PAPERHID_PASSWORD",
            "PAPERWRITER_PASSWORD",
            "PAPERPOINTER_PASSWORD",
            "MOVEWRITER_PASSWORD",
        ):
            v = os.environ.get(key)
            if v:
                return v
        raise SystemExit(
            "SSH password required. Pass --password or set PAPERHID_PASSWORD."
        )


def connect(host: str, password: str, user: str = DEFAULT_USER) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        username=user,
        password=password,
        timeout=12,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[str, str, int]:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    return out, err, code


def put_tree(
    c: paramiko.SSHClient,
    local_dir: Path,
    remote_dir: str,
    preserve_existing: set[str] | None = None,
) -> None:
    """Upload a tree, optionally retaining named files already on the target."""
    text_suffixes = {".py", ".sh", ".service", ".conf", ".md", ".txt", ".qmd"}
    preserved = preserve_existing or set()
    sftp = c.open_sftp()
    try:
        run(c, f"mkdir -p {remote_dir}")
        for path in local_dir.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(local_dir).as_posix()
            rpath = f"{remote_dir}/{rel}"
            if rel in preserved:
                try:
                    sftp.stat(rpath)
                    continue
                except OSError:
                    pass
            rparent = rpath.rsplit("/", 1)[0]
            run(c, f"mkdir -p {rparent}")
            data = path.read_bytes()
            if path.suffix.lower() in text_suffixes or path.name in {"run.sh"}:
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                with sftp.file(rpath, "wb") as rf:
                    rf.write(data)
            else:
                sftp.put(str(path), rpath)
            mode = path.stat().st_mode
            if mode & stat.S_IXUSR or path.suffix == ".sh" or path.name.endswith(".py"):
                sftp.chmod(rpath, 0o755)
            else:
                sftp.chmod(rpath, 0o644)
    finally:
        sftp.close()


def put_file_atomic(
    c: paramiko.SSHClient,
    local_path: Path,
    remote_path: str,
    mode: int = 0o644,
) -> None:
    """Upload one file and atomically replace its same-filesystem destination."""
    data = local_path.read_bytes()
    if local_path.suffix.lower() in {".py", ".sh", ".service", ".conf", ".txt"}:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    temporary = f"{remote_path}.tmp-{os.getpid()}"
    sftp = c.open_sftp()
    try:
        with sftp.file(temporary, "wb") as remote_file:
            remote_file.write(data)
            remote_file.flush()
        sftp.chmod(temporary, mode)
        try:
            sftp.posix_rename(temporary, remote_path)
        except OSError:
            # Dropbear/SFTP builds without the OpenSSH rename extension still
            # have same-filesystem `mv`, which is atomic for regular files.
            out, err, code = run(
                c,
                f"mv -f -- {shlex.quote(temporary)} {shlex.quote(remote_path)}",
            )
            if code != 0:
                raise OSError(
                    f"remote atomic replace failed ({code}): {(err or out).strip()}"
                )
    finally:
        try:
            sftp.remove(temporary)
        except OSError:
            pass
        sftp.close()
