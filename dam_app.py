import os
import json
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
import gspread
from google.oauth2.service_account import Credentials

# アプリの基本設定（centeredに変更してカード感を持たせる）
st.set_page_config(page_title="My Dam Map", layout="centered")

# --- Googleスプレッドシート接続設定 ---
def get_gcp_credentials():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    creds_path = os.path.join(".streamlit", "credentials.json")
    # パターン1: ローカルPCの場合（credentials.jsonが存在する）
    if os.path.exists(creds_path):
        return Credentials.from_service_account_file(creds_path, scopes=scopes)
    # パターン2: クラウド（本番環境）の場合（StreamlitのSecretsから読み込む）
    else:
        creds_info = st.secrets["gcp_service_account_json"]
        creds_dict = json.loads(creds_info) if isinstance(creds_info, str) else dict(creds_info)
        return Credentials.from_service_account_info(creds_dict, scopes=scopes)

@st.cache_resource(ttl=1800)
def get_worksheet():
    creds = get_gcp_credentials()
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(st.secrets["SPREADSHEET_ID"])
    return spreadsheet.sheet1

def parse_visited_flag(val):
    return str(val).strip().upper() in ("TRUE", "1", "YES", "済")

def load_dam_data_from_sheet():
    sheet = get_worksheet()
    all_values = sheet.get_all_values()
    if not all_values or len(all_values) < 2:
        st.error("スプレッドシートにデータが見つかりません。シートの中身を確認してください。")
        st.stop()

    headers = all_values[0]
    if "訪問済" not in headers:
        st.error("スプレッドシートの1行目に「訪問済」列が見つかりません。")
        st.stop()

    visited_col_num = headers.index("訪問済") + 1
    rows = []
    sheet_row_nums = []
    for row_idx, row in enumerate(all_values[1:], start=2):
        # 完全に空の行はスキップし、実際のシート行番号を記録する
        if any(cell.strip() != "" for cell in row):
            padded_row = row + [""] * (len(headers) - len(row))
            rows.append(padded_row[:len(headers)])
            sheet_row_nums.append(row_idx)

    df = pd.DataFrame(rows, columns=headers)
    df["_sheet_row"] = sheet_row_nums
    df["緯度"] = pd.to_numeric(df["緯度"], errors="coerce")
    df["経度"] = pd.to_numeric(df["経度"], errors="coerce")
    df["訪問済"] = df["訪問済"].apply(parse_visited_flag)
    return df, visited_col_num

# --- データの初期化 ---
if "dam_data" not in st.session_state or "visited_col_num" not in st.session_state:
    try:
        initial_data, visited_col_num = load_dam_data_from_sheet()
        st.session_state.dam_data = initial_data
        st.session_state.visited_col_num = visited_col_num
    except Exception as e:
        st.error(f"スプレッドシートの読み込みに失敗しました: {e}")
        st.stop()

# 表裏切り替えフラグの初期化（True: 表面/地図, False: 裏面/リスト）
if "is_front" not in st.session_state:
    st.session_state.is_front = True

# 状態切り替え用の関数
def toggle_card():
    st.session_state.is_front = not st.session_state.is_front

# チェックボックス操作時のデータ更新関数（該当する1セルのみピンポイント更新）
def toggle_visit(idx):
    current = bool(st.session_state.dam_data.at[idx, '訪問済'])
    new_val = not current
    sheet_row = int(st.session_state.dam_data.at[idx, '_sheet_row'])
    visited_col = int(st.session_state.visited_col_num)

    try:
        sheet = get_worksheet()
        sheet.update_cell(sheet_row, visited_col, new_val)
        st.session_state.dam_data.at[idx, '訪問済'] = new_val
    except Exception as e:
        st.error(f"スプレッドシートの更新に失敗しました: {e}")

