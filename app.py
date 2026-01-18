import streamlit as st
import pandas as pd
import numpy as np
import io

# --- 页面设置 ---
st.set_page_config(page_title="直接调拨单生成器", layout="wide")

st.title("📦 自动化直接调拨单生成器")
st.markdown("### 逻辑：库存扣减 -> FNSKU匹配 -> 自动拆行补位")

# --- 侧边栏：参数设置 ---
with st.sidebar:
    st.header("⚙️ 参数设置")
    header_row = st.number_input("Excel表头所在行 (默认第2行请输入1)", min_value=0, value=1, help="Python是从0开始计数的，如果表头在第2行，这里填1")
    st.info("说明：\n1. 请确保库存表中包含【可用库存、仓库名称、FnSKU、库区】字段。\n2. 支持直接粘贴需求数据。")

# --- 核心逻辑函数 ---
def process_data(df_demand, df_inv, df_plan=None):
    logs = [] # 用于记录处理日志
    results = []

    # 1. 字段标准化 (去除空格)
    df_inv.columns = [str(c).strip() for c in df_inv.columns]
    
    # 2. 字段映射 (兼容性处理)
    # 尝试自动寻找对应的列名，防止Excel列名微小变动
    col_map = {
        '可用库存': 'Stock', '仓库名称': 'Warehouse', 'FnSKU': 'FNSKU', '库区': 'Zone',
        'SKU': 'SKU'
    }
    # 简单的列名检查
    for key, val in col_map.items():
        # 如果找不到标准名，尝试找包含该名的列
        if key not in df_inv.columns:
            found = False
            for c in df_inv.columns:
                if key in c:
                    df_inv.rename(columns={c: val}, inplace=True)
                    found = True
                    break
            if not found and key != '库区': # 库区非必须，其他必须
                return None, f"错误：库存表中找不到【{key}】列，请检查表头。"
        else:
            df_inv.rename(columns={key: val}, inplace=True)

    # 3. 数据清洗
    df_inv['Stock'] = pd.to_numeric(df_inv['Stock'], errors='coerce').fillna(0)
    
    # 4. 筛选外协/天源仓库
    def is_target_wh(name):
        if pd.isna(name): return False
        return ("外协" in str(name)) or ("天源" in str(name))
    
    df_inv_target = df_inv[df_inv['Warehouse'].apply(is_target_wh)].copy()
    
    # 5. 扣减提货计划 (如果有)
    if df_plan is not None:
        # 这里假设计划表也有 SKU, FNSKU, 计划数
        # 实际逻辑需要根据您具体的计划表结构来写，这里做个预留框架
        pass 
    
    # 按库存降序排序（为了优先拿库存最多的其他FNSKU补位）
    df_inv_target.sort_values(by='Stock', ascending=False, inplace=True)

    # 6. 循环处理每一行需求
    for idx, row in df_demand.iterrows():
        sku = row['SKU']
        target_fnsku = row['FNSKU']
        qty_needed = row['需求数']
        country = row['国家'] # 保留国家信息

        if pd.isna(sku) or qty_needed <= 0: continue
        
        # 修正：需求数转为float/int
        try:
            qty_needed = float(qty_needed)
        except:
            continue

        # --- 阶段1：找目标FNSKU ---
        target_rows = df_inv_target[(df_inv_target['SKU'] == sku) & (df_inv_target['FNSKU'] == target_fnsku)]
        
        for _, stock_row in target_rows.iterrows():
            if qty_needed <= 0: break
            
            avail = stock_row['Stock']
            can_take = min(qty_needed, avail)
            
            if can_take > 0:
                results.append({
                    '国家': country,
                    '调出仓库': stock_row['Warehouse'],
                    '调入仓库': 'DLM供应链亚马逊深圳仓-SZ',
                    'SKU': sku,
                    'FNSKU': target_fnsku, # 原配
                    '调拨数量': can_take,
                    '库区': stock_row.get('Zone', ''), # 防止没有库区列
                    '备注': '目标匹配'
                })
                qty_needed -= can_take
                
        # --- 阶段2：补位 (找同SKU其他FNSKU) ---
        if qty_needed > 0:
            other_rows = df_inv_target[(df_inv_target['SKU'] == sku) & (df_inv_target['FNSKU'] != target_fnsku)]
            
            for _, stock_row in other_rows.iterrows():
                if qty_needed <= 0: break
                
                avail = stock_row['Stock']
                can_take = min(qty_needed, avail)
                
                if can_take > 0:
                    results.append({
                        '国家': country,
                        '调出仓库': stock_row['Warehouse'],
                        '调入仓库': 'DLM供应链亚马逊深圳仓-SZ',
                        'SKU': sku,
                        'FNSKU': stock_row['FNSKU'], # 替补 FNSKU
                        '调拨数量': can_take,
                        '库区': stock_row.get('Zone', ''),
                        '备注': '自动补位'
                    })
                    qty_needed -= can_take
        
        # 如果还是不够
        if qty_needed > 0:
            logs.append(f"⚠️ 警告：SKU {sku} (目标FNSKU {target_fnsku}) 总库存不足，仍缺货 {qty_needed}")

    if not results:
        return None, "没有生成任何调拨数据，请检查库存是否充足或SKU是否匹配。"
    
    return pd.DataFrame(results), logs

