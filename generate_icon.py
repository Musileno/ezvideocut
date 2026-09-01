#!/usr/bin/env python3
"""
Pure Python .ico Generator for EZVideo Cut
Icon: Scissors cutting a film strip (Makas + Film Şeridi)
Multi-resolution ICO without PIL or external dependencies.
"""

import struct
import math

def create_scissors_film_image(size: int) -> bytes:
    """Renders a BGRA image buffer of scissors cutting a film strip."""
    pixels = bytearray(size * size * 4)
    
    def set_pixel(x, y, r, g, b, a):
        if 0 <= x < size and 0 <= y < size:
            # ICO DIB stores bottom-to-top
            idx = ((size - 1 - y) * size + x) * 4
            # Alpha blending
            cur_a = pixels[idx + 3] / 255.0
            new_a = a / 255.0
            out_a = new_a + cur_a * (1.0 - new_a)
            if out_a > 0:
                pixels[idx] = int((b * new_a + pixels[idx] * cur_a * (1.0 - new_a)) / out_a)
                pixels[idx + 1] = int((g * new_a + pixels[idx + 1] * cur_a * (1.0 - new_a)) / out_a)
                pixels[idx + 2] = int((r * new_a + pixels[idx + 2] * cur_a * (1.0 - new_a)) / out_a)
                pixels[idx + 3] = int(out_a * 255)

    s = size / 64.0  # Scale factor based on 64x64 coordinate space
    
    # 1. Background glow rounded box
    for y in range(size):
        for x in range(size):
            dx = x - size / 2
            dy = y - size / 2
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < size * 0.48:
                # Dark background circle/squircle
                set_pixel(x, y, 15, 22, 36, 240)
            elif dist < size * 0.50:
                set_pixel(x, y, 0, 210, 255, 120)

    # 2. Draw Film Strip (horizontal bar between y = 24*s and y = 40*s)
    film_y1 = int(24 * s)
    film_y2 = int(40 * s)
    for y in range(film_y1, film_y2):
        for x in range(int(6 * s), int(58 * s)):
            set_pixel(x, y, 30, 41, 59, 255)  # dark slate film base

    # Film strip border
    for x in range(int(6 * s), int(58 * s)):
        set_pixel(x, film_y1, 255, 152, 0, 255)
        set_pixel(x, film_y2 - 1, 255, 152, 0, 255)

    # Film perforations (holes)
    hole_w = max(1, int(3 * s))
    hole_h = max(1, int(2.5 * s))
    hole_xs = [int(p * s) for p in [10, 20, 30, 40, 50]]
    for hx in hole_xs:
        for y in range(film_y1 + int(1.5 * s), film_y1 + int(1.5 * s) + hole_h):
            for x in range(hx, hx + hole_w):
                set_pixel(x, y, 255, 193, 7, 255)
        for y in range(film_y2 - int(4 * s), film_y2 - int(4 * s) + hole_h):
            for x in range(hx, hx + hole_w):
                set_pixel(x, y, 255, 193, 7, 255)

    # 3. Draw Scissors Handles
    # Top Handle (Circle at (18*s, 16*s))
    h1_cx, h1_cy, h_r = 18 * s, 16 * s, 7 * s
    h2_cx, h2_cy = 18 * s, 48 * s
    thick = max(1.2, 2.5 * s)
    for y in range(size):
        for x in range(size):
            d1 = math.sqrt((x - h1_cx)**2 + (y - h1_cy)**2)
            if abs(d1 - h_r) < thick:
                set_pixel(x, y, 0, 210, 255, 255)
            d2 = math.sqrt((x - h2_cx)**2 + (y - h2_cy)**2)
            if abs(d2 - h_r) < thick:
                set_pixel(x, y, 0, 210, 255, 255)

    # 4. Scissors Blades (Crossing lines through pivot (32*s, 32*s))
    pv_x, pv_y = 32 * s, 32 * s
    # Blade 1: from (22*s, 20*s) to (54*s, 46*s)
    # Blade 2: from (22*s, 44*s) to (54*s, 18*s)
    for t in [i / 200.0 for i in range(200)]:
        # Blade 1
        bx1 = (20 * (1 - t) + 54 * t) * s
        by1 = (20 * (1 - t) + 44 * t) * s
        for ox in [-1, 0, 1]:
            for oy in [-1, 0, 1]:
                set_pixel(int(bx1 + ox), int(by1 + oy), 0, 210, 255, 255)
        # Blade 2
        bx2 = (20 * (1 - t) + 54 * t) * s
        by2 = (44 * (1 - t) + 20 * t) * s
        for ox in [-1, 0, 1]:
            for oy in [-1, 0, 1]:
                set_pixel(int(bx2 + ox), int(by2 + oy), 0, 210, 255, 255)

    # 5. Pivot screw (Center point)
    for y in range(int(pv_y - 2.5 * s), int(pv_y + 3 * s)):
        for x in range(int(pv_x - 2.5 * s), int(pv_x + 3 * s)):
            if math.sqrt((x - pv_x)**2 + (y - pv_y)**2) <= 2.5 * s:
                set_pixel(x, y, 255, 255, 255, 255)

    # 6. Cut Flash Sparkle at the tip of blade cut
    sp_x, sp_y = int(36 * s), int(32 * s)
    set_pixel(sp_x, sp_y, 255, 255, 255, 255)
    set_pixel(sp_x + 1, sp_y, 255, 255, 255, 230)
    set_pixel(sp_x - 1, sp_y, 255, 255, 255, 230)
    set_pixel(sp_x, sp_y + 1, 255, 255, 255, 230)
    set_pixel(sp_x, sp_y - 1, 255, 255, 255, 230)

    return bytes(pixels)

