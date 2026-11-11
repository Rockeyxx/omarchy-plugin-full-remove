# Full Remove

Uninstall apps on Omarchy and clean up what they leave behind, in one panel.

Full Remove handles pacman and AUR packages, Flatpaks (system and user), Snaps,
AppImages, and globally installed dev tools (npm, bun, uv, pipx, cargo, go,
gem). After the uninstall it finds the app's leftover folders in your home,
moves the ones you keep ticked to the Trash, then offers to remove orphaned
packages and unused Flatpak runtimes.

![Full Remove](preview.png)

## How it differs from Omarchy's Remove > Package

Omarchy's own entry runs `pacman -Rns` in a terminal. That removes the package
and its unneeded dependencies, and nothing else. Full Remove also:

- removes Flatpaks, Snaps, AppImages and dev tools from the same list
- finds leftovers in `~/.config`, `~/.local/share`, `~/.local/state`, `~/.cache`,
  `~/.var/app`, `~/snap`, dot-folders in `~`, the yay/paru build cache, and the
  package's files in `/var/cache/pacman/pkg`
- moves leftovers to the Trash instead of deleting them
- shows everything before it runs, including the exact command that runs as root
- has a scan-only mode that needs no password and changes nothing

## Requirements

- Omarchy 4.x (Quattro shell)
- `python3`, `gio` (glib2) and `polkit`, all present on a normal Omarchy install
- Optional: `flatpak`, `snapd`, `xdg-user-dirs`, and npm / bun / uv / pipx /
  cargo / go / ruby+gem for the Dev tools tab. Anything missing is skipped.

The plugin makes no network calls and downloads nothing.

## Install