# --- 界面布局 ---

col1, col2 = st.columns([1, 1])

# --- 区域 1: 需求输入 (支持粘贴) ---
with col1:
    st.subheader("1. 输入/粘贴 调拨需求")
    st.caption("请直接从Excel复制数据粘贴到下方表格中（点击首行首列粘贴）")
    
    # 初始化一个空的DataFrame模板
    template_data = pd.DataFrame(columns=["国家", "SKU", "FNSKU", "需求数"])
    # 预留10行空行方便粘贴
    # template_data = pd.concat([template_data, pd.DataFrame([['']]*10, columns=template_data.columns)], ignore_index=True)

    edited_df = st.data_editor(
        template_data,
        num_rows="dynamic", # 允许动态添加行
        use_container_width=True,
        height=300
    )

# --- 区域 2: 文件上传 (支持Excel) ---
with col2:
    st.subheader("2. 上传数据源文件")
    
    # 在库库存上传
    inv_file = st.file_uploader("上传《在库库存》表", type=['xlsx', 'xls', 'csv'])
    
    # 提货计划上传
    plan_file = st.file_uploader("上传《美国提货计划》表 (可选)", type=['xlsx', 'xls', 'csv'])

# --- 执行按钮 ---
st.divider()
if st.button("🚀 开始生成直接调拨单", type="primary"):
    if edited_df.dropna(how='all').empty:
        st.error("请在左侧输入需求数据！")
    elif not inv_file:
        st.error("请上传在库库存文件！")
    else:
        with st.spinner("正在计算并拆分库区..."):
            try:
                # 1. 读取库存文件 (支持 Excel)
                if inv_file.name.endswith('.csv'):
                    df_inv = pd.read_csv(inv_file, header=header_row, encoding='utf-8-sig') # 尝试常用编码
                else:
                    df_inv = pd.read_excel(inv_file, header=header_row)

                # 2. 读取计划文件 (暂留空，逻辑同上)
                df_plan = None
                if plan_file:
                    if plan_file.name.endswith('.csv'):
                        df_plan = pd.read_csv(plan_file, header=header_row)
                    else:
                        df_plan = pd.read_excel(plan_file, header=header_row)

                # 3. 运行处理
                result_df, logs = process_data(edited_df, df_inv, df_plan)

                if isinstance(result_df, pd.DataFrame):
                    st.success(f"成功生成！共 {len(result_df)} 条调拨指令")
                    
                    # 显示日志
                    if logs:
                        with st.expander("查看处理日志/警告"):
                            for log in logs:
                                st.write(log)

                    # --- 结果展示与下载 ---
                    st.dataframe(result_df.head())
                    
                    # 准备下载文件：总表
                    buffer_master = io.BytesIO()
                    with pd.ExcelWriter(buffer_master, engine='xlsxwriter') as writer:
                        result_df.to_excel(writer, index=False, sheet_name='总表')
                    
                    st.download_button(
                        label="📥 下载完整汇总表 (.xlsx)",
                        data=buffer_master.getvalue(),
                        file_name="直接调拨单_汇总.xlsx",
                        mime="application/vnd.ms-excel"
                    )

                    # 准备下载文件：分仓表
                    st.markdown("### 🏘️ 各库区独立文件下载")
                    unique_whs = result_df['调出仓库'].unique()
                    
                    # 使用 columns 布局下载按钮
                    cols = st.columns(len(unique_whs))
                    for i, wh in enumerate(unique_whs):
                        sub_df = result_df[result_df['调出仓库'] == wh]
                        
                        buffer_sub = io.BytesIO()
                        with pd.ExcelWriter(buffer_sub, engine='xlsxwriter') as writer:
                            sub_df.to_excel(writer, index=False, sheet_name='Sheet1')
                        
                        safe_name = str(wh).replace('/', '_')
                        cols[i].download_button(
                            label=f"📥 {safe_name} ({len(sub_df)}行)",
                            data=buffer_sub.getvalue(),
                            file_name=f"直接调拨单_{safe_name}.xlsx",
                            mime="application/vnd.ms-excel"
                        )
                else:
                    st.error(logs) # 显示错误信息

            except Exception as e:
                st.error(f"发生程序错误: {e}")
                st.exception(e)