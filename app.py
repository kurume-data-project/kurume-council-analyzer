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
                        "答弁担当部署": "",
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
        "公式資料URL", "質問本文", "答弁本文", "答弁担当部署", "会議録URL",
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

def extract_docx_text(uploaded_file):
    """Extract paragraph and table text from a downloaded council-minutes DOCX."""
    import zipfile
    from xml.etree import ElementTree as ET

    with zipfile.ZipFile(uploaded_file) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    body = root.find("w:body", ns)
    if body is None:
        return ""
    for node in body:
        tag = node.tag.split("}")[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in node.findall(".//w:t", ns))
            if clean_text(text):
                lines.append(clean_text(text))
        elif tag == "tbl":
            for row in node.findall(".//w:tr", ns):
                cells = []
                for cell in row.findall("w:tc", ns):
                    value = " ".join(
                        clean_text("".join(t.text or "" for t in para.findall(".//w:t", ns)))
                        for para in cell.findall(".//w:p", ns)
                    )
                    if value:
                        cells.append(value)
                if cells:
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def split_minutes_speakers(text):
    """Split minutes into speaker turns. Handles common plain-text DOCX layouts.

    A turn boundary is detected only when a line looks like a speaker label;
    otherwise the line is appended to the current speaker's utterance.
    """
    speaker_patterns = [
        # ○○議員： / ○○部長： / 市長：
        re.compile(r"^(?P<speaker>(?:[○●◎◇◆□■]?[0-9０-９]+番\s*)?[^：:]{1,55}?(?:議員|君|市長|副市長|教育長|部長|局長|課長|室長|次長|参事|委員長|事務局長))\s*[：:]\s*(?P<body>.*)$"),
        # ○○（議員） or ○○（○○部長） as standalone heading
        re.compile(r"^(?P<speaker>[^：:（）()]{1,40}[（(](?:議員|市長|副市長|教育長|[^）)]{1,20}(?:部長|局長|課長|室長|次長))[）)])\s*$"),
        # ○○議員 / ○○部長 as standalone heading
        re.compile(r"^(?P<speaker>(?:[○●◎◇◆□■]?[0-9０-９]+番\s*)?[^：:]{1,45}?(?:議員|君|市長|副市長|教育長|部長|局長|課長|室長|次長|参事|委員長|事務局長))\s*$"),
    ]
    turns = []
    current_speaker = ""
    current_lines = []

    def role_for(speaker):
        label = clean_text(speaker)
        if is_council_member_speaker(label):
            return "質問・議員発言候補"
        if re.search(r"市長|副市長|教育長|部長|局長|課長|室長|次長|参事|事務局長", label):
            return "答弁側候補"
        return "未判定"

    def flush():
        body = "\n".join(current_lines).strip()
        if body:
            turns.append({
                "発言者": current_speaker or "発言者未判定",
                "区分（推定）": role_for(current_speaker),
                "発言内容": body,
            })

    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        if not line:
            continue
        found = None
        for pattern in speaker_patterns:
            found = pattern.match(line)
            if found:
                break
        if found:
            flush()
            current_speaker = clean_text(found.group("speaker"))
            body = clean_text(found.groupdict().get("body") or "")
            current_lines = [body] if body else []
        elif current_lines or current_speaker:
            current_lines.append(line)
        else:
            # Front matter (meeting title, date, page header) is not treated as speech.
            continue
    flush()
    return turns


