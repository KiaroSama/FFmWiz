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

Slider {
    id: sl
    implicitHeight: Tok.rowSm
    background: Rectangle {
        x: sl.leftPadding; y: sl.topPadding + sl.availableHeight / 2 - height / 2
        width: sl.availableWidth; height: 3; radius: 1.5
        color: Skin.col("surface", "#21262d")
        Rectangle {
            width: sl.visualPosition * parent.width; height: parent.height
            radius: parent.radius; color: Skin.col("accent", "#3b82f6")
        }
    }
    handle: Rectangle {
        x: sl.leftPadding + sl.visualPosition * (sl.availableWidth - width)
        y: sl.topPadding + sl.availableHeight / 2 - height / 2
        width: 12; height: 12; radius: 6
        color: sl.pressed ? Skin.col("accent_hover", "#5b9bff") : Skin.col("text", "#e8edfb")
        border.color: Skin.col("border_strong", "#3a4150")
    }
}
