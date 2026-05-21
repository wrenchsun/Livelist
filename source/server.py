#!/usr/bin/env python3
"""
Livelist サーバー
- YouTube RSS + Data API v3 でチャンネルを定期巡回
- 現在時刻 ±2日 のデータをキャッシュ
- ローカルネットワーク対応 ThreadingHTTPServer
"""
import json, os, re, socket, sys, threading, time, uuid
import urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from typing import Literal, NotRequired, TypedDict

try:
    from _build_info import __version__, __branch__, __commit__
except ImportError:
    __version__ = '1.0.0'
    __branch__  = 'dev'
    __commit__  = 'local'

# パス解決:
#   exe 版  → STATIC=_MEIPASS(index.html同梱), BASE=exe隣(jsonデータ)
#   source/ → STATIC=BASE=ルート(index.htmlとjsonが同居)
#   ルート直置き → STATIC=BASE=スクリプトと同じ場所
if getattr(sys, 'frozen', False):
    STATIC = sys._MEIPASS
    BASE   = os.path.dirname(sys.executable)
else:
    _here = os.path.dirname(os.path.abspath(__file__))
    BASE   = os.path.dirname(_here) if os.path.basename(_here) == 'source' else _here
    STATIC = BASE

ASSET = os.path.join(BASE, 'asset')
os.makedirs(ASSET, exist_ok=True)

F_CONFIG   = os.path.join(BASE,  'config.json')     # ルート（ユーザーが直接編集）
F_CHANNELS = os.path.join(ASSET, 'channels.json')
F_CACHE    = os.path.join(ASSET, 'cache.json')
F_TAGS     = os.path.join(ASSET, 'tags.json')
F_STATS    = os.path.join(ASSET, 'api_stats.json')
F_SEARCH   = os.path.join(ASSET, 'search_counter.json')
PORT = 8080

DEFAULT_MEMBER_KEYWORDS = ['メン限', 'メンバー限定', 'メンバーシップ限定', 'member only', 'members only', 'メンバー専用']
DEFAULT_SHORTS_KEYWORDS = ['#shorts', '#short', '#ショート', 'short', 'ショート']

DEFAULT_CONFIG = {
    "youtube_api_key": "",
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "port": 8080,
    "refresh_interval": 300,
    "days_past":   2,
    "days_future": 2,
    "member_keywords": DEFAULT_MEMBER_KEYWORDS,
    "shorts_keywords": DEFAULT_SHORTS_KEYWORDS,
    "operating_hours": {"enabled": False, "start": "08:00", "end": "23:00"},
    "search_max_daily": 10,
}

STAT_KINDS = ('rss', 'oembed', 'videos_list', 'resolve',
              'twitch_resolve', 'twitch_streams', 'twitch_schedule', 'twitch_videos',
              'ch_search')

# ── 型定義 ────────────────────────────────────────────────────────────────────

StreamType = Literal['live', 'upcoming', 'premiere', 'archive', 'short', 'member', 'video']
Platform   = Literal['youtube', 'twitch']

class OperatingHoursDict(TypedDict):
    enabled: bool
    start:   str   # "HH:MM"
    end:     str   # "HH:MM"

class StreamDict(TypedDict):
    id:               str
    channelId:        str
    title:            str
    url:              str
    platform:         Platform
    type:             StreamType
    publishedAt:      NotRequired[str | None]
    scheduledAt:      NotRequired[str | None]
    actualStart:      NotRequired[str | None]
    actualEnd:        NotRequired[str | None]
    duration:         NotRequired[str | None]
    thumbnail:        NotRequired[str | None]
    channelName:      NotRequired[str]
    channelThumbnail: NotRequired[str | None]

class ChannelDict(TypedDict):
    id:           str
    name:         str
    url:          str
    platform:     Platform
    thumbnail:    NotRequired[str | None]
    tags:         NotRequired[list[str]]
    registeredAt: NotRequired[str]
    twitchLogin:  NotRequired[str]

# ── JSON helpers ──────────────────────────────────────────────────────────────

def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

_config_lock   = threading.Lock()
_channels_lock = threading.Lock()
_tags_lock     = threading.Lock()

def save_json(path, data):
    tmp = f'{path}.{uuid.uuid4().hex}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def cfg():
    return {**DEFAULT_CONFIG, **load_json(F_CONFIG, {})}

# ── Datetime helpers ──────────────────────────────────────────────────────────

def parse_dt(s):
    if not s:
        return None
    s = re.sub(r'\.\d+', '', s).replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)
        except Exception:
            return None

def now_utc():
    return datetime.now(timezone.utc)

# ── Network ───────────────────────────────────────────────────────────────────

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return 'localhost'

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124'

def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept-Language': 'ja,en;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace')

def fetch_with_final_url(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept-Language': 'ja,en;q=0.9'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace'), r.url

# ── API stats ─────────────────────────────────────────────────────────────────

_stats_lock = threading.Lock()

def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def _load_stats():
    s = load_json(F_STATS, {})
    today = _today()
    if s.get('date') != today:
        history = s.get('history', [])
        if s.get('date') and s.get('daily'):
            history.append({'date': s['date'], **s['daily']})
        history = history[-30:]
        s = {
            'date': today,
            'daily': {k: {'calls':0,'ok':0,'fail':0,'quota':0} for k in STAT_KINDS},
            'total': s.get('total', {k: {'calls':0,'ok':0,'fail':0,'quota':0} for k in STAT_KINDS}),
            'history': history,
        }
    return s

