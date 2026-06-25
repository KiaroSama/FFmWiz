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
    color: col("bg", "#0d1117")

    palette.window: col("bg", "#0d1117")
    palette.windowText: col("text", "#e6edf3")
    palette.base: col("panel_alt", "#1a1f2a")
    palette.text: col("text", "#e6edf3")
    palette.button: col("surface", "#21262d")
    palette.buttonText: col("text", "#e6edf3")
    palette.highlight: col("accent", "#1f6feb")
    palette.highlightedText: "#ffffff"
    palette.mid: col("border", "#30363d")

    // ---------- Reusable styled components ----------
    component Card: Rectangle {
        radius: 10
        color: win.col("panel", "#161b22")
        border.color: win.col("border", "#30363d")
    }

    component PadButton: Button {
        id: pb
        property color baseColor: win.col("surface", "#21262d")
        property color textColor: win.col("text", "#e6edf3")
        implicitHeight: 34
        padding: 8
        hoverEnabled: true
        // Pointing-hand cursor on hover/click so buttons feel clickable.
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            radius: 7
            color: pb.down ? Qt.darker(pb.baseColor, 1.25)
                           : (pb.hovered ? Qt.lighter(pb.baseColor, 1.18) : pb.baseColor)
            border.color: win.col("border_strong", "#3a4150")
            border.width: 1
        }
        contentItem: Label {
            text: pb.text
            color: pb.enabled ? pb.textColor : win.col("text_mute", "#7d8590")
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 13
            elide: Text.ElideRight
        }
    }

    component SectionLabel: Label {
        color: win.col("accent_text", "#79b4ff")
        font.bold: true
        font.pixelSize: 12
    }

    component CropField: RowLayout {
        property string label: ""
        property int maxv: 9999
        property int v: 0
        signal edited(int value)
        spacing: 6
        Label { text: parent.label; color: win.col("text_mute", "#7d8590"); Layout.preferredWidth: 14 }
        SpinBox {
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
                list.push({ path: js[i].path, name: js[i].name || ("Video " + (i + 1)), start: off, duration: d })
                off += d
            }
            totalDuration = off
        } else {
            var d0 = Math.max(0.001, Number(req.duration) || 0)
            list.push({ path: req.input_path, name: "Video 1", start: 0, duration: d0 })
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
            var ct = Number(chs[ci].start !== undefined ? chs[ci].start : chs[ci].t)
            if (!isNaN(ct) && ct >= 0 && ct <= totalDuration)
                clist.push({ t: ct, title: String(chs[ci].title || chs[ci].name || ("Chapter " + (ci + 1))) })
        }
        chapters = clist
        var keep = req.initial_keep_ranges || []
        if (keep.length > 0) { markIn = Number(keep[0][0]) || 0; markOut = Number(keep[keep.length - 1][1]) || totalDuration }
        else { markIn = 0; markOut = totalDuration }
        // Reconstruct removed (cut) ranges from the kept ranges carried over.
        cuts = invertRanges(keep.map(function (r) { return [Number(r[0]), Number(r[1])] }), totalDuration)

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
            if (gen !== revGen || !revActive || path === "") return
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
        revWinStart = winStart; revWinEnd = winEnd
        revGen += 1
        var s = segmentForTime(Math.max(0, winEnd - 1e-3))   // map window to its segment (joins)
        var ss = Math.max(0, winStart - segs[s.index].start)
        var dur = Math.max(0.05, winEnd - winStart)
        var w = Math.max(320, Math.min(1280, Math.round(previewArea.width)))
        bridge.renderReverse(JSON.stringify({ gen: revGen, src: segs[s.index].path, ss: ss, dur: dur, width: w }))
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
            keep_ranges: keepFromCuts(), separator_points: separatorPoints,
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
                Label { text: "FFmWiz  •  Unified Video Editor"; color: win.col("accent_text", "#79b4ff"); font.pixelSize: 15; font.bold: true }
                Label { text: segs.length > 1 ? (segs.length + " joined videos") : "1 video"; color: win.col("text_mute", "#7d8590"); font.pixelSize: 12 }
                Item { Layout.fillWidth: true }
                PadButton { text: "↶ Undo"; implicitWidth: 92; enabled: histUndo.length > 0; onClicked: doUndo() }
                PadButton { text: "↷ Redo"; implicitWidth: 92; enabled: histRedo.length > 0; onClicked: doRedo() }
                Label { text: "MODERN (QML)"; color: win.col("chapter_text", "#d9bdff"); font.pixelSize: 11; font.bold: true }
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

                        SectionLabel { text: "CROP (pixels)" }
                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2; rowSpacing: 8; columnSpacing: 10
                            CropField { Layout.fillWidth: true; label: "T"; maxv: sourceH; v: cropTop; onEdited: (value) => { cropTop = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "B"; maxv: sourceH; v: cropBottom; onEdited: (value) => { cropBottom = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "L"; maxv: sourceW; v: cropLeft; onEdited: (value) => { cropLeft = value; commit() } }
                            CropField { Layout.fillWidth: true; label: "R"; maxv: sourceW; v: cropRight; onEdited: (value) => { cropRight = value; commit() } }
                        }
                        Switch { text: "Edit crop on preview"; checked: cropEdit; onToggled: cropEdit = checked }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            PadButton { Layout.fillWidth: true; text: "Reset Crop"; onClicked: resetCrop() }
                            Switch { text: "Overlay"; checked: cropOverlayOn; onToggled: cropOverlayOn = checked }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            PadButton { Layout.fillWidth: true; text: "Hand (H)"; baseColor: win.tool === "hand" ? win.col("accent", "#1f6feb") : win.col("surface", "#21262d"); onClicked: { win.tool = "hand"; win.cropEdit = false } }
                            PadButton { Layout.fillWidth: true; text: "Zoom (Z)"; baseColor: win.tool === "zoom" ? win.col("accent", "#1f6feb") : win.col("surface", "#21262d"); onClicked: { win.tool = "zoom"; win.cropEdit = false } }
                            PadButton { Layout.preferredWidth: 62; text: "Reset"; onClicked: resetPreviewView() }
                        }
                        Label { text: "Preview zoom: " + Math.round(pvZoom * 100) + "%   \u2022   Tool: " + tool; color: win.col("text_mute", "#7d8590"); font.pixelSize: 11 }

                        Rectangle { Layout.fillWidth: true; height: 1; color: win.col("border", "#30363d") }

                        SectionLabel { text: "SPEED & AUDIO" }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Label { text: "Speed"; color: win.col("text", "#e6edf3") }
                            ComboBox {
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
                        Switch { text: "Reverse video"; checked: reverse; onToggled: {
                                reverse = checked; commit()
                                if (!checked) { revActive = false; actP().playbackRate = 1.0; var s = segmentForTime(cti); loadSegment(s.index, s.local, false) }
                            } }
                        Switch { text: "Include audio"; checked: includeAudio; enabled: hasAudio; onToggled: { includeAudio = checked; commit() } }

                        Rectangle { Layout.fillWidth: true; height: 1; color: win.col("border", "#30363d") }

                        SectionLabel { text: "CUTS & SPLIT" }
                        Switch { text: "Magnetic snapping"; checked: snapEnabled; onToggled: snapEnabled = checked }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; rowSpacing: 8; columnSpacing: 8
                            PadButton { Layout.fillWidth: true; text: "Mark In (I)"; onClicked: setMarkIn() }
                            PadButton { Layout.fillWidth: true; text: "Mark Out (O)"; onClicked: setMarkOut() }
                            PadButton { Layout.fillWidth: true; text: "Cut Selection"; baseColor: win.col("danger_cut", "#7f123f"); textColor: "#ffffff"; onClicked: cutSelection() }
                            PadButton { Layout.fillWidth: true; text: "Delete Cut"; onClicked: deleteCutAtCti() }
                            PadButton { Layout.fillWidth: true; text: "Add Split"; onClicked: addSplit() }
                            PadButton { Layout.fillWidth: true; text: "Del Split"; onClicked: deleteSplitAtCti() }
                        }
                        PadButton { Layout.fillWidth: true; text: "Clear Cuts"; onClicked: { cuts = []; selCut = -1; tl.requestPaint(); commit() } }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; rowSpacing: 8; columnSpacing: 8
                            PadButton { Layout.fillWidth: true; text: "Invert Cuts"; onClicked: invertCutsAll() }
                            PadButton { Layout.fillWidth: true; text: "Convert In/Out"; onClicked: convertMarker() }
                            PadButton { Layout.fillWidth: true; text: "Delete Selected"; onClicked: deleteSelection() }
                            PadButton { Layout.fillWidth: true; text: "Prev/Next edge"; onClicked: seekCutEdge(1) }
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: {
                                var ow = sourceW - cropLeft - cropRight, oh = sourceH - cropTop - cropBottom
                                var cropTxt = (cropTop + cropLeft + cropRight + cropBottom) > 0 ? ("Crop \u2192 " + ow + "\u00d7" + oh) : "No crop"
                                return cropTxt + "  \u2022  Speed " + Math.round(speed * 100) + "%" + (reverse ? "  \u2022  Reversed" : "")
                                    + "\n" + cuts.length + " cut(s) \u2022 " + separatorPoints.length + " split(s) \u2022 " + chapters.length + " chapter(s)"
                                    + "\nKept: " + fmt(keepTotal()) + " of " + fmt(totalDuration)
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
                            property color handleCol: win.col("accent", "#1f6feb")

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

                            // Drag the WHOLE crop box (keeps its size). Declared before
                            // the edge/corner handles so those win along the borders.
                            MouseArea {
                                visible: cropEdit; hoverEnabled: true; cursorShape: Qt.SizeAllCursor
                                x: cropOverlay.rx + 14; y: cropOverlay.ry + 14
                                width: Math.max(0, cropOverlay.rw - 28); height: Math.max(0, cropOverlay.rh - 28)
                                property real sx: 0; property real sy: 0
                                property int oL: 0; property int oR: 0; property int oT: 0; property int oB: 0
                                onPressed: (mouse) => { var p = mapToItem(cropOverlay, mouse.x, mouse.y); sx = p.x; sy = p.y; oL = cropLeft; oR = cropRight; oT = cropTop; oB = cropBottom }
                                onPositionChanged: (mouse) => {
                                    var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                    var dvx = Math.round((p.x - sx) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                    var dvy = Math.round((p.y - sy) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                    var nL = oL + dvx, nR = oR - dvx
                                    if (nL < 0) { nR += nL; nL = 0 }
                                    if (nR < 0) { nL += nR; nR = 0 }
                                    var nT = oT + dvy, nB = oB - dvy
                                    if (nT < 0) { nB += nT; nT = 0 }
                                    if (nB < 0) { nT += nB; nB = 0 }
                                    cropLeft = Math.max(0, nL); cropRight = Math.max(0, nR)
                                    cropTop = Math.max(0, nT); cropBottom = Math.max(0, nB)
                                }
                                onReleased: commit()
                            }

                            // Top edge: full-width grab strip + centered affordance bar.
                            Item {
                                visible: cropEdit
                                x: cropOverlay.rx; y: cropOverlay.ry - 10; width: cropOverlay.rw; height: 20
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeVerCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropTop = Math.max(0, Math.min(sourceH - cropBottom - 10, v))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; antialiasing: true
                                    width: Math.min(40, parent.width * 0.5); height: 5; radius: 2.5; color: cropOverlay.handleCol }
                            }
                            // Bottom edge
                            Item {
                                visible: cropEdit
                                x: cropOverlay.rx; y: cropOverlay.ry + cropOverlay.rh - 10; width: cropOverlay.rw; height: 20
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeVerCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropBottom = Math.max(0, Math.min(sourceH - cropTop - 10, sourceH - v))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; antialiasing: true
                                    width: Math.min(40, parent.width * 0.5); height: 5; radius: 2.5; color: cropOverlay.handleCol }
                            }
                            // Left edge
                            Item {
                                visible: cropEdit
                                x: cropOverlay.rx - 10; y: cropOverlay.ry; width: 20; height: cropOverlay.rh
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeHorCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        cropLeft = Math.max(0, Math.min(sourceW - cropRight - 10, v))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; antialiasing: true
                                    width: 5; height: Math.min(40, parent.height * 0.5); radius: 2.5; color: cropOverlay.handleCol }
                            }
                            // Right edge
                            Item {
                                visible: cropEdit
                                x: cropOverlay.rx + cropOverlay.rw - 10; y: cropOverlay.ry; width: 20; height: cropOverlay.rh
                                MouseArea {
                                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeHorCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        cropRight = Math.max(0, Math.min(sourceW - cropLeft - 10, sourceW - v))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; antialiasing: true
                                    width: 5; height: Math.min(40, parent.height * 0.5); radius: 2.5; color: cropOverlay.handleCol }
                            }
                            // Corner handles (drag both axes). 22px hit area, 12px dot.
                            Item {   // top-left
                                visible: cropEdit
                                x: cropOverlay.rx - 11; y: cropOverlay.ry - 11; width: 22; height: 22
                                MouseArea { anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeFDiagCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var vx = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        var vy = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropLeft = Math.max(0, Math.min(sourceW - cropRight - 10, vx))
                                        cropTop = Math.max(0, Math.min(sourceH - cropBottom - 10, vy))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 2; color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2; antialiasing: true }
                            }
                            Item {   // top-right
                                visible: cropEdit
                                x: cropOverlay.rx + cropOverlay.rw - 11; y: cropOverlay.ry - 11; width: 22; height: 22
                                MouseArea { anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeBDiagCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var vx = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        var vy = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropRight = Math.max(0, Math.min(sourceW - cropLeft - 10, sourceW - vx))
                                        cropTop = Math.max(0, Math.min(sourceH - cropBottom - 10, vy))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 2; color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2; antialiasing: true }
                            }
                            Item {   // bottom-left
                                visible: cropEdit
                                x: cropOverlay.rx - 11; y: cropOverlay.ry + cropOverlay.rh - 11; width: 22; height: 22
                                MouseArea { anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeBDiagCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var vx = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        var vy = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropLeft = Math.max(0, Math.min(sourceW - cropRight - 10, vx))
                                        cropBottom = Math.max(0, Math.min(sourceH - cropTop - 10, sourceH - vy))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 2; color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2; antialiasing: true }
                            }
                            Item {   // bottom-right
                                visible: cropEdit
                                x: cropOverlay.rx + cropOverlay.rw - 11; y: cropOverlay.ry + cropOverlay.rh - 11; width: 22; height: 22
                                MouseArea { anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.SizeFDiagCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var vx = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        var vy = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropRight = Math.max(0, Math.min(sourceW - cropLeft - 10, sourceW - vx))
                                        cropBottom = Math.max(0, Math.min(sourceH - cropTop - 10, sourceH - vy))
                                    }
                                    onReleased: commit()
                                }
                                Rectangle { anchors.centerIn: parent; width: 12; height: 12; radius: 2; color: "#ffffff"; border.color: cropOverlay.handleCol; border.width: 2; antialiasing: true }
                            }
                        }
                        }
                        // Pan/zoom interaction layer. Hand drags to pan; Zoom click
                        // zooms (Alt = out); wheel zooms; double-click resets. Disabled
                        // while editing crop so the crop handles receive the mouse.
                        MouseArea {
                            id: panArea
                            anchors.fill: parent
                            enabled: !cropEdit
                            acceptedButtons: Qt.LeftButton
                            property real lastX: 0
                            property real lastY: 0
                            cursorShape: win.tool === "zoom" ? Qt.CrossCursor : (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                            onPressed: (m) => {
                                lastX = m.x; lastY = m.y
                                if (win.tool === "zoom") win.pvZoomAt((m.modifiers & Qt.AltModifier) ? 0.8 : 1.25, m.x, m.y)
                            }
                            onPositionChanged: (m) => {
                                if (pressed && win.tool === "hand") { win.pvOffX += (m.x - lastX); win.pvOffY += (m.y - lastY); lastX = m.x; lastY = m.y; win.clampPan() }
                            }
                            onDoubleClicked: win.resetPreviewView()
                        }
                        // Wheel zoom works in ANY mode (even while editing crop),
                        // independent of the Hand/Zoom pan MouseArea above.
                        WheelHandler {
                            onWheel: (ev) => win.pvZoomAt(ev.angleDelta.y > 0 ? 1.25 : 0.8, ev.point.position.x, ev.point.position.y)
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: actP().mediaStatus === MediaPlayer.NoMedia || actP().mediaStatus === MediaPlayer.LoadingMedia
                            text: "Loading preview…"; color: win.col("text_mute", "#7d8590"); font.pixelSize: 14
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
                            ctx.globalAlpha = 0.4; ctx.fillStyle = win.col("accent_dim", "#1f3a66")
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
                                ctx.strokeStyle = "rgba(47,129,247,0.9)"; ctx.lineWidth = 1
                                for (var w = 0; w < nb; ++w) {
                                    var wx = pad + (w + 0.5) / nb * inner
                                    var mn = wv[w][0], mx = wv[w][1]
                                    var yTop = midY - mx * halfMax
                                    var yBot = midY - mn * halfMax
                                    if (yBot - yTop < 0.8) { yTop -= 0.4; yBot += 0.4 }
                                    ctx.beginPath(); ctx.moveTo(wx, yTop); ctx.lineTo(wx, yBot); ctx.stroke()
                                }
                            } else if (win.hasAudio) {
                                ctx.fillStyle = win.col("text_subtle", "#484f58"); ctx.font = "10px 'Segoe UI'"; ctx.textAlign = "center"
                                ctx.fillText("decoding waveform…", width / 2, midY - 2)
                            }
                            // Cut (removed) ranges: translucent red over the waveform.
                            var cz = win.cuts
                            for (var c1 = 0; c1 < cz.length; ++c1) {
                                var cx0 = t2x(cz[c1][0]), cx1 = t2x(cz[c1][1])
                                ctx.fillStyle = "rgba(248,81,73,0.32)"
                                ctx.fillRect(cx0, 6, Math.max(1, cx1 - cx0), height - 12)
                                ctx.strokeStyle = (c1 === win.selCut) ? "rgba(255,255,255,0.95)" : "rgba(248,81,73,0.9)"; ctx.lineWidth = (c1 === win.selCut) ? 2 : 1
                                ctx.strokeRect(cx0, 6, Math.max(1, cx1 - cx0), height - 12)
                            }
                            ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "center"
                            for (var i = 1; i < segs.length; ++i) {
                                var bx = t2x(segs[i].start)
                                ctx.strokeStyle = "rgba(232,178,120,0.78)"; ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                                ctx.beginPath(); ctx.moveTo(bx, 4); ctx.lineTo(bx, height - 4); ctx.stroke(); ctx.setLineDash([])
                                ctx.fillStyle = "rgba(240,200,150,0.92)"; ctx.fillText(segs[i].name, bx, 13)
                            }
                            // Chapters: dashed purple markers with titles (parity with classic).
                            for (var ch = 0; ch < chapters.length; ++ch) {
                                var chx = t2x(Number(chapters[ch].t))
                                if (chx < pad - 1 || chx > width - pad + 1) continue
                                ctx.strokeStyle = win.col("chapter_text", "#d9bdff"); ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                                ctx.beginPath(); ctx.moveTo(chx, height - 16); ctx.lineTo(chx, height - 4); ctx.stroke(); ctx.setLineDash([])
                                ctx.fillStyle = win.col("chapter_text", "#d9bdff"); ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "left"
                                ctx.fillText(chapters[ch].title, chx + 3, height - 6)
                            }
                            function vbar(x, c) { ctx.strokeStyle = c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, 6); ctx.lineTo(x, height - 6); ctx.stroke() }
                            vbar(xi, win.col("marker_in", "#2ddc7f")); vbar(xo, win.col("marker_out", "#d29922"))
                            for (var k = 0; k < separatorPoints.length; ++k) {
                                var sx = t2x(Number(separatorPoints[k]))
                                ctx.strokeStyle = (k === win.selSplit) ? "#ffffff" : "#38bdf8"; ctx.lineWidth = (k === win.selSplit) ? 3 : 2
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
                                    color: win.col("text", "#e6edf3"); font.pixelSize: 10; font.family: "Consolas"
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
                    Label { text: fmt(cti) + " / " + fmt(totalDuration); color: win.col("text", "#e6edf3"); font.pixelSize: 12; font.family: "Consolas" }
                    Label { text: "f " + curFrame() + "/" + totalFrames(); color: win.col("text_mute", "#7d8590"); font.pixelSize: 10; font.family: "Consolas" }
                    Item { Layout.fillWidth: true }
                    PadButton { Layout.preferredWidth: 74; text: win.muted ? "Unmute" : "Mute"; onClicked: win.muted = !win.muted }
                    Slider { Layout.preferredWidth: 88; from: 0; to: 1; value: win.volume; onMoved: { win.volume = value; win.muted = false } }
                    PadButton { Layout.preferredWidth: 34; text: "\u2212"; onClicked: { win.zoomAt(0.8, cti, 0.5); tl.requestPaint() } }
                    Slider {
                        Layout.preferredWidth: 120
                        from: 0; to: 100
                        value: 100 * Math.log(Math.max(1, win.zoom)) / Math.log(400)
                        onMoved: {
                            win.zoom = Math.max(1, Math.pow(400, value / 100))
                            var sp = win.viewSpan(); win.viewStart = win.cti - sp / 2; win.clampView(); tl.requestPaint()
                        }
                    }
                    Label { text: (Math.round(win.zoom * 100) / 100) + "\u00d7"; color: win.col("text_mute", "#7d8590"); font.pixelSize: 11 }
                    PadButton { Layout.preferredWidth: 34; text: "+"; onClicked: { win.zoomAt(1.25, cti, 0.5); tl.requestPaint() } }
                    PadButton { Layout.preferredWidth: 46; text: "Fit"; onClicked: { win.fitZoom(); tl.requestPaint() } }
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
                PadButton { text: "Cancel (Esc)"; implicitWidth: 150; implicitHeight: 40; baseColor: win.col("danger", "#a40e26"); textColor: "#ffffff"; onClicked: bridge.cancel() }
                PadButton { text: "Confirm (Enter)"; implicitWidth: 180; implicitHeight: 40; baseColor: win.col("green", "#238636"); textColor: "#ffffff"; onClicked: bridge.submit(buildResult()) }
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