def department_profile_from_speaker(speaker):
    """Return department/section evidence from a minutes speaker label.

    Exact section names are preferred. Parent department labels are retained as
    parent-level evidence; they are not silently converted to a specific section.
    """
    label = clean_text(speaker)
    if not label:
        return "", ""

    # Explicit section names in the speaker label.
    section_names = [
        section for sections in DUTY_RULES.values() for section in sections
        if section and section in label
    ]
    if section_names:
        section = max(section_names, key=len)
        section_theme = next(
            (theme for theme, sections in DUTY_RULES.items() if section in sections),
            "",
        )
        return section, section_theme, "発言者表記に課名あり"

    parent_map = {
        "教育委員会": ("教育委員会", "子育て・教育"),
        "教育部": ("教育部", "子育て・教育"),
        "子ども未来部": ("子ども未来部", "子育て・教育"),
        "こども未来部": ("こども未来部", "子育て・教育"),
        "健康福祉部": ("健康福祉部", "高齢・介護・福祉"),
        "福祉部": ("福祉部", "高齢・介護・福祉"),
        "環境部": ("環境部", "環境・上下水道"),
        "環境保全部": ("環境保全部", "環境・上下水道"),
        "都市建設部": ("都市建設部", "道路・交通・都市整備"),
        "都市整備部": ("都市整備部", "道路・交通・都市整備"),
        "上下水道部": ("上下水道部", "環境・上下水道"),
        "農政部": ("農政部", "産業・農業・観光"),
        "商工観光労働部": ("商工観光労働部", "産業・農業・観光"),
        "商工観光部": ("商工観光部", "産業・農業・観光"),
        "総務部": ("総務部", "行政運営・DX・財政"),
        "総合政策部": ("総合政策部", "行政運営・DX・財政"),
        "市民文化部": ("市民文化部", "文化・市民協働"),
        "協働推進部": ("協働推進部", "文化・市民協働"),
        "防災対策担当": ("防災対策担当", "防災・危機管理"),
    }
    for parent, result in parent_map.items():
        if parent in label:
            return result[0], result[1], "発言者表記に部局名あり（課までは特定できず）"
    return "", "", "発言者表記から部署を特定できず"


def infer_department_from_speaker(speaker):
    return department_profile_from_speaker(speaker)[0]


def is_council_member_speaker(speaker):
    label = clean_text(speaker)
    return bool(re.search(r"議員|君", label)) and not bool(
        re.search(r"委員会事務局|教育委員会", label)
    )


def pair_question_and_answers(turns, filename=""):
    """Create candidate pairs: member turn followed by executive turns.

    Pairing is scoped to the same imported minutes document and follows the
    speaker order. The UI labels these as candidates because some formats split
    one question/answer across multiple speakers or sections.
    """
    pairs = []
    current_question = None
    answer_parts = []

    def flush_pair():
        if current_question is None:
            return
        answer_text = "\n\n".join(
            part["発言内容"] for part in answer_parts if part.get("発言内容")
        )
        answer_speakers = list(dict.fromkeys(
            part["発言者"] for part in answer_parts if part.get("発言内容")
        ))
        profiles = [department_profile_from_speaker(name) for name in answer_speakers]
        departments = list(dict.fromkeys(name for name, _, _ in profiles if name))
        dept_themes = [theme for _, theme, _ in profiles if theme]
        department_evidence = list(dict.fromkeys(reason for name, _, reason in profiles if name))
        department = "・".join(departments)

        # When an exact section or parent department is identified, use its
        # official-duty theme as the primary classification.
        if dept_themes:
            unique_themes = list(dict.fromkeys(dept_themes))
            if len(unique_themes) == 1:
                theme = unique_themes[0]
                evidence = "答弁者の所属表記: " + department
            else:
                theme = "その他・要確認"
                evidence = "答弁者の所属分野が複数: " + " / ".join(unique_themes)
            _, keyword_evidence, question_dept = classify_theme(current_question.get("発言内容", ""))
        else:
            theme, keyword_evidence, question_dept = classify_theme(current_question.get("発言内容", ""))
            evidence = "答弁部署を特定できず。質問文による参考分類: " + keyword_evidence

        if not answer_text:
            status = "答弁候補なし"
            confidence = "低"
        elif departments and len(set(dept_themes)) == 1:
            status = "発言順・答弁者所属からの対応候補"
            confidence = "中"
        else:
            status = "発言順による対応候補（部署未確定）"
            confidence = "低"

        pairs.append({
            "ファイル名": filename,
            "質問者": current_question.get("発言者", ""),
            "質問内容": current_question.get("発言内容", ""),
            "答弁者": "・".join(answer_speakers),
            "答弁担当部署": department or "判定できず",
            "部署判定根拠": "／".join(department_evidence) if department_evidence else "部署名を発言者表記から確認できず",
            "答弁内容": answer_text,
            "テーマ（答弁者部署を優先）": theme,
            "分類根拠": evidence,
            "対応付け信頼度（参考）": confidence,
            "対応付け状況": status,
        })

    for turn in turns:
        speaker = turn.get("発言者", "")
        if is_council_member_speaker(speaker):
            flush_pair()
            current_question = turn
            answer_parts = []
        elif current_question is not None and turn.get("区分（推定）") == "答弁側候補":
            answer_parts.append(turn)
    flush_pair()
    return pairs

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


