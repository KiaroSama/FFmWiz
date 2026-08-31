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

// Border all the way round or not at all, and a flat fill -- a one-sided
// rule reads as a rendering artefact and a gradient ground fights the
// video, which is the only thing in this window that should hold colour.
Rectangle {
    radius: Tok.radMd
    color: Skin.col("panel", "#161b22")
    border.color: Skin.col("border_soft", "#21262d")
    border.width: 1
}