# --- 地図のカスタムアイコン生成 ---
def create_custom_icon(pref_name, number, visited):
    hue = sum(ord(c) for c in pref_name) * 37 % 360
    if visited:
        bg_color = f"hsl({hue}, 100%, 40%)"
        text_color = "white"
        border = "2px solid white"
        shadow = "2px 2px 4px rgba(0,0,0,0.5)"
    else:
        bg_color = f"hsl({hue}, 20%, 80%)"
        text_color = "#444444"
        border = "1px solid #999999"
        shadow = "1px 1px 2px rgba(0,0,0,0.2)"
        
    html = f"""
    <div style="
        background-color: {bg_color};
        color: {text_color};
        border: {border};
        border-radius: 50%;
        width: 24px;
        height: 24px;
        display: flex;
        justify-content: center;
        align-items: center;
        font-size: 11px;
        font-weight: bold;
        box-shadow: {shadow};
    ">
        {number}
    </div>
    """
    return folium.DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))

# --- データの前処理 ---
st.session_state.dam_data['県名'] = st.session_state.dam_data['ダム名'].apply(lambda x: x.split('の')[0] if 'の' in x else 'その他')
st.session_state.dam_data['県内No'] = st.session_state.dam_data.groupby('県名').cumcount() + 1

pref_list = st.session_state.dam_data['県名'].unique().tolist()
default_index = pref_list.index('愛知県') if '愛知県' in pref_list else 0

# --- UI上部：都道府県選択（タイトル文字そのものをクリックできるボタンにする） ---
if "selected_pref" not in st.session_state:
    st.session_state.selected_pref = pref_list[default_index]

with st.popover(f"{st.session_state.selected_pref}ダム"):
    st.radio(
        "表示する県を選択",
        pref_list,
        key="selected_pref",
        label_visibility="collapsed"
    )

selected_pref = st.session_state.selected_pref

# 選択された県のデータに絞り込み
filtered_df = st.session_state.dam_data[st.session_state.dam_data['県名'] == selected_pref].copy()
filtered_df['表示用ダム名'] = filtered_df.apply(lambda row: row['ダム名'].replace(row['県名'] + 'の', ''), axis=1)

# 進捗の計算
total_dams = len(filtered_df)
visited_dams = filtered_df['訪問済'].sum()
progress_str = f"{visited_dams}/{total_dams}"