def record_api(kind, success, quota=0):
    with _stats_lock:
        s = _load_stats()
        for scope in ('daily', 'total'):
            d = s[scope].setdefault(kind, {'calls':0,'ok':0,'fail':0,'quota':0})
            d['calls'] += 1
            d['ok' if success else 'fail'] += 1
            d['quota'] += quota
        save_json(F_STATS, s)

def yt_api(endpoint, params, key):
    params = {**params, 'key': key}
    url = 'https://www.googleapis.com/youtube/v3/' + endpoint + '?' + urllib.parse.urlencode(params)
    kind = 'videos_list'
    quota = 1
    try:
        result = json.loads(fetch(url))
        record_api(kind, True, quota)
        return result
    except Exception as e:
        record_api(kind, False, quota)
        raise

# ── Channel search ────────────────────────────────────────────────────────────

_search_lock = threading.Lock()

def _search_remaining():
    max_daily = cfg().get('search_max_daily', 90)
    with _search_lock:
        d = load_json(F_SEARCH, {})
        today = datetime.now().strftime('%Y-%m-%d')
        count = d.get('count', 0) if d.get('date') == today else 0
        return max(0, max_daily - count)

def _consume_search():
    with _search_lock:
        d = load_json(F_SEARCH, {})
        today = datetime.now().strftime('%Y-%m-%d')
        count = d.get('count', 0) if d.get('date') == today else 0
        save_json(F_SEARCH, {'date': today, 'count': count + 1})

def yt_search_channels(q, api_key, max_results=8):
    params = {'part': 'snippet', 'q': q, 'type': 'channel', 'maxResults': max_results, 'key': api_key}
    url = 'https://www.googleapis.com/youtube/v3/search?' + urllib.parse.urlencode(params)
    try:
        result = json.loads(fetch(url))
        record_api('ch_search', True, 100)
    except Exception:
        record_api('ch_search', False, 100)
        raise
    out = []
    for item in result.get('items', []):
        sn  = item.get('snippet', {})
        cid = sn.get('channelId') or item.get('id', {}).get('channelId', '')
        if not cid:
            continue
        thumb = (sn.get('thumbnails') or {})
        thumb_url = (thumb.get('medium') or thumb.get('default') or {}).get('url')
        out.append({
            'id':        cid,
            'name':      sn.get('channelTitle', ''),
            'thumbnail': thumb_url,
            'url':       f'https://www.youtube.com/channel/{cid}',
            'platform':  'youtube',
        })
    return out

def twitch_search_channels(q, client_id, client_secret, max_results=8):
    token = get_twitch_token(client_id, client_secret)
    data  = twitch_api('search/channels', {'query': q, 'first': max_results}, client_id, token)
    out   = []
    for ch in data.get('data', []):
        out.append({
            'id':        ch['id'],
            'name':      ch.get('display_name', ch.get('broadcaster_login', '')),
            'thumbnail': ch.get('thumbnail_url') or None,
            'url':       f"https://www.twitch.tv/{ch.get('broadcaster_login', '')}",
            'platform':  'twitch',
            'isLive':    ch.get('is_live', False),
        })
    return out

# ── Twitch API ────────────────────────────────────────────────────────────────

_twitch_token = {'token': None, 'expires_at': 0}
_twitch_lock  = threading.Lock()

def get_twitch_token(client_id, client_secret):
    with _twitch_lock:
        if _twitch_token['token'] and _twitch_token['expires_at'] > time.time() + 60:
            return _twitch_token['token']
        data = urllib.parse.urlencode({
            'client_id': client_id,
            'client_secret': client_secret,
            'grant_type': 'client_credentials',
        }).encode()
        req = urllib.request.Request('https://id.twitch.tv/oauth2/token', data=data, method='POST')
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
        _twitch_token['token'] = resp['access_token']
        _twitch_token['expires_at'] = time.time() + resp.get('expires_in', 3600)
        return _twitch_token['token']

def twitch_api(endpoint, params, client_id, token):
    url = 'https://api.twitch.tv/helix/' + endpoint + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'Client-ID': client_id,
        'Authorization': f'Bearer {token}',
    })
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())

# ── Channel resolution ────────────────────────────────────────────────────────

_CH_ID_RE = re.compile(r'UC[\w-]{22}')

def resolve_channel(url):
    url = url.strip().rstrip('/')
    if 'twitch.tv/' in url.lower():
        return resolve_twitch_channel(url)
    return resolve_youtube_channel(url)

def resolve_twitch_channel(url):
    m = re.search(r'twitch\.tv/([A-Za-z0-9_]+)', url, re.IGNORECASE)
    if not m:
        raise Exception('Twitch URLを確認してください（例: https://www.twitch.tv/username）')
    login = m.group(1).lower()
    c = cfg()
    client_id     = c.get('twitch_client_id', '')
    client_secret = c.get('twitch_client_secret', '')
    if not client_id or not client_secret:
        raise Exception('Twitch Client ID と Client Secret を設定してください（⚙設定）')
    try:
        token = get_twitch_token(client_id, client_secret)
        data  = twitch_api('users', {'login': login}, client_id, token)
        users = data.get('data', [])
        if not users:
            raise Exception(f'Twitchユーザー「{login}」が見つかりません')
        user = users[0]
        record_api('twitch_resolve', True)
        return {
            'id':          user['id'],
            'name':        user['display_name'],
            'thumbnail':   user.get('profile_image_url'),
            'platform':    'twitch',
            'twitchLogin': user['login'],
        }
    except Exception:
        record_api('twitch_resolve', False)
        raise

