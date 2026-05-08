import requests
import csv
import os
from datetime import datetime
from pathlib import Path

# ===== 環境変数から読み込み =====
NOTE_USERNAME = os.environ['NOTE_USERNAME']
COOKIE = os.environ['NOTE_COOKIE']
OUTPUT_DIR = Path('./note_stats')

HEADERS = {
    'Cookie': COOKIE,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://note.com/sitesettings/stats',
}


def fetch_pv_stats():
    all_articles = []
    page = 1
    while True:
        url = f'https://note.com/api/v3/stats/pv?filter=all&page={page}&sort=pv'
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        data = res.json().get('data', {})
        articles = data.get('note_stats', [])
        if not articles:
            break
        all_articles.extend(articles)
        if data.get('last_page', True):
            break
        page += 1
    return all_articles


def fetch_user_stats():
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
    articles = fetch_pv_stats()
    total_pv = sum(a.get('read_count', 0) for a in articles)
    total_likes = sum(a.get('like_count', 0) for a in articles)

    append_to_csv(
        OUTPUT_DIR / 'user_stats.csv',
        ['日付', 'フォロワー数', 'フォロー数', '記事数', '総PV', '総スキ数'],
        [today, user.get('followerCount'), user.get('followingCount'),
         len(articles), total_pv, total_likes]
    )

    for a in articles:
        append_to_csv(
            OUTPUT_DIR / 'article_stats.csv',
            ['日付', 'タイトル', 'URL', 'PV', 'スキ数', 'コメント数'],
            [today, a.get('name'),
             f"https://note.com/{NOTE_USERNAME}/n/{a.get('key')}",
             a.get('read_count', 0), a.get('like_count', 0),
             a.get('comment_count', 0)]
        )

    print(f'[{today}] 記録完了：{len(articles)}記事、総PV {total_pv}')


if __name__ == '__main__':
    main()
