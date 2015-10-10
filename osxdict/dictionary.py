# -*- coding: utf-8 -*-

from __future__ import print_function
import os
import io
import zlib
import logging
import threading
import functools
import collections

import six
import lxml.html

from . import structs


if not six.PY3:
    open = io.open

logger = logging.getLogger(__name__)


def locked(fcn):
    '''Threading lock decorator

    Allows multithreaded applications to access the dictionary, using mutual
    exclusion where necessary to keep the file position correct
    '''
    @functools.wraps(fcn)
    def inner(self, *args, **kwargs):
        with self._lock:
            return fcn(self, *args, **kwargs)

    return inner


BlockOffset = collections.namedtuple('BlockOffset', 'block offset')


class EntryCache(dict):
    max_entries = 1000

    # TODO


class DictionaryBody(object):
    '''Dictionary body parser'''

    def __init__(self, path, body_fn=None, encoding=None, cache=False,
                 cache_inst=None, title_tag='d:title'):
        if body_fn is None:
            body_fn = 'Body.data'
        if encoding is None:
            encoding = 'utf-8'

        if cache:
            if cache_inst is None:
                cache = EntryCache()
            self._cache = cache
        else:
            self._cache = None

        self._f = None
        self._path = path
        self._fn = body_fn
        self._encoding = encoding
        self._index = None
        self._block_pos = None
        self._block_size = None
        self._lock = threading.RLock()
        self._title_tag = title_tag

    @property
    def filename(self):
        '''The full path and filename to the text'''
        return os.path.join(self._path, self._fn)

    def close(self):
        '''Close the file'''
        if self._f is not None:
            self._f.close()
            self._f = None

    __del__ = close

    def open(self):
        '''Open the file and read the header'''
        self._f = open(self.filename, 'rb')
        self._read_body_header()

    @locked
    def _read_body_header(self):
        '''Read the dictionary body header'''
        f = self._f

        header = structs.DictHeader()
        f.seek(0)
        f.readinto(header)
        logger.debug('Stream length: {}'.format(header.stream_length))
        logger.debug('Check value: {:x}'.format(header.check))
        logger.debug('Block count: {}'.format(header.block_count))

        if not header.is_valid:
            raise ValueError('Invalid dictionary header '
                             '(header_check={})'.format(header.check))

        self._stream_length = header.stream_length
        self._block_count = header.block_count
        self._block_pos = {0: f.tell()}

    def _decompress(self, buf):
        '''Decompress a (zlib) byte stream'''
        if six.PY3:
            return zlib.decompress(buf)
        else:
            # Copies, everywhere! :( 2.x expects a string.
            # memoryview doesn't work, either
            return zlib.decompress(bytes(buf))

    @locked
    def _read_raw_entry_block(self, header):
        '''Read and uncompress a block of raw entries'''
        f = self._f
        buf = bytearray(header.block_length)
        f.readinto(buf)
        buf = self._decompress(buf)
        if len(buf) != header.unpacked_length:
            raise RuntimeError('Unpacked length: {:x} '
                               'Expected: {:x}'.format(len(buf),
                                                       header.unpacked_length))

        return buf

    def _entry_from_block(self, f, entry_header=None):
        '''Get entry text from a raw entry

        Uses the entry header to get the right raw string size,
        decodes it based on the dictionary's encoding
        '''

        if entry_header is None:
            entry_header = structs.EntryHeader()

        f.readinto(entry_header)

        raw_entry = bytearray(entry_header.length)
        f.readinto(raw_entry)
        entry_text = raw_entry.decode(self._encoding)
        return entry_text

    def _read_entry(self, block_offset=None):
        '''Read an entry from disk given a block and offset'''
        header = self._read_block_header(block_offset.block)[0]

        raw_entries = self._read_raw_entry_block(header)
        with io.BytesIO(raw_entries[block_offset.offset:]) as f:
            return self._entry_from_block(f)

    def _read_entries(self, block, header):
        '''Read all entries from the current block

        Yields: (offset, entry) for all entries in the block
        '''
        buf = self._read_raw_entry_block(header)

        entry_header = structs.EntryHeader()
        with io.BytesIO(buf) as entry_buf:
            while entry_buf.tell() < len(buf):
                pos = entry_buf.tell()
                entry_text = self._entry_from_block(entry_buf, entry_header)
                yield pos, entry_text

    @locked
    def _read_block_header(self, index=None):
        '''Read the header for the block

        If index is specified, the block will first be found
        If not, it is assumed the current file position is the start of the
        block.
        '''
        f = self._f

        if index is not None:
            self.seek_block(index)
            # logger.debug('Block %d pos=%x', index, f.tell())

        header = structs.BlockHeader()

        f.readinto(header)
        next_block = header.get_next_block(f.tell())

        logger.debug('Raw next block pointer: %x (next=%x) '
                     'Block length on disk: %x (unpacked=%x)',
                     header.next_block, next_block, header.block_length,
                     header.unpacked_length)
        if header.block_length > 1e8:
            raise RuntimeError('Unexpected block length '
                               '{}'.format(header.block_length))

        return header, next_block

    @locked
    def read_all_entries(self):
        '''Get all entries in all blocks

        Yields: (block, offset, entry_text)
        '''
        if self._f is None:
            self.open()

        for i in range(self._block_count):
            header, next_block = self._read_block_header(i)

            entries = self._read_entries(i, header)
            for offset, entry_text in entries:
                yield i, offset, entry_text

    @locked
    def seek_block(self, block_index):
        '''Go to a specific block in the file'''
        self._find_blocks()

        try:
            self._f.seek(self._block_pos[block_index])
        except IndexError:
            raise ValueError('Invalid block number')

    @locked
    def _find_blocks(self):
        '''Makes note of all block locations in the body file'''
        if self._f is None:
            self.open()

        if len(self._block_pos) > 1:
            return

        f = self._f
        f.seek(self._block_pos[0])

        for i in range(self._block_count):
            self._block_pos[i] = f.tell()
            logger.debug('Block %s at %x', i, f.tell())
            header, next_block = self._read_block_header()
            f.seek(next_block)

    def __iter__(self):
        '''Iterate over all entries in the dictionary'''
        for block, offset, entry_text in self.read_all_entries():
            yield self.interpret_text(entry_text)

    @locked
    def build_index(self):
        '''Build an index of {word: block_offset}'''
        if self._index is not None:
            return

        self._index = collections.OrderedDict()
        title_tag = '{}="'.format(self._title_tag)
        for block, offset, block_text in self.read_all_entries():
            try:
                tag_pos = block_text.index(title_tag) + len(title_tag)
            except IndexError:
                logger.debug('Title not found?')
                continue

            # 20x faster than parsing all the html just to get the title
            # html = lxml.html.fromstring(block_text)
            # title = html.get('d:title', None)
            title = block_text[tag_pos:]
            title = title[:title.index('"')]
            if title:
                b_o = BlockOffset(block, offset)
                try:
                    index = self._index[title]
                except KeyError:
                    index = self._index[title] = []

                index.append(b_o)

        logger.debug('Total index entries %d', len(self._index))

    def __getitem__(self, word):
        '''Read an entry from the dictionary by word'''
        if self._cache is not None:
            try:
                return self._cache[word]
            except KeyError:
                pass

        try:
            block_offs = self._index[word]
        except KeyError:
            raise KeyError('Word not found in index')

        matches = [self.interpret_text(self._read_entry(block_off))
                   for block_off in block_offs]
        if self._cache is not None:
            self._cache[word] = matches

        return matches

    def interpret_text(self, entry_text):
        '''Override this to convert to a more convenient format'''
        return entry_text