def resolve_youtube_channel(url):

    if re.fullmatch(r'UC[\w-]{22}', url):
        return _enrich_from_rss(url, f'https://www.youtube.com/channel/{url}')

    m = re.search(r'youtube\.com/channel/(UC[\w-]{22})', url)
    if m:
        cid = m.group(1)
        return _enrich_from_rss(cid, url)

    cid = None
    name = None
    thumb = None

    try:
        oembed_url = f'https://www.youtube.com/oembed?url={urllib.parse.quote(url, safe="")}&format=json'
        oembed_data = json.loads(fetch(oembed_url))
        author_url = oembed_data.get('author_url', '')
        m2 = re.search(r'youtube\.com/channel/(UC[\w-]{22})', author_url)
        if m2:
            cid = m2.group(1)
            name = oembed_data.get('author_name', 'Unknown')
            record_api('oembed', True)
        else:
            record_api('oembed', False)
    except Exception:
        record_api('oembed', False)

    if not cid:
        html, _ = fetch_with_final_url(url)
        for pat in [
            r'<link rel="alternate"[^>]+href="https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[\w-]{22})"',
            r'<meta property="og:url" content="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
            r'<link rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
            r'<meta itemprop="channelId" content="(UC[\w-]{22})"',
        ]:
            m = re.search(pat, html)
            if m:
                cid = m.group(1)
                break

        if cid and not name:
            m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            if m:
                name = re.sub(r'\s*[-–]\s*YouTube\s*$', '', m.group(1)).strip()
            m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
            if m:
                thumb = m.group(1)

    if not cid:
        record_api('resolve', False)
        raise Exception('チャンネルIDを取得できませんでした。URLを確認してください。')

    if not name:
        name = 'Unknown'

    record_api('resolve', True)
    return {'id': cid, 'name': name, 'thumbnail': thumb}

def _enrich_from_rss(cid, url):
    try:
        rss = fetch(f'https://www.youtube.com/feeds/videos.xml?channel_id={cid}')
        root = ET.fromstring(rss)
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        name = root.findtext('a:title', namespaces=ns) or 'Unknown'
    except Exception:
        name = 'Unknown'
    thumb = None
    try:
        html, _ = fetch_with_final_url(url)
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            thumb = m.group(1)
    except Exception:
        pass
    return {'id': cid, 'name': name, 'thumbnail': thumb}

# ── Stream fetching ───────────────────────────────────────────────────────────

def fetch_channel_streams(channel):
    if channel.get('platform') == 'twitch':
        return fetch_twitch_streams(channel)
    return fetch_youtube_streams(channel)

def fetch_twitch_streams(channel: ChannelDict) -> list[StreamDict]:
    c             = cfg()
    client_id     = c.get('twitch_client_id', '')
    client_secret = c.get('twitch_client_secret', '')
    user_id       = channel['id']
    login         = channel.get('twitchLogin', user_id)
    cutoff_past   = now_utc() - timedelta(days=c.get('days_past',   2))
    cutoff_future = now_utc() + timedelta(days=c.get('days_future', 2))

    if not client_id or not client_secret:
        return []

    try:
        token = get_twitch_token(client_id, client_secret)
    except Exception as e:
        print(f'  Twitch token error: {e}')
        return []

    def fix_thumb(url):
        return url.replace('%{width}', '640').replace('%{height}', '360') \
                  .replace('{width}', '640').replace('{height}', '360')

    streams = []

    try:
        data = twitch_api('streams', {'user_id': user_id}, client_id, token)
        record_api('twitch_streams', True)
        for s in data.get('data', []):
            thumb = fix_thumb(s.get('thumbnail_url', ''))
            started = s.get('started_at')
            stitle = s.get('title', '')
            stype  = 'member' if _is_member_only(stitle) else 'live'
            streams.append({
                'id':          f'tw_stream_{s["id"]}',
                'channelId':   user_id,
                'title':       stitle,
                'url':         f'https://www.twitch.tv/{login}',
                'thumbnail':   thumb or None,
                'type':        stype,
                'platform':    'twitch',
                'publishedAt': started,
                'scheduledAt': None,
                'actualStart': started,
                'duration':    None,
            })
    except Exception as e:
        record_api('twitch_streams', False)
        print(f'  Twitch streams error ({login}): {e}')

    try:
        data = twitch_api('schedule', {'broadcaster_id': user_id, 'first': 25}, client_id, token)
        record_api('twitch_schedule', True)
        for seg in data.get('data', {}).get('segments', []):
            start_dt = parse_dt(seg.get('start_time'))
            if not start_dt:
                continue
            if start_dt < cutoff_past or start_dt > cutoff_future:
                continue
            streams.append({
                'id':          f'tw_sched_{seg["id"]}',
                'channelId':   user_id,
                'title':       seg.get('title', ''),
                'url':         f'https://www.twitch.tv/{login}',
                'thumbnail':   None,
                'type':        'upcoming',
                'platform':    'twitch',
                'publishedAt': None,
                'scheduledAt': seg.get('start_time'),
                'duration':    None,
            })
    except Exception as e:
        if '404' not in str(e):
            record_api('twitch_schedule', False)
            print(f'  Twitch schedule error ({login}): {e}')
        else:
            record_api('twitch_schedule', True)

    try:
        data = twitch_api('videos', {'user_id': user_id, 'type': 'archive', 'first': 20}, client_id, token)
        record_api('twitch_videos', True)
        for v in data.get('data', []):
            pub = v.get('published_at') or v.get('created_at')
            pub_dt = parse_dt(pub)
            if pub_dt and (pub_dt < cutoff_past or pub_dt > cutoff_future):
                continue
            thumb  = fix_thumb(v.get('thumbnail_url', ''))
            vtitle = v.get('title', '')
            vtype  = 'member' if _is_member_only(vtitle) else 'archive'
            streams.append({
                'id':          f'tw_vod_{v["id"]}',
                'channelId':   user_id,
                'title':       vtitle,
                'url':         v.get('url', f'https://www.twitch.tv/videos/{v["id"]}'),
                'thumbnail':   thumb or None,
                'type':        vtype,
                'platform':    'twitch',
                'publishedAt': pub,
                'scheduledAt': None,
                'duration':    _twitch_duration(v.get('duration', '')),
            })
    except Exception as e:
        record_api('twitch_videos', False)
        print(f'  Twitch videos error ({login}): {e}')

    return streams

