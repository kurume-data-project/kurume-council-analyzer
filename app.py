import re
from datetime import datetime
from urllib.parse import urljoin

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="久留米市議会・議員活動分析", page_icon="🏛️", layout="wide")

GENERAL_INDEX = "https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/3030hongikai/4001shitumon/index.html"
NEWS_INDEX = "https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/3060tayori/2019-0726-0924-175t1.html"
MINUTES_HOME = "https://www.kensakusystem.jp/kurume/"
START_YEAR = 2018

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; KurumeCouncilResearch/1.0; public-information research)"}

@st.cache_data(ttl=86400, show_spinner=False)
def get_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "html.parser")

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_general_questions(start_year=START_YEAR):
    soup = get_soup(GENERAL_INDEX)
    links = []
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(GENERAL_INDEX, a["href"])
        m = re.search(r"(平成|令和)(\d+)年", title)
        if not m:
            continue
        era, n = m.group(1), int(m.group(2))
        year = 1988 + n if era == "平成" else 2018 + n
        if year >= start_year and "一般質問表" in title:
            links.append((title, href, year))
    rows = []
    for title, url, year in links:
        try:
            page = get_soup(url)
            for table in page.find_all("table"):
                for tr in table.find_all("tr"):
                    cells = tr.find_all(["th", "td"], recursive=False)
                    if len(cells) < 2:
                        continue
                    speaker = " ".join(cells[0].get_text(" ", strip=True).split())
                    content = "\n".join(x.strip() for x in cells[1].stripped_strings)
                    if not speaker or not content or speaker in ("質問者", "氏名"):
                        continue
                    rows.append({"年度": year, "会議": title, "議員": speaker, "質問項目": content, "公式資料": url})
        except Exception:
            continue
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["年度", "会議", "議員", "質問項目"]).reset_index(drop=True)
    return df, len(links)

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_newsletter_links():
    soup = get_soup(NEWS_INDEX)
    rows = []
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = urljoin(NEWS_INDEX, a["href"])
        if title and ("第" in title or "号" in title or href.lower().endswith(".pdf")):
            rows.append({"掲載資料": title, "URL": href})
    return pd.DataFrame(rows).drop_duplicates() if rows else pd.DataFrame(columns=["掲載資料", "URL"])

def theme_of(text):
    rules = {
        "子育て・教育": ["子ども", "子育て", "保育", "学校", "教育", "不登校", "給食", "学童"],
        "高齢・介護・福祉": ["高齢", "介護", "福祉", "障害", "生活保護", "医療", "健康"],
        "防災・危機管理": ["防災", "災害", "避難", "豪雨", "消防", "地震", "水害"],
        "道路・交通・都市整備": ["道路", "交通", "バス", "鉄道", "河川", "公園", "都市計画"],
        "産業・農業・観光": ["農業", "商工", "観光", "企業", "産業", "雇用", "創業"],
        "行政運営・DX・財政": ["財政", "予算", "行政", "DX", "デジタル", "職員", "市政", "窓口"],
        "環境・上下水道": ["環境", "ごみ", "水道", "下水", "脱炭素", "エネルギー"],
    }
    for theme, words in rules.items():
        if any(w in text for w in words):
            return theme
    return "その他・要確認"

def explainability_score(text):
    # Textual indicators only; this is a triage aid, not an AI judgement of a councillor.
    indicators = ["何人", "何件", "割合", "実績", "課題", "原因", "具体", "目標", "いつまで", "検証", "改善", "提案", "市民", "住民"]
    return round(100 * sum(w in text for w in indicators) / len(indicators))

st.title("🏛️ 久留米市議会・議員活動分析")
st.caption("2018年以降の一般質問を公式公開資料から取得し、テーマ・質問の特徴・市民への影響を整理する調査支援ツール")
st.info("このアプリは議員の優劣を自動判定するものではありません。公開資料から確認できる事実・分析上の手掛かりを整理します。質問項目一覧だけでは答弁内容や政策実現の有無は判断できません。")

