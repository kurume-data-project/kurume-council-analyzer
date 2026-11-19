
# 久留米市議会・議員活動分析（試作 v0.1）

## 起動
```bash
pip install -r requirements.txt
streamlit run app.py
```

## データ投入
`data/questions_template.csv` を入力用テンプレートとして利用してください。
列名・評価項目はアプリ内の「評価方法」タブを参照。

- meeting_date: YYYY-MM-DD
- meeting_name: 例「令和○年第○回定例会」
- member_name: 議員名
- question_title: 質問項目
- policy_area: 教育・子育て、福祉・医療、防災、交通、産業、行政DX、財政、環境、その他など
- question_text / answer_text: 議事録の該当箇所
- source_url: 元資料のURL
- target_group: 対象者・対象地域
- target_population: 推計対象人数（根拠がある場合のみ）
- population_basis: 推計根拠・統計年度
- issue_clarity / evidence / proposal / specificity / follow_up: 各0～20点。根拠を確認して入力

## データ収集の原則
1. 2018-11-20以降から最新公開分までを対象とする。
2. 質問項目一覧と会議録本文を紐づける。
3. 議員の在職期間・会派は年度ごとに管理し、現行名簿を過去に遡及適用しない。
4. 公式資料と会議録検索システムの記載を照合する。
5. 対象人口は母集団の定義、統計年度、推計方法を記録する。
6. AIによる採点は参考値とし、原文引用・出典URL・人の確認を残す。
7. 「質問の質」「市民への影響」「政策実現・成果」は別々に扱う。

## 注意
この初期版はCSVを読み込んで可視化するための基盤です。公式議事録の自動取得、AI自動採点、政策実現の因果評価はまだ実装していません。
