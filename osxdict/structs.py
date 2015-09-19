from __future__ import print_function
import ctypes

'''
DictHeader
- BlockHeader
- BlockHeader
- BlockHeader
- ...

Compressed block is comprised of:
  EntryHeader + encoded entry text
  EntryHeader + encoded entry text
  ...
'''


class DictHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [('unknown1', ctypes.c_ubyte * 0x40),  # 0x00-0x40
                ('stream_length', ctypes.c_uint),     # 0x40-0x44
                ('unknown2', ctypes.c_ubyte * 8),     # 0x44-0x4c
                ('check', ctypes.c_uint),             # 0x4c-0x50
                ('unknown3', ctypes.c_ubyte * 4),     # 0x50-0x54
                ('block_count', ctypes.c_uint),       # 0x54-0x58
                ('unknown4', ctypes.c_ubyte * 8),     # 0x58-0x60
                ]

    @property
    def is_valid(self):
        return (self.check == 0x20)


class BlockHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [('next_block', ctypes.c_uint),               # 0x60-0x64
                ('block_length', ctypes.c_uint),             # 0x64-0x68
                ('unpacked_length', ctypes.c_uint),          # 0x68-0x6c
                ]

    def get_next_block(self, file_pos):
        return file_pos + self.next_block - BlockHeader.unpacked_length.offset


class EntryHeader(ctypes.LittleEndianStructure):
    _fields_ = [('length', ctypes.c_uint),
                ]


class KeyIndexHeader(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [('unknown1', ctypes.c_ubyte * 0x42),  # 0x00-0x40
                # ('stream_length', ctypes.c_uint),     # 0x40-0x44
                ('check', ctypes.c_uint),             # 0x44-0x49
                ]

    @property
    def is_valid(self):
        return (self.check == 0x20)


def _sanity_check():
    assert DictHeader.stream_length.offset == 0x40
    assert DictHeader.check.offset == 0x4c
    assert DictHeader.block_count.offset == 0x54
    assert ctypes.sizeof(DictHeader) == 0x60

    assert BlockHeader.next_block.offset == 0x0
    assert BlockHeader.block_length.offset == 0x4
    assert BlockHeader.unpacked_length.offset == 0x8
    assert ctypes.sizeof(BlockHeader) == 0x0c


_sanity_check()
