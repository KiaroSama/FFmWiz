// What to do next, and what the keys are -- the two lines the classic engine
// has that this one lacked.
//
// Every key printed here is a Shortcut that exists in KeyMap.qml. A printed
// key that does nothing is worse than no help at all, so the two files are
// changed together.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    Layout.fillWidth: true
    Layout.preferredHeight: 48
    ColumnLayout {
        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
        anchors.topMargin: 5; anchors.bottomMargin: 5; spacing: 2
        Label {
            Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 11
            color: win.col("text", "#e8edfb")
            text: {
                if (cuts.length === 0 && separatorPoints.length === 0)
                    return "Cut ranges and Splits: none. Mark In/Out and press Cut Selection (A), or press S at the playhead to split the final output into parts."
                return "Cut ranges: " + cuts.length + "  \u2022  Splits: " + separatorPoints.length
                     + "  \u2022  the output keeps " + fmt(keepTotal()) + " of " + fmt(totalDuration)
                     + (separatorPoints.length > 0 ? " across " + (separatorPoints.length + 1) + " parts." : ".")
            }
        }
        Label {
            Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 10
            color: win.col("text_mute", "#8891b4")
            text: "Space play/pause  \u2022  I/O mark  \u2022  A cut  \u2022  S split  \u2022  Del remove  "
                + "\u2022  Ctrl+I convert mark  \u2022  Ctrl+Shift+I invert cuts  \u2022  Ctrl+U overlay  \u2022  Ctrl+R reset crop  "
                + "\u2022  , / . frame step  \u2022  Shift+\u2190/\u2192 5s  \u2022  Ctrl+Alt+\u2190/\u2192 cut edge  \u2022  M mute  \u2022  Ctrl+Z/Y undo/redo"
        }
    }
}
