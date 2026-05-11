import requests
import csv
import os
import json
import io
import re
import time
from datetime import datetime
from pathlib import Path

# google-auth と google-api-python-client が必要
# pip install google-auth google-api-python-client
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ===== 環境変数から読み込み =====
NOTE_USERNAME = os.environ['NOTE_USERNAME']
COOKIE = os.environ['NOTE_COOKIE']

# Google Drive 用（GitHub Actions の Secrets に設定）
# GOOGLE_SERVICE_ACCOUNT_JSON : サービスアカウントの JSON キー全体を文字列として格納
# GOOGLE_DRIVE_FOLDER_ID      : アップロード先フォルダの ID（共有設定でサービスアカウントに編集権限を付与すること）
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ['GOOGLE_SERVICE_ACCOUNT_JSON']
GOOGLE_DRIVE_FOLDER_ID = os.environ['GOOGLE_DRIVE_FOLDER_ID']

OUTPUT_DIR = Path('./public')

HEADERS = {
    'Cookie': COOKIE,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://note.com/sitesettings/stats',
}

DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']


# ──────────────────────────────────────────────
# Google Drive ヘルパー
# ──────────────────────────────────────────────

def build_drive_service():
    """サービスアカウント JSON からDriveクライアントを生成"""
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=DRIVE_SCOPES)
    return build('drive', 'v3', credentials=creds)


def get_or_create_file_id(service, filename: str, folder_id: str) -> str | None:
    """フォルダ内に同名ファイルがあれば ID を返し、なければ None を返す"""
    query = (
        f"name = '{filename}' "
        f"and '{folder_id}' in parents "
        f"and mimeType = 'text/csv' "
        f"and trashed = false"
    )
    result = service.files().list(q=query, fields='files(id, name)').execute()
    files = result.get('files', [])
    return files[0]['id'] if files else None


def upload_csv_to_drive(service, local_path: Path, folder_id: str):
    """
    ローカルの CSV を Google Drive の指定フォルダへアップロード。
    同名ファイルが既にある場合は上書き（update）、なければ新規作成（create）。
    """
    filename = local_path.name
    content = local_path.read_bytes()
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype='text/csv', resumable=False)

    file_id = get_or_create_file_id(service, filename, folder_id)

    if file_id:
        # 既存ファイルを上書き（メタデータは変更しない）
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f'  [Drive] 更新: {filename} (id={file_id})')
    else:
        # 新規作成
        metadata = {'name': filename, 'parents': [folder_id]}
        service.files().create(body=metadata, media_body=media, fields='id').execute()
        print(f'  [Drive] 新規作成: {filename}')


# ──────────────────────────────────────────────
# note API
# ──────────────────────────────────────────────

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


def fetch_char_count(key: str) -> int:
    """記事詳細APIから本文テキストを取得し文字数を返す（取得失敗時は0）"""
    try:
        url = f'https://note.com/api/v3/notes/{key}'
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.raise_for_status()
        data = res.json().get('data', {})
        # body フィールドはHTML。タグを除去してカウント
        body_html = data.get('body', '') or ''
        text = re.sub(r'<[^>]+>', '', body_html)
        return len(text.strip())
    except Exception:
        return 0


def load_char_cache(cache_path: Path) -> dict:
    """key→文字数 のキャッシュをCSVから読み込む"""
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
    """key→文字数 のキャッシュをCSVに保存"""
    with open(cache_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['key', '文字数'])
        for key, count in cache.items():
            writer.writerow([key, count])


def build_char_map(articles: list, cache_path: Path) -> dict:
    """
    全記事の key→文字数 を返す。
    キャッシュにない記事だけAPIを叩き、終了後にキャッシュを更新する。
    """
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
        time.sleep(0.5)  # レート制限対策

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

    user = fetch_user_stats()
    articles, totals = fetch_pv_stats()
    publish_map = fetch_publish_dates()

    # ── 文字数キャッシュ（初回のみAPIを叩き、以降は再利用）──
    char_cache_csv = OUTPUT_DIR / 'char_cache.csv'
    print('文字数を取得中（キャッシュにない記事のみ）...')
    char_map = build_char_map(articles, char_cache_csv)

    # ── ユーザー全体の統計 ──
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

    # ── 記事ごとの統計 ──
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

    # ── Google Drive へアップロード ──
    print('Google Drive へアップロード中...')
    drive = build_drive_service()
    for csv_path in [user_csv, article_csv, char_cache_csv]:
        upload_csv_to_drive(drive, csv_path, GOOGLE_DRIVE_FOLDER_ID)
    print('アップロード完了')


if __name__ == '__main__':
    main()
