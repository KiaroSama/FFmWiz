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

// A group break as one word. It was a Rectangle literal with a hand-picked
// colour each time, and it used `border` -- a weight meant for outlining a
// shape, which reads a shade too strong drawn as a rule across a panel.
Rectangle {
    Layout.fillWidth: true
    implicitHeight: 1
    color: Skin.col("border_soft", "#21262d")
}