def _twitch_duration(dur_str):
    if not dur_str:
        return None
    h = re.search(r'(\d+)h', dur_str)
    m = re.search(r'(\d+)m', dur_str)
    s = re.search(r'(\d+)s', dur_str)
    parts = 'PT'
    if h: parts += f'{h.group(1)}H'
    if m: parts += f'{m.group(1)}M'
    if s: parts += f'{s.group(1)}S'
    return parts if parts != 'PT' else None

def fetch_youtube_streams(channel: ChannelDict) -> list[StreamDict]:
    c = cfg()
    api_key  = c.get('youtube_api_key', '')
    cid      = channel['id']
    cutoff_past   = now_utc() - timedelta(days=c.get('days_past',   2))
    cutoff_future = now_utc() + timedelta(days=c.get('days_future', 2))

    videos = {}
    try:
        rss = fetch(f'https://www.youtube.com/feeds/videos.xml?channel_id={cid}')
        record_api('rss', True)
        root = ET.fromstring(rss)
        ns = {
            'a':  'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
            'me': 'http://search.yahoo.com/mrss/',
        }
        for entry in root.findall('a:entry', ns):
            vid = entry.findtext('yt:videoId', namespaces=ns)
            if not vid:
                continue
            title = entry.findtext('a:title', namespaces=ns) or ''
            pub   = entry.findtext('a:published', namespaces=ns)
            link  = entry.find('a:link', ns)
            href  = link.get('href') if link is not None else f'https://www.youtube.com/watch?v={vid}'
            thumb = None
            te = entry.find('.//me:thumbnail', ns)
            if te is not None:
                thumb = te.get('url')
            if not thumb:
                thumb = f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg'

            pub_dt = parse_dt(pub)
            videos[vid] = {
                'id':          vid,
                'channelId':   cid,
                'title':       title,
                'url':         href,
                'thumbnail':   thumb,
                'type':        'member' if _is_member_only(title) else 'video',
                'publishedAt': pub_dt.isoformat() if pub_dt else None,
                'scheduledAt': None,
                'duration':    None,
            }
    except Exception as e:
        record_api('rss', False)
        print(f'    RSS error ({cid}): {e}')
        raise

    if api_key and videos:
        ids = list(videos.keys())
        for chunk_start in range(0, len(ids), 50):
            chunk = ids[chunk_start:chunk_start+50]
            try:
                data = yt_api('videos', {
                    'part': 'snippet,liveStreamingDetails,contentDetails',
                    'id':   ','.join(chunk),
                }, api_key)
                for item in data.get('items', []):
                    vid  = item['id']
                    snip = item.get('snippet', {})
                    live = item.get('liveStreamingDetails', {})
                    cont = item.get('contentDetails', {})
                    lbc  = snip.get('liveBroadcastContent', 'none')
                    dur  = cont.get('duration', '')
                    sched = live.get('scheduledStartTime')
                    astart= live.get('actualStartTime')
                    aend  = live.get('actualEndTime')

                    title_str = snip.get('title', '')
                    if lbc == 'live':
                        vtype = 'live'
                    elif lbc == 'upcoming':
                        vtype = 'premiere' if _duration_sec(dur) > 0 else 'upcoming'
                    elif aend:
                        vtype = 'archive'
                    elif _is_short(dur, title_str):
                        vtype = 'short'
                    else:
                        vtype = 'video'
                    if _is_member_only(title_str):
                        vtype = 'member'

                    thumbs = snip.get('thumbnails', {})
                    best = (thumbs.get('maxres') or thumbs.get('high')
                            or thumbs.get('medium') or {}).get('url')

                    videos[vid].update({
                        'type':        vtype,
                        'scheduledAt': sched,
                        'actualStart': astart,
                        'actualEnd':   aend,
                        'duration':    dur,
                        'thumbnail':   best or videos[vid].get('thumbnail'),
                    })
            except Exception as e:
                print(f'    videos.list error: {e}')
                # API失敗時: upcoming/premiere 以外はキャッシュの分類を復元
                try:
                    cached_map = {s['id']: s for s in load_json(F_CACHE, {'streams': []}).get('streams', [])}
                    for vid in chunk:
                        if vid in videos and vid in cached_map:
                            ct = cached_map[vid].get('type', 'video')
                            if ct not in ('upcoming', 'premiere'):
                                videos[vid]['type'] = ct
                except Exception:
                    pass

    result = []
    for s in videos.values():
        if s.get('type') == 'live':
            result.append(s)
            continue
        ref = parse_dt(s.get('scheduledAt') or s.get('publishedAt'))
        if ref:
            if ref < cutoff_past or ref > cutoff_future:
                continue
        result.append(s)

    return result

