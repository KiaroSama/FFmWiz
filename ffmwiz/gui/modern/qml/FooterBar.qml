// Reset, cancel and confirm. Split out of UnifiedEditor.qml.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    Layout.fillWidth: true
    Layout.preferredHeight: 56
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
        PadButton { text: "Reset all"; implicitWidth: 110
            onClicked: { cropTop = cropLeft = cropRight = cropBottom = 0; speed = 1.0; reverse = false; includeAudio = hasAudio; markIn = 0; markOut = totalDuration; cuts = []; separatorPoints = []; speedBox.currentIndex = 3; zoom = 1.0; viewStart = 0; cropEdit = false; cropOverlayOn = true; muted = false; selMarker = ""; selSplit = -1; selCut = -1; timelinePanel.canvas.requestPaint(); commit() } }
        PadButton { text: "Reset Panels"; implicitWidth: 130; onClicked: leftPanel.SplitView.preferredWidth = 340 }
        Item { Layout.fillWidth: true }
        PadButton { text: "Cancel (Esc)"; implicitWidth: 150; implicitHeight: 40; baseColor: win.col("danger", "#a40e26"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: bridge.cancel() }
        PadButton { text: "Confirm (Enter)"; implicitWidth: 180; implicitHeight: 40; baseColor: win.col("green", "#238636"); hoverColor: win.col("green_hover", "#2ea043"); pressedColor: win.col("green_pressed", "#196c2e"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: bridge.submit(buildResult()) }
    }
}
