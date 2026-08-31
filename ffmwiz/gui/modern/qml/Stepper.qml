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

SpinBox {
    id: sb
    implicitHeight: Tok.rowMd
    font.pixelSize: Tok.fsBody
    background: Rectangle {
        radius: Tok.radSm
        color: Skin.col("surface", "#21262d")
        border.color: sb.activeFocus ? Skin.col("accent", "#3b82f6")
                                     : Skin.col("border_strong", "#3a4150")
        border.width: 1
    }
}
