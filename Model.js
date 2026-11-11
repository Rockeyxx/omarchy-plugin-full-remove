.pragma library

// Pure helpers for Panel.qml. No side effects; tested under node.

function formatSize(bytes) {
  var n = Number(bytes) || 0
  if (n < 1024) return n + " B"
  var units = ["KB", "MB", "GB", "TB"]
  var i = -1
  do { n /= 1024; i++ } while (n >= 1024 && i < units.length - 1)
  return n.toFixed(1) + " " + units[i]
}

function sourceLabel(source) {
  return ({
    "pacman": "pacman", "aur": "AUR", "flatpak-system": "Flatpak", "flatpak-user": "Flatpak (user)",
    "snap": "Snap", "appimage": "AppImage", "orphan": "Orphan", "runtime": "Runtime"
  })[source] || String(source)
}

function filterItems(items, query) {
  var q = String(query || "").toLowerCase().trim()
  if (!q) return items
  return items.filter(function(it) {
    return [it.display, it.name, it.description, it.tool].some(function(v) {
      return v && String(v).toLowerCase().indexOf(q) !== -1
    })
  })
}

function backendArgv(pluginDir, cmd, args) {
  return ["python3", "-I", pluginDir + "/backend/fullremove.py", cmd].concat(args || [])
}

function shellQuote(arg) {
  var s = String(arg)
  if (/^[A-Za-z0-9@%+=:,.\/_-]+$/.test(s)) return s
  return "'" + s.replace(/'/g, "'\\''") + "'"
}

// For display only; nothing ever runs this string.
function displayCommand(argv) {
  return (argv || []).map(shellQuote).join(" ")
}

function localPath(url) {
  var s = String(url)
  if (s.indexOf("file://") === 0) s = s.substring(7)
  return decodeURIComponent(s)
}

function defaultTicks(leftovers) {
  var t = {}
  ;(leftovers || []).forEach(function(l) { if (l.tier === "strong") t[l.path] = true })
  return t
}

// Plain-text report of everything a run would do (scan-only Copy/Export).
function reportText(plan, cleanup) {
  var out = ["Full Remove scan (nothing was changed)", ""]
  if (plan) {
    out.push("Packages pacman would remove: " + ((plan.removes || []).join(" ") || "none"))
    ;(plan.breaks || []).forEach(function(b) {
      out.push("  BLOCKED: removing " + b.package + " breaks " + b.required_by)
    })
    if (plan.root_argv && plan.root_argv.length) out.push("Runs as root: " + displayCommand(plan.root_argv))
    ;(plan.cache || []).forEach(function(c) { out.push("  cache file: " + c) })
    ;(plan.user || []).forEach(function(u) { out.push("Runs as you: " + u.command) })
    out.push("", "Leftovers (strong = ticked by default, maybe = unticked):")
    ;(plan.leftovers || []).forEach(function(l) {
      out.push("  [" + l.tier + "] " + formatSize(l.size) + "  " + l.path + "  (" + l.reason + ")")
    })
    if (!(plan.leftovers || []).length) out.push("  none")
  }
  if (cleanup) {
    out.push("", "Orphan packages: " + ((cleanup.orphans || []).join(" ") || "none"))
    ;(cleanup.orphan_cache || []).forEach(function(c) { out.push("  cache file: " + c) })
    out.push("Unused Flatpak runtimes (approximate; flatpak decides the final list):")
    ;(cleanup.runtimes || []).forEach(function(r) { out.push("  " + r.ref + " (" + r.installation + ")") })
    if (!(cleanup.runtimes || []).length) out.push("  none")
  }
  return out.join("\n") + "\n"
}

// Package name of a pacman cache file, same rule as the helper.
function cachePkgName(file) {
  var base = String(file).replace(/^.*\//, "").replace(/\.sig$/, "")
  if (base.indexOf(".pkg.tar") === -1) return null
  var parts = base.split(".pkg.tar")[0].split("-")
  return parts.length >= 4 ? parts.slice(0, -3).join("-") : null
}

// Only what a removal created: orphans/runtimes in `after` but not `before`.
// Without a baseline, offer nothing (never every orphan on the system).
function cleanupDiff(before, after) {
  if (!before || !after) return { orphans: [], orphan_cache: [], runtimes: [] }
  var oldO = {}, oldR = {}
  ;(before.orphans || []).forEach(function(o) { oldO[o] = true })
  ;(before.runtimes || []).forEach(function(r) { oldR[r.installation + " " + r.ref] = true })
  var orphans = (after.orphans || []).filter(function(o) { return !oldO[o] })
  var keep = {}
  orphans.forEach(function(o) { keep[o] = true })
  return {
    orphans: orphans,
    orphan_cache: (after.orphan_cache || []).filter(function(c) { return keep[cachePkgName(c)] }),
    runtimes: (after.runtimes || []).filter(function(r) { return !oldR[r.installation + " " + r.ref] })
  }
}

function cleanupEmpty(c) {
  return !c || (!(c.orphans || []).length && !(c.runtimes || []).length)
}

// Rows for the Orphans tab (and the cleanup step).
function orphanItems(c) {
  var out = [], userRt = []
  if (!c) return out
  ;(c.orphans || []).forEach(function(o) {
    out.push({ id: "orphan:" + o, name: o, display: o, source: "orphan", description: "orphan package (root)", size: 0 })
  })
  ;(c.runtimes || []).forEach(function(r) {
    if (r.installation === "system")
      out.push({ id: "runtime:" + r.ref, name: r.ref, display: r.ref, source: "runtime", description: "unused system runtime (root, approximate)", size: 0 })
    else userRt.push(r.ref)
  })
  if (userRt.length)
    out.push({ id: "user-runtimes", name: "user-runtimes", display: "Unused user runtimes", source: "runtime",
               description: userRt.join(", ") + " (flatpak uninstall --user --unused, no password)", size: 0 })
  return out
}