# 久留米市行政組織規則・別表第1/第4の分掌事務を読み取り、
# その事務内容に現れる語句を分類辞書として整理したもの。
# 公式規則URLは SOURCE_DUTIES_URL を参照。
SOURCE_DUTIES_URL = "https://www1.city.kurume.fukuoka.jp/reiki_int/reiki_honbun/q005RG00000062.html"

# 各課の分掌事務をもとにした「質問文→推定所管課→政策テーマ」の対応表。
# ここでの所管課は質問文からの推定であり、実際の答弁部署を示すものではない。
DUTY_RULES = {
    "子育て・教育": {
        "子ども政策課": ["子育て支援", "次世代育成", "子ども・子育て支援事業計画", "児童遊園", "学童保育", "少子化"],
        "子ども保育課": ["教育・保育の認定", "保育施設", "保育所", "幼稚園", "認定こども園", "保育士", "保育料", "入所"],
        "家庭子ども相談課": ["家庭児童相談", "ひとり親", "児童手当", "児童扶養手当", "母子生活支援"],
        "こども子育てサポートセンター": ["母子保健", "妊産婦", "乳幼児", "子育て相談", "児童センター"],
        "青少年育成課": ["青少年", "若者相談", "非行", "薬物乱用"],
        "学校教育課": ["学校教育", "学習指導", "教職員", "不登校", "いじめ", "特別支援教育", "学校給食", "授業", "学級", "教科", "教育課程"],
        "学校施設課": ["学校施設", "校舎", "体育館", "通学路"],
    },
    "高齢・介護・福祉": {
        "長寿支援課": ["高齢者福祉", "老人福祉", "地域包括支援", "高齢者相談", "認知症", "介護予防"],
        "介護保険課": ["介護保険", "介護サービス", "要介護", "要支援", "介護認定", "介護保険料"],
        "障害者福祉課": ["障害者福祉", "障がい者", "障害福祉サービス", "障害者手帳", "自立支援医療"],
        "生活支援第1課": ["生活保護", "生活援護", "保護費", "社会援護"],
        "生活支援第2課": ["生活困窮者", "生活困窮", "自立相談", "生活相談"],
        "地域福祉課": ["地域福祉", "民生委員", "社会福祉", "福祉活動"],
        "健康推進課": ["健康づくり", "健康診査", "特定健康診査", "保健指導", "健康相談", "在宅医療", "栄養指導", "歯科保健"],
        "地域保健課": ["地域保健", "保健センター", "感染症", "予防接種"],
        "医療・年金課": ["国民健康保険", "後期高齢者医療", "国民年金", "医療費助成"],
    },
    "防災・危機管理": {
        "防災対策課": ["防災", "災害対策", "避難", "避難所", "国民保護", "消防団", "危機管理", "地域防災計画", "備蓄"],
        "安全安心推進課": ["交通安全", "防犯", "犯罪", "暴力団", "安全安心", "消費生活"],
        "河川課": ["浸水", "内水", "水害", "河川整備", "排水機", "樋門", "流域治水"],
    },
    "道路・交通・都市整備": {
        "都市計画課": ["都市計画", "土地利用", "土地区画整理", "景観", "開発許可", "都市施設"],
        "交通政策課": ["公共交通", "公共輸送", "バス", "鉄道", "自転車利用", "駐車場", "放置自転車"],
        "国県事業調整課": ["国県道", "幹線道路", "国県河川", "治水"],
        "まちなか整備課": ["中心市街地", "まちなか整備", "市街地再整備"],
        "住宅政策課": ["住宅政策", "空き家", "空家", "マンション", "賃貸住宅", "住宅セーフティネット"],
        "市営住宅課": ["市営住宅", "市営団地", "市営住宅家賃"],
        "公園緑化推進課": ["公園", "緑地", "緑化", "街路樹"],
        "路政課": ["道路占用", "道路境界", "道路台帳", "私道"],
        "道路整備課": ["道路整備", "道路改良", "橋りょう", "橋梁", "歩道", "街路事業", "舗装", "舗装補修", "路面", "道路補修"],
        "河川課": ["河川", "水路", "排水", "浸水被害", "治水"],
        "建築指導課": ["建築基準", "建築確認", "耐震", "既存建築物", "建築物防災"],
    },
    "産業・農業・観光": {
        "農政課": ["農業政策", "農地", "農業振興", "認定農業者", "新規就農", "担い手", "耕作放棄地", "集落営農"],
        "農業の魅力促進課": ["地産地消", "農産物", "農業ブランド", "道の駅", "食育", "農業公園", "緑花木"],
        "生産流通課": ["農産物生産", "農産物流通", "園芸", "畜産", "農業用施設"],
        "商工政策課": ["商工業", "中小企業", "商店街", "創業", "企業支援", "雇用", "労働"],
        "観光・国際課": ["観光", "観光振興", "コンベンション", "国際交流", "インバウンド"],
        "企業誘致推進課": ["企業誘致", "工場立地", "企業立地", "産業団地"],
    },
    "行政運営・DX・財政": {
        "総合政策課": ["総合政策", "総合計画", "重要施策", "政策調整", "広域行政"],
        "財政課": ["予算編成", "財政計画", "市債", "地方交付税", "基金", "財政"],
        "広報戦略課": ["広報", "市政広報", "情報発信", "シティプロモーション"],
        "情報政策課": ["情報政策", "情報処理システム", "情報セキュリティ", "デジタル", "DX", "電子申請"],
        "行財政改革推進課": ["行財政改革", "行政改革", "業務改善", "民間委託", "指定管理"],
        "人事厚生課": ["職員人事", "職員採用", "定員管理", "職員厚生"],
        "契約課": ["契約", "入札", "公共調達"],
        "法制室": ["条例", "規則", "法令", "情報公開", "個人情報保護", "行政不服審査"],
        "市民税課": ["市民税", "個人住民税", "法人市民税", "軽自動車税"],
        "資産税課": ["固定資産税", "都市計画税", "土地評価", "家屋評価"],
        "税収納推進課": ["市税収納", "滞納", "徴収", "納税"],
        "総務課": ["市議会", "行政一般", "ふるさと納税", "統計", "市勢要覧"],
    },
    "環境・上下水道": {
        "環境政策課": ["環境政策", "環境美化", "地球温暖化", "温暖化対策", "環境衛生"],
        "廃棄物指導課": ["廃棄物処理施設", "産業廃棄物", "不法投棄", "建設廃棄物", "廃棄物処理業"],
        "環境保全課": ["公害", "自然環境", "防疫", "害虫", "そ族", "騒音", "大気汚染"],
        "資源循環推進課": ["ごみ減量", "ごみ", "リサイクル", "一般廃棄物", "ごみ収集", "資源循環"],
        "上下水道部": ["水道", "下水道", "給水", "排水設備", "浄水", "水質", "漏水"],
    },
    "文化・市民協働": {
        "協働推進課": ["市民協働", "市民活動", "NPO", "ボランティア"],
        "地域コミュニティ課": ["自治会", "地域コミュニティ", "校区", "まちづくり"],
        "人権・同和対策課": ["人権", "同和", "差別", "男女平等", "ジェンダー"],
        "文化振興課": ["文化芸術", "文化振興", "文化施設", "シティプラザ"],
        "生涯学習推進課": ["生涯学習", "社会教育", "図書館", "公民館"],
        "スポーツ政策課": ["スポーツ", "体育施設", "競技", "運動施設"],
    },
}


