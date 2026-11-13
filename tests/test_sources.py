import json
import struct

import pytest


def test_parse_pacman_qi(fr, fixture_text):
    pkgs = {p["name"]: p for p in fr.parse_pacman_qi(fixture_text("pacman_qi.txt"))}
    assert pkgs["bash"]["description"] == "The GNU Bourne Again shell"
    assert pkgs["gum"]["size"] > 1_000_000
    assert pkgs["bash"]["version"].startswith("5.")


@pytest.mark.parametrize("text,n", [("27.18 KiB", 27832), ("1.4 MB", 1_400_000), ("2.00 GiB", 2 * 1024**3),
                                    ("512 B", 512), ("0 B", 0), ("", 0), ("junk", 0)])
def test_parse_size(fr, text, n):
    assert fr.parse_size(text) == n


def test_parse_pacman_ql(fr, fixture_text):
    files = fr.parse_pacman_ql(fixture_text("pacman_ql.txt"))
    assert "/usr/bin/gum" in files["gum"]


def test_parse_pkgbase(fr):
    assert fr.parse_desc_base("%NAME%\nfoo-bin\n\n%BASE%\nfoo\n\n%DESC%\nx\n") == "foo"
    assert fr.parse_desc_base("%NAME%\nfoo\n") is None


def test_parse_flatpak_apps(fr, fixture_text):
    apps = fr.parse_flatpak_apps(fixture_text("flatpak_apps.txt"))
    steam = next(a for a in apps if a["id"] == "flatpak-system:com.valvesoftware.Steam")
    assert steam["name"] == "Steam" and steam["size"] == 53_900_000 and steam["source"] == "flatpak"


def test_unused_runtimes(fr, fixture_text):
    runtimes = fr.parse_flatpak_runtimes(fixture_text("flatpak_runtimes.txt"))
    used = fr.parse_app_runtimes(fixture_text("flatpak_app_runtimes.txt"))
    used |= fr.parse_extensions(fixture_text("flatpak_extensions.txt"))
    unused = fr.unused_runtimes(runtimes, used)
    refs = {r["ref"] for r in unused}
    assert "runtime/org.freedesktop.Platform/x86_64/25.08" in refs
    assert "runtime/org.freedesktop.Platform/x86_64/26.08" not in refs
    assert "runtime/org.freedesktop.Platform.Compat.i386/x86_64/26.08" not in refs  # app extension


def test_parse_snap_list(fr):
    text = "Name    Version   Rev    Tracking       Publisher   Notes\nhello-world  6.4  29  latest/stable  canonical✓  -\ncore22  2024  1663  latest/stable  canonical✓  base\n"
    snaps = fr.parse_snap_list(text)
    assert [(s["name"], s["rev"]) for s in snaps] == [("hello-world", "29"), ("core22", "1663")]
    assert fr.parse_snap_list("No snaps are installed yet.") == []


def test_parse_desktop(fr, fixture_text):
    d = fr.parse_desktop(fixture_text("code.desktop"))
    assert d == {"Name": "Visual Studio Code", "Exec": "/usr/bin/code %F", "Icon": "vscode",
                 "StartupWMClass": "Code", "Comment": "Code Editing. Redefined.", "Type": "Application"}


def test_parse_desktop_keeps_escaped_newline_as_text(fr, fixture_text):
    d = fr.parse_desktop(fixture_text("joplin.desktop"))
    assert d["Name"] == "Evil\nName"
    # survives the JSON boundary intact
    assert json.loads(json.dumps(d))["Name"] == "Evil\nName"


def test_appimage_from_exec(fr):
    assert fr.appimage_from_exec('"/home/user/Applications/Joplin-3.1.24.AppImage" %u') == "/home/user/Applications/Joplin-3.1.24.AppImage"
    assert fr.appimage_from_exec("/opt/x/app.appimage --no-sandbox") == "/opt/x/app.appimage"
    assert fr.appimage_from_exec("/usr/bin/code %F") is None


