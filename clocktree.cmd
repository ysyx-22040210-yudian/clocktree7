@echo off
cd /d "%~dp0"
if defined CLOCKTREE_PYTHON (
  "%CLOCKTREE_PYTHON%" -m clocktree %*
) else (
  python -m clocktree %*
)
