"""Command/file transport: SSH (desktop) or local subprocess (on-device)."""
from __future__ import annotations

import os
import subprocess
import time


class PtySession:
    def write_line(self, text: str) -> None:
        raise NotImplementedError

    def read(self, timeout: float = 0.5) -> str:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class Transport:
    def run(self, cmd: str, timeout: float = 30):
        raise NotImplementedError

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    def write_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def write_text(self, path: str, text: str) -> None:
        self.write_bytes(path, text.encode("utf-8"))

    def exists(self, path: str) -> bool:
        _, _, code = self.run(f"test -e {path}", timeout=5)
        return code == 0

    def open_pty(self, command: str | None = None) -> PtySession:
        raise NotImplementedError

    def ensure_connected(self, **kwargs) -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return True


class SshPtySession(PtySession):
    def __init__(self, channel):
        self._ch = channel

    def write_line(self, text: str) -> None:
        self._ch.sendall(text + "\n")

    def read(self, timeout: float = 0.5) -> str:
        data = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if not self._ch.recv_ready():
                    if data or self._ch.closed or self._ch.exit_status_ready():
                        break
                    time.sleep(0.05)
                    continue
                chunk = self._ch.recv(4096)
                if not chunk:
                    break
                data += chunk
            except Exception:
                break
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self._ch.close()
        except Exception:
            pass


class SshTransport(Transport):
    def __init__(self, ssh):
        self.ssh = ssh

    def run(self, cmd: str, timeout: float = 30):
        return self.ssh.exec(cmd, timeout=timeout)

    def read_bytes(self, path: str) -> bytes:
        return self.ssh.download_bytes(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        self.ssh.upload_bytes(data, path)

    def write_text(self, path: str, text: str) -> None:
        self.ssh.upload_string(text, path)

    def open_pty(self, command: str | None = None) -> PtySession:
        open_pty = getattr(self.ssh, "open_pty_command", None)
        if open_pty:
            return SshPtySession(open_pty(command))
        ch = self.ssh.open_channel()
        return SshPtySession(ch)

    def ensure_connected(self, **kwargs) -> bool:
        fn = getattr(self.ssh, "ensure_connected", None)
        if fn:
            return bool(fn(**kwargs))
        return self.is_connected

    @property
    def is_connected(self) -> bool:
        return bool(getattr(self.ssh, "is_connected", True))


class LocalPtySession(PtySession):
    def __init__(self, command: str = "bluetoothctl"):
        import pty
        import select

        self._select = select
        master_fd, slave_fd = pty.openpty()
        self._proc = subprocess.Popen(
            [command] if isinstance(command, str) else command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        self._fd = master_fd

    def write_line(self, text: str) -> None:
        os.write(self._fd, (text + "\n").encode())

    def read(self, timeout: float = 0.5) -> str:
        data = b""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            ready, _, _ = self._select.select(
                [self._fd], [], [], min(end - time.monotonic(), 0.1)
            )
            if ready:
                try:
                    chunk = os.read(self._fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                data += chunk
            elif data:
                break
        return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.write_line("quit")
            time.sleep(0.2)
        except Exception:
            pass
        try:
            os.close(self._fd)
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class LocalTransport(Transport):
    def run(self, cmd: str, timeout: float = 30):
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"Command timed out after {timeout}s: {cmd}") from e

    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def write_text(self, path: str, text: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def open_pty(self, command: str | None = None) -> PtySession:
        return LocalPtySession(command or "bluetoothctl")


def as_transport(dev) -> Transport:
    if isinstance(dev, Transport):
        return dev
    return SshTransport(dev)
