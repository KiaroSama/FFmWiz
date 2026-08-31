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
        var spanPx = Math.max(1, timelinePanel.canvas.width - 2 * timelinePanel.canvas.pad)
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
        timelinePanel.canvas.requestPaint()
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
    // The design tokens moved to the `Tok` context property so the split .qml
    // files read the SAME 4px grid, two radii and type scale. Keeping a copy
    // here as well would have been a second source for one set of numbers --
    // exactly the drift the token block was introduced to end.
    readonly property int fsMicro: 10     // captions, hints, section titles
    readonly property int fsBody: 11      // labels and button text
    readonly property int fsLead: 12      // values worth reading first
    readonly property int fsTitle: 14     // the app title, once
    readonly property int rowSm: 22       // inline control
    readonly property int rowMd: 26       // standard button
    readonly property int rowLg: 32       // primary action



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
            timelinePanel.canvas.requestPaint()
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
        var w = Math.max(16, Math.round(timelinePanel.canvas.width - 2 * timelinePanel.canvas.pad))
        try {
            var s = bridge.waveformWindow(a, b, w)
            win.wf = JSON.parse(s) || []
        } catch (e) { /* keep previous wf */ }
        timelinePanel.canvas.requestPaint()
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
        videoOutput: previewPanel.videoA
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
        videoOutput: previewPanel.videoB
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
        var c = cuts.slice(); c.push([lo, hi]); cuts = normRanges(c); timelinePanel.canvas.requestPaint(); commit()
    }
    function deleteCutAtCti() {
        var c = []
        for (var i = 0; i < cuts.length; ++i) if (!(cti >= cuts[i][0] - 0.001 && cti <= cuts[i][1] + 0.001)) c.push(cuts[i])
        cuts = c; timelinePanel.canvas.requestPaint(); commit()
    }
    function deleteSplitAtCti() {
        var best = -1, bd = 1e9
        for (var i = 0; i < separatorPoints.length; ++i) { var d = Math.abs(Number(separatorPoints[i]) - cti); if (d < bd) { bd = d; best = i } }
        var px = timelinePanel.canvas.t2x(cti)
        if (best >= 0 && bd / Math.max(0.001, totalDuration) * (timelinePanel.canvas.width - 12) < 14) {
            var sp = separatorPoints.slice(); sp.splice(best, 1); separatorPoints = sp; timelinePanel.canvas.requestPaint(); commit()
        }
    }
    function addSplit() {
        var sp = separatorPoints.slice(); sp.push(snapTime(cti, -1, null)); separatorPoints = sp; timelinePanel.canvas.requestPaint(); commit()
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
        cuts = invertRanges(cuts, totalDuration); selCut = -1; timelinePanel.canvas.requestPaint(); commit()
    }
    // Convert the selected mark in<->out (classic convert_selected_marker).
    function convertMarker() {
        if (selMarker === "in") { markOut = markIn; markIn = totalDuration + 1; selMarker = "out" }
        else if (selMarker === "out") { markIn = markOut; markOut = -1; selMarker = "in" }
        else return
        // clamp the "cleared" sentinel back into range visually
        if (markIn > totalDuration) markIn = 0
        if (markOut < 0) markOut = totalDuration
        commit(); timelinePanel.canvas.requestPaint()
    }
    // Delete whatever is selected (classic delete_selection routing).
    function deleteSelection() {
        if (selMarker === "in") { markIn = 0; selMarker = ""; commit(); timelinePanel.canvas.requestPaint(); return }
        if (selMarker === "out") { markOut = totalDuration; selMarker = ""; commit(); timelinePanel.canvas.requestPaint(); return }
        if (selSplit >= 0 && selSplit < separatorPoints.length) {
            var sp = separatorPoints.slice(); sp.splice(selSplit, 1); separatorPoints = sp; selSplit = -1; commit(); timelinePanel.canvas.requestPaint(); return
        }
        if (selCut >= 0 && selCut < cuts.length) {
            var c = cuts.slice(); c.splice(selCut, 1); cuts = c; selCut = -1; commit(); timelinePanel.canvas.requestPaint(); return
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
    function resetCrop() { cropTop = cropLeft = cropRight = cropBottom = 0; timelinePanel.canvas.requestPaint(); commit() }
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
            LeftPanel { id: leftPanel }

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

                PreviewPanel { id: previewPanel }

                TimelinePanel { id: timelinePanel }
                }

                TransportBar { Layout.fillWidth: true }
            }
        }

        GuidanceBar { Layout.fillWidth: true }

        FooterBar { Layout.fillWidth: true }
    }

    KeyMap { }
}