@pytest.mark.parametrize("fn,name", [("Joplin-3.1.24.AppImage", "Joplin"), ("lichii-linux-x86_64-pgo.AppImage", "lichii-linux"),
                                     ("Obsidian_v1.7.7_amd64.AppImage", "Obsidian"), ("cool app.AppImage", "cool app"),
                                     ("Krita-5.2.6-x86_64.appimage", "Krita")])
def test_appimage_name(fr, fn, name):
    assert fr.appimage_name(fn) == name


def test_appimage_icon_owned(fr):
    ai = "/home/user/Applications/Joplin-3.1.24.AppImage"
    home = "/home/user"
    assert fr.appimage_icon_owned("/home/user/.local/share/icons/appimagekit_abc123_joplin.png", ai, "Joplin", home)
    assert fr.appimage_icon_owned("/home/user/Applications/joplin.png", ai, "Joplin", home)
    assert not fr.appimage_icon_owned("joplin", ai, "Joplin", home)                         # theme name
    assert not fr.appimage_icon_owned("/home/user/Pictures/joplin.png", ai, "Joplin", home)  # user file
    assert not fr.appimage_icon_owned("/home/user/.local/share/icons/other.png", ai, "Joplin", home)


def test_parse_gearlever_keyfile(fr, fixture_text):
    assert fr.parse_gearlever_keyfile(fixture_text("gearlever_keyfile.txt")) == "/home/user/Desktop/AppImage"
    assert fr.parse_gearlever_keyfile("[x]\n") is None


def test_parse_asar_productname(fr):
    header = json.dumps({"files": {"package.json": {"size": 30, "offset": "0"}}}).encode()
    body = json.dumps({"productName": "Signal"}).encode()
    header_padded = header + b" " * ((4 - len(header) % 4) % 4)
    # asar: pickle(uint32 size=4, uint32 header_pickle_size) + pickle(uint32 payload, uint32 strlen, str)
    pickle2 = struct.pack("<II", len(header_padded) + 4, len(header)) + header_padded
    blob = struct.pack("<II", 4, len(pickle2)) + pickle2
    files = json.loads(header)
    files["files"]["package.json"]["size"] = len(body)
    header = json.dumps(files).encode()
    header_padded = header + b" " * ((4 - len(header) % 4) % 4)
    pickle2 = struct.pack("<II", len(header_padded) + 4, len(header)) + header_padded
    blob = struct.pack("<II", 4, len(pickle2)) + pickle2 + body
    assert fr.asar_product_name(blob) == "Signal"
    assert fr.asar_product_name(b"garbage") is None


def test_parse_npm(fr, fixture_text):
    assert fr.parse_npm_ls(fixture_text("npm.json")) == ["agent-desktop"]


def test_parse_bun(fr, fixture_text):
    assert fr.parse_bun_ls(fixture_text("bun.txt")) == ["@anthropic-ai/sdk", "cowsay"]


def test_parse_uv(fr, fixture_text):
    assert fr.parse_uv_tools(fixture_text("uv.txt")) == ["aider-chat", "ruff"]


def test_parse_pipx(fr, fixture_text):
    assert fr.parse_pipx(fixture_text("pipx.json")) == ["black", "httpie"]
    assert fr.parse_pipx("nothing has been installed\n{\"venvs\": {}}") == []


def test_parse_cargo(fr, fixture_text):
    assert fr.parse_cargo_list(fixture_text("cargo.txt")) == ["bat", "ripgrep"]


def test_gem_names_from_specs(fr):
    assert fr.gem_names(["rails-7.1.0.gemspec", "nokogiri-1.16.0-x86_64-linux.gemspec", "a-b-1.0.gemspec"]) == ["a-b", "nokogiri", "rails"]


@pytest.mark.parametrize("tool,name,argv", [
    ("npm", "@scope/pkg", ["npm", "uninstall", "-g", "@scope/pkg"]),
    ("bun", "cowsay", ["bun", "remove", "-g", "cowsay"]),
    ("uv", "ruff", ["uv", "tool", "uninstall", "ruff"]),
    ("pipx", "black", ["pipx", "uninstall", "black"]),
    ("cargo", "bat", ["cargo", "uninstall", "bat"]),
    ("gem", "rails", ["gem", "uninstall", "--user-install", "-a", "-x", "rails"]),
])
def test_dev_argv(fr, tool, name, argv):
    assert fr.dev_argv(tool, name) == argv