# --- カスタムCSSの注入（本物のカードデザインを再現） ---
common_css = """
<style>
/* Streamlitのデフォルトノイズと上部の見えないヘッダー余白を完全に消す */
#MainMenu, footer, header, div[data-testid="stToolbar"], div[data-testid="stDecoration"] {
    display: none !important;
}
.stApp {
    background-color: #f3f4f6;
}
section[data-testid="stMain"],
div[data-testid="stAppViewContainer"] {
    padding: 0 !important;
    margin: 0 !important;
}

/* ウィンドウの横幅・高さの両方に収まる最大サイズを基準単位（--u）として定義 */
:root {
    --card-w: min(100vw, 122vh);
    --u: calc(var(--card-w) / 100);
}

/* 1. カード全体の外枠（紺色・周囲の余白をなくし下側をスリム化） */
.block-container {
    background-color: #1f3b58 !important;
    border-radius: calc(var(--u) * 2) !important;
    padding: calc(var(--u) * 1.6) calc(var(--u) * 2.8) calc(var(--u) * 1.2) calc(var(--u) * 2.8) !important;
    max-width: var(--card-w) !important;
    width: var(--card-w) !important;
    margin: 0 auto !important;
    box-sizing: border-box !important;
    box-shadow: 0 10px 30px rgba(0,0,0,0.4) !important;
    position: relative;
}
/* CSS注入用の見えないブロックが余計な隙間を作らないように隠す */
div[data-testid="stElementContainer"]:has(style),
div.element-container:has(style) {
    display: none !important;
}
/* カード内の各要素（タイトル・地図・フッター）の縦間隔をカード幅に連動させる */
.block-container div[data-testid="stVerticalBlock"] {
    gap: calc(var(--u) * 0.8) !important;
}

/* 左上の黄色いタイトル（固定テキスト） */
.card-header-title {
    color: #FFD700;
    font-size: 1.8rem;
    font-weight: bold;
    margin-bottom: 5px;
    padding-left: 5px;
    line-height: 1.2;
    white-space: nowrap;
}

/* 2. ポップオーバーのボタンを「黄色いタイトル文字」に見せるCSS（カード幅に連動） */
div[data-testid="stPopover"] {
    margin-bottom: 0 !important;
}
div[data-testid="stPopover"] button {
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    padding: calc(var(--u) * 0.5) calc(var(--u) * 1.0) !important;
    min-height: 0 !important;
    color: #FFD700 !important;
}
/* マウスを乗せたときだけタイトル全体がうっすら光る */
div[data-testid="stPopover"] button:hover {
    background-color: rgba(255, 215, 0, 0.15) !important;
    border-radius: calc(var(--u) * 1.0) !important;
}
/* タイトル文字を黄色・太字にし、サイズをカード幅に連動させる */
div[data-testid="stPopover"] button p {
    color: #FFD700 !important;
    font-size: calc(var(--u) * 3.6) !important;
    font-weight: bold !important;
    line-height: 1.2 !important;
}
/* タイトル横の小さな矢印も色・サイズをカード幅に連動させる */
div[data-testid="stPopover"] button svg,
div[data-testid="stPopover"] button span[data-testid="stIconMaterial"] {
    color: #FFD700 !important;
    fill: #FFD700 !important;
    font-size: calc(var(--u) * 2.2) !important;
    width: calc(var(--u) * 2.2) !important;
    height: calc(var(--u) * 2.2) !important;
}
/* 開いた小窓の横幅をスリムにし、縦はスクロール可能にする */
div[data-testid="stPopoverBody"] {
    min-width: 140px !important;
    width: 160px !important;
    max-height: 300px !important;
    overflow-y: auto !important;
}

/* フッターの横並びを狭い画面でも絶対に折り返さない設定 */
div[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 0.5rem !important;
}
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    min-width: 0 !important;
    width: auto !important;
}
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:first-child,
div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:first-child {
    flex: 1 1 auto !important;
}
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:last-child,
div[data-testid="stHorizontalBlock"] > div[data-testid="column"]:last-child {
    flex: 0 0 auto !important;
}

/* 左下のバージョン表記（カード幅に連動） */
.card-version {
    color: white;
    font-size: calc(var(--u) * 1.7) !important;
    font-family: monospace;
    padding-left: calc(var(--u) * 0.6) !important;
    margin-top: 0 !important;
    white-space: nowrap !important;
}

/* 右下の切り替えボタン（上部の余白を完全に消し、文字を上下中央へ揃えてスリム化） */
div.stButton,
div[data-testid="stButton"] {
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1 !important;
}
.stButton button,
div[data-testid="stButton"] button {
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    background-color: rgba(255,255,255,0.1) !important;
    color: white !important;
    border: 1px solid rgba(255,255,255,0.3) !important;
    border-radius: calc(var(--u) * 0.75) !important;
    padding: calc(var(--u) * 0.45) calc(var(--u) * 1.5) !important;
    margin: 0 !important;
    min-height: 0 !important;
    height: auto !important;
    line-height: 1.2 !important;
    font-size: calc(var(--u) * 1.8) !important;
    font-weight: bold !important;
    white-space: nowrap !important;
}
.stButton button div[data-testid="stMarkdownContainer"],
div[data-testid="stButton"] button div[data-testid="stMarkdownContainer"],
.stButton button p,
div[data-testid="stButton"] button p,
div[data-testid="stPopover"] button p {
    margin: 0 !important;
    padding: 0 !important;
    line-height: 1.2 !important;
}
.stButton button p,
div[data-testid="stButton"] button p {
    font-size: calc(var(--u) * 1.8) !important;
}
.stButton button:hover,
div[data-testid="stButton"] button:hover {
    background-color: #FFD700 !important;
    color: #1f3b58 !important;
    border-color: #FFD700 !important;
}

/* スマホ横画面専用：左右に安全余白を残しつつ画面いっぱいに横長ワイド化 */
@media (orientation: landscape) and (max-height: 520px) {
    :root {
        --u: calc(100vh / 68);
        --u: calc(100dvh / 68);
    }
    .block-container {
        width: 90vw !important;
        max-width: 90vw !important;
        padding: calc(var(--u) * 0.9) calc(var(--u) * 2.0) calc(var(--u) * 0.7) calc(var(--u) * 2.0) !important;
        border-radius: calc(var(--u) * 1.5) !important;
    }
    .block-container div[data-testid="stVerticalBlock"] {
        gap: calc(var(--u) * 0.55) !important;
    }
    div[data-testid="stPopover"] button {
        padding: calc(var(--u) * 0.2) calc(var(--u) * 0.8) !important;
    }
    div[data-testid="stPopover"] button p {
        font-size: calc(var(--u) * 3.1) !important;
    }
    div[data-testid="stPopoverBody"] {
        max-height: 65dvh !important;
    }
    .card-version {
        font-size: max(10px, calc(var(--u) * 1.7)) !important;
    }
    .stButton button,
    div[data-testid="stButton"] button {
        padding: calc(var(--u) * 0.35) calc(var(--u) * 1.4) !important;
        font-size: max(11px, calc(var(--u) * 1.75)) !important;
    }
    .stButton button p,
    div[data-testid="stButton"] button p {
        font-size: max(11px, calc(var(--u) * 1.75)) !important;
    }
}
</style>
"""

