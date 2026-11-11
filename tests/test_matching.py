import pytest


# --- length tiers -----------------------------------------------------------

@pytest.mark.parametrize(
    "name,kw",
    [
        ("obsidian", "obsidian"),            # equal
        ("Obsidian", "obsidian"),            # case
        ("obsidian-sync", "obsidian"),       # whole word
        ("obsidianmd", "obsidian"),          # 6+ prefix
        ("visual-studio-code", "Visual Studio Code"),
        ("Code", "code"),                    # 4-5 equal
        ("code-oss", "code"),                # 4-5 whole word
        ("mpv", "mpv"),                      # <4 exact
        (".mpv", "mpv"),                     # <4 exact, one leading dot ignored
        ("vlc", "VLC"),
        ("obsidian.desktop", "obsidian"),    # .desktop suffix ignored
    ],
)
def test_matches(fr, name, kw):
    assert fr.matches(name, kw)


@pytest.mark.parametrize(
    "name,kw",
    [
        (".codex", "code"),          # 4-5 chars: no prefix
        (".codeium", "code"),
        ("mpv-shim", "mpv"),         # <4: exact only
        ("mpvx", "mpv"),
        ("..mpv", "mpv"),            # only one leading dot
        ("xobsidian", "obsidian"),
        ("thunderbird", "thunder"),  # 7 chars prefix allowed? -> yes, see next test
    ][:-1],
)
def test_no_match(fr, name, kw):
    assert not fr.matches(name, kw)


def test_long_keyword_prefix_matches(fr):
    assert fr.matches("thunderbird", "thunder")


def test_empty_keyword_never_matches(fr):
    assert not fr.matches("anything", "")
    assert not fr.matches("anything", "---")


# --- keyword tiers ----------------------------------------------------------

def test_generic_words_move_to_maybe(fr):
    strong, maybe = fr.tier_keywords({"telegram", "desktop", "client"}, set())
    assert strong == {"telegram"}
    assert {"desktop", "client"} <= maybe


def test_runtime_window_classes_dropped(fr):
    strong, maybe = fr.tier_keywords({"Electron", "java-app", "wine", "signal"}, set())
    assert strong == {"signal"}
    assert not ({"Electron", "java-app", "wine"} & maybe)


def test_strong_keyword_not_duplicated_in_maybe(fr):
    strong, maybe = fr.tier_keywords({"obsidian"}, {"obsidian", "md.obsidian"})
    assert "obsidian" not in maybe


# --- classify with demotion -------------------------------------------------

def test_classify_strong(fr):
    tier, reason = fr.classify("obsidian", {"obsidian"}, set(), [])
    assert tier == "strong" and "obsidian" in reason


def test_classify_maybe_keyword(fr):
    tier, reason = fr.classify(".vscode", {"code"}, {"vscode"}, [])
    assert tier == "maybe" and "vscode" in reason


def test_classify_demoted_when_other_app_matches(fr):
    others = [("claude", "~/.local/bin/claude")]
    tier, reason = fr.classify("claude", {"claude"}, set(), others)
    assert tier == "maybe"
    assert "also matches" in reason and "~/.local/bin/claude" in reason


def test_classify_no_match(fr):
    assert fr.classify("pipewire", {"obsidian"}, {"md"}, []) is None


# --- keyword helpers --------------------------------------------------------

@pytest.mark.parametrize(
    "pkg,base",
    [("visual-studio-code-bin", "visual-studio-code"), ("zen-browser-bin", "zen-browser"),
     ("foo-git", "foo"), ("sl", "sl"), ("obsidian", "obsidian")],
)
def test_strip_pkg_suffix(fr, pkg, base):
    assert fr.strip_pkg_suffix(pkg) == base


@pytest.mark.parametrize(
    "exec_line,binary",
    [
        ("/usr/bin/code %F", "code"),
        ("env GDK_BACKEND=x11 /opt/app/app --flag %U", "app"),
        ("electron35 /usr/lib/signal/app.asar %U", None),
        ("flatpak run org.x.Y", None),
        ('sh -c "foo --bar"', None),
        ("uwsm app -- obsidian %u", "obsidian"),
        ("", None),
    ],
)
def test_exec_binary_skips_wrappers(fr, exec_line, binary):
    assert fr.exec_binary(exec_line) == binary
