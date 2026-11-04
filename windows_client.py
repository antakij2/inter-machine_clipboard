#TODO:
# Make it start automatically on logon with task scheduler
# Any error in any thread notifies user via pop up window. Have wrapper logic: try: [create and run client] except Exception as e: MessageBox(e)
import socketio
import win32clipboard as wclip
import win32con as wcon
from win32com.shell import shellcon as wsc  # noqa
import win32gui as wg
import winerror as we
import win32api as wa
from striprtf.striprtf import rtf_to_text
import struct
import os
import mimetypes
import ctypes
from ctypes import wintypes
import threading
import time
import json
import re
import webbrowser
import tarfile
from argparse import ArgumentParser as AP

CLIPBOARD = 'clipboard'
MIMETYPE = 'mimetype'
FILENAME = 'filename'
CLIPBOARD_RETRIES = 5
CLIPBOARD_RETRY_MULTIPLIER = 2
TEMP_DIR = os.environ['TEMP']

rich_text_fmt = wclip.RegisterClipboardFormat('Rich Text Format')
png_fmt = wclip.RegisterClipboardFormat('PNG')
filenamew_fmt = wclip.RegisterClipboardFormat(wsc.CFSTR_FILENAMEW)
sendable_formats_to_mimetypes = {
    wcon.CF_HDROP : None,
    png_fmt : 'image/png',
    rich_text_fmt : 'text/rtf',
    wcon.CF_UNICODETEXT : 'text/plain'
}

WM_CLIPBOARDUPDATE = 0x031D
WM_TRAYICON = wcon.WM_USER
ICON_ID = 0
OPEN_TAB_LINKS_IN_FIREFOX = 1

rtf_link_list_pattern = re.compile(r'(?<={\\field{\\\*\\fldinst{HYPERLINK \").+(?=\"})')

send_clipboard_data_lock = threading.Lock()