class DictionaryBodyHTML(DictionaryBody):
    '''Read dictionary bodies with the lxml library'''

    def interpret_text(self, entry_text):
        html = lxml.html.fromstring(entry_text)
        # return html.get('d:title', None), html
        return html


class Dictionary(object):
    '''Dictionary Reader

    Parameters
    ----------
    path : str
        Path to the dictionary's contents, something like:
        /Library/Dictionaries/{}.dictionary/Contents/
    body_fn : str, optional
        Dictionary body filename (default: Body.data)
    encoding : str, optional
        Encoding of the dictionary (default: utf-8)
    body_class : class, optional
        A class which handles converting the raw dictionary text to a usable
        format. (default: DictionaryBodyHTML)

    '''

    def __init__(self, path, body_fn=None, encoding='utf-8',
                 body_class=None, index_fn=None):

        if body_class is None:
            body_class = DictionaryBodyHTML

        self.body = body_class(path, body_fn=body_fn, encoding=encoding)
        # self.index = DictionaryIndex(path, index_fn=index_fn,
        #                              encoding=encoding)
        # TODO this was split up as I thought I could figure out the other
        #      other file format

    def entries(self):
        '''All body entries in the dictionary'''
        for entry in self.body:
            yield entry

    __iter__ = entries

    def __getitem__(self, word):
        '''Read an entry from the dictionary by word'''
        return self.body[word]


def _test():
    logging.basicConfig()
    # logger.setLevel(logging.DEBUG)
    # dict_name = 'Sanseido Super Daijirin'
    dict_name = 'Oxford Dictionary of English'

    path = "/Library/Dictionaries/{}.dictionary/Contents/".format(dict_name)

    # dc = Dictionary(path, body_class=DictionaryBodyHTML)
    dc = Dictionary(path, body_class=DictionaryBody)
    import time
    t0 = time.time()
    dc.body.build_index()
    t1 = time.time() - t0

    print('index built in', t1, 'sec')
    for i, entry in enumerate(dc):
        print(u'Entry {}: {}'.format(i, entry))
        break

    print('getitem "a":', dc[u'a'])
    try:
        print('getitem "あ":', dc[u'あ'])
    except KeyError:
        pass

    return dc


if __name__ == '__main__':
    dc = _test()
