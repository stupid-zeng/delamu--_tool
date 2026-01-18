import streamlit as st
import pandas as pd
import io

# ==========================================
# 1. 页面基础配置 (必须是第一个 st 命令)
# ==========================================
st.set_page_config(page_title="外协调拨系统(倩文)", layout="wide", page_icon="🔒")

# ==========================================
# ==========================================
# 2. 核心隐私保护代码 (核弹级隐藏)
# ==========================================
hide_st_style = """
    <style>
    /* 1. 隐藏顶部的汉堡菜单 */
    #MainMenu {visibility: hidden;}
    
    /* 2. 隐藏页脚 "Made with Streamlit" */
    footer {visibility: hidden;}
    
    /* 3. 隐藏顶部的彩色装饰条 */
    header {visibility: hidden;}
    
    /* 4. 暴力隐藏右下角的 Streamlit 工具栏 */
    /* 针对新版 Shadow DOM 的隐藏 */
    [data-testid="stToolbar"] {
        visibility: hidden !important;
        display: none !important;
        height: 0px !important;
    }
    
    /* 针对旧版结构的隐藏 */
    .stApp > header {
        visibility: hidden !important;
        display: none !important;
    }
    
    /* 隐藏状态组件 */
    div[data-testid="stStatusWidget"] {
        visibility: hidden !important;
        display: none !important;
    }
    
    /* 隐藏头像框 */
    [data-testid="stDecoration"] {
        visibility: hidden !important;
        display: none !important;
    }

    /* 5. 调整主区域上边距，防止顶部留白 */
    .block-container {
        padding-top: 1rem !important;
    }
    </style>
    """
st.markdown(hide_st_style, unsafe_allow_html=True)

# ==========================================
# 3. 主程序标题
# ==========================================
st.title("🏭 外协/云仓库存 -> 直接调拨单生成器")

# --- 辅助函数：自动寻找表头 ---
def load_and_find_header(file_obj):
    """
    自动扫描前10行，找到包含 'SKU' 的那一行作为表头
    """
    try:
        file_obj.seek(0)
        # 1. 读取文件
        if file_obj.name.endswith('.csv'):
            try:
                df_raw = pd.read_csv(file_obj, header=None, encoding='utf-8-sig')
            except:
                file_obj.seek(0)
                df_raw = pd.read_csv(file_obj, header=None, encoding='gbk')
        else:
            df_raw = pd.read_excel(file_obj, header=None)
        
        # 2. 扫描前 10 行
        header_row_index = -1
        for i in range(min(10, len(df_raw))):
            row_values = [str(v).strip().upper() for v in df_raw.iloc[i].values]
            if 'SKU' in row_values:
                header_row_index = i
                break
        
        if header_row_index == -1:
            return None, "❌ 扫描失败：前10行未找到包含'SKU'的行。"

        # 3. 设置表头
        df_final = df_raw.iloc[header_row_index+1:].copy()
        df_final.columns = df_raw.iloc[header_row_index].values
        df_final.reset_index(drop=True, inplace=True)
        
        return df_final, f"✅ 已定位表头在第 {header_row_index+1} 行"

    except Exception as e:
        return None, f"❌ 文件读取错误: {e}"

# --- 核心逻辑：智能选列 ---
def smart_select_columns(df):
    """
    解决列名重复问题：从多个同名列中，挑选最合适的那一列
    """
    # 1. 清洗列名：转字符串、去空格
    df.columns = [str(c).strip() for c in df.columns]
    all_cols = list(df.columns)
    
    selected_cols = {}
    
    # --- A. 寻找 FNSKU (优先匹配 FNSKU, fnsku) ---
    fnsku_candidates = [c for c in all_cols if 'FNSKU' in c.upper()]
    if fnsku_candidates:
        selected_cols['FNSKU'] = fnsku_candidates[0]
    else:
        return None, f"❌ 未找到 FNSKU 列。现有列名：{all_cols}"

    # --- B. 寻找 SKU (不能包含 FNSKU) ---
    sku_candidates = [c for c in all_cols if 'SKU' in c.upper() and 'FNSKU' not in c.upper()]
    if sku_candidates:
        sku_candidates.sort(key=len)
        selected_cols['SKU'] = sku_candidates[0]
    else:
        return None, "❌ 未找到 SKU 列。"

    # --- C. 寻找 仓库 ---
    wh_candidates = [c for c in all_cols if '仓库' in c]
    if wh_candidates:
        selected_cols['Warehouse'] = wh_candidates[0]
    else:
        return None, "❌ 未找到 仓库 列。"

    # --- D. 寻找 库存 (最关键!!!) ---
    stock_candidates_priority = [c for c in all_cols if '可用' in c]
    if stock_candidates_priority:
        selected_cols['Stock'] = stock_candidates_priority[0]
    else:
        stock_others = [c for c in all_cols if '库存' in c and '主体' not in c]
        if stock_others:
            selected_cols['Stock'] = stock_others[0]
        else:
             return None, "❌ 未找到 库存/可用数量 列。"

    # --- E. 寻找 库区 ---
    zone_candidates = [c for c in all_cols if '库区' in c and '标记' not in c]
    if zone_candidates:
        selected_cols['Zone'] = zone_candidates[0]
    else:
        selected_cols['Zone'] = None 

    # --- 构建干净的 DataFrame ---
    df_clean = pd.DataFrame()
    df_clean['SKU'] = df[selected_cols['SKU']]
    df_clean['FNSKU'] = df[selected_cols['FNSKU']]
    df_clean['Warehouse'] = df[selected_cols['Warehouse']]
    df_clean['Stock'] = df[selected_cols['Stock']]
    
    if selected_cols['Zone']:
        df_clean['Zone'] = df[selected_cols['Zone']]
    else:
        df_clean['Zone'] = ''

    return df_clean, f"列映射报告：\nSKU取自: [{selected_cols['SKU']}]\nFNSKU取自: [{selected_cols['FNSKU']}]\n库存取自: [{selected_cols['Stock']}]"

