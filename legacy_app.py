import streamlit as st
import sqlite3
from datetime import datetime
import os
import uuid
import base64
import threading
import re
import html

# --- 0. 基础环境准备 ---
if not os.path.exists("uploads"):
    os.makedirs("uploads")
if not os.path.exists("static"):
    os.makedirs("static")
if not os.path.exists("exports"):
    os.makedirs("exports")

# --- 1. 数据库引擎：全局线程锁 ---
@st.cache_resource
def get_db_lock():
    return threading.Lock()

db_lock = get_db_lock()

def run_query(query, params=(), fetch=False):
    with db_lock:
        conn = sqlite3.connect("my_study_data.db", check_same_thread=False, timeout=60)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            c = conn.cursor()
            c.execute(query, params)
            if fetch:
                return c.fetchall()
            conn.commit()
        finally:
            conn.close()

# 错题库（保留原有结构，向后兼容旧数据）
run_query('''CREATE TABLE IF NOT EXISTS entries
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              type TEXT, category TEXT, title TEXT,
              content TEXT, answer TEXT, date TEXT, image_path TEXT)''')
run_query('''CREATE TABLE IF NOT EXISTS reviews
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              entry_id INTEGER, review_date TEXT, insight TEXT)''')
try:
    run_query("SELECT topic FROM entries LIMIT 1", fetch=True)
except sqlite3.OperationalError:
    run_query("ALTER TABLE entries ADD COLUMN topic TEXT DEFAULT '未分类'")

run_query('''CREATE TABLE IF NOT EXISTS archived_categories
             (category TEXT PRIMARY KEY, archived_date TEXT)''')

# 学科大纲：章节顺序 / 依赖 / 一句话总结
run_query('''CREATE TABLE IF NOT EXISTS chapters
             (category TEXT, title TEXT, chapter_order INTEGER,
              depends_on TEXT, summary TEXT,
              PRIMARY KEY (category, title))''')

# 新增：章节自由笔记（日志式，按时间往下写，对应你 docx 的写法）
run_query('''CREATE TABLE IF NOT EXISTS notes
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              category TEXT, chapter TEXT, content TEXT,
              date TEXT, image_path TEXT)''')

# ---- 新版：学科(三级大纲: 学科→章节→知识点/小节) + 分类型学习条目 ----
# 与旧的 chapters/notes/entries 完全独立，互不影响，旧数据原样保留。
ITEM_TYPES = ["知识点", "方法总结", "例题", "错题", "重点题型"]

run_query('''CREATE TABLE IF NOT EXISTS subjects
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT UNIQUE NOT NULL, created_date TEXT)''')
run_query('''CREATE TABLE IF NOT EXISTS outline_chapters
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              subject_id INTEGER NOT NULL, title TEXT NOT NULL,
              order_idx INTEGER, summary TEXT)''')
run_query('''CREATE TABLE IF NOT EXISTS outline_sections
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              chapter_id INTEGER NOT NULL, title TEXT NOT NULL,
              order_idx INTEGER, summary TEXT)''')
run_query('''CREATE TABLE IF NOT EXISTS study_items
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              subject_id INTEGER NOT NULL, chapter_id INTEGER NOT NULL,
              section_id INTEGER, item_type TEXT NOT NULL,
              title TEXT, content TEXT, image_path TEXT, date TEXT)''')
run_query('''CREATE TABLE IF NOT EXISTS study_item_reviews
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id INTEGER NOT NULL, review_date TEXT, insight TEXT)''')

# --- 2. 界面视觉设置 ---
st.set_page_config(page_title="我的知识库", layout="wide")

