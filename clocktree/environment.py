"""Locate the installed Verdi selected by the user's command environment."""
import os
from pathlib import Path
import shutil


def resolve_verdi(design=None, environment=None):
    environment = os.environ if environment is None else environment
    design = design or {}
    command = shutil.which('verdi', path=environment.get('PATH', ''))
    # A saved installation directory is only a compatibility fallback. The
    # active command takes precedence, including over paths from another host.
    if not command:
        for home in (environment.get('VERDI_HOME'), design.get('verdi_home')):
            if home:
                command = shutil.which(str(Path(home).expanduser() / 'bin' / 'verdi'))
                if command:
                    break
    if not command:
        raise ValueError('未找到 verdi 命令。请在已启用 Verdi 的环境中启动工具（将 verdi 加入 PATH）。')
    executable = Path(command).absolute()
    candidates = list(executable.resolve().parents) + list(executable.parents)
    candidates.extend(Path(home).expanduser().resolve() for home in
                      (environment.get('VERDI_HOME'), design.get('verdi_home')) if home)
    for home in candidates:
        if (home / 'share/NPI/L1/TCL/npi_L1.tcl').is_file():
            # Synopsys commands may all symlink to .wrapper and dispatch by
            # argv[0]; resolve the installation, never rename the command.
            return str(executable), str(home)
    raise ValueError('已找到 verdi：{}，但无法定位其 share/NPI/L1/TCL/npi_L1.tcl。请检查命令是否指向完整安装。'.format(executable))
