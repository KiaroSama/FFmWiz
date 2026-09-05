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
        // Vertical bands, so nothing has to guess where anything else is:
        //   0      .. RULER_H   timecode labels + their ticks
        //   RULER_H.. CHIP_BOT  IN/OUT/SPLIT/CENTER chips and the CTI arrow
        //   CHIP_BOT..          waveform, cut ranges, everything editable
        readonly property int rulerH: 20
        readonly property int chipTop: 21
        readonly property int chipBot: 41
        onPaint: {
            var ctx = getContext("2d"); ctx.reset()
            var RULER_H = rulerH, CHIP_TOP = chipTop, CHIP_BOT = chipBot
            var midY = Math.max(CHIP_BOT + 14, height * 0.58)
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
            // The ruler owns the top band ALONE. Its labels used to sit at y=9,
            // the marker chips at y=1..20 and the CTI clock at y=12..28 -- four
            // things in the same 28px, so a chip covered whatever label was
            // behind it and the CENTER chip was sliced in half by the clock.
            //
            // Shape copied from the classic ruler: the label CENTRED on its
            // tick with a short tick line under it, and a label dropped
            // entirely when it would touch the previous one. A number floating
            // with nothing under it does not say WHERE it is.
            ctx.font = "600 10px 'Consolas'"; ctx.textAlign = "center"
            var t0r = Math.ceil(win.viewStart / rstep) * rstep
            var lastRight = -1e9
            for (var tr = t0r; tr <= win.viewStart + span + 1e-6; tr += rstep) {
                var trx = t2x(tr)
                if (trx < pad - 1 || trx > width - pad + 1) continue
                // gridline, below the ruler band so it never crosses the text
                ctx.strokeStyle = win.col("timeline_track", "#1c2128"); ctx.lineWidth = 1
                ctx.globalAlpha = 0.45; ctx.beginPath(); ctx.moveTo(trx, RULER_H); ctx.lineTo(trx, height - 4); ctx.stroke(); ctx.globalAlpha = 1.0
                var lbl = win.fmt(tr)
                var lw = ctx.measureText(lbl).width
                var lcx = Math.max(pad + lw / 2, Math.min(width - pad - lw / 2, trx))
                if (lcx - lw / 2 < lastRight + 10) continue
                ctx.fillStyle = win.col("tick_hi", "#e6edf3")
                ctx.fillText(lbl, lcx, 10)
                ctx.strokeStyle = win.col("tick_hi", "#e6edf3"); ctx.lineWidth = 1
                ctx.beginPath(); ctx.moveTo(trx + 0.5, 13); ctx.lineTo(trx + 0.5, RULER_H - 1); ctx.stroke()
                lastRight = lcx + lw / 2
            }
            var xi = t2x(markIn), xo = t2x(markOut)
            ctx.globalAlpha = 0.4; ctx.fillStyle = win.col("accent_dim", "#1b3468")
            ctx.fillRect(xi, CHIP_BOT, Math.max(0, xo - xi), height - CHIP_BOT - 6); ctx.globalAlpha = 1.0
            // Audio waveform: per-column min/max for the CURRENT
            // viewport (fetched from the backend on viewport change).
            // The pair values are already in -1..1 vs int16 full
            // scale, so quiet stays quiet and loud stays loud.
            var wv = win.wf
            if (wv && wv.length > 1) {
                var halfMax = Math.min(midY - CHIP_BOT, height - 4 - midY)
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
                ctx.fillRect(cx0, CHIP_BOT, Math.max(1, cx1 - cx0), height - CHIP_BOT - 6)
                ctx.strokeStyle = (c1 === win.selCut) ? "rgba(255,255,255,0.95)" : win.colA("cut_red", "#f85149", 0.9); ctx.lineWidth = (c1 === win.selCut) ? 2 : 1
                ctx.strokeRect(cx0, CHIP_BOT, Math.max(1, cx1 - cx0), height - CHIP_BOT - 6)
                // Name the range, as the classic does. Without it a red block
                // says only "something was removed here", not WHICH cut -- and
                // the delete/select buttons refer to cuts by number.
                var cw = cx1 - cx0
                if (cw > 44) {
                    ctx.font = "600 10px 'Segoe UI'"; ctx.textAlign = "center"
                    ctx.fillStyle = win.col("text", "#e8edfb")
                    ctx.fillText("Cut #" + (c1 + 1), cx0 + cw / 2, CHIP_BOT + 13)
                }
            }
            ctx.font = "9px 'Segoe UI'"; ctx.textAlign = "center"
            for (var i = 1; i < segs.length; ++i) {
                var bx = t2x(segs[i].start)
                ctx.strokeStyle = win.colA("segment_boundary", "#e8b278", 0.78); ctx.lineWidth = 1; ctx.setLineDash([2, 3])
                ctx.beginPath(); ctx.moveTo(bx, CHIP_BOT); ctx.lineTo(bx, height - 4); ctx.stroke(); ctx.setLineDash([])
                ctx.fillStyle = win.colA("segment_boundary", "#e8b278", 0.92); ctx.fillText(segs[i].name, bx, CHIP_BOT + 24)
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
            // A marker is a rounded CHIP with its label inside, on a crisp stem
            // in its own colour. The previous shape -- a pennant triangle over a
            // wide blue halo -- was the classic's, and copying it put a BLUE glow
            // around a green marker. Here the glow is the marker's own colour, the
            // label sits in the chip instead of floating beside it, and selection
            // is shown by weight and a ring rather than by blur.
            function chip(x, key, fb, isSel, label) {
                var c = win.col(key, fb)
                ctx.font = (isSel ? "600 " : "500 ") + (isSel ? 12 : 11) + "px 'Segoe UI'"
                var tw = ctx.measureText(label).width
                var w = tw + 14, h = isSel ? 19 : 17, r = h / 2
                var lx = Math.max(1, Math.min(width - w - 1, x - w / 2))
                var top = CHIP_TOP + (19 - h) / 2
                // stem: a soft same-colour glow under a crisp core
                ctx.strokeStyle = win.colA(key, fb, 0.22); ctx.lineWidth = isSel ? 7 : 5
                ctx.beginPath(); ctx.moveTo(x, top + h); ctx.lineTo(x, height - 5); ctx.stroke()
                ctx.strokeStyle = c; ctx.lineWidth = isSel ? 2.5 : 2
                ctx.beginPath(); ctx.moveTo(x, top + h); ctx.lineTo(x, height - 5); ctx.stroke()
                // chip
                ctx.fillStyle = c
                ctx.beginPath()
                ctx.moveTo(lx + r, top); ctx.lineTo(lx + w - r, top)
                ctx.arcTo(lx + w, top, lx + w, top + r, r); ctx.lineTo(lx + w, top + h - r)
                ctx.arcTo(lx + w, top + h, lx + w - r, top + h, r); ctx.lineTo(lx + r, top + h)
                ctx.arcTo(lx, top + h, lx, top + h - r, r); ctx.lineTo(lx, top + r)
                ctx.arcTo(lx, top, lx + r, top, r); ctx.closePath(); ctx.fill()
                if (isSel) {
                    ctx.strokeStyle = win.col("text", "#e8edfb"); ctx.lineWidth = 1.5; ctx.stroke()
                }
                ctx.fillStyle = win.col("timeline_bg", "#0a0d12")
                ctx.textAlign = "center"; ctx.textBaseline = "middle"
                ctx.fillText(label, lx + w / 2, top + h / 2 + 0.5)
                ctx.textBaseline = "alphabetic"
            }
            // The guide goes down FIRST: when two markers land close together
            // the one you can DRAG has to stay readable, and this one you
            // cannot move.
            // Centre-of-video guide: a violet CHIP on a dashed stem, so it
            // belongs to the same family as the IN/OUT/SPLIT markers instead of
            // being a second arrowhead competing with the playhead. Dashed is
            // what keeps it readable as a GUIDE rather than a position you set.
            var cgx = t2x(totalDuration / 2)
            if (totalDuration > 0 && cgx >= pad - 2 && cgx <= width - pad + 2) {
                ctx.strokeStyle = win.colA("center_guide", "#c084fc", 0.20); ctx.lineWidth = 5
                ctx.beginPath(); ctx.moveTo(cgx, CHIP_BOT); ctx.lineTo(cgx, height - 5); ctx.stroke()
                ctx.strokeStyle = win.col("center_guide", "#c084fc"); ctx.lineWidth = 2; ctx.setLineDash([5, 4])
                ctx.beginPath(); ctx.moveTo(cgx, CHIP_BOT); ctx.lineTo(cgx, height - 5); ctx.stroke(); ctx.setLineDash([])
                // Just the word. It used to be a chip PLUS a separate clock pill
                // whose band overlapped the chip's, which sliced the word in
                // half. Carrying the time inside the chip fixed that but made
                // the chip 130px wide, which then buried whatever SPLIT sat
                // near it. The centre is always duration/2 and hovering reads
                // the time off the ruler, so the word alone is enough here --
                // the classic can afford "Center hh:mm:ss" because its ruler is
                // tall enough to give the pill a row of its own.
                chip(cgx, "center_guide", "#c084fc", false, "CENTER")
            }
            chip(xi, "marker_in", "#2ddc7f", win.selMarker === "in", "IN")
            chip(xo, "marker_out", "#d29922", win.selMarker === "out", "OUT")
            // Splits get the same flag as IN/OUT (the classic draws all three
            // identically); as a bare line they read as a gridline.
            for (var k = 0; k < separatorPoints.length; ++k) {
                var kSel = (k === win.selSplit)
                chip(t2x(Number(separatorPoints[k])),
                     kSel ? "split_marker_sel" : "split_marker",
                     kSel ? "#7dd3fc" : "#38bdf8", kSel, "SPLIT")
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
            // A downward ARROW over a thick stem -- the classic's shape, which
            // is what makes the playhead readable as "the frame you are on"
            // rather than as one more vertical marker. The rounded handle this
            // replaces was a tag with a grip mark: it pointed at nothing, and
            // at chip width it was hard to tell from a SPLIT chip.
            Canvas {
                id: ctiHead
                x: -13; y: 0; width: 26; height: tl.chipTop + 2
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    var w = width, tip = height - 1
                    c.fillStyle = win.col("playhead", "#ff4d55")
                    c.strokeStyle = win.col("playhead_halo", "#3b82f6")
                    c.lineWidth = 2; c.lineJoin = "round"
                    c.beginPath()
                    c.moveTo(1, 1); c.lineTo(w - 1, 1); c.lineTo(w / 2, tip)
                    c.closePath(); c.fill(); c.stroke()
                }
                Component.onCompleted: requestPaint()
                Connections { target: win; function onPaletteRev() { ctiHead.requestPaint() } }
            }
            // Stem: a wide same-colour glow under a crisp core, from the arrow
            // tip down. colA() returns a CSS rgba() string, which is Canvas-only,
            // so a QML color property cannot parse it -- the alpha lives on
            // opacity instead.
            Rectangle {
                x: -3.5; y: tl.chipTop; width: 7; height: cti.height - tl.chipTop
                color: win.col("playhead", "#ff4d55"); opacity: 0.22
            }
            Rectangle {
                x: -1.5; y: tl.chipTop; width: 3; height: cti.height - tl.chipTop
                color: win.col("playhead", "#ff4d55")
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
                x: Math.max(0, Math.min(tl.width - width, hov.hx - width / 2)); y: tl.chipBot + 2
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
