# Validation workspace

Tool source is shared between Windows `D:/VMshare/clocktree7` and Linux
`/mnt/hgfs/VMshare/clocktree7` (mount confirmed by SSH on 2026-11-02).

Source repositories and pinned dependencies live under `validation/projects`
and `validation/deps`. Generated file lists, extraction results and reports
are written below this directory. VCS build caches may reside on the VM's
native filesystem at `/home/clocktree7-work`.

Do not put SSH passwords or license contents in this directory.

The primary UI is now native Tk/ttk (`bash launch.sh`). Native pixel captures
and control / extraction acceptance records are in `tk_gui/`. The GUI has
been exercised under the VM's ordinary desktop user `host`; the live GNOME
window is opened from shared source. The web UI remains an optional entry
through `launch_web.sh`.