class IMC_Client:
    @staticmethod
    def get_authorization_header():
        with open('authorization_header.json', encoding='utf-8') as file:
            return json.load(file)

    def __init__(self, server_hostname, server_port):
        # Web links extracted from an RTF format list of links
        self.links = []

        # Allow use of (Add/Remove)ClipboardFormatListener since it's not exposed in pywin32
        self.user32_lib = ctypes.WinDLL('user32', use_last_error=True)

        self.user32_lib.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        self.user32_lib.AddClipboardFormatListener.restype = wintypes.BOOL

        self.user32_lib.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        self.user32_lib.RemoveClipboardFormatListener.restype = wintypes.BOOL

        # Create the websocket client that will communicate with the server
        self.client = socketio.Client(logger=True, engineio_logger=True)
        self.client.on('server to client', self.server_to_client_handler)
        # TODO: let user give URL as command line argument
        self.client.connect(f'http://{server_hostname}:{server_port}', transports=['websocket'])
        # At this point, the websocket client is already listening in an event loop for websocket events/data from the server

        # Create an invisible Windows window so that this app can be notified of clipboard updates and other window messages
        window_class = wg.WNDCLASS()
        window_class.lpszClassName = 'imc_dummy_window'
        window_class.lpfnWndProc = {
            WM_CLIPBOARDUPDATE : self.on_clipboard_update,
            wcon.WM_CLOSE : self.on_close,
            WM_TRAYICON : self.on_tray_icon,
            wcon.WM_COMMAND : self.on_command
        }
        class_atom = wg.RegisterClass(window_class)
        self.hwnd = wg.CreateWindow(
            class_atom, # className
            'imc', # windowName
            wcon.WS_DISABLED, # style
            0, 0, # x, y
            0, 0, # width, height
            None, # parent
            None, # menu
            None, # hinstance
            None  # reserved
        )

        # Put an icon for this app in the Windows system tray
        icon_file_path = os.path.join(os.path.dirname(__file__), 'clipboard.ico')
        icon = wg.LoadImage(None, icon_file_path, wcon.IMAGE_ICON, 0, 0, wcon.LR_LOADFROMFILE)
        nid = (
            self.hwnd, # hWnd
            ICON_ID, # ID
            wg.NIF_ICON | wg.NIF_MESSAGE | wg.NIF_TIP, # Flags
            WM_TRAYICON, # CallbackMessage
            icon, # hIcon
            'Inter-machine Clipboard' # Tip
        )

        wg.Shell_NotifyIcon(wg.NIM_ADD, nid)
        res = self.user32_lib.AddClipboardFormatListener(self.hwnd)
        if not res:
            raise ctypes.WinError(ctypes.get_last_error())

        print('about to pump messages')
        # Enter event loop to process Windows window messages
        try:
            wg.PumpMessages()
        finally:
            wg.UnregisterClass(class_atom, None)
            self.client.shutdown()

    def open_clipboard(self):
        clipboard_start_time = time.time()
        for i in range(CLIPBOARD_RETRIES):
            try:
                #TODO: if this fails because another app has the clipboard open, no exception seems to be raised normally.
                # when running in Windows Terminal, Windows Terminal freezes for several seconds and then recovers. When in the 
                # terminal in PyCharm, PyCharm itself shows a notification window saying the clipboard isn't available
                wclip.OpenClipboard(self.hwnd)
            except wclip.error as e:
                if e.winerror != we.ERROR_ACCESS_DENIED:
                    raise

                if i == CLIPBOARD_RETRIES - 1:
                    raise Exception(f'OpenClipboard failed after {CLIPBOARD_RETRIES} tries')

                time.sleep((time.time() - clipboard_start_time) * CLIPBOARD_RETRY_MULTIPLIER)
            else:
                return

    #TODO: There is still the issue of sending server data back if this method executes and then WM_CLIPBOARDUPDATE gets processed. Right?
    def server_to_client_handler(self, data):
        payload = data[CLIPBOARD]
        mimetype_general, mimetype_specific = data[MIMETYPE].split('/')

        self.open_clipboard()
        try:
            wclip.EmptyClipboard()
            if mimetype_general == 'text':
                payload_unicode = payload.decode('utf-8')
                if mimetype_specific == 'rtf' or mimetype_specific == 'richtext':
                    wclip.SetClipboardData(rich_text_fmt, payload)

                    plain_payload_unicode = rtf_to_text(payload_unicode)
                    wclip.SetClipboardData(wcon.CF_UNICODETEXT, plain_payload_unicode)
                else:
                    wclip.SetClipboardData(wcon.CF_UNICODETEXT, payload_unicode)

            else:
                # TODO: A: to avoid name clashes, maybe make 'inter-machine_clipboard' folder in TEMP to store things in
                # TODO: B: use werkzeug safe_join to test for malicious filename?
                # TODO: C: does iphone check for malicious filenames when you save something to Files?
                tempfile_abs_path = os.path.join(TEMP_DIR, data[FILENAME])
                with open(tempfile_abs_path, 'wb') as f: 
                    f.write(payload)

                if mimetype_general == 'application' and mimetype_specific == 'x-gzip':
                    # the server sent an archive of multiple files
                    with tarfile.open(tempfile_abs_path, 'r:gz') as archive:
                        archive.extractall(TEMP_DIR, filter='data')

                        files = [os.path.join(TEMP_DIR, name) for name in archive.getnames()]
                else:
                    files = [tempfile_abs_path]

                file_list = ('\0'.join(files) + '\0\0').encode('utf-16le')
                DROPFILES_STRUCT = struct.pack(
                    "<Lllii",
                    20,  # pFiles
                    0, 0,  # pt (x, y)
                    0,  # fNC
                    1  # fWide
                )

                fifteen_data = DROPFILES_STRUCT + file_list

                wclip.SetClipboardData(wcon.CF_HDROP, fifteen_data)
                wclip.SetClipboardData(filenamew_fmt, (files[0] + '\0\0').encode('utf-16le'))
        finally:
            wclip.CloseClipboard()

    def on_clipboard_update(self, hwnd, msg, wp, lp):
        print('on_clipboard_update')
        # Make sure the window that just changed the clipboard is not our own
        try:
            clipboard_owner_handle = wclip.GetClipboardOwner()
        except wclip.error as e:
            if e.winerror != we.NO_ERROR:
                raise

            clipboard_owner_handle = None

        if clipboard_owner_handle != self.hwnd:
            # It was another app that changed the clipboard, so send the appropriate clipboard data (if any) to the server
            data = None
            self.open_clipboard()
            try:
                for fmt, mimetype in sendable_formats_to_mimetypes.items(): # TODO: use GetPriorityClipboardFormat
                    try:
                        data = wclip.GetClipboardData(fmt)
                    except TypeError:
                        continue
                    else:
                        break
            finally:
                wclip.CloseClipboard()

            if data:
                filename = 'clipboard'
                #TODO: if user copies a folder, just (recursively?) pile its contents into a flat archive. But if user copies a big folder
                # they don't intend to copy to another device, that's a lot of unnecessary work. Make it an explicit option in the client
                # icon's right click menu to compress and send a folder that's already on the clipboard, to the server.
                if fmt == wcon.CF_HDROP:
                    # We found CF_HDROP data on the clipboard, so what we have is a path(s) to the file we want to send to
                    #  the server. If it's 1 path, we'll send that file's contents, and if its multiple paths, we'll
                    #  create a tarball out of those files and send that
                    if len(data) == 1:
                        filename = os.path.basename(data[0]) # TODO: B: use werkzeug safe_join to test for malicious filename?
                        mimetype, encoding = mimetypes.guess_file_type(data[0]) # TODO: check for older python version and use older function
                        contentfile = data[0]
                    else:
                        archive_basename = 'clipboard.tar.gz'
                        archive_abs_path = os.path.join(TEMP_DIR, archive_basename)
                        with tarfile.open(archive_abs_path, 'w:gz') as archive:
                            for path in data:
                                archive.add(path, arcname=os.path.basename(path))

                        filename = archive_basename
                        mimetype, encoding = mimetypes.guess_file_type(archive_abs_path)
                        contentfile = archive_abs_path

                    with open(contentfile, 'rb') as file:
                        data = file.read()

                elif fmt == wcon.CF_UNICODETEXT:
                    data = data.encode('utf-8')

                with send_clipboard_data_lock:
                    print(f'emitting to server: {filename}')
                    self.client.emit(
                        'client to server',
                        {
                            # TODO: send encoding too?
                            MIMETYPE: mimetype,
                            FILENAME: filename,
                            CLIPBOARD: data
                        }
                    )

        return we.NO_ERROR

    def on_tray_icon(self, hwnd, msg, wparam, lparam):
        #TODO: add menu option to re-pull from server and put content on clipboard again 
        if lparam == wcon.WM_LBUTTONUP:
            wg.PostMessage(self.hwnd, wcon.WM_CLOSE)

        elif lparam == wcon.WM_RBUTTONUP:
            # Check if the clipboard is holding rtf data, gray the "open links" menu item out if it doesn't
            menu_item_appearance = wcon.MF_GRAYED
            if wclip.IsClipboardFormatAvailable(rich_text_fmt):
                self.links = []
                self.open_clipboard()
                try:
                    self.links = rtf_link_list_pattern.findall(wclip.GetClipboardData(rich_text_fmt).decode('utf-8'))
                finally:
                    wclip.CloseClipboard()

                if self.links:
                    menu_item_appearance = wcon.MF_STRING

            menu = wg.CreatePopupMenu()
            wg.AppendMenu(menu, menu_item_appearance, OPEN_TAB_LINKS_IN_FIREFOX, "Open URLs in browser")

            x, y = wa.GetCursorPos()

            # Required so menu dismisses correctly
            wg.SetForegroundWindow(self.hwnd)

            wg.TrackPopupMenu(
                menu,   # hMenu
                wcon.TPM_LEFTALIGN | wcon.TPM_BOTTOMALIGN, # uFlags
                x, y,   # coordinates
                0,  # (reserved, must be 0)
                self.hwnd,  # hWnd (the owner of the menu)
                None    # (ignored)
            )

        return we.NO_ERROR

    def on_close(self, hwnd, msg, wp, lp):
        print('on_close')
        res = self.user32_lib.RemoveClipboardFormatListener(self.hwnd)
        if not res:
            raise ctypes.WinError(ctypes.get_last_error())

        wg.Shell_NotifyIcon(wg.NIM_DELETE, (self.hwnd, ICON_ID))
        wg.DestroyWindow(self.hwnd)
        wg.PostQuitMessage(we.NO_ERROR)
        return we.NO_ERROR

    def on_command(self, hwnd, msg, wp, lp):
        print('on_command')
        if wp == OPEN_TAB_LINKS_IN_FIREFOX:
            for l in self.links:
                webbrowser.open_new_tab(l)

        return we.NO_ERROR

if __name__ == '__main__':
    DEFAULT_PORT = '3000'

    parser = AP(description='Windows client for Inter-machine Clipboard (IMC)')
    parser.add_argument('-s', '--server', required=True, help='the host name that the IMC server is running on')
    parser.add_argument('-p', '--port', default=DEFAULT_PORT, 
                        help=f'the port number that the IMC server is listening on (default {DEFAULT_PORT})'
    )
    args = parser.parse_args()

    client = IMC_Client(args.server, args.port)
