#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
インフルエンサー検索・連絡先収集ツール

必要なライブラリ:
pip install streamlit requests beautifulsoup4 google-api-python-client gspread google-auth pandas
"""

import os
import re
import json
import time
import random
import tempfile
import datetime
import streamlit as st
import pandas as pd
import requests
from typing import List, Dict, Any, Optional, Union, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
import gspread
from google.oauth2.service_account import Credentials
import base64
import io

# ページ設定
st.set_page_config(
    page_title="インフルエンサー検索・連絡先収集ツール",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# セッション状態の初期化
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'filtered_results' not in st.session_state:
    st.session_state.filtered_results = []
if 'api_keys' not in st.session_state:
    st.session_state.api_keys = {}
if 'csv_filename' not in st.session_state:
    st.session_state.csv_filename = "influencer_contacts.csv"


class InfluencerFinder:
    """インフルエンサー情報収集のメインクラス"""
    
    def __init__(self):
        """初期化"""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
        # メールアドレス抽出用の正規表現パターン
        self.email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        
        # SNSプロフィールページのURL抽出用パターン
        self.sns_patterns = {
            'instagram': [
                re.compile(r'https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+/?'),
                re.compile(r'instagram\.com/[a-zA-Z0-9_.]+/?')
            ],
            'tiktok': [
                re.compile(r'https?://(?:www\.)?tiktok\.com/@[a-zA-Z0-9_.]+/?'),
                re.compile(r'tiktok\.com/@[a-zA-Z0-9_.]+/?')
            ],
            'youtube': [
                re.compile(r'https?://(?:www\.)?youtube\.com/(?:channel|user|c)/[a-zA-Z0-9_-]+/?'),
                re.compile(r'https?://(?:www\.)?youtube\.com/@[a-zA-Z0-9_-]+/?'),
                re.compile(r'youtube\.com/(?:channel|user|c)/[a-zA-Z0-9_-]+/?'),
                re.compile(r'youtube\.com/@[a-zA-Z0-9_-]+/?')
            ],
            'x_twitter': [
                re.compile(r'https?://(?:www\.)?(?:twitter|x)\.com/[a-zA-Z0-9_]+/?'),
                re.compile(r'(?:twitter|x)\.com/[a-zA-Z0-9_]+/?')
            ],
            'facebook': [
                re.compile(r'https?://(?:www\.)?facebook\.com/[a-zA-Z0-9.]+/?'),
                re.compile(r'facebook\.com/[a-zA-Z0-9.]+/?')
            ]
        }
    
    def search_google(self, query: str, api_key: str, cx: str, num_results: int = 20) -> List[Dict[str, Any]]:
        """
        Google Custom Search APIを使用して検索
        
        Args:
            query: 検索クエリ
            api_key: Google API Key
            cx: Custom Search Engine ID
            num_results: 取得する結果の数
            
        Returns:
            検索結果のリスト
        """
        try:
            service = build("customsearch", "v1", developerKey=api_key)
            
            # 複数ページの結果を取得
            all_results = []
            pages = (num_results + 9) // 10  # 10件ずつ取得するため、必要なページ数を計算
            
            for page in range(pages):
                start_index = page * 10 + 1
                
                # API呼び出し
                result = service.cse().list(
                    q=query,
                    cx=cx,
                    start=start_index
                ).execute()
                
                # 結果がない場合は終了
                if 'items' not in result:
                    break
                    
                all_results.extend(result['items'])
                
                # APIレート制限に配慮して少し待機
                time.sleep(0.5)
            
            # 検索結果を整形
            formatted_results = []
            for item in all_results[:num_results]:  # 指定された数だけ取得
                # タイトルから企業名/個人名を推定
                title = item.get('title', '')
                company_name = self._extract_company_name(title)
                
                result = {
                    'company_name': company_name,
                    'website_url': item.get('link', ''),
                    'title': title,
                    'snippet': item.get('snippet', ''),
                    'instagram_url': '',
                    'tiktok_url': '',
                    'youtube_url': '',
                    'x_url': '',
                    'facebook_url': '',
                    'email': '',
                    'source': 'Google Search'
                }
                formatted_results.append(result)
            
            return formatted_results
                
        except Exception as e:
            st.error(f"Google検索中にエラーが発生しました: {str(e)}")
            return []
    
    def _extract_company_name(self, title: str) -> str:
        """
        タイトルから企業名/個人名を推定する
        
        Args:
            title: ページタイトル
            
        Returns:
            推定された企業名/個人名
        """
        # 「|」「-」「:」などで分割し、最初の部分を取得
        separators = ['|', '-', ':', '：', '／', '/', '｜']
        company_name = title
        
        for sep in separators:
            if sep in company_name:
                parts = company_name.split(sep)
                company_name = parts[0].strip()
        
        return company_name
    
    def extract_contact_info(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        URLからコンタクト情報を抽出
        
        Args:
            result: 検索結果の辞書
            
        Returns:
            更新された検索結果辞書
        """
        url = result.get('website_url', '')
        if not url:
            return result
        
        try:
            # Webページを取得
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # ページのエンコーディングを確認
            if 'Content-Type' in response.headers:
                content_type = response.headers['Content-Type']
                if 'charset=' in content_type:
                    encoding = content_type.split('charset=')[1].split(';')[0].strip()
                    response.encoding = encoding
            
            # BeautifulSoupでHTMLを解析
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # スクリプトとスタイルタグを削除
            for script in soup(["script", "style"]):
                script.extract()
            
            # テキスト抽出
            text = soup.get_text()
            
            # HTMLソース全体
            html_source = response.text
            
            # メールアドレスを抽出
            emails = self._extract_emails(text)
            emails.extend(self._extract_emails(html_source))  # HTMLソースからも抽出
            if emails:
                result['email'] = emails[0]  # 最初のメールアドレスを使用
            
            # SNSリンクを抽出
            sns_urls = self._extract_sns_urls(html_source)
            sns_urls.update(self._extract_sns_urls_from_links(soup))
            
            # 結果を更新
            for sns_type, url in sns_urls.items():
                if url:
                    result[f'{sns_type}_url'] = url
            
            return result
            
        except Exception as e:
            st.warning(f"URL解析中にエラーが発生しました: {url} - {str(e)}")
            return result
    
    def _extract_emails(self, text: str) -> List[str]:
        """
        テキストからメールアドレスを抽出
        
        Args:
            text: 検索対象のテキスト
            
        Returns:
            メールアドレスのリスト
        """
        if not text:
            return []
        
        # メールアドレスを検索
        emails = self.email_pattern.findall(text)
        
        # 重複を削除して返す
        return list(set(emails))
    
    def _extract_sns_urls(self, text: str) -> Dict[str, str]:
        """
        テキストからSNS URLを抽出
        
        Args:
            text: 検索対象のテキスト
            
        Returns:
            SNSタイプとURLの辞書
        """
        sns_urls = {
            'instagram': '',
            'tiktok': '',
            'youtube': '',
            'x': '',
            'facebook': ''
        }
        
        # 各SNSのパターンでURLを検索
        for sns_type, patterns in self.sns_patterns.items():
            for pattern in patterns:
                matches = pattern.findall(text)
                if matches:
                    # 最初のマッチを使用
                    url = matches[0]
                    
                    # プロトコルがない場合は追加
                    if not url.startswith('http'):
                        url = 'https://' + url
                    
                    # SNSタイプに応じてキーを設定
                    if sns_type == 'x_twitter':
                        sns_urls['x'] = url
                    else:
                        sns_urls[sns_type] = url
                    break
        
        return sns_urls
    
    def _extract_sns_urls_from_links(self, soup: BeautifulSoup) -> Dict[str, str]:
        """
        HTMLのリンクからSNS URLを抽出
        
        Args:
            soup: BeautifulSoupオブジェクト
            
        Returns:
            SNSタイプとURLの辞書
        """
        sns_urls = {
            'instagram': '',
            'tiktok': '',
            'youtube': '',
            'x': '',
            'facebook': ''
        }
        
        # aタグを取得
        links = soup.find_all('a', href=True)
        
        for link in links:
            href = link.get('href', '')
            
            # Instagramリンク
            if 'instagram.com' in href:
                sns_urls['instagram'] = href
            
            # TikTokリンク
            elif 'tiktok.com' in href:
                sns_urls['tiktok'] = href
            
            # YouTubeリンク
            elif 'youtube.com' in href or 'youtu.be' in href:
                sns_urls['youtube'] = href
            
            # Twitter/Xリンク
            elif 'twitter.com' in href or 'x.com' in href:
                sns_urls['x'] = href
            
            # Facebookリンク
            elif 'facebook.com' in href:
                sns_urls['facebook'] = href
        
        return sns_urls
    
    def process_search_results(self, results: List[Dict[str, Any]], max_pages_to_scan: int = 20) -> List[Dict[str, Any]]:
        """
        検索結果を処理し、各URLからコンタクト情報を抽出
        
        Args:
            results: 検索結果のリスト
            max_pages_to_scan: スキャンするページの最大数
            
        Returns:
            コンタクト情報付きの検索結果
        """
        processed_results = []
        
        # プログレスバーを表示
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, result in enumerate(results[:max_pages_to_scan]):
            status_text.text(f"処理中... {i+1}/{min(len(results), max_pages_to_scan)}: {result['title']}")
            progress_percent = (i + 1) / min(len(results), max_pages_to_scan)
            progress_bar.progress(progress_percent)
            
            # リンク先のページからコンタクト情報を抽出
            if result.get('website_url'):
                updated_result = self.extract_contact_info(result.copy())
                processed_results.append(updated_result)
            else:
                processed_results.append(result)
            
            # サーバー負荷軽減のため少し待機
            time.sleep(random.uniform(0.5, 1.5))
        
        status_text.text("処理完了!")
        time.sleep(1)
        status_text.empty()
        progress_bar.empty()
        
        return processed_results


