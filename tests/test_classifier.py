"""
ストリーム分類ロジックのテスト
対象: _duration_sec / _is_short / _is_member_only / ストリームtype判定
"""
import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'source'))

DEFAULT_SHORTS_KW  = ['#shorts', '#short', '#ショート', 'short', 'ショート']
DEFAULT_MEMBER_KW  = ['メン限', 'メンバー限定', 'メンバーシップ限定',
                      'member only', 'members only', 'メンバー専用']
MOCK_CFG = {
    'shorts_keywords': DEFAULT_SHORTS_KW,
    'member_keywords': DEFAULT_MEMBER_KW,
}

import server


class TestDurationSec(unittest.TestCase):
    def test_full(self):
        self.assertEqual(server._duration_sec('PT1H2M3S'), 3723)

    def test_minutes_seconds(self):
        self.assertEqual(server._duration_sec('PT3M0S'), 180)

    def test_seconds_only(self):
        self.assertEqual(server._duration_sec('PT59S'), 59)

    def test_hours_only(self):
        self.assertEqual(server._duration_sec('PT2H'), 7200)

    def test_zero(self):
        self.assertEqual(server._duration_sec('PT0S'), 0)

    def test_empty(self):
        self.assertEqual(server._duration_sec(''), 0)

    def test_none(self):
        self.assertEqual(server._duration_sec(None), 0)

    def test_live_slot(self):
        # 配信枠は duration なし（''）→ 0
        self.assertEqual(server._duration_sec(''), 0)

    def test_invalid(self):
        self.assertEqual(server._duration_sec('P0D'), 0)


class TestIsShort(unittest.TestCase):
    def _call(self, dur, title=''):
        with patch('server.cfg', return_value=MOCK_CFG):
            return server._is_short(dur, title)

    # duration による判定
    def test_under_180(self):
        self.assertTrue(self._call('PT59S'))

    def test_exactly_180(self):
        self.assertTrue(self._call('PT3M0S'))

    def test_over_180(self):
        self.assertFalse(self._call('PT3M1S'))

    def test_zero_duration(self):
        # 0秒（配信枠）はショートではない
        self.assertFalse(self._call('PT0S'))

    # タイトルキーワードによる判定（duration より優先）
    def test_keyword_shorts(self):
        self.assertTrue(self._call('PT5M', '#Shorts'))

    def test_keyword_short(self):
        self.assertTrue(self._call('PT5M', '#short動画'))

    def test_keyword_katakana(self):
        self.assertTrue(self._call('PT5M', '今日のショート'))

    def test_keyword_over_180_but_title_match(self):
        # 3分超でもタイトルにキーワードがあればショート
        self.assertTrue(self._call('PT10M', '解説 #shorts'))

    def test_no_keyword_long(self):
        self.assertFalse(self._call('PT10M', '普通の動画'))

    def test_empty_duration_no_keyword(self):
        self.assertFalse(self._call('', '普通の動画'))


class TestIsMemberOnly(unittest.TestCase):
    def _call(self, title):
        with patch('server.cfg', return_value=MOCK_CFG):
            return server._is_member_only(title)

    def test_men_gen(self):
        self.assertTrue(self._call('【メン限】雑談'))

    def test_member_limited(self):
        self.assertTrue(self._call('メンバー限定ライブ'))

    def test_member_only_en(self):
        self.assertTrue(self._call('members only stream'))

    def test_case_insensitive(self):
        self.assertTrue(self._call('Member Only Talk'))

    def test_normal_title(self):
        self.assertFalse(self._call('普通の配信'))

    def test_empty(self):
        self.assertFalse(self._call(''))


class TestStreamTypeClassification(unittest.TestCase):
    """
    fetch_youtube_streams 内の type 判定ロジックを
    同等の条件で直接検証する
    """
    def _classify(self, lbc, dur, title='', aend=None):
        with patch('server.cfg', return_value=MOCK_CFG):
            if lbc == 'live':
                vtype = 'live'
            elif lbc == 'upcoming':
                vtype = 'premiere' if server._duration_sec(dur) > 0 else 'upcoming'
            elif aend:
                vtype = 'archive'
            elif server._is_short(dur, title):
                vtype = 'short'
            else:
                vtype = 'video'
            if server._is_member_only(title):
                vtype = 'member'
            return vtype

    def test_live(self):
        self.assertEqual(self._classify('live', ''), 'live')

    def test_upcoming_scheduled(self):
        # 配信枠（duration なし）
        self.assertEqual(self._classify('upcoming', ''), 'upcoming')
        self.assertEqual(self._classify('upcoming', 'PT0S'), 'upcoming')

    def test_premiere(self):
        # プレミア公開（duration あり）
        self.assertEqual(self._classify('upcoming', 'PT10M'), 'premiere')

    def test_archive(self):
        self.assertEqual(self._classify('none', 'PT30M', aend='2024-01-01T00:00:00Z'), 'archive')

    def test_short_by_duration(self):
        self.assertEqual(self._classify('none', 'PT59S'), 'short')

    def test_short_by_keyword(self):
        self.assertEqual(self._classify('none', 'PT10M', title='テスト #shorts'), 'short')

    def test_video(self):
        self.assertEqual(self._classify('none', 'PT10M', title='普通の動画'), 'video')

    def test_member_overrides_all(self):
        # member キーワードは live も含め上書き
        self.assertEqual(self._classify('live', '', title='メン限ライブ'), 'member')
        self.assertEqual(self._classify('none', 'PT1M', title='メンバー限定ショート'), 'member')


if __name__ == '__main__':
    unittest.main()
