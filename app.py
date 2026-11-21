import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="久留米市議会・議員活動分析",
    page_icon="🏛️",
    layout="wide",
)

GENERAL_INDEX = (
    "https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/"
    "3030hongikai/4001shitumon/index.html"
)
NEWS_INDEX = (
    "https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/"
    "3060tayori/2019-0726-0924-175t1.html"
)
MINUTES_HOME = "https://www.kensakusystem.jp/kurume/"
MEMBER_LIST_URL = (
    "https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/"
    "3020meibo/2019-0509-1504-175t1.html"
)
TERM_START_YEAR = 2023
TERM_START_SESSION = 2  # 令和5年第2回定例会以降を現任期として扱う
TERM_END_YEAR = 2027
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; KurumeCouncilResearch/2.0; "
        "public-information research)"
    )
}
REQUEST_TIMEOUT = 30


def clean_text(value):
    """Normalize display text without changing the original source column."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalize_member_name(value):
    """Stable person key: ignore faction labels and spacing variants."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("・", "").replace("･", "")
    return text.strip()


def era_to_year(text):
    """Convert Japanese era year in a title to Gregorian year."""
    match = re.search(r"(令和|平成)(元|\d+)年", text)
    if not match:
        return None
    era, number = match.groups()
    n = 1 if number == "元" else int(number)
    return (2018 + n) if era == "令和" else (1988 + n)


@st.cache_data(ttl=86400, show_spinner=False)
def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return BeautifulSoup(response.text, "html.parser")


@st.cache_data(ttl=86400, show_spinner=False)
def discover_general_pages(start_year=TERM_START_YEAR):
    soup = get_soup(GENERAL_INDEX)
    found = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        title = clean_text(anchor.get_text(" ", strip=True))
        url = urljoin(GENERAL_INDEX, anchor["href"])
        year = era_to_year(title)
        # 現任期は2023年5月2日開始。2023年分は第2回定例会以降に限定。
        session_match = re.search(r"第\s*([0-9０-９]+)\s*回", title)
        session_number = (
            int(unicodedata.normalize("NFKC", session_match.group(1)))
            if session_match else None
        )
        in_current_term = (
            year is not None
            and (year > TERM_START_YEAR or (
                year == TERM_START_YEAR
                and session_number is not None
                and session_number >= TERM_START_SESSION
            ))
            and year <= TERM_END_YEAR
        )
        if (
            in_current_term
            and year >= int(start_year)
            and "一般質問表" in title
            and url not in seen
        ):
            found.append(
                {
                    "年度": year,
                    "会議": title,
                    "公式資料URL": url,
                }
            )
            seen.add(url)

    return sorted(found, key=lambda item: (item["年度"], item["会議"]))



