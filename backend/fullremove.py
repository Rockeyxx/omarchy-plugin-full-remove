#!/usr/bin/env python3
"""Full Remove backend. Runs as the user, never as root.

Called by Panel.qml as:  python3 -I <plugin>/backend/fullremove.py <command> [args]
Every command prints one JSON document on stdout (except `run`, the detached
runner, which writes a log and a state file instead). See the design doc for
the command list. Standard library only, one file, so `python3 -I` works.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.parse

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# Never offered for deletion, whatever matches. The owner's script list plus
# user dotfiles and dev-tool homes (design §2).
PROTECT = frozenset("""
.config .local .cache .var .ssh .gnupg .claude .claude-mem omarchy hypr applications
icons fonts themes systemd autostart Trash keyrings flatpak mime dbus-1 pulse pipewire
wireplumber fontconfig gtk-3.0 gtk-4.0 xdg-desktop-portal Desktop Documents Downloads
Music Pictures Videos Work
.claude.json .bashrc .bash_profile .bash_history .profile .zshrc .gitconfig .npmrc
.Xresources .cargo .rustup .npm .bun .pki mise uv go pipx gem
""".split())

# Words that say nothing about which app a folder belongs to.
GENERIC = frozenset("""
desktop client app application electron java wine microsoft google mozilla github
gnome kde freedesktop org com io bin share data launcher studio
""".split())

# Window classes that name a runtime, not an app.
RUNTIME_CLASS_RE = re.compile(r"^(electron\d*|java.*|wine.*)$", re.I)

PKG_SUFFIX_RE = re.compile(r"-(bin|git|appimage|desktop|electron|nightly|beta|stable)$")

# Exec wrappers: transparent ones are skipped; the rest mean "no useful binary".
EXEC_TRANSPARENT = {"env", "uwsm", "uwsm-app"}
EXEC_STOP_RE = re.compile(
    r"^(sh|bash|zsh|dash|flatpak|snap|electron\d*|java|wine.*|python[\d.]*|node|"
    r"gtk-launch|xdg-open|omarchy-.*)$"
)

HELPER = "/usr/lib/full-remove/full-remove-helper"
HELPER_SUPPORTED = (1,)


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------

def norm(s: str) -> str:
    """Lowercase, non-alphanumerics to single spaces (the script's `norm`)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def squash(s: str) -> str:
    """Lowercase alphanumerics only (the script's `squash`)."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _entry(name: str) -> str:
    return name[: -len(".desktop")] if name.endswith(".desktop") else name


def matches(name: str, kw: str) -> bool:
    """Does folder/file `name` belong to keyword `kw`? Length tiers (design §2)."""
    kc = squash(kw)
    if not kc:
        return False
    name = _entry(name)
    if len(kc) < 4:
        e = name.lower()
        if e.startswith("."):
            e = e[1:]
        return e == kw.lower()
    nc = squash(name)
    if nc == kc or f" {norm(kw)} " in f" {norm(name)} ":
        return True
    return len(kc) >= 6 and nc.startswith(kc)


def tier_keywords(strong: set[str], maybe: set[str]) -> tuple[set[str], set[str]]:
    """Drop runtime classes, move generic words to maybe, dedupe."""
    s, m = set(), set()
    for kw in strong:
        if not squash(kw) or RUNTIME_CLASS_RE.match(kw):
            continue
        (m if squash(kw) in GENERIC else s).add(kw)
    strong_sq = {squash(k) for k in s}
    for kw in maybe:
        if squash(kw) and not RUNTIME_CLASS_RE.match(kw) and squash(kw) not in strong_sq:
            m.add(kw)
    return s, m


def classify(name, strong, maybe, others):
    """Return (tier, reason) or None. `others` = [(keyword, label)] of things
    that are NOT being removed; a strong match that also matches one of them
    is demoted to maybe."""
    for kw in sorted(strong):
        if matches(name, kw):
            for okw, label in others:
                if matches(name, okw):
                    return "maybe", f"also matches {label}"
            return "strong", f"matches '{kw}'"
    for kw in sorted(maybe):
        if matches(name, kw):
            return "maybe", f"matched '{kw}'"
    return None


def strip_pkg_suffix(pkg: str) -> str:
    return PKG_SUFFIX_RE.sub("", pkg)


def exec_binary(exec_line: str) -> str | None:
    """The app binary an Exec= line runs, or None if it goes through an
    interpreter/launcher wrapper."""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None
    prev = None
    for tok in tokens:
        base = os.path.basename(tok)
        if base in EXEC_TRANSPARENT or (prev in ("uwsm",) and tok == "app") or tok == "--":
            prev = base
            continue
        if prev == "env" and "=" in tok or tok.startswith("-") or tok.startswith("%"):
            continue
        if EXEC_STOP_RE.match(base):
            return None
        return base or None
    return None


# ----------------------------------------------------------------------------
# Paths, sizes, Trash
# ----------------------------------------------------------------------------

def _roots(home: str) -> list[str]:
    return [os.path.join(home, r) for r in (".config", ".local/share", ".local/state", ".cache", ".var/app")]


def validate_home_path(path: str, home: str, protect=PROTECT) -> str | None:
    """Reason to refuse `path`, or None if it may be trashed/deleted (design §2)."""
    if not os.path.isabs(path):
        return "not an absolute path"
    if ".." in path.split("/"):
        return "contains '..'"
    home = os.path.normpath(home)
    path = os.path.normpath(path)
    if not path.startswith(home + "/"):
        return "outside the home folder"
    if not os.path.lexists(path):
        return "does not exist"
    name, parent = os.path.basename(path), os.path.dirname(path)
    if name in protect:
        return "protected"
    if parent in _roots(home):
        return None
    if parent == home and name.startswith("."):
        return None
    if parent in (os.path.join(home, ".cache/yay"), os.path.join(home, ".cache/paru/clone"), os.path.join(home, "snap")):
        return None
    if name.endswith(".desktop") and parent in (
        os.path.join(home, ".local/share/applications"), os.path.join(home, ".config/autostart")
    ):
        return None
    return "not in a cleanup location"


def tree_size(path: str) -> int:
    """Disk usage in bytes (st_blocks), never following symlinks, one filesystem."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    total = st.st_blocks * 512
    if not stat.S_ISDIR(st.st_mode):
        return total
    dev, stack = st.st_dev, [path]
    while stack:
        try:
            it = os.scandir(stack.pop())
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += s.st_blocks * 512
                if stat.S_ISDIR(s.st_mode) and s.st_dev == dev:
                    stack.append(e.path)
    return total


def delete_path(path: str) -> None:
    """Permanent delete: unlink files and links, rmtree only real folders."""
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def gio_trash(path: str) -> tuple[bool, str]:
    r = subprocess.run(["gio", "trash", "--", path], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _trash_dir() -> str:
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local/share")
    return os.path.join(data, "Trash")


def parse_trashinfo(text: str) -> tuple[str | None, dt.datetime | None]:
    path = when = None
    for line in text.splitlines():
        if line.startswith("Path="):
            path = urllib.parse.unquote(line[5:].strip())
        elif line.startswith("DeletionDate="):
            try:
                when = dt.datetime.fromisoformat(line[13:].strip())
            except ValueError:
                when = None
    return path, when


def empty_trashed(since: dt.datetime, paths: list[str]) -> list[str]:
    """Permanently delete home-Trash entries this run created (original path in
    `paths`, deleted at or after `since` - 1s). Returns the original paths."""
    trash = _trash_dir()
    info_dir, files_dir = os.path.join(trash, "info"), os.path.join(trash, "files")
    wanted, cutoff, removed = set(paths), since - dt.timedelta(seconds=1), []
    try:
        infos = sorted(os.listdir(info_dir))
    except OSError:
        return removed
    for fn in infos:
        if not fn.endswith(".trashinfo"):
            continue
        ipath = os.path.join(info_dir, fn)
        try:
            with open(ipath, encoding="utf-8", errors="replace") as f:
                orig, when = parse_trashinfo(f.read())
        except OSError:
            continue
        if orig not in wanted or when is None or when < cutoff:
            continue
        fpath = os.path.join(files_dir, fn[: -len(".trashinfo")])
        if os.path.lexists(fpath):
            delete_path(fpath)
        os.unlink(ipath)
        removed.append(orig)
    return sorted(removed)


# ----------------------------------------------------------------------------
# Leftover scan
# ----------------------------------------------------------------------------

def _result(path: str, tier: str, reason: str) -> dict:
    return {"path": path, "tier": tier, "reason": reason, "size": tree_size(path), "link": os.path.islink(path)}


def scan_leftovers(home, strong, maybe, others, fixed=()):
    """Leftover candidates in `home` for the given keywords (design §2)."""
    found: dict[str, dict] = {}
    for p in fixed:
        if os.path.lexists(p) and validate_home_path(p, home) is None:
            found[p] = _result(p, "strong", "known location")
    dirs = [(r, False) for r in _roots(home)] + [(home, True)]
    dirs += [(os.path.join(home, ".local/share/applications"), "desktop"),
             (os.path.join(home, ".config/autostart"), "desktop")]
    for d, mode in dirs:
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            n = e.name
            if e.path in found or n in PROTECT:
                continue
            if mode is True and not n.startswith("."):
                continue
            if mode == "desktop" and not n.endswith(".desktop"):
                continue
            c = classify(n, strong, maybe, others)
            if c:
                found[e.path] = _result(e.path, *c)
    return sorted(found.values(), key=lambda r: (r["tier"] != "strong", r["path"]))


# ----------------------------------------------------------------------------
# Parsers (pure functions, fed with command output)
# ----------------------------------------------------------------------------

_UNITS = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
          "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}


def parse_size(text: str) -> int:
    m = re.match(r"^\s*([\d.,]+)\s*([a-zA-Z]+)\s*$", text or "")
    if not m or m.group(2).lower() not in _UNITS:
        return 0
    try:
        return int(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2).lower()])
    except ValueError:
        return 0


