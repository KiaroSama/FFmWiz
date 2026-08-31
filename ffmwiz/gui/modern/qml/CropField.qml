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

RowLayout {
    property string label: ""
    property int maxv: 9999
    property int v: 0
    signal edited(int value)
    spacing: 6
    Label { text: parent.label; color: Skin.col("text_mute", "#8891b4"); Layout.preferredWidth: 14 }
    Stepper {
        from: 0; to: parent.maxv; value: parent.v; editable: true
        Layout.fillWidth: true
        onValueModified: parent.edited(value)
    }
}
