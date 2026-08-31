// The editor's left control column: crop, speed/audio, cuts and split.
//
// Split out of UnifiedEditor.qml, which carried the whole engine in one
// file. It reaches the editing state through `win` -- the root
// ApplicationWindow -- which a child .qml can still see by id because it
// is instantiated INSIDE that window's tree, unlike the reusable controls
// beside it that take their palette from the root context instead.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia

Card {
            id: leftPanel
            // Two columns of readable buttons need this much. At 384 the right
            // column was clipped: nothing about the LAYOUT was wrong, the
            // panel was simply narrower than the content it holds --
            // verified by widening it and watching every column complete.
            SplitView.preferredWidth: 470
            SplitView.minimumWidth: 400
            ScrollView {
                id: leftScroll
                anchors.fill: parent
                anchors.margins: 12
                contentWidth: availableWidth
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    // Measured from the CARD, not from the ScrollView. Binding
                    // this to availableWidth while the ScrollView binds
                    // contentWidth back to it is a loop, and it settled wider
                    // than the panel -- every 2-column grid then laid out at
                    // ~438px inside a 360px viewport and its right column was
                    // clipped. 24 = the ScrollView margins, 14 = the scrollbar.
                    width: leftScroll.availableWidth
                    spacing: 16

                    // PLAYBACK -- first, and in the panel, exactly as the classic
                    // lays it out: a full-width primary Play, the CTI clock centred
                    // directly under it, then a 2-column seek grid. This used to be a
                    // strip of ~34px squares under the timeline, which is why the
                    // controls read as small, colourless and detached from the panel.
                    SectionLabel { text: "PLAYBACK"; Layout.bottomMargin: Tok.sp1 }
                    PadButton {
                        Layout.fillWidth: true
                        implicitHeight: 34
                        text: actP().playbackState === MediaPlayer.PlayingState
                              ? "❚❚  Pause (Space)" : "▶  Play (Space)"
                        baseColor: win.col("accent", "#3b82f6")
                        hoverColor: win.col("accent_hover", "#5b9bff")
                        pressedColor: win.col("accent_pressed", "#2563eb")
                        textColor: win.col("text_on_accent", "#ffffff")
                        accentColor: win.col("accent", "#3b82f6")
                        onClicked: togglePlay()
                    }
                    // The clock the classic puts here. It was only in the transport
                    // strip before, at body size; the CTI is the number you read most.
                    Label {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: win.fmt(win.cti)
                        color: win.col("text", "#e8edfb")
                        font.pixelSize: 17
                        font.family: "Consolas"
                    }
                    Label {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: win.fmt(win.totalDuration) + "   ·   f " + win.curFrame() + "/" + win.totalFrames()
                        color: win.col("text_mute", "#8891b4")
                        font.pixelSize: Tok.fsMicro
                        font.family: "Consolas"
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: Tok.sp1
                        columns: 2; rowSpacing: 6; columnSpacing: 8
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "⏮  Home"; onClicked: seekTo(0) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "End  ⏭"; onClicked: seekTo(totalDuration) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "-5s"; autoRepeat: true; onClicked: seekTo(cti - 5) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "-1s"; autoRepeat: true; onClicked: seekTo(cti - 1) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "+1s"; autoRepeat: true; onClicked: seekTo(cti + 1) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "+5s"; autoRepeat: true; onClicked: seekTo(cti + 5) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "◀|  Frame"; onClicked: frameStep(-1) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Frame  |▶"; onClicked: frameStep(1) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "⟝  Prev Edge"; onClicked: seekCutEdge(-1) }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Next Edge  ⟞"; onClicked: seekCutEdge(1) }
                    }

                    RowLayout {
                        Layout.fillWidth: true; Layout.topMargin: Tok.sp1; spacing: Tok.sp2
                        PadButton { Layout.preferredWidth: 92; text: win.muted ? "Unmute (M)" : "Mute (M)"
                                    onClicked: win.muted = !win.muted }
                        Track { Layout.fillWidth: true; from: 0; to: 1; value: win.volume
                                onMoved: { win.volume = value; win.muted = false } }
                        Label { text: Math.round(win.volume * 100) + "%"; color: win.col("text_mute", "#8891b4")
                                font.pixelSize: Tok.fsBody; Layout.preferredWidth: 34 }
                    }

                    SectionLabel { text: "CUTS & SPLIT"; Layout.topMargin: Tok.sp2; Layout.bottomMargin: Tok.sp1 }
                    Toggle { text: "Magnetic snapping"; checked: snapEnabled; onToggled: snapEnabled = checked }
                    GridLayout {
                        Layout.fillWidth: true; columns: 2; rowSpacing: Tok.sp2; columnSpacing: Tok.sp2
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Mark In (I)"; baseColor: win.col("btn_mark_in_bg", "#123a29"); textColor: win.col("btn_mark_in_text", "#d7ffe8"); accentColor: win.col("marker_in", "#2ddc7f"); onClicked: setMarkIn() }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Mark Out (O)"; baseColor: win.col("btn_mark_out_bg", "#3b2b12"); textColor: win.col("btn_mark_out_text", "#ffe7b8"); accentColor: win.col("marker_out", "#d29922"); onClicked: setMarkOut() }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Cut Selection (A)"; baseColor: win.col("green", "#238636"); hoverColor: win.col("green_hover", "#2ea043"); pressedColor: win.col("green_pressed", "#196c2e"); accentColor: win.col("green", "#238636"); textColor: win.col("text_on_accent", "#ffffff"); onClicked: cutSelection() }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Delete Cut"; baseColor: win.col("danger_cut", "#7f123f"); accentColor: win.col("danger_cut_hover", "#a51b55"); hoverColor: win.col("danger_cut_hover", "#a51b55"); pressedColor: win.col("danger_cut_pressed", "#5e0d2e"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteCutAtCti() }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Add Split (S)"; baseColor: win.col("btn_split_bg", "#075985"); textColor: win.col("text_on_accent", "#ffffff"); accentColor: win.col("btn_split_border", "#0ea5e9"); onClicked: addSplit() }
                        PadButton { Layout.fillWidth: true; Layout.preferredWidth: 1; text: "Del Split"; baseColor: win.col("danger_alt", "#643618"); accentColor: win.col("danger_alt_hover", "#8a4a1f"); hoverColor: win.col("danger_alt_hover", "#8a4a1f"); pressedColor: win.col("danger_alt_pressed", "#4d2812"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteSplitAtCti() }
                    }
                    PadButton { Layout.fillWidth: true; text: "Clear Cuts"; baseColor: win.col("danger", "#a40e26"); accentColor: win.col("danger_hover", "#c9303f"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: { cuts = []; selCut = -1; tl.requestPaint(); commit() } }
                    // One column, not two: these four labels carry their shortcut and
                    // a half-width button elides them to "Invert Cuts (Ctrl+S...",
                    // which is worse than printing no shortcut at all.
                    GridLayout {
                        Layout.fillWidth: true; columns: 1; rowSpacing: Tok.sp2; columnSpacing: Tok.sp2
                        PadButton { Layout.fillWidth: true; text: "Invert Cuts (Ctrl+Shift+I)"; baseColor: win.col("purple", "#7c3aed"); accentColor: win.col("purple_hover", "#8b5cf6"); hoverColor: win.col("purple_hover", "#8b5cf6"); pressedColor: win.col("purple_pressed", "#6d28d9"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: invertCutsAll() }
                        PadButton { Layout.fillWidth: true; enabled: selMarker !== ""
                            // Disabled it reads "Select Marker": the classic engine
                            // relabels the same button, which is how you learn the
                            // action needs a selected mark first.
                            text: selMarker !== "" ? "Convert In/Out (Ctrl+I)" : "Select Marker"
                            onClicked: convertMarker() }
                        PadButton { Layout.fillWidth: true; text: "Delete Selected (Del)"; baseColor: win.col("danger", "#a40e26"); accentColor: win.col("danger_hover", "#c9303f"); hoverColor: win.col("danger_hover", "#c9303f"); pressedColor: win.col("danger_pressed", "#7d0a1c"); textColor: win.col("text_on_accent", "#ffffff");  onClicked: deleteSelection() }
                        PadButton { Layout.fillWidth: true; text: "Prev/Next edge (Ctrl+Alt+\u2190/\u2192)"; onClicked: seekCutEdge(1) }
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: {
                            var ow = sourceW - cropLeft - cropRight, oh = sourceH - cropTop - cropBottom
                            var cropTxt = (cropTop + cropLeft + cropRight + cropBottom) > 0 ? ("Crop \u2192 " + ow + "\u00d7" + oh) : "No crop"
                            return cropTxt + "  \u2022  Speed " + Math.round(speed * 100) + "%" + (reverse ? "  \u2022  Reversed" : "")
                                + "\n" + cuts.length + " cut(s) \u2022 " + separatorPoints.length + " split(s) \u2022 " + chapters.length + " chapter(s)"
                                + "\nKept: " + fmt(keepTotal()) + " of " + fmt(totalDuration)
                                + "\nIn " + (markIn > 0 ? fmt(markIn) : "--")
                                + "   \u2022   Out " + (markOut < totalDuration ? fmt(markOut) : "--")
                        }
                        color: win.col("marker_in", "#2ddc7f"); font.pixelSize: 11
                    }

                    Item { Layout.fillHeight: true }
                    SectionLabel { text: "CROP (pixels)"; Layout.bottomMargin: Tok.sp1 }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2; rowSpacing: 8; columnSpacing: 10
                        CropField { Layout.fillWidth: true; label: "T"; maxv: sourceH; v: cropTop; onEdited: (value) => { cropTop = value; commit() } }
                        CropField { Layout.fillWidth: true; label: "B"; maxv: sourceH; v: cropBottom; onEdited: (value) => { cropBottom = value; commit() } }
                        CropField { Layout.fillWidth: true; label: "L"; maxv: sourceW; v: cropLeft; onEdited: (value) => { cropLeft = value; commit() } }
                        CropField { Layout.fillWidth: true; label: "R"; maxv: sourceW; v: cropRight; onEdited: (value) => { cropRight = value; commit() } }
                    }
                    Toggle { text: "Edit crop on preview"; checked: cropEdit; onToggled: cropEdit = checked }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        PadButton { Layout.fillWidth: true; text: "Reset Crop (Ctrl+R)"; onClicked: resetCrop() }
                        Toggle { text: "Overlay (Ctrl+U)"; checked: cropOverlayOn; onToggled: cropOverlayOn = checked }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        PadButton { Layout.fillWidth: true; text: "Hand (H)"; iconSource: "../../../assets/icons/tool_hand.svg"; baseColor: win.tool === "hand" ? win.col("accent", "#3b82f6") : win.col("surface_modern", "#2d323a"); onClicked: win.tool = "hand" }
                        PadButton { Layout.fillWidth: true; text: "Zoom (Z)"; iconSource: "../../../assets/icons/tool_zoom.svg"; baseColor: win.tool === "zoom" ? win.col("accent", "#3b82f6") : win.col("surface_modern", "#2d323a"); onClicked: win.tool = "zoom" }
                        PadButton { Layout.preferredWidth: 62; text: "Reset"; onClicked: resetPreviewView() }
                    }
                    Label { text: "Preview zoom: " + Math.round(pvZoom * 100) + "%   \u2022   Tool: " + tool; color: win.col("text_mute", "#8891b4"); font.pixelSize: 11 }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 10
                        color: win.col("text_mute", "#8891b4")
                        text: "Crop is auto-aligned to even dimensions to keep the chroma phase correct so the video colors are not damaged."
                    }

                    Rule { Layout.topMargin: Tok.sp1; Layout.bottomMargin: Tok.sp1 }

                    SectionLabel { text: "SPEED & AUDIO"; Layout.topMargin: Tok.sp2; Layout.bottomMargin: Tok.sp1 }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Label { text: "Speed"; color: win.col("text", "#e8edfb") }
                        Picker {
                            id: speedBox
                            Layout.fillWidth: true
                            editable: true
                            model: ["25%", "50%", "75%", "100%", "125%", "150%", "200%", "300%", "400%", "500%", "800%", "1000%"]
                            currentIndex: 3
                            function applyText(txt) {
                                var raw = String(txt)
                                var v = parseFloat(raw.replace("%", "").replace("x", "").replace("X", ""))
                                if (isNaN(v) || v <= 0) return
                                // "2x" or a bare value <= 10 means a factor; otherwise it is a percent.
                                var f = (raw.toLowerCase().indexOf("x") >= 0 || v <= 10) ? v : v / 100.0
                                speed = Math.max(0.1, Math.min(10.0, f)); commit()
                            }
                            onActivated: applyText(currentText)
                            onAccepted: applyText(editText)
                            Component.onCompleted: { var idx = model.indexOf(Math.round(speed * 100) + "%"); if (idx >= 0) currentIndex = idx; else editText = Math.round(speed * 100) + "%" }
                            WheelHandler {
                                onWheel: (ev) => {
                                    var stepv = (ev.modifiers & Qt.ControlModifier) ? 0.25 : 0.05
                                    speed = Math.max(0.1, Math.min(10.0, speed + (ev.angleDelta.y > 0 ? stepv : -stepv)))
                                    speedBox.editText = Math.round(speed * 100) + "%"; commit()
                                }
                            }
                        }
                    }
                    // `applyText` above takes "2x" as readily as "200%", but nothing
                    // said so, so the factor spelling the classic engine offers as its
                    // own dropdown looked missing here.
                    Label { text: "percent or factor \u2014 200% and 2x are the same"; color: win.col("text_mute", "#8891b4"); font.pixelSize: 10 }
                    Toggle { text: "Reverse video"; checked: reverse; onToggled: {
                            reverse = checked; commit()
                            if (!checked) { revActive = false; bridge.cancelReverse(); actP().playbackRate = 1.0; var s = segmentForTime(cti); loadSegment(s.index, s.local, false) }
                        } }
                    Toggle { text: "Include audio"; checked: includeAudio; enabled: hasAudio; onToggled: { includeAudio = checked; commit() } }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        // Only worth the space when there is a choice to make.
                        visible: audioTrackCount() > 1
                        Label { text: "Track"; color: win.col("text", "#e8edfb") }
                        Picker {
                            id: audioTrackBox
                            Layout.fillWidth: true
                            model: audioTrackModel()
                            currentIndex: audioTrack
                            onActivated: { audioTrack = currentIndex; applyAudioTrack() }
                        }
                    }
                    Label {
                        visible: audioTrackCount() > 1
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 10
                        color: win.col("text_mute", "#8891b4")
                        text: "Preview only — which track is encoded stays the wizard's audio question."
                    }

                    Rule { Layout.topMargin: Tok.sp1; Layout.bottomMargin: Tok.sp1 }

                }
            }
        }
