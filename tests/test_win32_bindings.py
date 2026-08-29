"""Every Win32 call this project makes declares its types.

`ctypes` defaults an unbound argument and return value to `c_int` -- 32 bits.
A Windows HANDLE, HWND, HDC or HBITMAP is 64 bits on a 64-bit build, so an
unbound call hands back a TRUNCATED handle. The nastiest part is that it can
look like it works: when the real handle's upper 32 bits are all ones, sign
extension rebuilds the value by luck, and the bug only appears for a handle
without that shape.

The declarations are the contract, so that is what these tests read. Forcing an
actual truncation would need a process handle above 2**31, which nothing here
can arrange on demand.
"""
import os
import unittest

windows_only = unittest.skipUnless(os.name == "nt", "Win32 bindings are Windows-only")


@windows_only
class TheWatchdogCallsAreBound(unittest.TestCase):

    def setUp(self):
        from ffmwiz.gui import gui_common
        self.kernel32, self.shell32 = gui_common._bound_win32()

    def test_a_process_handle_is_returned_and_taken_as_a_pointer(self):
        import ctypes
        self.assertIs(ctypes.c_void_p, self.kernel32.OpenProcess.restype)
        for name in ("WaitForSingleObject", "CloseHandle"):
            with self.subTest(call=name):
                self.assertEqual(ctypes.c_void_p,
                                 getattr(self.kernel32, name).argtypes[0],
                                 f"{name} takes the handle as its first argument")

    def test_open_process_takes_its_three_arguments(self):
        import ctypes
        self.assertEqual([ctypes.c_uint, ctypes.c_int, ctypes.c_uint],
                         self.kernel32.OpenProcess.argtypes)

    def test_the_app_id_call_is_bound_as_wide_text_returning_an_hresult(self):
        import ctypes
        call = self.shell32.SetCurrentProcessExplicitAppUserModelID
        self.assertEqual([ctypes.c_wchar_p], call.argtypes)
        self.assertIs(ctypes.c_long, call.restype)

    def test_the_declarations_are_made_once(self):
        # The watchdog asks on every tick. Re-declaring per call would be waste,
        # and `functools.cache` is what keeps it to one.
        from ffmwiz.gui import gui_common
        self.assertIs(self.kernel32, gui_common._bound_win32()[0])


@windows_only
class TheIconCallsAreStillBound(unittest.TestCase):
    """These were already correct. This keeps them that way."""

    def test_the_native_icon_path_declares_pointer_types(self):
        src = __import__("pathlib").Path(
            __file__).resolve().parent.parent / "ffmwiz" / "gui" / "gui_common.py"
        text = src.read_text(encoding="utf-8")
        for call in ("LoadImageW", "SendMessageW"):
            with self.subTest(call=call):
                self.assertIn(f"{call}.restype = ctypes.c_void_p", text)
                self.assertIn(f"{call}.argtypes", text)


class TheWatchdogAnswersCorrectly(unittest.TestCase):
    """Behaviour, not declarations -- and it must hold on every platform."""

    def test_this_process_is_alive(self):
        from ffmwiz.gui import gui_common
        self.assertTrue(gui_common._parent_process_is_alive(os.getpid()))

    def test_a_pid_that_cannot_exist_is_not_alive(self):
        from ffmwiz.gui import gui_common
        # 0x7FFFFFFF is above every real Windows pid and every Linux pid_max.
        self.assertFalse(gui_common._parent_process_is_alive(0x7FFFFFFF))

    def test_no_parent_means_keep_running(self):
        # A missing or nonsense pid must not shut the editor down.
        from ffmwiz.gui import gui_common
        for pid in (None, 0, -1):
            with self.subTest(pid=pid):
                self.assertTrue(gui_common._parent_process_is_alive(pid))


if __name__ == "__main__":
    unittest.main()
