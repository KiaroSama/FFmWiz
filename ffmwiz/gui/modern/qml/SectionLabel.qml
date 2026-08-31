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

Label {
    color: Skin.col("text_mute", "#8891b4")
    font.bold: true
    font.pixelSize: Tok.fsMicro
    font.letterSpacing: 0.8
    font.capitalization: Font.AllUppercase
    topPadding: 2
    bottomPadding: 2
}
