#!/usr/bin/env python3
"""Render an EAN-13 barcode as a Y4M video for Chromium's fake camera.

    python3 scripts/make-barcode-y4m.py 460700123456 /tmp/barcode.y4m
    # → prints the full 13-digit code (check digit added)

Chromium plays it as the camera with
    --use-fake-device-for-media-stream --use-file-for-fake-video-capture=/tmp/barcode.y4m
which lets scripts/ui-check.mjs exercise the real zxing scanner end to end.
No dependencies: the bars are written straight into the luma plane.
"""

from __future__ import annotations

import sys
from pathlib import Path

L_CODES = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
G_CODES = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
R_CODES = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def check_digit(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def ean13_modules(code: str) -> str:
    first, left, right = int(code[0]), code[1:7], code[7:]
    bits = "101"
    for digit, parity in zip(left, PARITY[first], strict=True):
        bits += (L_CODES if parity == "L" else G_CODES)[int(digit)]
    bits += "01010"
    for digit in right:
        bits += R_CODES[int(digit)]
    return bits + "101"


def render(code: str, out: Path, width: int = 640, height: int = 480, frames: int = 15) -> None:
    modules = ean13_modules(code)
    module_px = 4
    bar_width = len(modules) * module_px
    x0 = (width - bar_width) // 2
    top, bottom = height // 4, height * 3 // 4
    luma = bytearray([235]) * (width * height)
    for y in range(top, bottom):
        row = y * width
        for index, bit in enumerate(modules):
            if bit == "1":
                start = row + x0 + index * module_px
                luma[start : start + module_px] = bytes([16]) * module_px
    chroma = bytes([128]) * ((width // 2) * (height // 2))
    with out.open("wb") as handle:
        handle.write(f"YUV4MPEG2 W{width} H{height} F15:1 Ip A1:1 C420jpeg\n".encode())
        for _ in range(frames):
            handle.write(b"FRAME\n")
            handle.write(luma)
            handle.write(chroma)
            handle.write(chroma)


if __name__ == "__main__":
    digits = "".join(ch for ch in sys.argv[1] if ch.isdigit())
    if len(digits) == 12:
        digits += check_digit(digits)
    if len(digits) != 13 or check_digit(digits[:12]) != digits[12]:
        sys.exit("need 12 digits (check digit is added) or a valid 13-digit EAN")
    render(digits, Path(sys.argv[2]))
    print(digits)