def split_question_items(cell):
    """Return atomic question items, preserving their parent headings.

    A main item (1., 2.) is retained as the parent. If it has sub-items
    ((1), ア, イ, etc.), each sub-item becomes an individual record.
    Continuation lines stay with the immediately preceding item.
    """
    raw = cell.get_text("\n", strip=True)
    lines = []
    for raw_line in raw.splitlines():
        line = unicodedata.normalize("NFKC", raw_line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)

    main_re = re.compile(r"^([0-9]+)\s*[.．、)]\s*(.*)$")
    paren_re = re.compile(r"^[（(]\s*([0-9]+)\s*[）)]\s*(.*)$")
    kana_re = re.compile(r"^([アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワ])(?:[.．、]|\s+)\s*(.*)$")
    sub_re = re.compile(r"^([0-9]+)\s*[）)]\s*(.*)$")

    mains = []
    current_main = None
    current_sub = None

    for line in lines:
        m = main_re.match(line)
        if m:
            current_main = {
                "parent_number": m.group(1),
                "parent_title": m.group(2).strip(),
                "children": [],
            }
            mains.append(current_main)
            current_sub = None
            continue

        m = paren_re.match(line) or kana_re.match(line) or sub_re.match(line)
        if m and current_main is not None:
            current_sub = {
                "sub_number": m.group(1),
                "sub_title": m.group(2).strip(),
                "detail": [],
            }
            current_main["children"].append(current_sub)
            continue

        # Indented continuation / additional descriptive line.
        if current_sub is not None:
            current_sub["detail"].append(line)
        elif current_main is not None:
            current_main["parent_title"] = (
                current_main["parent_title"] + " " + line
            ).strip()
        else:
            # Text before the first numbered item is kept as a standalone item.
            mains.append({
                "parent_number": "",
                "parent_title": line,
                "children": [],
            })
            current_main = mains[-1]

    records = []
    for main in mains:
        if main["children"]:
            for child in main["children"]:
                records.append({
                    "質問番号": main["parent_number"],
                    "質問項目": child["sub_title"],
                    "質問詳細": "\n".join(child["detail"]),
                    "親質問番号": main["parent_number"],
                    "親質問項目": main["parent_title"],
                    "小項目番号": child["sub_number"],
                    "分割階層": "小項目",
                })
        else:
            records.append({
                "質問番号": main["parent_number"],
                "質問項目": main["parent_title"],
                "質問詳細": "",
                "親質問番号": "",
                "親質問項目": "",
                "小項目番号": "",
                "分割階層": "大項目",
            })

    return records

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_current_members():
    """Get current council members from Kurume City's official roster."""
    soup = get_soup(MEMBER_LIST_URL)
    members = set()

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                continue
            seat = clean_text(cells[0].get_text(" ", strip=True))
            if not re.fullmatch(r"[0-9０-９]+", seat):
                continue
            # The second column contains the name and, in parentheses, kana.
            name = cells[1].get_text(" ", strip=True)
            name = re.sub(r"[（(].*?[）)]", "", name)
            key = normalize_member_name(name)
            if key:
                members.add(key)

    if not members:
        raise RuntimeError(
            "久留米市公式の現職議員名簿を読み取れませんでした。"
            "安全のため、全議員データを表示せず取得を停止しました。"
        )
    return members


def parse_question_page(page_url, year, meeting_title):
    """Parse the official questioner/question-item tables.

    The official pages list questioners and notified question subjects.
    They do not necessarily contain the full spoken question or answers.
    """
    soup = get_soup(page_url)
    rows = []

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                continue

            speaker = clean_text(cells[0].get_text(" ", strip=True))
            content = cells[1].get_text("\n", strip=True)
            if not speaker or not content:
                continue
            if speaker in {"質問者", "氏名", "質問議員"}:
                continue
            if "質問項目" in speaker and len(speaker) < 15:
                continue

            member_name = normalize_member_name(speaker)
            item_records = split_question_items(cells[1])

            for item in item_records:
                rows.append(
                    {
                        "年度": year,
                        "会議": meeting_title,
                        "議員": member_name or speaker,
                        "議員表示名（公式表記）": speaker,
                        **item,
                        "資料種別": "公式一般質問表（小項目単位に分割）",
                        "取得日時": datetime.now().astimezone().isoformat(
                            timespec="seconds"
                        ),
                        "公式資料URL": page_url,
                        "質問本文": "",
                        "答弁本文": "",
                        "会議録URL": MINUTES_HOME,
                        "本文取得状況": "未取得：会議録で確認が必要",
                    }
                )

    # Some pages may use non-table markup. Do not guess speaker/question
    # boundaries; report no rows rather than manufacturing records.
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_general_questions(start_year=TERM_START_YEAR):
    pages = discover_general_pages(start_year)
    all_rows = []
    errors = []

    for page in pages:
        try:
            page_rows = parse_question_page(
                page["公式資料URL"], page["年度"], page["会議"]
            )
            all_rows.extend(page_rows)
            if not page_rows:
                errors.append(
                    f"{page['会議']}: 読み取れる表が見つかりませんでした"
                )
        except Exception as exc:
            errors.append(f"{page['会議']}: {type(exc).__name__}: {exc}")

    columns = [
        "年度", "会議", "議員", "議員表示名（公式表記）",
        "質問番号", "質問項目", "質問詳細", "親質問番号", "親質問項目",
        "小項目番号", "分割階層", "資料種別", "取得日時",
        "公式資料URL", "質問本文", "答弁本文", "会議録URL",
        "本文取得状況",
    ]
    df = pd.DataFrame(all_rows, columns=columns)
    # Limit analysis to people currently listed as Kurume City council members.
    current_members = fetch_current_members()
    if not df.empty:
        df = df[df["議員"].map(normalize_member_name).isin(current_members)].copy()
        df = df.drop_duplicates(
            subset=["年度", "会議", "議員", "質問番号", "小項目番号", "質問項目"]
        ).reset_index(drop=True)

    return df, len(pages), errors


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_newsletter_links():
    soup = get_soup(NEWS_INDEX)
    rows = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        title = clean_text(anchor.get_text(" ", strip=True))
        url = urljoin(NEWS_INDEX, anchor["href"])
        is_relevant = (
            ("議会だより" in title)
            or ("市議会だより" in title)
            or re.search(r"\d+号", title) is not None
            or url.lower().endswith(".pdf")
        )
        if title and is_relevant and url not in seen:
            rows.append(
                {
                    "掲載資料": title,
                    "URL": url,
                    "確認状況": "リンク取得済み・掲載内容の精査は未実施",
                }
            )
            seen.add(url)

    return pd.DataFrame(rows)