def get_base64_of_bin_file(bin_file):
    if os.path.exists(bin_file):
        with open(bin_file, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    return ""

img_path = "static/image_ef9894.jpg"
img_base64 = get_base64_of_bin_file(img_path)
st.markdown(f"""
    <style>
    .stApp {{
        background-image: url("data:image/jpg;base64,{img_base64}");
        background-attachment: fixed;
        background-size: cover;
        background-position: center;
    }}
    [data-testid="block-container"] {{
        background-color: rgba(30, 35, 40, 0.75) !important;
        backdrop-filter: blur(16px) saturate(180%) !important;
        border-radius: 20px;
        padding: 3rem !important;
        margin-top: 2rem;
        border: 1px solid rgba(255, 255, 255, 0.1);
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.6);
    }}
    label p, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown p strong {{
        color: #111111 !important;
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        font-weight: 900 !important;
        letter-spacing: 1.5px !important;
        text-shadow: 0px 0px 4px rgba(255,255,255,0.8), 0px 0px 8px rgba(212,119,255,0.4) !important;
    }}
    [data-testid="stExpander"] details summary p {{
        color: #00E5FF !important;
        font-weight: 900 !important;
        letter-spacing: 1.5px !important;
        background-color: transparent !important;
        text-shadow: 1px 1px 3px rgba(0,0,0,0.9), 0px 0px 8px rgba(0, 229, 255, 0.4) !important;
    }}
    [data-testid="stExpander"] details summary {{
        background-color: transparent !important;
        border: none !important;
    }}
    [data-testid="stExpanderDetails"] {{
        background-color: rgba(0, 0, 0, 0.5) !important;
        padding: 1.5rem !important;
        border-radius: 0 0 8px 8px;
        border-top: 1px solid rgba(255,255,255,0.1);
    }}
    [data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] {{
        background-color: rgba(20, 20, 25, 0.6) !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
        border-radius: 8px !important;
    }}
    input, textarea, [data-baseweb="select"] span {{
        color: #ffffff !important;
        background-color: transparent !important;
        -webkit-text-fill-color: #ffffff !important;
        letter-spacing: 1px !important;
    }}
    [data-testid="stSidebar"] {{
        background-color: rgba(15, 15, 20, 0.75) !important;
        backdrop-filter: blur(15px);
    }}
    /* 修复：卡片内文字统一强制深色，不再依赖会被覆盖的选择器，避免白字白底看不清 */
    .outline-card, .outline-card * ,
    .note-card, .note-card *,
    .note-card-date {{
        color: #0d3b3f !important;
        -webkit-text-fill-color: #0d3b3f !important;
        text-shadow: none !important;
    }}
    .outline-card {{
        background-color: rgba(0, 229, 255, 0.12) !important;
        border-left: 4px solid #00E5FF !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
        margin-bottom: 10px !important;
    }}
    .note-card {{
        background-color: rgba(255, 255, 255, 0.55) !important;
        border-left: 4px solid #B37FE0 !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
        margin-bottom: 14px !important;
    }}
    .note-card-date {{
        font-size: 12px !important;
        opacity: 0.7 !important;
        margin-bottom: 6px !important;
        display: block;
    }}
    </style>
    """, unsafe_allow_html=True)

# --- 3. 工具函数 ---
def archive_category(category):
    run_query("INSERT OR IGNORE INTO archived_categories (category, archived_date) VALUES (?, ?)",
               (category, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def restore_category(category):
    run_query("DELETE FROM archived_categories WHERE category=?", (category,))

def get_archived_categories():
    rows = run_query("SELECT category FROM archived_categories ORDER BY archived_date DESC", fetch=True)
    return [row[0] for row in rows]

def safe_filename(name):
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name if name else "未命名学科"

def image_to_data_uri(path):
    if not path or not os.path.exists(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"

def simple_markdown_to_html(text):
    if not text:
        return ""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped, flags=re.DOTALL)
    return escaped.replace("\n", "<br>")

def save_uploaded_images(files):
    paths = []
    for f in (files or []):
        fn = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}.{f.name.split('.')[-1]}"
        p = os.path.join("uploads", fn)
        with open(p, "wb") as out_f:
            out_f.write(f.getbuffer())
        paths.append(p)
    return paths

# ---- 所有学科 / 章节 相关 ----
def get_all_categories():
    """学科来源 = chapters 表 ∪ entries 表 ∪ notes 表，去重"""
    cats = set()
    for row in run_query("SELECT DISTINCT category FROM chapters", fetch=True):
        if row[0]: cats.add(row[0])
    for row in run_query("SELECT DISTINCT category FROM entries", fetch=True):
        if row[0]: cats.add(row[0])
    for row in run_query("SELECT DISTINCT category FROM notes", fetch=True):
        if row[0]: cats.add(row[0])
    return sorted(cats)

def get_chapters(category):
    return run_query(
        "SELECT title, chapter_order, depends_on, summary FROM chapters WHERE category=? ORDER BY chapter_order ASC, title ASC",
        (category,), fetch=True)

def upsert_chapter(category, title, chapter_order, depends_on="", summary=""):
    run_query(
        """INSERT INTO chapters (category, title, chapter_order, depends_on, summary)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(category, title) DO UPDATE SET
             chapter_order=excluded.chapter_order,
             depends_on=CASE WHEN excluded.depends_on!='' THEN excluded.depends_on ELSE chapters.depends_on END,
             summary=CASE WHEN excluded.summary!='' THEN excluded.summary ELSE chapters.summary END""",
        (category, title, chapter_order, depends_on, summary))

def render_outline(category):
    chapters = get_chapters(category)
    if not chapters:
        st.caption("这门学科还没写大纲，去「🆕 新建学科 / 大纲」里补一下章节目录。")
        return
    st.markdown("### 🗺️ 学科大纲")
    for title, order, depends_on, summary in chapters:
        dep_text = f"　依赖：<b>{html.escape(depends_on)}</b>" if depends_on else ""
        order_text = order if order is not None else "-"
        st.markdown(
            f"<div class='outline-card'><b>{order_text}. {html.escape(title)}</b>{dep_text}<br>"
            f"{html.escape(summary) if summary else '（还没写一句话总结，可以先跳过）'}</div>",
            unsafe_allow_html=True)
    st.divider()

# ---- 新版：学科(三级大纲) + 分类型学习条目 相关函数 ----
def get_subjects():
    return run_query("SELECT id, name FROM subjects ORDER BY name ASC", fetch=True)

def create_subject(name):
    run_query("INSERT OR IGNORE INTO subjects (name, created_date) VALUES (?, ?)",
               (name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    row = run_query("SELECT id FROM subjects WHERE name=?", (name,), fetch=True)
    return row[0][0] if row else None

def get_outline_chapters(subject_id):
    return run_query(
        "SELECT id, title, order_idx, summary FROM outline_chapters WHERE subject_id=? ORDER BY order_idx ASC, title ASC",
        (subject_id,), fetch=True)

def add_chapters_bulk(subject_id, lines):
    existing = get_outline_chapters(subject_id)
    start_order = (max([c[2] for c in existing], default=-1) + 1)
    for i, line in enumerate(lines):
        run_query("INSERT INTO outline_chapters (subject_id, title, order_idx, summary) VALUES (?,?,?,?)",
                   (subject_id, line, start_order + i, ""))

def update_chapter_summary(chapter_id, summary):
    run_query("UPDATE outline_chapters SET summary=? WHERE id=?", (summary, chapter_id))

def get_outline_sections(chapter_id):
    return run_query(
        "SELECT id, title, order_idx, summary FROM outline_sections WHERE chapter_id=? ORDER BY order_idx ASC, title ASC",
        (chapter_id,), fetch=True)

def add_sections_bulk(chapter_id, lines):
    existing = get_outline_sections(chapter_id)
    start_order = (max([s[2] for s in existing], default=-1) + 1)
    for i, line in enumerate(lines):
        run_query("INSERT INTO outline_sections (chapter_id, title, order_idx, summary) VALUES (?,?,?,?)",
                   (chapter_id, line, start_order + i, ""))

def update_section_summary(section_id, summary):
    run_query("UPDATE outline_sections SET summary=? WHERE id=?", (summary, section_id))

def get_study_items(chapter_id, section_id, item_type):
    """section_id: None 表示不限小节（用于导出）；"__chapter__" 表示只看挂在章节整体的条目；否则是具体小节 id"""
    if section_id == "__chapter__":
        return run_query(
            "SELECT id, title, content, image_path, date FROM study_items WHERE chapter_id=? AND section_id IS NULL AND item_type=? ORDER BY id ASC",
            (chapter_id, item_type), fetch=True)
    elif section_id is None:
        return run_query(
            "SELECT id, title, content, image_path, date FROM study_items WHERE chapter_id=? AND item_type=? ORDER BY id ASC",
            (chapter_id, item_type), fetch=True)
    else:
        return run_query(
            "SELECT id, title, content, image_path, date FROM study_items WHERE chapter_id=? AND section_id=? AND item_type=? ORDER BY id ASC",
            (chapter_id, section_id, item_type), fetch=True)

def add_study_item(subject_id, chapter_id, section_id, item_type, title, content, image_path):
    run_query(
        """INSERT INTO study_items (subject_id, chapter_id, section_id, item_type, title, content, image_path, date)
           VALUES (?,?,?,?,?,?,?,?)""",
        (subject_id, chapter_id, section_id, item_type, title, content, image_path,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def update_study_item(item_id, content):
    run_query("UPDATE study_items SET content=? WHERE id=?", (content, item_id))

def delete_study_item(item_id, image_path):
    if image_path:
        for p in image_path.split(","):
            if os.path.exists(p): os.remove(p)
    run_query("DELETE FROM study_items WHERE id=?", (item_id,))
    run_query("DELETE FROM study_item_reviews WHERE item_id=?", (item_id,))

def get_item_reviews(item_id):
    return run_query("SELECT review_date, insight FROM study_item_reviews WHERE item_id=? ORDER BY id ASC",
                      (item_id,), fetch=True)

def add_item_review(item_id, insight):
    run_query("INSERT INTO study_item_reviews (item_id, review_date, insight) VALUES (?, ?, ?)",
               (item_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), insight))

# --- 4. 主逻辑 ---
st.sidebar.title("📌 导航菜单")
choice = st.sidebar.radio(
    "请选择操作",
    ["🆕 旧版：新建学科 / 大纲", "📖 旧版：章节笔记", "❌ 旧版：错题本",
     "🧭 新版：学科大纲（章节+知识点）", "📚 新版：学习笔记（按类型）", "📘 学科秘籍（导出）"]
)
if 'edit_id' not in st.session_state:
    st.session_state.edit_id = None
if 'edit_note_id' not in st.session_state:
    st.session_state.edit_note_id = None
if 'edit_item_id' not in st.session_state:
    st.session_state.edit_item_id = None

# ============ 1. 新建学科 / 大纲 ============
if choice == "🆕 旧版：新建学科 / 大纲":
    st.header("🆕 新建学科 / 编写大纲")
    st.caption("先把这门学科的章节目录列出来（一行一个），保存后就能去「章节笔记」里往每一章写内容了。已有学科也可以在这里补充/调整章节。")

    existing_cats = get_all_categories()
    mode = st.radio("操作", ["新建学科", "编辑已有学科的大纲"], horizontal=True)

    if mode == "新建学科":
        new_cat = st.text_input("学科名称", autocomplete="off")
        chapter_text = st.text_area(
            "章节目录（每行一个章节名，按顺序输入）",
            height=200,
            placeholder="例如：\n第1章 随机事件与概率\n第2章 随机变量及其分布\n第3章 多维随机变量\n..."
        )
        if st.button("🚀 创建学科大纲"):
            if new_cat.strip() and chapter_text.strip():
                lines = [l.strip() for l in chapter_text.split("\n") if l.strip()]
                for i, line in enumerate(lines):
                    upsert_chapter(new_cat.strip(), line, i)
                st.success(f"已创建「{new_cat}」，共 {len(lines)} 个章节，可以去「📖 章节笔记」开始写了。")
            else:
                st.error("学科名称和章节目录都要填。")
    else:
        if not existing_cats:
            st.info("还没有任何学科，先创建一个吧。")
        else:
            sel_c = st.selectbox("选择学科", ["请选择"] + existing_cats)
            if sel_c != "请选择":
                chapters = get_chapters(sel_c)
                st.markdown("#### 当前章节")
                render_outline(sel_c)
                st.markdown("#### 追加新章节")
                add_text = st.text_area("新增章节（每行一个，会追加到已有章节后面）", height=100, key="add_chapters")
                if st.button("➕ 追加章节"):
                    if add_text.strip():
                        start_order = (max([c[1] for c in chapters], default=-1) + 1)
                        lines = [l.strip() for l in add_text.split("\n") if l.strip()]
                        for i, line in enumerate(lines):
                            upsert_chapter(sel_c, line, start_order + i)
                        st.success(f"已追加 {len(lines)} 个章节")
                        st.rerun()
                st.markdown("#### 编辑某一章节的依赖 / 总结")
                if chapters:
                    tits = [c[0] for c in chapters]
                    sel_t = st.selectbox("选择章节", tits, key="edit_chapter_sel")
                    cur = next(c for c in chapters if c[0] == sel_t)
                    dep_options = ["无"] + [t for t in tits if t != sel_t]
                    dep_val = st.selectbox("依赖的前置章节", dep_options,
                                            index=dep_options.index(cur[2]) if cur[2] in dep_options else 0)
                    sum_val = st.text_area("一句话总结", value=cur[3] or "", height=68)
                    if st.button("💾 保存这一章"):
                        dep_to_save = "" if dep_val == "无" else dep_val
                        run_query(
                            "UPDATE chapters SET depends_on=?, summary=? WHERE category=? AND title=?",
                            (dep_to_save, sum_val, sel_c, sel_t))
                        st.success("已保存")
                        st.rerun()

# ============ 2. 章节笔记（自由日志式） ============
elif choice == "📖 旧版：章节笔记":
    st.header("📖 章节笔记")
    st.caption("像写日记一样往章节里加内容，新的写在最下面，可以随时补充，不用死板填表格。")

    cats = get_all_categories()
    if not cats:
        st.info("还没有学科，先去「🆕 新建学科 / 大纲」建一个。")
    else:
        sel_c = st.selectbox("选择学科", ["请选择"] + cats)
        if sel_c != "请选择":
            chapters = get_chapters(sel_c)
            tits = [c[0] for c in chapters]
            if not tits:
                st.warning("这门学科还没有章节，先去「🆕 新建学科 / 大纲」里补章节目录。")
            else:
                sel_t = st.selectbox("选择章节", tits)
                if sel_t:
                    cur = next((c for c in chapters if c[0] == sel_t), None)
                    if cur and cur[3]:
                        st.caption(f"本章总结：{cur[3]}")
                    st.divider()
                    notes = run_query(
                        "SELECT id, content, date, image_path FROM notes WHERE category=? AND chapter=? ORDER BY id ASC",
                        (sel_c, sel_t), fetch=True)
                    if not notes:
                        st.caption("这一章还没有笔记，在下面写下第一条吧。")
                    for note_id, content, ndate, image_path in notes:
                        if st.session_state.edit_note_id == note_id:
                            new_content = st.text_area("编辑这条笔记", value=content, height=150, key=f"editnote_{note_id}")
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.button("💾 保存", key=f"savenote_{note_id}"):
                                    run_query("UPDATE notes SET content=? WHERE id=?", (new_content, note_id))
                                    st.session_state.edit_note_id = None
                                    st.rerun()
                            with c2:
                                if st.button("❌ 取消", key=f"cancelnote_{note_id}"):
                                    st.session_state.edit_note_id = None
                                    st.rerun()
                        else:
                            st.markdown(
                                f"<div class='note-card'><span class='note-card-date'>{html.escape(ndate)}</span>"
                                f"{simple_markdown_to_html(content)}</div>",
                                unsafe_allow_html=True)
                            if image_path:
                                for p in image_path.split(","):
                                    if os.path.exists(p):
                                        st.image(p, use_container_width=True)
                            cbtn1, cbtn2, _ = st.columns([1, 1, 8])
                            if cbtn1.button("🖊️ 编辑", key=f"editbtn_{note_id}"):
                                st.session_state.edit_note_id = note_id
                                st.rerun()
                            if cbtn2.button("🗑️ 删除", key=f"delbtn_{note_id}"):
                                if image_path:
                                    for p in image_path.split(","):
                                        if os.path.exists(p): os.remove(p)
                                run_query("DELETE FROM notes WHERE id=?", (note_id,))
                                st.rerun()
                    st.divider()
                    st.markdown("#### ✍️ 新增笔记")
                    new_content = st.text_area("写点什么", height=150, key="new_note_content")
                    new_imgs = st.file_uploader("插入图片（可多选）", accept_multiple_files=True, key="new_note_imgs")
                    if st.button("🚀 保存到本章"):
                        if new_content.strip():
                            paths = save_uploaded_images(new_imgs)
                            img_str = ",".join(paths) if paths else None
                            run_query(
                                "INSERT INTO notes (category, chapter, content, date, image_path) VALUES (?,?,?,?,?)",
                                (sel_c, sel_t, new_content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), img_str))
                            st.success("已保存")
                            st.rerun()
                        else:
                            st.warning("写点内容再保存吧")

# ============ 3. 错题本 ============
elif choice == "❌ 旧版：错题本":
    st.header("❌ 错题本")
    tab_add, tab_view = st.tabs(["➕ 录入错题", "📚 查看错题"])

    with tab_add:
        cats = get_all_categories()
        c1, c2, c3 = st.columns(3)
        with c1:
            category = st.selectbox("学科", ["新建学科..."] + cats)
            if category == "新建学科...":
                category = st.text_input("输入新学科名", autocomplete="off")
        with c2:
            chapter_options = [c[0] for c in get_chapters(category)] if category else []
            title = st.selectbox("关联章节（可选）", ["不关联"] + chapter_options + ["其他..."])
            if title == "其他...":
                title = st.text_input("输入章节名", autocomplete="off")
            elif title == "不关联":
                title = "未分类"
        with c3:
            topic = st.text_input("知识点 / 题型名", autocomplete="off")
        st.markdown("### 🧩 结构化拆解（可选，不想填的留空即可）")
        ca, cb = st.columns(2)
        with ca:
            q1 = st.text_input("1. 题型", autocomplete="off")
            q2 = st.text_input("2. 特点", autocomplete="off")
            q3 = st.text_input("3. 切入点", autocomplete="off")
        with cb:
            q5 = st.text_input("5. 知识点用法", autocomplete="off")
            q6 = st.text_input("6. 坑点", autocomplete="off")
        q4 = st.text_area("4. 通用 SOP / 解题过程")
        q7 = st.text_input("7. 整体流程 (一句话总结)", autocomplete="off")
        content = f"**1. 题型**\n\n{q1}\n\n**2. 特点**\n\n{q2}\n\n**3. 切入点**\n\n{q3}\n\n**4. 通用 SOP**\n\n{q4}\n\n**5. 用法**\n\n{q5}\n\n**6. 坑点**\n\n{q6}\n\n**7. 流程**\n\n{q7}"
        files = st.file_uploader("🖼️ 例题/补充 (多选)", accept_multiple_files=True)
        if st.button("🚀 保存错题"):
            if category and topic:
                paths = save_uploaded_images(files)
                img_str = ",".join(paths) if paths else None
                run_query(
                    "INSERT INTO entries (type, category, title, topic, content, date, image_path) VALUES (?,?,?,?,?,?,?)",
                    ("题型SOP归纳", category, title, topic, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), img_str))
                st.success("保存成功！")
            else:
                st.error("学科和知识点/题型名必填")

    with tab_view:
        archived_cats = get_archived_categories()
        with st.expander(f"📦 已收起的学科（{len(archived_cats)} 个，点这里可以恢复）"):
            if not archived_cats:
                st.caption("暂无已收起学科。")
            else:
                for cat in archived_cats:
                    rc1, rc2 = st.columns([4, 1])
                    with rc1:
                        st.write(cat)
                    with rc2:
                        if st.button("恢复", key=f"restore_{cat}"):
                            restore_category(cat)
                            st.success(f"已恢复：{cat}")
                            st.rerun()
        kw = st.text_input("🔍 全局搜索", autocomplete="off")
        if len(archived_cats) == 0:
            query = "SELECT * FROM entries WHERE (category LIKE ? OR title LIKE ? OR content LIKE ? OR topic LIKE ?) ORDER BY id DESC"
            params = (f'%{kw}%', f'%{kw}%', f'%{kw}%', f'%{kw}%')
        else:
            placeholders = ",".join(["?"] * len(archived_cats))
            query = f"""SELECT * FROM entries WHERE (category LIKE ? OR title LIKE ? OR content LIKE ? OR topic LIKE ?)
                        AND (category IS NULL OR category NOT IN ({placeholders})) ORDER BY id DESC"""
            params = (f'%{kw}%', f'%{kw}%', f'%{kw}%', f'%{kw}%', *archived_cats)
        results = run_query(query, params, fetch=True)
        for r in results:
            entry_id = r[0]
            with st.expander(f"【{r[2]} | {r[3]}】{r[8] if r[8] else '未分类'} [ID:{entry_id}] — {r[6]}"):
                if st.session_state.edit_id == entry_id:
                    new_con = st.text_area("修改正文", r[4], height=250, key=f"con_{entry_id}")
                    cc1, cc2 = st.columns(2)
                    with cc1:
                        if st.button("💾 保存修改", key=f"save_{entry_id}"):
                            run_query("UPDATE entries SET content=? WHERE id=?", (new_con, entry_id))
                            st.session_state.edit_id = None
                            st.rerun()
                    with cc2:
                        if st.button("❌ 取消", key=f"cancel_{entry_id}"):
                            st.session_state.edit_id = None
                            st.rerun()
                else:
                    st.markdown(r[4] if r[4] else "*(无正文)*")
                    if r[7]:
                        for p in r[7].split(','):
                            if os.path.exists(p): st.image(p, use_container_width=True)
                    st.divider()
                    st.markdown("#### 🔄 温故而知新 (复习打卡)")
                    reviews = run_query("SELECT review_date, insight FROM reviews WHERE entry_id=? ORDER BY id ASC", (entry_id,), fetch=True)
                    if reviews:
                        for rev_date, insight in reviews:
                            st.info(f"📅 **{rev_date}**\n\n💡 {insight}")
                    else:
                        st.caption("暂无打卡记录。")
                    new_insight = st.text_area("今天复习有了什么新启发？", key=f"rev_{entry_id}_{len(reviews)}")
                    if st.button("✅ 记录本次复习", key=f"revbtn_{entry_id}_{len(reviews)}"):
                        if new_insight.strip():
                            run_query("INSERT INTO reviews (entry_id, review_date, insight) VALUES (?, ?, ?)",
                                       (entry_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), new_insight))
                            st.success("打卡成功")
                            st.rerun()
                    st.divider()
                    eb1, eb2, _ = st.columns([1, 1, 8])
                    if eb1.button("🖊️ 编辑", key=f"edit_{entry_id}"):
                        st.session_state.edit_id = entry_id
                        st.rerun()
                    if eb2.button("🗑️ 删除", key=f"del_{entry_id}"):
                        if r[7]:
                            for p in r[7].split(','):
                                if os.path.exists(p): os.remove(p)
                        run_query("DELETE FROM entries WHERE id=?", (entry_id,))
                        run_query("DELETE FROM reviews WHERE entry_id=?", (entry_id,))
                        st.rerun()

# ============ 4. 新版：学科大纲（章节+知识点） ============
elif choice == "🧭 新版：学科大纲（章节+知识点）":
    st.header("🧭 新版：学科大纲")
    st.caption("三级目录：学科 → 章节 → 知识点/小节。先把框架搭出来，再去「📚 新版：学习笔记」里按知识点/方法总结/例题/错题/重点题型往里填内容。")

    subjects = get_subjects()
    subject_names = [s[1] for s in subjects]
    mode = st.radio("操作", ["新建学科", "管理已有学科大纲"], horizontal=True, key="new_outline_mode")

    if mode == "新建学科":
        new_subj = st.text_input("学科名称", autocomplete="off", key="new_subj_name")
        chapter_text = st.text_area(
            "章节目录（每行一个章节名，按顺序输入）",
            height=200,
            placeholder="例如：\n第1章 随机事件与概率\n第2章 随机变量及其分布\n第3章 多维随机变量\n...",
            key="new_subj_chapters")
        if st.button("🚀 创建学科大纲", key="create_new_subj"):
            if new_subj.strip() and chapter_text.strip():
                if new_subj.strip() in subject_names:
                    st.error("这个学科名已经存在，去「管理已有学科大纲」里继续编辑吧。")
                else:
                    subj_id = create_subject(new_subj.strip())
                    lines = [l.strip() for l in chapter_text.split("\n") if l.strip()]
                    add_chapters_bulk(subj_id, lines)
                    st.success(f"已创建「{new_subj}」，共 {len(lines)} 个章节，可以去「📚 新版：学习笔记」开始填内容了。")
            else:
                st.error("学科名称和章节目录都要填。")
    else:
        if not subjects:
            st.info("还没有任何新版学科，先创建一个吧。")
        else:
            sel_name = st.selectbox("选择学科", ["请选择"] + subject_names, key="manage_subj_sel")
            if sel_name != "请选择":
                subj_id = next(s[0] for s in subjects if s[1] == sel_name)
                chapters = get_outline_chapters(subj_id)
                st.markdown("#### 当前章节大纲")
                if not chapters:
                    st.caption("还没有章节，先在下面加一个。")
                else:
                    for cid, title, order, summ in chapters:
                        st.markdown(
                            f"<div class='outline-card'><b>{order}. {html.escape(title)}</b><br>"
                            f"{html.escape(summ) if summ else '（还没写一句话总结，可以先跳过）'}</div>",
                            unsafe_allow_html=True)
                st.divider()

                st.markdown("#### 追加新章节")
                add_text = st.text_area("新增章节（每行一个，会追加到已有章节后面）", height=100, key="add_outline_chapters")
                if st.button("➕ 追加章节", key="add_outline_chapters_btn"):
                    if add_text.strip():
                        lines = [l.strip() for l in add_text.split("\n") if l.strip()]
                        add_chapters_bulk(subj_id, lines)
                        st.success(f"已追加 {len(lines)} 个章节")
                        st.rerun()

                if chapters:
                    st.divider()
                    st.markdown("#### 管理某一章节的小节 / 知识点目录")
                    chap_titles = [c[1] for c in chapters]
                    sel_chap_title = st.selectbox("选择章节", chap_titles, key="manage_chapter_sel")
                    cur_chap = next(c for c in chapters if c[1] == sel_chap_title)
                    chap_id = cur_chap[0]

                    chap_summary = st.text_area("这一章的一句话总结", value=cur_chap[3] or "", height=68, key=f"chap_summ_{chap_id}")
                    if st.button("💾 保存章节总结", key=f"save_chap_summ_{chap_id}"):
                        update_chapter_summary(chap_id, chap_summary)
                        st.success("已保存")
                        st.rerun()

                    st.markdown("##### 当前小节 / 知识点")
                    sections = get_outline_sections(chap_id)
                    if not sections:
                        st.caption("这一章还没有小节，在下面加一个。")
                    else:
                        for sid, stitle, sorder, ssumm in sections:
                            st.markdown(
                                f"<div class='outline-card'><b>{sorder}. {html.escape(stitle)}</b><br>"
                                f"{html.escape(ssumm) if ssumm else '（还没写一句话总结，可以先跳过）'}</div>",
                                unsafe_allow_html=True)

                    add_sec_text = st.text_area("新增小节 / 知识点（每行一个）", height=100, key=f"add_sections_{chap_id}")
                    if st.button("➕ 追加小节", key=f"add_sections_btn_{chap_id}"):
                        if add_sec_text.strip():
                            lines = [l.strip() for l in add_sec_text.split("\n") if l.strip()]
                            add_sections_bulk(chap_id, lines)
                            st.success(f"已追加 {len(lines)} 个小节")
                            st.rerun()

                    if sections:
                        st.markdown("##### 编辑某个小节的一句话总结")
                        sec_titles = [s[1] for s in sections]
                        sel_sec_title = st.selectbox("选择小节", sec_titles, key=f"edit_sec_sel_{chap_id}")
                        cur_sec = next(s for s in sections if s[1] == sel_sec_title)
                        sec_summary = st.text_area("一句话总结", value=cur_sec[3] or "", height=68, key=f"sec_summ_{cur_sec[0]}")
                        if st.button("💾 保存小节总结", key=f"save_sec_summ_{cur_sec[0]}"):
                            update_section_summary(cur_sec[0], sec_summary)
                            st.success("已保存")
                            st.rerun()

# ============ 5. 新版：学习笔记（按类型） ============
elif choice == "📚 新版：学习笔记（按类型）":
    st.header("📚 新版：学习笔记")
    st.caption("选好章节/小节后，往下面 5 个标签页里分别填知识点、方法总结、例题、错题、重点题型整理。")

    subjects = get_subjects()
    subject_names = [s[1] for s in subjects]
    if not subjects:
        st.info("还没有新版学科，先去「🧭 新版：学科大纲」搭一个框架。")
    else:
        sel_name = st.selectbox("选择学科", ["请选择"] + subject_names, key="study_subj_sel")
        if sel_name != "请选择":
            subj_id = next(s[0] for s in subjects if s[1] == sel_name)
            chapters = get_outline_chapters(subj_id)
            if not chapters:
                st.warning("这门学科还没有章节，先去「🧭 新版：学科大纲」里补章节目录。")
            else:
                chap_titles = [c[1] for c in chapters]
                sel_chap_title = st.selectbox("选择章节", chap_titles, key="study_chap_sel")
                cur_chap = next(c for c in chapters if c[1] == sel_chap_title)
                chap_id = cur_chap[0]
                if cur_chap[3]:
                    st.caption(f"本章总结：{cur_chap[3]}")

                sections = get_outline_sections(chap_id)
                sec_options = ["整章（不挂具体小节）"] + [s[1] for s in sections]
                sel_sec_option = st.selectbox("选择小节", sec_options, key="study_sec_sel")
                if sel_sec_option == "整章（不挂具体小节）":
                    section_id = "__chapter__"
                    sec_summary = ""
                else:
                    cur_sec = next(s for s in sections if s[1] == sel_sec_option)
                    section_id = cur_sec[0]
                    sec_summary = cur_sec[3]
                if sec_summary:
                    st.caption(f"本节总结：{sec_summary}")

                st.divider()
                tabs = st.tabs(ITEM_TYPES)
                for tab, item_type in zip(tabs, ITEM_TYPES):
                    with tab:
                        items = get_study_items(chap_id, section_id, item_type)
                        if not items:
                            st.caption(f"这里还没有「{item_type}」，在下面写下第一条吧。")
                        for item_id, i_title, i_content, image_path, idate in items:
                            if st.session_state.edit_item_id == item_id:
                                new_content = st.text_area("编辑这条内容", value=i_content, height=150, key=f"edititem_{item_id}")
                                ic1, ic2 = st.columns(2)
                                with ic1:
                                    if st.button("💾 保存", key=f"saveitem_{item_id}"):
                                        update_study_item(item_id, new_content)
                                        st.session_state.edit_item_id = None
                                        st.rerun()
                                with ic2:
                                    if st.button("❌ 取消", key=f"cancelitem_{item_id}"):
                                        st.session_state.edit_item_id = None
                                        st.rerun()
                            else:
                                title_html = f"<b>{html.escape(i_title)}</b><br>" if i_title else ""
                                st.markdown(
                                    f"<div class='note-card'><span class='note-card-date'>{html.escape(idate)}</span>"
                                    f"{title_html}{simple_markdown_to_html(i_content)}</div>",
                                    unsafe_allow_html=True)
                                if image_path:
                                    for p in image_path.split(","):
                                        if os.path.exists(p):
                                            st.image(p, use_container_width=True)
                                ibtn1, ibtn2, _ = st.columns([1, 1, 8])
                                if ibtn1.button("🖊️ 编辑", key=f"editbtn_item_{item_id}"):
                                    st.session_state.edit_item_id = item_id
                                    st.rerun()
                                if ibtn2.button("🗑️ 删除", key=f"delbtn_item_{item_id}"):
                                    delete_study_item(item_id, image_path)
                                    st.rerun()

                                with st.expander("🔄 温故而知新（复习打卡）"):
                                    revs = get_item_reviews(item_id)
                                    if revs:
                                        for rev_date, insight in revs:
                                            st.info(f"📅 **{rev_date}**\n\n💡 {insight}")
                                    else:
                                        st.caption("暂无打卡记录。")
                                    new_insight = st.text_area("今天复习有了什么新启发？", key=f"itemrev_{item_id}_{len(revs)}")
                                    if st.button("✅ 记录本次复习", key=f"itemrevbtn_{item_id}_{len(revs)}"):
                                        if new_insight.strip():
                                            add_item_review(item_id, new_insight)
                                            st.success("打卡成功")
                                            st.rerun()

                        st.divider()
                        st.markdown(f"#### ✍️ 新增「{item_type}」")
                        new_title = st.text_input("标题（可选）", autocomplete="off", key=f"newitem_title_{item_type}")
                        new_content = st.text_area("正文内容", height=150, key=f"newitem_content_{item_type}")
                        new_imgs = st.file_uploader("插入图片（可多选）", accept_multiple_files=True, key=f"newitem_imgs_{item_type}")
                        if st.button(f"🚀 保存到「{item_type}」", key=f"newitem_save_{item_type}"):
                            if new_content.strip():
                                paths = save_uploaded_images(new_imgs)
                                img_str = ",".join(paths) if paths else None
                                save_section_id = None if section_id == "__chapter__" else section_id
                                add_study_item(subj_id, chap_id, save_section_id, item_type, new_title.strip(), new_content, img_str)
                                st.success("已保存")
                                st.rerun()
                            else:
                                st.warning("写点内容再保存吧")

# ============ 6. 学科秘籍（导出） ============
elif choice == "📘 学科秘籍（导出）":
    st.header("📘 学科秘籍")
    source = st.radio("数据来源", ["旧版学科", "新版学科大纲"], horizontal=True, key="export_source")
    if source == "新版学科大纲":
        subjects = get_subjects()
        subject_names = [s[1] for s in subjects]
        if not subjects:
            st.info("还没有新版学科，先去「🧭 新版：学科大纲」搭一个框架。")
        else:
            sel_name = st.selectbox("选择学科", ["请选择"] + subject_names, key="export_subj_sel")
            if sel_name != "请选择":
                subj_id = next(s[0] for s in subjects if s[1] == sel_name)
                chapters = get_outline_chapters(subj_id)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                parts = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(sel_name)} 学科秘籍</title>
<style>
body{{margin:0;padding:40px;font-family:"Microsoft YaHei",Arial,sans-serif;background:#111827;color:#f5f7ff;line-height:1.75;}}
.container{{max-width:980px;margin:0 auto;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.14);border-radius:18px;padding:36px;}}
h1{{color:#fff;font-size:32px;}} h2{{margin-top:36px;border-bottom:1px solid rgba(255,255,255,0.22);padding-bottom:8px;}}
h3{{margin-top:22px;color:#00e5ff;}} h4{{margin-top:16px;color:rgba(255,255,255,0.85);}}
.card{{background:rgba(0,0,0,0.28);border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:16px 20px;margin:14px 0;}}
.meta{{color:rgba(255,255,255,0.6);font-size:12px;margin-bottom:8px;}}
.outline{{background:rgba(0,229,255,0.1);border-left:4px solid #00e5ff;padding:10px 14px;margin:8px 0;border-radius:8px;}}
img{{max-width:100%;border-radius:12px;margin:10px 0;}}
</style></head><body><div class="container">
<h1>{html.escape(sel_name)} 学科秘籍</h1>
<p class="meta">生成时间：{html.escape(now)}</p><hr>"""]

                if chapters:
                    parts.append("<h2>学科大纲</h2>")
                    for cid, title, order, summ in chapters:
                        parts.append(f"<div class='outline'><b>{order}. {html.escape(title)}</b><br>{html.escape(summ or '')}</div>")

                for cid, title, order, summ in chapters:
                    parts.append(f"<h2>{html.escape(title)}</h2>")
                    sections = get_outline_sections(cid)
                    section_scopes = [("__chapter__", "本章整体")] + [(sid, stitle) for sid, stitle, sorder, ssumm in sections]
                    for section_id, section_label in section_scopes:
                        section_has_content = any(get_study_items(cid, section_id, t) for t in ITEM_TYPES)
                        if not section_has_content:
                            continue
                        if section_id != "__chapter__":
                            parts.append(f"<h3>{html.escape(section_label)}</h3>")
                        for item_type in ITEM_TYPES:
                            items = get_study_items(cid, section_id, item_type)
                            if not items:
                                continue
                            parts.append(f"<h4>{html.escape(item_type)}</h4>")
                            for item_id, i_title, i_content, image_path, idate in items:
                                title_html = f"<b>{html.escape(i_title)}</b><br>" if i_title else ""
                                parts.append(f"<div class='card'><div class='meta'>{html.escape(idate)}</div>{title_html}{simple_markdown_to_html(i_content or '')}")
                                if image_path:
                                    for p in image_path.split(","):
                                        uri = image_to_data_uri(p.strip())
                                        if uri: parts.append(f"<img src='{uri}'>")
                                parts.append("</div>")

                parts.append("</div></body></html>")
                html_text = "\n".join(parts)
                file_name = safe_filename(sel_name) + "_新版学科秘籍.html"
                file_path = os.path.join("exports", file_name)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(html_text)
                st.download_button("⬇️ 下载可离线打开的 HTML 学科秘籍", data=html_text.encode("utf-8"),
                                    file_name=file_name, mime="text/html")
                st.caption("下载后的 HTML 文件已经内嵌图片，双击即可离线打开。")
    else:
        cats = get_all_categories()
        if not cats:
            st.info("暂无内容")
        else:
            selected_subject = st.selectbox("选择学科", ["请选择"] + cats)
            if selected_subject != "请选择":
                chapters = get_chapters(selected_subject)
                notes_all = run_query("SELECT chapter, content, date, image_path FROM notes WHERE category=? ORDER BY chapter, id ASC", (selected_subject,), fetch=True)
                entries_all = run_query("SELECT * FROM entries WHERE category=? ORDER BY title, topic", (selected_subject,), fetch=True)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                parts = [f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(selected_subject)} 学科秘籍</title>
<style>
body{{margin:0;padding:40px;font-family:"Microsoft YaHei",Arial,sans-serif;background:#111827;color:#f5f7ff;line-height:1.75;}}
.container{{max-width:980px;margin:0 auto;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.14);border-radius:18px;padding:36px;}}
h1{{color:#fff;font-size:32px;}} h2{{margin-top:36px;border-bottom:1px solid rgba(255,255,255,0.22);padding-bottom:8px;}}
.card{{background:rgba(0,0,0,0.28);border:1px solid rgba(255,255,255,0.12);border-radius:14px;padding:16px 20px;margin:14px 0;}}
.meta{{color:rgba(255,255,255,0.6);font-size:12px;margin-bottom:8px;}}
.outline{{background:rgba(0,229,255,0.1);border-left:4px solid #00e5ff;padding:10px 14px;margin:8px 0;border-radius:8px;}}
img{{max-width:100%;border-radius:12px;margin:10px 0;}}
</style></head><body><div class="container">
<h1>{html.escape(selected_subject)} 学科秘籍</h1>
<p class="meta">生成时间：{html.escape(now)}</p><hr>"""]

                if chapters:
                    parts.append("<h2>学科大纲</h2>")
                    for title, order, dep, summ in chapters:
                        dep_text = f"　依赖：<b>{html.escape(dep)}</b>" if dep else ""
                        parts.append(f"<div class='outline'><b>{order}. {html.escape(title)}</b>{dep_text}<br>{html.escape(summ or '')}</div>")

                notes_by_chapter = {}
                for chapter, content, ndate, image_path in notes_all:
                    notes_by_chapter.setdefault(chapter, []).append((content, ndate, image_path))

                for title, order, dep, summ in chapters:
                    parts.append(f"<h2>{html.escape(title)}</h2>")
                    for content, ndate, image_path in notes_by_chapter.get(title, []):
                        parts.append(f"<div class='card'><div class='meta'>{html.escape(ndate)}</div>{simple_markdown_to_html(content)}")
                        if image_path:
                            for p in image_path.split(","):
                                uri = image_to_data_uri(p.strip())
                                if uri: parts.append(f"<img src='{uri}'>")
                        parts.append("</div>")
                    mistakes = [e for e in entries_all if e[3] == title]
                    if mistakes:
                        parts.append("<h3>本章错题</h3>")
                        for e in mistakes:
                            parts.append(f"<div class='card'><div class='meta'>{html.escape(e[8] or '')} ｜ {html.escape(e[6] or '')}</div>{simple_markdown_to_html(e[4] or '')}")
                            if e[7]:
                                for p in e[7].split(","):
                                    uri = image_to_data_uri(p.strip())
                                    if uri: parts.append(f"<img src='{uri}'>")
                            parts.append("</div>")

                parts.append("</div></body></html>")
                html_text = "\n".join(parts)
                file_name = safe_filename(selected_subject) + "_学科秘籍.html"
                file_path = os.path.join("exports", file_name)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(html_text)
                st.download_button("⬇️ 下载可离线打开的 HTML 学科秘籍", data=html_text.encode("utf-8"),
                                    file_name=file_name, mime="text/html")
                st.caption("下载后的 HTML 文件已经内嵌图片，双击即可离线打开。")
                st.divider()
                render_outline(selected_subject)
                st.subheader("网页内预览")
                for title, order, dep, summ in chapters:
                    st.markdown(f"## {title}")
                    for content, ndate, image_path in notes_by_chapter.get(title, []):
                        st.markdown(
                            f"<div class='note-card'><span class='note-card-date'>{html.escape(ndate)}</span>{simple_markdown_to_html(content)}</div>",
                            unsafe_allow_html=True)
                        if image_path:
                            for p in image_path.split(","):
                                if os.path.exists(p): st.image(p, use_container_width=True)
                    mistakes = [e for e in entries_all if e[3] == title]
                    for e in mistakes:
                        with st.expander(f"❌ {e[8] or '未分类'}"):
                            st.markdown(e[4] or "")
                            if e[7]:
                                for p in e[7].split(","):
                                    if os.path.exists(p): st.image(p, use_container_width=True)
