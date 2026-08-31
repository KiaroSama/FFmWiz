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

ComboBox {
    id: cb
    implicitHeight: Tok.rowMd
    font.pixelSize: Tok.fsBody
    background: Rectangle {
        radius: Tok.radSm
        color: Skin.col("surface", "#21262d")
        border.color: cb.activeFocus ? Skin.col("accent", "#3b82f6")
                                     : Skin.col("border_strong", "#3a4150")
        border.width: 1
    }
    contentItem: Label {
        text: cb.editable ? cb.editText : cb.displayText
        color: Skin.col("text", "#e8edfb"); font: cb.font
        verticalAlignment: Text.AlignVCenter
        leftPadding: Tok.sp2; rightPadding: Tok.sp2
        elide: Text.ElideRight
    }
}