@pytest.mark.parametrize("tool,name", [("npm", "--global"), ("uv", "-x"), ("pipx", "a b"), ("cargo", ""),
                                       ("mise", "node"), ("npm", "../x")])
def test_dev_argv_rejects(fr, tool, name):
    assert fr.dev_argv(tool, name) is None


@pytest.mark.parametrize("item,ok", [("pkg:sl", True), ("pkg:--root", False), ("flatpak-system:org.x.Y", True),
                                     ("flatpak-user:org.x", False), ("snap:hello-world", True), ("snap:core22", False),
                                     ("dev:npm:cowsay", True), ("dev:mise:node", False), ("appimage:/x/a.AppImage", True),
                                     ("appimage:rel.AppImage", False), ("bogus:x", False), ("pkg", False)])
def test_parse_item_id(fr, item, ok):
    assert (fr.parse_item(item) is not None) == ok


def test_cache_rule_matches_helper(fr, tmp_path):
    names = ["sl-5.05-1-x86_64.pkg.tar.zst", "sl-5.05-1-x86_64.pkg.tar.zst.sig", "sl-extra-1.0-1-any.pkg.tar.zst",
             "lib32-sl-1:2.0-3-x86_64.pkg.tar.xz", "notes.txt", "x.pkg.tar.zst"]
    assert [fr.cache_pkg_name(n) for n in names] == ["sl", "sl", "sl-extra", "lib32-sl", None, None]


def test_parse_breaks(fr):
    err = ("error: failed to prepare transaction (could not satisfy dependencies)\n"
           ":: removing gum breaks dependency 'gum' required by omarchy\n"
           ":: removing gum breaks dependency 'gum' required by omarchy-settings\n")
    assert fr.parse_breaks(err) == [("gum", "omarchy"), ("gum", "omarchy-settings")]


def test_removal_result_reads_breaks_from_stdout(fr):
    # pacman prints the "breaks dependency" lines on stdout, the error on stderr
    so = ":: removing mpv breaks dependency 'mpv' required by mpv-mpris\n:: removing mpv breaks dependency 'mpv' required by owe\n"
    se = "error: failed to prepare transaction (could not satisfy dependencies)\n"
    r = fr.removal_result(1, so, se, ["mpv"])
    assert r["blocked"] is True
    assert r["breaks"] == [{"package": "mpv", "required_by": "mpv-mpris"}, {"package": "mpv", "required_by": "owe"}]


def test_removal_result_ok(fr):
    r = fr.removal_result(0, "discord\nlibfoo\n", "", ["discord"])
    assert r == {"removes": ["discord", "libfoo"], "breaks": [], "blocked": False}


def test_own_launcher_by_binary(fr):
    d = {"Name": "Media Player", "Exec": "mpv --player-operation-mode=pseudo-gui -- %U"}
    assert fr.launcher_is_own(d, "mpv-custom", own_stems=set(), own_bins={"mpv"}, own_flatpaks=set())


def test_own_launcher_by_stem(fr):
    assert fr.launcher_is_own({}, "com.valvesoftware.Steam", own_stems={"com.valvesoftware.Steam"}, own_bins=set(), own_flatpaks=set())


def test_own_launcher_by_flatpak_run(fr):
    d = {"Exec": "/usr/bin/flatpak run --branch=stable com.valvesoftware.Steam"}
    assert fr.launcher_is_own(d, "x", own_stems=set(), own_bins=set(), own_flatpaks={"com.valvesoftware.Steam"})


def test_other_launcher_is_not_own(fr):
    d = {"Exec": "celluloid %U"}
    assert not fr.launcher_is_own(d, "celluloid", own_stems={"mpv"}, own_bins={"mpv"}, own_flatpaks=set())