with st.sidebar:
    st.header("データ取得")
    start_year = st.selectbox("分析開始年", list(range(2018, datetime.now().year + 1)), index=0)
    if st.button("公式資料を再取得", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("取得元：久留米市公式サイト。取得結果には出典URLを保持します。")

try:
    with st.spinner("久留米市公式サイトから一般質問一覧を取得しています…"):
        questions, page_count = fetch_general_questions(start_year)
    news = fetch_newsletter_links()
    fetch_error = None
except Exception as e:
    questions, page_count, news = pd.DataFrame(), 0, pd.DataFrame()
    fetch_error = str(e)

if fetch_error:
    st.error(f"公式資料を取得できませんでした：{fetch_error}")
elif questions.empty:
    st.warning("一般質問データを取得できませんでした。公式サイトの構造変更や一時的な通信障害の可能性があります。")
else:
    questions["テーマ（自動仮分類）"] = questions["質問項目"].apply(theme_of)
    questions["質問文の確認指標（参考）"] = questions["質問項目"].apply(explainability_score)
    st.success(f"一般質問の項目を {len(questions):,} 件取得しました（対象ページ {page_count} 件）。")

    t1, t2, t3, t4, t5 = st.tabs(["全体ダッシュボード", "議員別分析", "質問データ", "市民への影響", "情報発信・出典"])
    with t1:
        st.subheader("全体の傾向")
        a, b, c = st.columns(3)
        a.metric("質問項目数", f"{len(questions):,}")
        b.metric("掲載ページ数", f"{page_count:,}")
        c.metric("対象期間", f"{start_year}〜{datetime.now().year}")
        theme_counts = questions["テーマ（自動仮分類）"].value_counts().rename_axis("テーマ").reset_index(name="件数")
        st.plotly_chart(px.bar(theme_counts, x="テーマ", y="件数", title="政策テーマ別の質問項目数"), use_container_width=True)
        yearly = questions.groupby("年度").size().reset_index(name="質問項目数")
        st.plotly_chart(px.line(yearly, x="年度", y="質問項目数", markers=True, title="年度別の質問項目数"), use_container_width=True)
    with t2:
        st.subheader("議員別の質問傾向")
        names = sorted(questions["議員"].unique())
        selected = st.selectbox("議員を選択", names)
        person = questions[questions["議員"] == selected]
        st.metric("取得できた質問項目数", len(person))
        st.dataframe(person[["年度", "会議", "テーマ（自動仮分類）", "質問項目", "公式資料"]], use_container_width=True, hide_index=True)
        ptheme = person["テーマ（自動仮分類）"].value_counts().rename_axis("テーマ").reset_index(name="件数")
        st.plotly_chart(px.bar(ptheme, x="テーマ", y="件数", title=f"{selected}さんのテーマ傾向"), use_container_width=True)
    with t3:
        st.subheader("取得した一般質問データ")
        search = st.text_input("議員名・質問内容で検索")
        view = questions.copy()
        if search:
            view = view[view.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False)).any(axis=1)]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.download_button("取得データをCSV保存", view.to_csv(index=False).encode("utf-8-sig"), "kurume_general_questions.csv", "text/csv")
    with t4:
        st.subheader("市民への影響を整理する")
        st.write("質問項目から対象者を自動推定すると誤判定が起きるため、根拠を確認して分類する設計です。")
        st.dataframe(pd.DataFrame([
            ["子育て・教育", "子ども、保護者、学校関係者", "学校別児童生徒数、制度対象者数", "市統計・担当課資料"],
            ["高齢・介護・福祉", "高齢者、障害者、介護者等", "年齢別人口、認定者数、利用者数", "市統計・事業実績"],
            ["道路・交通", "沿線住民、通勤通学者、道路利用者", "路線・区域別人口、交通利用者数", "GIS・交通調査"],
            ["防災", "浸水想定区域・避難対象地域の住民", "ハザード区域人口、避難者数", "ハザードマップ・防災計画"],
        ], columns=["分野", "想定対象者（仮）", "規模推計に必要なデータ", "確認する根拠"]), use_container_width=True, hide_index=True)
        st.warning("対象市民数は、質問項目だけから数値化しません。人口統計・制度対象者数・対象区域等との照合後に、推計根拠と対象年度を明示して算出します。")
    with t5:
        st.subheader("情報発信力：公式サイト・市議会だよりのみ")
        st.write("個人SNSは評価対象外です。まず公式議会だよりの掲載資料リンクを収集します。")
        if not news.empty:
            st.dataframe(news, use_container_width=True, hide_index=True)
        else:
            st.info("議会だよりのリンク一覧を取得できませんでした。公式ページを直接確認してください。")
        st.markdown(f"- [一般質問の質問者及び質問項目]({GENERAL_INDEX})")
        st.markdown(f"- [市議会だよりバックナンバー]({NEWS_INDEX})")
        st.markdown(f"- [会議録検索システム]({MINUTES_HOME})")
        st.caption("会議録検索システムは平成19年5月以降の会議録を閲覧できますが、システム自身が公式記録ではない旨を表示しています。原本・公式公開資料との照合が必要です。")

st.divider()
st.caption("分析上の注意：テーマ分類と質問文指標は文字列に基づく参考値です。質問の質、行政の対応、市民への成果を確定評価するものではありません。評価を公表する際は、発言全文・答弁・公式資料・対象者数の根拠を人が確認してください。")
