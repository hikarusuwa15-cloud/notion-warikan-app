import streamlit as st
import notion_client
import pandas as pd
from collections import defaultdict

# --- 債務最適化アルゴリズム ---
def simplify_debts(balances):
    """
    貸し借りの残高辞書を受け取り、最小の送金リストを返す。
    例: {'A': 500, 'B': -200, 'C': -300} -> ["B pays A 200", "C pays A 300"]
    """
    # 残高をプラス（債権者）とマイナス（債務者）に分割
    creditors = {person: amount for person, amount in balances.items() if amount > 0}
    debtors = {person: amount for person, amount in balances.items() if amount < 0}

    transactions = []

    # sortedを使うと、常に同じ結果（例：B->A, C->A）になり、
    # 実行のたびに（C->A, B->A）と順番が変わるのを防げる
    sorted_debtors = sorted(debtors.items(), key=lambda item: item[1]) # 負の値が小さい順
    sorted_creditors = sorted(creditors.items(), key=lambda item: item[1], reverse=True) # 正の値が大きい順

    # リストのインデックスとして使用
    d_idx = 0
    c_idx = 0

    while d_idx < len(sorted_debtors) and c_idx < len(sorted_creditors):
        debtor_name, debtor_amount = sorted_debtors[d_idx]
        creditor_name, creditor_amount = sorted_creditors[c_idx]

        # 支払うべき額（債務）と受け取るべき額（債権）
        # debtor_amountは負の値なので-1をかける
        payment = min(-debtor_amount, creditor_amount)

        # 0.01円未満の取引は無視する（浮動小数点誤差対策）
        if payment < 0.01:
            d_idx += 1 # 債務者の残高がほぼ0なので次へ
            continue

        transactions.append(f"**{debtor_name}** さんは **{creditor_name}** さんに **{payment:,.0f}円** 支払う")

        # 残高を更新
        new_debtor_amount = debtor_amount + payment
        new_creditor_amount = creditor_amount - payment

        # 更新した残高をリストに書き戻す
        sorted_debtors[d_idx] = (debtor_name, new_debtor_amount)
        sorted_creditors[c_idx] = (creditor_name, new_creditor_amount)

        # どちらかの残高が0になったら、次の人へ
        # 誤差を考慮して 0 ではなく -0.01 と比較
        if new_debtor_amount > -0.01:
            d_idx += 1
        if new_creditor_amount < 0.01:
            c_idx += 1

    return transactions

# --- Notion APIからデータを取得 ---
def fetch_notion_data(api_key, database_id):
    notion = notion_client.Client(auth=api_key)
    results = []
    has_more = True
    next_cursor = None

    while has_more:
        response = notion.databases.query(
            database_id=database_id,
            start_cursor=next_cursor,
            page_size=100, # 100件ずつ取得
        )
        results.extend(response.get("results"))
        has_more = response.get("has_more")
        next_cursor = response.get("next_cursor")
        
    return results

