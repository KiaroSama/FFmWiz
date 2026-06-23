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
    property var peaks: []
    property bool ready: false

    // ---- Phase 5: timeline zoom/pan + interactive crop ----
    property real zoom: 1.0        // 1 = whole clip visible; higher = zoomed in
    property real viewStart: 0     // left edge of the visible window, in seconds
    property bool cropEdit: false  // show draggable crop handles on the preview
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
        var idx = speedBox.model.indexOf(Math.round(speed * 100) + "%"); if (idx >= 0) speedBox.currentIndex = idx
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

    // Waveform peaks arrive asynchronously from the audio decode.
    Connections {
        target: bridge
        function onWaveformReady(peaksJson) {
            try { win.peaks = JSON.parse(peaksJson) || [] } catch (e) { win.peaks = [] }
            tl.requestPaint()
        }
    }

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
        audioOutput: AudioOutput { id: aoA; volume: 0.85 }
        onPositionChanged: { if (ready && activeAB === 0 && segs.length) cti = Math.min(totalDuration, segs[curSeg].start + position / 1000.0) }
        onMediaStatusChanged: { if (activeAB === 0 && mediaStatus === MediaPlayer.EndOfMedia) advanceToNext() }
    }
    MediaPlayer {
        id: playerB
        videoOutput: voB
        audioOutput: AudioOutput { id: aoB; volume: 0.85 }
        onPositionChanged: { if (ready && activeAB === 1 && segs.length) cti = Math.min(totalDuration, segs[curSeg].start + position / 1000.0) }
        onMediaStatusChanged: { if (activeAB === 1 && mediaStatus === MediaPlayer.EndOfMedia) advanceToNext() }
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
        var s = segmentForTime(t)
        cti = Math.max(0, Math.min(totalDuration, t))
        if (s.index === curSeg) actP().position = Math.round(s.local * 1000)
        else loadSegment(s.index, s.local, actP().playbackState === MediaPlayer.PlayingState)
    }
    function togglePlay() {
        if (actP().playbackState === MediaPlayer.PlayingState) { actP().pause(); wantPlaying = false }
        else { actP().play(); wantPlaying = true }
    }
    function fmt(t) {
        t = Math.max(0, t)
        var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60), ms = Math.floor((t - Math.floor(t)) * 1000)
        function p(n, w) { var x = String(n); while (x.length < w) x = "0" + x; return x }
        return p(h, 2) + ":" + p(m, 2) + ":" + p(s, 2) + "." + p(ms, 3)
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
    function setMarkIn() { markIn = snapTime(cti, -1, "in"); commit() }
    function setMarkOut() { markOut = snapTime(cti, -1, "out"); commit() }

    function buildResult() {
        return JSON.stringify({ status: "ok", margins: [cropTop, cropLeft, cropRight, cropBottom],
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

        // ---- Main: left controls + center preview/timeline ----
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            // ----- Left control column -----
            Card {
                Layout.preferredWidth: 300
                Layout.minimumWidth: 260
                Layout.fillHeight: true
                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 12
                    contentWidth: availableWidth
                    clip: true
                    ColumnLayout {
                        width: parent.parent.availableWidth
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

                        Rectangle { Layout.fillWidth: true; height: 1; color: win.col("border", "#30363d") }

                        SectionLabel { text: "SPEED & AUDIO" }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 8
                            Label { text: "Speed"; color: win.col("text", "#e6edf3") }
                            ComboBox {
                                id: speedBox
                                Layout.fillWidth: true
                                model: ["25%", "50%", "75%", "100%", "125%", "150%", "200%"]
                                currentIndex: 3
                                onActivated: { speed = parseFloat(currentText) / 100.0; commit() }
                                Component.onCompleted: { var idx = model.indexOf(Math.round(speed * 100) + "%"); if (idx >= 0) currentIndex = idx }
                            }
                        }
                        Switch { text: "Reverse video"; checked: reverse; onToggled: { reverse = checked; commit() } }
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
                        PadButton { Layout.fillWidth: true; text: "Clear Cuts"; onClicked: { cuts = []; tl.requestPaint(); commit() } }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: cuts.length + " cut(s) • " + separatorPoints.length + " split(s)\nKept: " + fmt(keepTotal()) + " of " + fmt(totalDuration)
                            color: win.col("marker_in", "#2ddc7f"); font.pixelSize: 11
                        }

                        Item { Layout.fillHeight: true }
                    }
                }
            }

            // ----- Center: preview + timeline + transport -----
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                Card {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: win.col("timeline_bg", "#0a0d12")
                    border.color: win.col("border_strong", "#3a4150")
                    clip: true
                    // previewArea hosts both video outputs and the crop overlay in
                    // ONE coordinate space, so contentRect maps 1:1 to overlay pixels.
                    Item {
                        id: previewArea
                        anchors.fill: parent
                        anchors.margins: 6
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
                            visible: ready && (cropEdit || (cropTop + cropLeft + cropRight + cropBottom) > 0)
                            property rect cr: (win.activeAB === 0 ? voA.contentRect : voB.contentRect)
                            property real rx: cr.x + cr.width * (cropLeft / Math.max(1, sourceW))
                            property real ry: cr.y + cr.height * (cropTop / Math.max(1, sourceH))
                            property real rw: cr.width * Math.max(0, (sourceW - cropLeft - cropRight)) / Math.max(1, sourceW)
                            property real rh: cr.height * Math.max(0, (sourceH - cropTop - cropBottom)) / Math.max(1, sourceH)
                            property color handleCol: win.col("accent", "#1f6feb")

                            Rectangle {
                                color: "transparent"; border.color: win.col("warn", "#d29922"); border.width: 2
                                x: cropOverlay.rx; y: cropOverlay.ry; width: cropOverlay.rw; height: cropOverlay.rh
                            }
                            // Top edge handle
                            Rectangle {
                                visible: cropEdit; radius: 3; opacity: 0.92; color: cropOverlay.handleCol
                                height: 10; x: cropOverlay.rx; width: cropOverlay.rw; y: cropOverlay.ry - 5
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.SizeVerCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropTop = Math.max(0, Math.min(sourceH - cropBottom - 10, v))
                                    }
                                    onReleased: commit()
                                }
                            }
                            // Bottom edge handle
                            Rectangle {
                                visible: cropEdit; radius: 3; opacity: 0.92; color: cropOverlay.handleCol
                                height: 10; x: cropOverlay.rx; width: cropOverlay.rw; y: cropOverlay.ry + cropOverlay.rh - 5
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.SizeVerCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.y - cropOverlay.cr.y) / Math.max(1, cropOverlay.cr.height) * sourceH)
                                        cropBottom = Math.max(0, Math.min(sourceH - cropTop - 10, sourceH - v))
                                    }
                                    onReleased: commit()
                                }
                            }
                            // Left edge handle
                            Rectangle {
                                visible: cropEdit; radius: 3; opacity: 0.92; color: cropOverlay.handleCol
                                width: 10; y: cropOverlay.ry; height: cropOverlay.rh; x: cropOverlay.rx - 5
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.SizeHorCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        cropLeft = Math.max(0, Math.min(sourceW - cropRight - 10, v))
                                    }
                                    onReleased: commit()
                                }
                            }
                            // Right edge handle
                            Rectangle {
                                visible: cropEdit; radius: 3; opacity: 0.92; color: cropOverlay.handleCol
                                width: 10; y: cropOverlay.ry; height: cropOverlay.rh; x: cropOverlay.rx + cropOverlay.rw - 5
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.SizeHorCursor
                                    onPositionChanged: (mouse) => {
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var v = Math.round((p.x - cropOverlay.cr.x) / Math.max(1, cropOverlay.cr.width) * sourceW)
                                        cropRight = Math.max(0, Math.min(sourceW - cropLeft - 10, sourceW - v))
                                    }
                                    onReleased: commit()
                                }
                            }
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: actP().mediaStatus === MediaPlayer.NoMedia || actP().mediaStatus === MediaPlayer.LoadingMedia
                            text: "Loading preview…"; color: win.col("text_mute", "#7d8590"); font.pixelSize: 14
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 78
                    color: win.col("timeline_bg", "#0a0d12")
                    border.color: win.col("border_strong", "#3a4150")
                    Canvas {
                        id: tl
                        anchors.fill: parent; anchors.margins: 8
                        property real pad: 6
                        // Zoom/pan-aware mapping: the visible window is [viewStart, viewStart+span].
                        function t2x(t) { var sp = win.viewSpan(); return pad + ((t - win.viewStart) / Math.max(0.001, sp)) * (width - 2 * pad) }
                        function x2t(x) { var sp = win.viewSpan(); return Math.max(0, Math.min(totalDuration, win.viewStart + (x - pad) / Math.max(1, (width - 2 * pad)) * sp)) }
                        onPaint: {
                            var ctx = getContext("2d"); ctx.reset()
                            var midY = height * 0.52
                            ctx.strokeStyle = win.col("timeline_track", "#1c2128"); ctx.lineWidth = 1
                            ctx.beginPath(); ctx.moveTo(pad, midY); ctx.lineTo(width - pad, midY); ctx.stroke()
                            var xi = t2x(markIn), xo = t2x(markOut)
                            ctx.globalAlpha = 0.4; ctx.fillStyle = win.col("accent_dim", "#1f3a66")
                            ctx.fillRect(xi, 6, Math.max(0, xo - xi), height - 12); ctx.globalAlpha = 1.0
                            // Audio waveform (amplitude envelope), centered on midY.
                            var pk = win.peaks
                            if (pk && pk.length > 1) {
                                var halfMax = Math.min(midY - 4, height - 4 - midY)
                                var np = pk.length
                                ctx.strokeStyle = "rgba(47,129,247,0.85)"; ctx.lineWidth = 1
                                for (var w = 0; w < np; ++w) {
                                    var wt = (w / (np - 1)) * totalDuration
                                    var wx = t2x(wt)
                                    if (wx < pad - 1 || wx > width - pad + 1) continue   // outside zoom window
                                    var hh = Math.max(0.4, pk[w] * halfMax)
                                    ctx.beginPath(); ctx.moveTo(wx, midY - hh); ctx.lineTo(wx, midY + hh); ctx.stroke()
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
                                ctx.strokeStyle = "rgba(248,81,73,0.9)"; ctx.lineWidth = 1
                                ctx.strokeRect(cx0, 6, Math.max(1, cx1 - cx0), height - 12)
                            }
                            ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "center"
                            for (var i = 1; i < segs.length; ++i) {
                                var bx = t2x(segs[i].start)
                                ctx.strokeStyle = "rgba(232,178,120,0.78)"; ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                                ctx.beginPath(); ctx.moveTo(bx, 4); ctx.lineTo(bx, height - 4); ctx.stroke(); ctx.setLineDash([])
                                ctx.fillStyle = "rgba(240,200,150,0.92)"; ctx.fillText(segs[i].name, bx, 13)
                            }
                            function vbar(x, c) { ctx.strokeStyle = c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, 6); ctx.lineTo(x, height - 6); ctx.stroke() }
                            vbar(xi, win.col("marker_in", "#2ddc7f")); vbar(xo, win.col("marker_out", "#d29922"))
                            for (var k = 0; k < separatorPoints.length; ++k) {
                                var sx = t2x(Number(separatorPoints[k])); ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2
                                ctx.beginPath(); ctx.moveTo(sx, 6); ctx.lineTo(sx, height - 6); ctx.stroke()
                            }
                            var px = t2x(cti); ctx.strokeStyle = win.col("playhead", "#ff4d55"); ctx.lineWidth = 2
                            ctx.beginPath(); ctx.moveTo(px, 2); ctx.lineTo(px, height - 2); ctx.stroke()
                        }
                        MouseArea {
                            anchors.fill: parent
                            onPressed: (m) => seekTo(snapTime(tl.x2t(m.x), -1, null))
                            onPositionChanged: (m) => { if (pressed) seekTo(snapTime(tl.x2t(m.x), -1, null)) }
                            // Wheel zooms the timeline around the cursor time.
                            onWheel: (wheel) => {
                                var tUnder = tl.x2t(wheel.x)
                                var frac = (wheel.x - tl.pad) / Math.max(1, (tl.width - 2 * tl.pad))
                                win.zoomAt(wheel.angleDelta.y > 0 ? 1.25 : 0.8, tUnder, frac)
                                tl.requestPaint()
                            }
                        }
                    }
                    Connections { target: win; function onCtiChanged() { tl.requestPaint() } }
                    Connections { target: win; function onMarkInChanged() { tl.requestPaint() } }
                    Connections { target: win; function onMarkOutChanged() { tl.requestPaint() } }
                    Connections { target: win; function onCutsChanged() { tl.requestPaint() } }
                    Connections { target: win; function onReadyChanged() { tl.requestPaint() } }
                    Connections { target: win; function onZoomChanged() { tl.requestPaint() } }
                    Connections { target: win; function onViewStartChanged() { tl.requestPaint() } }
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

                // Transport
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    PadButton { Layout.preferredWidth: 104; text: actP().playbackState === MediaPlayer.PlayingState ? "❚❚  Pause" : "▶  Play"; onClicked: togglePlay() }
                    PadButton { Layout.preferredWidth: 50; text: "◀◀"; onClicked: seekTo(cti - 1) }    // -1 s
                    PadButton { Layout.preferredWidth: 44; text: "◀|"; onClicked: frameStep(-1) }       // -1 frame
                    PadButton { Layout.preferredWidth: 44; text: "|▶"; onClicked: frameStep(1) }        // +1 frame
                    PadButton { Layout.preferredWidth: 50; text: "▶▶"; onClicked: seekTo(cti + 1) }     // +1 s
                    Label { text: fmt(cti) + "  /  " + fmt(totalDuration); color: win.col("text", "#e6edf3"); font.pixelSize: 13; font.family: "Consolas" }
                    Label { text: "f " + curFrame() + " / " + totalFrames(); color: win.col("text_mute", "#7d8590"); font.pixelSize: 11; font.family: "Consolas" }
                    Item { Layout.fillWidth: true }
                    PadButton { Layout.preferredWidth: 40; text: "−"; onClicked: { win.zoomAt(0.8, cti, 0.5); tl.requestPaint() } }
                    Label { text: (Math.round(win.zoom * 100) / 100) + "×"; color: win.col("text_mute", "#7d8590"); font.pixelSize: 11 }
                    PadButton { Layout.preferredWidth: 40; text: "+"; onClicked: { win.zoomAt(1.25, cti, 0.5); tl.requestPaint() } }
                    PadButton { Layout.preferredWidth: 54; text: "Fit"; onClicked: { win.fitZoom(); tl.requestPaint() } }
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
                    onClicked: { cropTop = cropLeft = cropRight = cropBottom = 0; speed = 1.0; reverse = false; includeAudio = hasAudio; markIn = 0; markOut = totalDuration; cuts = []; separatorPoints = []; speedBox.currentIndex = 3; zoom = 1.0; viewStart = 0; cropEdit = false; tl.requestPaint(); commit() } }
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
    Shortcut { sequence: "Ctrl+Z"; onActivated: doUndo() }
    Shortcut { sequence: "Ctrl+Y"; onActivated: doRedo() }
    Shortcut { sequence: "Ctrl+Shift+Z"; onActivated: doRedo() }
    Shortcut { sequence: "Left"; onActivated: frameStep(-1) }
    Shortcut { sequence: "Right"; onActivated: frameStep(1) }
    Shortcut { sequence: "Shift+Left"; onActivated: seekTo(cti - 1) }
    Shortcut { sequence: "Shift+Right"; onActivated: seekTo(cti + 1) }
}
