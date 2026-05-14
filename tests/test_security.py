"""
セキュリティ関連の HTTP ハンドラ補助処理テスト
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'source'))

import server


class TestHandlerPathNormalization(unittest.TestCase):
    def _path_for(self, raw_path):
        handler = server.Handler.__new__(server.Handler)
        handler.path = raw_path
        return server.Handler._path(handler)

    def test_decodes_percent_encoded_traversal(self):
        self.assertEqual(self._path_for('/%2e%2e%2fconfig.json'), '/../config.json')

    def test_decodes_before_query_removal(self):
        self.assertEqual(self._path_for('/asset%2ficon-192.png?cache=1'), '/asset/icon-192.png')

    def test_strips_trailing_slash_after_decode(self):
        self.assertEqual(self._path_for('/asset%2f'), '/asset')


class TestTagDeleteNoDoubleUnquote(unittest.TestCase):
    def _tag_name_from_path(self, raw_path):
        """_path() 経由で取得したパスからタグ名を抽出する（do_DELETE と同じ処理）"""
        handler = server.Handler.__new__(server.Handler)
        handler.path = raw_path
        p = server.Handler._path(handler)
        return p.rsplit('/', 1)[-1]

    def test_plain_tag(self):
        self.assertEqual(self._tag_name_from_path('/api/tags/vtuber'), 'vtuber')

    def test_percent_encoded_tag(self):
        # タグ名 "100%" → フロントエンドが encodeURIComponent → "100%25"
        self.assertEqual(self._tag_name_from_path('/api/tags/100%25'), '100%')

    def test_no_double_decode_for_literal_percent25_tag(self):
        # タグ名 "100%25" → encodeURIComponent → "100%2525"
        # _path() で一度だけデコード → "100%25" (タグ名と一致)
        self.assertEqual(self._tag_name_from_path('/api/tags/100%2525'), '100%25')


if __name__ == '__main__':
    unittest.main()
