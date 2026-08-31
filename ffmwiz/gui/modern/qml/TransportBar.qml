// The timeline's zoom and view controls, plus volume.
//
// The play/seek transport that used to live here moved into the left panel,
// where the classic editor keeps it: as ~34px squares in a horizontal strip
// the controls read as small, uniform and detached from everything else.
// What belongs down here is what acts on the TIMELINE, and the classic gives
// each of those a text label and its own colour rather than a bare slider.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    Layout.fillWidth: true
    spacing: 8

    Label { text: "Timeline zoom"; color: win.col("text_mute", "#8891b4"); font.pixelSize: Tok.fsBody }
    PadButton { Layout.preferredWidth: 30; text: "−"; onClicked: { win.zoomAt(0.8, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Track {
        Layout.preferredWidth: 150
        from: 0; to: 100
        fillColor: win.col("accent", "#3b82f6")
        value: 100 * Math.log(Math.max(1, win.zoom)) / Math.log(400)
        onMoved: {
            win.zoom = Math.max(1, Math.pow(400, value / 100))
            var sp = win.viewSpan(); win.viewStart = win.cti - sp / 2; win.clampView(); timelinePanel.canvas.requestPaint()
        }
    }
    PadButton { Layout.preferredWidth: 30; text: "+"; onClicked: { win.zoomAt(1.25, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Label { text: (Math.round(win.zoom * 100) / 100) + "×"; color: win.col("text", "#e8edfb")
            font.pixelSize: Tok.fsBody; font.family: "Consolas"; Layout.preferredWidth: 46 }
    PadButton { Layout.preferredWidth: 44; text: "Fit"; onClicked: { win.fitZoom(); timelinePanel.canvas.requestPaint() } }

    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
           // A FIXED height, never fillHeight: a filling child makes the whole
           // row demand vertical space, and it took it from the video preview.
           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
           Layout.leftMargin: Tok.sp1; Layout.rightMargin: Tok.sp1 }

    // The second slider the classic has and this bar did not: scrolling the
    // visible window when the timeline is zoomed in. Without it a zoomed view
    // could only be moved by dragging the canvas.
    Label { text: "Timeline view"; color: win.col("text_mute", "#8891b4"); font.pixelSize: Tok.fsBody }
    Track {
        Layout.fillWidth: true
        Layout.minimumWidth: 120
        from: 0; to: 1
        enabled: win.zoom > 1.0001
        opacity: enabled ? 1.0 : 0.45
        fillColor: win.col("green_text", "#56d364")
        value: {
            var hidden = Math.max(0, win.totalDuration - win.viewSpan())
            return hidden <= 0 ? 0 : Math.min(1, Math.max(0, win.viewStart / hidden))
        }
        onMoved: {
            var hidden = Math.max(0, win.totalDuration - win.viewSpan())
            win.viewStart = hidden * value; win.clampView(); timelinePanel.canvas.requestPaint()
        }
    }

}
