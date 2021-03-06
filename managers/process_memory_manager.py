import ctypes
import functools
import logging
import time
from typing import List

import psutil
import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageGrab

from managers.loop_manager import Manager


class Process:
    def __init__(self, process_id: int, process_name: str, window_name: str, base_address):
        self.process_id = process_id
        self.process_name = process_name

        self.base_address = base_address
        self.window_name = window_name

        self.__last_window_handle = None
        self.__last_window_thread_id = None

    @functools.cached_property
    def process_handle(self):
        """We don't use the handle we got while getting the process by name,
        because that handle contains sub modules,
        which we don't want while reading the memory."""
        return ctypes.windll.kernel32.OpenProcess(win32con.PROCESS_VM_READ, False, self.process_id)

    @functools.cached_property
    def window_handle(self):
        """To focus, and send inputs"""
        return win32gui.FindWindow(None, self.window_name)

    @functools.cached_property
    def thread_id(self):
        """To focus, and send inputs"""
        return win32process.GetWindowThreadProcessId(self.window_handle)[0]

    def read_memory(self, address, offsets: List = None, byte=False):
        """Reads memory using a base_address and a list of offsets (optional).
        Returns a pointer and a value."""

        if byte is True:
            data = ctypes.c_ubyte(0)
            bytes_read = ctypes.c_ubyte(0)
        else:
            data = ctypes.c_uint(0)  # our data or the address pointer
            bytes_read = ctypes.c_uint(0)  # bytes read

        current_address = address

        if offsets:
            for offset in offsets:
                # Convert to int if its str
                if isinstance(offset, str):
                    offset = int(offset, 16)

                # https://docs.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory
                ctypes.windll.kernel32.ReadProcessMemory(self.process_handle, current_address, ctypes.byref(data),
                                                         ctypes.sizeof(data), ctypes.byref(bytes_read))

                # Replace the address with the new data address
                current_address = data.value + offset
        else:
            # Just read the single memory address
            ctypes.windll.kernel32.ReadProcessMemory(self.process_handle, current_address, ctypes.byref(data),
                                                     ctypes.sizeof(data),
                                                     ctypes.byref(bytes_read))

        # Return a pointer to the value and the value
        # If current offset is `None`, return the value of the last offset
        logging.debug(f"(Address: {hex(current_address)}) Value: {data.value}")
        return current_address, data.value

    def focus(self):
        """Focuses on the process window."""
        self.__last_window_handle = win32gui.GetForegroundWindow()
        # https://docs.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getcurrentthreadid
        self.__last_window_thread_id = win32api.GetCurrentThreadId()

        if self.__last_window_handle != self.window_handle:
            exc = None

            # try 2 times
            for _ in range(2):
                try:
                    # SetForegroundWindow doesn't work without sending an alt key first
                    Manager.press_and_release('alt', sleep_between=0, precise=True)

                    # https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput
                    win32process.AttachThreadInput(self.__last_window_thread_id, self.thread_id, True)
                    # https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setfocus
                    win32gui.SetForegroundWindow(self.window_handle)

                except Exception as e:
                    # del the attribute to update it
                    delattr(self, 'window_handle')
                    exc = e
                    time.sleep(0.1)
                else:
                    return

            raise exc

    def focus_back_to_last_window(self):
        """Focuses back to the last window that was active before focusing on our process."""
        if self.__last_window_handle != self.window_handle:
            # SetForegroundWindow doesn't work without sending 'alt' first
            Manager.press_and_release('alt', sleep_between=0)

            win32process.AttachThreadInput(self.__last_window_thread_id, self.thread_id, False)
            try:
                win32gui.SetForegroundWindow(self.__last_window_handle)
            except:
                # if we couldn't focus back, just ignore
                pass

    @staticmethod
    def kill_by_name(names: List[str]):
        """Kill every process by specified list of names."""
        names = list(map(lambda x: x.casefold(), names))

        for proc in psutil.process_iter():
            if proc.name().casefold() in names:
                try:
                    proc.kill()
                except psutil.AccessDenied:
                    logging.debug(f'Could not kill process "{proc.name()}". Ignoring.')
                except psutil.NoSuchProcess:
                    pass

    @staticmethod
    def char2key(c):
        """Converts a key to a Windows Virtual Key code."""

        # https://docs.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-vkkeyscanw
        result = win32api.VkKeyScan(c)

        # shift_state = (result & 0xFF00) >> 8
        vk_key = result & 0xFF  # remove the shift state

        return vk_key

    def send_input(self, *keys: str,
                   sleep_between_keys: float = 0,
                   sleep_between_presses: float = 0,
                   focus: bool = True,
                   focus_back: bool = True,
                   send_to_process: bool = False):
        """Sends a key input straight to the process. This took me a lot of time, but it was worth it."""
        if focus is True:  # focus if needed
            self.focus()
            time.sleep(sleep_between_presses)

        for key in keys:
            if send_to_process is False:
                Manager.press_and_release(key, sleep_between=sleep_between_presses, precise=True)
            else:
                # split combination
                _keys = key.split('+')

                # get the virtual key code
                vk = self.char2key(_keys[0])

                if 'ctrl' in _keys:
                    vk = 0x200 | vk

                win32api.SendMessage(self.window_handle, win32con.WM_KEYDOWN, vk,
                                     self._prepare_lparam(win32con.WM_KEYDOWN, vk))
                time.sleep(sleep_between_presses)
                win32api.PostMessage(self.window_handle, win32con.WM_KEYUP, vk,
                                     self._prepare_lparam(win32con.WM_KEYUP, vk))

            time.sleep(sleep_between_keys)

        if focus_back is True:
            self.focus_back_to_last_window()

    def _prepare_lparam(self, message, vk):
        l_param = win32api.MapVirtualKey(vk, 0) << 16

        if message is win32con.WM_KEYDOWN:
            l_param |= 0x00000000
        else:
            l_param |= 0x50000001

        return l_param

    @classmethod
    def get_by_name(cls, process_name: str, window_name: str) -> "Process":
        """Finds a process by name and returns a Process object."""

        for process_id in win32process.EnumProcesses():
            # If process_id is the same as this program, skip it
            if process_id == -1:
                continue

            handle = None
            # Try to read the process memory
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                                              True, process_id)
            except:
                continue
            else:
                # iterate over an array of base addresses of each module
                for base_address in win32process.EnumProcessModules(handle):
                    # Get the name of the module
                    current_name = str(win32process.GetModuleFileNameEx(handle, base_address))

                    # compare it
                    if process_name.casefold() in current_name.casefold():
                        logging.debug(f"Base address of {process_name} ({process_id}): {hex(base_address)}")
                        return cls(process_id, process_name, window_name, base_address)

            finally:
                if handle:
                    # close the handle as we don't need it anymore
                    win32api.CloseHandle(handle)

        raise Exception(f"{process_name} could not be found.")

    # image stuff
    def get_window_size(self) -> (int, int):
        rect = win32gui.GetClientRect(self.window_handle)
        return rect[2], rect[3]

    def client_to_window_coords(self, client_coord_x: int, client_coord_y: int) -> (int, int):
        return win32gui.ClientToScreen(self.window_handle, (int(client_coord_x), int(client_coord_y)))

    def screenshot_captcha(self, captcha_image_size_x, captcha_image_size_y) -> Image:
        # we can use this if we switch to working in background
        # https://stackoverflow.com/questions/53551676/python-screenshot-of-background-inactive-window

        # get window size eg. 1280x768
        window_size_x, window_size_y = self.get_window_size()

        # calculate the captcha coords according to client
        captcha_top_left_coords = (
            (window_size_x / 2) - (captcha_image_size_x / 2),
            (window_size_y / 2) - 22
        )
        captcha_bottom_right_coords = (
            captcha_top_left_coords[0] + captcha_image_size_x,
            captcha_top_left_coords[1] + captcha_image_size_y
        )

        # convert them to actual screen cords
        captcha_top_left_coords = self.client_to_window_coords(*captcha_top_left_coords)
        captcha_bottom_right_coords = self.client_to_window_coords(*captcha_bottom_right_coords)

        # take screenshot
        image = ImageGrab.grab(captcha_top_left_coords + captcha_bottom_right_coords, all_screens=True)

        return image

    def __del__(self):
        """Close the handle when our object gets garbage collected."""
        if self.process_handle:
            ctypes.windll.kernel32.CloseHandle(self.process_handle)