def parse_pacman_qi(text: str) -> list[dict]:
    out, cur, key = [], {}, None
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur.get("Name"):
                out.append({"name": cur["Name"], "version": cur.get("Version", ""),
                            "description": cur.get("Description", ""),
                            "size": parse_size(cur.get("Installed Size", ""))})
            cur, key = {}, None
            continue
        m = re.match(r"^([A-Za-z][A-Za-z ]*?)\s*:\s?(.*)$", line)
        if m and not line.startswith(" "):
            key = m.group(1).strip()
            cur[key] = m.group(2).strip()
        elif key:
            cur[key] += " " + line.strip()
    return out


def parse_pacman_ql(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        pkg, _, path = line.partition(" ")
        if path:
            out.setdefault(pkg, []).append(path)
    return out


def parse_desc_base(text: str) -> str | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == "%BASE%" and i + 1 < len(lines):
            return lines[i + 1].strip() or None
    return None


def parse_flatpak_apps(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4 or parts[3] not in ("system", "user"):
            continue
        appid, name, size, inst = parts[:4]
        out.append({"id": f"flatpak-{inst}:{appid}", "appid": appid, "name": name or appid,
                    "size": parse_size(size), "source": "flatpak", "installation": inst})
    return out


def parse_flatpak_runtimes(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].count("/") == 2:
            out.append({"ref": "runtime/" + parts[0], "installation": parts[1]})
    return out


def parse_app_runtimes(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].count("/") == 2:
            out.add("runtime/" + parts[1])
    return out


def parse_extensions(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"^\s*Extension:\s*(\S+)\s*$", text, re.M)}


def unused_runtimes(runtimes: list[dict], used: set[str]) -> list[dict]:
    return [r for r in runtimes if r["ref"] not in used]


def parse_snap_list(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] == "Name" or line.startswith("No snaps"):
            continue
        out.append({"name": parts[0], "version": parts[1], "rev": parts[2]})
    return out


def _desktop_unescape(v: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "s": " ", "r": "\r", "\\": "\\"}.get(m.group(1), m.group(1)), v)


def parse_desktop(text: str) -> dict:
    """Non-localized keys of the [Desktop Entry] group."""
    out, in_entry = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_entry = s == "[Desktop Entry]"
            continue
        if not in_entry or not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        if "[" not in k and k not in out:
            out[k] = _desktop_unescape(v.strip())
    return out


def appimage_from_exec(exec_line: str) -> str | None:
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return None
    return tokens[0] if tokens and tokens[0].lower().endswith(".appimage") else None


_AI_CUT = re.compile(r"[-_ ](v?\d|x86[-_]64|amd64|aarch64|arm64|i[3-6]86)", re.I)