def export_to_csv(data: List[Dict[str, Any]], filename: str) -> Optional[str]:
    """
    データをCSVファイルにエクスポート
    
    Args:
        data: エクスポートするデータ
        filename: 出力するCSVファイル名
        
    Returns:
        ダウンロードリンク用のHTML
    """
    if not data:
        return None
    
    try:
        # 出力カラムを定義
        columns = [
            'company_name', 'website_url', 'instagram_url', 'tiktok_url', 
            'youtube_url', 'x_url', 'facebook_url', 'email'
        ]
        
        # 必要なカラムだけを抽出
        filtered_data = []
        for item in data:
            filtered_item = {col: item.get(col, '') for col in columns}
            filtered_data.append(filtered_item)
        
        # DataFrameに変換
        df = pd.DataFrame(filtered_data)
        
        # CSVデータの作成
        csv = df.to_csv(index=False)
        
        # CSVのエンコード
        b64 = base64.b64encode(csv.encode()).decode()
        
        # ダウンロードリンクの作成
        href = f'<a href="data:file/csv;base64,{b64}" download="{filename}">CSVファイルをダウンロード</a>'
        return href
        
    except Exception as e:
        st.error(f"CSVエクスポート中にエラーが発生しました: {str(e)}")
        return None


def export_to_google_spreadsheet(data: List[Dict[str, Any]], service_account_json: str, spreadsheet_id: str, sheet_name: str) -> bool:
    """
    データをGoogleスプレッドシートにエクスポート
    
    Args:
        data: エクスポートするデータ
        service_account_json: サービスアカウントJSONの内容
        spreadsheet_id: スプレッドシートID
        sheet_name: シート名
        
    Returns:
        成功したかどうか
    """
    try:
        # 出力カラムを定義
        columns = [
            'company_name', 'website_url', 'instagram_url', 'tiktok_url', 
            'youtube_url', 'x_url', 'facebook_url', 'email'
        ]
        
        # 必要なカラムだけを抽出
        filtered_data = []
        for item in data:
            filtered_item = {col: item.get(col, '') for col in columns}
            filtered_data.append(filtered_item)
        
        # 一時ファイルに書き込み
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json') as temp:
            json.dump(json.loads(service_account_json), temp)
            temp_path = temp.name
        
        # 認証
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        credentials = Credentials.from_service_account_file(temp_path, scopes=scope)
        gc = gspread.authorize(credentials)
        
        # 一時ファイルを削除
        os.unlink(temp_path)
        
        # スプレッドシートを開く
        try:
            spreadsheet = gc.open_by_key(spreadsheet_id)
        except Exception as e:
            st.error(f"スプレッドシートが見つかりません: {str(e)}")
            return False
        
        # シートを取得または作成
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
            worksheet.clear()  # 既存のデータをクリア
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
        
        # DataFrameに変換
        df = pd.DataFrame(filtered_data)
        
        # ヘッダと値を分離
        header = df.columns.tolist()
        values = df.values.tolist()
        
        # スプレッドシートに書き込み
        worksheet.update([header] + values)
        
        return True
        
    except Exception as e:
        st.error(f"Googleスプレッドシートへのエクスポート中にエラーが発生しました: {str(e)}")
        return False