# --- 主处理逻辑 ---
def process_data(df_demand, inv_file, plan_file=None):
    logs = []
    results = []

    # 1. 读取原始库存
    df_inv_raw, msg = load_and_find_header(inv_file)
    if df_inv_raw is None: return None, msg, None

    # 2. 智能选列
    df_inv, col_msg = smart_select_columns(df_inv_raw)
    if df_inv is None: return None, col_msg, None
    
    # 3. 数据清洗
    df_inv['SKU'] = df_inv['SKU'].astype(str).str.strip()
    df_inv['FNSKU'] = df_inv['FNSKU'].astype(str).str.strip()
    df_inv['Warehouse'] = df_inv['Warehouse'].astype(str).str.strip()
    df_inv['Zone'] = df_inv['Zone'].astype(str).str.strip()
    df_inv['Stock'] = pd.to_numeric(df_inv['Stock'], errors='coerce').fillna(0)

    # 4. 筛选外协
    filter_mask = df_inv['Warehouse'].str.contains("外协|天源", na=False)
    df_inv_target = df_inv[filter_mask].copy()
    
    debug_info = {
        "raw_cols": list(df_inv_raw.columns),
        "clean_head": df_inv.head(3),
        "target_count": len(df_inv_target),
        "col_msg": col_msg
    }

    if df_inv_target.empty:
        return None, "⚠️ 筛选后数据为空！请检查“仓库”列是否包含“外协”或“天源”。", debug_info

    # 5. 扣减计划
    if plan_file is not None:
        df_plan_raw, _ = load_and_find_header(plan_file)
        if df_plan_raw is not None:
            df_plan_raw.columns = [str(c).strip() for c in df_plan_raw.columns]
            p_map = {}
            for c in df_plan_raw.columns:
                if 'FNSKU' in c.upper(): p_map[c] = 'FNSKU'
                elif '需求' in c or 'QTY' in c.upper(): p_map[c] = 'PlanQty'
                elif 'SKU' in c.upper(): p_map[c] = 'SKU'
            df_plan_raw.rename(columns=p_map, inplace=True)
            
            df_plan_raw = df_plan_raw.loc[:, ~df_plan_raw.columns.duplicated()]

            if 'SKU' in df_plan_raw and 'PlanQty' in df_plan_raw:
                df_plan_raw['SKU'] = df_plan_raw['SKU'].astype(str).str.strip()
                if 'FNSKU' in df_plan_raw:
                     df_plan_raw['FNSKU'] = df_plan_raw['FNSKU'].astype(str).str.strip()
                else:
                     df_plan_raw['FNSKU'] = ''
                
                df_plan_raw['PlanQty'] = pd.to_numeric(df_plan_raw['PlanQty'], errors='coerce').fillna(0)
                
                plan_dict = df_plan_raw.groupby(['SKU', 'FNSKU'])['PlanQty'].sum().to_dict()
                
                for idx, row in df_inv_target.iterrows():
                    key = (row['SKU'], row['FNSKU'])
                    if key in plan_dict and plan_dict[key] > 0:
                        deduct = min(row['Stock'], plan_dict[key])
                        df_inv_target.at[idx, 'Stock'] -= deduct
                        plan_dict[key] -= deduct

    df_inv_target.sort_values(by='Stock', ascending=False, inplace=True)

    # 6. 匹配逻辑
    df_demand['SKU'] = df_demand['SKU'].astype(str).str.strip()
    if 'FnSKU' in df_demand.columns: df_demand['FNSKU'] = df_demand['FnSKU'].astype(str).str.strip()
    elif 'FNSKU' in df_demand.columns: df_demand['FNSKU'] = df_demand['FNSKU'].astype(str).str.strip()
    df_demand['订单需求'] = pd.to_numeric(df_demand['订单需求'], errors='coerce').fillna(0)

    for idx, row in df_demand.iterrows():
        sku = row['SKU']
        target_fnsku = row['FNSKU']
        qty_needed = row['订单需求']
        
        if sku == 'nan' or qty_needed <= 0: continue

        matches = df_inv_target[
            (df_inv_target['SKU'] == sku) & 
            (df_inv_target['FNSKU'] == target_fnsku)
        ]
        
        for _, inv_row in matches.iterrows():
            if qty_needed <= 0: break
            avail = inv_row['Stock']
            if avail <= 0: continue 
            
            take = min(qty_needed, avail)
            results.append({
                'SKU': sku, 'FNSKU': target_fnsku, '调拨数量': take,
                '调出仓库': inv_row['Warehouse'], '调出库区': inv_row['Zone'], '备注': '目标匹配'
            })
            qty_needed -= take
            
        if qty_needed > 0:
            subs = df_inv_target[
                (df_inv_target['SKU'] == sku) & 
                (df_inv_target['FNSKU'] != target_fnsku)
            ]
            for _, inv_row in subs.iterrows():
                if qty_needed <= 0: break
                avail = inv_row['Stock']
                if avail <= 0: continue
                
                take = min(qty_needed, avail)
                results.append({
                    'SKU': sku, 'FNSKU': inv_row['FNSKU'], 
                    '调拨数量': take,
                    '调出仓库': inv_row['Warehouse'], '调出库区': inv_row['Zone'], '备注': '自动补位'
                })
                qty_needed -= take
        
        if qty_needed > 0:
            logs.append(f"SKU {sku} (FnSKU: {target_fnsku}) 缺货: {qty_needed}")

    if not results:
        return None, "❌ 计算完成，但未生成任何调拨单。\n可能原因：1.所有SKU库存均为0；2.需求SKU与库存SKU不匹配。", debug_info
    
    # 7. 格式化
    df_res = pd.DataFrame(results)
    df_res['调拨类型'] = '组织内调拨'
    df_res['调入仓库'] = 'DLM供应链亚马逊深圳仓-SZ'
    df_res['调入库区'] = '成品-存储1区'
    df_res['调出库区'] = df_res.apply(lambda x: x['调出库区'] if x['调出库区'] and str(x['调出库区'])!='nan' else x['调出仓库'], axis=1)

    final_cols = ['调拨类型', '调出仓库', '调入仓库', 'SKU', 'FNSKU', '调出库区', '调入库区', '调拨数量', '备注']
    for c in final_cols:
        if c not in df_res.columns: df_res[c] = ''
            
    return df_res[final_cols], logs, debug_info