def classify_theme(text, department=""):
    """Match question wording against keywords derived from Kurume City's official duties."""
    value = clean_text(text)
    dept = clean_text(department)
    theme_scores = {}
    theme_evidence = {}
    dept_scores = {}

    for theme, departments in DUTY_RULES.items():
        for dept_name, keywords in departments.items():
            hits = [word for word in keywords if word and word in value]
            if hits:
                # Each distinct duty keyword contributes one point; longer phrases are retained as evidence.
                theme_scores[theme] = theme_scores.get(theme, 0) + len(hits)
                theme_evidence.setdefault(theme, []).extend(hits)
                dept_scores[dept_name] = dept_scores.get(dept_name, 0) + len(hits)

    # If an actual answer department is later obtained from the minutes, it is used as corroborating evidence.
    if dept:
        for theme, departments in DUTY_RULES.items():
            for dept_name in departments:
                if dept_name in dept:
                    theme_scores[theme] = theme_scores.get(theme, 0) + 3
                    theme_evidence.setdefault(theme, []).append(f"答弁部署:{dept_name}")
                    dept_scores[dept_name] = dept_scores.get(dept_name, 0) + 3

    if not theme_scores:
        return "その他・要確認", "所掌事務に対応する語句なし", ""

    ordered = sorted(theme_scores.items(), key=lambda item: item[1], reverse=True)
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        tied_themes = [name for name, score in ordered if score == ordered[0][1]]
        return "その他・要確認", "複数の所掌分野が同点: " + " / ".join(tied_themes), ""

    theme = ordered[0][0]
    top_departments = [name for name, score in dept_scores.items() if score == max(dept_scores.values())]
    estimated_dept = "・".join(top_departments[:2])
    evidence = "、".join(dict.fromkeys(theme_evidence.get(theme, [])))
    return theme, f"所掌事務キーワード: {evidence[:180]}", estimated_dept