def main():
    """メイン関数"""
    st.title("🔍 インフルエンサー検索・連絡先収集ツール")
    st.markdown("""
    このツールは、指定したジャンルのインフルエンサーや企業を検索し、
    ウェブサイトやSNSアカウント、メールアドレスなどの連絡先情報を自動的に収集します。
    """)
    
    # サイドバー - 設定
    with st.sidebar:
        st.title("検索設定")
        
        st.header("Google API設定")
        google_api_key = st.text_input(
            "Google API Key",
            type="password",
            value=st.session_state.api_keys.get('google_api_key', '')
        )
        
        google_cx = st.text_input(
            "Google Custom Search Engine ID",
            value=st.session_state.api_keys.get('google_cx', '')
        )
        
        if google_api_key and google_cx:
            st.session_state.api_keys['google_api_key'] = google_api_key
            st.session_state.api_keys['google_cx'] = google_cx
            st.success("Google API設定が保存されました")
        
        st.header("Google Spreadsheet設定")
        service_account_json = st.text_area(
            "サービスアカウントJSON",
            value=st.session_state.api_keys.get('google_service_account', ''),
            height=100
        )
        
        if service_account_json:
            try:
                # JSONとして解析できるか確認
                json.loads(service_account_json)
                st.session_state.api_keys['google_service_account'] = service_account_json
                st.success("サービスアカウント設定が保存されました")
            except json.JSONDecodeError:
                st.error("有効なJSONではありません")
    
    # メインコンテンツ
    # タブを作成
    search_tab, results_tab, export_tab = st.tabs(["検索", "結果", "エクスポート"])
    
    # 検索タブ
    with search_tab:
        st.header("インフルエンサー検索")
        
        search_keywords = st.text_input(
            "検索キーワード（スペース区切りで複数指定可能）",
            placeholder="例: スピリチュアル 子育て"
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            num_results = st.slider(
                "取得する結果数",
                min_value=10,
                max_value=50,
                value=20,
                step=10
            )
        
        with col2:
            search_categories = st.multiselect(
                "ジャンル（任意）",
                options=["スピリチュアル", "子育て", "ファッション", "情報商材", "ビジネス", "健康", "美容", "ライフスタイル"],
                default=[]
            )
        
        # 詳細オプション
        with st.expander("詳細オプション"):
            st.slider(
                "ページスキャン間隔（秒）",
                min_value=0.5,
                max_value=5.0,
                value=1.0,
                step=0.5,
                help="各ウェブページをスキャンする間隔です。短すぎると対象サイトに負荷がかかります。"
            )
            
            st.checkbox(
                "InstagramとTikTokを優先",
                value=True,
                help="検索結果からInstagramとTikTokのアカウントを優先的に探します。"
            )
        
        # 検索ボタン
        if st.button("検索開始", type="primary"):
            if not search_keywords:
                st.error("検索キーワードを入力してください。")
            elif not ('google_api_key' in st.session_state.api_keys and 'google_cx' in st.session_state.api_keys):
                st.error("Google API KeyとSearch Engine IDを設定してください。")
            else:
                # 検索クエリの構築
                query = search_keywords
                if search_categories:
                    query += " " + " ".join(search_categories)
                
                with st.spinner(f"「{query}」で検索中..."):
                    finder = InfluencerFinder()
                    results = finder.search_google(
                        query,
                        st.session_state.api_keys['google_api_key'],
                        st.session_state.api_keys['google_cx'],
                        num_results
                    )
                    
                    if results:
                        st.success(f"{len(results)}件の検索結果が見つかりました。各ページからコンタクト情報を収集しています...")
                        processed_results = finder.process_search_results(results, num_results)
                        
                        # 結果を保存
                        st.session_state.search_results = processed_results
                        st.session_state.filtered_results = processed_results
                        
                        # 結果タブに切り替え
                        results_tab.active = True
                        
                        st.success(f"処理が完了しました! {len(processed_results)}件の結果を収集しました。")
                    else:
                        st.error("検索結果が見つかりませんでした。キーワードを変更してみてください。")
    
    # 結果タブ
    with results_tab:
        if not st.session_state.search_results:
            st.info("まだ検索結果がありません。検索タブで検索を実行してください。")
        else:
            st.header("検索結果")
            
            # フィルタリングオプション
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                filter_instagram = st.checkbox("Instagramあり", value=False)
            
            with col2:
                filter_tiktok = st.checkbox("TikTokあり", value=False)
            
            with col3:
                filter_youtube = st.checkbox("YouTubeあり", value=False)
            
            with col4:
                filter_email = st.checkbox("メールアドレスあり", value=False)
            
            # フィルタリング適用
            filtered_results = st.session_state.search_results
            
            if filter_instagram:
                filtered_results = [item for item in filtered_results if item.get('instagram_url')]
            
            if filter_tiktok:
                filtered_results = [item for item in filtered_results if item.get('tiktok_url')]
            
            if filter_youtube:
                filtered_results = [item for item in filtered_results if item.get('youtube_url')]
            
            if filter_email:
                filtered_results = [item for item in filtered_results if item.get('email')]
            
            st.session_state.filtered_results = filtered_results
            
            # 結果表示
            st.write(f"フィルタ適用後: {len(filtered_results)} 件の結果")
            
            if filtered_results:
                # 結果をテーブルで表示
                table_data = []
                for item in filtered_results:
                    table_data.append({
                        "企業/個人名": item.get('company_name', ''),
                        "ウェブサイト": item.get('website_url', ''),
                        "Instagram": "✓" if item.get('instagram_url') else "",
                        "TikTok": "✓" if item.get('tiktok_url') else "",
                        "YouTube": "✓" if item.get('youtube_url') else "",
                        "X": "✓" if item.get('x_url') else "",
                        "メール": "✓" if item.get('email') else ""
                    })
                
                st.table(pd.DataFrame(table_data))
                
                # 詳細表示
                st.subheader("詳細情報")
                for i, result in enumerate(filtered_results):
                    with st.expander(f"{i+1}. {result['company_name']}"):
                        st.markdown(f"**タイトル:** {result['title']}")
                        st.markdown(f"**概要:** {result['snippet']}")
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown(f"**ウェブサイト:** [{result['website_url']}]({result['website_url']})")
                            if result.get('instagram_url'):
                                st.markdown(f"**Instagram:** [{result['instagram_url']}]({result['instagram_url']})")
                            if result.get('tiktok_url'):
                                st.markdown(f"**TikTok:** [{result['tiktok_url']}]({result['tiktok_url']})")
                        
                        with col2:
                            if result.get('youtube_url'):
                                st.markdown(f"**YouTube:** [{result['youtube_url']}]({result['youtube_url']})")
                            if result.get('x_url'):
                                st.markdown(f"**X (Twitter):** [{result['x_url']}]({result['x_url']})")
                            if result.get('facebook_url'):
                                st.markdown(f"**Facebook:** [{result['facebook_url']}]({result['facebook_url']})")
                        
                        if result.get('email'):
                            st.markdown(f"**メール:** {result['email']}")
            else:
                st.warning("フィルタ条件に一致する結果がありません。")
    
    # エクスポートタブ
    with export_tab:
        st.header("データのエクスポート")
        
        if not st.session_state.filtered_results:
            st.info("エクスポートするデータがありません。検索タブで検索を実行してください。")
        else:
            st.write(f"エクスポート可能な結果: {len(st.session_state.filtered_results)} 件")
            
            # CSVエクスポート
            st.subheader("CSVファイルとしてエクスポート")
            
            csv_filename = st.text_input(
                "CSVファイル名",
                value=st.session_state.csv_filename
            )
            
            st.session_state.csv_filename = csv_filename
            
            if st.button("CSVファイルを生成"):
                csv_href = export_to_csv(st.session_state.filtered_results, csv_filename)
                if csv_href:
                    st.markdown(csv_href, unsafe_allow_html=True)
                    st.success(f"{csv_filename} の生成に成功しました。上のリンクからダウンロードしてください。")
            
            # Googleスプレッドシートエクスポート
            st.subheader("Googleスプレッドシートにエクスポート")
            
            if 'google_service_account' in st.session_state.api_keys and st.session_state.api_keys['google_service_account']:
                col1, col2 = st.columns(2)
                
                with col1:
                    spreadsheet_id = st.text_input("SpreadsheetのID", key="spreadsheet_id")
                
                with col2:
                    sheet_name = st.text_input(
                        "シート名", 
                        value=f"インフルエンサー_{datetime.datetime.now().strftime('%Y%m%d')}", 
                        key="sheet_name"
                    )
                
                if st.button("Googleスプレッドシートに出力"):
                    if not spreadsheet_id:
                        st.error("Spreadsheet IDを入力してください。")
                    else:
                        with st.spinner("Googleスプレッドシートに出力中..."):
                            success = export_to_google_spreadsheet(
                                st.session_state.filtered_results,
                                st.session_state.api_keys['google_service_account'],
                                spreadsheet_id,
                                sheet_name
                            )
                            if success:
                                st.success(f"データを「{sheet_name}」シートに出力しました！")
            else:
                st.warning("Googleスプレッドシートにエクスポートするには、サイドバーでサービスアカウントJSONを設定してください。")


if __name__ == "__main__":
    main()