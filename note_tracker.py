import requests
import csv
import os
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


def fetch_pv_stats():
    """ダッシュボードAPIから記事統計と全体集計を取得"""
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
    """記事一覧APIから公開日を取得し、key→公開日の辞書を返す"""
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
                # ISO形式の日時から日付部分だけ抜き出す（例: 2025-05-09T12:34:56+09:00 → 2025-05-09）
                publish_map[key] = publish_at[:10]
        if data.get('isLastPage', True):
            break
        page += 1
    return publish_map


def fetch_user_stats():
    """ユーザー情報（フォロワー数など）を取得"""
    url = f'https://note.com/api/v2/creators/{NOTE_USERNAME}'
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json().get('data', {})


def append_to_csv(filepath, headers, row):
    file_exists = filepath.exists()
    with open(filepath, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)
        writer.writerow(row)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')

    user = fetch_user_stats()
    articles, totals = fetch_pv_stats()
    publish_map = fetch_publish_dates()

    # ユーザー全体の統計
    append_to_csv(
        OUTPUT_DIR / 'user_stats.csv',
        ['日付', 'フォロワー数', 'フォロー数', '記事数', '総PV(直近1年)', '総スキ数(直近1年)', '総コメント数(直近1年)'],
        [today,
         user.get('followerCount'),
         user.get('followingCount'),
         len(articles),
         totals['total_pv'],
         totals['total_like'],
         totals['total_comment']]
    )

    # 記事ごとの統計（公開日を追加）
    for a in articles:
        key = a.get('key')
        publish_date = publish_map.get(key, '')
        append_to_csv(
            OUTPUT_DIR / 'article_stats.csv',
            ['日付', '公開日', 'タイトル', 'URL', 'PV', 'スキ数', 'コメント数'],
            [today,
             publish_date,
             a.get('name'),
             f"https://note.com/{NOTE_USERNAME}/n/{key}",
             a.get('read_count', 0),
             a.get('like_count', 0),
             a.get('comment_count', 0)]
        )

    print(f'[{today}] 記録完了：{len(articles)}記事、総PV {totals["total_pv"]}、総スキ {totals["total_like"]}')


if __name__ == '__main__':
    main()