def theme_of(text):
    rules = {
        "子育て・教育": [
            "子ども", "子育て", "保育", "学校", "教育", "不登校",
            "給食", "学童", "児童", "生徒",
        ],
        "高齢・介護・福祉": [
            "高齢", "介護", "福祉", "障害", "生活保護", "医療",
            "健康", "認知症",
        ],
        "防災・危機管理": [
            "防災", "災害", "避難", "豪雨", "消防", "地震", "水害",
        ],
        "道路・交通・都市整備": [
            "道路", "交通", "バス", "鉄道", "河川", "公園",
            "都市計画", "駅",
        ],
        "産業・農業・観光": [
            "農業", "商工", "観光", "企業", "産業", "雇用", "創業",
        ],
        "行政運営・DX・財政": [
            "財政", "予算", "行政", "DX", "デジタル", "職員",
            "市政", "窓口", "公共施設",
        ],
        "環境・上下水道": [
            "環境", "ごみ", "水道", "下水", "脱炭素", "エネルギー",
        ],
    }
    value = str(text or "")
    for theme, words in rules.items():
        if any(word in value for word in words):
            return theme
    return "その他・要確認"


def broad_member_type(theme_share, total_count):
    """Transparent, rule-based descriptive grouping; not a value judgement."""
    if total_count < 5 or theme_share.empty:
        return "判定保留（質問数不足）", "分類済み質問が5件未満"
    top_theme = theme_share.index[0]
    top_share = float(theme_share.iloc[0])
    if top_share < 0.35:
        return "総合・幅広型", "特定テーマへの偏りが小さい"
    groups = {
        "子育て・教育重視型": ["子育て・教育"],
        "福祉・生活重視型": ["高齢・介護・福祉"],
        "防災・安全重視型": ["防災・危機管理"],
        "都市基盤・交通重視型": ["道路・交通・都市整備"],
        "産業・地域振興型": ["産業・農業・観光"],
        "行政運営・財政型": ["行政運営・DX・財政"],
        "環境・生活基盤型": ["環境・上下水道"],
    }
    for label, themes in groups.items():
        if top_theme in themes:
            return label, f"最多テーマ：{top_theme}（{top_share:.0%}）"
    return "総合・幅広型", "複数テーマに分散"


def text_clarity_indicators(text):
    """Transparent text indicators only; not a quality score."""
    value = str(text or "")
    indicators = {
        "数値・規模": ["何人", "何件", "割合", "人数", "件数", "％", "%"],
        "課題・原因": ["課題", "原因", "現状", "問題", "背景"],
        "期限・目標": ["いつまで", "目標", "年度", "期限", "今後"],
        "改善・提案": ["改善", "提案", "見直し", "拡充", "導入"],
        "市民との関係": ["市民", "住民", "保護者", "利用者", "地域"],
    }
    return {
        label: "該当" if any(word in value for word in words) else "記載確認なし"
        for label, words in indicators.items()
    }


st.title("🏛️ 久留米市議会・議員活動分析")
st.caption(
    "現任期中の一般質問を久留米市公式公開資料から取得し、"
    "質問テーマ・会議録確認状況・市民への影響・公式発信を整理する調査支援ツール"
)
st.info(
    "このアプリは議員の優劣を自動判定するものではありません。"
    "一般質問表から取得できるのは主に質問者と質問項目です。"
    "質問本文・答弁本文・政策実現の有無は、会議録等の根拠を確認して別途記録します。"
)

