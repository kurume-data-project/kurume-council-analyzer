
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path

st.set_page_config(page_title="久留米市議会・議員活動分析", page_icon="🏛️", layout="wide")

st.title("🏛️ 久留米市議会・議員活動分析")
st.caption("2018年以降の一般質問を中心に、質問の特徴と市民への影響を整理する分析ツール")

st.info(
    "この初期版は分析基盤です。実際の議事録を取り込むまでは、議員別の点数・順位は表示しません。"
    "AIや人による評価は、根拠となる発言・会議日・出典を確認できる形で運用してください。"
)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
TEMPLATE = DATA_DIR / "questions_template.csv"

COLUMNS = [
    "meeting_date", "meeting_name", "member_name", "question_title",
    "policy_area", "question_text", "answer_text", "source_url",
    "target_group", "target_population", "population_basis",
    "issue_clarity", "evidence", "proposal", "specificity", "follow_up",
    "outcome_status", "review_note"
]

if not TEMPLATE.exists():
    pd.DataFrame(columns=COLUMNS).to_csv(TEMPLATE, index=False, encoding="utf-8-sig")

st.sidebar.header("データ")
uploaded = st.sidebar.file_uploader("質問データCSVを読み込む", type=["csv"])
if uploaded:
    try:
        df = pd.read_csv(uploaded)
        st.sidebar.success(f"{len(df)}件を読み込みました")
    except Exception as e:
        st.sidebar.error(f"CSVを読み込めません: {e}")
        df = pd.DataFrame(columns=COLUMNS)
else:
    local = DATA_DIR / "questions.csv"
    if local.exists():
        df = pd.read_csv(local)
    else:
        df = pd.DataFrame(columns=COLUMNS)

for c in COLUMNS:
    if c not in df.columns:
        df[c] = pd.NA

if not df.empty:
    df["meeting_date"] = pd.to_datetime(df["meeting_date"], errors="coerce")
    df["year"] = df["meeting_date"].dt.year
    df["member_name"] = df["member_name"].fillna("未確認")
    df["policy_area"] = df["policy_area"].fillna("未分類")
    df["question_title"] = df["question_title"].fillna("（質問項目未入力）")

    st.sidebar.subheader("絞り込み")
    years = sorted([int(x) for x in df["year"].dropna().unique()])
    selected_years = st.sidebar.multiselect("開催年", years, default=years)
    members = sorted(df["member_name"].dropna().unique().tolist())
    selected_members = st.sidebar.multiselect("議員", members, default=members)
    areas = sorted(df["policy_area"].dropna().unique().tolist())
    selected_areas = st.sidebar.multiselect("政策分野", areas, default=areas)
    filtered = df[
        df["year"].isin(selected_years)
        & df["member_name"].isin(selected_members)
        & df["policy_area"].isin(selected_areas)
    ].copy()
else:
    filtered = df.copy()

tab1, tab2, tab3, tab4 = st.tabs(["全体ダッシュボード", "議員別分析", "質問データ", "評価方法"])

with tab1:
    st.subheader("全体ダッシュボード")
    if filtered.empty:
        st.warning("まだ分析データがありません。公式資料から質問・答弁を整理したCSVを読み込んでください。")
        st.markdown("**最初の作業**：一般質問の質問者・質問項目を取得し、会議録本文と対応づけます。")
    else:
        a, b, c, d = st.columns(4)
        a.metric("質問項目数", f"{len(filtered):,}")
        b.metric("議員数", filtered["member_name"].nunique())
        c.metric("政策分野数", filtered["policy_area"].nunique())
        d.metric("対象人口入力済", f"{filtered['target_population'].notna().sum():,}件")
        area_counts = filtered["policy_area"].value_counts().rename_axis("政策分野").reset_index(name="質問項目数")
        st.plotly_chart(px.bar(area_counts, x="政策分野", y="質問項目数", title="政策分野別の質問項目数"), use_container_width=True)
        yearly = filtered.dropna(subset=["year"]).groupby("year").size().reset_index(name="質問項目数")
        if not yearly.empty:
            st.plotly_chart(px.line(yearly, x="year", y="質問項目数", markers=True, title="年別の質問項目数"), use_container_width=True)