def create_ico(sizes=[16, 32, 48, 64, 128, 256], filename="app_icon.ico"):
    """Packages pure BMP DIB images into a valid multi-size Windows .ico file."""
    images_data = []
    
    for sz in sizes:
        img_bytes = create_scissors_film_image(sz)
        # BITMAPINFOHEADER (40 bytes)
        # height is doubled in ICO format (image height + mask height)
        header = struct.pack(
            "<LLLHHLLLLLL",
            40,          # biSize
            sz,          # biWidth
            sz * 2,      # biHeight (doubled for ICO XOR + AND mask)
            1,           # biPlanes
            32,          # biBitCount (32-bit BGRA)
            0,           # biCompression (BI_RGB)
            len(img_bytes), # biSizeImage
            0, 0, 0, 0   # unused
        )
        # 1-bit AND mask (all zeros for 32-bit alpha transparency)
        mask_row_bytes = ((sz + 31) // 32) * 4
        and_mask = bytes(mask_row_bytes * sz)
        
        dib_data = header + img_bytes + and_mask
        images_data.append((sz, dib_data))
    
    # ICO Header: 6 bytes
    # idReserved (0), idType (1 for ICO), idCount (len(sizes))
    ico_header = struct.pack("<HHH", 0, 1, len(sizes))
    
    # Directory entries (16 bytes each)
    dir_entries = []
    offset = 6 + 16 * len(sizes)
    
    for sz, dib_data in images_data:
        w_byte = 0 if sz >= 256 else sz
        h_byte = 0 if sz >= 256 else sz
        entry = struct.pack(
            "<BBBBHHII",
            w_byte,      # bWidth
            h_byte,      # bHeight
            0,           # bColorCount
            0,           # bReserved
            1,           # wPlanes
            32,          # wBitCount
            len(dib_data), # dwBytesInRes
            offset       # dwImageOffset
        )
        dir_entries.append(entry)
        offset += len(dib_data)
        
    with open(filename, "wb") as f:
        f.write(ico_header)
        for entry in dir_entries:
            f.write(entry)
        for _, dib_data in images_data:
            f.write(dib_data)
            
    print(f"[BAŞARILI] {filename} dosyası oluşturuldu! ({len(sizes)} farklı çözünürlük içerir)")

if __name__ == "__main__":
    create_ico()
