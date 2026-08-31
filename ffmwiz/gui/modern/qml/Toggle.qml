// Part of the FFmWiz modern editor, split out of UnifiedEditor.qml so no
// single QML file carries the whole engine.
//
// Colours and metrics come from the `Palette` and `Tok` context properties,
// not from a parent window: a separate file cannot see the root object, but
// every file shares the root CONTEXT. `gui_style.PALETTE` stays the one
// source of colour.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Switch {
    id: sw
    implicitHeight: Tok.rowMd
    font.pixelSize: Tok.fsBody
    indicator: Rectangle {
        implicitWidth: 30; implicitHeight: 16
        x: sw.leftPadding; y: (sw.height - height) / 2
        radius: height / 2
        color: sw.checked ? Skin.col("accent", "#3b82f6") : Skin.col("surface", "#21262d")
        border.color: sw.visualFocus ? Skin.col("accent_hover", "#5b9bff")
                                     : Skin.col("border_strong", "#3a4150")
        border.width: 1
        opacity: sw.enabled ? 1.0 : 0.45
        Behavior on color { ColorAnimation { duration: 110 } }
        Rectangle {
            x: sw.checked ? parent.width - width - 2 : 2
            y: 2; width: 12; height: 12; radius: 6
            color: Skin.col("text", "#e8edfb")
            Behavior on x { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
        }
    }
    contentItem: Label {
        text: sw.text; font: sw.font
        color: Skin.col("text", "#e8edfb")
        opacity: sw.enabled ? 1.0 : 0.45
        verticalAlignment: Text.AlignVCenter
        leftPadding: sw.indicator.width + Tok.sp2
    }
}
