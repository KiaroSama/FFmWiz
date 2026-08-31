// The waveform/ruler timeline and everything drawn on it: cuts, splits,
// chapters, the in/out marks and the playhead.
//
// Split out of UnifiedEditor.qml. Like LeftPanel it lives inside the root
// window's tree, so it still reads the editing state through `win`.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Card {
    id: timelinePanel
    // The root window repaints the canvas and reads its width/padding
    // when it converts between time and pixels.
    property alias canvas: tl

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
            // 132px per tick, not 90: the labels below are FULL timecodes
            // (00:01:40.000, as the classic writes them) and at 90 they
            // collided. Fewer, readable labels beat more, overlapping ones.
            var targetTicks = Math.max(2, Math.floor((width - 2 * pad) / 132))
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
                ctx.fillStyle = win.col("tick_lo", "#7d8590"); ctx.fillText(win.fmt(tr), trx + 2, 9)
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
                // Name the range, as the classic does. Without it a red block
                // says only "something was removed here", not WHICH cut -- and
                // the delete/select buttons refer to cuts by number.
                var cw = cx1 - cx0
                if (cw > 44) {
                    ctx.font = "600 10px 'Segoe UI'"; ctx.textAlign = "center"
                    ctx.fillStyle = win.col("text", "#e8edfb")
                    ctx.fillText("Cut #" + (c1 + 1), cx0 + cw / 2, 20)
                }
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
            // IN/OUT are not plain vbars. Drawn as one they were the same
            // shape and weight as a SPLIT marker, so the green IN was easy to miss
            // entirely. The classic editor gives each a halo, a thicker stem
            // and a filled triangle flag at the ruler, and grows all three
            // when it is the selected marker -- copied here for parity.
            // The classic also writes the marker's name beside the flag, so
            // IN/OUT/SPLIT are readable without hovering -- copied here too.
            function flag(x, key, fb, isSel, label) {
                var c = win.col(key, fb)
                var half = isSel ? 9 : 7
                var tip = isSel ? 20 : 17
                // Stem hangs from the flag's tip (not from its base), as in the
                // classic: markers stay shorter than the CTI and centre guide.
                ctx.strokeStyle = win.colA("playhead_halo", "#3b82f6", isSel ? 0.55 : 0.35)
                ctx.lineWidth = isSel ? 8 : 6
                ctx.beginPath(); ctx.moveTo(x, tip); ctx.lineTo(x, height - 6); ctx.stroke()
                ctx.strokeStyle = c; ctx.lineWidth = isSel ? 4 : 3
                ctx.beginPath(); ctx.moveTo(x, tip); ctx.lineTo(x, height - 6); ctx.stroke()
                ctx.fillStyle = c
                ctx.beginPath(); ctx.moveTo(x - half, 5); ctx.lineTo(x + half, 5)
                ctx.lineTo(x, tip); ctx.closePath(); ctx.fill()
                ctx.font = "bold " + (isSel ? 10 : 9) + "px 'Segoe UI'"; ctx.textAlign = "left"
                ctx.fillText(label, x + 5, tip + 2)
            }
            flag(xi, "marker_in", "#2ddc7f", win.selMarker === "in", "IN")
            flag(xo, "marker_out", "#d29922", win.selMarker === "out", "OUT")
            // Splits get the same flag as IN/OUT (the classic draws all three
            // identically); as a bare line they read as a gridline.
            for (var k = 0; k < separatorPoints.length; ++k) {
                var kSel = (k === win.selSplit)
                flag(t2x(Number(separatorPoints[k])),
                     kSel ? "split_marker_sel" : "split_marker",
                     kSel ? "#7dd3fc" : "#38bdf8", kSel, "SPLIT")
            }
            // Centre-of-video guide: fixed at totalDuration/2, shaped like the
            // CTI (arrow head + full-height line) but violet and dashed, so it
            // is never confused with the red playhead. Straight from the classic.
            var cgx = t2x(totalDuration / 2)
            if (totalDuration > 0 && cgx >= pad - 2 && cgx <= width - pad + 2) {
                ctx.strokeStyle = win.colA("timeline_bg", "#0a0d12", 0.86); ctx.lineWidth = 3
                ctx.beginPath(); ctx.moveTo(cgx, 18); ctx.lineTo(cgx, height); ctx.stroke()
                ctx.strokeStyle = win.col("center_guide", "#c084fc"); ctx.lineWidth = 2; ctx.setLineDash([4, 4])
                ctx.beginPath(); ctx.moveTo(cgx, 18); ctx.lineTo(cgx, height); ctx.stroke(); ctx.setLineDash([])
                ctx.fillStyle = win.col("center_guide", "#c084fc")
                ctx.strokeStyle = win.col("bg", "#0d1117"); ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(cgx - 9, 1); ctx.lineTo(cgx + 9, 1)
                ctx.lineTo(cgx, 18); ctx.closePath(); ctx.fill(); ctx.stroke()
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
        // Weight and shape copied from the classic: a wide blue halo behind a
        // thick red stem, topped by a broad triangular head at the ruler. A 2px
        // hairline was invisible over the waveform.
        Item {
            id: cti
            y: 0; width: 0; height: tl.height
            x: Math.max(0, Math.min(tl.width, tl.t2x(win.cti)))
            visible: win.ready && win.cti >= win.viewStart - 1e-6
                     && win.cti <= win.viewStart + win.viewSpan() + 1e-6
            Rectangle {
                x: -4.5; y: 17; width: 9; height: cti.height - 17
                // colA() is a CSS rgba() string -- Canvas-only; a QML color
                // property cannot parse it, so the alpha lives on opacity.
                color: win.col("playhead_halo", "#3b82f6"); opacity: 0.82
            }
            Rectangle {
                x: -2.5; y: 17; width: 5; height: cti.height - 17
                color: win.col("playhead", "#ff4d55")
            }
            Canvas {   // the head; painted once, the parent Item does the moving
                x: -14; y: 0; width: 28; height: 20
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    c.fillStyle = win.col("playhead", "#ff4d55")
                    c.strokeStyle = win.colA("playhead_halo", "#3b82f6", 0.82); c.lineWidth = 2
                    c.beginPath(); c.moveTo(2, 1); c.lineTo(26, 1); c.lineTo(14, 18)
                    c.closePath(); c.fill(); c.stroke()
                }
            }
        }
        // "Center hh:mm:ss" pill under the centre guide's arrow, like the
        // classic's. Reuses the hover tooltip's shape so the two read as one set.
        Rectangle {
            property real cgx: tl.t2x(totalDuration / 2)
            visible: win.ready && totalDuration > 0 && cgx >= tl.pad - 2 && cgx <= tl.width - tl.pad + 2
            color: win.col("bg", "#0d1117"); border.color: win.col("center_guide", "#c084fc")
            radius: 4; height: 16; width: cgLbl.implicitWidth + 12
            x: Math.max(0, Math.min(tl.width - width, cgx - width / 2)); y: 12
            Label {
                id: cgLbl; anchors.centerIn: parent
                color: win.col("center_guide_text", "#e9d5ff"); font.pixelSize: 9; font.bold: true
                text: "Center  " + fmt(totalDuration / 2)
            }
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
                color: win.col("surface_modern", "#2d323a"); border.color: win.col("border_strong", "#3a4150")
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