def _duration_sec(dur: str | None) -> int:
    if not dur:
        return 0
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', dur)
    if not m:
        return 0
    return int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)

def _is_short(dur: str, title: str = '') -> bool:
    if title:
        keywords = cfg().get('shorts_keywords', DEFAULT_SHORTS_KEYWORDS)
        t = title.lower()
        if any(k.lower() in t for k in keywords if k):
            return True
    total = _duration_sec(dur)
    return 0 < total <= 180

def _recheck_pinned(streams: list[StreamDict], api_key: str) -> list[StreamDict]:
    """
    キャッシュに残っている upcoming/live を videos.list で再確認し
    ステータスを更新する。配信終了・削除済み・期限切れは除外して返す。
    APIキーなしの場合は時刻だけで期限管理。
    """
    if not streams:
        return []

    now = now_utc()

    if not api_key:
        result = []
        for s in streams:
            sched = parse_dt(s.get('scheduledAt') or s.get('publishedAt'))
            if sched and sched + timedelta(hours=4) < now:
                continue
            result.append(s)
        return result

    id_map = {s['id']: dict(s) for s in streams}
    ids    = list(id_map.keys())
    updated = {}

    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = yt_api('videos', {
                'part': 'snippet,liveStreamingDetails',
                'id':   ','.join(chunk),
            }, api_key)
            returned_ids = set()
            for item in data.get('items', []):
                vid  = item['id']
                returned_ids.add(vid)
                snip = item.get('snippet', {})
                live = item.get('liveStreamingDetails', {})
                lbc  = snip.get('liveBroadcastContent', 'none')

                if live.get('actualEndTime'):
                    continue  # 配信終了 → まもなくRSSのアーカイブに出る

                s = id_map[vid]
                if lbc == 'live':
                    s['type']        = 'live'
                    s['actualStart'] = live.get('actualStartTime')
                elif lbc == 'upcoming':
                    if s.get('type') not in ('premiere', 'upcoming'):
                        s['type'] = 'upcoming'
                    s['scheduledAt'] = live.get('scheduledStartTime')
                else:
                    # 配信中でも予定でもない → scheduledAt から4時間で期限切れ
                    sched = parse_dt(s.get('scheduledAt') or s.get('publishedAt'))
                    if not sched or sched + timedelta(hours=4) < now:
                        continue

                updated[vid] = s

            # APIに存在しないID（削除・非公開）は除外
            for vid in chunk:
                if vid not in returned_ids and vid in id_map:
                    pass  # 自然に updated に入らないので除外される

        except Exception as e:
            print(f'  pinned recheck error: {e}')
            # エラー時は既存データを時刻ベースで保持
            for vid in chunk:
                s = id_map[vid]
                sched = parse_dt(s.get('scheduledAt') or s.get('publishedAt'))
                if not sched or sched + timedelta(hours=4) >= now:
                    updated[vid] = s

    return list(updated.values())

def _is_member_only(title: str) -> bool:
    keywords = cfg().get('member_keywords', DEFAULT_MEMBER_KEYWORDS)
    t = title.lower()
    return any(k.lower() in t for k in keywords if k)

# ── Operating hours ───────────────────────────────────────────────────────────

def _in_operating_hours(c: dict | None = None) -> bool:
    if c is None:
        c = cfg()
    oh = c.get('operating_hours', {})
    if not oh.get('enabled', False):
        return True
    try:
        sh, sm = map(int, oh.get('start', '00:00').split(':'))
        eh, em = map(int, oh.get('end',   '23:59').split(':'))
    except Exception:
        return True
    now_local = datetime.now()
    now_min   = now_local.hour * 60 + now_local.minute
    start_min = sh * 60 + sm
    end_min   = eh * 60 + em
    if start_min <= end_min:
        return start_min <= now_min < end_min
    else:  # midnight crossing
        return now_min >= start_min or now_min < end_min

# ── Background refresh ────────────────────────────────────────────────────────

_lock         = threading.Lock()
_refresh_lock = threading.Lock()

def _safe_refresh():
    """フルリフレッシュ（重複実行スキップ）"""
    if not _refresh_lock.acquire(blocking=False):
        print(f'[{_ts()}] 更新スキップ（実行中）')
        return
    try:
        refresh_all()
    except Exception as e:
        print(f'Refresh error: {e}')
    finally:
        _refresh_lock.release()

