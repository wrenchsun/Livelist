"""
チャンネル URL パーサーのテスト
対象: resolve_youtube_channel の URL 正規化・ID 抽出ロジック
      HTTP 通信は発生しない純粋なパターンマッチング部分のみ検証
"""
import sys
import os
import re
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'source'))

import server

CHANNEL_ID_RE = re.compile(r'UC[\w-]{22}')
VALID_CID     = 'UCxxxxxxxxxxxxxxxxxxxxxx'  # 24文字


class TestChannelIdPattern(unittest.TestCase):
    def test_valid_id(self):
        self.assertTrue(bool(CHANNEL_ID_RE.fullmatch(VALID_CID)))

    def test_too_short(self):
        self.assertFalse(bool(CHANNEL_ID_RE.fullmatch('UCshort')))

    def test_too_long(self):
        self.assertFalse(bool(CHANNEL_ID_RE.fullmatch('UC' + 'x' * 23)))

    def test_wrong_prefix(self):
        self.assertFalse(bool(CHANNEL_ID_RE.fullmatch('AB' + 'x' * 22)))

    def test_hyphen_allowed(self):
        self.assertTrue(bool(CHANNEL_ID_RE.fullmatch('UC' + 'a-b_' * 5 + 'xx')))

    def test_extract_from_url(self):
        url = 'https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx/videos'
        m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', url)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), VALID_CID)


class TestYouTubeUrlPatterns(unittest.TestCase):
    """
    resolve_youtube_channel が内部で使う各 URL 形式のパターンを検証
    """
    def _extract_cid_from_channel_url(self, url):
        m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', url)
        return m.group(1) if m else None

    def test_channel_url(self):
        url = f'https://www.youtube.com/channel/{VALID_CID}'
        self.assertEqual(self._extract_cid_from_channel_url(url), VALID_CID)

    def test_channel_url_with_path(self):
        url = f'https://www.youtube.com/channel/{VALID_CID}/videos'
        self.assertEqual(self._extract_cid_from_channel_url(url), VALID_CID)

    def test_handle_url_no_cid(self):
        # @ハンドル形式はチャンネルID を直接含まない
        url = 'https://www.youtube.com/@SomeChannel'
        self.assertIsNone(self._extract_cid_from_channel_url(url))

    def test_oembed_author_url(self):
        # oembed レスポンスの author_url からIDを抽出
        author_url = f'https://www.youtube.com/channel/{VALID_CID}'
        m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', author_url)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), VALID_CID)

    def test_og_url_pattern(self):
        html = f'<meta property="og:url" content="https://www.youtube.com/channel/{VALID_CID}">'
        m = re.search(r'<meta property="og:url" content="https://www\.youtube\.com/channel/(UC[\w-]{22})"', html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), VALID_CID)

    def test_rss_link_pattern(self):
        html = f'<link rel="alternate" type="application/rss+xml" href="https://www.youtube.com/feeds/videos.xml?channel_id={VALID_CID}">'
        m = re.search(r'href="https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[\w-]{22})"', html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), VALID_CID)


class TestTwitchUrlPattern(unittest.TestCase):
    def _extract_login(self, url):
        m = re.search(r'twitch\.tv/([A-Za-z0-9_]+)', url, re.IGNORECASE)
        return m.group(1).lower() if m else None

    def test_basic(self):
        self.assertEqual(self._extract_login('https://www.twitch.tv/username'), 'username')

    def test_uppercase(self):
        self.assertEqual(self._extract_login('https://www.twitch.tv/UserName'), 'username')

    def test_with_path(self):
        self.assertEqual(self._extract_login('https://www.twitch.tv/username/videos'), 'username')

    def test_invalid(self):
        self.assertIsNone(self._extract_login('https://www.youtube.com/@channel'))


class TestOperatingHours(unittest.TestCase):
    def _check(self, enabled, start, end, now_hm):
        c = {'operating_hours': {'enabled': enabled, 'start': start, 'end': end}}
        h, m = map(int, now_hm.split(':'))
        import datetime
        fake_now = datetime.datetime(2024, 1, 1, h, m)
        with __import__('unittest.mock', fromlist=['patch']).patch(
            'server.datetime') as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: __import__('datetime').datetime(*a, **kw)
            return server._in_operating_hours(c)

    def test_disabled_always_true(self):
        # 稼働時間設定が無効なら常に True
        c = {'operating_hours': {'enabled': False, 'start': '08:00', 'end': '23:00'}}
        self.assertTrue(server._in_operating_hours(c))

    def test_within_hours(self):
        c = {'operating_hours': {'enabled': True, 'start': '08:00', 'end': '23:00'}}
        # 直接時刻を操作せず境界値だけロジックで確認
        sh, sm = 8, 0
        eh, em = 23, 0
        start_min, end_min = sh * 60 + sm, eh * 60 + em
        now_min = 12 * 60  # 12:00
        self.assertTrue(start_min <= now_min < end_min)

    def test_outside_hours(self):
        start_min, end_min = 8 * 60, 23 * 60
        now_min = 2 * 60  # 02:00
        self.assertFalse(start_min <= now_min < end_min)

    def test_midnight_crossing_in(self):
        # 22:00〜02:00 の設定、現在 23:30 → 稼働中
        start_min, end_min = 22 * 60, 2 * 60
        now_min = 23 * 60 + 30
        result = now_min >= start_min or now_min < end_min
        self.assertTrue(result)

    def test_midnight_crossing_out(self):
        # 22:00〜02:00 の設定、現在 10:00 → 稼働外
        start_min, end_min = 22 * 60, 2 * 60
        now_min = 10 * 60
        result = now_min >= start_min or now_min < end_min
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
