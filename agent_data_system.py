import sqlite3
import pandas as pd
import json
import os
import sys
from datetime import datetime

# Configuration
DB_PATH = 'agent_analysis.db'

def init_db():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # --- 1. Signing Analysis Tables ---
    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_signing_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            new_sign_amount REAL,
            churn_amount REAL,
            net_sign_amount REAL,
            star_level TEXT,
            is_diamond BOOLEAN,
            UNIQUE(agent_name, month)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_signing_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            top_customers_new_json TEXT,
            top_customers_churn_json TEXT,
            top_customers_net_json TEXT,
            top_units_new_json TEXT,
            top_units_churn_json TEXT,
            top_units_net_json TEXT,
            business_details_json TEXT,
            UNIQUE(agent_name, month)
        )
    ''')

    # --- 2. Income Analysis Tables ---
    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_income_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            total_income REAL,
            fixed_income REAL,
            mobile_income REAL,
            UNIQUE(agent_name, month)
        )
    ''')
    
    # Migration: Add columns if they don't exist (for existing DBs)
    try:
        c.execute("ALTER TABLE agent_income_stats ADD COLUMN fixed_income REAL")
    except sqlite3.OperationalError:
        pass # Column likely exists
        
    try:
        c.execute("ALTER TABLE agent_income_stats ADD COLUMN mobile_income REAL")
    except sqlite3.OperationalError:
        pass # Column likely exists

    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_income_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            product_type_dist_json TEXT,
            top_customers_json TEXT,
            top_units_json TEXT,
            UNIQUE(agent_name, month)
        )
    ''')

    # --- 3. Cluster Analysis Tables ---
    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_cluster_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            line_count INTEGER,
            star_level TEXT,
            UNIQUE(agent_name, month)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS agent_cluster_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT,
            month TEXT,
            top_customers_json TEXT,
            UNIQUE(agent_name, month)
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

def normalize_month(val):
    """Normalize month string to YYYY-MM format."""
    s = str(val).strip()
    s = s.replace('年', '-').replace('月', '')
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:]}"
    return s

def import_signing_data(file_path):
    """Import data for 'Signing Analysis' (Double Line Business)."""
    print(f"Importing Signing Data from {file_path}...")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    col_map = {
        '月份': 'month',
        '上级代理商': 'agent', '渠道名称': 'agent', '代理商名称': 'agent',
        '客户名称': 'customer',
        '营销单元简称': 'unit', '营销单元': 'unit',
        '产品详细名称': 'product',
        '万元': 'revenue',
        '新签/变更/流失': 'type',
        '是否大于0': 'is_positive',
        '星级标签': 'level', '代理商星级': 'level'
    }
    
    df.columns = [c.strip() for c in df.columns]
    renamed = {}
    for col in df.columns:
        for k, v in col_map.items():
            if k == col:
                renamed[col] = v
                break
    df = df.rename(columns=renamed)

    required = ['month', 'agent', 'revenue', 'is_positive']
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"Missing required columns: {missing}")
        conn.close()
        return

    df['month'] = df['month'].apply(normalize_month)
    groups = df.groupby(['agent', 'month'])

    for (agent, month), group in groups:
        if pd.isna(agent) or agent == 'nan' or agent == '未知代理商':
            continue

        new_sign = group[group['is_positive'] == '是']['revenue'].sum()
        churn = group[group['is_positive'] == '否']['revenue'].abs().sum()
        net_sign = group['revenue'].sum()
        
        level = group['level'].iloc[0] if 'level' in group.columns else ''
        is_diamond = '钻石' in str(level)

        cursor.execute('''
            INSERT INTO agent_signing_stats (agent_name, month, new_sign_amount, churn_amount, net_sign_amount, star_level, is_diamond)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            new_sign_amount=excluded.new_sign_amount,
            churn_amount=excluded.churn_amount,
            net_sign_amount=excluded.net_sign_amount,
            star_level=excluded.star_level,
            is_diamond=excluded.is_diamond
        ''', (agent, month, new_sign, churn, net_sign, str(level), is_diamond))

        def get_top(sub_df, metric_col, n=20):
            res = sub_df.groupby('customer')['revenue'].sum().reset_index()
            if metric_col == 'churn':
                res['revenue'] = res['revenue'].abs()
            res = res.sort_values('revenue', ascending=False).head(n)
            return res[['customer', 'revenue']].to_dict('records')

        top_cust_new = get_top(group[group['is_positive'] == '是'], 'new')
        top_cust_churn = get_top(group[group['is_positive'] == '否'], 'churn')
        top_cust_net = group.groupby('customer')['revenue'].sum().reset_index().sort_values('revenue', ascending=False).head(20).to_dict('records')

        def get_top_unit(sub_df, metric_col, n=20):
            if 'unit' not in sub_df.columns: return []
            res = sub_df.groupby('unit')['revenue'].sum().reset_index()
            if metric_col == 'churn':
                res['revenue'] = res['revenue'].abs()
            res = res.sort_values('revenue', ascending=False).head(n)
            return res[['unit', 'revenue']].to_dict('records')

        top_unit_new = get_top_unit(group[group['is_positive'] == '是'], 'new')
        top_unit_churn = get_top_unit(group[group['is_positive'] == '否'], 'churn')
        top_unit_net = get_top_unit(group, 'net') if 'unit' in group.columns else []

        detail_df = group.copy()
        if 'unit' not in detail_df.columns: detail_df['unit'] = '-'
        biz_details = detail_df.groupby(['customer', 'unit'])['revenue'].sum().reset_index()
        biz_details = biz_details.sort_values('revenue', ascending=False).head(50)
        biz_details['month'] = month
        biz_details_list = biz_details.rename(columns={'revenue': 'net_sign'}).to_dict('records')

        cursor.execute('''
            INSERT INTO agent_signing_details (agent_name, month, top_customers_new_json, top_customers_churn_json, top_customers_net_json, top_units_new_json, top_units_churn_json, top_units_net_json, business_details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            top_customers_new_json=excluded.top_customers_new_json,
            top_customers_churn_json=excluded.top_customers_churn_json,
            top_customers_net_json=excluded.top_customers_net_json,
            top_units_new_json=excluded.top_units_new_json,
            top_units_churn_json=excluded.top_units_churn_json,
            top_units_net_json=excluded.top_units_net_json,
            business_details_json=excluded.business_details_json
        ''', (
            agent, month,
            json.dumps(top_cust_new, ensure_ascii=False),
            json.dumps(top_cust_churn, ensure_ascii=False),
            json.dumps(top_cust_net, ensure_ascii=False),
            json.dumps(top_unit_new, ensure_ascii=False),
            json.dumps(top_unit_churn, ensure_ascii=False),
            json.dumps(top_unit_net, ensure_ascii=False),
            json.dumps(biz_details_list, ensure_ascii=False)
        ))

    conn.commit()
    conn.close()
    print("Signing Data Import Completed.")

def import_income_data(file_path):
    """Import data for 'Income Analysis'."""
    print(f"Importing Income Data from {file_path}...")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    col_map = {
        '账期': 'month',
        '代理商名称': 'agent',
        '客户名称': 'customer',
        '简称': 'unit', '营销单元': 'unit',
        '产品类型': 'product_type',
        '万元': 'income'
    }
    
    df.columns = [c.strip() for c in df.columns]
    renamed = {}
    for col in df.columns:
        for k, v in col_map.items():
            if k == col:
                renamed[col] = v
                break
    df = df.rename(columns=renamed)

    required = ['month', 'agent', 'income']
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"Missing required columns: {missing}")
        conn.close()
        return

    df['month'] = df['month'].apply(normalize_month)
    groups = df.groupby(['agent', 'month'])

    for (agent, month), group in groups:
        if pd.isna(agent) or agent == 'nan' or agent == '未知代理商':
            continue

        total_income = group['income'].sum()
        
        mobile_products = ['物联网', '集群', '行业短信']
        mobile_income = group[group['product_type'].isin(mobile_products)]['income'].sum()
        fixed_income = total_income - mobile_income

        cursor.execute('''
            INSERT INTO agent_income_stats (agent_name, month, total_income, fixed_income, mobile_income)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            total_income=excluded.total_income,
            fixed_income=excluded.fixed_income,
            mobile_income=excluded.mobile_income
        ''', (agent, month, total_income, fixed_income, mobile_income))

        prod_dist = group.groupby('product_type')['income'].sum().reset_index().sort_values('income', ascending=False).to_dict('records')
        top_cust = group.groupby('customer')['income'].sum().reset_index().sort_values('income', ascending=False).head(20).to_dict('records')
        top_unit = []
        if 'unit' in group.columns:
            top_unit = group.groupby('unit')['income'].sum().reset_index().sort_values('income', ascending=False).head(20).to_dict('records')

        cursor.execute('''
            INSERT INTO agent_income_details (agent_name, month, product_type_dist_json, top_customers_json, top_units_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            product_type_dist_json=excluded.product_type_dist_json,
            top_customers_json=excluded.top_customers_json,
            top_units_json=excluded.top_units_json
        ''', (
            agent, month,
            json.dumps(prod_dist, ensure_ascii=False),
            json.dumps(top_cust, ensure_ascii=False),
            json.dumps(top_unit, ensure_ascii=False)
        ))

    conn.commit()
    conn.close()
    print("Income Data Import Completed.")

def import_cluster_data(file_path):
    """Import data for 'Cluster Business'."""
    print(f"Importing Cluster Data from {file_path}...")
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    col_map = {
        '账期': 'month', '月份': 'month',
        '代理商名称': 'agent', '代理商': 'agent',
        '客户名称': 'customer', '客户': 'customer',
        '营销单元': 'unit', '营销单元名称': 'unit', '营销单元简称': 'unit',
        '线数': 'count', '设备数': 'count', '总线数': 'count', '设备号': 'count', # Count rows if '设备号' exists but no 'count' col
        '星级': 'level', '代理商星级': 'level', '类型': 'type', '是否代理商': 'is_agent'
    }
    
    df.columns = [c.strip() for c in df.columns]
    renamed = {}
    for col in df.columns:
        for k, v in col_map.items():
            if k == col:
                renamed[col] = v
                break
    df = df.rename(columns=renamed)

    if 'count' in df.columns:
         # Check if it looks like a device ID (e.g. not a small number, or is string)
         # For cluster business, usually 1 row = 1 line/device.
         df['count'] = 1
    elif 'count' not in df.columns and '设备号' in df.columns:
         df['count'] = 1

    required = ['month', 'agent', 'count']
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"Missing required columns: {missing}")
        conn.close()
        return

    df['month'] = df['month'].apply(normalize_month)
    df = df[df['agent'].notna() & (df['agent'] != 'nan') & (df['agent'] != '未知代理商')]
    groups = df.groupby(['agent', 'month'])

    for (agent, month), group in groups:
        total_count = group['count'].sum()
        level = group['level'].iloc[0] if 'level' in group.columns else ''

        cursor.execute('''
            INSERT INTO agent_cluster_stats (agent_name, month, line_count, star_level)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            line_count=excluded.line_count,
            star_level=excluded.star_level
        ''', (agent, month, int(total_count), str(level)))

        top_cust = group.groupby('customer')['count'].sum().reset_index()
        top_cust = top_cust.sort_values('count', ascending=False).head(50)
        top_cust_list = top_cust.rename(columns={'count': 'value'}).to_dict('records')

        cursor.execute('''
            INSERT INTO agent_cluster_details (agent_name, month, top_customers_json)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_name, month) DO UPDATE SET
            top_customers_json=excluded.top_customers_json
        ''', (agent, month, json.dumps(top_cust_list, ensure_ascii=False)))

    conn.commit()
    conn.close()
    print("Cluster Data Import Completed.")

def generate_report():
    """Generate the combined HTML report."""
    # Ensure DB is initialized to avoid 'no such table' errors
    init_db()
    
    print("Generating Report...")
    conn = sqlite3.connect(DB_PATH)
    
    # Fetch all unique agents from all tables
    agents = pd.read_sql("""
        SELECT DISTINCT agent_name FROM agent_signing_stats 
        UNION 
        SELECT DISTINCT agent_name FROM agent_income_stats
        UNION
        SELECT DISTINCT agent_name FROM agent_cluster_stats
    """, conn)
    agent_list = agents['agent_name'].tolist()

    # Pre-calculate Cluster Ranks
    cluster_stats_df = pd.read_sql("SELECT * FROM agent_cluster_stats", conn)
    if not cluster_stats_df.empty:
        cluster_stats_df['rank_in_level'] = cluster_stats_df.groupby(['month', 'star_level'])['line_count'].rank(ascending=False, method='min')

    # Pre-calculate Signing Ranks (New Sign)
    signing_stats_df = pd.read_sql("SELECT * FROM agent_signing_stats", conn)
    if not signing_stats_df.empty:
        signing_stats_df['rank_in_level_new'] = signing_stats_df.groupby(['month', 'star_level'])['new_sign_amount'].rank(ascending=False, method='min').fillna(0).astype(int)
    
    html_content = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <title>代理商综合分析报告</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
        <style>
            /* Custom Scrollbar for better look */
            ::-webkit-scrollbar { width: 6px; height: 6px; }
            ::-webkit-scrollbar-track { bg: transparent; }
            ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
            ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
            
            /* Print/Export specific styles */
            .print-mode .max-h-60, 
            .print-mode .overflow-y-auto {
                max-height: none !important;
                overflow: visible !important;
                height: auto !important;
            }
            .print-mode .agent-card {
                break-inside: avoid;
                margin-bottom: 2rem;
                box-shadow: none;
                border: 1px solid #e5e7eb;
            }
            .print-mode body {
                background: white;
            }
        </style>
    </head>
    <body class="bg-gray-100 p-8 font-sans">
        <div class="max-w-7xl mx-auto" id="mainWrapper">
            <h1 class="text-4xl font-bold text-gray-800 mb-8 text-center">代理商综合分析报告</h1>
            
            <!-- Controls -->
            <div class="bg-white p-4 rounded-xl shadow-sm border border-gray-200 mb-8 sticky top-4 z-50 flex gap-4 items-center" id="controlPanel">
                <!-- Search -->
                <div class="relative flex-1">
                    <div class="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                         <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                    </div>
                    <input type="text" id="agentSearch" placeholder="输入代理商名称搜索..." class="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 outline-none transition-all">
                </div>
                
                <!-- Month Filter -->
                <div class="relative w-48">
                    <select id="monthFilter" class="w-full pl-3 pr-8 py-2 rounded-lg border border-gray-300 bg-white focus:ring-2 focus:ring-indigo-500 outline-none appearance-none">
                        <option value="all">所有月份 (最新)</option>
                    </select>
                    <div class="absolute inset-y-0 right-0 flex items-center px-2 pointer-events-none">
                        <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                    </div>
                </div>



                <!-- PDF Export -->
                <button onclick="exportPDF()" id="exportBtn" class="bg-rose-600 text-white px-4 py-2 rounded-lg font-bold hover:bg-rose-700 transition shadow-sm flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed" disabled>
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
                    导出报告
                </button>
            </div>

            <div id="reportContainer" class="space-y-8"></div>
        </div>

        <script>
            const agentData = AGENT_DATA_PLACEHOLDER;
            const container = document.getElementById('reportContainer');
            const searchInput = document.getElementById('agentSearch');
            const monthFilter = document.getElementById('monthFilter');
            const exportBtn = document.getElementById('exportBtn');
            const mainWrapper = document.getElementById('mainWrapper');
            const controlPanel = document.getElementById('controlPanel');

            // Populate Month Filter
            let allMonths = new Set();
            Object.values(agentData).forEach(d => Object.keys(d).forEach(m => allMonths.add(m)));
            const sortedMonths = Array.from(allMonths).sort().reverse();
            sortedMonths.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                opt.textContent = m;
                monthFilter.appendChild(opt);
            });

            function renderAgent(agent) {
                const data = agentData[agent];
                if (!data) return '';

                const closeBtn = `<button onclick="this.parentElement.remove()" class="absolute top-2 right-2 text-gray-400 hover:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-black/5 rounded-full z-10" title="移除"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></button>`;

                let targetMonth;
                const selectedMonth = monthFilter.value;
                
                if (selectedMonth === 'all') {
                    // Default to latest available month for this agent
                    const months = Object.keys(data).sort().reverse(); 
                    if (months.length === 0) return '';
                    targetMonth = months[0];
                } else {
                    targetMonth = selectedMonth;
                }

                const mData = data[targetMonth];
                if (!mData) return ''; // Agent has no data for this month
                
                const signing = mData.signing || {};
                const income = mData.income || {};
                const cluster = mData.cluster || {};
                
                const signDetails = mData.signing_details || {};
                const incDetails = mData.income_details || {};
                const cluDetails = mData.cluster_details || {};
                const cluTrend = mData.cluster_trend || {}; // {labels: [], values: []}

                // Header Info
                const starLevel = signing.star_level || cluster.star_level || '-';
                const isDiamond = signing.is_diamond || false;

                return `
                <div class="bg-white rounded-2xl shadow-md p-8 agent-card mb-10 transition-all hover:shadow-xl" data-name="${agent}" id="card-${agent}">
                    <!-- Header -->
                    <div class="flex justify-between items-start mb-6 border-b pb-4">
                        <div>
                            <h2 class="text-3xl font-black text-gray-800 flex items-center gap-3">
                                ${agent}
                                ${isDiamond ? '<span class="px-3 py-1 bg-purple-100 text-purple-700 text-sm rounded-full font-bold">钻石代理商</span>' : ''}
                            </h2>
                            <p class="text-gray-500 text-sm mt-2 font-medium">
                                数据账期: <span class="text-gray-800 font-bold">${targetMonth}</span> 
                                <span class="mx-2 text-gray-300">|</span> 
                                星级: <span class="text-orange-500 font-bold">${starLevel}</span>
                            </p>
                        </div>
                    </div>

                    <!-- 1. 签约情况分析 -->
                    ${signing.new_sign_amount !== undefined ? `
                    <div class="mb-10">
                        <h3 class="text-xl font-bold text-gray-700 mb-6 flex items-center gap-2">
                            <span class="w-1.5 h-6 bg-indigo-500 rounded"></span>签约情况分析 (双线)
                        </h3>
                        <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8 content-cards">
                            <div class="bg-blue-50 p-6 rounded-2xl border border-blue-100 relative group">
                                ${closeBtn}
                                <div class="text-blue-500 text-sm font-bold uppercase mb-1">新签月租 (万元)</div>
                                <div class="text-3xl font-black text-blue-700">${signing.new_sign_amount.toFixed(2)}</div>
                            </div>
                            <div class="bg-rose-50 p-6 rounded-2xl border border-rose-100 relative group">
                                ${closeBtn}
                                <div class="text-rose-500 text-sm font-bold uppercase mb-1">流失月租 (万元)</div>
                                <div class="text-3xl font-black text-rose-700">${signing.churn_amount.toFixed(2)}</div>
                            </div>
                            <div class="bg-emerald-50 p-6 rounded-2xl border border-emerald-100 relative group">
                                ${closeBtn}
                                <div class="text-emerald-500 text-sm font-bold uppercase mb-1">净签月租 (万元)</div>
                                <div class="text-3xl font-black text-emerald-700">${signing.net_sign_amount.toFixed(2)}</div>
                            </div>
                            <!-- Rank Card -->
                            <div class="bg-indigo-50 p-6 rounded-2xl border border-indigo-100 flex flex-col justify-center relative group">
                                ${closeBtn}
                                <div class="text-indigo-500 text-sm font-bold uppercase mb-1">同星级新签排名</div>
                                <div class="text-3xl font-black text-indigo-700">第${signing.rank_in_level_new || '-'}名</div>
                                <div class="text-xs text-indigo-400 mt-1">在 ${starLevel} 级代理商中</div>
                            </div>
                        </div>
                        
                        <!-- Top Lists (Collapsed view idea, or simple tabs? Let's keep it clean list) -->
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-6">
                            <div class="bg-gray-50 p-4 rounded-xl relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-600 mb-3 text-sm">Top 10 客户 (新签)</h4>
                                <table class="w-full text-xs">
                                    ${(signDetails.top_customers_new_json || []).slice(0, 10).map((c,i) => 
                                        `<tr class="border-b border-gray-200 last:border-0"><td class="py-1 text-gray-400">#${i+1}</td><td class="py-1 truncate max-w-[150px]" title="${c.customer}">${c.customer}</td><td class="py-1 text-right font-bold text-blue-600">${c.revenue.toFixed(2)}</td></tr>`
                                    ).join('')}
                                </table>
                            </div>
                            <div class="bg-gray-50 p-4 rounded-xl relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-600 mb-3 text-sm">Top 10 营销单元 (新签)</h4>
                                <table class="w-full text-xs">
                                    ${(signDetails.top_units_new_json || []).slice(0, 10).map((c,i) => 
                                        `<tr class="border-b border-gray-200 last:border-0"><td class="py-1 text-gray-400">#${i+1}</td><td class="py-1 truncate max-w-[150px]" title="${c.unit}">${c.unit}</td><td class="py-1 text-right font-bold text-blue-600">${c.revenue.toFixed(2)}</td></tr>`
                                    ).join('')}
                                </table>
                            </div>
                        </div>
                    </div>
                    ` : ''}

                    <!-- 2. 收入数据分析 -->
                    ${income.total_income !== undefined ? `
                    <div class="mb-10 pt-8 border-t border-gray-100">
                        <h3 class="text-xl font-bold text-gray-700 mb-6 flex items-center gap-2">
                            <span class="w-1.5 h-6 bg-blue-500 rounded"></span>收入数据分析
                        </h3>
                        
                        <!-- Cards Row -->
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
                             <div class="flex items-center justify-between bg-blue-50 p-6 rounded-2xl border border-blue-100 relative group">
                                 ${closeBtn}
                                 <div>
                                    <div class="text-blue-600 font-bold">当月总收入</div>
                                 </div>
                                 <div class="text-2xl font-black text-blue-800">${income.total_income.toFixed(2)} <span class="text-sm font-normal text-blue-600">万元</span></div>
                            </div>
                             <div class="flex items-center justify-between bg-indigo-50 p-6 rounded-2xl border border-indigo-100 relative group">
                                 ${closeBtn}
                                 <div>
                                    <div class="text-indigo-600 font-bold text-sm uppercase">移网总收入</div>
                                    <div class="text-xs text-indigo-400">物联网/集群/行业短信</div>
                                 </div>
                                 <div class="text-2xl font-black text-indigo-800">${(income.mobile_income || 0).toFixed(2)}</div>
                            </div>
                             <div class="flex items-center justify-between bg-purple-50 p-6 rounded-2xl border border-purple-100 relative group">
                                 ${closeBtn}
                                 <div>
                                    <div class="text-purple-600 font-bold text-sm uppercase">固网总收入</div>
                                    <div class="text-xs text-purple-400">宽带、组网、固网等</div>
                                 </div>
                                 <div class="text-2xl font-black text-purple-800">${(income.fixed_income || 0).toFixed(2)}</div>
                            </div>
                        </div>

                        <!-- Lists Row -->
                        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                            <!-- Top Customers -->
                            <div class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-700 mb-4 text-sm">产品类型收入分布</h4>
                                <div class="max-h-60 overflow-y-auto">
                                    <table class="w-full text-xs">
                                        ${(incDetails.product_type_dist_json || []).slice(0, 10).map((c,i) => 
                                            `<tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50"><td class="py-2 text-gray-400">#${i+1}</td><td class="py-2 truncate">${c.product_type}</td><td class="py-2 text-right font-bold text-indigo-600">${c.income.toFixed(2)}</td></tr>`
                                        ).join('')}
                                    </table>
                                </div>
                            </div>                        
                            <!-- Top Customers -->
                            <div class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-700 mb-4 text-sm">Top 客户收入贡献</h4>
                                <div class="max-h-60 overflow-y-auto">
                                    <table class="w-full text-xs">
                                        ${(incDetails.top_customers_json || []).slice(0, 10).map((c,i) => 
                                            `<tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50"><td class="py-2 text-gray-400">#${i+1}</td><td class="py-2 truncate max-w-[120px]">${c.customer}</td><td class="py-2 text-right font-bold text-blue-600">${c.income.toFixed(2)}</td></tr>`
                                        ).join('')}
                                    </table>
                                </div>
                            </div>
                            
                            <!-- Top Units (New) -->
                            <div class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-700 mb-4 text-sm">Top 营销单元收入贡献</h4>
                                <div class="max-h-60 overflow-y-auto">
                                    <table class="w-full text-xs">
                                        ${(incDetails.top_units_json || []).slice(0, 10).map((c,i) => 
                                            `<tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50"><td class="py-2 text-gray-400">#${i+1}</td><td class="py-2 truncate max-w-[120px]">${c.unit}</td><td class="py-2 text-right font-bold text-blue-600">${c.income.toFixed(2)}</td></tr>`
                                        ).join('')}
                                    </table>
                                </div>
                            </div>


                        </div>
                    </div>
                    ` : ''}

                    <!-- 3. 集群业务分析 -->
                    ${cluster.line_count !== undefined ? `
                    <div class="pt-8 border-t border-gray-100">
                        <h3 class="text-xl font-bold text-gray-700 mb-6 flex items-center gap-2">
                            <span class="w-1.5 h-6 bg-teal-500 rounded"></span>集群业务分析
                        </h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                            <div class="bg-teal-50 p-6 rounded-2xl border border-teal-100 flex items-center justify-between relative group">
                                ${closeBtn}
                                <div>
                                    <div class="text-teal-600 text-sm font-bold uppercase mb-1">当月新签线数</div>
                                    
                                </div>
                                <div class="text-4xl font-black text-teal-700">${cluster.line_count}</div>
                            </div>
                            <div class="bg-orange-50 p-6 rounded-2xl border border-orange-100 flex items-center justify-between relative group">
                                ${closeBtn}
                                <div>
                                    <div class="text-orange-600 text-sm font-bold uppercase mb-1">同星级排名</div>
                                    <div class="text-xs text-orange-400"></div>
                                </div>
                                <div class="text-4xl font-black text-orange-700">第${cluster.rank_in_level || '-'}名</div>
                            </div>
                        </div>
                        
                        <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                            
                             <!-- Top Customers -->
                             <div class="bg-white border border-gray-100 rounded-xl p-5 shadow-sm relative group">
                                ${closeBtn}
                                <h4 class="font-bold text-gray-700 mb-4 text-sm">Top 客户线数排名</h4>
                                <div class="max-h-60 overflow-y-auto">
                                    <table class="w-full text-xs">
                                        ${(cluDetails.top_customers_json || []).slice(0, 10).map((c,i) => 
                                            `<tr class="border-b border-gray-100 last:border-0 hover:bg-gray-50"><td class="py-2 text-gray-400">#${i+1}</td><td class="py-2 truncate max-w-[180px]">${c.customer}</td><td class="py-2 text-right font-bold text-teal-600">${c.value}</td></tr>`
                                        ).join('')}
                                    </table>
                                </div>
                             </div>
                        </div>
                    </div>
                    ` : ''}

                </div>
                `;
            }

            function initClusterChart(agent, labels, values) {
                 const chartId = `chart-${agent.replace(/[^a-zA-Z0-9]/g, '')}`;
                 const ctx = document.getElementById(chartId);
                 if(!ctx) return;
                 
                 // Destroy existing if any (simple check)
                 if(window[chartId] instanceof Chart) {
                     window[chartId].destroy();
                 }

                 window[chartId] = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: '线数',
                            data: values,
                            borderColor: '#0d9488',
                            backgroundColor: 'rgba(13, 148, 136, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 3
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: { legend: { display: false } },
                        scales: {
                            y: { beginAtZero: true, grid: { borderDash: [2, 4] } },
                            x: { grid: { display: false } }
                        }
                    }
                 });
            }

            function exportPDF() {
                const searchVal = searchInput.value.toLowerCase();
                const visibleCards = document.querySelectorAll('.agent-card');
                
                if (visibleCards.length === 0) {
                    alert('没有可导出的报告');
                    return;
                }

                // Add print mode class to body to expand scroll areas
                document.body.classList.add('print-mode');
                controlPanel.style.display = 'none'; // Hide controls
                
                // Determine filename
                let filename = '代理商综合分析报告.pdf';
                if (visibleCards.length === 1) {
                    const agentName = visibleCards[0].getAttribute('data-name');
                    const month = monthFilter.value === 'all' ? '最新账期' : monthFilter.value;
                    filename = `${agentName}_分析报告_${month}.pdf`;
                }

                const opt = {
                    margin: [10, 10],
                    filename: filename,
                    image: { type: 'jpeg', quality: 0.98 },
                    html2canvas: { scale: 2, useCORS: true, logging: false, windowWidth: document.body.scrollWidth },
                    jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
                    pagebreak: { mode: ['avoid-all', 'css', 'legacy'] }
                };

                // Add loading state
                const originalText = exportBtn.innerHTML;
                exportBtn.textContent = '正在生成...';
                exportBtn.disabled = true;

                // Export main wrapper
                html2pdf().set(opt).from(mainWrapper).save().then(() => {
                    // Restore state
                    exportBtn.innerHTML = originalText;
                    exportBtn.disabled = false;
                    document.body.classList.remove('print-mode');
                    controlPanel.style.display = 'flex';
                }).catch(err => {
                    console.error(err);
                    alert('导出失败，请重试');
                    exportBtn.innerHTML = originalText;
                    exportBtn.disabled = false;
                    document.body.classList.remove('print-mode');
                    controlPanel.style.display = 'flex';
                });
            }

            function renderAll() {
                const agents = Object.keys(agentData).sort();
                // Render top 50
                const targetAgents = agents.slice(0, 50);
                container.innerHTML = targetAgents.map(renderAgent).join('');
                
                // Update Export Button State
                exportBtn.disabled = targetAgents.length === 0;
                
                // Initialize charts
                requestAnimationFrame(() => {
                    targetAgents.forEach(agent => {
                        const mData = agentData[agent];
                        // Cluster Trend
                        if(mData && mData.cluster_trend) {
                            initClusterChart(agent, mData.cluster_trend.labels, mData.cluster_trend.values);
                        }
                    });
                });
            }

            searchInput.addEventListener('input', (e) => {
                const q = e.target.value.toLowerCase();
                const filtered = Object.keys(agentData).filter(a => a.toLowerCase().includes(q));
                const targetAgents = filtered.slice(0, 50); // Limit for performance
                
                container.innerHTML = targetAgents.map(renderAgent).join('');
                
                if (filtered.length === 0) {
                    container.innerHTML = '<div class="text-center text-gray-400 py-10">未找到匹配代理商</div>';
                    exportBtn.disabled = true;
                } else {
                    exportBtn.disabled = false;
                }

                requestAnimationFrame(() => {
                    targetAgents.forEach(agent => {
                        // Re-init charts for filtered items
                        // We need to look up data again or pass it
                        // Simplified: access global data
                        // Note: renderAgent uses filtered month, so charts might need to be aware?
                        // Actually trend chart is usually all-time.
                        const data = agentData[agent];
                        // The 'renderAgent' uses 'monthFilter.value' to pick data.
                        // But cluster trend is agent-level (all months).
                        // However, 'mData' inside renderAgent logic extracts trend from the *selected month's* data object structure.
                        // In our python script, 'cluster_trend' is added to EVERY month's data object.
                        // So getting it from the selected month is fine.
                        
                        let targetMonth = monthFilter.value;
                        if (targetMonth === 'all') {
                             const months = Object.keys(data).sort().reverse();
                             targetMonth = months[0];
                        }
                        
                        const mData = data[targetMonth];
                        if(mData && mData.cluster_trend) {
                            initClusterChart(agent, mData.cluster_trend.labels, mData.cluster_trend.values);
                        }
                    });
                });
            });

            monthFilter.addEventListener('change', () => {
                // Re-render current list based on search
                const q = searchInput.value.toLowerCase();
                const filtered = Object.keys(agentData).filter(a => a.toLowerCase().includes(q));
                const targetAgents = filtered.slice(0, 50);
                
                container.innerHTML = targetAgents.map(renderAgent).join('');
                
                requestAnimationFrame(() => {
                    targetAgents.forEach(agent => {
                        let targetMonth = monthFilter.value;
                        if (targetMonth === 'all') {
                             const months = Object.keys(agentData[agent]).sort().reverse();
                             targetMonth = months[0];
                        }
                        const mData = agentData[agent][targetMonth];
                        if(mData && mData.cluster_trend) {
                            initClusterChart(agent, mData.cluster_trend.labels, mData.cluster_trend.values);
                        }
                    });
                });
            });

            renderAll();
        </script>
    </body>
    </html>
    """

    data_export = {}
    for agent in agent_list:
        data_export[agent] = {}
        
        # Use pre-calculated dataframe for signing stats to get the correct rank
        s_stats = signing_stats_df[signing_stats_df['agent_name'] == agent] if not signing_stats_df.empty else pd.DataFrame()
        s_details = pd.read_sql(f"SELECT * FROM agent_signing_details WHERE agent_name = '{agent}'", conn)
        i_stats = pd.read_sql(f"SELECT * FROM agent_income_stats WHERE agent_name = '{agent}'", conn)
        i_details = pd.read_sql(f"SELECT * FROM agent_income_details WHERE agent_name = '{agent}'", conn)
        c_stats = cluster_stats_df[cluster_stats_df['agent_name'] == agent] if not cluster_stats_df.empty else pd.DataFrame()
        c_details = pd.read_sql(f"SELECT * FROM agent_cluster_details WHERE agent_name = '{agent}'", conn)

        all_months = set(s_stats['month'].tolist() + i_stats['month'].tolist() + c_stats['month'].tolist() if not c_stats.empty else [])
        
        # Calculate Cluster Trend once per agent
        cluster_trend = { 'labels': [], 'values': [] }
        if not c_stats.empty:
            c_sorted = c_stats.sort_values('month')
            cluster_trend['labels'] = c_sorted['month'].tolist()
            cluster_trend['values'] = c_sorted['line_count'].tolist()

        for m in all_months:
            data_export[agent][m] = {
                'signing': s_stats[s_stats['month'] == m].to_dict('records')[0] if not s_stats[s_stats['month'] == m].empty else {},
                'signing_details': {},
                'income': i_stats[i_stats['month'] == m].to_dict('records')[0] if not i_stats[i_stats['month'] == m].empty else {},
                'income_details': {},
                'cluster': c_stats[c_stats['month'] == m].to_dict('records')[0] if not c_stats.empty and not c_stats[c_stats['month'] == m].empty else {},
                'cluster_details': {},
                'cluster_trend': cluster_trend
            }
            
            # JSON Parse Logic
            if not s_details[s_details['month'] == m].empty:
                rec = s_details[s_details['month'] == m].iloc[0]
                data_export[agent][m]['signing_details'] = {
                    'top_customers_new_json': json.loads(rec['top_customers_new_json']) if rec['top_customers_new_json'] else [],
                    'top_units_new_json': json.loads(rec['top_units_new_json']) if rec['top_units_new_json'] else []
                }
            
            if not i_details[i_details['month'] == m].empty:
                rec = i_details[i_details['month'] == m].iloc[0]
                data_export[agent][m]['income_details'] = {
                    'product_type_dist_json': json.loads(rec['product_type_dist_json']) if rec['product_type_dist_json'] else [],
                    'top_customers_json': json.loads(rec['top_customers_json']) if rec['top_customers_json'] else [],
                    'top_units_json': json.loads(rec['top_units_json']) if rec['top_units_json'] else []
                }
            
            if not c_details[c_details['month'] == m].empty:
                rec = c_details[c_details['month'] == m].iloc[0]
                data_export[agent][m]['cluster_details'] = {
                    'top_customers_json': json.loads(rec['top_customers_json']) if rec['top_customers_json'] else []
                }

    final_html = html_content.replace('AGENT_DATA_PLACEHOLDER', json.dumps(data_export, ensure_ascii=False))
    
    with open('agent_analysis_report.html', 'w', encoding='utf-8') as f:
        f.write(final_html)
    
    print("Report generated: agent_analysis_report.html")
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent_data_system.py [init|import_signing|import_income|import_cluster|report] [file_path]")
    else:
        cmd = sys.argv[1]
        if cmd == 'init':
            init_db()
        elif cmd == 'import_signing':
            if len(sys.argv) < 3: print("Please provide file path")
            else: import_signing_data(sys.argv[2])
        elif cmd == 'import_income':
            if len(sys.argv) < 3: print("Please provide file path")
            else: import_income_data(sys.argv[2])
        elif cmd == 'import_cluster':
            if len(sys.argv) < 3: print("Please provide file path")
            else: import_cluster_data(sys.argv[2])
        elif cmd == 'report':
            generate_report()
        else:
            print("Unknown command")
