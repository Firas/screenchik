"""Driver for the QDtech 'USB-Display' (VID 1908:0102) — an AX206 DPF.

Vendored from https://github.com/sunzhengya/ax206-usb-display-macos
(GPL-3.0), confirmed working on this exact hardware (VID:PID 1908:0102,
QTKeJi.Ltd "USB-Display") and macOS.

CRITICAL: this firmware only implements the BLIT command (CDB op 0x12).
Any other vendor command (INQUIRY, GETLCD, SETPROPERTY/brightness) times
out and wedges the USB endpoint, requiring a physical unplug/replug to
recover. Never call get_lcd_info() / set_brightness() on this unit.
"""
from __future__ import annotations

import struct
import time
from typing import Optional

import numpy as np
import usb.core
import usb.util
from PIL import Image

VID = 0x1908
PID = 0x0102
EP_OUT = 0x01
EP_IN = 0x81

NATIVE_WIDTH = 480
NATIVE_HEIGHT = 320

USBCMD_SETPROPERTY = 0x01
USBCMD_BLIT = 0x12
PROPERTY_BRIGHTNESS = 0x01

DIR_OUT = 0x00
DIR_IN = 0x80


def to_rgb565_be(img: Image.Image) -> bytes:
    arr = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r = arr[:, :, 0]
    g = arr[:, :, 1]
    b = arr[:, :, 2]
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565.astype(">u2").tobytes()


class AX206Display:
    def __init__(self, width: int = NATIVE_WIDTH, height: int = NATIVE_HEIGHT) -> None:
        self.dev: Optional[usb.core.Device] = None
        self.width = width
        self.height = height

    def open(self) -> "AX206Display":
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise RuntimeError(f"AX206 display {VID:#06x}:{PID:#06x} not found")
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass
        try:
            usb.util.claim_interface(dev, 0)
        except usb.core.USBError:
            pass
        for ep in (EP_OUT, EP_IN):
            try:
                dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        self.dev = dev
        return self

    def close(self) -> None:
        if self.dev is not None:
            try:
                usb.util.release_interface(self.dev, 0)
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self.dev)
            self.dev = None

    def __enter__(self) -> "AX206Display":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _bulk_out(self, data: bytes, timeout: int = 8000, retries: int = 2) -> int:
        assert self.dev is not None
        last = None
        for _ in range(retries + 1):
            try:
                return self.dev.write(EP_OUT, data, timeout=timeout)
            except usb.core.USBError as e:
                last = e
                if e.errno in (5, 32):
                    try:
                        self.dev.clear_halt(EP_OUT)
                    except usb.core.USBError:
                        pass
                    time.sleep(0.05)
                    continue
                raise
        raise last

    def _bulk_in(self, length: int, timeout: int = 4000) -> bytes:
        assert self.dev is not None
        return bytes(self.dev.read(EP_IN, length, timeout=timeout))

    def recover(self) -> None:
        if self.dev is None:
            return
        try:
            self.dev.ctrl_transfer(0x21, 0xFF, 0x0000, 0x0000, None, timeout=500)
        except usb.core.USBError:
            pass
        for ep in (EP_OUT, EP_IN):
            try:
                self.dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        for _ in range(3):
            try:
                self.dev.read(EP_IN, 64, timeout=60)
            except usb.core.USBError:
                break

    def reopen(self) -> bool:
        self.close()
        time.sleep(0.4)
        try:
            self.open()
            return True
        except Exception:
            return False

    @staticmethod
    def _cbw(data_len: int, direction: int, cdb: bytes) -> bytes:
        assert len(cdb) == 16
        return (b"USBC"
                + b"\xde\xad\xbe\xef"
                + struct.pack("<I", data_len)
                + bytes([direction, 0x00, 0x10])
                + cdb)

    def _read_csw(self, retries: int = 5) -> int:
        last = None
        for _ in range(retries):
            try:
                csw = self._bulk_in(13, timeout=2000)
            except usb.core.USBError as e:
                last = e
                continue
            if len(csw) >= 13 and csw[:4] == b"USBS":
                return csw[12]
            last = RuntimeError(f"bad CSW: {csw.hex()}")
        raise last if last else RuntimeError("no CSW")

    def _command(self, cdb: bytes, direction: int = DIR_OUT,
                 data: bytes = b"", in_len: int = 0) -> bytes:
        block_len = in_len if direction == DIR_IN else len(data)
        self._bulk_out(self._cbw(block_len, direction, cdb))
        result = b""
        if direction == DIR_OUT and data:
            self._bulk_out(data)
        elif direction == DIR_IN and in_len:
            result = self._bulk_in(in_len)
        status = self._read_csw()
        if status != 0:
            raise RuntimeError(f"command CSW status = {status}")
        return result

    def blit(self, x0: int, y0: int, x1: int, y1: int, pixels_rgb565_be: bytes) -> None:
        w, h = x1 - x0, y1 - y0
        if len(pixels_rgb565_be) != w * h * 2:
            raise ValueError(f"need {w*h*2} bytes, got {len(pixels_rgb565_be)}")
        cdb = bytearray(16)
        cdb[0] = 0xCD
        cdb[5] = 0x06
        cdb[6] = USBCMD_BLIT
        struct.pack_into("<HHHH", cdb, 7, x0, y0, x1 - 1, y1 - 1)
        self._command(bytes(cdb), DIR_OUT, data=pixels_rgb565_be)

    def fill(self, rgb: tuple[int, int, int] = (0, 0, 0)) -> None:
        img = Image.new("RGB", (self.width, self.height), rgb)
        self.blit(0, 0, self.width, self.height, to_rgb565_be(img))

    def clear(self) -> None:
        self.fill((0, 0, 0))

    def draw_image(self, img: Image.Image, x: int = 0, y: int = 0,
                    fit: str = "stretch") -> None:
        if fit == "stretch":
            frame = img.convert("RGB").resize((self.width, self.height))
            self.blit(0, 0, self.width, self.height, to_rgb565_be(frame))
            return
        if fit == "contain":
            canvas = Image.new("RGB", (self.width, self.height), (0, 0, 0))
            src = img.convert("RGB")
            src.thumbnail((self.width, self.height))
            ox = (self.width - src.width) // 2
            oy = (self.height - src.height) // 2
            canvas.paste(src, (ox, oy))
            self.blit(0, 0, self.width, self.height, to_rgb565_be(canvas))
            return
        src = img.convert("RGB")
        w = min(src.width, self.width - x)
        h = min(src.height, self.height - y)
        if w <= 0 or h <= 0:
            return
        src = src.crop((0, 0, w, h))
        self.blit(x, y, x + w, y + h, to_rgb565_be(src))