def theme_of(text):
    return classify_theme(text)[0]


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
    st.caption("分類基準：久留米市行政組織規則・分掌事務")
    st.markdown(f"[公式の行政組織規則を確認]({SOURCE_DUTIES_URL})")
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
    # 質問項目・親質問項目・質問詳細をまとめて分類に使う。
    # 答弁担当部署は現行の一般質問表から取得できないため、列が空欄なら質問文のみで判定。
    classification_text = (
        questions["質問項目"].fillna("") + " "
        + questions["親質問項目"].fillna("") + " "
        + questions["質問詳細"].fillna("")
    )
    classification = [
        classify_theme(text, dept)
        for text, dept in zip(classification_text, questions["答弁担当部署"].fillna(""))
    ]
    questions["テーマ（自動仮分類）"] = [item[0] for item in classification]
    questions["分類根拠"] = [item[1] for item in classification]
    questions["推定所管課（質問文から）"] = [item[2] for item in classification]
    indicator_frame = questions["質問項目"].apply(text_clarity_indicators).apply(pd.Series)
    questions = pd.concat([questions, indicator_frame], axis=1)

    st.success(
        f"現職議員の質問項目を {len(questions):,} 件取得しました"
        f"（対象ページ {page_count:,} 件）。"
    )
    st.info(
        "分類は久留米市行政組織規則の各課の分掌事務に記載された業務内容を基準にしています。"
        "質問文から所管課を推定し、対応する政策テーマに集約します。"
        "『推定所管課』は質問文からの推定であり、実際に答弁した部署ではありません。"
        "複数分野が同点、または対応語句がない場合は『その他・要確認』に残します。"
    )
    if fetch_errors:
        with st.expander(f"取得時の注意（{len(fetch_errors)}件）"):
            for item in fetch_errors:
                st.write(f"- {item}")

    t1, t2, t3, t4, t5, t6, t7 = st.tabs(
        [
            "全体ダッシュボード",
            "議員別分析",
            "質問データ",
            "市民への影響",
            "情報発信・出典",
            "質問・答弁を探す",
            "会議録をまとめて読み込む",
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
                selected_questions[["年度", "会議", "質問項目", "テーマ（自動仮分類）", "推定所管課（質問文から）", "分類根拠", "答弁担当部署", "公式資料URL"]],
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
                    "質問項目", "推定所管課（質問文から）", "分類根拠", "答弁担当部署", "公式資料URL",
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
        st.caption("『推定所管課（質問文から）』は所掌事務キーワードによる推定です。実際の答弁部署とは異なる場合があります。『答弁担当部署』は会議録からの取得機能が未実装のため、現在は空欄です。")
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

    with t6:
        st.subheader("質問に対する答弁を探す")
        st.write(
            "現任期の一般質問を選び、久留米市議会の会議録検索システムで "
            "質問と答弁の記録を確認します。会議録検索システムの公開対象は平成19年5月以降です。"
        )
        st.warning(
            "現在の検索システムは検索フォームとWord形式ダウンロードを提供していますが、 "
            "外部システムの検索結果をアプリが自動取得できる仕様かは確認できていません。 "
            "そのため、この初版では検索語を準備して公式検索画面を開く方式です。"
        )
        if questions.empty:
            st.info("質問データがないため、質問を選択できません。")
        else:
            member_options = sorted(questions["議員"].dropna().unique().tolist())
            chosen_member = st.selectbox("質問した議員", member_options, key="answer_search_member")
            member_questions = questions[questions["議員"] == chosen_member].copy()
            member_questions["選択表示"] = member_questions.apply(
                lambda r: f"{r['年度']}年・{r['会議']}・{clean_text(r['質問項目'])[:70]}", axis=1
            )
            selected_label = st.selectbox(
                "答弁を探したい質問", member_questions["選択表示"].tolist(),
                key="answer_search_question",
            )
            selected_row = member_questions[member_questions["選択表示"] == selected_label].iloc[0]
            st.markdown("**選択した質問項目**")
            st.write(clean_text(selected_row["質問項目"]))
            if clean_text(selected_row.get("質問詳細", "")):
                st.caption(clean_text(selected_row["質問詳細"]))
            st.caption(f"会議：{selected_row['会議']} ／ 質問者：{selected_row['議員']}")

            suggested_terms = [
                clean_text(selected_row.get("質問項目", "")),
                clean_text(selected_row.get("親質問項目", "")),
            ]
            suggested_terms = [x for x in suggested_terms if x]
            default_terms = " ".join(dict.fromkeys(suggested_terms))[:100]
            search_terms = st.text_input(
                "会議録検索に使う語句（必要に応じて短くしてください）",
                value=default_terms,
                key="answer_search_terms",
            )
            st.text_input(
                "発言者（質問した議員）", value=chosen_member,
                help="会議録検索画面の『発言者』欄に入力してください。",
                key="answer_search_speaker",
            )
            st.markdown(
                "**検索の手順**\n"
                "1. 下のボタンで会議録検索画面を開く\n"
                "2. 検索語を入力し、会議期間を質問の年度・会議に合わせる\n"
                "3. 発言者を指定し、必要に応じて答弁者も選択する\n"
                "4. 検索結果から該当する質問・答弁を開き、Word形式で保存する"
            )
            st.code(search_terms or "質問項目から検索語を入力してください", language=None)
            st.link_button(
                "久留米市議会 会議録検索を開く",
                "https://www.kensakusystem.jp/kurume/cgi-bin3/Search2.exe?Code=k0yboqg35vrfmhbt6j&sTarget=2",
                use_container_width=True,
            )
            st.link_button(
                "会議録検索システムの操作説明",
                "https://www.kensakusystem.jp/kurume/help/help_top.html",
                use_container_width=True,
            )
            st.caption(
                "検索結果の発言者・会議名・発言順を照合してから、質問と答弁を対応付けてください。"
                "同じキーワードが含まれるだけでは、同じ質問への答弁とは限りません。"
            )



    with t7:
        st.subheader("会議録をまとめて読み込む")
        st.write(
            "会議録検索システムからWord形式（.docx）で保存したファイルを、"
            "複数まとめて選択できます。本文を抽出し、発言者ごとの発言ブロックに整理します。"
        )
        st.link_button("久留米市議会 会議録検索システムを開く", MINUTES_HOME, use_container_width=True)
        st.caption(
            "公式ヘルプで確認できるのは、検索・閲覧と表示中会議録のWordダウンロードです。"
            "全期間一括取得や公開APIは確認できていないため、この機能は複数のWordファイルを"
            "一度に取り込む方式です。"
        )
        uploaded_minutes = st.file_uploader(
            "会議録のWordファイル（複数選択可）",
            type=["docx"], accept_multiple_files=True, key="minutes_docx_upload",
        )
        if uploaded_minutes:
            extracted_records = []
            paired_records = []
            for file in uploaded_minutes:
                try:
                    full_text = extract_docx_text(file)
                    turns = split_minutes_speakers(full_text)
                    if not turns:
                        turns = [{"発言者": "発言者未判定", "区分（推定）": "未判定", "発言内容": full_text}] if full_text else []
                    for turn_no, turn in enumerate(turns, start=1):
                        extracted_records.append({
                            "ファイル名": file.name, "発言順": turn_no,
                            "発言者": turn["発言者"], "区分（推定）": turn["区分（推定）"],
                            "答弁担当部署（発言者表記から推定）": infer_department_from_speaker(turn["発言者"]),
                            "発言内容": turn["発言内容"],
                        })
                    paired_records.extend(pair_question_and_answers(turns, file.name))
                except Exception as exc:
                    st.error(f"{file.name} を読み込めませんでした：{type(exc).__name__}: {exc}")
            if extracted_records:
                minutes_df = pd.DataFrame(extracted_records)
                st.success(f"{len(uploaded_minutes)}ファイルから {len(minutes_df)} 発言ブロックを抽出しました。")
                st.subheader("質問と答弁の対応候補")
                if paired_records:
                    paired_df = pd.DataFrame(paired_records)
                    st.info(
                        "議員の発言の後に続く答弁側発言を、発言順で対応付けています。"
                        "答弁者の役職・部署名から担当部署を推定し、その部署を優先してテーマを判定します。"
                    )
                    st.dataframe(
                        paired_df[["ファイル名", "質問者", "質問内容", "答弁者", "答弁担当部署", "テーマ（答弁者部署を優先）", "対応付け信頼度（参考）", "対応付け状況"]],
                        use_container_width=True, hide_index=True,
                    )
                    with st.expander("答弁本文・分類根拠を確認"):
                        for idx, row in paired_df.iterrows():
                            st.markdown(f"**{idx + 1}. {row['質問者']}／{row['テーマ（答弁者部署を優先）']}**")
                            st.write("質問：", row["質問内容"])
                            st.write("答弁者：", row["答弁者"] or "未検出")
                            st.write("担当部署：", row["答弁担当部署"])
                            st.write("答弁：", row["答弁内容"] or "答弁候補を検出できませんでした")
                            st.caption("部署判定根拠：" + row["部署判定根拠"] + " ／ 分類根拠：" + row["分類根拠"] + " ／ 対応付け：" + row["対応付け状況"])
                    st.download_button(
                        "質問・答弁の対応候補をCSV保存",
                        data=paired_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name="kurume_question_answer_pairs.csv",
                        mime="text/csv", use_container_width=True,
                    )
                    st.session_state["minutes_question_answer_pairs"] = paired_df
                else:
                    st.warning("質問者・答弁者の発言区分を判定できませんでした。Wordの発言者表記を確認してください。")

                with st.expander("発言ブロック一覧（確認用）"):
                    speaker_filter = st.selectbox(
                        "表示する区分", ["すべて", "質問・発言側候補", "答弁側候補", "未判定"],
                        key="minutes_speaker_filter",
                    )
                    view_df = minutes_df if speaker_filter == "すべて" else minutes_df[minutes_df["区分（推定）"] == speaker_filter]
                    st.dataframe(view_df, use_container_width=True, hide_index=True)
                    st.download_button(
                        "抽出した発言一覧をCSVで保存",
                        data=minutes_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name="kurume_minutes_speeches.csv",
                        mime="text/csv", use_container_width=True,
                    )
                st.session_state["minutes_imported_records"] = minutes_df

st.divider()
st.caption(
    "分析上の注意：テーマ分類は久留米市行政組織規則の分掌事務を基準にしたキーワード照合による参考情報です。"
    "質問の質、行政の対応、市民への成果を確定評価するものではありません。"
    "評価を公表する際は、発言全文・答弁・公式資料・対象者数の根拠を"
    "人が確認してください。"
)
