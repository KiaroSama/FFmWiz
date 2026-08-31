// Play/seek, the clock and frame counter, volume, and timeline zoom --
// four clusters separated by hairlines rather than one flat run of ten
// controls at even spacing.
//
// Split out of UnifiedEditor.qml; reads playback state through `win`.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

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
           Layout.leftMargin: Tok.sp1; Layout.rightMargin: Tok.sp1 }
    Label { text: fmt(cti) + " / " + fmt(totalDuration); color: win.col("text", "#e8edfb"); font.pixelSize: Tok.fsLead; font.family: "Consolas" }
    Label { text: "f " + curFrame() + "/" + totalFrames(); color: win.col("text_mute", "#8891b4"); font.pixelSize: 10; font.family: "Consolas" }
    Item { Layout.fillWidth: true }
    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
           // A FIXED height, never fillHeight: a filling child makes the
           // whole transport row demand vertical space, and it took it
           // from the video preview -- which collapsed to a thumbnail
           // with the controls floating in the middle of the gap.
           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
           Layout.leftMargin: Tok.sp1; Layout.rightMargin: Tok.sp1 }
    PadButton { Layout.preferredWidth: 66; text: win.muted ? "Unmute" : "Mute"; onClicked: win.muted = !win.muted }
    Track { Layout.preferredWidth: 88; from: 0; to: 1; value: win.volume; onMoved: { win.volume = value; win.muted = false } }
    PadButton { Layout.preferredWidth: 34; text: "\u2212"; onClicked: { win.zoomAt(0.8, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    Rule { Layout.preferredWidth: 1; Layout.fillWidth: false
           // A FIXED height, never fillHeight: a filling child makes the
           // whole transport row demand vertical space, and it took it
           // from the video preview -- which collapsed to a thumbnail
           // with the controls floating in the middle of the gap.
           Layout.preferredHeight: 16; Layout.alignment: Qt.AlignVCenter
           Layout.leftMargin: Tok.sp1; Layout.rightMargin: Tok.sp1 }
    Track {
        Layout.preferredWidth: 120
        from: 0; to: 100
        value: 100 * Math.log(Math.max(1, win.zoom)) / Math.log(400)
        onMoved: {
            win.zoom = Math.max(1, Math.pow(400, value / 100))
            var sp = win.viewSpan(); win.viewStart = win.cti - sp / 2; win.clampView(); timelinePanel.canvas.requestPaint()
        }
    }
    Label { text: (Math.round(win.zoom * 100) / 100) + "\u00d7"; color: win.col("text_mute", "#8891b4"); font.pixelSize: 11 }
    PadButton { Layout.preferredWidth: 34; text: "+"; onClicked: { win.zoomAt(1.25, cti, 0.5); timelinePanel.canvas.requestPaint() } }
    PadButton { Layout.preferredWidth: 46; text: "Fit"; onClicked: { win.fitZoom(); timelinePanel.canvas.requestPaint() } }
}