def appimage_name(filename: str) -> str:
    stem = re.sub(r"\.appimage$", "", os.path.basename(filename), flags=re.I)
    m = _AI_CUT.search(stem)
    return (stem[: m.start()] if m and m.start() > 0 else stem).strip()


def appimage_icon_owned(icon: str, appimage_path: str, name: str, home: str) -> bool:
    if not icon or not os.path.isabs(icon):
        return False
    icon = os.path.normpath(icon)
    icons_dir = os.path.join(home, ".local/share/icons") + "/"
    in_place = icon.startswith(icons_dir) or os.path.dirname(icon) == os.path.dirname(appimage_path)
    base = os.path.basename(icon).lower()
    return in_place and (base.startswith("appimagekit_") or (bool(squash(name)) and squash(name) in squash(base)))


def parse_gearlever_keyfile(text: str) -> str | None:
    m = re.search(r"^appimages-default-folder\s*=\s*'?(.*?)'?\s*$", text, re.M)
    return m.group(1) if m and m.group(1) else None


def _asar_product_name(read_at) -> str | None:
    import struct
    try:
        _, header_size = struct.unpack("<II", read_at(0, 8))
        _, strlen = struct.unpack("<II", read_at(8, 8))
        if strlen > 50_000_000:
            return None
        header = json.loads(read_at(16, strlen))
        entry = header["files"]["package.json"]
        data = read_at(8 + header_size + int(entry["offset"]), int(entry["size"]))
        name = json.loads(data).get("productName")
        return name if isinstance(name, str) and name else None
    except Exception:
        return None


def asar_product_name(blob: bytes) -> str | None:
    return _asar_product_name(lambda o, s: blob[o:o + s])


def asar_product_name_file(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            def read_at(o, s):
                f.seek(o)
                return f.read(s)
            return _asar_product_name(read_at)
    except OSError:
        return None


def parse_npm_ls(text: str) -> list[str]:
    try:
        deps = json.loads(text).get("dependencies") or {}
    except ValueError:
        return []
    return sorted(k for k in deps if k not in ("npm", "corepack"))


def parse_bun_ls(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if "── " in line:
            spec = line.split("── ", 1)[1].strip()
            out.append(spec.rsplit("@", 1)[0] if spec.rfind("@") > 0 else spec)
    return out


def parse_uv_tools(text: str) -> list[str]:
    return [l.split()[0] for l in text.splitlines() if l.strip() and not l.startswith("-") and " v" in l]


def parse_pipx(text: str) -> list[str]:
    i = text.find("{")
    try:
        return sorted((json.loads(text[i:]) .get("venvs") or {}).keys()) if i >= 0 else []
    except ValueError:
        return []


def parse_cargo_list(text: str) -> list[str]:
    return [l.split()[0] for l in text.splitlines() if l and not l[0].isspace() and l.rstrip().endswith(":")]


def gem_names(spec_files: list[str]) -> list[str]:
    return sorted({re.sub(r"-\d.*$", "", f[: -len(".gemspec")]) for f in spec_files if f.endswith(".gemspec")})


_DEV_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_JS_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$")
DEV_TOOLS = ("npm", "bun", "uv", "pipx", "cargo", "go", "gem")


def dev_argv(tool: str, name: str) -> list[str] | None:
    """Uninstall argv for a dev tool package, or None if invalid. `go` has no
    uninstall command (its binary is trashed instead)."""
    ok = _JS_NAME if tool in ("npm", "bun") else _DEV_NAME
    if not name or not ok.match(name):
        return None
    return {
        "npm": ["npm", "uninstall", "-g", name],
        "bun": ["bun", "remove", "-g", name],
        "uv": ["uv", "tool", "uninstall", name],
        "pipx": ["pipx", "uninstall", name],
        "cargo": ["cargo", "uninstall", name],
        "gem": ["gem", "uninstall", "--user-install", "-a", "-x", name],
    }.get(tool)


_PKG = re.compile(r"^[a-z0-9][a-z0-9@._+-]*$")
_FLATPAK = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*){2,}$")
_SNAP = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")


def parse_item(item: str):
    """Validate an item ID from the panel. Returns (kind, value[, extra]) or None."""
    kind, sep, val = item.partition(":")
    if not sep:
        return None
    if kind == "pkg" and _PKG.match(val):
        return ("pkg", val)
    if kind in ("flatpak-system", "flatpak-user") and _FLATPAK.match(val):
        return (kind, val)
    if kind == "snap" and len(val) <= 40 and _SNAP.match(val) and val != "snapd" and not val.startswith("core") and val != "bare":
        return ("snap", val)
    if kind == "appimage" and os.path.isabs(val) and val.lower().endswith(".appimage"):
        return ("appimage", val)
    if kind == "dev":
        tool, _, name = val.partition(":")
        if tool in DEV_TOOLS and (dev_argv(tool, name) or (tool == "go" and _DEV_NAME.match(name))):
            return ("dev", tool, name)
    return None


# Mirrors the helper's guard (root/full-remove-helper, guard_hit). The helper
# is the real check; this only greys out rows the helper would refuse.
_PROTECTED_RE = re.compile(
    r"^(base|filesystem|glibc|bash|coreutils|pacman|systemd|sudo|polkit|shadow|pam|pam-.*|pambase"
    r"|linux|linux-firmware|linux-firmware-.*|limine|limine-.*|grub|systemd-boot.*|efibootmgr|mkinitcpio|dracut"
    r"|amd-ucode|intel-ucode|cryptsetup|btrfs-progs|snapper"
    r"|ufw|firewalld|nftables|iptables|iptables-.*|apparmor|audit|clamav|fail2ban|opensnitch"
    r"|omarchy|omarchy-.*)$")
_PROTECTED_EXCEPT_RE = re.compile(r"^linux(-.*)?-(docs|headers)$")


def is_protected(name: str, omarchy_deps: set, kernels: set = frozenset()) -> bool:
    if _PROTECTED_EXCEPT_RE.match(name):
        return False
    return bool(_PROTECTED_RE.match(name)) or name in omarchy_deps or name in kernels


def installed_kernels(modules_dir: str = "/usr/lib/modules") -> set[str]:
    out = set()
    try:
        for v in os.listdir(modules_dir):
            f = os.path.join(modules_dir, v, "pkgbase")
            if os.path.isfile(f) and not os.path.islink(f):
                out.add(_read(f).strip().splitlines()[0] if _read(f).strip() else "")
    except OSError:
        pass
    return out - {""}


# System pieces: shown in the System tab, view-only.
_SYSTEM_RE = re.compile(
    r"^(linux-firmware.*|.*-firmware|linux(-.*)?-headers|.*-dkms|mesa.*|lib32-mesa.*|vulkan-.*|lib32-vulkan-.*"
    r"|nvidia.*|lib32-nvidia.*|opencl-.*|xf86-.*|xorg-server.*|.*-ucode|base|base-devel"
    r"|pipewire.*|wireplumber|networkmanager|iwd|bluez.*|sof-firmware|alsa-firmware|intel-media-driver|libva-.*)$")

# Language runtimes and toolchains: shown in the Dev tools tab.
_DEV_RE = re.compile(
    r"^(python|python[23]?|python-.*|pyenv|nodejs.*|npm|pnpm|yarn|bun|deno|go|gopls|rust|rustup|cargo-.*"
    r"|jdk.*|jre.*|.*-openjdk.*|java-.*|maven|gradle|kotlin|dotnet-.*|mono|ruby|ruby-.*|php|php-.*|lua|luarocks"
    r"|clang|llvm|llvm-.*|lldb|gcc|gcc-.*|gdb|cmake|make|meson|ninja|autoconf|automake|pkgconf|zig|ghc|cabal-install"
    r"|cuda|cuda-.*|uv|pipx|poetry)$")


_SYSTEM_UNIT_RE = re.compile(r"^/usr/lib/systemd/system/[^/]+\.(service|socket|timer)$")


def is_service_package(files: list[str]) -> bool:
    """Ships a system unit and no app launcher (acpid, earlyoom, atd...)."""
    return any(_SYSTEM_UNIT_RE.match(f) for f in files) and not _desktop_files(files)


def package_group(name: str, in_omarchy: bool, protected: bool, service: bool = False) -> str:
    """Tab for a pacman package: system | omarchy | dev | services | user (Apps)."""
    if protected or _SYSTEM_RE.match(name):
        return "system"
    if in_omarchy:
        return "omarchy"
    if _DEV_RE.match(name):
        return "dev"
    if service:
        return "services"
    return "user"


def parse_package_list(text: str) -> set[str]:
    """Omarchy's install/*.packages files: one package per line, # comments."""
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line.split()[0])
    return out