This plugin needs a manual step: a small root helper has to be installed
root-owned (see [Security](#security) for why). Without it, scan-only mode,
user Flatpaks, AppImages and dev tools still work.

1. Install the plugin:

   ```bash
   omarchy plugin add https://github.com/Rockeyxx/omarchy-plugin-full-remove --enable
   ```

2. Install the root helper. Read `root/` first; it is two short files.
   Build it from a fresh clone:

   ```bash
   git clone https://github.com/Rockeyxx/omarchy-plugin-full-remove /tmp/full-remove
   cd /tmp/full-remove/root
   less full-remove-helper io.github.rockeyxx.full-remove.policy
   makepkg -si
   ```

   This installs the `full-remove-helper` package: the helper at
   `/usr/lib/full-remove/full-remove-helper` and the polkit action
   `io.github.rockeyxx.full-remove.run`. Everything is built from the two local
   files; nothing is downloaded.

   If you prefer not to install a package, do it by hand:

   ```bash
   sudo install -Dm755 -o root -g root full-remove-helper /usr/lib/full-remove/full-remove-helper
   sudo install -Dm644 -o root -g root io.github.rockeyxx.full-remove.policy \
     /usr/share/polkit-1/actions/io.github.rockeyxx.full-remove.policy
   ```

3. Add it to the Omarchy menu under Remove. In
   `~/.config/omarchy/extensions/omarchy-menu.jsonc`:

   ```jsonc
   "remove.full-remove": {
     "icon": "󰩺",
     "label": "Full remove",
     "action": "omarchy-shell shell summon io.github.rockeyxx.full-remove '{}'"
   }
   ```

4. Optional keybind, in `~/.config/hypr/bindings.lua` (pick a free key):

   ```lua
   o.bind("SUPER + ALT + DELETE", "Full Remove", "omarchy-shell shell summon io.github.rockeyxx.full-remove '{}'")
   ```

## Usage

1. **Choose apps.** Search and tick what to remove, then press Continue. Turn
   on *Scan only (no password)* to only look. There are five tabs:
   - **Apps:** what you installed (pacman, AUR, Flatpak, Snap, AppImage).
   - **Omarchy:** packages Omarchy's installer put there, kept apart so they
     are not mixed with yours.
   - **Dev tools:** language runtimes and toolchains (python and its
     libraries, nodejs, go, rust, JDK, dotnet, clang, gdb, ...) plus global
     npm, bun, uv, pipx, cargo, go and gem installs.
   - **System:** kernels, firmware, drivers, bootloader, audio/network stack,
     and everything the system or Omarchy depends on. View only.
   - **Orphans:** orphaned packages and unused Flatpak runtimes that already
     exist, to clean up whenever you like.

   Keyboard: ↑/↓ move, Space tick, Enter continue, Tab / Shift+Tab switch tabs,
   `/` or Ctrl+F search, Esc leave search, then close.
2. **Review.** You see what pacman will also remove, the cache files that will
   be deleted, and every command, split into "runs as root" and "runs as you".
   If removing a package would break another installed package, Confirm stays
   disabled and the panel names the package.
3. **Removing.** Press Confirm. Root work asks for your password; the log
   streams into the panel. If you close the panel, the step that is running
   finishes and the rest is cancelled.
4. **Leftovers.** Folders that clearly belong to the app are ticked. Weaker
   matches are marked *maybe* and unticked, with the reason shown. Untick
   anything you want to keep, then *Move to Trash*. *Delete permanently…* skips
   the Trash, after its own confirmation.
5. **Cleanup.** Only orphans and unused runtimes that *this removal* created
   are offered (orphans ticked, runtimes unticked because that list is an
   estimate; flatpak refuses to remove a runtime an app still needs). If the
   removal left none behind, this step is skipped. Older orphans are on the
   Orphans tab.
6. **Done.** *Empty this run's items from Trash…* permanently deletes only what
   this run moved there.

In scan-only mode you get the full report instead, with *Copy* and *Export*
(a text file in your Downloads folder).

Logs of this boot's runs are in `/run/user/<uid>/full-remove/`. pacman also
records every removal in `/var/log/pacman.log`.

## Uninstall

```bash
omarchy plugin remove io.github.rockeyxx.full-remove
sudo pacman -R full-remove-helper
```

If you installed the helper by hand:

```bash
sudo rm /usr/lib/full-remove/full-remove-helper \
  /usr/share/polkit-1/actions/io.github.rockeyxx.full-remove.policy
sudo rmdir /usr/lib/full-remove
```

Then remove the `remove.full-remove` entry from `omarchy-menu.jsonc`, and the
keybind if you added one.

## Updating

Plugins run unsandboxed with your full user rights, so read what changed before
you update:

```bash
cd ~/.config/omarchy/plugins/io.github.rockeyxx.full-remove
git fetch && git log -p HEAD..origin/main
omarchy plugin update io.github.rockeyxx.full-remove
```

If the update touches `root/`, the panel will tell you the helper is outdated.
Read the new `root/` files, then reinstall the helper from a fresh clone as in
step 2 of Install.

## Security

Read this before installing. The short version: the plugin can only ever do
"remove these named packages" as root, it asks for your password every time,
and it moves files to the Trash instead of deleting them.

**Plugins are not sandboxed.** An Omarchy plugin runs inside the shell with
everything your user account can do. Read the code before installing or
updating any plugin, this one included. The part that matters most is
`root/full-remove-helper`, about 200 lines of bash.

**Why the root helper is installed separately.** The plugin folder is owned and
writable by your user. If the code that runs as root lived there, anything that
manages to run as your user (a bad AUR package, an exploited app, another
plugin) could replace it and wait for your next password prompt. So the only
root code lives in `/usr/lib/full-remove/`, owned by root, and the polkit action
is pinned to that exact path. The panel refuses to use a helper that is not
root-owned.

**Your password is asked every time.** The polkit action uses `auth_admin`, not
`auth_admin_keep`, and there is no sudoers rule. The prompt shows the exact
command it will run, for example
`Full Remove: /usr/lib/full-remove/full-remove-helper remove pkg:sl`. Only type
your password if you just pressed Confirm and the command matches the panel.

**What the helper accepts.** Nothing but these items, all checked before
anything happens:

- `pkg:<name>`: the name must match `^[a-z0-9][a-z0-9@._+-]*$` and be an
  installed package under exactly that name. It is passed to pacman after
  `--`, so it can never become an option.
- `flatpak:<app-id>`: an installed system Flatpak app.
- `snap:<name>`: an installed snap (never `snapd`, `core*` or `bare`).
- `orphan:<name>`: a package that is still an orphan when the helper runs.
- `runtime:runtime/<id>/<arch>/<branch>`: an installed system Flatpak runtime.

It finds cache files itself, only directly inside `/var/cache/pacman/pkg`,
never following symlinks, and only files whose package name exactly equals a
removed package. It takes no paths and no commands, uses no temp files, and
reads no environment variables.

**It refuses essential and security packages.** If the full removal set
contains the kernel, bootloader, glibc, systemd, pacman, sudo, polkit, PAM,
firewall/AppArmor/audit tools, any Omarchy package or anything Omarchy depends
on, the helper refuses. It also refuses whenever pacman cannot work out the
removal set.

**Leftovers go to the Trash.** Home folder cleanup never runs as root and
never follows symlinks: a symlinked folder is moved as a link, its target is
left alone. Only direct children of the cleanup folders listed above can be
moved; your Documents, Pictures and similar folders cannot. A failed trash
leaves the item in place; it never falls back to deleting.

**Scan-only mode** never asks for a password and changes nothing. It runs the
same detection and shows what would be removed.

**Known limits.** No remover can fix these:

- A package's own pre/post-remove script runs as root during the uninstall.
  It already had root when it was installed.
- Code that already runs as your user could draw a fake password prompt, or
  read input inside the shell (Omarchy's polkit agent runs in the same shell
  process as every plugin). Local polkit rules, such as Arch's `empower` group,
  can turn a password prompt into "yes".
- Flatpak's own polkit rule lets wheel users remove system Flatpaks with no
  password. Routing them through this helper adds a prompt but no protection.
- Installing the helper is a trust step, like any AUR package: you build it
  from files in a folder your user can write. Build from a fresh clone you have
  read.
- Malware that already has root is out of scope.

## Development

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests/
shellcheck root/full-remove-helper
qmllint -I /usr/share/omarchy/shell Panel.qml ItemRow.qml
omarchy plugin validate .
```

After editing QML in an installed copy, run `omarchy-restart-shell`. Hot reload
can keep serving the old panel code.

## License

MIT, see [LICENSE](LICENSE).
