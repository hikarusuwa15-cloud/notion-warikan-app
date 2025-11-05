import streamlit as st
import notion_client
import pandas as pd
from collections import defaultdict

# --- 債務最適化アルゴリズム ---
def simplify_debts(balances):
    """
    貸し借りの残高辞書を受け取り、最小の送金リストを返す。
    """
    # 残高をプラス（債権者）とマイナス（債務者）に分割
    creditors = {person: amount for person, amount in balances.items() if amount > 0}
    debtors = {person: amount for person, amount in balances.items() if amount < 0}

    transactions = []

    sorted_debtors = sorted(debtors.items(), key=lambda item: item[1])
    sorted_creditors = sorted(creditors.items(), key=lambda item: item[1], reverse=True)

    d_idx = 0
    c_idx = 0

    while d_idx < len(sorted_debtors) and c_idx < len(sorted_creditors):
        debtor_name, debtor_amount = sorted_debtors[d_idx]
        creditor_name, creditor_amount = sorted_creditors[c_idx]

        payment = min(-debtor_amount, creditor_amount)

        if payment < 0.01:
            d_idx += 1
            continue

        transactions.append(f"**{debtor_name}** さんは **{creditor_name}** さんに **{payment:,.0f}円** 支払う")

        new_debtor_amount = debtor_amount + payment
        new_creditor_amount = creditor_amount - payment

        sorted_debtors[d_idx] = (debtor_name, new_debtor_amount)
        sorted_creditors[c_idx] = (creditor_name, new_creditor_amount)

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
            page_size=100,
        )
        results.extend(response.get("results"))
        has_more = response.get("has_more")
        next_cursor = response.get("next_cursor")
        
    return results

# --- メインのStreamlitアプリ ---
def main():
    st.set_page_config(layout="wide")
    st.title("旅行費用 割り勘精算アプリ 💰")

    # --- 説明文を「単一選択」と「マルチセレクト」に修正 ---
    st.info(
        """
        このアプリが正しく動作するには、Notionデータベースに以下のプロパティが必要です。
        1.  `金額` (数値プロパティ)
        2.  `払った人` (**単一選択** プロパティ)
        3.  `誰の分` (**マルチセレクト** プロパティ)
        """
    )
    
    st.warning(
        """
        ⚠️ **運用の注意点** ⚠️
        
        もし2人で出し合った場合は、面倒でも2行に分けて入力してください。
        （例：1万円をAさんとBさんが5000円ずつ払った場合）
        
        * (良い例) 行1: 金額 5000, 払った人 [ A ], 誰の分 [ A, B, C ]
        * (良い例) 行2: 金額 5000, 払った人 [ B ], 誰の分 [ A, B, C ]
        """
    )
    
    if st.button("📊 精算結果を計算する", type="primary"):
        try:
            api_key = st.secrets["NOTION_API_KEY"]
            database_id = st.secrets["NOTION_DATABASE_ID"]
        except Exception:
            st.error("Streamlit CloudのSecretsが設定されていません。")
            return

        with st.spinner("Notionデータベースから支出データを取得中..."):
            try:
                data = fetch_notion_data(api_key, database_id)
            except Exception as e:
                st.error(f"Notion APIへの接続に失敗しました: {e}")
                return

        with st.spinner("精算金額を計算中..."):
            balances = defaultdict(float)
            processed_items = []
            
            for item in data:
                try:
                    props = item.get("properties", {})
                    
                    if "金額" not in props or "払った人" not in props or "誰の分" not in props:
                        continue
                        
                    amount = props["金額"].get("number")
                    
                    # 
                    # 変更点：「払った人」を "select" から取得
                    # 
                    payer_select = props["払った人"].get("select")
                    
                    # 
                    # 変更点：「誰の分」を "multi_select" から取得
                    #
                    sharers_multi_select = props["誰の分"].get("multi_select", [])

                    # データが不完全な場合はスキップ
                    if amount is None or amount == 0 or not payer_select or not sharers_multi_select:
                        continue
                        
                    # 
                    # 
                    
                    payer_name = payer_select["name"]
                    
                    sharer_names = [s["name"] for s in sharers_multi_select]
                    share_count = len(sharer_names)
                    
                    per_person_amount = round(amount / share_count, 2)

                    balances[payer_name] += amount
                    
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

            transactions = simplify_debts(balances)
            
            st.success("🎉 計算が完了しました！")
            
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
            
            with st.expander("処理された支出データ一覧を表示"):
                st.dataframe(pd.DataFrame(processed_items))

if __name__ == "__main__":
    main()