with st.sidebar:
    st.header("データ取得")
    start_year = TERM_START_YEAR
    st.info("分析対象：現任期（2023年5月2日以降）")
    if st.button("公式資料を再取得", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("取得元：久留米市公式サイト")
    st.caption("分析対象：公式名簿に掲載された現職議員のみ")
    st.caption("現任期中の一般質問のみを集計します。")
    st.caption("現職名簿：" + MEMBER_LIST_URL)

try:
    with st.spinner("久留米市公式サイトから一般質問表を取得しています…"):
        questions, page_count, fetch_errors = fetch_general_questions(start_year)
    news = fetch_newsletter_links()
    fetch_error = None
except Exception as exc:
    questions = pd.DataFrame()
    page_count = 0
    fetch_errors = []
    news = pd.DataFrame()
    fetch_error = f"{type(exc).__name__}: {exc}"

if fetch_error:
    st.error(f"公式資料を取得できませんでした：{fetch_error}")
elif questions.empty:
    st.warning(
        "一般質問データを取得できませんでした。"
        "公式サイトの構造変更、通信障害、または表形式の変更が考えられます。"
    )
    st.write(f"確認した公式資料ページ数：{page_count}")
    if fetch_errors:
        st.dataframe(
            pd.DataFrame({"取得時の注意": fetch_errors}),
            use_container_width=True,
            hide_index=True,
        )
else:
    questions["テーマ（自動仮分類）"] = questions["質問項目"].apply(theme_of)
    indicator_frame = questions["質問項目"].apply(text_clarity_indicators).apply(pd.Series)
    questions = pd.concat([questions, indicator_frame], axis=1)

    st.success(
        f"現職議員の質問項目を {len(questions):,} 件取得しました"
        f"（対象ページ {page_count:,} 件）。"
    )
    if fetch_errors:
        with st.expander(f"取得時の注意（{len(fetch_errors)}件）"):
            for item in fetch_errors:
                st.write(f"- {item}")

    t1, t2, t3, t4, t5 = st.tabs(
        [
            "全体ダッシュボード",
            "議員別分析",
            "質問データ",
            "市民への影響",
            "情報発信・出典",
        ]
    )

    with t1:
        st.subheader("全体の傾向")
        col1, col2, col3 = st.columns(3)
        col1.metric("質問項目数", f"{len(questions):,}")
        col2.metric("取得ページ数", f"{page_count:,}")
        col3.metric("対象期間", "2023年5月〜現任期終了")

        # 「その他・要確認」は分類の精度を確認できていないため、円グラフから除外。
        chart_questions = questions[
            questions["テーマ（自動仮分類）"] != "その他・要確認"
        ]
        theme_counts = (
            chart_questions["テーマ（自動仮分類）"]
            .value_counts()
            .rename_axis("テーマ")
            .reset_index(name="件数")
        )
        if not theme_counts.empty:
            st.plotly_chart(
                px.pie(
                    theme_counts,
                    names="テーマ",
                    values="件数",
                    title="政策テーマ別の質問項目割合（その他・要確認を除く）",
                    hole=0.35,
                ),
                use_container_width=True,
            )
        else:
            st.info("円グラフに表示できる分類済みデータがありません。")
        yearly = (
            questions.groupby("年度")
            .size()
            .reset_index(name="質問項目数")
        )
        st.plotly_chart(
            px.line(
                yearly, x="年度", y="質問項目数",
                markers=True, title="年度別の質問項目数",
            ),
            use_container_width=True,
        )
        st.caption(
            "集計単位は公式一般質問表から抽出した質問項目です。"
            "発言回数や質問時間を示すものではありません。"
        )

        st.subheader("質問テーマの変化")
        trend = chart_questions.groupby(["年度", "テーマ（自動仮分類）"]).size().reset_index(name="件数")
        if not trend.empty:
            trend["年度"] = trend["年度"].astype(str)
            st.plotly_chart(
                px.bar(
                    trend, x="年度", y="件数", color="テーマ（自動仮分類）",
                    title="年度別・政策テーマ別の質問項目数",
                ), use_container_width=True,
            )
            share_trend = trend.copy()
            share_trend["割合"] = share_trend["件数"] / share_trend.groupby("年度")["件数"].transform("sum")
            st.plotly_chart(
                px.line(
                    share_trend, x="年度", y="割合", color="テーマ（自動仮分類）",
                    markers=True, title="政策テーマ別の構成比の推移",
                ), use_container_width=True,
            )
            available_years = sorted(chart_questions["年度"].dropna().astype(int).unique())
            if len(available_years) >= 2:
                first_years = available_years[:min(3, len(available_years))]
                last_years = available_years[-min(3, len(available_years)):]
                first = chart_questions[chart_questions["年度"].isin(first_years)]["テーマ（自動仮分類）"].value_counts(normalize=True)
                last = chart_questions[chart_questions["年度"].isin(last_years)]["テーマ（自動仮分類）"].value_counts(normalize=True)
                compare = pd.concat([first.rename("初期期間割合"), last.rename("直近期間割合")], axis=1).fillna(0)
                compare["変化（ポイント）"] = (compare["直近期間割合"] - compare["初期期間割合"]) * 100
                compare = compare.sort_values("変化（ポイント）", ascending=False).reset_index(names="テーマ")
                st.markdown(f"**初期：{min(first_years)}〜{max(first_years)}年 / 直近：{min(last_years)}〜{max(last_years)}年**")
                st.dataframe(compare.style.format({"初期期間割合":"{:.1%}", "直近期間割合":"{:.1%}", "変化（ポイント）":"{:+.1f}"}), use_container_width=True, hide_index=True)
                st.caption("期間内の質問構成比を比較しています。質問項目数の違いによる影響を抑えるため割合で比較。『その他・要確認』は除外。")

        st.subheader("議員別の質問件数（クリックして一覧を表示）")
        count_table = (
            questions.groupby("議員").size().rename("質問件数")
            .reset_index().sort_values("質問件数", ascending=False)
        )
        cols = st.columns(3)
        for idx, row in count_table.iterrows():
            with cols[idx % 3]:
                if st.button(
                    f"{row['議員']}　{int(row['質問件数'])}件",
                    key=f"member_count_{row['議員']}",
                    use_container_width=True,
                ):
                    st.session_state["count_selected_member"] = row["議員"]
        selected_count_member = st.session_state.get("count_selected_member")
        if selected_count_member in set(questions["議員"]):
            selected_questions = questions[questions["議員"] == selected_count_member].copy()
            st.markdown(f"**{selected_count_member} 議員の質問一覧（{len(selected_questions)}件）**")
            st.dataframe(
                selected_questions[["年度", "会議", "質問項目", "テーマ（自動仮分類）", "公式資料URL"]],
                use_container_width=True, hide_index=True,
                column_config={"公式資料URL": st.column_config.LinkColumn("公式資料", display_text="資料を開く")},
            )

        st.subheader("現職議員の質問傾向による大分類")
        classified = questions[questions["テーマ（自動仮分類）"] != "その他・要確認"]
        member_rows = []
        for member, group in questions.groupby("議員"):
            valid = group[group["テーマ（自動仮分類）"] != "その他・要確認"]
            shares = valid["テーマ（自動仮分類）"].value_counts(normalize=True)
            label, reason = broad_member_type(shares, len(valid))
            member_rows.append({"議員": member, "質問項目数": len(group), "分類済み件数": len(valid), "質問傾向タイプ": label, "判定根拠": reason})
        member_types = pd.DataFrame(member_rows)
        if not member_types.empty:
            st.plotly_chart(
                px.bar(member_types[member_types["質問傾向タイプ"].str.startswith("判定保留") == False]["質問傾向タイプ"].value_counts().rename_axis("タイプ").reset_index(name="議員数"),
                       x="タイプ", y="議員数", title="質問傾向タイプ別の議員数"),
                use_container_width=True,
            )
            st.dataframe(member_types.sort_values(["質問傾向タイプ", "議員"]), use_container_width=True, hide_index=True)
            st.caption("分類は分類済み質問が5件以上あり、最多テーマが35%以上の場合にテーマ別タイプとします。それ以外は総合・幅広型または判定保留。議員の思想・能力・実績を示すものではありません。")

    with t2:
        st.subheader("議員別の質問傾向")
        names = sorted(questions["議員"].dropna().unique())
        selected = st.selectbox("議員を選択", names)
        person = questions[questions["議員"] == selected]
        st.metric("取得できた質問項目数", len(person))
        person_valid = person[person["テーマ（自動仮分類）"] != "その他・要確認"]
        person_shares = person_valid["テーマ（自動仮分類）"].value_counts(normalize=True)
        person_type, person_reason = broad_member_type(person_shares, len(person_valid))
        st.info(f"質問傾向タイプ（参考）：**{person_type}**\n\n判定根拠：{person_reason}")
        st.dataframe(
            person[
                [
                    "年度", "会議", "テーマ（自動仮分類）",
                    "質問項目", "公式資料URL",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        person_chart = person[
            person["テーマ（自動仮分類）"] != "その他・要確認"
        ]
        ptheme = (
            person_chart["テーマ（自動仮分類）"]
            .value_counts()
            .rename_axis("テーマ")
            .reset_index(name="件数")
        )
        if not ptheme.empty:
            st.plotly_chart(
                px.pie(
                    ptheme,
                    names="テーマ",
                    values="件数",
                    title=f"{selected}さんの質問テーマ割合（その他・要確認を除く）",
                    hole=0.35,
                ),
                use_container_width=True,
            )
        else:
            st.info("この議員には円グラフに表示できる分類済みデータがありません。")

    with t3:
        st.subheader("取得した一般質問データ")
        search = st.text_input("議員名・質問内容で検索")
        view = questions.copy()
        if search:
            mask = view.astype(str).apply(
                lambda col: col.str.contains(
                    search, case=False, na=False, regex=False
                )
            ).any(axis=1)
            view = view[mask]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.download_button(
            "取得データをCSV保存",
            view.to_csv(index=False).encode("utf-8-sig"),
            "kurume_general_questions.csv",
            "text/csv",
        )

    with t4:
        st.subheader("市民への影響を整理する")
        st.write(
            "質問項目から対象者や影響人数を断定しません。"
            "対象地域・制度対象者・統計年度などの根拠を確認して整理します。"
        )
        st.dataframe(
            pd.DataFrame(
                [
                    [
                        "子育て・教育", "子ども、保護者、学校関係者",
                        "学校別児童生徒数、制度対象者数", "市統計・担当課資料",
                    ],
                    [
                        "高齢・介護・福祉", "高齢者、障害者、介護者等",
                        "年齢別人口、認定者数、利用者数", "市統計・事業実績",
                    ],
                    [
                        "道路・交通", "沿線住民、通勤通学者、道路利用者",
                        "路線・区域別人口、交通利用者数", "GIS・交通調査",
                    ],
                    [
                        "防災", "浸水想定区域・避難対象地域の住民",
                        "ハザード区域人口、避難者数",
                        "ハザードマップ・防災計画",
                    ],
                ],
                columns=[
                    "分野", "想定対象者（仮）",
                    "規模推計に必要なデータ", "確認する根拠",
                ],
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.warning(
            "対象市民数は質問項目だけから数値化しません。"
            "人口統計・制度対象者数・対象区域等との照合後に、"
            "推計根拠と対象年度を明示して算出します。"
        )

    with t5:
        st.subheader("情報発信力：公式サイト・市議会だよりのみ")
        st.write(
            "個人SNSは評価対象外です。"
            "まず公式サイトと議会だよりの公開リンクを収集します。"
        )
        if not news.empty:
            st.dataframe(news, use_container_width=True, hide_index=True)
        else:
            st.info(
                "議会だよりのリンク一覧を取得できませんでした。"
                "公式ページを直接確認してください。"
            )

        st.markdown(f"- [一般質問の質問者及び質問項目]({GENERAL_INDEX})")
        st.markdown(f"- [市議会だよりバックナンバー]({NEWS_INDEX})")
        st.markdown(f"- [会議録検索システム]({MINUTES_HOME})")
        st.caption(
            "会議録検索システムは平成19年5月以降の会議録を閲覧できます。"
            "ただし、同システムは公式記録ではない旨を表示しています。"
            "会議録原本・公式公開資料との照合が必要です。"
        )

st.divider()
st.caption(
    "分析上の注意：テーマ分類・文章指標は文字列に基づく参考情報です。"
    "質問の質、行政の対応、市民への成果を確定評価するものではありません。"
    "評価を公表する際は、発言全文・答弁・公式資料・対象者数の根拠を"
    "人が確認してください。"
)
