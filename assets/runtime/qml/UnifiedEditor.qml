// FFmWiz Unified Video Editor — QtQuick/QML (modern engine, Phase 1).
// Colors come from the classic palette (bridge.paletteJson) so the look matches.
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia

ApplicationWindow {
    id: win
    visible: true
    width: 1280
    height: 800
    title: "FFmWiz Unified Video Editor"

    // ---- Data parsed from the bridge ----
    property var req: ({})
    property var theme: ({})
    property var segs: []          // [{path, name, start, duration}]
    property real totalDuration: 0 // seconds
    property real fps: 30
    property int sourceW: 1920
    property int sourceH: 1080
    property bool hasAudio: true

    // ---- Editing state ----
    property real cti: 0           // current timeline position (seconds)
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
    property bool ready: false

    function col(key, fallback) { return (theme && theme[key]) ? theme[key] : fallback }

    color: col("bg", "#0d1117")

    // Fusion-free dark control palette so Controls.Basic renders on-theme.
    palette.window: col("bg", "#0d1117")
    palette.windowText: col("text", "#e6edf3")
    palette.base: col("panel", "#161b22")
    palette.text: col("text", "#e6edf3")
    palette.button: col("surface", "#21262d")
    palette.buttonText: col("text", "#e6edf3")
    palette.highlight: col("accent", "#1f6feb")
    palette.highlightedText: "#ffffff"

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

        // Build segments (single input or multi-segment join), with cumulative starts.
        var list = []
        var off = 0
        var js = req.join_segments || []
        if (js.length > 0) {
            for (var i = 0; i < js.length; ++i) {
                var d = Math.max(0.001, Number(js[i].duration) || 0)
                list.push({ path: js[i].path, name: js[i].name || ("Video " + (i + 1)),
                            start: off, duration: d })
                off += d
            }
            totalDuration = off
        } else {
            var d0 = Math.max(0.001, Number(req.duration) || 0)
            list.push({ path: req.input_path, name: "Video 1", start: 0, duration: d0 })
            totalDuration = d0
        }
        segs = list

        // Initial edit state carried over from a previous session.
        var m = req.initial_margins || [0, 0, 0, 0]
        cropTop = m[0] || 0; cropLeft = m[1] || 0; cropRight = m[2] || 0; cropBottom = m[3] || 0
        speed = Number(req.initial_speed) || 1.0
        reverse = !!req.initial_reverse
        includeAudio = (req.initial_include_audio !== undefined) ? !!req.initial_include_audio : hasAudio
        separatorPoints = req.initial_separator_points || []
        var keep = req.initial_keep_ranges || []
        if (keep.length > 0) {
            markIn = Number(keep[0][0]) || 0
            markOut = Number(keep[keep.length - 1][1]) || totalDuration
        } else {
            markIn = 0; markOut = totalDuration
        }

        ready = true
        loadSegment(0, 0, false)
        if (win.visibility !== Window.Maximized)
            win.showMaximized()
    }

    // ---------- Playback / segment handling ----------
    MediaPlayer {
        id: player
        videoOutput: videoOut
        audioOutput: AudioOutput { id: audioOut; volume: 0.85 }
        onPositionChanged: {
            if (!ready || segs.length === 0) return
            cti = Math.min(totalDuration, segs[curSeg].start + position / 1000.0)
        }
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.EndOfMedia && curSeg + 1 < segs.length) {
                // Continue into the next joined video automatically.
                loadSegment(curSeg + 1, 0, true)
            }
        }
    }

    function loadSegment(index, localSeconds, playAfter) {
        if (segs.length === 0) return
        index = Math.max(0, Math.min(segs.length - 1, index))
        curSeg = index
        player.source = "file:///" + String(segs[index].path).replace(/\\/g, "/")
        player.position = Math.max(0, Math.round(localSeconds * 1000))
        if (playAfter) player.play(); else player.pause()
    }

    function segmentForTime(t) {
        t = Math.max(0, Math.min(totalDuration, t))
        for (var i = 0; i < segs.length; ++i) {
            if (t < segs[i].start + segs[i].duration - 1e-6 || i === segs.length - 1)
                return { index: i, local: t - segs[i].start }
        }
        return { index: 0, local: t }
    }

    function seekTo(t) {
        var s = segmentForTime(t)
        cti = Math.max(0, Math.min(totalDuration, t))
        if (s.index !== curSeg) loadSegment(s.index, s.local, player.playbackState === MediaPlayer.PlayingState)
        else player.position = Math.round(s.local * 1000)
    }

    function togglePlay() {
        if (player.playbackState === MediaPlayer.PlayingState) player.pause()
        else player.play()
    }

    function fmt(t) {
        t = Math.max(0, t)
        var h = Math.floor(t / 3600); var m = Math.floor((t % 3600) / 60)
        var s = Math.floor(t % 60); var ms = Math.floor((t - Math.floor(t)) * 1000)
        function p(n, w) { var x = String(n); while (x.length < w) x = "0" + x; return x }
        return p(h, 2) + ":" + p(m, 2) + ":" + p(s, 2) + "." + p(ms, 3)
    }

    function buildResult() {
        var lo = Math.max(0, Math.min(markIn, markOut))
        var hi = Math.min(totalDuration, Math.max(markIn, markOut))
        if (hi - lo < 0.05) { lo = 0; hi = totalDuration }
        return JSON.stringify({
            status: "ok",
            margins: [cropTop, cropLeft, cropRight, cropBottom],
            keep_ranges: [[lo, hi]],
            separator_points: separatorPoints,
            speed: speed,
            reverse: reverse,
            include_audio: includeAudio
        })
    }

    // ===================== LAYOUT =====================
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        // ---- Header ----
        Rectangle {
            Layout.fillWidth: true
            height: 44
            radius: 10
            color: col("panel", "#161b22")
            border.color: col("border", "#30363d")
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14; anchors.rightMargin: 14
                spacing: 10
                Label {
                    text: "FFmWiz  •  Unified Video Editor"
                    color: col("accent_text", "#79b4ff")
                    font.pixelSize: 15; font.bold: true
                }
                Label {
                    text: segs.length > 1 ? (segs.length + " joined videos") : "1 video"
                    color: col("text_mute", "#7d8590"); font.pixelSize: 12
                }
                Item { Layout.fillWidth: true }
                Label {
                    text: "MODERN (QML)"
                    color: col("chapter_text", "#d9bdff"); font.pixelSize: 11; font.bold: true
                }
            }
        }

        // ---- Center: preview ----
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 10
            color: col("timeline_bg", "#0a0d12")
            border.color: col("border_strong", "#3a4150")
            clip: true

            VideoOutput {
                id: videoOut
                anchors.fill: parent
                anchors.margins: 6
                fillMode: VideoOutput.PreserveAspectFit   // <-- never stretches
            }

            // Crop overlay (mapped to the actual video content rect).
            Item {
                anchors.fill: parent
                visible: ready && (cropTop + cropLeft + cropRight + cropBottom) > 0
                property rect cr: videoOut.contentRect
                Rectangle {  // kept (clear) crop region outline
                    color: "transparent"
                    border.color: col("warn", "#d29922"); border.width: 2
                    x: parent.cr.x + parent.cr.width * (cropLeft / Math.max(1, sourceW))
                    y: parent.cr.y + parent.cr.height * (cropTop / Math.max(1, sourceH))
                    width: parent.cr.width * Math.max(0, (sourceW - cropLeft - cropRight)) / Math.max(1, sourceW)
                    height: parent.cr.height * Math.max(0, (sourceH - cropTop - cropBottom)) / Math.max(1, sourceH)
                }
            }

            Label {
                anchors.centerIn: parent
                visible: player.mediaStatus === MediaPlayer.LoadingMedia || player.mediaStatus === MediaPlayer.NoMedia
                text: "Loading preview…"
                color: col("text_mute", "#7d8590"); font.pixelSize: 14
            }
        }

        // ---- Timeline (ruler + waveformless track for Phase 1) ----
        Rectangle {
            Layout.fillWidth: true
            height: 66
            radius: 10
            color: col("timeline_bg", "#0a0d12")
            border.color: col("border_strong", "#3a4150")

            Canvas {
                id: tl
                anchors.fill: parent
                anchors.margins: 8
                property real pad: 6
                function t2x(t) { return pad + (t / Math.max(0.001, totalDuration)) * (width - 2 * pad) }
                function x2t(x) { return Math.max(0, Math.min(totalDuration, (x - pad) / Math.max(1, (width - 2 * pad)) * totalDuration)) }
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    // track baseline
                    ctx.strokeStyle = col("timeline_track", "#1c2128"); ctx.lineWidth = 1
                    var midY = height * 0.62
                    ctx.beginPath(); ctx.moveTo(pad, midY); ctx.lineTo(width - pad, midY); ctx.stroke()
                    // kept region (mark in -> out)
                    var xi = t2x(markIn), xo = t2x(markOut)
                    ctx.fillStyle = col("accent_dim", "#1f3a66")
                    ctx.globalAlpha = 0.45
                    ctx.fillRect(xi, 6, Math.max(0, xo - xi), height - 12)
                    ctx.globalAlpha = 1.0
                    // joined-video boundaries: thin dotted amber, centered label
                    ctx.font = "9px 'Segoe UI'"
                    for (var i = 1; i < segs.length; ++i) {
                        var bx = t2x(segs[i].start)
                        ctx.strokeStyle = "rgba(232,178,120,0.78)"; ctx.lineWidth = 1
                        ctx.setLineDash([2, 3])
                        ctx.beginPath(); ctx.moveTo(bx, 4); ctx.lineTo(bx, height - 4); ctx.stroke()
                        ctx.setLineDash([])
                        ctx.fillStyle = "rgba(240,200,150,0.92)"
                        ctx.textAlign = "center"
                        ctx.fillText(segs[i].name, bx, 14)
                    }
                    // mark in / out
                    function vbar(x, c) { ctx.strokeStyle = c; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x, 6); ctx.lineTo(x, height - 6); ctx.stroke() }
                    vbar(xi, col("marker_in", "#2ddc7f"))
                    vbar(xo, col("marker_out", "#d29922"))
                    // split points
                    for (var k = 0; k < separatorPoints.length; ++k) {
                        var sx = t2x(Number(separatorPoints[k]))
                        ctx.strokeStyle = "#38bdf8"; ctx.lineWidth = 2
                        ctx.beginPath(); ctx.moveTo(sx, 6); ctx.lineTo(sx, height - 6); ctx.stroke()
                    }
                    // playhead (CTI)
                    var px = t2x(cti)
                    ctx.strokeStyle = col("playhead", "#ff4d55"); ctx.lineWidth = 2
                    ctx.beginPath(); ctx.moveTo(px, 2); ctx.lineTo(px, height - 2); ctx.stroke()
                }
                MouseArea {
                    anchors.fill: parent
                    onPressed: (m) => seekTo(tl.x2t(m.x))
                    onPositionChanged: (m) => { if (pressed) seekTo(tl.x2t(m.x)) }
                }
            }
            // Repaint when any timeline-relevant value changes.
            Connections { target: win; function onCtiChanged() { tl.requestPaint() } }
            Connections { target: win; function onMarkInChanged() { tl.requestPaint() } }
            Connections { target: win; function onMarkOutChanged() { tl.requestPaint() } }
            Connections { target: win; function onReadyChanged() { tl.requestPaint() } }
        }

        // ---- Transport row ----
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Button {
                text: player.playbackState === MediaPlayer.PlayingState ? "❚❚  Pause" : "▶  Play"
                onClicked: togglePlay()
                Layout.preferredWidth: 110
            }
            Button { text: "⟸ -1s"; onClicked: seekTo(cti - 1) }
            Button { text: "+1s ⟹"; onClicked: seekTo(cti + 1) }
            Label {
                text: fmt(cti) + "  /  " + fmt(totalDuration)
                color: col("text", "#e6edf3"); font.pixelSize: 13
                font.family: "Consolas"
            }
            Item { Layout.fillWidth: true }
            Button { text: "Mark In";  onClicked: { markIn = cti } }
            Button { text: "Mark Out"; onClicked: { markOut = cti } }
            Button { text: "Add Split"; onClicked: { var sp = separatorPoints.slice(); sp.push(cti); separatorPoints = sp; tl.requestPaint() } }
        }

        // ---- Controls row ----
        Rectangle {
            Layout.fillWidth: true
            height: 92
            radius: 10
            color: col("panel", "#161b22")
            border.color: col("border", "#30363d")
            RowLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 18

                // Crop
                ColumnLayout {
                    spacing: 4
                    Label { text: "Crop (px)"; color: col("accent_text", "#79b4ff"); font.bold: true; font.pixelSize: 12 }
                    GridLayout {
                        columns: 4; rowSpacing: 4; columnSpacing: 6
                        Label { text: "T"; color: col("text_mute", "#7d8590") }
                        SpinBox { from: 0; to: sourceH; value: cropTop; editable: true; onValueModified: cropTop = value; implicitWidth: 90 }
                        Label { text: "B"; color: col("text_mute", "#7d8590") }
                        SpinBox { from: 0; to: sourceH; value: cropBottom; editable: true; onValueModified: cropBottom = value; implicitWidth: 90 }
                        Label { text: "L"; color: col("text_mute", "#7d8590") }
                        SpinBox { from: 0; to: sourceW; value: cropLeft; editable: true; onValueModified: cropLeft = value; implicitWidth: 90 }
                        Label { text: "R"; color: col("text_mute", "#7d8590") }
                        SpinBox { from: 0; to: sourceW; value: cropRight; editable: true; onValueModified: cropRight = value; implicitWidth: 90 }
                    }
                }

                Rectangle { width: 1; Layout.fillHeight: true; color: col("border", "#30363d") }

                // Speed / reverse / audio
                ColumnLayout {
                    spacing: 6
                    RowLayout {
                        spacing: 8
                        Label { text: "Speed"; color: col("text", "#e6edf3") }
                        ComboBox {
                            id: speedBox
                            implicitWidth: 110
                            model: ["25%", "50%", "75%", "100%", "125%", "150%", "200%"]
                            currentIndex: 3
                            onActivated: { var v = parseFloat(currentText) / 100.0; speed = v }
                            Component.onCompleted: {
                                var pct = Math.round(speed * 100) + "%"
                                var idx = model.indexOf(pct); if (idx >= 0) currentIndex = idx
                            }
                        }
                    }
                    RowLayout {
                        spacing: 14
                        Switch { text: "Reverse"; checked: reverse; onToggled: reverse = checked }
                        Switch { text: "Include audio"; checked: includeAudio; enabled: hasAudio; onToggled: includeAudio = checked }
                    }
                }

                Item { Layout.fillWidth: true }
            }
        }

        // ---- Footer ----
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Button {
                text: "Reset all"
                onClicked: {
                    cropTop = cropLeft = cropRight = cropBottom = 0
                    speed = 1.0; reverse = false; includeAudio = hasAudio
                    markIn = 0; markOut = totalDuration; separatorPoints = []
                    speedBox.currentIndex = 3
                    tl.requestPaint()
                }
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Cancel (Esc)"
                onClicked: bridge.cancel()
                background: Rectangle { radius: 8; color: col("danger", "#a40e26"); border.color: col("border_strong", "#3a4150") }
                contentItem: Label { text: parent.text; color: "#ffffff"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.bold: true }
                Layout.preferredWidth: 150; Layout.preferredHeight: 40
            }
            Button {
                text: "Confirm (Enter)"
                onClicked: bridge.submit(buildResult())
                background: Rectangle { radius: 8; color: col("green", "#238636"); border.color: col("border_strong", "#3a4150") }
                contentItem: Label { text: parent.text; color: "#ffffff"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; font.bold: true }
                Layout.preferredWidth: 180; Layout.preferredHeight: 40
            }
        }
    }

    // ---- Keyboard shortcuts ----
    Shortcut { sequence: "Space"; onActivated: togglePlay() }
    Shortcut { sequence: "Esc"; onActivated: bridge.cancel() }
    Shortcut { sequence: "Return"; onActivated: bridge.submit(buildResult()) }
    Shortcut { sequence: "Enter"; onActivated: bridge.submit(buildResult()) }
    Shortcut { sequence: "I"; onActivated: markIn = cti }
    Shortcut { sequence: "O"; onActivated: markOut = cti }
}
