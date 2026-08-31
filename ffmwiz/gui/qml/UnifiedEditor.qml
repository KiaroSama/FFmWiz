// FFmWiz Unified Video Editor — QtQuick/QML (modern engine).
// Colors come from the classic palette (bridge.paletteJson) so the look matches.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: win
    visible: true
    width: 1360
    height: 860
    minimumWidth: 1080
    minimumHeight: 660
    title: "FFmWiz Unified Video Editor"

    // ---- Data parsed from the bridge ----
    property var req: ({})
    property var theme: ({})
    property var segs: []
    property real totalDuration: 0
    property real fps: 30
    property int sourceW: 1920
    property int sourceH: 1080
    property bool hasAudio: true

    // ---- Editing state ----
    property real cti: 0
    property int curSeg: 0
    property real markIn: 0
    property real markOut: 0
    property int cropTop: 0
    property int cropLeft: 0
    property int cropRight: 0
    property int cropBottom: 0
    property real speed: 1.0
    property bool reverse: false
    property bool includeAudio: true
    property var separatorPoints: []
    property var cuts: []          // ranges to REMOVE; keep = complement
    property var wf: []            // [min,max] amplitude pairs for the CURRENT viewport
    property string wfKey: ""      // backend waveform cache key (changes => re-fetch)
    property var chapters: []      // [{t, title}] drawn on the timeline
    property bool ready: false

    // ---- Audio / volume (parity with classic mute + volume control) ----
    property real volume: 0.6
    property bool muted: false
    // ---- Crop overlay can be shown independently of edit mode (Ctrl+U) ----
    property bool cropOverlayOn: true
    // ---- Selection on the timeline (what Delete acts on) ----
    property string selMarker: ""  // "in" | "out" | ""
    property int selSplit: -1
    property int selCut: -1

    // ---- Preview (video) pan/zoom — parity with classic Hand/Zoom tools ----
    property real pvZoom: 1.0
    property real pvOffX: 0
    property real pvOffY: 0
    property string tool: "hand"   // "hand" | "zoom"

    // ---- Live reverse preview state (renders reversed proxy chunks) ----
    property bool revActive: false
    property int revGen: 0
    property real revPlayBase: 0   // CTI where the current reverse run began
    property real revWinStart: 0
    property real revWinEnd: 0
    readonly property real revWindow: 15.0
    property string notice: ""     // transient warning shown in the header

    // ---- Phase 5: timeline zoom/pan + interactive crop ----
    property real zoom: 1.0        // 1 = whole clip visible; higher = zoomed in
    property real viewStart: 0     // left edge of the visible window, in seconds
    property bool cropEdit: true   // show draggable crop handles on the preview
    property bool snapEnabled: true   // magnetic snapping of marks/splits/CTI to targets
    // Undo/redo: JSON snapshots of the editable state (mirrors classic HistoryStack).
    property var histUndo: []
    property var histRedo: []
    property bool restoring: false
    property string histCurrent: ""

    function viewSpan() { return totalDuration / Math.max(1, zoom) }
    function clampView() {
        var sp = viewSpan()
        viewStart = Math.max(0, Math.min(Math.max(0, totalDuration - sp), viewStart))
    }
    // Zoom around an anchor time, keeping it at the same fractional x position.
    function zoomAt(factor, anchorT, frac) {
        var z = Math.max(1, Math.min(400, zoom * factor))
        if (z === zoom) return
        zoom = z
        var sp = viewSpan()
        viewStart = anchorT - frac * sp
        clampView()
    }
    function fitZoom() { zoom = 1.0; viewStart = 0 }

    // ---- Preview pan/zoom (zoom INTO the video; classic Hand/Zoom tools) ----
    function pvZoomAt(factor, ax, ay) {
        var z = Math.max(1, Math.min(8, pvZoom * factor))
        if (z === pvZoom) return
        var cx = (ax - pvOffX) / pvZoom, cy = (ay - pvOffY) / pvZoom
        pvZoom = z; pvOffX = ax - cx * pvZoom; pvOffY = ay - cy * pvZoom; clampPan()
    }
    function clampPan() {
        if (pvZoom <= 1.0001) { pvOffX = 0; pvOffY = 0; return }
        var w = previewArea.width, h = previewArea.height
        pvOffX = Math.max(w - w * pvZoom, Math.min(0, pvOffX))
        pvOffY = Math.max(h - h * pvZoom, Math.min(0, pvOffY))
    }
    function resetPreviewView() { pvZoom = 1.0; pvOffX = 0; pvOffY = 0 }

    // ---- Frame-accurate scrubbing ----
    function curFrame() { return Math.round(cti * fps) }
    function totalFrames() { return Math.round(totalDuration * fps) }
    function frameStep(dir) {
        var f = Math.round(cti * fps) + dir
        seekTo(Math.max(0, Math.min(totalDuration, f / Math.max(0.001, fps))))
    }

    // ---- Magnetic snapping (pixel-based pull, tightens as you zoom) ----
    function snapTargets(excludeSplitIdx, excludeMarker) {
        var t = [cti, 0, totalDuration]
        if (excludeMarker !== "in") t.push(markIn)
        if (excludeMarker !== "out") t.push(markOut)
        for (var i = 0; i < separatorPoints.length; ++i)
            if (i !== excludeSplitIdx) t.push(Number(separatorPoints[i]))
        for (var s = 1; s < segs.length; ++s) t.push(segs[s].start)   // join boundaries
        return t
    }
    function snapTime(t, excludeSplitIdx, excludeMarker) {
        t = Math.max(0, Math.min(totalDuration, t))
        if (!snapEnabled) return t
        var spanPx = Math.max(1, tl.width - 2 * tl.pad)
        var tol = (viewSpan() / spanPx) * 8.0          // ~8 px magnet
        var tg = snapTargets(excludeSplitIdx, excludeMarker)
        var best = t, bd = tol
        for (var k = 0; k < tg.length; ++k) { var d = Math.abs(t - tg[k]); if (d <= bd) { best = tg[k]; bd = d } }
        return best
    }

    // ---- Undo/redo: snapshot the full editable state as JSON ----
    function snapshot() {
        return JSON.stringify({ ct: cropTop, cl: cropLeft, cr: cropRight, cb: cropBottom,
            sp: speed, rv: reverse, ia: includeAudio, mi: markIn, mo: markOut,
            cuts: cuts, splits: separatorPoints })
    }
    function applySnapshot(s) {
        var o = JSON.parse(s)
        restoring = true
        cropTop = o.ct; cropLeft = o.cl; cropRight = o.cr; cropBottom = o.cb
        speed = o.sp; reverse = o.rv; includeAudio = o.ia
        markIn = o.mi; markOut = o.mo
        cuts = o.cuts; separatorPoints = o.splits
        var idx = speedBox.model.indexOf(Math.round(speed * 100) + "%"); if (idx >= 0) speedBox.currentIndex = idx; else speedBox.editText = Math.round(speed * 100) + "%"
        restoring = false
        tl.requestPaint()
    }
    // Record a new state after an edit-affecting action (skips no-op duplicates).
    function commit() {
        if (restoring) return
        var snap = snapshot()
        if (snap === histCurrent) return
        if (histCurrent !== "") { var u = histUndo.slice(); u.push(histCurrent); if (u.length > 100) u.shift(); histUndo = u }
        histCurrent = snap
        histRedo = []
    }
    function doUndo() {
        if (!histUndo.length) return
        var u = histUndo.slice(); var prev = u.pop()
        var r = histRedo.slice(); r.push(histCurrent); histRedo = r
        histCurrent = prev; histUndo = u
        applySnapshot(prev)
    }
    function doRedo() {
        if (!histRedo.length) return
        var r = histRedo.slice(); var nxt = r.pop()
        var u = histUndo.slice(); u.push(histCurrent); histUndo = u
        histCurrent = nxt; histRedo = r
        applySnapshot(nxt)
    }

    function col(key, fallback) { return (theme && theme[key]) ? theme[key] : fallback }
    // Canvas wants a CSS colour string; palette tokens are #rrggbb. Keeps the
    // translucent timeline fills on the shared tokens instead of hand-written
    // rgba() literals that silently drift from the classic editor (USER-12-3).
    function colA(key, fallback, a) {
        var c = String(col(key, fallback))
        return "rgba(" + parseInt(c.substr(1, 2), 16) + "," + parseInt(c.substr(3, 2), 16)
               + "," + parseInt(c.substr(5, 2), 16) + "," + a + ")"
    }
    color: col("bg", "#0d1117")

    palette.window: col("bg", "#0d1117")
    palette.windowText: col("text", "#e8edfb")
    palette.base: col("panel_alt", "#1a1f2a")
    palette.text: col("text", "#e8edfb")
    palette.button: col("surface", "#21262d")
    palette.buttonText: col("text", "#e8edfb")
    palette.highlight: col("accent", "#3b82f6")
    palette.highlightedText: "#ffffff"
    palette.mid: col("border", "#30363d")

    // ---------- Reusable styled components ----------
    // A panel, not a card. radius 10 with a full border made every surface read
    // as a floating tile, which is the single biggest reason this looked like a
    // toy next to a real NLE -- Premiere and Resolve separate panels with a
    // hairline and almost no corner, so the eye reads regions instead of boxes.
    // ---- design tokens -------------------------------------------------
    // One place for the values that decide whether a panel reads as designed or
    // assembled. Before this, spacing was 2/5/6/8/10/16 and radius was 2/4/7/10
    // with no rule behind either -- an eye reads that inconsistency as cheap
    // long before it can say why.
    //
    // A 4px grid, two radii, and a four-step type scale. Every new control
    // should take its numbers from here rather than inventing one more.
    readonly property int sp0: 2          // hairline, icon-to-label
    readonly property int sp1: 4          // inside a control
    readonly property int sp2: 8          // between controls in a group
    readonly property int sp3: 12         // between groups
    readonly property int sp4: 16         // panel padding
    readonly property int sp5: 24         // between panels

    // Nested radius: an inner shape inside an outer one, with a gap under
    // 32px, takes `outer - gap` -- and stays square when that lands at 2 or
    // below. It is why a 3px panel holding 6px-padded buttons gives those
    // buttons a square corner instead of a second, competing curve.
    readonly property int radSm: 2        // controls
    readonly property int radMd: 3        // panels
    function radNested(outer, gap) { var r = outer - gap; return r > 2 ? r : 0 }
    readonly property int fsMicro: 10     // captions, hints, section titles
    readonly property int fsBody: 11      // labels and button text
    readonly property int fsLead: 12      // values worth reading first
    readonly property int fsTitle: 14     // the app title, once
    readonly property int rowSm: 22       // inline control
    readonly property int rowMd: 26       // standard button
    readonly property int rowLg: 32       // primary action

    // Border all the way round or not at all, and a flat fill -- a one-sided
    // rule reads as a rendering artefact and a gradient ground fights the
    // video, which is the only thing in this window that should hold colour.
    component Card: Rectangle {
        radius: win.radMd
        color: win.col("panel", "#161b22")
        border.color: win.col("border_soft", "#21262d")
        border.width: 1
    }

    component PadButton: Button {
        id: pb
        property color baseColor: win.col("surface", "#21262d")
        // Tokenised interaction states. Computing them with Qt.lighter/darker
        // produced different hexes from the Qt QSS for the same role, so the two
        // engines hovered differently (USER-12-2). The computed values remain the
        // default for buttons whose base colour has no hover/pressed token.
        property color hoverColor: (pb.baseColor == win.col("surface", "#21262d"))
                                   ? win.col("surface_hover", "#2e353d") : Qt.lighter(pb.baseColor, 1.18)
        property color pressedColor: (pb.baseColor == win.col("surface", "#21262d"))
                                     ? win.col("surface_pressed", "#1c2128") : Qt.darker(pb.baseColor, 1.25)
        property color textColor: win.col("text", "#e8edfb")
        property url iconSource: ""
        implicitHeight: win.rowMd
        padding: win.sp1 + 2
        hoverEnabled: true
        // Pointing-hand cursor on hover/click so buttons feel clickable.
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            radius: win.radSm
            color: pb.down ? pb.pressedColor : (pb.hovered ? pb.hoverColor : pb.baseColor)
            // Outline only while hovered. A permanent 1px border around every
            // button turns a control panel into a grid of boxes; the fill
            // already separates the button from the panel, and the outline is
            // then free to mean "you are pointing at this".
            // Hover outlines; keyboard focus outlines in the accent. Without the
            // second case, tabbing through the panel moved an invisible cursor.
            border.color: pb.visualFocus ? win.col("accent", "#3b82f6")
                        : (pb.hovered ? win.col("border_strong", "#3a4150") : "transparent")
            border.width: 1
        }
        contentItem: RowLayout {
            spacing: 6
            Image {
                visible: String(pb.iconSource) !== ""
                Layout.preferredWidth: visible ? 16 : 0
                Layout.preferredHeight: 16
                Layout.alignment: Qt.AlignVCenter
                source: pb.iconSource
                sourceSize.width: 16; sourceSize.height: 16
                fillMode: Image.PreserveAspectFit
                opacity: pb.enabled ? 1.0 : 0.5
            }
            Label {
                Layout.fillWidth: true
                text: pb.text
                color: pb.enabled ? pb.textColor : win.col("text_mute", "#8891b4")
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: 13
                elide: Text.ElideRight
            }
        }
    }

    // A panel title, not a headline. Bright accent-blue at 12px competed with
    // the controls under it; a muted, letter-spaced 10px label sits behind them
    // and lets the eye go straight to what is actionable.
    // ---- the non-button controls ----------------------------------------
    // PadButton was styled and everything else was left as stock Qt Quick
    // Controls, so a designed button sat beside a default switch and a default
    // combo in the same column. That mismatch is what reads as assembled
    // rather than designed -- more than any single control being plain.
    //
    // These four take the same tokens the buttons do, plus a focus ring, which
    // nothing in this window had at all.

    component Toggle: Switch {
        id: sw
        implicitHeight: win.rowMd
        font.pixelSize: win.fsBody
        indicator: Rectangle {
            implicitWidth: 30; implicitHeight: 16
            x: sw.leftPadding; y: (sw.height - height) / 2
            radius: height / 2
            color: sw.checked ? win.col("accent", "#3b82f6") : win.col("surface", "#21262d")
            border.color: sw.visualFocus ? win.col("accent_hover", "#5b9bff")
                                         : win.col("border_strong", "#3a4150")
            border.width: 1
            opacity: sw.enabled ? 1.0 : 0.45
            Behavior on color { ColorAnimation { duration: 110 } }
            Rectangle {
                x: sw.checked ? parent.width - width - 2 : 2
                y: 2; width: 12; height: 12; radius: 6
                color: win.col("text", "#e8edfb")
                Behavior on x { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
            }
        }
        contentItem: Label {
            text: sw.text; font: sw.font
            color: win.col("text", "#e8edfb")
            opacity: sw.enabled ? 1.0 : 0.45
            verticalAlignment: Text.AlignVCenter
            leftPadding: sw.indicator.width + win.sp2
        }
    }

    component Picker: ComboBox {
        id: cb
        implicitHeight: win.rowMd
        font.pixelSize: win.fsBody
        background: Rectangle {
            radius: win.radSm
            color: win.col("surface", "#21262d")
            border.color: cb.activeFocus ? win.col("accent", "#3b82f6")
                                         : win.col("border_strong", "#3a4150")
            border.width: 1
        }
        contentItem: Label {
            text: cb.editable ? cb.editText : cb.displayText
            color: win.col("text", "#e8edfb"); font: cb.font
            verticalAlignment: Text.AlignVCenter
            leftPadding: win.sp2; rightPadding: win.sp2
            elide: Text.ElideRight
        }
    }

    component Stepper: SpinBox {
        id: sb
        implicitHeight: win.rowMd
        font.pixelSize: win.fsBody
        background: Rectangle {
            radius: win.radSm
            color: win.col("surface", "#21262d")
            border.color: sb.activeFocus ? win.col("accent", "#3b82f6")
                                         : win.col("border_strong", "#3a4150")
            border.width: 1
        }
    }

    component Track: Slider {
        id: sl
        implicitHeight: win.rowSm
        background: Rectangle {
            x: sl.leftPadding; y: sl.topPadding + sl.availableHeight / 2 - height / 2
            width: sl.availableWidth; height: 3; radius: 1.5
            color: win.col("surface", "#21262d")
            Rectangle {
                width: sl.visualPosition * parent.width; height: parent.height
                radius: parent.radius; color: win.col("accent", "#3b82f6")
            }
        }
        handle: Rectangle {
            x: sl.leftPadding + sl.visualPosition * (sl.availableWidth - width)
            y: sl.topPadding + sl.availableHeight / 2 - height / 2
            width: 12; height: 12; radius: 6
            color: sl.pressed ? win.col("accent_hover", "#5b9bff") : win.col("text", "#e8edfb")
            border.color: win.col("border_strong", "#3a4150")
        }
    }

    // A group break as one word. It was a Rectangle literal with a hand-picked
    // colour each time, and it used `border` -- a weight meant for outlining a
    // shape, which reads a shade too strong drawn as a rule across a panel.
    component Rule: Rectangle {
        Layout.fillWidth: true
        implicitHeight: 1
        color: win.col("border_soft", "#21262d")
    }

    component SectionLabel: Label {
        color: win.col("text_mute", "#8891b4")
        font.bold: true
        font.pixelSize: win.fsMicro
        font.letterSpacing: 0.8
        font.capitalization: Font.AllUppercase
        topPadding: 2
        bottomPadding: 2
    }

    component CropField: RowLayout {
        property string label: ""
        property int maxv: 9999
        property int v: 0
        signal edited(int value)
        spacing: 6
        Label { text: parent.label; color: win.col("text_mute", "#8891b4"); Layout.preferredWidth: 14 }
        Stepper {
            from: 0; to: parent.maxv; value: parent.v; editable: true
            Layout.fillWidth: true
            onValueModified: parent.edited(value)
        }
    }

    Component.onCompleted: {
        try {
            req = JSON.parse(bridge.requestJson)
            theme = JSON.parse(bridge.paletteJson)
        } catch (e) {
            bridge.logMessage("Failed to parse request/palette: " + e)
            return
        }
        fps = Number(req.fps) || 30
        sourceW = Number(req.source_w) || 1920
        sourceH = Number(req.source_h) || 1080
        hasAudio = !!req.has_audio

        var list = []; var off = 0
        var js = req.join_segments || []
        if (js.length > 0) {
            for (var i = 0; i < js.length; ++i) {
                var d = Math.max(0.001, Number(js[i].duration) || 0)
                // Carry each segment's own audio flag: the reverse preview picks
                // one segment, and using the whole job's flag muted an audible
                // clip whenever input 1 was silent (R02).
                var segAudio = (js[i].has_audio !== undefined) ? !!js[i].has_audio : true
                list.push({ path: js[i].path, name: js[i].name || ("Video " + (i + 1)), start: off, duration: d, hasAudio: segAudio })
                off += d
                if (segAudio) hasAudio = true
            }
            totalDuration = off
        } else {
            var d0 = Math.max(0.001, Number(req.duration) || 0)
            list.push({ path: req.input_path, name: "Video 1", start: 0, duration: d0, hasAudio: !!req.has_audio })
            totalDuration = d0
        }
        segs = list

        var m = req.initial_margins || [0, 0, 0, 0]
        cropTop = m[0] || 0; cropLeft = m[1] || 0; cropRight = m[2] || 0; cropBottom = m[3] || 0
        speed = Number(req.initial_speed) || 1.0
        reverse = !!req.initial_reverse
        includeAudio = (req.initial_include_audio !== undefined) ? !!req.initial_include_audio : hasAudio
        separatorPoints = req.initial_separator_points || []
        // Chapters (parity with classic): [{start,title}] -> [{t,title}] for drawing.
        var chs = req.chapters || []
        var clist = []
        for (var ci = 0; ci < chs.length; ++ci) {
            // The bridge ships {start, end, title} already in SECONDS; ffprobe's
            // own `start` is in time_base ticks and its title lives under
            // tags.title, which is why raw dicts drew wrong or nothing (D14).
            var ct = Number(chs[ci].start)
            if (!isNaN(ct) && ct >= 0 && ct <= totalDuration)
                clist.push({ t: ct, title: String(chs[ci].title || ("Chapter " + (ci + 1))) })
        }
        chapters = clist
        var keep = req.initial_keep_ranges || []
        if (keep.length > 0) { markIn = Number(keep[0][0]) || 0; markOut = Number(keep[keep.length - 1][1]) || totalDuration }
        else { markIn = 0; markOut = totalDuration }
        // Reconstruct removed (cut) ranges from the kept ranges carried over.
        cuts = cutsFromInitialKeep(keep)

        ready = true
        histCurrent = snapshot()   // baseline state for undo/redo
        loadSegment(0, 0, false)
        bridge.startWaveform()
        if (win.visibility !== Window.Maximized) win.showMaximized()
    }

    // Waveform overview arrives asynchronously from the backend decode; then we
    // immediately request a precise window for the current viewport.
    Connections {
        target: bridge
        function onWaveformReady(cacheKey, overviewJson) {
            win.wfKey = cacheKey
            try { win.wf = JSON.parse(overviewJson) || [] } catch (e) { win.wf = [] }
            tl.requestPaint()
            win.refreshWaveform()
        }
    }
    // Fetch per-viewport [min,max] data from the backend. Called ONLY when the
    // viewport (zoom/pan) or canvas width changes — never on playback ticks — so
    // the heavy work stays in Python and the CTI overlay can move independently.
    function refreshWaveform() {
        if (!ready || wfKey === "") return
        var a = viewStart
        var b = viewStart + viewSpan()
        var w = Math.max(16, Math.round(tl.width - 2 * tl.pad))
        try {
            var s = bridge.waveformWindow(a, b, w)
            win.wf = JSON.parse(s) || []
        } catch (e) { /* keep previous wf */ }
        tl.requestPaint()
    }
    // Debounce viewport-driven refreshes so dragging zoom/pan doesn't spam the
    // backend; the actual fetch runs once motion settles.
    Timer {
        id: wfTimer
        interval: 60; repeat: false
        onTriggered: win.refreshWaveform()
    }
    function scheduleWaveform() { wfTimer.restart() }

    // ---------- Playback (two players: preload next segment for near-seamless joins) ----------
    property int activeAB: 0       // 0 -> playerA active, 1 -> playerB active
    property bool wantPlaying: false

    // Release BOTH media sources before the app exits. On Windows the
    // MediaPlayer keeps the reverse proxy file open, so TemporaryDirectory
    // cleanup on the Python side hits WinError 32 and abandons the file in
    // %TEMP% -- silently, because that cleanup swallows OSError (NEW-GUI3).
    onClosing: {
        playerA.stop(); playerB.stop()
        playerA.source = ""; playerB.source = ""
    }

    // Which audio track the PREVIEW plays. A dual-language release carries one
    // track per language, and with only the first one audible you cannot hear
    // what you are cutting. Preview only: which track ends up in the OUTPUT is
    // the wizard's own audio-track question, and answering it in two places
    // would give the job two sources of truth.
    //
    // Held here rather than on a player because the join path swaps between
    // playerA and playerB, and a choice stored on one is lost at the swap.
    property int audioTrack: 0

    function audioTrackCount() {
        var t = playerA.audioTracks
        return t ? t.length : 0
    }

    // "2 - jpn - AAC" from whatever the file actually declares. Qt hands back
    // metadata keys, not a formatted string, and a track may declare none of
    // them -- hence the numbered fallback rather than a blank row.
    function audioTrackLabel(i) {
        var t = playerA.audioTracks
        if (!t || i < 0 || i >= t.length) return "Track " + (i + 1)
        var md = t[i], bits = []
        try {
            var lang = md.stringValue(MediaMetaData.Language)
            if (lang) bits.push(lang)
            var codec = md.stringValue(MediaMetaData.AudioCodec)
            if (codec) bits.push(codec)
            var title = md.stringValue(MediaMetaData.Title)
            if (title) bits.push(title)
        } catch (e) { }
        return (i + 1) + (bits.length ? "  " + bits.join("  •  ") : "")
    }

    function audioTrackModel() {
        var out = []
        for (var i = 0; i < audioTrackCount(); i++) out.push(audioTrackLabel(i))
        return out
    }

    // Both players, so the selection survives the join hand-off mid-playback.
    function applyAudioTrack() {
        var n = audioTrackCount()
        if (n <= 0) return
        var idx = Math.max(0, Math.min(n - 1, audioTrack))
        playerA.activeAudioTrack = idx
        playerB.activeAudioTrack = idx
    }

    function actP() { return activeAB === 0 ? playerA : playerB }
    function idleP() { return activeAB === 0 ? playerB : playerA }
    function srcOf(i) { return "file:///" + String(segs[i].path).replace(/\\/g, "/") }
    // Visibility is driven by the declarative bindings `visible: win.activeAB === N`
    // on each VideoOutput. This is a no-op kept for call-site compatibility:
    // assigning voA.visible/voB.visible imperatively here would BREAK those
    // bindings (and could leave a stale/letterbox-mismatched surface shown).
    function showActive() {}

    MediaPlayer {
        id: playerA
        // Re-assert the chosen track: loading media resets
        // activeAudioTrack to 0, and the join path loads a new segment
        // on every hand-off, so a choice made once would not survive
        // the first seam.
        onTracksChanged: win.applyAudioTrack()
        videoOutput: voA
        audioOutput: AudioOutput { id: aoA; volume: win.muted ? 0.0 : win.volume }
        onPositionChanged: {
            if (!ready || activeAB !== 0) return
            if (revActive) cti = Math.min(totalDuration, revPlayBase + position / 1000.0)
            else if (segs.length) cti = Math.min(totalDuration, segs[curSeg].start + position / 1000.0)
        }
        onMediaStatusChanged: { if (activeAB === 0 && mediaStatus === MediaPlayer.EndOfMedia) { if (revActive) advanceReverse(); else advanceToNext() } }
    }
    MediaPlayer {
        id: playerB
        // Re-assert the chosen track: loading media resets
        // activeAudioTrack to 0, and the join path loads a new segment
        // on every hand-off, so a choice made once would not survive
        // the first seam.
        onTracksChanged: win.applyAudioTrack()
        videoOutput: voB
        audioOutput: AudioOutput { id: aoB; volume: win.muted ? 0.0 : win.volume }
        onPositionChanged: {
            if (!ready || activeAB !== 1) return
            if (revActive) cti = Math.min(totalDuration, revPlayBase + position / 1000.0)
            else if (segs.length) cti = Math.min(totalDuration, segs[curSeg].start + position / 1000.0)
        }
        onMediaStatusChanged: { if (activeAB === 1 && mediaStatus === MediaPlayer.EndOfMedia) { if (revActive) advanceReverse(); else advanceToNext() } }
    }

    // A reversed preview proxy finished rendering: load + play it (CTI advances
    // forward via the player's position; playbackRate bakes in the chosen speed).
    Connections {
        target: bridge
        function onReverseReady(gen, path) {
            if (gen !== revGen || !revActive) return
            if (path === "") {
                // The proxy did not render (bad range, unreadable source, ...).
                // Leave reverse playback instead of waiting forever for a chunk
                // that will never arrive (D17).
                revActive = false; wantPlaying = false
                actP().pause(); actP().playbackRate = 1.0
                win.notice = "Reverse preview couldn't render here (the exported file is still reversed)."
                return
            }
            win.notice = ""
            actP().source = "file:///" + String(path).replace(/\\/g, "/")
            actP().position = 0
            actP().playbackRate = Math.max(0.25, Math.min(4.0, speed))
            if (wantPlaying) actP().play(); else actP().pause()
        }
    }

    function preloadNext() {
        if (curSeg + 1 < segs.length) { idleP().source = srcOf(curSeg + 1); idleP().position = 0; idleP().pause() }
        else { idleP().source = "" }
    }
    // Hard switch the ACTIVE player to a segment (init + manual seeks across segments).
    function loadSegment(index, localSeconds, playAfter) {
        if (!segs.length) return
        index = Math.max(0, Math.min(segs.length - 1, index))
        curSeg = index
        wantPlaying = playAfter
        actP().source = srcOf(index)
        actP().position = Math.max(0, Math.round(localSeconds * 1000))
        if (playAfter) actP().play(); else actP().pause()
        showActive()
        preloadNext()
    }
    // Sequential boundary: hand over to the already-preloaded idle player (no reload gap).
    function advanceToNext() {
        if (curSeg + 1 >= segs.length) { actP().pause(); wantPlaying = false; return }
        if (idleP().source === undefined || String(idleP().source) === "") preloadNext()
        activeAB = (activeAB === 0) ? 1 : 0
        curSeg = curSeg + 1
        showActive()
        actP().position = 0
        if (wantPlaying) actP().play()
        preloadNext()
    }
    function segmentForTime(t) {
        t = Math.max(0, Math.min(totalDuration, t))
        for (var i = 0; i < segs.length; ++i)
            if (t < segs[i].start + segs[i].duration - 1e-6 || i === segs.length - 1) return { index: i, local: t - segs[i].start }
        return { index: 0, local: t }
    }
    function seekTo(t) {
        if (reverse) {
            // Re-anchor reverse playback: stop the proxy, show the mirrored frame.
            revActive = false; actP().playbackRate = 1.0
            bridge.cancelReverse()
            cti = Math.max(0, Math.min(totalDuration, t))
            var sr = segmentForTime(srcTime(cti))
            if (sr.index === curSeg) actP().position = Math.round(sr.local * 1000)
            else loadSegment(sr.index, sr.local, false)
            return
        }
        var s = segmentForTime(t)
        cti = Math.max(0, Math.min(totalDuration, t))
        if (s.index === curSeg) actP().position = Math.round(s.local * 1000)
        else loadSegment(s.index, s.local, actP().playbackState === MediaPlayer.PlayingState)
    }
    // Source time shown for a timeline CTI. Reverse mirrors the whole clip.
    function srcTime(t) { return reverse ? Math.max(0, Math.min(totalDuration, totalDuration - t)) : t }
    function togglePlay() {
        if (reverse) {
            if (actP().playbackState === MediaPlayer.PlayingState) { actP().pause(); wantPlaying = false }
            else if (revActive) { actP().play(); wantPlaying = true }
            else startReverse(cti)
            return
        }
        if (actP().playbackState === MediaPlayer.PlayingState) { actP().pause(); wantPlaying = false }
        else { actP().play(); wantPlaying = true }
    }
    // Render + play a reversed proxy window ending at the current source time;
    // the CTI moves FORWARD while the source content plays BACKWARD (mirror).
    function startReverse(fromCti) {
        var c = Math.max(0, Math.min(totalDuration, fromCti))
        revPlayBase = c
        var winEnd = srcTime(c)
        var winStart = Math.max(0, winEnd - revWindow)
        if (winEnd <= 0.05) return        // already at the source start
        revActive = true; wantPlaying = true
        renderReverseChunk(winStart, winEnd)
    }
    function renderReverseChunk(winStart, winEnd) {
        revGen += 1
        var s = segmentForTime(Math.max(0, winEnd - 1e-3))   // map window to its segment (joins)
        // Clip the window to that ONE segment rather than clamping the offset to
        // zero: a window straddling a join boundary used to be sourced entirely
        // from the end segment, skipping the tail of the previous one (D16).
        // Shortening it here makes advanceReverse resume exactly at the boundary.
        var eff = Math.max(winStart, segs[s.index].start)
        revWinStart = eff; revWinEnd = winEnd
        var ss = eff - segs[s.index].start
        var dur = Math.max(0.05, winEnd - eff)
        var w = Math.max(320, Math.min(1280, Math.round(previewArea.width)))
        bridge.renderReverse(JSON.stringify({ gen: revGen, src: segs[s.index].path, ss: ss, dur: dur, width: w,
                                             has_audio: !!segs[s.index].hasAudio }))
    }
    function advanceReverse() {
        revPlayBase = revPlayBase + (revWinEnd - revWinStart)
        var winEnd = revWinStart
        var winStart = Math.max(0, winEnd - revWindow)
        if (winEnd <= 0.05) { actP().pause(); revActive = false; wantPlaying = false; return }
        renderReverseChunk(winStart, winEnd)
    }
    function fmt(t) {
        t = Math.max(0, t)
        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60), ms = Math.floor((t - Math.floor(t)) * 1000)
        function p(n, w) { var x = String(n); while (x.length < w) x = "0" + x; return x }
        return p(h, 2) + ":" + p(m, 2) + ":" + p(s, 2) + "." + p(ms, 3)
    }
    // Compact HH:MM:SS (no milliseconds) for the timeline ruler tick labels.
    function fmtShort(t) {
        t = Math.max(0, t)
        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60)
        function p(n) { var x = String(n); return x.length < 2 ? ("0" + x) : x }
        return (h > 0 ? (p(h) + ":") : "") + p(m) + ":" + p(s)
    }

    // ---- Cut/keep range maths ----
    function normRanges(arr) {
        var r = []
        for (var i = 0; i < arr.length; ++i) {
            var s = Math.max(0, Math.min(totalDuration, Number(arr[i][0])))
            var e = Math.max(0, Math.min(totalDuration, Number(arr[i][1])))
            if (e > s + 0.02) r.push([s, e])
        }
        r.sort(function (a, b) { return a[0] - b[0] })
        var out = []
        for (var k = 0; k < r.length; ++k) {
            if (out.length && r[k][0] <= out[out.length - 1][1] + 0.001) out[out.length - 1][1] = Math.max(out[out.length - 1][1], r[k][1])
            else out.push([r[k][0], r[k][1]])
        }
        return out
    }
    function invertRanges(arr, dur) {
        var r = normRanges(arr), out = [], pos = 0
        for (var i = 0; i < r.length; ++i) { if (r[i][0] > pos + 0.02) out.push([pos, r[i][0]]); pos = Math.max(pos, r[i][1]) }
        if (pos < dur - 0.02) out.push([pos, dur])
        return out
    }
    function keepFromCuts() {
        var k = invertRanges(cuts, totalDuration)
        return k.length ? k : [[0, totalDuration]]
    }
    // A fresh edit carries an EMPTY keep list; inverting it marks the whole
    // timeline as one cut, so guard the inversion (D12).
    function cutsFromInitialKeep(keep) {
        if (!keep || !keep.length) return []
        return invertRanges(keep.map(function (r) { return [Number(r[0]), Number(r[1])] }), totalDuration)
    }
    // The reply carries the TRUE keep list — empty when every frame is cut.
    // `cuts_applied` is what tells the caller "empty" apart from "no cuts at
    // all", which keepFromCuts() (a display helper) cannot express (D13).
    function keepRangesForResult() {
        return cuts.length ? invertRanges(cuts, totalDuration) : []
    }
    function keepTotal() {
        var k = keepFromCuts(), t = 0
        for (var i = 0; i < k.length; ++i) t += (k[i][1] - k[i][0])
        return t
    }
    function cutSelection() {
        var lo = Math.min(markIn, markOut), hi = Math.max(markIn, markOut)
        if (hi - lo < 0.05) return
        var c = cuts.slice(); c.push([lo, hi]); cuts = normRanges(c); tl.requestPaint(); commit()
    }
    function deleteCutAtCti() {
        var c = []
        for (var i = 0; i < cuts.length; ++i) if (!(cti >= cuts[i][0] - 0.001 && cti <= cuts[i][1] + 0.001)) c.push(cuts[i])
        cuts = c; tl.requestPaint(); commit()
    }
    function deleteSplitAtCti() {
        var best = -1, bd = 1e9
        for (var i = 0; i < separatorPoints.length; ++i) { var d = Math.abs(Number(separatorPoints[i]) - cti); if (d < bd) { bd = d; best = i } }
        var px = tl.t2x(cti)
        if (best >= 0 && bd / Math.max(0.001, totalDuration) * (tl.width - 12) < 14) {
            var sp = separatorPoints.slice(); sp.splice(best, 1); separatorPoints = sp; tl.requestPaint(); commit()
        }
    }
    function addSplit() {
        var sp = separatorPoints.slice(); sp.push(snapTime(cti, -1, null)); separatorPoints = sp; tl.requestPaint(); commit()
    }
    function setMarkIn() { markIn = snapTime(cti, -1, "in"); selMarker = "in"; commit() }
    function setMarkOut() { markOut = snapTime(cti, -1, "out"); selMarker = "out"; commit() }

    // Add a cut: like classic add_cut — first press sets mark-in, second builds
    // the cut from [markIn, CTI].
    function addCutSmart() {
        if (markIn === null || markIn === undefined) { setMarkIn(); return }
        markOut = cti
        cutSelection()
    }
    // Invert the removed/kept ranges (classic invert_cuts).
    function invertCutsAll() {
        if (!cuts.length) return
        cuts = invertRanges(cuts, totalDuration); selCut = -1; tl.requestPaint(); commit()
    }
    // Convert the selected mark in<->out (classic convert_selected_marker).
    function convertMarker() {
        if (selMarker === "in") { markOut = markIn; markIn = totalDuration + 1; selMarker = "out" }
        else if (selMarker === "out") { markIn = markOut; markOut = -1; selMarker = "in" }
        else return
        // clamp the "cleared" sentinel back into range visually
        if (markIn > totalDuration) markIn = 0
        if (markOut < 0) markOut = totalDuration
        commit(); tl.requestPaint()
    }
    // Delete whatever is selected (classic delete_selection routing).
    function deleteSelection() {
        if (selMarker === "in") { markIn = 0; selMarker = ""; commit(); tl.requestPaint(); return }
        if (selMarker === "out") { markOut = totalDuration; selMarker = ""; commit(); tl.requestPaint(); return }
        if (selSplit >= 0 && selSplit < separatorPoints.length) {
            var sp = separatorPoints.slice(); sp.splice(selSplit, 1); separatorPoints = sp; selSplit = -1; commit(); tl.requestPaint(); return
        }
        if (selCut >= 0 && selCut < cuts.length) {
            var c = cuts.slice(); c.splice(selCut, 1); cuts = c; selCut = -1; commit(); tl.requestPaint(); return
        }
        deleteCutAtCti()
    }
    // Jump to the previous/next cut edge (classic seek_nearest_cut_edge).
    function seekCutEdge(dir) {
        var edges = []
        for (var i = 0; i < cuts.length; ++i) { edges.push(cuts[i][0]); edges.push(cuts[i][1]) }
        edges.sort(function (a, b) { return a - b })
        if (!edges.length) return
        var t = cti, target
        if (dir < 0) { var lo = edges.filter(function (v) { return v < t - 1e-4 }); target = lo.length ? lo[lo.length - 1] : edges[edges.length - 1] }
        else { var hi = edges.filter(function (v) { return v > t + 1e-4 }); target = hi.length ? hi[0] : edges[0] }
        seekTo(target)
    }
    function resetCrop() { cropTop = cropLeft = cropRight = cropBottom = 0; tl.requestPaint(); commit() }
    // Snap crop margins so the OUTPUT width/height stay even (chroma-phase safe),
    // mirroring the classic _normalize_crop_margins_even applied on confirm.
    function evenMargins(t, l, r, b) {
        if ((t + l + r + b) <= 0) return [t, l, r, b]
        var ow = sourceW - l - r, oh = sourceH - t - b
        if (ow % 2 !== 0) { if (sourceW - l - (r + 1) >= 2) r += 1; else if (sourceW - (l + 1) - r >= 2) l += 1 }
        if (oh % 2 !== 0) { if (sourceH - t - (b + 1) >= 2) b += 1; else if (sourceH - (t + 1) - b >= 2) t += 1 }
        return [t, l, r, b]
    }

    function buildResult() {
        var m = evenMargins(cropTop, cropLeft, cropRight, cropBottom)
        return JSON.stringify({ status: "ok", margins: m,
            keep_ranges: keepRangesForResult(), cuts_applied: cuts.length > 0,
            separator_points: separatorPoints,
            speed: speed, reverse: reverse, include_audio: includeAudio })
    }

    // ===================== LAYOUT =====================
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        // ---- Header ----
        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 10
                Label { text: "FFmWiz  •  Unified Video Editor"; color: win.col("accent_text", "#7db3ff"); font.pixelSize: 15; font.bold: true }
                Label {
                    // The classic engine puts the source filename here. This one showed
                    // only a count, so on a join there was nothing on screen saying WHICH
                    // timeline you were editing.
                    text: segs.length > 1 ? (segs.length + " joined videos  \u2022  " + (segs[0].name || ""))
                                          : (segs.length ? segs[0].name : "1 video")
                    color: win.col("text_mute", "#8891b4"); font.pixelSize: 12
                    elide: Text.ElideMiddle; Layout.maximumWidth: 440
                }
                Label { text: win.notice; visible: win.notice !== ""; color: win.col("danger_text", "#ff7b72"); font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 520 }
                Item { Layout.fillWidth: true }
                PadButton { text: "↶ Undo"; implicitWidth: 92; enabled: histUndo.length > 0; onClicked: doUndo() }
                PadButton { text: "↷ Redo"; implicitWidth: 92; enabled: histRedo.length > 0; onClicked: doRedo() }
                Label { text: "MODERN (QML)"; color: win.col("chapter_text", "#e9a8f2"); font.pixelSize: 11; font.bold: true }
            }
        }

        // ---- Main: resizable left controls | center preview/timeline ----
        SplitView {
            id: mainSplit
            Layout.fillWidth: true
            Layout.fillHeight: true
            orientation: Qt.Horizontal

            // ----- Left control column -----
            Card {
                id: leftPanel
                SplitView.preferredWidth: 340
                SplitView.minimumWidth: 300
                ScrollView {
                    id: leftScroll
                    anchors.fill: parent
                    anchors.margins: 12
                    contentWidth: availableWidth
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                    ColumnLayout {
                        width: leftScroll.availableWidth
                        spacing: 16

                        SectionLabel { text: "CROP (pixels)"; Layout.bottomMargin: win.sp1 }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2; rowSpacing: 8; columnSpacing: 10
                            CropField { Layout.fillWidth: true; label: "T"; maxv: sourceH; v: cropTop; onEdited: (value) => { cropTop = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "B"; maxv: sourceH; v: cropBottom; onEdited: (value) => { cropBottom = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "L"; maxv: sourceW; v: cropLeft; onEdited: (value) => { cropLeft = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "R"; maxv: sourceW; v: cropRight; onEdited: (value) => { cropRight = value; commit() } }
                        }
                        Toggle { text: "Edit crop on preview"; checked: cropEdit; onToggled: cropEdit = checked }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            PadButton { Layout.fillWidth: true; text: "Reset Crop (Ctrl+R)"; onClicked: resetCrop() }
                            Toggle { text: "Overlay (Ctrl+U)"; checked: cropOverlayOn; onToggled: cropOverlayOn = checked }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            PadButton { Layout.fillWidth: true; text: "Hand (H)"; iconSource: "../../assets/icons/lucide_hand.svg"; baseColor: win.tool === "hand" ? win.col("accent", "#3b82f6") : win.col("surface", "#21262d"); onClicked: win.tool = "hand" }
                            PadButton { Layout.fillWidth: true; text: "Zoom (Z)"; iconSource: "../../assets/icons/lucide_zoom_in.svg"; baseColor: win.tool === "zoom" ? win.col("accent", "#3b82f6") : win.col("surface", "#21262d"); onClicked: win.tool = "zoom" }
                            PadButton { Layout.preferredWidth: 62; text: "Reset"; onClicked: resetPreviewView() }
                        }
                        Label { text: "Preview zoom: " + Math.round(pvZoom * 100) + "%   \u2022   Tool: " + tool; color: win.col("text_mute", "#8891b4"); font.pixelSize: 11 }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 10
                            color: win.col("text_mute", "#8891b4")
                            text: "Crop is auto-aligned to even dimensions to keep the chroma phase correct so the video colors are not damaged."
                        }

                        Rule { Layout.topMargin: win.sp1; Layout.bottomMargin: win.sp1 }

                        SectionLabel { text: "SPEED & AUDIO"; Layout.topMargin: win.sp2; Layout.bottomMargin: win.sp1 }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Label { text: "Speed"; color: win.col("text", "#e8edfb") }
                            Picker {
                                id: speedBox
                                Layout.fillWidth: true
                                editable: true
                                model: ["25%", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%", "500%", "800%", "1000%"]
                                currentIndex: 3
                                function applyText(txt) {
                                    var raw = String(txt)
                                    var v = parseFloat(raw.replace("%", "").replace("x", "").replace("X", ""))
                                    if (isNaN(v) || v <= 0) return
                                    // "2x" or a bare value <= 10 means a factor; otherwise it is a percent.
                                    var f = (raw.toLowerCase().indexOf("x") >= 0 || v <= 10) ? v : v / 100.0
                                    speed = Math.max(0.1, Math.min(10.0, f)); commit()
                                }
                                onActivated: applyText(currentText)
                                onAccepted: applyText(editText)
                                Component.onCompleted: { var idx = model.indexOf(Math.round(speed * 100) + "%"); if (idx >= 0) currentIndex = idx; else editText = Math.round(speed * 100) + "%" }
                                WheelHandler {
                                    onWheel: (ev) => {
                                        var stepv = (ev.modifiers & Qt.ControlModifier) ? 0.25 : 0.05
                                        speed = Math.max(0.1, Math.min(10.0, speed + (ev.angleDelta.y > 0 ? stepv : -stepv)))
                                        speedBox.editText = Math.round(speed * 100) + "%"; commit()
                                    }
                                }
                            }
                        }
                        // `applyText` above takes "2x" as readily as "200%", but nothing
                        // said so, so the factor spelling the classic engine offers as its
                        // own dropdown looked missing here.
                        Label { text: "percent or factor \u2014 200% and 2x are the same"; color: win.col("text_mute", "#8891b4"); font.pixelSize: 10 }
                        Toggle { text: "Reverse video"; checked: reverse; onToggled: {
                                reverse = checked; commit()
                                if (!checked) { revActive = false; bridge.cancelReverse(); actP().playbackRate = 1.0; var s = segmentForTime(cti); loadSegment(s.index, s.local, false) }
                            } }
                        Toggle { text: "Include audio"; checked: includeAudio; enabled: hasAudio; onToggled: { includeAudio = checked; commit() } }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            // Only worth the space when there is a choice to make.
                            visible: audioTrackCount() > 1
                            Label { text: "Track"; color: win.col("text", "#e8edfb") }
                            Picker {
                                id: audioTrackBox
                                Layout.fillWidth: true
                                model: audioTrackModel()
                                currentIndex: audioTrack
                                onActivated: { audioTrack = currentIndex; applyAudioTrack() }
                            }
                        }
                        Label {
                            visible: audioTrackCount() > 1
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 10
                            color: win.col("text_mute", "#8891b4")
                            text: "Preview only — which track is encoded stays the wizard's audio question."
                        }

                        Rule { Layout.topMargin: win.sp1; Layout.bottomMargin: win.sp1 }

                        SectionLabel { text: "CUTS & SPLIT"; Layout.topMargin: win.sp2; Layout.bottomMargin: win.sp1 }
                        Toggle { text: "Magnetic snapping"; checked: snapEnabled; onToggled: snapEnabled = checked }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; rowSpacing: win.sp2; columnSpacing: win.sp2
                            PadButton { Layout.fillWidth: true; text: "Mark In (I)"; baseColor: win.col("marker_in", "#2ddc7f"); hoverColor: win.col("marker_in_hover", "#4ee89a"); pressedColor: win.col("marker_in_pressed", "#1fa860"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: setMarkIn() }
                            PadButton { Layout.fillWidth: true; text: "Mark Out (O)"; baseColor: win.col("marker_out", "#d29922"); hoverColor: win.col("marker_out_hover", "#e8b13c"); pressedColor: win.col("marker_out_pressed", "#a8760f"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: setMarkOut() }
                            PadButton { Layout.fillWidth: true; text: "Cut Selection (A)"; baseColor: win.col("danger_cut", "#7f123f"); hoverColor: win.col("danger_cut_hover", "#a51b55"); pressedColor: win.col("danger_cut_pressed", "#5e0d2e"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: cutSelection() }
                            PadButton { Layout.fillWidth: true; text: "Delete Cut"; baseColor: win.col("danger_cut", "#7f123f"); hoverColor: win.col("danger_cut_hover", "#a51b55"); pressedColor: win.col("danger_cut_pressed", "#5e0d2e"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteCutAtCti() }
                            PadButton { Layout.fillWidth: true; text: "Add Split (S)"; baseColor: win.col("accent", "#3b82f6"); hoverColor: win.col("accent_hover", "#5b9bff"); pressedColor: win.col("accent_pressed", "#2563eb"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: addSplit() }
                            PadButton { Layout.fillWidth: true; text: "Del Split"; baseColor: win.col("danger_alt", "#643618"); hoverColor: win.col("danger_alt_hover", "#8a4a1f"); pressedColor: win.col("danger_alt_pressed", "#4d2812"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteSplitAtCti() }
                        }
                        PadButton { Layout.fillWidth: true; text: "Clear Cuts"; baseColor: win.col("danger", "#a40e26"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: { cuts = []; selCut = -1; tl.requestPaint(); commit() } }
                        // One column, not two: these four labels carry their shortcut and
                        // a half-width button elides them to "Invert Cuts (Ctrl+S...",
                        // which is worse than printing no shortcut at all.
                        GridLayout {
                            Layout.fillWidth: true; columns: 1; rowSpacing: win.sp2; columnSpacing: win.sp2
                            PadButton { Layout.fillWidth: true; text: "Invert Cuts (Ctrl+Shift+I)"; baseColor: win.col("purple", "#7c3aed"); hoverColor: win.col("purple_hover", "#8b5cf6"); pressedColor: win.col("purple_pressed", "#6d28d9"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: invertCutsAll() }
                            PadButton { Layout.fillWidth: true; enabled: selMarker !== ""
                                // Disabled it reads "Select Marker": the classic engine
                                // relabels the same button, which is how you learn the
                                // action needs a selected mark first.
                                text: selMarker !== "" ? "Convert In/Out (Ctrl+I)" : "Select Marker"
                                onClicked: convertMarker() }
                            PadButton { Layout.fillWidth: true; text: "Delete Selected (Del)"; baseColor: win.col("danger", "#a40e26"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteSelection() }
                            PadButton { Layout.fillWidth: true; text: "Prev/Next edge (Ctrl+Alt+\u2190/\u2192)"; onClicked: seekCutEdge(1) }
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: {
                                var ow = sourceW - cropLeft - cropRight, oh = sourceH - cropTop - cropBottom
                                var cropTxt = (cropTop + cropLeft + cropRight + cropBottom) > 0 ? ("Crop \u2192 " + ow + "\u00d7" + oh) : "No crop"
                                return cropTxt + "  \u2022  Speed " + Math.round(speed * 100) + "%" + (reverse ? "  \u2022  Reversed" : "")
                                    + "\n" + cuts.length + " cut(s) \u2022 " + separatorPoints.length + " split(s) \u2022 " + chapters.length + " chapter(s)"
                                    + "\nKept: " + fmt(keepTotal()) + " of " + fmt(totalDuration)
                                    + "\nIn " + (markIn > 0 ? fmt(markIn) : "--")
                                    + "   \u2022   Out " + (markOut < totalDuration ? fmt(markOut) : "--")
                            }
                            color: win.col("marker_in", "#2ddc7f"); font.pixelSize: 11
                        }

                        Item { Layout.fillHeight: true }
                    }
                }
            }

            // ----- Center: preview + timeline + transport -----
            ColumnLayout {
                SplitView.fillWidth: true
                SplitView.minimumWidth: 520
                spacing: 8

                // Vertical splitter: drag the divider to resize the preview vs the
                // audio/timeline panel (parity with the classic resizable panels).
                SplitView {
                    orientation: Qt.Vertical
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                Card {
                    SplitView.fillHeight: true
                    SplitView.minimumHeight: 220
                    color: win.col("timeline_bg", "#0a0d12")
                    border.color: win.col("border_strong", "#3a4150")
                    clip: true
                    // previewArea hosts both video outputs and the crop overlay in
                    // ONE coordinate space, so contentRect maps 1:1 to overlay pixels.
                    Item {
                        id: previewArea
                        anchors.fill: parent
                        anchors.margins: 6
                        clip: true
                        // pvContent holds the video + crop overlay and is the layer
                        // the Hand/Zoom preview tools scale & pan. The pan MouseArea
                        // lives OUTSIDE it so it works in untransformed view coords.
                        Item {
                            id: pvContent
                            anchors.fill: parent
                            transformOrigin: Item.TopLeft
                            transform: [
                                Translate { x: win.pvOffX; y: win.pvOffY },
                                Scale { xScale: win.pvZoom; yScale: win.pvZoom }
                            ]
                        // Two stacked outputs for double-buffered seamless joins.
                        // PreserveAspectFit keeps each segment's native aspect ratio
                        // (black bars instead of stretching when dimensions differ).
                        VideoOutput {
                            id: voA
                            anchors.fill: parent
                            fillMode: VideoOutput.PreserveAspectFit
                            visible: win.activeAB === 0
                        }
                        VideoOutput {
                            id: voB
                            anchors.fill: parent
                            fillMode: VideoOutput.PreserveAspectFit
                            visible: win.activeAB === 1
                        }
                        // Interactive crop overlay: outline + draggable edge handles.
                        Item {
                            id: cropOverlay
                            anchors.fill: parent
                            visible: ready && cropOverlayOn && (cropEdit || (cropTop + cropLeft + cropRight + cropBottom) > 0)
                            property rect cr: (win.activeAB === 0 ? voA.contentRect : voB.contentRect)
                            property real rx: cr.x + cr.width * (cropLeft / Math.max(1, sourceW))
                            property real ry: cr.y + cr.height * (cropTop / Math.max(1, sourceH))
                            property real rw: cr.width * Math.max(0, (sourceW - cropLeft - cropRight)) / Math.max(1, sourceW)
                            property real rh: cr.height * Math.max(0, (sourceH - cropTop - cropBottom)) / Math.max(1, sourceH)
                            property color handleCol: win.col("accent", "#3b82f6")

                            // Dim the area OUTSIDE the crop rectangle (4 panels) so the
                            // kept region stands out, like a professional crop tool.
                            Rectangle { color: "#000000"; opacity: 0.5
                                x: cropOverlay.cr.x; y: cropOverlay.cr.y
                                width: cropOverlay.cr.width; height: Math.max(0, cropOverlay.ry - cropOverlay.cr.y) }
                            Rectangle { color: "#000000"; opacity: 0.5
                                x: cropOverlay.cr.x; y: cropOverlay.ry + cropOverlay.rh
                                width: cropOverlay.cr.width
                                height: Math.max(0, (cropOverlay.cr.y + cropOverlay.cr.height) - (cropOverlay.ry + cropOverlay.rh)) }
                            Rectangle { color: "#000000"; opacity: 0.5
                                x: cropOverlay.cr.x; y: cropOverlay.ry
                                width: Math.max(0, cropOverlay.rx - cropOverlay.cr.x); height: cropOverlay.rh }
                            Rectangle { color: "#000000"; opacity: 0.5
                                x: cropOverlay.rx + cropOverlay.rw; y: cropOverlay.ry
                                width: Math.max(0, (cropOverlay.cr.x + cropOverlay.cr.width) - (cropOverlay.rx + cropOverlay.rw))
                                height: cropOverlay.rh }

                            // Crisp thin crop border.
                            Rectangle {
                                color: "transparent"; border.color: "#ffffff"; border.width: 1; antialiasing: true
                                x: cropOverlay.rx; y: cropOverlay.ry; width: cropOverlay.rw; height: cropOverlay.rh
                            }
                            // Rule-of-thirds guides (only while editing).
                            Repeater {
                                model: cropEdit ? 2 : 0
                                Rectangle { color: "#ffffff"; opacity: 0.25; width: 1; antialiasing: true
                                    height: cropOverlay.rh; y: cropOverlay.ry
                                    x: cropOverlay.rx + cropOverlay.rw * (index + 1) / 3 }
                            }
                            Repeater {
                                model: cropEdit ? 2 : 0
                                Rectangle { color: "#ffffff"; opacity: 0.25; height: 1; antialiasing: true
                                    width: cropOverlay.rw; x: cropOverlay.rx
                                    y: cropOverlay.ry + cropOverlay.rh * (index + 1) / 3 }
                            }

                            // Edge affordance bars + corner dots (VISUAL ONLY). All
                            // mouse interaction is handled by the single previewMouse
                            // layer below (classic-style hit-testing).
                            Rectangle { visible: cropEdit; antialiasing: true; radius: 2.5; color: cropOverlay.handleCol
                                width: Math.min(40, cropOverlay.rw * 0.5); height: 5
                                x: cropOverlay.rx + (cropOverlay.rw - width) / 2; y: cropOverlay.ry - height / 2 }
                            Rectangle { visible: cropEdit; antialiasing: true; radius: 2.5; color: cropOverlay.handleCol
                                width: Math.min(40, cropOverlay.rw * 0.5); height: 5
                                x: cropOverlay.rx + (cropOverlay.rw - width) / 2; y: cropOverlay.ry + cropOverlay.rh - height / 2 }
                            Rectangle { visible: cropEdit; antialiasing: true; radius: 2.5; color: cropOverlay.handleCol
                                width: 5; height: Math.min(40, cropOverlay.rh * 0.5)
                                x: cropOverlay.rx - width / 2; y: cropOverlay.ry + (cropOverlay.rh - height) / 2 }
                            Rectangle { visible: cropEdit; antialiasing: true; radius: 2.5; color: cropOverlay.handleCol
                                width: 5; height: Math.min(40, cropOverlay.rh * 0.5)
                                x: cropOverlay.rx + cropOverlay.rw - width / 2; y: cropOverlay.ry + (cropOverlay.rh - height) / 2 }
                            Rectangle { visible: cropEdit; width: 12; height: 12; radius: 2; antialiasing: true
                                color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2
                                x: cropOverlay.rx - 6; y: cropOverlay.ry - 6 }
                            Rectangle { visible: cropEdit; width: 12; height: 12; radius: 2; antialiasing: true
                                color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2
                                x: cropOverlay.rx + cropOverlay.rw - 6; y: cropOverlay.ry - 6 }
                            Rectangle { visible: cropEdit; width: 12; height: 12; radius: 2; antialiasing: true
                                color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2
                                x: cropOverlay.rx - 6; y: cropOverlay.ry + cropOverlay.rh - 6 }
                            Rectangle { visible: cropEdit; width: 12; height: 12; radius: 2; antialiasing: true
                                color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2
                                x: cropOverlay.rx + cropOverlay.rw - 6; y: cropOverlay.ry + cropOverlay.rh - 6 }

                            // (edge/corner interaction handled by previewMouse below)
                            // (corner interaction handled by previewMouse below)
                        }
                        }
                        // Single classic-style interaction layer (parity with the
                        // classic CropView): hit-test crop handles first, otherwise
                        // apply the Hand/Zoom tool. Hover ONLY updates the cursor, so
                        // moving the mouse never resizes the crop.
                        MouseArea {
                            id: previewMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.LeftButton
                            property string activeHandle: ""
                            property string hoverHandle: ""
                            property bool panning: false
                            property bool dragged: false
                            property real lastX: 0
                            property real lastY: 0
                            property int oL: 0
                            property int oR: 0
                            property int oT: 0
                            property int oB: 0
                            property real mPressX: 0
                            property real mPressY: 0

                            function cropScreen() {
                                var z = win.pvZoom
                                return Qt.rect(z * cropOverlay.rx + win.pvOffX, z * cropOverlay.ry + win.pvOffY,
                                               z * cropOverlay.rw, z * cropOverlay.rh)
                            }
                            function srcX(mx) { var lx = (mx - win.pvOffX) / win.pvZoom; return Math.round((lx - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW) }
                            function srcY(my) { var ly = (my - win.pvOffY) / win.pvZoom; return Math.round((ly - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH) }
                            function insideCrop(mx, my) { var r = cropScreen(); return mx > r.x && mx < r.x + r.width && my > r.y && my < r.y + r.height }
                            function hitHandle(mx, my) {
                                if (!cropEdit) return ""
                                var r = cropScreen()
                                var x0 = r.x, y0 = r.y, x1 = r.x + r.width, y1 = r.y + r.height
                                var c = 22, t = 14
                                if (Math.abs(mx - x0) <= c && Math.abs(my - y0) <= c) return "nw"
                                if (Math.abs(mx - x1) <= c && Math.abs(my - y0) <= c) return "ne"
                                if (Math.abs(mx - x0) <= c && Math.abs(my - y1) <= c) return "sw"
                                if (Math.abs(mx - x1) <= c && Math.abs(my - y1) <= c) return "se"
                                if (Math.abs(my - y0) <= t && mx >= x0 - t && mx <= x1 + t) return "n"
                                if (Math.abs(my - y1) <= t && mx >= x0 - t && mx <= x1 + t) return "s"
                                if (Math.abs(mx - x0) <= t && my >= y0 - t && my <= y1 + t) return "w"
                                if (Math.abs(mx - x1) <= t && my >= y0 - t && my <= y1 + t) return "e"
                                return ""
                            }
                            function applyResize(h, mx, my) {
                                var sx = srcX(mx), sy = srcY(my)
                                if (h.indexOf("w") >= 0) cropLeft = Math.max(0, Math.min(sourceW - cropRight - 10, sx))
                                if (h.indexOf("e") >= 0) cropRight = Math.max(0, Math.min(sourceW - cropLeft - 10, sourceW - sx))
                                if (h.indexOf("n") >= 0) cropTop = Math.max(0, Math.min(sourceH - cropBottom - 10, sy))
                                if (h.indexOf("s") >= 0) cropBottom = Math.max(0, Math.min(sourceH - cropTop - 10, sourceH - sy))
                            }

                            cursorShape: {
                                var h = (pressed && activeHandle !== "") ? activeHandle : hoverHandle
                                if (h === "n" || h === "s") return Qt.SizeVerCursor
                                if (h === "e" || h === "w") return Qt.SizeHorCursor
                                if (h === "nw" || h === "se") return Qt.SizeFDiagCursor
                                if (h === "ne" || h === "sw") return Qt.SizeBDiagCursor
                                if (h === "move") return Qt.SizeAllCursor
                                if (win.tool === "zoom") return Qt.CrossCursor
                                return pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                            }
                            onExited: hoverHandle = ""
                            onPositionChanged: (m) => {
                                if (!pressed) {
                                    var hh = hitHandle(m.x, m.y)
                                    if (hh === "" && (m.modifiers & Qt.ControlModifier) && insideCrop(m.x, m.y)) hh = "move"
                                    hoverHandle = hh
                                    return
                                }
                                dragged = true
                                if (activeHandle === "move") {
                                    var dvx = srcX(m.x) - srcX(mPressX)
                                    var dvy = srcY(m.y) - srcY(mPressY)
                                    var nL = oL + dvx, nR = oR - dvx
                                    if (nL < 0) { nR += nL; nL = 0 }
                                    if (nR < 0) { nL += nR; nR = 0 }
                                    var nT = oT + dvy, nB = oB - dvy
                                    if (nT < 0) { nB += nT; nT = 0 }
                                    if (nB < 0) { nT += nB; nB = 0 }
                                    cropLeft = Math.max(0, nL); cropRight = Math.max(0, nR)
                                    cropTop = Math.max(0, nT); cropBottom = Math.max(0, nB)
                                } else if (activeHandle !== "") {
                                    applyResize(activeHandle, m.x, m.y)
                                } else if (panning) {
                                    win.pvOffX += (m.x - lastX); win.pvOffY += (m.y - lastY)
                                    lastX = m.x; lastY = m.y; win.clampPan()
                                }
                            }
                            onPressed: (m) => {
                                dragged = false; mPressX = m.x; mPressY = m.y
                                var h = hitHandle(m.x, m.y)
                                if (h !== "") {
                                    activeHandle = h
                                } else if ((m.modifiers & Qt.ControlModifier) && insideCrop(m.x, m.y)) {
                                    activeHandle = "move"; oL = cropLeft; oR = cropRight; oT = cropTop; oB = cropBottom
                                } else if (win.tool === "hand") {
                                    panning = true; lastX = m.x; lastY = m.y
                                }
                            }
                            onReleased: (m) => {
                                if (activeHandle !== "") commit()
                                else if (win.tool === "zoom" && !dragged) win.pvZoomAt((m.modifiers & Qt.AltModifier) ? (1.0 / 1.25) : 1.25, m.x, m.y)
                                activeHandle = ""; panning = false
                                hoverHandle = hitHandle(m.x, m.y)
                            }
                            onDoubleClicked: win.resetPreviewView()
                            onWheel: (w) => win.pvZoomAt(w.angleDelta.y > 0 ? 1.25 : (1.0 / 1.25), w.x, w.y)
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: actP().mediaStatus === MediaPlayer.NoMedia || actP().mediaStatus === MediaPlayer.LoadingMedia
                            text: "Loading preview…"; color: win.col("text_mute", "#8891b4"); font.pixelSize: 14
                        }
                    }
                }

                Card {
                    SplitView.preferredHeight: 140
                    SplitView.minimumHeight: 72
                    color: win.col("timeline_bg", "#0a0d12")
                    border.color: win.col("border_strong", "#3a4150")
                    Canvas {
                        id: tl
                        anchors.fill: parent; anchors.margins: 8
                        property real pad: 6
                        onWidthChanged: win.scheduleWaveform()
                        // Zoom/pan-aware mapping: the visible window is [viewStart, viewStart+span].
                        function t2x(t) { var sp = win.viewSpan(); return pad + ((t - win.viewStart) / Math.max(0.001, sp)) * (width - 2 * pad) }
                        function x2t(x) { var sp = win.viewSpan(); return Math.max(0, Math.min(totalDuration, win.viewStart + (x - pad) / Math.max(1, (width - 2 * pad)) * sp)) }
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset()
                            var midY = height * 0.52
                            ctx.strokeStyle = win.col("timeline_track", "#1c2128"); ctx.lineWidth = 1
                            ctx.beginPath(); ctx.moveTo(pad, midY); ctx.lineTo(width - pad, midY); ctx.stroke()
                            // Time ruler: gridlines + absolute timecode labels for the
                            // visible window, at a "nice" step so labels never overlap
                            // (parity with the classic editor's ruler).
                            var span = win.viewSpan()
                            var niceSteps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
                            var targetTicks = Math.max(2, Math.floor((width - 2 * pad) / 90))
                            var rawStep = span / targetTicks
                            var rstep = niceSteps[niceSteps.length - 1]
                            for (var ni = 0; ni < niceSteps.length; ++ni) { if (niceSteps[ni] >= rawStep) { rstep = niceSteps[ni]; break } }
                            ctx.font = "9px 'Consolas'"; ctx.textAlign = "left"
                            var t0r = Math.ceil(win.viewStart / rstep) * rstep
                            for (var tr = t0r; tr <= win.viewStart + span + 1e-6; tr += rstep) {
                                var trx = t2x(tr)
                                if (trx < pad - 1 || trx > width - pad + 1) continue
                                ctx.strokeStyle = win.col("timeline_track", "#1c2128"); ctx.lineWidth = 1
                                ctx.globalAlpha = 0.45; ctx.beginPath(); ctx.moveTo(trx, 12); ctx.lineTo(trx, height - 4); ctx.stroke(); ctx.globalAlpha = 1.0
                                ctx.fillStyle = win.col("tick_lo", "#7d8590"); ctx.fillText(win.fmtShort(tr), trx + 2, 9)
                            }
                            var xi = t2x(markIn), xo = t2x(markOut)
                            ctx.globalAlpha = 0.4; ctx.fillStyle = win.col("accent_dim", "#1b3468")
                            ctx.fillRect(xi, 6, Math.max(0, xo - xi), height - 12); ctx.globalAlpha = 1.0
                            // Audio waveform: per-column min/max for the CURRENT
                            // viewport (fetched from the backend on viewport change).
                            // The pair values are already in -1..1 vs int16 full
                            // scale, so quiet stays quiet and loud stays loud.
                            var wv = win.wf
                            if (wv && wv.length > 1) {
                                var halfMax = Math.min(midY - 4, height - 4 - midY)
                                var inner = width - 2 * pad
                                var nb = wv.length
                                ctx.strokeStyle = win.colA("waveform", "#22d3ee", 0.9); ctx.lineWidth = 1
                                for (var w = 0; w < nb; ++w) {
                                    var wx = pad + (w + 0.5) / nb * inner
                                    var mn = wv[w][0], mx = wv[w][1]
                                    var yTop = midY - mx * halfMax
                                    var yBot = midY - mn * halfMax
                                    if (yBot - yTop < 0.8) { yTop -= 0.4; yBot += 0.4 }
                                    ctx.beginPath(); ctx.moveTo(wx, yTop); ctx.lineTo(wx, yBot); ctx.stroke()
                                }
                            } else if (win.hasAudio) {
                                ctx.fillStyle = win.col("text_subtle", "#4e5680"); ctx.font = "10px 'Segoe UI'"; ctx.textAlign = "center"
                                ctx.fillText("decoding waveform…", width / 2, midY - 2)
                            }
                            // Cut (removed) ranges: translucent red over the waveform.
                            var cz = win.cuts
                            for (var c1 = 0; c1 < cz.length; ++c1) {
                                var cx0 = t2x(cz[c1][0]), cx1 = t2x(cz[c1][1])
                                ctx.fillStyle = win.colA("cut_red", "#f85149", 0.32)
                                ctx.fillRect(cx0, 6, Math.max(1, cx1 - cx0), height - 12)
                                ctx.strokeStyle = (c1 === win.selCut) ? "rgba(255,255,255,0.95)" : win.colA("cut_red", "#f85149", 0.9); ctx.lineWidth = (c1 === win.selCut) ? 2 : 1
                                ctx.strokeRect(cx0, 6, Math.max(1, cx1 - cx0), height - 12)
                            }
                            ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "center"
                            for (var i = 1; i < segs.length; ++i) {
                                var bx = t2x(segs[i].start)
                                ctx.strokeStyle = win.colA("segment_boundary", "#e8b278", 0.78); ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                                ctx.beginPath(); ctx.moveTo(bx, 4); ctx.lineTo(bx, height - 4); ctx.stroke(); ctx.setLineDash([])
                                ctx.fillStyle = win.colA("segment_boundary", "#e8b278", 0.92); ctx.fillText(segs[i].name, bx, 13)
                            }
                            // Chapters: dashed purple markers with titles (parity with classic).
                            for (var ch = 0; ch < chapters.length; ++ch) {
                                var chx = t2x(Number(chapters[ch].t))
                                if (chx < pad - 1 || chx > width - pad + 1) continue
                                ctx.strokeStyle = win.col("chapter_text", "#e9a8f2"); ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                                ctx.beginPath(); ctx.moveTo(chx, height - 16); ctx.lineTo(chx, height - 4); ctx.stroke(); ctx.setLineDash([])
                                ctx.fillStyle = win.col("chapter_text", "#e9a8f2"); ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "left"
                                ctx.fillText(chapters[ch].title, chx + 3, height - 6)
                            }
                            function vbar(x, c) { ctx.strokeStyle = c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, 6); ctx.lineTo(x, height - 6); ctx.stroke() }
                            vbar(xi, win.col("marker_in", "#2ddc7f")); vbar(xo, win.col("marker_out", "#d29922"))
                            for (var k = 0; k < separatorPoints.length; ++k) {
                                var sx = t2x(Number(separatorPoints[k]))
                                ctx.strokeStyle = (k === win.selSplit) ? win.col("split_marker_sel", "#7dd3fc") : win.col("split_marker", "#38bdf8"); ctx.lineWidth = (k === win.selSplit) ? 3 : 2
                                ctx.beginPath(); ctx.moveTo(sx, 6); ctx.lineTo(sx, height - 6); ctx.stroke()
                            }
                            // NOTE: the CTI/playhead is drawn as a separate overlay
                            // item (below) so playback moves it WITHOUT repainting
                            // this canvas / regenerating the waveform.
                        }
                        MouseArea {
                            id: tlMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            onExited: hov.hx = -1
                            property string dragKind: ""   // in|out|split|cutS|cutE|""
                            property int dragIdx: -1
                            // Pick the nearest draggable handle within ~7px of x.
                            function pick(x) {
                                var best = { kind: "", idx: -1, d: 7 }
                                function consider(k, idx, t) { var d = Math.abs(tl.t2x(t) - x); if (d <= best.d) best = { kind: k, idx: idx, d: d } }
                                consider("in", -1, markIn); consider("out", -1, markOut)
                                for (var i = 0; i < separatorPoints.length; ++i) consider("split", i, Number(separatorPoints[i]))
                                for (var c = 0; c < cuts.length; ++c) { consider("cutS", c, cuts[c][0]); consider("cutE", c, cuts[c][1]) }
                                return best
                            }
                            onPressed: (m) => {
                                var p = pick(m.x); dragKind = p.kind; dragIdx = p.idx
                                if (p.kind === "in") { selMarker = "in"; selSplit = -1; selCut = -1 }
                                else if (p.kind === "out") { selMarker = "out"; selSplit = -1; selCut = -1 }
                                else if (p.kind === "split") { selSplit = p.idx; selCut = -1; selMarker = "" }
                                else if (p.kind === "cutS" || p.kind === "cutE") { selCut = p.idx; selSplit = -1; selMarker = "" }
                                else { selMarker = ""; selSplit = -1; selCut = -1; seekTo(snapTime(tl.x2t(m.x), -1, null)) }
                            }
                            onPositionChanged: (m) => {
                                hov.hx = m.x   // hover marker + timestamp follows the cursor
                                if (!pressed) return
                                if (dragKind === "") { seekTo(snapTime(tl.x2t(m.x), -1, null)); return }
                                var t = snapTime(tl.x2t(m.x), dragKind === "split" ? dragIdx : -1,
                                                 dragKind === "in" ? "in" : (dragKind === "out" ? "out" : null))
                                if (dragKind === "in") markIn = t
                                else if (dragKind === "out") markOut = t
                                else if (dragKind === "split") { var sp = separatorPoints.slice(); sp[dragIdx] = t; separatorPoints = sp }
                                else if (dragKind === "cutS") { var c = cuts.slice(); c[dragIdx] = [Math.min(t, c[dragIdx][1] - 0.02), c[dragIdx][1]]; cuts = c }
                                else if (dragKind === "cutE") { var c2 = cuts.slice(); c2[dragIdx] = [c2[dragIdx][0], Math.max(t, c2[dragIdx][0] + 0.02)]; cuts = c2 }
                                tl.requestPaint()
                            }
                            onReleased: {
                                if (dragKind !== "") {
                                    if (dragKind === "cutS" || dragKind === "cutE") { cuts = normRanges(cuts); selCut = -1 }
                                    if (dragKind === "split") separatorPoints = separatorPoints.slice().sort(function (a, b) { return a - b })
                                    commit()
                                }
                                dragKind = ""; dragIdx = -1
                            }
                            // Wheel zooms the timeline around the cursor time.
                            onWheel: (wheel) => {
                                var tUnder = tl.x2t(wheel.x)
                                var frac = (wheel.x - tl.pad) / Math.max(1, (tl.width - 2 * tl.pad))
                                win.zoomAt(wheel.angleDelta.y > 0 ? 1.25 : 0.8, tUnder, frac)
                                tl.requestPaint()
                            }
                        }
                        // CTI / playhead overlay — bound to cti, so playback moves
                        // it WITHOUT repainting the waveform canvas (the heavy paint
                        // only runs on edits/zoom/pan, never on a playback tick).
                        Rectangle {
                            width: 2; color: win.col("playhead", "#ff4d55")
                            y: 0; height: tl.height
                            x: Math.max(0, Math.min(tl.width, tl.t2x(win.cti))) - 1
                            visible: win.ready && win.cti >= win.viewStart - 1e-6
                                     && win.cti <= win.viewStart + win.viewSpan() + 1e-6
                        }
                        // Hover marker + timestamp tooltip following the cursor.
                        Item {
                            id: hov
                            anchors.fill: parent
                            property real hx: -1
                            Rectangle {
                                visible: hov.hx >= 0; width: 1; x: hov.hx; y: 0; height: tl.height
                                color: win.col("tick_lo", "#7d8590"); opacity: 0.7
                            }
                            Rectangle {
                                visible: hov.hx >= 0
                                color: win.col("surface", "#21262d"); border.color: win.col("border_strong", "#3a4150")
                                radius: 4; height: 16; width: hovLbl.implicitWidth + 10
                                x: Math.max(0, Math.min(tl.width - width, hov.hx - width / 2)); y: 2
                                Label {
                                    id: hovLbl; anchors.centerIn: parent
                                    color: win.col("text", "#e8edfb"); font.pixelSize: 10; font.family: "Consolas"
                                    text: fmt(tl.x2t(hov.hx))
                                }
                            }
                        }
                    }
                    Connections { target: win; function onMarkInChanged() { tl.requestPaint() } }
                    Connections { target: win; function onMarkOutChanged() { tl.requestPaint() } }
                    Connections { target: win; function onCutsChanged() { tl.requestPaint() } }
                    Connections { target: win; function onReadyChanged() { tl.requestPaint(); win.scheduleWaveform() } }
                    Connections { target: win; function onZoomChanged() { tl.requestPaint(); win.scheduleWaveform() } }
                    Connections { target: win; function onViewStartChanged() { tl.requestPaint(); win.scheduleWaveform() } }
                    Connections { target: win; function onChaptersChanged() { tl.requestPaint() } }
                    Connections { target: win; function onSeparatorPointsChanged() { tl.requestPaint() } }
                    Connections { target: win; function onSelCutChanged() { tl.requestPaint() } }
                    Connections { target: win; function onSelSplitChanged() { tl.requestPaint() } }
                    // Pan bar (only when zoomed in).
                    ScrollBar {
                        id: tlScroll
                        orientation: Qt.Horizontal
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        anchors.margins: 4
                        height: 10
                        visible: win.zoom > 1.0001
                        policy: ScrollBar.AlwaysOn
                        size: 1.0 / Math.max(1, win.zoom)
                        position: win.viewStart / Math.max(0.001, totalDuration)
                        onPositionChanged: {
                            if (pressed) { win.viewStart = position * totalDuration; win.clampView() }
                        }
                    }
                }
                }

                // Transport
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    PadButton { Layout.preferredWidth: 92; text: actP().playbackState === MediaPlayer.PlayingState ? "\u275a\u275a  Pause" : "\u25b6  Play"; onClicked: togglePlay() }
                    PadButton { Layout.preferredWidth: 36; text: "\u23ee"; onClicked: seekTo(0) }                        // Home
                    PadButton { Layout.preferredWidth: 42; text: "-5s"; autoRepeat: true; onClicked: seekTo(cti - 5) }
                    PadButton { Layout.preferredWidth: 42; text: "-1s"; autoRepeat: true; onClicked: seekTo(cti - 1) }
                    PadButton { Layout.preferredWidth: 38; text: "\u25c0|"; onClicked: frameStep(-1) }                   // -1 frame
                    PadButton { Layout.preferredWidth: 38; text: "|\u25b6"; onClicked: frameStep(1) }                    // +1 frame
                    PadButton { Layout.preferredWidth: 42; text: "+1s"; autoRepeat: true; onClicked: seekTo(cti + 1) }
                    PadButton { Layout.preferredWidth: 42; text: "+5s"; autoRepeat: true; onClicked: seekTo(cti + 5) }
                    PadButton { Layout.preferredWidth: 36; text: "\u23ed"; onClicked: seekTo(totalDuration) }            // End
                    PadButton { Layout.preferredWidth: 32; text: "\u27dd"; onClicked: seekCutEdge(-1) }                  // prev cut edge
                    PadButton { Layout.preferredWidth: 32; text: "\u27de"; onClicked: seekCutEdge(1) }                   // next cut edge
                    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
                           // A FIXED height, never fillHeight: a filling child makes the
                           // whole transport row demand vertical space, and it took it
                           // from the video preview -- which collapsed to a thumbnail
                           // with the controls floating in the middle of the gap.
                           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
                           Layout.leftMargin: win.sp1; Layout.rightMargin: win.sp1 }
                    Label { text: fmt(cti) + " / " + fmt(totalDuration); color: win.col("text", "#e8edfb"); font.pixelSize: win.fsLead; font.family: "Consolas" }
                    Label { text: "f " + curFrame() + "/" + totalFrames(); color: win.col("text_mute", "#8891b4"); font.pixelSize: 10; font.family: "Consolas" }
                    Item { Layout.fillWidth: true }
                    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
                           // A FIXED height, never fillHeight: a filling child makes the
                           // whole transport row demand vertical space, and it took it
                           // from the video preview -- which collapsed to a thumbnail
                           // with the controls floating in the middle of the gap.
                           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
                           Layout.leftMargin: win.sp1; Layout.rightMargin: win.sp1 }
                    PadButton { Layout.preferredWidth: 66; text: win.muted ? "Unmute" : "Mute"; onClicked: win.muted = !win.muted }
                    Track { Layout.preferredWidth: 88; from: 0; to: 1; value: win.volume; onMoved: { win.volume = value; win.muted = false } }
                    PadButton { Layout.preferredWidth: 34; text: "\u2212"; onClicked: { win.zoomAt(0.8, cti, 0.5); tl.requestPaint() } }
                    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
                           // A FIXED height, never fillHeight: a filling child makes the
                           // whole transport row demand vertical space, and it took it
                           // from the video preview -- which collapsed to a thumbnail
                           // with the controls floating in the middle of the gap.
                           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
                           Layout.leftMargin: win.sp1; Layout.rightMargin: win.sp1 }
                    Track {
                        Layout.preferredWidth: 120
                        from: 0; to: 100
                        value: 100 * Math.log(Math.max(1, win.zoom)) / Math.log(400)
                        onMoved: {
                            win.zoom = Math.max(1, Math.pow(400, value / 100))
                            var sp = win.viewSpan(); win.viewStart = win.cti - sp / 2; win.clampView(); tl.requestPaint()
                        }
                    }
                    Label { text: (Math.round(win.zoom * 100) / 100) + "\u00d7"; color: win.col("text_mute", "#8891b4"); font.pixelSize: 11 }
                    PadButton { Layout.preferredWidth: 34; text: "+"; onClicked: { win.zoomAt(1.25, cti, 0.5); tl.requestPaint() } }
                    PadButton { Layout.preferredWidth: 46; text: "Fit"; onClicked: { win.fitZoom(); tl.requestPaint() } }
                }
            }
        }

        // ---- Guidance ----
        // Two lines the classic engine has and this one did not: what to do next,
        // and what the keys are. Everything listed is a Shortcut that actually
        // exists at the bottom of this file -- a printed key that does nothing is
        // worse than no help at all.
        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            ColumnLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                anchors.topMargin: 5; anchors.bottomMargin: 5; spacing: 2
                Label {
                    Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11
                    color: win.col("text", "#e8edfb")
                    text: {
                        if (cuts.length === 0 && separatorPoints.length === 0)
                            return "Cut ranges and Splits: none. Mark In/Out and press Cut Selection (A), or press S at the playhead to split the final output into parts."
                        return "Cut ranges: " + cuts.length + "  \u2022  Splits: " + separatorPoints.length
                             + "  \u2022  the output keeps " + fmt(keepTotal()) + " of " + fmt(totalDuration)
                             + (separatorPoints.length > 0 ? " across " + (separatorPoints.length + 1) + " parts." : ".")
                    }
                }
                Label {
                    Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10
                    color: win.col("text_mute", "#8891b4")
                    text: "Space play/pause  \u2022  I/O mark  \u2022  A cut  \u2022  S split  \u2022  Del remove  "
                        + "\u2022  Ctrl+I convert mark  \u2022  Ctrl+Shift+I invert cuts  \u2022  Ctrl+U overlay  \u2022  Ctrl+R reset crop  "
                        + "\u2022  , / . frame step  \u2022  Shift+\u2190/\u2192 5s  \u2022  Ctrl+Alt+\u2190/\u2192 cut edge  \u2022  M mute  \u2022  Ctrl+Z/Y undo/redo"
                }
            }
        }

        // ---- Footer ----
        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 56
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                PadButton { text: "Reset all"; implicitWidth: 110
                    onClicked: { cropTop = cropLeft = cropRight = cropBottom = 0; speed = 1.0; reverse = false; includeAudio = hasAudio; markIn = 0; markOut = totalDuration; cuts = []; separatorPoints = []; speedBox.currentIndex = 3; zoom = 1.0; viewStart = 0; cropEdit = false; cropOverlayOn = true; muted = false; selMarker = ""; selSplit = -1; selCut = -1; tl.requestPaint(); commit() } }
                PadButton { text: "Reset Panels"; implicitWidth: 130; onClicked: leftPanel.SplitView.preferredWidth = 340 }
                Item { Layout.fillWidth: true }
                PadButton { text: "Cancel (Esc)"; implicitWidth: 150; implicitHeight: 40; baseColor: win.col("danger", "#a40e26"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: bridge.cancel() }
                PadButton { text: "Confirm (Enter)"; implicitWidth: 180; implicitHeight: 40; baseColor: win.col("green", "#238636"); hoverColor: win.col("green_hover", "#2ea043"); pressedColor: win.col("green_pressed", "#196c2e"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: bridge.submit(buildResult()) }
            }
        }
    }

    Shortcut { sequence: "Space"; onActivated: togglePlay() }
    Shortcut { sequence: "Esc"; onActivated: bridge.cancel() }
    Shortcut { sequence: "Return"; onActivated: bridge.submit(buildResult()) }
    Shortcut { sequence: "Enter"; onActivated: bridge.submit(buildResult()) }
    Shortcut { sequence: "I"; onActivated: setMarkIn() }
    Shortcut { sequence: "O"; onActivated: setMarkOut() }
    Shortcut { sequence: "A"; onActivated: addCutSmart() }
    Shortcut { sequence: "S"; onActivated: addSplit() }
    Shortcut { sequence: "Delete"; onActivated: deleteSelection() }
    Shortcut { sequence: "M"; onActivated: win.muted = !win.muted }
    Shortcut { sequence: "Ctrl+I"; onActivated: convertMarker() }
    Shortcut { sequence: "Ctrl+O"; onActivated: convertMarker() }
    Shortcut { sequence: "Ctrl+Shift+I"; onActivated: invertCutsAll() }
    Shortcut { sequence: "Ctrl+R"; onActivated: resetCrop() }
    Shortcut { sequence: "Ctrl+U"; onActivated: cropOverlayOn = !cropOverlayOn }
    Shortcut { sequence: "Ctrl+Z"; onActivated: doUndo() }
    Shortcut { sequence: "Ctrl+Y"; onActivated: doRedo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; onActivated: doRedo() }
    Shortcut { sequence: "Home"; onActivated: seekTo(0) }
    Shortcut { sequence: "End"; onActivated: seekTo(totalDuration) }
    Shortcut { sequence: "Left"; onActivated: seekTo(cti - 1) }
    Shortcut { sequence: "Right"; onActivated: seekTo(cti + 1) }
    Shortcut { sequence: "Shift+Left"; onActivated: seekTo(cti - 5) }
    Shortcut { sequence: "Shift+Right"; onActivated: seekTo(cti + 5) }
    Shortcut { sequence: "Ctrl+Alt+Left"; onActivated: seekCutEdge(-1) }
    Shortcut { sequence: "Ctrl+Alt+Right"; onActivated: seekCutEdge(1) }
    Shortcut { sequence: ","; onActivated: frameStep(-1) }
    Shortcut { sequence: "."; onActivated: frameStep(1) }
    Shortcut { sequence: "+"; onActivated: { win.zoomAt(1.25, cti, 0.5); tl.requestPaint() } }
    Shortcut { sequence: "="; onActivated: { win.zoomAt(1.25, cti, 0.5); tl.requestPaint() } }
    Shortcut { sequence: "-"; onActivated: { win.zoomAt(0.8, cti, 0.5); tl.requestPaint() } }
    Shortcut { sequence: "H"; onActivated: win.tool = "hand" }
    Shortcut { sequence: "Z"; onActivated: win.tool = "zoom" }
    Shortcut { sequence: "Ctrl+0"; onActivated: resetPreviewView() }
    Shortcut { sequence: "Ctrl++"; onActivated: pvZoomAt(1.25, previewArea.width / 2, previewArea.height / 2) }
    Shortcut { sequence: "Ctrl+="; onActivated: pvZoomAt(1.25, previewArea.width / 2, previewArea.height / 2) }
    Shortcut { sequence: "Ctrl+-"; onActivated: pvZoomAt(0.8, previewArea.width / 2, previewArea.height / 2) }
}