with tab2:
    st.subheader("議員別分析")
    if filtered.empty:
        st.caption("データを読み込むと、議員別の質問傾向・評価を表示します。")
    else:
        names = sorted(filtered["member_name"].dropna().unique())
        chosen = st.selectbox("分析する議員", names)
        one = filtered[filtered["member_name"] == chosen].copy()
        st.metric("質問項目数", len(one))
        st.write("**政策分野の内訳**")
        counts = one["policy_area"].value_counts().rename_axis("政策分野").reset_index(name="件数")
        st.plotly_chart(px.pie(counts, names="政策分野", values="件数"), use_container_width=True)
        st.write("**質問一覧**")
        show_cols = ["meeting_date", "meeting_name", "question_title", "policy_area", "target_group", "source_url"]
        st.dataframe(one[show_cols], use_container_width=True, hide_index=True)
        score_cols = ["issue_clarity", "evidence", "proposal", "specificity", "follow_up"]
        if one[score_cols].notna().any().any():
            st.caption("評価点は各項目0～20点。未評価は空欄のまま扱います。")
            score = one[score_cols].apply(pd.to_numeric, errors="coerce")
            st.dataframe(score.rename(columns={
                "issue_clarity":"課題の明確性", "evidence":"データ・根拠",
                "proposal":"政策提案力", "specificity":"具体性", "follow_up":"継続・追跡性"
            }), use_container_width=True)

with tab3:
    st.subheader("質問データ")
    if filtered.empty:
        st.write("CSVを読み込むと、質問データを確認できます。")
    else:
        st.dataframe(filtered, use_container_width=True, hide_index=True)
    st.download_button(
        "入力用CSVテンプレートをダウンロード",
        data=pd.DataFrame(columns=COLUMNS).to_csv(index=False).encode("utf-8-sig"),
        file_name="questions_template.csv",
        mime="text/csv"
    )

with tab4:
    st.subheader("評価方法と注意事項")
    st.markdown("""
### 質問力（100点満点・試行基準）
| 評価軸 | 配点 | 確認する内容 |
|---|---:|---|
| 課題の明確性 | 20 | 誰のどの課題かが明確か |
| データ・根拠 | 20 | 統計、調査、制度、事実に基づくか |
| 政策提案力 | 20 | 実行可能な選択肢・改善案があるか |
| 具体性 | 20 | 対象、実施主体、時期、費用等が具体的か |
| 継続・追跡性 | 20 | 過去答弁や進捗を確認しているか |

### 市民への貢献度は別軸で評価
- 対象規模：対象となる市民・世帯・事業者の推計人数
- 影響の深さ：生活・安全・権利・負担への影響
- 公平性：支援が届きにくい層や地域への配慮
- 政策の進展：答弁、制度変更、予算化、実施状況の確認

**重要な原則**
1. 対象人口の多さだけで重要度を決めない。
2. 質問したことと、政策が実現したことを混同しない。
3. 答弁・成果を確認できない場合は「不明」とする。
4. AI評価は補助的な参考値とし、根拠の引用と人による確認を必須にする。
5. 現在の議員名簿を過去8年間に遡って当てはめず、当時の在職期間・会派を管理する。
6. 会議録検索システムは市の案内上「公式の記録ではない」と注記されているため、公式の一般質問表等と照合する。
""")
    st.markdown("### データ出典")
    st.markdown("""
- [久留米市議会・一般質問の質問者及び質問項目](https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/3030hongikai/4001shitumon/index.html)
- [久留米市議会・会議録検索システム](https://www.kensakusystem.jp/kurume/)
- [久留米市議会・議員名簿](https://www.city.kurume.fukuoka.jp/1100keikaku/2040shigikai/3020meibo/index.html)
- [久留米市の統計](https://www.city.kurume.fukuoka.jp/1100keikaku/2010shoukai/3040toukei/)
""")

st.divider()
st.caption("試作版 v0.1｜公開前に、議事録・統計の利用条件、引用方法、データの正確性を確認してください。")
