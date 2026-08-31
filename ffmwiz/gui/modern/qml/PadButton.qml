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

Button {
    id: pb
    property color baseColor: Skin.col("surface_modern", "#2d323a")
    // Tokenised interaction states. Computing them with Qt.lighter/darker
    // produced different hexes from the Qt QSS for the same role, so the two
    // engines hovered differently (USER-12-2). The computed values remain the
    // default for buttons whose base colour has no hover/pressed token.
    property color hoverColor: (pb.baseColor == Skin.col("surface_modern", "#2d323a"))
                               ? Skin.col("surface_hover_modern", "#3a414a") : Qt.lighter(pb.baseColor, 1.18)
    property color pressedColor: (pb.baseColor == Skin.col("surface_modern", "#2d323a"))
                                 ? Skin.col("surface_pressed_modern", "#282d35") : Qt.darker(pb.baseColor, 1.25)
    property color textColor: Skin.col("text", "#e8edfb")
    // The classic pairs a DARK tinted fill with a BRIGHT border in the role
    // colour; that border is what makes the button read as green/red/blue at a
    // glance. Outlining only on hover left every button the same grey shape.
    property color accentColor: "transparent"
    property url iconSource: ""
    implicitHeight: Tok.rowMd
    padding: Tok.sp1 + 2
    hoverEnabled: true
    // Pointing-hand cursor on hover/click so buttons feel clickable.
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        radius: Tok.radSm
        color: pb.down ? pb.pressedColor : (pb.hovered ? pb.hoverColor : pb.baseColor)
        // Outline only while hovered. A permanent 1px border around every
        // button turns a control panel into a grid of boxes; the fill
        // already separates the button from the panel, and the outline is
        // then free to mean "you are pointing at this".
        // Hover outlines; keyboard focus outlines in the accent. Without the
        // second case, tabbing through the panel moved an invisible cursor.
        border.color: pb.visualFocus ? Skin.col("accent", "#3b82f6")
                    : (pb.hovered ? Skin.col("border_strong", "#3a4150") : pb.accentColor)
        border.width: 1
    }
    contentItem: RowLayout {
        spacing: 6
        Image {
            visible: String(pb.iconSource) !== ""
            Layout.preferredWidth: visible ? 16 : 0
            Layout.preferredHeight: 16
            Layout.alignment: Qt.AlignVCenter
            source: pb.iconSource
            sourceSize.width: 16; sourceSize.height: 16
            fillMode: Image.PreserveAspectFit
            opacity: pb.enabled ? 1.0 : 0.5
        }
        Label {
            Layout.fillWidth: true
            // Zero, so the caption cannot impose its full text width as the
            // button's implicit minimum. It could, and a long label like
            // "Invert Cuts (Ctrl+Shift+I)" then widened its whole GRID column
            // past the panel, clipping the right-hand column. Elide handles
            // the overflow; this just stops it dictating the layout.
            Layout.preferredWidth: 0
            text: pb.text
            color: pb.enabled ? pb.textColor : Skin.col("text_mute", "#8891b4")
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 13
            elide: Text.ElideRight
        }
    }
}