if st.session_state.is_front:
    # --- 表面（地図）用のCSS ---
    mode_css = """
<style>
/* カード内側の枠を1.5:1の比率に固定し、茶色い枠線と角丸をカード幅に連動させる */
.block-container div[data-testid="stVerticalBlock"]:has(iframe):not(:has(div[data-testid="stVerticalBlock"])) {
    border: calc(var(--u) * 1.0) solid #8b5a2b !important;
    border-radius: calc(var(--u) * 0.8) !important;
    background-color: white !important;
    padding: 0 !important;
    gap: 0 !important;
    position: relative !important;
    width: 100% !important;
    aspect-ratio: 1.5 / 1 !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
}
/* 地図を含む要素を茶色い枠の縦横100%に隙間なくピタッと広げる */
.block-container div[data-testid="stElementContainer"]:has(iframe),
.block-container div.element-container:has(iframe) {
    position: absolute !important;
    top: 0 !important;
    left: 0 !important;
    width: 100% !important;
    height: 100% !important;
    line-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
}
.block-container div[data-testid="stElementContainer"]:has(iframe) > div,
.block-container div.element-container:has(iframe) > div {
    width: 100% !important;
    height: 100% !important;
}
/* 進捗テキスト用の見えない箱が下に隙間を作らないようにする */
.block-container div[data-testid="stElementContainer"]:has(.progress-overlay),
.block-container div.element-container:has(.progress-overlay) {
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    position: static !important;
}
/* iframe（地図本体）を親枠の縦横100%に完全追従させる */
.block-container iframe {
    width: 100% !important;
    height: 100% !important;
    border: none !important;
    display: block !important;
}
/* 地図上に重ねる右下の進捗テキスト（赤色・カード幅に連動） */
.progress-overlay {
    position: absolute !important;
    bottom: calc(var(--u) * 2.5) !important;
    right: calc(var(--u) * 3.0) !important;
    color: #902020 !important;
    font-size: calc(var(--u) * 5.0) !important;
    font-weight: bold !important;
    z-index: 1000 !important;
    line-height: 1 !important;
    text-shadow: calc(var(--u) * 0.25) calc(var(--u) * 0.25) calc(var(--u) * 0.5) rgba(255,255,255,0.8) !important;
    pointer-events: none !important;
}
/* スマホ横画面のときだけ縦横比を横長ワイド（2.55:1）に変更して確実に表示 */
@media (orientation: landscape) and (max-height: 520px) {
    .block-container div[data-testid="stVerticalBlock"]:has(iframe):not(:has(div[data-testid="stVerticalBlock"])) {
        aspect-ratio: 2.55 / 1 !important;
        height: auto !important;
    }
}
</style>
"""
else:
    # --- 裏面（リスト）用のCSS ---
    mode_css = """
<style>
/* 裏面は枠線を消しつつ、縦横比を表面の地図と揃える（2列構成になっても崩れない指定） */
div[data-testid="stVerticalBlock"]:has(.back-progress-text):not(:has(div[data-testid="stPopover"])) {
    border: none !important;
    background-color: transparent !important;
    padding: calc(var(--u) * 0.8) calc(var(--u) * 1.0) !important;
    width: 100% !important;
    aspect-ratio: 1.5 / 1 !important;
    overflow-y: auto !important;
    overflow-x: hidden !important;
    box-sizing: border-box !important;
    gap: calc(var(--u) * 0.5) !important;
}
/* 収まりきらない場合だけ目立ちすぎない細いスクロールバーを表示 */
div[data-testid="stVerticalBlock"]:has(.back-progress-text):not(:has(div[data-testid="stPopover"]))::-webkit-scrollbar {
    width: 6px;
}
div[data-testid="stVerticalBlock"]:has(.back-progress-text):not(:has(div[data-testid="stPopover"]))::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.28);
    border-radius: 3px;
}
div[data-testid="stVerticalBlock"]:has(.back-progress-text):not(:has(div[data-testid="stPopover"]))::-webkit-scrollbar-track {
    background: transparent;
}
.back-progress-text {
    color: white;
    font-size: calc(var(--u) * 2.1) !important;
    margin-bottom: calc(var(--u) * 1.0) !important;
    line-height: 1.4;
}
/* 裏面リストの2列表示を均等50%・上揃えに固定（フッターには影響させない） */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) {
    align-items: flex-start !important;
    gap: calc(var(--u) * 1.5) !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) > div[data-testid="stColumn"],
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) > div[data-testid="column"] {
    flex: 1 1 0 !important;
    width: 50% !important;
    min-width: 0 !important;
}
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) div[data-testid="stVerticalBlock"] {
    gap: calc(var(--u) * 0.45) !important;
}
/* チェックボックスの余白・最低高さを解除し、カード幅に連動して縮小させる */
div[data-testid="stCheckbox"] {
    margin: 0 !important;
    padding: 0 !important;
    min-height: 0 !important;
}
div[data-testid="stCheckbox"] label {
    min-height: 0 !important;
    padding: calc(var(--u) * 0.15) 0 !important;
    align-items: flex-start !important;
    gap: calc(var(--u) * 0.7) !important;
}
/* チェックボックスの四角アイコン自体もカード幅に合わせて縮小 */
div[data-testid="stCheckbox"] label > span:first-child {
    width: calc(var(--u) * 2.0) !important;
    height: calc(var(--u) * 2.0) !important;
    min-width: calc(var(--u) * 2.0) !important;
    min-height: calc(var(--u) * 2.0) !important;
    margin-top: calc(var(--u) * 0.25) !important;
    background-size: 70% !important;
    background-position: center !important;
    border-radius: calc(var(--u) * 0.4) !important;
}
/* リストの文字を確実に白にし、長い名称は自然に2行へ折り返す */
div[data-testid="stCheckbox"] label p {
    color: white !important;
    font-size: calc(var(--u) * 2.0) !important;
    line-height: 1.35 !important;
    white-space: normal !important;
    word-break: break-word !important;
}
/* 2列表示のときだけ文字間をわずかに詰めてスッキリ収める */
div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) div[data-testid="stCheckbox"] label p {
    font-size: calc(var(--u) * 1.85) !important;
    letter-spacing: -0.03em !important;
    line-height: 1.3 !important;
}
/* 訪問済（チェック入り）の文字色を水色に変える */
div[data-testid="stCheckbox"]:has(input:checked) label p {
    color: #60a5fa !important;
    font-weight: bold !important;
}
/* スマホ横画面のときだけ縦横比を横長ワイド（2.55:1）に変更して確実に表示 */
@media (orientation: landscape) and (max-height: 520px) {
    div[data-testid="stVerticalBlock"]:has(.back-progress-text):not(:has(div[data-testid="stPopover"])) {
        aspect-ratio: 2.55 / 1 !important;
        height: auto !important;
    }
    div[data-testid="stCheckbox"] label p,
    div[data-testid="stHorizontalBlock"]:has(div[data-testid="stCheckbox"]) div[data-testid="stCheckbox"] label p {
        font-size: max(12px, calc(var(--u) * 1.9)) !important;
    }
}
</style>
"""
    
