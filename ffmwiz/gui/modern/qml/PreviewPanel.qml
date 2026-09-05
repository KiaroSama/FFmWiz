// The video surface: two MediaPlayers for gapless join playback, the pan/zoom
// layer, and the interactive crop overlay -- all in ONE coordinate space so
// the overlay maps 1:1 onto preview pixels.
//
// Split out of UnifiedEditor.qml; reads the editing state through `win`.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Card {
    id: previewPanel
    // Published for the root window: its two MediaPlayers name these as
    // their videoOutput, and an id inside this file is not visible there.
    property alias videoA: voA
    property alias videoB: voB

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
            // Distance from the press point, NOT a boolean set by the first
            // mouse move. `dragged = true` on any movement meant a real click
            // -- which almost always travels a pixel or two between press and
            // release -- disarmed the zoom tool, so clicking with it did
            // nothing. The classic canvas uses a 4px threshold for exactly
            // this (`abs(dx) + abs(dy) < 4` in its mouseReleaseEvent).
            readonly property int clickSlop: 4
            property bool dragged: false
            property real zoomStart: 1.0
            property bool zooming: false
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
                if (Math.abs(m.x - mPressX) + Math.abs(m.y - mPressY) > clickSlop)
                    dragged = true
                if (zooming) {
                    win.pvZoomAt(zoomStart * Math.pow(2, (mPressY - m.y) / 110.0)
                                 / win.pvZoom, mPressX, mPressY)
                    return
                }
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
                } else if (win.tool === "zoom") {
                    // Dragging with the zoom tool zooms continuously, as the
                    // classic canvas does (2**(dy/110) from the press point).
                    // Without this a drag with this tool did nothing at all.
                    zooming = true; zoomStart = win.pvZoom; lastX = m.x; lastY = m.y
                } else if (win.tool === "hand") {
                    panning = true; lastX = m.x; lastY = m.y
                }
            }
            onReleased: (m) => {
                if (activeHandle !== "") commit()
                else if (win.tool === "zoom" && !dragged) win.pvZoomAt((m.modifiers & Qt.AltModifier) ? (1.0 / 1.25) : 1.25, m.x, m.y)
                activeHandle = ""; panning = false; zooming = false
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