# --- 界面 ---
col_in, col_up = st.columns([35, 65])

with col_in:
    st.subheader("1. 需求数据")
    default = pd.DataFrame(columns=["SKU", "FnSKU", "订单需求"])
    edited_df = st.data_editor(default, num_rows="dynamic", height=450)

with col_up:
    st.subheader("2. 上传库存")
    inv_file = st.file_uploader("📂 A. 在库库存 (必填)", type=['xlsx', 'xls', 'csv'])
    plan_file = st.file_uploader("📂 B. 提货计划 (选填)", type=['xlsx', 'xls', 'csv'])
    
    st.divider()
    run = st.button("🚀 生成调拨单", type="primary", use_container_width=True)

if run:
    if inv_file and not edited_df.empty:
        with st.spinner("正在分析数据..."):
            try:
                res, msgs= process_data(edited_df, inv_file, plan_file)
                
               

                if res is not None:
                    if msgs:
                        with st.expander(f"⚠️ 缺货日志 ({len(msgs)})"):
                            st.write(msgs)
                    
                    st.success(f"✅ 成功！生成 {len(res)} 条指令。")
                    
                    unique_zones = res['调出库区'].unique()
                    cols = st.columns(3) 
                    for i, zone in enumerate(unique_zones):
                        sub_df = res[res['调出库区'] == zone]
                        buf = io.BytesIO()
                        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                            sub_df.to_excel(writer, index=False)
                        safe_name = str(zone).replace('/', '_')
                        with cols[i % 3]:
                            st.info(f"📦 **{safe_name}** ({len(sub_df)}行)")
                            st.download_button(f"📥 下载 {safe_name}.xlsx", buf.getvalue(), f"{safe_name}.xlsx", "application/vnd.ms-excel", key=f"d{i}")
                else:
                    st.error(msgs)
            except ImportError:
                st.error("请安装 xlsxwriter")
            except Exception as e:
                st.error(f"出错: {e}")
