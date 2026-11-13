import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "Model.js" as Model

// Full Remove: uninstall apps from pacman/AUR, Flatpak, Snap, AppImage and
// dev tools, move their leftovers to the Trash, then offer orphan and unused
// runtime cleanup. Root work goes only through the root-owned helper via
// pkexec; everything else runs as the user. Removals run in a detached
// runner (backend `run`) so closing or reloading the panel cannot cut them off.
Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool opened: false

  readonly property string pluginDir: Model.localPath(Qt.resolvedUrl(".")).replace(/\/$/, "")
  readonly property string runDir: Quickshell.env("XDG_RUNTIME_DIR") + "/full-remove"
  readonly property var supportedProtocols: [1]

  // select | review | running | leftovers | cleanup | done | report
  property string step: "select"
  property bool scanOnly: false
  property string tab: "apps"
  property string query: ""
  property var apps: []
  property var devItems: []
  property var devMissing: []
  property var helper: ({})
  property bool loadingList: false
  property var selected: ({})
  property int selectedCount: 0
  property var plan: null
  property var cleanup: null
  property var cleanupBefore: null     // orphans/runtimes snapshot taken before a removal
  property var orphanAll: null         // everything for the Orphans tab
  property var leftTicks: ({})
  property var cleanupTicks: ({})
  property var runState: null
  property string runIds: ""
  property string logText: ""
  property string busy: ""
  property string error: ""
  property string notice: ""
  property string confirming: ""     // "" | "delete" | "empty" | "close"
  property var trashed: []
  property string trashSince: ""
  property bool partial: false

  readonly property bool helperOk: !!helper.supported
  // Tabs: Apps (what you installed), Omarchy, Dev tools, System (view-only), Orphans.
  readonly property var tabs: ["apps", "omarchy", "dev", "system", "orphans"]
  readonly property var userApps: apps.filter(function(a) { return !a.group || a.group === "user" })
  readonly property var omarchyApps: apps.filter(function(a) { return a.group === "omarchy" })
  readonly property var systemApps: apps.filter(function(a) { return a.group === "system" })
  readonly property var devAll: apps.filter(function(a) { return a.group === "dev" }).concat(devItems)
  readonly property var orphanList: Model.orphanItems(orphanAll)
  readonly property var currentItems: Model.filterItems(
    tab === "apps" ? userApps : tab === "omarchy" ? omarchyApps : tab === "dev" ? devAll
      : tab === "system" ? systemApps : orphanList, query)

  // ---------------------------------------------------------------- lifecycle

  function open(payloadJson) {
    opened = true
    error = ""
    notice = ""
    confirming = ""
    stateFile.reload()
    if (step === "done" || step === "report" || (step === "select" && apps.length === 0)) resetToSelect()
    Qt.callLater(function() { if (root.opened) keyCatcher.forceActiveFocus() })
  }

  function close() {
    opened = false
    confirming = ""
  }

  function dismiss() {
    if (runActive()) { confirming = "close"; return }
    hideNow()
  }

  function hideNow() {
    if (shell && typeof shell.hide === "function") shell.hide((manifest && manifest.id) || "io.github.rockeyxx.full-remove")
    else close()
  }

  function runActive() { return !!runState && !runState.finished }

  function resetToSelect() {
    step = "select"
    notice = ""
    error = ""
    selected = ({})
    selectedCount = 0
    plan = null
    cleanup = null
    leftTicks = ({})
    trashed = []
    trashSince = ""
    query = ""
    loadLists()
  }

  // ------------------------------------------------------------ backend calls

  property var queue: []
  property var pending: null

  function call(cmd, args, cb) {
    queue = queue.concat([{ cmd: cmd, args: args || [], cb: cb }])
    pump()
  }

  function pump() {
    if (backendProc.running || queue.length === 0) return
    pending = queue[0]
    queue = queue.slice(1)
    backendProc.command = Model.backendArgv(pluginDir, pending.cmd, pending.args)
    backendProc.running = true
  }

  Process {
    id: backendProc
    stdout: StdioCollector { id: backendOut; waitForEnd: true }
    onExited: function(exitCode) {
      var job = root.pending
      root.pending = null
      var res = null
      try { res = JSON.parse(String(backendOut.text || "")) } catch (e) { res = { error: "backend returned no data (exit " + exitCode + ")" } }
      if (job && job.cb) job.cb(res || {})
      Qt.callLater(root.pump)
    }
  }

  function loadLists() {
    loadingList = true
    call("status", [], function(r) { root.helper = r.helper || {} })
    call("list-apps", [], function(r) {
      if (r.error) root.error = r.error
      root.apps = (r.apps || []).filter(function(a) { return a.id !== "" || true })
    })
    call("list-dev", [], function(r) {
      root.devItems = (r.dev || []).map(function(d) {
        return { id: d.id, name: d.name, display: d.name, source: d.tool, description: d.command, tool: d.tool, size: 0 }
      })
      root.devMissing = r.missing || []
      root.loadingList = false
    })
    call("cleanup-plan", [], function(c) { root.orphanAll = c.error ? null : c })
  }

  function toggle(id) {
    if (!id) return
    var s = Object.assign({}, selected)
    if (s[id]) delete s[id]; else s[id] = true
    selected = s
    selectedCount = Object.keys(s).length
  }

  // ------------------------------------------------------------- keyboard

  function activeList() {
    return step === "select" ? selectList : step === "leftovers" ? leftList : step === "cleanup" ? cleanupList : null
  }

  function moveCursor(delta) {
    var l = activeList()
    if (!l || l.count === 0) return
    l.currentIndex = Math.max(0, Math.min(l.count - 1, (l.currentIndex < 0 ? (delta > 0 ? -1 : l.count) : l.currentIndex) + delta))
    l.positionViewAtIndex(l.currentIndex, ListView.Contain)
  }

  function toggleCurrent() {
    var l = activeList()
    if (l && l.currentItem && l.currentItem.showCheck) l.currentItem.toggled()
  }

  function setTab(t) {
    tab = t
    selectList.currentIndex = -1
  }

  function cycleTab(delta) {
    if (step !== "select") return
    var i = tabs.indexOf(tab)
    setTab(tabs[(i + delta + tabs.length) % tabs.length])
  }

  // Enter: the enabled main button of the current step.
  function primaryAction() {
    if (confirming !== "") return
    if (step === "select") {
      if (tab === "orphans") { if (orphanSelCount() > 0 && !scanOnly) reviewOrphans() }
      else if (appSelCount() > 0 && busy === "") makePlan()
    } else if (step === "review") {
      if (plan && !plan.blocked && (plan.root_argv.length === 0 || helperOk)) startRun("remove", selectedIds())
    } else if (step === "leftovers") trashLeftovers()
    else if (step === "cleanup") {
      if (cleanupIds().length > 0 && (cleanupRootArgv().length === 0 || helperOk)) startRun("cleanup", cleanupIds())
    } else if (step === "done") resetToSelect()
    else if (step === "report") step = "select"
  }

  function isCleanupId(id) { return /^(orphan:|runtime:|user-runtimes$)/.test(id) }
  function selectedIds() { return Object.keys(selected).filter(function(i) { return !root.isCleanupId(i) }) }
  function appSelCount() { return selectedIds().length }
  function orphanSel() { return Object.keys(selected).filter(function(i) { return root.isCleanupId(i) }) }
  function orphanSelCount() { return orphanSel().length }

  // Orphans tab: review the ticked items in the cleanup step (same password flow).
  function reviewOrphans() {
    notice = ""
    cleanup = orphanAll
    var t = {}
    orphanSel().forEach(function(i) { t[i] = true })
    cleanupTicks = t
    step = "cleanup"
  }

  function makePlan() {
    error = ""
    notice = ""
    busy = "Collecting what belongs to the selected apps…"
    call("plan", selectedIds(), function(r) {
      root.busy = ""
      if (r.error) { root.error = r.error; return }
      root.plan = r
      root.leftTicks = Model.defaultTicks(r.leftovers)
      if (root.scanOnly) {
        root.busy = "Checking orphans and unused runtimes…"
        root.call("cleanup-plan", [], function(c) { root.busy = ""; root.cleanup = c; root.step = "report" })
      } else {
        root.call("cleanup-plan", [], function(c) { root.cleanupBefore = c.error ? null : c })
        root.step = "review"
      }
    })
  }

  // --------------------------------------------------------- detached runner

  function startRun(stage, ids) {
    error = ""
    notice = ""
    logText = ""
    runIds = ids.join("\n")
    Quickshell.execDetached(Model.backendArgv(pluginDir, "run", [stage].concat(ids)))
    step = "running"
    stateFile.reload()
  }

  FileView {
    id: stateFile
    path: root.runDir + "/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.onState(text())
  }

  FileView {
    id: logFile
    path: root.runState && root.runState.log ? root.runState.log : ""
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.logText = text()
  }

  function onState(raw) {
    var st = null
    try { st = JSON.parse(raw) } catch (e) { return }
    var wasActive = runActive()
    runState = st
    if (!st.finished && (step === "running" || step === "select")) { step = "running"; return }
    if (st.finished && step === "running") afterRun(st)
    else if (st.finished && wasActive) afterRun(st)
  }

  function afterRun(st) {
    logFile.reload()
    if (st.stage === "cleanup") { step = "done"; return }
    trashSince = st.start || ""
    if (st.cancelled && st.steps.length === 0) { notice = st.reason; step = "done"; return }
    var ids = st.ids || []
    call("installed", ids, function(r) {
      var inst = r.installed || {}
      var still = ids.filter(function(i) { return inst[i] })
      ;(root.plan && root.plan.user ? root.plan.user : []).forEach(function(u) {
        if (u.files && !inst[u.id]) root.trashed = root.trashed.concat(u.files)
      })
      root.partial = still.length > 0
      if (st.cancelled) root.notice = st.reason
      if (still.length === ids.length) {
        root.notice = (root.notice ? root.notice + ". " : "") + "Nothing was removed, so no leftovers are offered."
        root.step = "done"
        return
      }
      if (root.partial) {
        root.leftTicks = ({})
        root.notice = "Some apps are still installed (" + still.join(", ") + "). Leftovers are unticked; check each one."
      }
      var gone = ids.filter(function(i) { return !inst[i] }).map(function(i) { return i.replace(/^[^:]*:/, "").replace(/^.*\//, "") })
      var hasLeft = root.plan && root.plan.leftovers && root.plan.leftovers.length
      root.notice = (root.notice ? root.notice + " " : "") + "Removed: " + gone.join(", ") + "." + (hasLeft ? "" : " No leftovers found.")
      root.step = hasLeft ? "leftovers" : "cleanupLoad"
      if (root.step === "cleanupLoad") root.loadCleanup()
    })
  }

  // ------------------------------------------------------- leftovers/cleanup

  function tickedLeftovers() {
    return (plan && plan.leftovers ? plan.leftovers : []).filter(function(l) { return leftTicks[l.path] }).map(function(l) { return l.path })
  }

  function toggleLeft(path) {
    var t = Object.assign({}, leftTicks)
    if (t[path]) delete t[path]; else t[path] = true
    leftTicks = t
  }

  function trashLeftovers() {
    var paths = tickedLeftovers()
    if (!paths.length) { loadCleanup(); return }
    busy = "Moving to Trash…"
    call("trash", paths, function(r) {
      root.busy = ""
      var ok = [], bad = []
      ;(r.results || []).forEach(function(x) { (x.ok ? ok : bad).push(x) })
      root.trashed = root.trashed.concat(ok.map(function(x) { return x.path }))
      if (bad.length) root.notice = "Not moved: " + bad.map(function(x) { return x.path + " (" + x.message + ")" }).join("; ")
      root.loadCleanup()
    })
  }

  function deleteLeftovers() {
    confirming = ""
    var paths = tickedLeftovers()
    busy = "Deleting permanently…"
    call("delete", paths, function(r) {
      root.busy = ""
      var bad = (r.results || []).filter(function(x) { return !x.ok })
      if (bad.length) root.notice = "Not deleted: " + bad.map(function(x) { return x.path + " (" + x.message + ")" }).join("; ")
      root.loadCleanup()
    })
  }

  function loadCleanup() {
    busy = "Checking orphans and unused runtimes…"
    call("cleanup-plan", [], function(c) {
      root.busy = ""
      // only what this removal created; skip the step when there is nothing
      root.cleanup = Model.cleanupDiff(root.cleanupBefore, c)
      var t = {}
      ;(root.cleanup.orphans || []).forEach(function(o) { t["orphan:" + o] = true })
      root.cleanupTicks = t
      root.step = Model.cleanupEmpty(root.cleanup) ? "done" : "cleanup"
    })
  }

  function toggleCleanup(id) {
    var t = Object.assign({}, cleanupTicks)
    if (t[id]) delete t[id]; else t[id] = true
    cleanupTicks = t
  }

  function cleanupIds() { return Object.keys(cleanupTicks) }

  function cleanupRootArgv() {
    var ids = cleanupIds().filter(function(i) { return i !== "user-runtimes" })
    return ids.length ? ["pkexec", "/usr/lib/full-remove/full-remove-helper", "cleanup"].concat(ids) : []
  }

  function emptyTrashed() {
    confirming = ""
    busy = "Emptying this run's items from the Trash…"
    call("empty-trashed", [trashSince].concat(trashed), function(r) {
      root.busy = ""
      root.notice = "Permanently deleted " + (r.removed || []).length + " item(s) from the Trash."
      root.trashed = []
    })
  }

  function openFolder(path) {
    var dir = path.replace(/\/[^\/]*$/, "") || "/"
    Quickshell.execDetached(["xdg-open", dir])
  }

  function copyReport() {
    Quickshell.execDetached(["wl-copy", "--", Model.reportText(plan, cleanup)])
    notice = "Copied to the clipboard."
  }

  function exportReport() {
    call("export", [Model.reportText(plan, cleanup)], function(r) {
      root.notice = r.error ? r.error : "Saved to " + r.path
    })
  }

  // ------------------------------------------------------------------ window

  OverlayWindow {
    shown: root.opened
    WlrLayershell.namespace: "full-remove"

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(0, 0, 0, 0.7)
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.onPressed: function(event) {
        var k = event.key
        if (k === Qt.Key_Escape) {
          if (root.confirming !== "") root.confirming = ""
          else root.dismiss()
        } else if (k === Qt.Key_Down) root.moveCursor(1)
        else if (k === Qt.Key_Up) root.moveCursor(-1)
        else if (k === Qt.Key_PageDown) root.moveCursor(10)
        else if (k === Qt.Key_PageUp) root.moveCursor(-10)
        else if (k === Qt.Key_Space) root.toggleCurrent()
        else if (k === Qt.Key_Return || k === Qt.Key_Enter) root.primaryAction()
        else if (k === Qt.Key_Tab) root.cycleTab(1)
        else if (k === Qt.Key_Backtab) root.cycleTab(-1)
        else if ((k === Qt.Key_Slash || (k === Qt.Key_F && (event.modifiers & Qt.ControlModifier))) && root.step === "select") search.forceActiveFocus()
        else return
        event.accepted = true
      }

      Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(Style.space(960), parent.width - Style.space(32))
        height: Math.min(Style.space(680), parent.height - Style.space(32))
        // opaque: the theme's popup color can be translucent, which makes long lists hard to read
        color: Qt.rgba(Color.popups.background.r, Color.popups.background.g, Color.popups.background.b, 1)
        border.color: Color.popups.border
        border.width: Math.max(1, Style.normalBorderWidth)
        radius: Style.cornerRadius

        MouseArea { anchors.fill: parent; onClicked: {} }

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: Style.space(20)
          spacing: Style.space(12)

          // header
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(12)
            Text {
              text: "Full Remove"
              textFormat: Text.PlainText
              color: Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.heading
              font.bold: true
            }
            Text {
              Layout.fillWidth: true
              text: ({ select: "1 · Choose apps", review: "2 · Review", running: "2 · Removing", leftovers: "3 · Leftovers",
                       cleanup: "4 · Cleanup", cleanupLoad: "4 · Cleanup", done: "Done", report: "Scan only · nothing was changed" })[root.step] || ""
              textFormat: Text.PlainText
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.body
            }
            Toggle {
              visible: root.step === "select"
              Layout.preferredWidth: Style.space(300)
              label: "Scan only (no password)"
              description: "Show what would be removed; change nothing."
              checked: root.scanOnly
              onClicked: root.scanOnly = !root.scanOnly
            }
          }

          // helper banner
          Text {
            Layout.fillWidth: true
            visible: root.step === "select" && !root.scanOnly && root.helper.installed !== undefined && !root.helperOk
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: Color.urgent
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            text: !root.helper.installed
              ? "The root helper is not installed, so packages, system Flatpaks and Snaps can't be removed (scan only, user Flatpaks, AppImages and Dev tools still work). Install it: cd " + root.pluginDir + "/root && makepkg -si"
              : !root.helper.root_owned ? "The root helper is not root-owned or is writable by others. Refusing to use it. Reinstall it with makepkg -si."
              : "The installed root helper (protocol " + root.helper.protocol + ") doesn't match this plugin, or its polkit policy is missing. Reinstall: cd " + root.pluginDir + "/root && makepkg -si"
          }

          Text {
            Layout.fillWidth: true
            visible: root.error !== "" || root.notice !== "" || root.busy !== ""
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: root.error !== "" ? Color.urgent : Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            text: root.error || root.busy || root.notice
          }

          // ---------------- step 1: select
          ColumnLayout {
            visible: root.step === "select"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Style.space(8)

            RowLayout {
              Layout.fillWidth: true
              Button { text: "Apps (" + root.userApps.length + ")"; selected: root.tab === "apps"; onClicked: root.setTab("apps") }
              Button { text: "Omarchy (" + root.omarchyApps.length + ")"; selected: root.tab === "omarchy"; onClicked: root.setTab("omarchy") }
              Button { text: "Dev tools (" + root.devAll.length + ")"; selected: root.tab === "dev"; onClicked: root.setTab("dev") }
              Button { text: "System (" + root.systemApps.length + ")"; selected: root.tab === "system"; onClicked: root.setTab("system") }
              Button { text: "Orphans (" + root.orphanList.length + ")"; selected: root.tab === "orphans"; onClicked: root.setTab("orphans") }
              TextField {
                id: search
                Layout.fillWidth: true
                placeholderText: "Search  ( / )"
                onTextChanged: root.query = text
                Keys.onEscapePressed: keyCatcher.forceActiveFocus()
                Keys.onDownPressed: { keyCatcher.forceActiveFocus(); root.moveCursor(1) }
                Keys.onReturnPressed: root.primaryAction()
                Keys.onTabPressed: { keyCatcher.forceActiveFocus(); root.cycleTab(1) }
                Keys.onBacktabPressed: { keyCatcher.forceActiveFocus(); root.cycleTab(-1) }
              }
            }

            Text {
              visible: root.loadingList || (root.tab === "dev" && root.devMissing.length > 0)
              textFormat: Text.PlainText
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              text: root.loadingList ? "Loading installed apps…" : "Not found: " + root.devMissing.join(", ")
            }

            ListView {
              id: selectList
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              currentIndex: -1
              highlightMoveDuration: 0
              model: root.currentItems
              delegate: ItemRow {
                required property var modelData
                required property int index
                width: ListView.view.width
                current: ListView.isCurrentItem
                primary: modelData.display || modelData.name
                secondary: Model.sourceLabel(modelData.source)
                  + (modelData.display && modelData.display !== modelData.name ? "  ·  " + modelData.name : "")
                  + (modelData.protected ? "  ·  protected: the system or Omarchy needs it"
                     : root.tab === "system" ? "  ·  system component, view only"
                     : modelData.description ? "  ·  " + modelData.description : "")
                tag: modelData.protected ? "protected" : root.tab === "system" ? "system" : ""
                sizeText: modelData.size ? Model.formatSize(modelData.size) : ""
                checked: !!root.selected[modelData.id]
                showCheck: modelData.id !== "" && !modelData.protected && root.tab !== "system"
                onToggled: root.toggle(modelData.id)
              }
            }
          }

          // ---------------- step 2: review / report
          ListView {
            visible: root.step === "review" || root.step === "report"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Style.space(4)
            model: root.plan ? root.reviewLines() : []
            delegate: Text {
              required property var modelData
              width: ListView.view.width
              wrapMode: Text.WrapAnywhere
              textFormat: Text.PlainText
              color: modelData.kind === "warn" ? Color.urgent : modelData.kind === "head" ? Color.popups.text : Color.muted
              font.family: modelData.kind === "cmd" ? "monospace" : Style.font.family
              font.bold: modelData.kind === "head"
              font.pixelSize: modelData.kind === "head" ? Style.font.body : Style.font.bodySmall
              text: modelData.text
            }
          }

          // ---------------- running
          Flickable {
            visible: root.step === "running" || (root.step === "done" && root.logText !== "")
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentHeight: logView.implicitHeight
            onContentHeightChanged: contentY = Math.max(0, contentHeight - height)
            Text {
              id: logView
              width: parent.width
              wrapMode: Text.WrapAnywhere
              textFormat: Text.PlainText
              color: Color.popups.text
              font.family: "monospace"
              font.pixelSize: Style.font.bodySmall
              text: root.logText || "Waiting for the runner… (a password prompt may appear)"
            }
          }

          // ---------------- step 3: leftovers
          ListView {
            id: leftList
            visible: root.step === "leftovers"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            currentIndex: -1
            highlightMoveDuration: 0
            model: root.plan ? root.plan.leftovers : []
            delegate: ItemRow {
              required property var modelData
              width: ListView.view.width
              current: ListView.isCurrentItem
              primary: modelData.path + (modelData.link ? "  (link)" : "")
              secondary: modelData.reason
              tag: modelData.tier === "maybe" ? "maybe" : ""
              sizeText: Model.formatSize(modelData.size)
              checked: !!root.leftTicks[modelData.path]
              showOpen: true
              onToggled: root.toggleLeft(modelData.path)
              onOpenFolder: root.openFolder(modelData.path)
            }
          }

          // ---------------- step 4: cleanup
          ColumnLayout {
            visible: root.step === "cleanup"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Text {
              Layout.fillWidth: true
              wrapMode: Text.Wrap
              textFormat: Text.PlainText
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              text: "Orphans are packages nothing needs any more. The runtime list is approximate; flatpak refuses to remove a runtime an app still needs."
                + (root.cleanupRootArgv().length ? "\nRuns as root: " + Model.displayCommand(root.cleanupRootArgv()) : "")
            }
            ListView {
              id: cleanupList
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              currentIndex: -1
              highlightMoveDuration: 0
              model: root.cleanup ? root.cleanupRows() : []
              delegate: ItemRow {
                required property var modelData
                width: ListView.view.width
                current: ListView.isCurrentItem
                primary: modelData.label
                secondary: modelData.detail
                checked: !!root.cleanupTicks[modelData.id]
                onToggled: root.toggleCleanup(modelData.id)
              }
            }
          }

          // ---------------- done
          Text {
            visible: root.step === "done" && root.logText === ""
            Layout.fillWidth: true
            Layout.fillHeight: true
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            text: "Finished." + (root.trashed.length ? " " + root.trashed.length + " item(s) are in the Trash." : "")
          }

          // ---------------- confirm strip
          Rectangle {
            visible: root.confirming !== ""
            Layout.fillWidth: true
            implicitHeight: confirmRow.implicitHeight + Style.space(16)
            color: Qt.rgba(Color.urgent.r, Color.urgent.g, Color.urgent.b, 0.15)
            radius: Style.cornerRadius
            RowLayout {
              id: confirmRow
              anchors.fill: parent
              anchors.margins: Style.space(8)
              Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                color: Color.popups.text
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                text: root.confirming === "delete" ? "Permanently delete " + root.tickedLeftovers().length + " item(s)? This skips the Trash and cannot be undone."
                  : root.confirming === "empty" ? "Permanently delete the " + root.trashed.length + " item(s) this run moved to the Trash? Nothing else in the Trash is touched."
                  : "A removal is running. Close anyway? The running step will finish; the rest is cancelled."
              }
              Button { text: "Cancel"; bordered: true; onClicked: root.confirming = "" }
              Button {
                text: root.confirming === "close" ? "Close and cancel the rest" : "Delete permanently"
                bordered: true
                foreground: Color.urgent
                onClicked: {
                  if (root.confirming === "delete") root.deleteLeftovers()
                  else if (root.confirming === "empty") root.emptyTrashed()
                  else { root.confirming = ""; root.call("cancel", [], function() {}); root.hideNow() }
                }
              }
            }
          }

          // ---------------- footer
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(8)
            Text {
              Layout.fillWidth: true
              elide: Text.ElideRight
              textFormat: Text.PlainText
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              text: root.confirming !== "" ? "Esc cancel"
                : root.step === "select" ? "↑↓ move · Space tick · Enter continue · Tab tabs · / search · Esc close"
                : root.step === "leftovers" || root.step === "cleanup" ? "↑↓ move · Space tick · Enter " + (root.step === "leftovers" ? "move to Trash" : "remove") + " · Esc close"
                : root.step === "review" ? "Enter confirm · Esc close"
                : root.step === "running" ? "Esc close (the running step finishes)"
                : "Enter " + (root.step === "done" ? "start over" : "back") + " · Esc close"
            }

            Button {
              visible: root.step === "review" || root.step === "report"
              text: "Back"; bordered: true
              onClicked: { root.notice = ""; root.step = "select" }
            }
            Button {
              visible: root.step === "report"
              text: "Copy"; bordered: true
              onClicked: root.copyReport()
            }
            Button {
              visible: root.step === "report"
              text: "Export"; bordered: true
              onClicked: root.exportReport()
            }
            Button {
              visible: root.step === "select"
              enabled: root.selectedCount >= 0 && (root.tab === "orphans" ? root.orphanSelCount() > 0 && !root.scanOnly : root.appSelCount() > 0) && root.busy === ""
              text: root.selectedCount >= 0 && root.tab === "orphans" ? "Continue (" + root.orphanSelCount() + ")"
                : root.scanOnly ? "Scan " + root.appSelCount() : "Continue (" + root.appSelCount() + ")"
              bordered: true
              onClicked: root.tab === "orphans" ? root.reviewOrphans() : root.makePlan()
            }
            Button {
              visible: root.step === "review"
              enabled: root.plan && !root.plan.blocked && (root.plan.root_argv.length === 0 || root.helperOk)
              text: root.plan && root.plan.root_argv.length ? "Confirm (asks for password)" : "Confirm"
              bordered: true
              onClicked: root.startRun("remove", root.selectedIds())
            }
            Button {
              visible: root.step === "leftovers"
              text: "Skip"; bordered: true
              onClicked: root.loadCleanup()
            }
            Button {
              visible: root.step === "leftovers"
              enabled: root.tickedLeftovers().length > 0
              text: "Delete permanently…"; bordered: true
              foreground: Color.urgent
              onClicked: root.confirming = "delete"
            }
            Button {
              visible: root.step === "leftovers"
              text: "Move " + root.tickedLeftovers().length + " to Trash"; bordered: true
              onClicked: root.trashLeftovers()
            }
            Button {
              visible: root.step === "cleanup"
              text: "Skip"; bordered: true
              onClicked: root.step = "done"
            }
            Button {
              visible: root.step === "cleanup"
              enabled: root.cleanupIds().length > 0 && (root.cleanupRootArgv().length === 0 || root.helperOk)
              text: root.cleanupRootArgv().length ? "Remove (asks for password)" : "Remove"
              bordered: true
              onClicked: root.startRun("cleanup", root.cleanupIds())
            }
            Button {
              visible: root.step === "done" && root.trashed.length > 0
              text: "Empty this run's items from Trash…"; bordered: true
              foreground: Color.urgent
              onClicked: root.confirming = "empty"
            }
            Button {
              visible: root.step === "done"
              text: "Start over"; bordered: true
              onClicked: root.resetToSelect()
            }
            Button {
              text: "Close"; bordered: true
              onClicked: root.dismiss()
            }
          }
        }
      }
    }
  }

  // ----------------------------------------------------------- view models

  function reviewLines() {
    var p = plan, out = []
    function add(kind, text) { out.push({ kind: kind, text: text }) }
    add("head", root.scanOnly ? "What would be removed" : "What will be removed")
    if ((p.removes || []).length) add("", "pacman removes: " + p.removes.join(" "))
    ;(p.breaks || []).forEach(function(b) {
      add("warn", b.package ? "Blocked: removing " + b.package + " breaks " + b.required_by + ". Untick it or also select " + b.required_by + "."
                            : "Blocked: " + b.required_by)
    })
    if ((p.root_argv || []).length) {
      add("head", "Runs as root (you'll be asked for your password)")
      add("cmd", Model.displayCommand(p.root_argv))
      if (!root.helperOk) add("warn", "The root helper is missing or outdated; install it first (see the banner on the first page).")
    }
    if ((p.cache || []).length) {
      add("head", "Package cache files the helper will delete (as of now)")
      p.cache.forEach(function(c) { add("cmd", c) })
    }
    if ((p.user || []).length) {
      add("head", "Runs as you (no password)")
      p.user.forEach(function(u) { add("cmd", u.command) })
    }
    add("head", "Leftovers found: " + (p.leftovers || []).length + " (you choose in the next step)")
    ;(p.leftovers || []).forEach(function(l) {
      add(l.tier === "maybe" ? "warn" : "", (l.tier === "maybe" ? "[maybe] " : "") + Model.formatSize(l.size) + "  " + l.path + "  (" + l.reason + ")")
    })
    if (root.step === "report" && root.cleanup) {
      add("head", "Orphan packages: " + ((root.cleanup.orphans || []).join(" ") || "none"))
      add("head", "Unused Flatpak runtimes (approximate)")
      ;(root.cleanup.runtimes || []).forEach(function(r) { add("", r.ref + " (" + r.installation + ")") })
    }
    return out
  }

  function cleanupRows() {
    var rows = []
    ;(cleanup.orphans || []).forEach(function(o) { rows.push({ id: "orphan:" + o, label: o, detail: "orphan package (root)" }) })
    var userRt = []
    ;(cleanup.runtimes || []).forEach(function(r) {
      if (r.installation === "system") rows.push({ id: "runtime:" + r.ref, label: r.ref, detail: "unused system runtime (root)" })
      else userRt.push(r.ref)
    })
    if (userRt.length) rows.push({ id: "user-runtimes", label: "Unused user runtimes", detail: userRt.join(", ") + " (flatpak uninstall --user --unused, no password)" })
    return rows
  }
}