def test_parse_omarchy_lists(fr):
    text = "# Core\nhyprland\nwaybar  # bar\n\n  gum\n"
    assert fr.parse_package_list(text) == {"hyprland", "waybar", "gum"}


@pytest.mark.parametrize("name,deps,prot", [
    ("glibc", set(), True), ("linux", set(), True), ("linux-headers", set(), False), ("linux-wallpaperengine-git", set(), False),
    ("limine", set(), True), ("ufw", set(), True), ("omarchy-settings", set(), True), ("gum", {"gum"}, True),
    ("sl", set(), False), ("discord", {"gum"}, False), ("pam", set(), True), ("amd-ucode", set(), True),
])
def test_is_protected(fr, name, deps, prot):
    assert fr.is_protected(name, deps) is prot


def test_is_protected_kernels(fr):
    assert fr.is_protected("linux-cachyos", set(), {"linux-cachyos"}) is True
    assert fr.is_protected("linux-wallpaperengine-git", set(), {"linux-omarchy"}) is False


@pytest.mark.parametrize("name,omarchy,prot,group", [
    ("linux", False, True, "system"),            # protected always -> system
    ("gum", True, True, "system"),               # protected beats the Omarchy list
    ("linux-firmware-nvidia", False, False, "system"),
    ("mesa", False, False, "system"), ("vulkan-radeon", False, False, "system"), ("lib32-vulkan-radeon", False, False, "system"),
    ("nvidia-open-dkms", False, False, "system"), ("acpi_call-dkms", False, False, "system"), ("sof-firmware", False, False, "system"),
    ("xf86-video-amdgpu", False, False, "system"), ("linux-headers", False, False, "system"), ("base-devel", False, False, "system"),
    ("waybar", True, False, "omarchy"),
    ("python", False, False, "dev"), ("python-flet", False, False, "dev"), ("nodejs", False, False, "dev"), ("go", False, False, "dev"),
    ("rustup", False, False, "dev"), ("jdk8-openjdk", False, False, "dev"), ("dotnet-runtime-9.0", False, False, "dev"),
    ("clang", False, False, "dev"), ("llvm", False, False, "dev"), ("gdb", False, False, "dev"), ("cmake", False, False, "dev"),
    ("cuda", False, False, "dev"), ("java-atk-wrapper-openjdk17", False, False, "dev"),
    ("discord", False, False, "user"), ("libreoffice-still", False, False, "user"), ("nmap", False, False, "user"),
    ("linux-wallpaperengine-git", False, False, "user"), ("google-drive-ocamlfuse", False, False, "user"),
])
def test_package_group(fr, name, omarchy, prot, group):
    assert fr.package_group(name, omarchy, prot) == group


@pytest.mark.parametrize("name,omarchy,prot,group", [
    ("acpid", False, False, "services"), ("earlyoom", False, False, "services"),
    ("systemd", False, True, "system"),          # protected beats Services
    ("bluez", False, False, "system"),           # system pattern beats Services
    ("hypridle", True, False, "omarchy"),        # Omarchy list beats Services
    ("python", False, False, "dev"),             # toolchains stay in Dev
])
def test_package_group_service(fr, name, omarchy, prot, group):
    assert fr.package_group(name, omarchy, prot, service=True) == group


@pytest.mark.parametrize("files,expected", [
    (["/usr/bin/acpid", "/usr/lib/systemd/system/acpid.service", "/usr/lib/systemd/system/acpid.socket"], True),
    (["/usr/bin/atd", "/usr/lib/systemd/system/atd.service"], True),
    (["/usr/lib/systemd/system/getty@.service"], True),
    (["/usr/lib/systemd/system/fstrim.timer"], True),
    (["/usr/bin/waydroid", "/usr/lib/systemd/system/waydroid-container.service",
      "/usr/share/applications/Waydroid.desktop"], False),       # has a launcher: an app
    (["/usr/bin/foo", "/usr/lib/systemd/user/foo.service"], False),  # user unit only
    (["/usr/lib/systemd/system/"], False),
    (["/usr/bin/discord"], False),
])
def test_is_service_package(fr, files, expected):
    assert fr.is_service_package(files) is expected
