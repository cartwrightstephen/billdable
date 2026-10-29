"""QR codes as SVG, standard library only, for the two-step sign-in key (R6-05).

Byte mode at error-correction level M, versions 1-40, with the mask that scores lowest on the
standard's four penalty rules. It follows the published QR Code specification (ISO/IEC 18004)
the way Project Nayuki's reference encoder lays it out; nothing here is specific to Duracash.
"""

from __future__ import annotations

import base64

# Level M, indexed by version (index 0 unused).
ECC_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
                 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
          17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49)
FORMAT_BITS_M = 0


def _raw_modules(ver: int) -> int:
    """Modules left for data and error correction once the fixed patterns are drawn."""
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list, divisor: list) -> list:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data: bytes) -> tuple:
    """(version, the codewords with error correction, interleaved)."""
    for ver in range(1, 41):
        count_bits = 8 if ver < 10 else 16
        if 4 + count_bits + 8 * len(data) <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("Too much text for a QR code.")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(count_bits))]
    for byte in data:
        bits += [(byte >> i) & 1 for i in reversed(range(8))]
    capacity = _data_codewords(ver) * 8
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    # Split into blocks, add each block's error correction, and interleave.
    num_blocks, ecc_len = BLOCKS[ver], ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    short_blocks, short_len = num_blocks - raw % num_blocks, raw // num_blocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(num_blocks):
        dat = words[k:k + short_len - ecc_len + (0 if i < short_blocks else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, divisor)
        if i < short_blocks:
            dat.append(0)
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                out.append(block[i])
    return ver, out


def _alignment_positions(ver: int, size: int) -> list:
    if ver == 1:
        return []
    n = ver // 7 + 2
    step = 26 if ver == 32 else (ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
    return [6] + sorted(size - 7 - i * step for i in range(n - 1))


class _Symbol:
    def __init__(self, ver: int):
        self.ver, self.size = ver, ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def put(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x], self.fixed[y][x] = dark, True

    def patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if 0 <= cx + dx < size and 0 <= cy + dy < size:
                        self.put(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, size)
        last = len(pos) - 1
        for i, x in enumerate(pos):
            for j, y in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)  # reserves the area; drawn again with the chosen mask
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format_bits(self, mask: int) -> None:
        data = FORMAT_BITS_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1
        size = self.size
        for i in range(6):
            self.put(8, i, bit(i))
        self.put(8, 7, bit(6))
        self.put(8, 8, bit(7))
        self.put(7, 8, bit(8))
        for i in range(9, 15):
            self.put(14 - i, 8, bit(i))
        for i in range(8):
            self.put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.put(8, size - 15 + i, bit(i))
        self.put(8, size - 8, True)

    def place(self, words: list) -> None:
        size, i, total = self.size, 0, len(words) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    y = size - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        rule = MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and rule(x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        size, grid, score = self.size, self.dark, 0
        lines = [row for row in grid] + [[grid[y][x] for y in range(size)] for x in range(size)]
        finder = ("10111010000", "00001011101")
        for line in lines:
            run, last = 0, None
            for cell in line:
                run = run + 1 if cell == last else 1
                last = cell
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            text = "0000" + "".join("1" if c else "0" for c in line) + "0000"
            score += 40 * sum(text.count(f) for f in finder)
        for y in range(size - 1):
            for x in range(size - 1):
                if grid[y][x] == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in grid)
        total = size * size
        score += 10 * max(0, (abs(dark * 20 - total * 10) + total - 1) // total - 1)  # total is odd, so never exactly half
        return score


MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def matrix(text: str) -> list:
    """The QR code for `text` as rows of booleans (True is dark), without the quiet zone."""
    ver, words = _codewords(text.encode("utf-8"))
    sym = _Symbol(ver)
    sym.patterns()
    sym.place(words)
    best, best_score = 0, None
    for mask in range(8):
        sym.apply_mask(mask)
        sym.format_bits(mask)
        score = sym.penalty()
        if best_score is None or score < best_score:
            best, best_score = mask, score
        sym.apply_mask(mask)  # XOR again undoes it
    sym.apply_mask(best)
    sym.format_bits(best)
    return sym.dark


def svg(text: str, border: int = 4) -> str:
    """The QR code as an SVG drawing, dark squares on white, with the standard quiet zone."""
    grid = matrix(text)
    size = len(grid) + border * 2
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(grid) for x, cell in enumerate(row) if cell)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def data_uri(text: str) -> str:
    """The SVG as a data: address for an <img>, which the page's policy allows (img-src data:)."""
    return "data:image/svg+xml;base64," + base64.b64encode(svg(text).encode("utf-8")).decode("ascii")