# --- メインのStreamlitアプリ ---
def main():
    st.set_page_config(layout="wide")
    st.title("旅行費用 割り勘精算アプリ 💰")

    # --- 警告：プロパティ設定について ---
    st.info(
        """
        このアプリが正しく動作するには、Notionデータベースに以下のプロパティが必要です。
        1.  `金額` (数値プロパティ)
        2.  `払った人` (マルチセレクト プロパティ)
        3.  `誰の分` (マルチセレクト プロパティ)
        """
    )

    # --- ！！最重要：運用ルールの警告！！ ---
    st.warning(
        """
        ⚠️ **運用の注意点** ⚠️
        
        **「払った人」は必ず1人だけ選んでください！**
        
        このプロパティは「マルチセレクト」ですが、計算ロジック上、
        2人以上（例：AさんとBさんが割り勘で）払った場合に正しく計算できません。
        もし2人で出し合った場合は、面倒でも2行に分けて入力してください。
        
        * (良い例) 行1: 金額 5000, 払った人 [ A ], 誰の分 [ A, B, C ]
        * (良い例) 行2: 金額 5000, 払った人 [ B ], 誰の分 [ A, B, C ]
        * (悪い例) 行1: 金額 10000, 払った人 [ A, B ], 誰の分 [ A, B, C ]
        """
    )
    
    if st.button("📊 精算結果を計算する", type="primary"):
        try:
            # Streamlit CloudのSecretsからキーとIDを取得
            api_key = st.secrets["NOTION_API_KEY"]
            database_id = st.secrets["NOTION_DATABASE_ID"]
        except FileNotFoundError:
            st.error("シークレットが設定されていません。ローカルでテストする場合は、`secrets.toml`を作成してください。")
            return
        except KeyError:
            st.error("`NOTION_API_KEY` または `NOTION_DATABASE_ID` がStreamlitのSecretsに設定されていません。")
            return

        with st.spinner("Notionデータベースから支出データを取得中..."):
            try:
                data = fetch_notion_data(api_key, database_id)
            except Exception as e:
                st.error(f"Notion APIへの接続に失敗しました: {e}")
                return

        with st.spinner("精算金額を計算中..."):
            balances = defaultdict(float) # 全員の残高（プラスが貸し、マイナスが借り）
            processed_items = [] # デバッグ用のテーブルデータ
            
            for item in data:
                try:
                    props = item.get("properties", {})
                    
                    # 必須プロパティの存在チェック
                    if "金額" not in props or "払った人" not in props or "誰の分" not in props:
                        continue # 必要な情報がない行はスキップ
                        
                    amount = props["金額"].get("number")
                    payers = props["払った人"].get("multi_select", [])
                    sharers = props["誰の分"].get("multi_select", [])

                    # データが不完全な場合はスキップ
                    if amount is None or amount == 0 or not payers or not sharers:
                        continue
                        
                    # 警告した通り、「払った人」は1人目のみを正とする
                    payer_name = payers[0]["name"]
                    
                    # 割り勘対象者
                    sharer_names = [s["name"] for s in sharers]
                    share_count = len(sharer_names)
                    
                    # 1人あたりの金額（小数点以下2桁で丸める）
                    per_person_amount = round(amount / share_count, 2)

                    # 払った人の残高を増やす
                    balances[payer_name] += amount
                    
                    # 割り勘対象者の残高を減らす
                    for name in sharer_names:
                        balances[name] -= per_person_amount
                        
                    processed_items.append({
                        "支出名": props.get("費用の種類", {}).get("title", [{}])[0].get("plain_text", "（名称未設定）"),
                        "金額": amount,
                        "払った人": payer_name,
                        "対象者": ", ".join(sharer_names),
                        "1人あたり": per_person_amount
                    })

                except Exception as e:
                    st.warning(f"一部のデータの処理に失敗しました: {e}。該当行：{item.get('id')}")

            if not balances:
                st.error("計算対象のデータが見つかりませんでした。データベースの入力内容を確認してください。")
                return

            # 債務最適化を実行
            transactions = simplify_debts(balances)
            
            st.success("🎉 計算が完了しました！")
            
            # --- 結果の表示 ---
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("最小の送金リスト")
                if not transactions:
                    st.success("全員の精算は完了しています！")
                else:
                    for t in transactions:
                        st.markdown(f"### 💸 {t}")
            
            with col2:
                st.subheader("最終残高（貸し借り）")
                st.info("プラスは受け取る金額、マイナスは支払う金額です。")
                balance_df = pd.DataFrame.from_dict(balances, orient='index', columns=['金額（円）'])
                balance_df = balance_df.sort_values(by='金額（円）', ascending=False)
                st.dataframe(balance_df.style.format("{:,.0f}円").applymap(
                    lambda v: 'color: green' if v > 0 else ('color: red' if v < 0 else 'color: white')
                ))
            
            # デバッグ用
            with st.expander("処理された支出データ一覧を表示"):
                st.dataframe(pd.DataFrame(processed_items))

if __name__ == "__main__":
    main()