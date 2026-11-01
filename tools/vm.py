"""Local SSH/SFTP task helper. Connection details arrive via stdin, never saved."""
import json
import os
from pathlib import Path
import posixpath
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)
import paramiko


def main():
    request = json.load(sys.stdin)
    client = paramiko.SSHClient()
    hosts = Path(__file__).resolve().parents[1] / "tmp" / "vm_known_hosts"
    hosts.parent.mkdir(parents=True, exist_ok=True)
    if hosts.exists():
        client.load_host_keys(str(hosts))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        request["host"], username=request["username"], password=request["password"],
        timeout=20, auth_timeout=20, look_for_keys=False, allow_agent=False,
    )
    client.save_host_keys(str(hosts))
    try:
        if request.get("upload") or request.get("download"):
            sftp = client.open_sftp()
            def mkdirs(path):
                if path in ("", "/"):
                    return
                try:
                    sftp.stat(path)
                except IOError:
                    mkdirs(posixpath.dirname(path))
                    sftp.mkdir(path)
            for local, remote in request.get("upload", []):
                source = Path(local)
                if source.is_dir():
                    for item in source.rglob("*"):
                        if item.is_file() and not any(p in ("__pycache__", "node_modules", ".git", ".pytest_cache") for p in item.relative_to(source).parts):
                            target = posixpath.join(remote, item.relative_to(source).as_posix())
                            mkdirs(posixpath.dirname(target))
                            sftp.put(str(item), target)
                else:
                    mkdirs(posixpath.dirname(remote))
                    sftp.put(str(source), remote)
            for remote, local in request.get("download", []):
                Path(local).parent.mkdir(parents=True, exist_ok=True)
                sftp.get(remote, local)
            sftp.close()
        if request.get("command"):
            transport = client.get_transport()
            channel = transport.open_session(timeout=20)
            channel.set_combine_stderr(True)
            channel.exec_command(request["command"])
            while True:
                data = channel.recv(65536)
                if not data:
                    break
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
            code = channel.recv_exit_status()
            print("\nREMOTE_EXIT_CODE=" + str(code), flush=True)
            return code
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