# CSSをアプリに適用
st.markdown(common_css + mode_css, unsafe_allow_html=True)


# --- UI描画エリア（カードの中身） ---
# 内側のコンテナ（標準の枠線と余白を消し、CSSの茶色枠を直接適用する）
card_inner = st.container(border=False)

with card_inner:
    if st.session_state.is_front:
        # 【表面：地図モード】
        mapbox_token = st.secrets["MAPBOX_TOKEN"]
        map_tiles = f"https://api.mapbox.com/styles/v1/masachi/cmwhkwtw2005i01sl2oyc3dma/tiles/256/{{z}}/{{x}}/{{y}}?access_token={mapbox_token}"
        map_attr = "&copy; <a href='https://www.mapbox.com/about/maps/' target='_blank'>Mapbox</a>"
        
        # 選択された県の有効な座標データを抽出
        valid_coords = filtered_df.dropna(subset=["緯度", "経度"])
        
        if len(valid_coords) >= 2:
            min_lat, max_lat = valid_coords["緯度"].min(), valid_coords["緯度"].max()
            min_lon, max_lon = valid_coords["経度"].min(), valid_coords["経度"].max()
            center_lat = (min_lat + max_lat) / 2
            center_lon = (min_lon + max_lon) / 2
            m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles=map_tiles, attr=map_attr)
            if min_lat != max_lat or min_lon != max_lon:
                # ピンが枠の端ギリギリにならないよう余白を持たせて自動フィット
                m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=(30, 30), max_zoom=11)
        elif len(valid_coords) == 1:
            center_lat = valid_coords["緯度"].iloc[0]
            center_lon = valid_coords["経度"].iloc[0]
            m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles=map_tiles, attr=map_attr)
        else:
            # ダムが0件の県を選んだ場合の安全ガード（デフォルト位置）
            m = folium.Map(location=[35.29, 136.93], zoom_start=9, tiles=map_tiles, attr=map_attr)
        
        # パターンα：全国のピンを配置（緯度・経度が空欄の行は安全にスキップ）
        all_valid_dams = st.session_state.dam_data.dropna(subset=["緯度", "経度"])
        for i, row in all_valid_dams.iterrows():
            custom_icon = create_custom_icon(row["県名"], row["県内No"], row["訪問済"])
            folium.Marker(
                location=[row["緯度"], row["経度"]],
                popup=f"No.{row['県内No']} {row['ダム名']}",
                icon=custom_icon
            ).add_to(m)
            
        # 枠の縦横100%に自動追従するHTML形式で地図を描画
        map_html = m.get_root().render()
        components.html(map_html, height=500, scrolling=False)
        
        # 地図の上に赤文字の進捗を浮かせる
        st.markdown(f"<div class='progress-overlay'>{progress_str}</div>", unsafe_allow_html=True)
        
    else:
        # 【裏面：リストモード】
        st.markdown(f"<div class='back-progress-text'>{progress_str} ダム<br>以下表示している県のダムのリスト</div>", unsafe_allow_html=True)
        
        rows = list(filtered_df.iterrows())
        if len(rows) >= 8:
            # 8件以上は自動で2列表示（左列に前半、右列に後半を配置）
            mid = (len(rows) + 1) // 2
            list_cols = st.columns(2)
            with list_cols[0]:
                for idx, row in rows[:mid]:
                    st.checkbox(
                        f"{row['県内No']}. {row['表示用ダム名']}",
                        value=bool(row['訪問済']),
                        key=f"chk_{idx}",
                        on_change=toggle_visit,
                        args=(idx,)
                    )
            with list_cols[1]:
                for idx, row in rows[mid:]:
                    st.checkbox(
                        f"{row['県内No']}. {row['表示用ダム名']}",
                        value=bool(row['訪問済']),
                        key=f"chk_{idx}",
                        on_change=toggle_visit,
                        args=(idx,)
                    )
        else:
            # 7件以下はゆったり1列表示
            for idx, row in rows:
                st.checkbox(
                    f"{row['県内No']}. {row['表示用ダム名']}",
                    value=bool(row['訪問済']),
                    key=f"chk_{idx}",
                    on_change=toggle_visit,
                    args=(idx,)
                )

# --- フッターエリア（バージョン表記と切り替えボタン） ---
footer_cols = st.columns([4, 2])

with footer_cols[0]:
    st.markdown("<div class='card-version'>Ver.1.0 (2026.11)</div>", unsafe_allow_html=True)

with footer_cols[1]:
    btn_text = "↻ 裏面（リスト）を見る" if st.session_state.is_front else "↻ 表面（地図）を見る"
    st.button(btn_text, on_click=toggle_card, use_container_width=True)