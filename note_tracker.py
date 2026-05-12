import requests
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# ===== 環境変数から読み込み =====
NOTE_USERNAME = os.environ['NOTE_USERNAME']
COOKIE = os.environ['NOTE_COOKIE']

OUTPUT_DIR = Path('./public')

HEADERS = {
    'Cookie': COOKIE,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://note.com/sitesettings/stats',
}


# ──────────────────────────────────────────────
# note API
# ──────────────────────────────────────────────

def check_auth():
    """セッションCookieの有効性を確認する"""
    url = 'https://note.com/api/v1/stats/pv?filter=yearly&page=1&sort=pv'
    res = requests.get(url, headers=HEADERS)

    if res.status_code == 401:
        print('::error::認証エラー（401）: NOTE_COOKIEが失効しています。GitHubのSecretsを更新してください。')
        sys.exit(1)
    if res.status_code == 403:
        print('::error::アクセス拒否（403）: NOTE_COOKIEが無効です。GitHubのSecretsを更新してください。')
        sys.exit(1)
    if res.status_code != 200:
        print(f'::error::APIエラー（{res.status_code}）: {res.text[:200]}')
        sys.exit(1)

    data = res.json().get('data', {})
    if not data.get('note_stats') and data.get('total_pv', 0) == 0:
        print('::error::データが取得できませんでした。NOTE_COOKIEが失効している可能性があります。')
        sys.exit(1)

    print('認証OK: Cookieは有効です。')


def fetch_pv_stats():
    all_articles = []
    totals = {'total_pv': 0, 'total_like': 0, 'total_comment': 0}
    page = 1

    while True:
        url = f'https://note.com/api/v1/stats/pv?filter=yearly&page={page}&sort=pv'
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        data = res.json().get('data', {})

        articles = data.get('note_stats', [])
        if not articles:
            break
        all_articles.extend(articles)

        if page == 1:
            totals['total_pv'] = data.get('total_pv', 0)
            totals['total_like'] = data.get('total_like', 0)
            totals['total_comment'] = data.get('total_comment', 0)

        if data.get('last_page', True):
            break
        page += 1

    return all_articles, totals


def fetch_publish_dates():
    publish_map = {}
    page = 1
    while True:
        url = f'https://note.com/api/v2/creators/{NOTE_USERNAME}/contents?kind=note&page={page}'
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        data = res.json().get('data', {})
        contents = data.get('contents', [])
        if not contents:
            break
        for c in contents:
            key = c.get('key')
            publish_at = c.get('publishAt') or c.get('publish_at')
            if key and publish_at:
                publish_map[key] = publish_at[:10]
        if data.get('isLastPage', True):
            break
        page += 1
    return publish_map


def fetch_user_stats():
    url = f'https://note.com/api/v2/creators/{NOTE_USERNAME}'
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json().get('data', {})


def fetch_char_count(key: str) -> int:
    try:
        url = f'https://note.com/api/v3/notes/{key}'
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        data = res.json().get('data', {})
        body_html = data.get('body', '') or ''
        text = re.sub(r'<[^>]+>', '', body_html)
        return len(text.strip())
    except Exception:
        return 0


def load_char_cache(cache_path: Path) -> dict:
    cache = {}
    if not cache_path.exists():
        return cache
    with open(cache_path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            key = row.get('key', '').strip()
            count = row.get('文字数', '0').strip()
            if key:
                try:
                    cache[key] = int(count)
                except ValueError:
                    cache[key] = 0
    return cache


def save_char_cache(cache_path: Path, cache: dict):
    with open(cache_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['key', '文字数'])
        for key, count in cache.items():
            writer.writerow([key, count])


def build_char_map(articles: list, cache_path: Path) -> dict:
    cache = load_char_cache(cache_path)
    updated = False

    for a in articles:
        key = a.get('key')
        if not key or key in cache:
            continue
        count = fetch_char_count(key)
        cache[key] = count
        updated = True
        print(f'  [文字数取得] {key}: {count}文字')
        time.sleep(0.5)

    if updated:
        save_char_cache(cache_path, cache)
        print(f'  [キャッシュ更新] {cache_path}')

    return cache


def append_to_csv(filepath, headers, row):
    file_exists = filepath.exists()
    with open(filepath, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerow(row)


# ──────────────────────────────────────────────
# main
# ──────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')

    print('認証チェック中...')
    check_auth()

    user = fetch_user_stats()
    articles, totals = fetch_pv_stats()
    publish_map = fetch_publish_dates()

    char_cache_csv = OUTPUT_DIR / 'char_cache.csv'
    print('文字数を取得中（キャッシュにない記事のみ）...')
    char_map = build_char_map(articles, char_cache_csv)

    user_csv = OUTPUT_DIR / 'user_stats.csv'
    append_to_csv(
        user_csv,
        ['日付', 'フォロワー数', 'フォロー数', '記事数', '総PV(直近1年)', '総スキ数(直近1年)', '総コメント数(直近1年)'],
        [today,
         user.get('followerCount'),
         user.get('followingCount'),
         len(articles),
         totals['total_pv'],
         totals['total_like'],
         totals['total_comment']]
    )

    article_csv = OUTPUT_DIR / 'article_stats.csv'
    for a in articles:
        key = a.get('key')
        publish_date = publish_map.get(key, '')
        char_count = char_map.get(key, 0)
        append_to_csv(
            article_csv,
            ['日付', '公開日', 'タイトル', 'URL', 'PV', 'スキ数', 'コメント数', '文字数'],
            [today,
             publish_date,
             a.get('name'),
             f"https://note.com/{NOTE_USERNAME}/n/{key}",
             a.get('read_count', 0),
             a.get('like_count', 0),
             a.get('comment_count', 0),
             char_count]
        )

    print(f'[{today}] 記録完了：{len(articles)}記事、総PV {totals["total_pv"]}、総スキ {totals["total_like"]}')


if __name__ == '__main__':
    main()