def refresh_all(single_channel: ChannelDict | None = None) -> None:
    channels = load_json(F_CHANNELS, [])
    if single_channel:
        targets = [single_channel]
    else:
        targets = channels
    if not targets:
        return

    label = single_channel['name'] if single_channel else f'{len(targets)} チャンネル'
    print(f'[{_ts()}] 更新開始: {label}')

    all_streams = []
    failed_cids = set()
    for ch in targets:
        try:
            streams = fetch_channel_streams(ch)
            for s in streams:
                s['channelName']      = ch.get('name', '')
                s['channelThumbnail'] = ch.get('thumbnail')
            all_streams.extend(streams)
            print(f'  {ch["name"]}: {len(streams)} 件')
        except Exception as e:
            failed_cids.add(ch['id'])
            print(f'  {ch.get("name","?")} エラー: {e}')

    with _lock:
        cache       = load_json(F_CACHE, {'streams': []})
        api_key     = cfg().get('youtube_api_key', '')
        target_cids = {ch['id'] for ch in targets}

        # 旧キャッシュの upcoming/live（YouTube のみ）をピン止め候補として抽出し再確認
        pinned_candidates = [
            s for s in cache.get('streams', [])
            if s.get('type') in ('upcoming', 'live')
            and s.get('platform', 'youtube') == 'youtube'
            and s.get('channelId') in target_cids
        ]
        pinned = _recheck_pinned(pinned_candidates, api_key)

        # 今回の取得結果にないピン止めストリームを補完
        new_ids = {s['id'] for s in all_streams}
        extra   = [s for s in pinned if s['id'] not in new_ids]
        if extra:
            print(f'  ピン止め補完: {len(extra)} 件')

        # RSS失敗チャンネルの既存キャッシュを保持（archive/video 消失を防ぐ）
        fallback = [s for s in cache.get('streams', []) if s.get('channelId') in failed_cids]
        if fallback:
            print(f'  フォールバック（RSS失敗）: {len(fallback)} 件保持')

        if single_channel:
            kept   = [s for s in cache.get('streams', []) if s.get('channelId') not in target_cids]
            merged = kept + all_streams + extra + fallback
        else:
            merged = all_streams + extra + fallback

        seen, unique = set(), []
        for s in merged:
            if s['id'] not in seen:
                seen.add(s['id'])
                unique.append(s)

        save_json(F_CACHE, {
            'streams':   unique,
            'updatedAt': now_utc().isoformat(),
        })

    print(f'[{_ts()}] 更新完了: {len(unique)} 件（うちピン止め補完 {len(extra)} 件）')

def _ts():
    return datetime.now().strftime('%H:%M:%S')

def _refresh_loop():
    _safe_refresh()
    while True:
        interval = cfg().get('refresh_interval', 300)
        time.sleep(interval)
        if not _in_operating_hours():
            print(f'[{_ts()}] 稼働時間外: 自動更新をスキップ')
            continue
        _safe_refresh()

# ── HTTP Handler ──────────────────────────────────────────────────────────────

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.ico':  'image/x-icon',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.svg':  'image/svg+xml',
    '.webp': 'image/webp',
    '.gif':  'image/gif',
}

