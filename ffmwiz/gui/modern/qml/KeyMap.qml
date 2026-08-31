// Every keyboard shortcut the editor answers to, in one place.
//
// Split out of UnifiedEditor.qml: a flat run of 35 Shortcut declarations is
// one responsibility, and keeping it beside the layout meant the file that
// defines the WINDOW also defined the key map. The printed hints in the
// footer are generated from nothing -- they are written by hand -- so this
// file and that line have to be changed together.
import QtQuick
import QtQuick.Controls

Item {
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
    Shortcut { sequence: "+"; onActivated: { win.zoomAt(1.25, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Shortcut { sequence: "="; onActivated: { win.zoomAt(1.25, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Shortcut { sequence: "-"; onActivated: { win.zoomAt(0.8, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Shortcut { sequence: "H"; onActivated: win.tool = "hand" }
    Shortcut { sequence: "Z"; onActivated: win.tool = "zoom" }
    Shortcut { sequence: "Ctrl+0"; onActivated: resetPreviewView() }
    Shortcut { sequence: "Ctrl++"; onActivated: pvZoomAt(1.25, previewArea.width / 2, previewArea.height / 2) }
    Shortcut { sequence: "Ctrl+="; onActivated: pvZoomAt(1.25, previewArea.width / 2, previewArea.height / 2) }
    Shortcut { sequence: "Ctrl+-"; onActivated: pvZoomAt(0.8, previewArea.width / 2, previewArea.height / 2) }
}
