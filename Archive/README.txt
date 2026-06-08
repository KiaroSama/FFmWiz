FFmWiz — Archived GUI
=====================

ffmwiz_gui_original_2026-06-08.py
    This is the ORIGINAL production GUI that lived at
        assets/runtime/ffmwiz_gui.py
    before it was replaced (on 2026-06-08) with the improved build that had
    been developed and tested under the Preview/ folder.

    What the new build adds over this original:
      - Much faster startup (window appears in ~1.6s instead of ~5-8s; the side
        control panels stream in after the first paint).
      - Cheap, anti-aliased waveform rendering (smooth at extreme zoom, no lag).
      - Live in-GUI reverse preview (reversed proxy chunks, synced audio).
      - Fix: timeline view no longer jumps to the start when a reverse chunk
        hands off to the next.
      - Fix: Mark IN / Mark OUT markers are now the same height as SPLIT.

To restore this original:
    Copy this file back over assets/runtime/ffmwiz_gui.py and rename it to
    ffmwiz_gui.py, then change near the top:
        ASSETS_ROOT = Path(__file__).resolve().parents[1]
    (the original already uses that form — no edit needed if copied as-is).

Nothing else in the project was changed by the replacement except:
    - assets/runtime/ffmwiz_gui.py  (replaced with the new build)
    - assets/icons/spin_up.svg, assets/icons/spin_down.svg  (added: crop
      spin-box arrow icons the new build references)