def cache_pkg_name(filename: str) -> str | None:
    stem = filename[:-4] if filename.endswith(".sig") else filename
    if ".pkg.tar" not in stem:
        return None
    parts = stem.split(".pkg.tar")[0].split("-")
    return "-".join(parts[:-3]) if len(parts) >= 4 else None


def parse_breaks(stderr: str) -> list[tuple[str, str]]:
    return re.findall(r"removing (\S+) breaks dependency '[^']+' required by (\S+)", stderr)


def removal_result(rc: int, stdout: str, stderr: str, pkgs: list[str]) -> dict:
    """Interpret `pacman -Rsp --print-format %n`. pacman prints the
    "breaks dependency" lines on stdout and the error line on stderr."""
    if rc == 0:
        return {"removes": stdout.split(), "breaks": [], "blocked": False}
    breaks = [{"package": a, "required_by": b} for a, b in parse_breaks(stdout + "\n" + stderr)]
    if not breaks:
        breaks = [{"package": "", "required_by": (stderr or stdout).strip()[:300]}]
    return {"removes": list(pkgs), "breaks": breaks, "blocked": True}


def launcher_is_own(desktop: dict, stem: str, own_stems: set, own_bins: set, own_flatpaks: set) -> bool:
    """Is a user launcher just another entry point of an app being removed?"""
    if stem in own_stems:
        return True
    exec_line = desktop.get("Exec", "")
    if exec_binary(exec_line) in own_bins:
        return True
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        tokens = []
    return any(t in own_flatpaks for t in tokens)


# ----------------------------------------------------------------------------
# Detached runner (design §6)
# ----------------------------------------------------------------------------

def run_dir() -> str:
    """$XDG_RUNTIME_DIR/full-remove: created 0700, refused if a symlink or not ours."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isabs(base):
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    d = os.path.join(base, "full-remove")
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"unsafe run folder: {d}")
    os.chmod(d, 0o700)
    return d


def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def run_command(argv: list[str], write) -> int:
    """Run argv (no shell), stream stdout+stderr lines to write(), return rc."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, errors="replace")
    except OSError as e:
        write(f"cannot run {argv[0]}: {e}")
        return 127
    for line in p.stdout:
        write(line.rstrip("\n"))
    return p.wait()