# 静的配信を許可する拡張子（.json/.py/.bat 等は除外）
_STATIC_ALLOWED_EXT = frozenset(MIME.keys())

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get('Origin', '')
        if origin == 'null':
            # file:// アクセス
            allow = 'null'
        elif origin:
            try:
                parsed = urllib.parse.urlparse(origin)
                if parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1'):
                    allow = origin
                else:
                    allow = 'null'
            except Exception:
                allow = 'null'
        else:
            allow = ''
        if allow:
            self.send_header('Access-Control-Allow-Origin', allow)
        self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(min(n, 1_048_576)))
        except (json.JSONDecodeError, ValueError):
            self._json(400, {'error': 'invalid json'})
            return None

    def _path(self):
        return urllib.parse.unquote(self.path.split('?')[0]).rstrip('/')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = self._path()

        if p == '/api/ping':
            self._json(200, {'ok': True})

        elif p == '/api/status':
            c = cfg()
            cache = load_json(F_CACHE, {})
            oh = c.get('operating_hours', {})
            self._json(200, {
                'ok':             True,
                'hasApiKey':      bool(c.get('youtube_api_key')),
                'refreshInterval':c.get('refresh_interval', 300),
                'lastUpdated':    cache.get('updatedAt'),
                'channelCount':   len(load_json(F_CHANNELS, [])),
                'streamCount':    len(cache.get('streams', [])),
                'ip':             local_ip(),
                'inOperatingHours': _in_operating_hours(c),
                'operatingHours': oh,
                'version': __version__,
                'branch':  __branch__,
                'commit':  __commit__,
                'searchRemaining': _search_remaining(),
                'searchMaxDaily':  c.get('search_max_daily', 90),
            })

        elif p == '/api/tags':
            self._json(200, load_json(F_TAGS, []))

        elif p == '/api/channels':
            self._json(200, load_json(F_CHANNELS, []))

        elif p == '/api/streams':
            with _lock:
                cache = load_json(F_CACHE, {})
            self._json(200, cache.get('streams', []))

        elif p == '/api/config':
            c = cfg()
            safe = {k: v for k, v in c.items() if k not in ('youtube_api_key', 'twitch_client_secret')}
            safe['hasApiKey']      = bool(c.get('youtube_api_key'))
            safe['hasTwitchCreds'] = bool(c.get('twitch_client_id') and c.get('twitch_client_secret'))
            self._json(200, safe)

        elif p == '/api/stats':
            with _stats_lock:
                self._json(200, _load_stats())

        elif p == '/api/search/channels':
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            q  = qs.get('q', [''])[0].strip()
            if not q:
                self._json(400, {'error': 'q parameter required'}); return
            c          = cfg()
            api_key    = c.get('youtube_api_key', '')
            client_id  = c.get('twitch_client_id', '')
            client_sec = c.get('twitch_client_secret', '')
            if not api_key and not (client_id and client_sec):
                self._json(400, {'error': '検索にはYouTube APIキーまたはTwitch認証情報が必要です'}); return
            registered_ids = {ch['id'] for ch in load_json(F_CHANNELS, [])}
            result = {'youtube': [], 'twitch': []}
            if api_key:
                remaining = _search_remaining()
                if remaining <= 0:
                    self._json(429, {'error': '本日の検索上限に達しました', 'remaining': 0}); return
                _consume_search()
                try:
                    items = yt_search_channels(q, api_key)
                    for r in items:
                        r['registered'] = r['id'] in registered_ids
                    result['youtube'] = items
                except Exception as e:
                    result['youtubeError'] = str(e)
            if client_id and client_sec:
                try:
                    items = twitch_search_channels(q, client_id, client_sec)
                    for r in items:
                        r['registered'] = r['id'] in registered_ids
                    result['twitch'] = items
                except Exception as e:
                    result['twitchError'] = str(e)
            result['remaining'] = _search_remaining()
            self._json(200, result)

        elif p.startswith('/api/debug/resolve'):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = qs.get('url', [''])[0]
            if not target:
                self._json(400, {'error': 'url parameter required'}); return
            try:
                info = resolve_channel(target)
                chs = load_json(F_CHANNELS, [])
                info['alreadyRegistered'] = any(c['id'] == info['id'] for c in chs)
                self._json(200, info)
            except Exception as e:
                self._json(500, {'error': str(e)})

        else:
            rel = 'index.html' if p in ('', '/') else p.lstrip('/')
            ext = os.path.splitext(rel)[1].lower()
            if ext not in _STATIC_ALLOWED_EXT:
                self.send_response(403); self.end_headers(); return
            fp = os.path.join(STATIC, rel.replace('/', os.sep))
            # パストラバーサル防止
            try:
                safe = os.path.commonpath([os.path.abspath(fp), os.path.abspath(STATIC)])
                if safe != os.path.abspath(STATIC):
                    self.send_response(403); self.end_headers(); return
            except ValueError:
                self.send_response(403); self.end_headers(); return
            if os.path.isfile(fp):
                with open(fp, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', MIME.get(ext, 'application/octet-stream'))
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    def do_POST(self):
        p = self._path()

        if p == '/api/channels':
            d = self._body()
            if d is None: return
            url   = d.get('url', '').strip()
            if not url:
                self._json(400, {'error': 'URLが必要です'}); return
            try:
                info = resolve_channel(url)
            except Exception as e:
                self._json(400, {'error': str(e)}); return

            with _channels_lock:
                chs = load_json(F_CHANNELS, [])
                existing = next((c for c in chs if c['id'] == info['id']), None)
                if existing:
                    if existing['name'] != info['name']:
                        msg = f'「{info["name"]}」は既に「{existing["name"]}」として登録済みです（リブランド）。サイドバーの ✏ 編集で名前・URLを更新してください。'
                    else:
                        msg = f'「{info["name"]}」は既に登録されています'
                    self._json(409, {'error': msg}); return

                ch = {
                    'id':           info['id'],
                    'name':         info['name'],
                    'url':          url,
                    'thumbnail':    info.get('thumbnail'),
                    'tags':         d.get('tags', []),
                    'platform':     info.get('platform', 'youtube'),
                    'registeredAt': now_utc().isoformat(),
                }
                if info.get('platform') == 'twitch':
                    ch['twitchLogin'] = info.get('twitchLogin', '')
                chs.append(ch)
                save_json(F_CHANNELS, chs)
            threading.Thread(target=refresh_all, args=(ch,), daemon=True).start()
            self._json(201, ch)

        elif p == '/api/tags':
            d = self._body()
            if d is None: return
            name = d.get('name', '').strip()
            if not name:
                self._json(400, {'error': '名前が必要です'}); return
            with _tags_lock:
                tags = load_json(F_TAGS, [])
                if name not in tags:
                    tags.append(name)
                    save_json(F_TAGS, tags)
            self._json(200, tags)

        elif p == '/api/tags/reorder':
            d = self._body()
            if d is None: return
            order = d.get('order', [])
            with _tags_lock:
                existing = load_json(F_TAGS, [])
                new_tags = [t for t in order if t in existing]
                for t in existing:
                    if t not in new_tags:
                        new_tags.append(t)
                save_json(F_TAGS, new_tags)
            self._json(200, new_tags)

        elif p == '/api/channels/reorder':
            d = self._body()
            if d is None: return
            ids = d.get('ids', [])
            with _channels_lock:
                chs = load_json(F_CHANNELS, [])
                id_map = {c['id']: c for c in chs}
                new_order = [id_map[i] for i in ids if i in id_map]
                in_ids = set(ids)
                for c in chs:
                    if c['id'] not in in_ids:
                        new_order.append(c)
                save_json(F_CHANNELS, new_order)
            self._json(200, new_order)

        elif p == '/api/refresh':
            threading.Thread(target=_safe_refresh, daemon=True).start()
            self._json(200, {'ok': True, 'message': '更新を開始しました'})

        elif p == '/api/config':
            d = self._body()
            if d is None: return
            reset_twitch = False
            trigger_refresh = 'youtube_api_key' in d or 'twitch_client_secret' in d
            with _config_lock:
                c = cfg()
                if 'youtube_api_key' in d:
                    c['youtube_api_key'] = d['youtube_api_key']
                if 'twitch_client_id' in d:
                    c['twitch_client_id'] = d['twitch_client_id']
                if 'twitch_client_secret' in d:
                    c['twitch_client_secret'] = d['twitch_client_secret']
                    reset_twitch = True
                if 'refresh_interval' in d:
                    c['refresh_interval'] = max(60, int(d['refresh_interval']))
                if 'days_past' in d:
                    c['days_past']   = max(1, min(14, int(d['days_past'])))
                if 'days_future' in d:
                    c['days_future'] = max(1, min(14, int(d['days_future'])))
                if 'member_keywords' in d:
                    kws = [str(k).strip() for k in d['member_keywords'] if str(k).strip()]
                    c['member_keywords'] = kws
                if 'shorts_keywords' in d:
                    kws = [str(k).strip() for k in d['shorts_keywords'] if str(k).strip()]
                    c['shorts_keywords'] = kws
                if 'search_max_daily' in d:
                    c['search_max_daily'] = max(1, min(90, int(d['search_max_daily'])))
                if 'operating_hours' in d:
                    oh = d['operating_hours']
                    enabled = bool(oh.get('enabled', False))
                    start = str(oh.get('start', '08:00'))
                    end   = str(oh.get('end',   '23:00'))
                    def _valid_time(t):
                        try:
                            h, m = map(int, t.split(':'))
                            return 0 <= h <= 23 and 0 <= m <= 59
                        except Exception:
                            return False
                    if _valid_time(start) and _valid_time(end):
                        c['operating_hours'] = {'enabled': enabled, 'start': start, 'end': end}
                save_json(F_CONFIG, c)
            if reset_twitch:
                with _twitch_lock:
                    _twitch_token['token'] = None
                    _twitch_token['expires_at'] = 0
            if trigger_refresh:
                threading.Thread(target=_safe_refresh, daemon=True).start()
            self._json(200, {'ok': True})

        else:
            self.send_response(404); self.end_headers()

    def do_PUT(self):
        p = self._path()
        if p.startswith('/api/channels/'):
            cid = p.rsplit('/', 1)[-1]
            d   = self._body()
            if d is None: return
            with _channels_lock:
                chs = load_json(F_CHANNELS, [])
                for i, c in enumerate(chs):
                    if c['id'] == cid:
                        if 'name' in d: chs[i]['name'] = d['name']
                        if 'tags' in d: chs[i]['tags'] = d['tags']
                        save_json(F_CHANNELS, chs)
                        self._json(200, chs[i]); return
            self._json(404, {'error': 'not found'})
        else:
            self.send_response(404); self.end_headers()

    def do_DELETE(self):
        p = self._path()
        if p.startswith('/api/tags/'):
            name = p.rsplit('/', 1)[-1]
            with _tags_lock:
                tags = load_json(F_TAGS, [])
                tags = [t for t in tags if t != name]
                save_json(F_TAGS, tags)
            with _channels_lock:
                chs = load_json(F_CHANNELS, [])
                for c in chs:
                    if 'tags' in c:
                        c['tags'] = [t for t in c['tags'] if t != name]
                save_json(F_CHANNELS, chs)
            self._json(200, tags)

        elif p.startswith('/api/channels/'):
            cid = p.rsplit('/', 1)[-1]
            with _channels_lock:
                chs = load_json(F_CHANNELS, [])
                new = [c for c in chs if c['id'] != cid]
                save_json(F_CHANNELS, new)
            with _lock:
                cache = load_json(F_CACHE, {'streams': []})
                cache['streams'] = [s for s in cache['streams'] if s.get('channelId') != cid]
                save_json(F_CACHE, cache)
            self._json(200, {'ok': True})
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, fmt, *args):
        msg = fmt % args
        if '/api/ping' not in msg:
            print(f'[{_ts()}] {msg}')

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    c = cfg()
    port = c.get('port', PORT)

    for f, default in [(F_CHANNELS, []), (F_CACHE, {'streams': [], 'updatedAt': None}), (F_TAGS, [])]:
        if not os.path.exists(f):
            save_json(f, default)

    ip = local_ip()
    print('=' * 55)
    print('  Livelist サーバー')
    print('=' * 55)
    print(f'  ローカル:     http://localhost:{port}')
    print(f'  ネットワーク: http://{ip}:{port}')
    print('  (Ctrl+C で停止)')
    print('=' * 55)
    if not c.get('youtube_api_key'):
        print('\n  ⚠  YouTube API Key 未設定')
        print('     ブラウザで ⚙設定 → APIキー を登録してください')
    if not c.get('twitch_client_id') or not c.get('twitch_client_secret'):
        print('\n  ⚠  Twitch Client ID / Secret 未設定')
        print('     Twitchチャンネルを追加する場合は ⚙設定 で登録してください\n')

    threading.Thread(target=_refresh_loop, daemon=True).start()

    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nサーバーを停止しました。')
