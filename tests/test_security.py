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


if __name__ == '__main__':
    unittest.main()