def run_queue(rdir: str, stage: str, ids: list[str], steps) -> dict:
    """Run steps [(name, fn(write) -> rc)] one after another. Checks the cancel
    file before each step; pkexec 126/127 on the root step ends the run."""
    import fcntl
    lockfd = os.open(os.path.join(rdir, "lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lockfd)
        return {"error": "busy"}
    cancel = os.path.join(rdir, "cancel")
    if os.path.lexists(cancel):
        os.unlink(cancel)
    start = dt.datetime.now().replace(microsecond=0)
    log = os.path.join(rdir, f"run-{start:%Y%m%d-%H%M%S}.log")
    st = {"stage": stage, "ids": ids, "start": start.isoformat(), "log": log, "steps": [],
          "current": None, "finished": False, "cancelled": False, "reason": ""}
    spath = os.path.join(rdir, "state.json")
    _save_json(spath, st)
    try:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lf:
            def write(line: str) -> None:
                lf.write(line + "\n")
                lf.flush()
            for name, fn in steps:
                if os.path.lexists(cancel):
                    st.update(cancelled=True, reason="cancelled by user")
                    write("== cancelled: remaining steps skipped")
                    break
                st["current"] = name
                _save_json(spath, st)
                write(f"== step: {name}")
                try:
                    rc = fn(write)
                except Exception as e:  # report, never crash mid-queue
                    write(f"error: {e}")
                    rc = 1
                st["steps"].append({"name": name, "rc": rc})
                _save_json(spath, st)
                if name == "root" and rc in (126, 127):
                    st.update(cancelled=True, reason="password prompt dismissed or not authorized")
                    write("== password prompt dismissed or not authorized: run cancelled")
                    break
    finally:
        st.update(finished=True, current=None)
        _save_json(spath, st)
        os.close(lockfd)
    return st


# ----------------------------------------------------------------------------
# I/O layer: listing, planning, removing (runs real commands)
# ----------------------------------------------------------------------------

def run(argv: list[str], timeout: int = 60, stdin_text: str | None = None) -> tuple[int, str, str]:
    env = dict(os.environ, LC_ALL="C")
    try:
        r = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=env, input=stdin_text,
                           stdin=None if stdin_text is not None else subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def _home() -> str:
    return os.path.expanduser("~")


def _read(path: str, limit: int = 1_000_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def helper_status() -> dict:
    out = {"path": HELPER, "installed": False, "root_owned": False, "protocol": None,
           "supported": False, "policy": os.path.isfile(f"/usr/share/polkit-1/actions/io.github.rockeyxx.full-remove.policy")}
    try:
        for p in (HELPER, os.path.dirname(HELPER)):
            st = os.lstat(p)
            if st.st_uid != 0 or st.st_mode & 0o022 or stat.S_ISLNK(st.st_mode):
                return dict(out, installed=True)
    except OSError:
        return out
    out.update(installed=True, root_owned=True)
    rc, so, _ = run([HELPER, "--version"], timeout=10)
    m = re.match(r"^full-remove-helper (\d+)$", so.strip())
    if rc == 0 and m:
        out["protocol"] = int(m.group(1))
        out["supported"] = out["protocol"] in HELPER_SUPPORTED and out["policy"]
    return out


def cmd_status() -> dict:
    try:
        rd = run_dir()
    except RuntimeError as e:
        rd = f"error: {e}"
    tools = {t: bool(shutil.which(t)) for t in ("pacman", "flatpak", "snap", "gio", *DEV_TOOLS)}
    return {"helper": helper_status(), "tools": tools, "run_dir": rd}


def _desktop_files(paths: list[str]) -> list[str]:
    return [p for p in paths if p.startswith("/usr/share/applications/") and p.endswith(".desktop")]


def find_appimages(home: str) -> list[dict]:
    found: dict[str, dict] = {}
    appdir = os.path.join(home, ".local/share/applications")
    try:
        desktops = sorted(os.listdir(appdir))
    except OSError:
        desktops = []
    for fn in desktops:
        if not fn.endswith(".desktop"):
            continue
        dpath = os.path.join(appdir, fn)
        if os.path.islink(dpath):
            continue
        d = parse_desktop(_read(dpath))
        ai = appimage_from_exec(d.get("Exec", ""))
        if ai:
            found[ai] = {"path": ai, "desktop": dpath, "name": d.get("Name") or appimage_name(ai),
                         "icon": d.get("Icon", ""), "wmclass": d.get("StartupWMClass", "")}
    dirs = [os.path.join(home, d) for d in ("Applications", "AppImages", "Desktop/AppImage")]
    gl = parse_gearlever_keyfile(_read(os.path.join(home, ".var/app/it.mijorus.gearlever/config/glib-2.0/settings/keyfile")))
    if gl:
        dirs.append(os.path.expanduser(gl))
    for d in dirs:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.name.lower().endswith(".appimage") and e.is_file(follow_symlinks=False) and e.path not in found:
                found[e.path] = {"path": e.path, "desktop": None, "name": appimage_name(e.name), "icon": "", "wmclass": ""}
    out = []
    for a in found.values():
        try:
            size = os.lstat(a["path"]).st_size
        except OSError:
            continue
        a.update(size=size, in_home=os.path.normpath(a["path"]).startswith(home + "/"))
        out.append(a)
    return out


def omarchy_packages() -> set[str]:
    """Packages Omarchy's installer lists (install/*.packages)."""
    out = set()
    for base in {os.environ.get("OMARCHY_PATH") or "", "/usr/share/omarchy"}:
        d = os.path.join(base, "install") if base else ""
        try:
            for fn in os.listdir(d):
                if fn.endswith(".packages"):
                    out |= parse_package_list(_read(os.path.join(d, fn)))
        except OSError:
            pass
    return out


def omarchy_deps(installed: list[str]) -> set[str]:
    om = [n for n in installed if n.startswith("omarchy")]
    if not om:
        return set()
    deps = set()
    for line in run(["pacman", "-Qi", "--", *om])[1].splitlines():
        if line.startswith("Depends On"):
            deps |= {re.split(r"[<>=]", d)[0] for d in line.split(":", 1)[1].split() if d != "None"}
    return deps


def cmd_list_apps() -> dict:
    home, apps = _home(), []
    rc, explicit, _ = run(["pacman", "-Qqe"])
    names = explicit.split()
    foreign = set(run(["pacman", "-Qqm"])[1].split())
    om_pkgs = omarchy_packages()
    om_deps = omarchy_deps(run(["pacman", "-Qq"])[1].split())
    kernels = installed_kernels()
    if names:
        info = {p["name"]: p for p in parse_pacman_qi(run(["pacman", "-Qi", "--", *names])[1])}
        files = parse_pacman_ql(run(["pacman", "-Ql", "--", *names])[1])
        for n in names:
            display = ""
            for dfile in _desktop_files(files.get(n, [])):
                display = parse_desktop(_read(dfile)).get("Name", "")
                if display:
                    break
            p = info.get(n, {})
            apps.append({"id": f"pkg:{n}", "name": n, "display": display or n, "source": "aur" if n in foreign else "pacman",
                         "size": p.get("size", 0), "description": p.get("description", ""),
                         "group": package_group(n, n in om_pkgs, is_protected(n, om_deps, kernels),
                                                is_service_package(files.get(n, []))),
                         "protected": is_protected(n, om_deps, kernels)})
    if shutil.which("flatpak"):
        for a in parse_flatpak_apps(run(["flatpak", "list", "--app", "--columns=application,name,size,installation"])[1]):
            apps.append({"id": a["id"], "name": a["appid"], "display": a["name"], "source": f"flatpak-{a['installation']}",
                         "size": a["size"], "description": a["appid"]})
    if shutil.which("snap"):
        for s in parse_snap_list(run(["snap", "list"])[1]):
            if parse_item(f"snap:{s['name']}") is None:
                continue
            try:
                size = os.path.getsize(f"/var/lib/snapd/snaps/{s['name']}_{s['rev']}.snap")
            except OSError:
                size = 0
            apps.append({"id": f"snap:{s['name']}", "name": s["name"], "display": s["name"], "source": "snap",
                         "size": size, "description": f"snap {s['version']}"})
    for a in find_appimages(home):
        apps.append({"id": f"appimage:{a['path']}" if a["in_home"] else "", "name": os.path.basename(a["path"]),
                     "display": a["name"], "source": "appimage", "size": a["size"],
                     "description": a["path"] if a["in_home"] else f"{a['path']} (outside your home: remove manually)"})
    return {"apps": apps}


def _go_bin() -> str | None:
    rc, out, _ = run(["go", "env", "GOBIN", "GOPATH"], timeout=20)
    if rc != 0:
        return None
    lines = out.splitlines() + ["", ""]
    return lines[0].strip() or (os.path.join(lines[1].strip().split(":")[0], "bin") if lines[1].strip() else None)


def cmd_list_dev() -> dict:
    items, missing = [], []

    def add(tool, names, extra=None):
        for n in names:
            argv = dev_argv(tool, n)
            if argv or tool == "go":
                items.append({"id": f"dev:{tool}:{n}", "tool": tool, "name": n,
                              "command": " ".join(argv) if argv else f"move {extra}/{n} to Trash"})

    for tool, argv, parser in (
        ("npm", ["npm", "ls", "-g", "--depth=0", "--json"], parse_npm_ls),
        ("bun", ["bun", "pm", "ls", "-g"], parse_bun_ls),
        ("uv", ["uv", "tool", "list"], parse_uv_tools),
        ("pipx", ["pipx", "list", "--json"], parse_pipx),
        ("cargo", ["cargo", "install", "--list"], parse_cargo_list),
    ):
        if not shutil.which(tool):
            missing.append(tool)
            continue
        add(tool, parser(run(argv, timeout=30)[1]))
    if shutil.which("go"):
        gb = _go_bin()
        if gb and os.path.isdir(gb):
            add("go", sorted(e.name for e in os.scandir(gb) if e.is_file(follow_symlinks=False)), gb)
    else:
        missing.append("go")
    if shutil.which("ruby") and shutil.which("gem"):
        rc, udir, _ = run(["ruby", "-e", "print Gem.user_dir"], timeout=20)
        try:
            add("gem", gem_names(os.listdir(os.path.join(udir.strip(), "specifications"))) if rc == 0 else [])
        except OSError:
            pass
    else:
        missing.append("gem")
    return {"dev": items, "missing": missing}


def _pkg_keywords(name: str, files: list[str]) -> tuple[set, set]:
    strong, maybe = {name, strip_pkg_suffix(name)}, set()
    for f in files:
        if re.match(r"^/usr/bin/[^/]+$", f):
            strong.add(os.path.basename(f))
        m = re.match(r"^/opt/([^/]+)/$", f)
        if m and run(["pacman", "-Qoq", "--", f])[1].split() == [name]:
            strong.add(m.group(1))
        if f.endswith("resources/app.asar"):
            pn = asar_product_name_file(f)
            if pn:
                maybe.add(pn)
        elif f.endswith("resources/app/package.json"):
            try:
                pn = json.loads(_read(f)).get("productName")
                if isinstance(pn, str) and pn:
                    maybe.add(pn)
            except ValueError:
                pass
    for dfile in _desktop_files(files):
        stem = os.path.basename(dfile)[: -len(".desktop")]
        strong |= {stem, stem.split(".")[-1]}
        d = parse_desktop(_read(dfile))
        strong |= {v for v in (d.get("Name"), d.get("StartupWMClass")) if v}
        if d.get("Icon") and not os.path.isabs(d["Icon"]):
            maybe.add(d["Icon"])
        b = exec_binary(d.get("Exec", ""))
        if b:
            maybe.add(b)
    return strong, maybe


def _flatpak_desktop(appid: str, inst: str, home: str) -> dict:
    base = "/var/lib/flatpak" if inst == "system" else os.path.join(home, ".local/share/flatpak")
    return parse_desktop(_read(f"{base}/exports/share/applications/{appid}.desktop"))


def _cache_files(names: set[str]) -> list[str]:
    out = []
    try:
        for e in os.scandir("/var/cache/pacman/pkg"):
            if e.is_file(follow_symlinks=False) and cache_pkg_name(e.name) in names:
                out.append(e.path)
    except OSError:
        pass
    return sorted(out)


def _bin_names(home: str) -> list[tuple[str, str]]:
    out = []
    for d in (".local/bin", ".cargo/bin", "go/bin", ".bun/bin", ".npm-global/bin"):
        try:
            for e in os.scandir(os.path.join(home, d)):
                out.append((e.name, f"~/{d}/{e.name}"))
        except OSError:
            pass
    return out


def build_others(home: str, removing_pkgs: set[str], removing_ids: set[str], own_desktops: set[str],
                 own_stems: set = frozenset(), own_bins: set = frozenset(), own_flatpaks: set = frozenset()) -> list[tuple[str, str]]:
    """Keywords of things that stay installed (for demotion)."""
    others = [(n, f"installed package {n}") for n in run(["pacman", "-Qq"])[1].split() if n not in removing_pkgs]
    if shutil.which("flatpak"):
        for a in parse_flatpak_apps(run(["flatpak", "list", "--app", "--columns=application,name,size,installation"])[1]):
            if a["id"] not in removing_ids:
                others += [(a["appid"].split(".")[-1], f"Flatpak {a['name']}"), (a["name"], f"Flatpak {a['name']}")]
    others += _bin_names(home)
    appdir = os.path.join(home, ".local/share/applications")
    try:
        for fn in os.listdir(appdir):
            p = os.path.join(appdir, fn)
            if fn.endswith(".desktop") and p not in own_desktops:
                d = parse_desktop(_read(p))
                if launcher_is_own(d, fn[:-8], own_stems, own_bins, own_flatpaks):
                    continue
                nm = d.get("Name", "")
                others += [(fn[:-8], f"launcher {fn}")] + ([(nm, f"launcher {fn}")] if nm else [])
    except OSError:
        pass
    # generic words never count as evidence either way
    return [(k, l) for k, l in others if squash(k) and squash(k) not in GENERIC]


def cmd_plan(ids: list[str]) -> dict:
    home = _home()
    items = [parse_item(i) for i in ids]
    if not ids or None in items:
        return {"error": "invalid item: " + ", ".join(i for i, p in zip(ids, items) if p is None)}
    strong, maybe, fixed, own_desktops = set(), set(), [], set()
    own_stems, own_bins, own_flatpaks = set(), set(), set()
    pkgs = [p[1] for p in items if p[0] == "pkg"]
    out = {"items": [], "removes": [], "breaks": [], "cache": [], "root_argv": [], "user": [], "blocked": False}
    if pkgs:
        files = parse_pacman_ql(run(["pacman", "-Ql", "--", *pkgs])[1])
        vers = {p["name"]: p["version"] for p in parse_pacman_qi(run(["pacman", "-Qi", "--", *pkgs])[1])}
        for n in pkgs:
            own_bins |= {os.path.basename(f) for f in files.get(n, []) if re.match(r"^/usr/bin/[^/]+$", f)}
            own_stems |= {os.path.basename(f)[:-8] for f in _desktop_files(files.get(n, []))}
            s, m = _pkg_keywords(n, files.get(n, []))
            strong |= s
            maybe |= m
            base = parse_desc_base(_read(f"/var/lib/pacman/local/{n}-{vers.get(n, '')}/desc")) or n
            fixed += [os.path.join(home, ".cache/yay", base), os.path.join(home, ".cache/paru/clone", base)]
        out.update(removal_result(*run(["pacman", "-Rsp", "--print-format", "%n", "--", *pkgs]), pkgs))
        out["cache"] = _cache_files(set(out["removes"]))
    flat = {a["id"]: a for a in parse_flatpak_apps(run(["flatpak", "list", "--app", "--columns=application,name,size,installation"])[1])} if shutil.which("flatpak") else {}
    appimages = {a["path"]: a for a in find_appimages(home)}
    root_items = [f"pkg:{n}" for n in pkgs]
    for it in items:
        kind = it[0]
        if kind in ("flatpak-system", "flatpak-user"):
            a = flat.get(f"{kind}:{it[1]}")
            if not a:
                return {"error": f"not installed: {it[1]}"}
            strong |= {it[1], it[1].split(".")[-1], a["name"]}
            d = _flatpak_desktop(it[1], a["installation"], home)
            if d.get("StartupWMClass"):
                strong.add(d["StartupWMClass"])
            if d.get("Icon") and not os.path.isabs(d["Icon"]):
                maybe.add(d["Icon"])
            fixed.append(os.path.join(home, ".var/app", it[1]))
            own_flatpaks.add(it[1])
            own_stems.add(it[1])
            if kind == "flatpak-system":
                root_items.append(f"flatpak:{it[1]}")
            else:
                out["user"].append({"id": f"{kind}:{it[1]}", "command": f"flatpak uninstall --user --app -- {it[1]}"})
        elif kind == "snap":
            strong.add(it[1])
            fixed.append(os.path.join(home, "snap", it[1]))
            root_items.append(f"snap:{it[1]}")
        elif kind == "appimage":
            a = appimages.get(it[1])
            if not a or not a["in_home"]:
                return {"error": f"not a known AppImage in your home: {it[1]}"}
            strong |= {v for v in (appimage_name(a["path"]), a["name"], a["wmclass"]) if v}
            if a["icon"] and not os.path.isabs(a["icon"]):
                maybe.add(a["icon"])
            files_ = [a["path"]] + ([a["desktop"]] if a["desktop"] else [])
            if appimage_icon_owned(a["icon"], a["path"], a["name"], home):
                files_.append(a["icon"])
            own_desktops |= {a["desktop"]} if a["desktop"] else set()
            out["user"].append({"id": f"appimage:{a['path']}", "command": "move to Trash: " + ", ".join(files_),
                                "files": files_})
        elif kind == "dev":
            argv = dev_argv(it[1], it[2])
            out["user"].append({"id": f"dev:{it[1]}:{it[2]}",
                                "command": " ".join(argv) if argv else f"move {_go_bin()}/{it[2]} to Trash"})
    if root_items:
        out["root_argv"] = ["pkexec", HELPER, "remove", *root_items]
    strong, maybe = tier_keywords(strong, maybe)
    if strong or maybe or fixed:
        others = build_others(home, set(out["removes"]) | set(pkgs), set(ids), own_desktops,
                              own_stems, own_bins, own_flatpaks)
        lefts = scan_leftovers(home, strong, maybe, others, fixed=fixed)
        skip = set(appimages) | own_desktops
        out["leftovers"] = [l for l in lefts if l["path"] not in skip]
    else:
        out["leftovers"] = []
    out["keywords"] = {"strong": sorted(strong), "maybe": sorted(maybe)}
    return out


def remove_user(ids: list[str], write) -> int:
    """User-level removals: user Flatpaks, AppImages (to Trash), dev tools."""
    home, status = _home(), 0
    appimages = None
    for i in ids:
        it = parse_item(i)
        if not it:
            write(f"invalid item {i}")
            status = 1
            continue
        if it[0] == "flatpak-user":
            write(f"== flatpak uninstall --user --app -- {it[1]}")
            status |= run_command(["flatpak", "uninstall", "--user", "--app", "--noninteractive", "-y", "--", it[1]], write) != 0
        elif it[0] == "appimage":
            appimages = appimages if appimages is not None else {a["path"]: a for a in find_appimages(home)}
            a = appimages.get(it[1])
            if not a or not a["in_home"]:
                write(f"refused: {it[1]} is not a known AppImage in your home")
                status = 1
                continue
            targets = [a["path"]]
            if a["desktop"] and validate_home_path(a["desktop"], home) is None:
                targets.append(a["desktop"])
            if appimage_icon_owned(a["icon"], a["path"], a["name"], home):
                targets.append(a["icon"])
            for t in targets:
                if os.path.lexists(t):
                    ok, msg = gio_trash(t)
                    write(f"{'trashed' if ok else 'could not trash'} {t}{'' if ok else ': ' + msg}")
                    status |= not ok
            run(["update-desktop-database", os.path.join(home, ".local/share/applications")])
        elif it[0] == "dev":
            tool, name = it[1], it[2]
            if tool == "go":
                gb = _go_bin()
                p = os.path.join(gb, name) if gb else ""
                if gb and os.path.isfile(p) and not os.path.islink(p) and os.path.dirname(p) == gb:
                    ok, msg = gio_trash(p)
                    write(f"{'trashed' if ok else 'could not trash'} {p}")
                    status |= not ok
                else:
                    write(f"refused: {name} is not in the go bin folder")
                    status = 1
            else:
                argv = dev_argv(tool, name)
                write("== " + " ".join(argv))
                status |= run_command(argv, write) != 0
    return int(bool(status))


def cmd_installed(ids: list[str]) -> dict:
    res = {}
    for i in ids:
        it = parse_item(i)
        if not it:
            continue
        k = it[0]
        if k == "pkg":
            res[i] = run(["pacman", "-Qq", "--", it[1]])[1].strip() == it[1]
        elif k in ("flatpak-system", "flatpak-user"):
            res[i] = run(["flatpak", "info", "--" + k.split("-")[1], "--app", "--", it[1]])[0] == 0
        elif k == "snap":
            res[i] = run(["snap", "list", "--", it[1]])[0] == 0
        elif k == "appimage":
            res[i] = os.path.lexists(it[1])
        elif k == "dev":
            res[i] = any(d["id"] == i for d in cmd_list_dev()["dev"])
    return {"installed": res}


def _paths_action(paths: list[str], action) -> dict:
    home, results = _home(), []
    for p in paths:
        why = validate_home_path(p, home)
        if why:
            results.append({"path": p, "ok": False, "message": f"refused: {why}"})
            continue
        try:
            ok, msg = action(p)
        except OSError as e:
            ok, msg = False, str(e)
        results.append({"path": p, "ok": ok, "message": msg})
    return {"results": results}


def cmd_trash(paths):
    return _paths_action(paths, gio_trash)


def cmd_delete(paths):
    def _del(p):
        delete_path(p)
        return True, ""
    return _paths_action(paths, _del)


def cmd_empty_trashed(since: str, paths: list[str]) -> dict:
    try:
        when = dt.datetime.fromisoformat(since)
    except ValueError:
        return {"error": "invalid time"}
    home = _home()
    return {"removed": empty_trashed(when, [p for p in paths if p.startswith(home + "/")])}


def cmd_cleanup_plan() -> dict:
    out = {"orphans": [], "orphan_cache": [], "runtimes": [], "approximate": True}
    rc, so, _ = run(["pacman", "-Qdtq"])
    out["orphans"] = so.split() if rc == 0 else []
    out["orphan_cache"] = _cache_files(set(out["orphans"]))
    if shutil.which("flatpak"):
        runtimes = parse_flatpak_runtimes(run(["flatpak", "list", "--runtime", "--columns=ref,installation"])[1])
        apps_rt = run(["flatpak", "list", "--app", "--columns=application,runtime,installation"])[1]
        used = parse_app_runtimes(apps_rt)
        for line in apps_rt.splitlines():
            appid = line.split("\t")[0]
            if appid:
                used |= parse_extensions(run(["flatpak", "info", "--show-extensions", "--", appid])[1])
        for ref in list(used):
            used |= parse_extensions(run(["flatpak", "info", "--show-extensions", "--", ref])[1])
        out["runtimes"] = unused_runtimes(runtimes, used)
    return out


def cleanup_steps(ids: list[str]):
    root = [i for i in ids if (i.startswith("orphan:") and _PKG.match(i[7:]))
            or (i.startswith("runtime:") and re.match(r"^runtime/[A-Za-z0-9_][A-Za-z0-9._-]*/[A-Za-z0-9_]+/[A-Za-z0-9._-]+$", i[8:]))]
    steps = []
    if root:
        steps.append(("root", lambda w: run_command(["pkexec", HELPER, "cleanup", *root], w)))
    if "user-runtimes" in ids:
        steps.append(("user", lambda w: run_command(["flatpak", "uninstall", "--user", "--unused", "--noninteractive", "-y"], w)))
    return steps


def remove_steps(ids: list[str]):
    items = [parse_item(i) for i in ids]
    root = [f"pkg:{p[1]}" if p[0] == "pkg" else f"flatpak:{p[1]}" if p[0] == "flatpak-system" else f"snap:{p[1]}"
            for p in items if p and p[0] in ("pkg", "flatpak-system", "snap")]
    user = [i for i, p in zip(ids, items) if p and p[0] in ("flatpak-user", "appimage", "dev")]
    steps = []
    if root:
        steps.append(("root", lambda w: run_command(["pkexec", HELPER, "remove", *root], w)))
    if user:
        steps.append(("user", lambda w: remove_user(user, w)))
    return steps


def cmd_run(stage: str, ids: list[str]) -> dict:
    if stage not in ("remove", "cleanup"):
        return {"error": "unknown stage"}
    steps = remove_steps(ids) if stage == "remove" else cleanup_steps(ids)
    return run_queue(run_dir(), stage, ids, steps)


def cmd_cancel() -> dict:
    open(os.path.join(run_dir(), "cancel"), "a").close()
    return {"ok": True}


def cmd_export(text: str) -> dict:
    rc, d, _ = run(["xdg-user-dir", "DOWNLOAD"], timeout=5)
    d = d.strip() if rc == 0 and d.strip() else os.path.join(_home(), "Downloads")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"full-remove-scan-{dt.datetime.now():%Y%m%d-%H%M%S}.txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return {"path": path}


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if not argv:
        print(json.dumps({"error": "no command"}))
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "status":
            res = cmd_status()
        elif cmd == "list-apps":
            res = cmd_list_apps()
        elif cmd == "list-dev":
            res = cmd_list_dev()
        elif cmd == "plan":
            res = cmd_plan(args)
        elif cmd == "installed":
            res = cmd_installed(args)
        elif cmd == "trash":
            res = cmd_trash(args)
        elif cmd == "delete":
            res = cmd_delete(args)
        elif cmd == "empty-trashed" and args:
            res = cmd_empty_trashed(args[0], args[1:])
        elif cmd == "cleanup-plan":
            res = cmd_cleanup_plan()
        elif cmd == "run" and args:
            res = cmd_run(args[0], args[1:])
        elif cmd == "cancel":
            res = cmd_cancel()
        elif cmd == "export" and len(args) == 1:
            res = cmd_export(args[0])
        else:
            res = {"error": f"unknown command: {cmd}"}
    except Exception as e:  # always answer the panel with JSON
        res = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(res))
    return 1 if "error" in res else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
