#!/usr/bin/env python3
"""
Generate blog/index.html by scanning all blog post HTML files.
Reads title, date, category, description from meta/og tags.
Idempotent — can be run any number of times.
"""
import os, re, glob, stat
from pathlib import Path
from datetime import datetime

BLOG_DIR = "/var/www/html/blog"
INDEX_FILE = os.path.join(BLOG_DIR, "index.html")

# Read the existing index to extract the template (everything before and after posts)
TEMPLATE_BEFORE_MARKER = '<section class="posts-container">'
TEMPLATE_AFTER_MARKER = '</section>'

# The frame (nav, sprite, everything outside the post list) comes from the
# moltrust-web checkout, which deploy.sh sets to the deployed commit (ops
# BLOG-GEN-NAV). blog/index.html is not part of the web deploy, so a frame read
# from the served file never picks up a nav change. If the checkout file cannot
# be used, the served frame stays and the run says why; the post list is
# written either way. None = frame from the served file.
FRAME_FILE = "/home/moltstack/moltrust-web/blog/index.html"
FRAME_MAX = 1 << 20


def read_frame_source(path):
    """(text, None) or (None, reason). The cron runs this as root on a file in
    a user's checkout, so only a plain file of the checkout's owner counts: no
    symlink at the end, no second hard link, no larger than FRAME_MAX. Such a
    file holds nothing its owner could not deploy anyway."""
    checkout = os.path.dirname(os.path.dirname(path))
    try:
        top = os.lstat(checkout)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        return None, f"frame source not readable ({type(e).__name__})"
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(top.st_mode) or not stat.S_ISREG(st.st_mode) \
                or st.st_nlink != 1 or st.st_uid != top.st_uid or st.st_size > FRAME_MAX:
            return None, "frame source refused (not a plain file of the checkout owner)"
        chunks, n = [], 0
        while n <= FRAME_MAX:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
            n += len(b)
        if n > FRAME_MAX:
            return None, "frame source refused (too large)"
    finally:
        os.close(fd)
    return b"".join(chunks).decode("utf-8", "replace"), None


def split_frame(text):
    """(before, after) around the post list, or None without the markers."""
    i = text.find(TEMPLATE_BEFORE_MARKER)
    if i < 0:
        return None
    j = text.find(TEMPLATE_AFTER_MARKER, i + len(TEMPLATE_BEFORE_MARKER))
    if j < 0:
        return None
    return text[:i + len(TEMPLATE_BEFORE_MARKER)], text[j:]


def extract_meta(html_path):
    """Extract metadata from a blog post HTML file."""
    try:
        content = open(html_path, encoding='utf-8', errors='replace').read()
    except Exception:
        return None

    filename = os.path.basename(html_path)
    if filename == "index.html":
        return None

    # Skip redirect stubs (meta-refresh or tiny files)
    if len(content) < 500 and ("http-equiv" in content or "301 Moved" in content):
        return None

    def find_meta(name):
        # Try og: first (both attribute orders), then regular meta
        m = re.search(rf'<meta\s+property="og:{name}"\s+content="([^"]*)"', content)
        if m:
            return m.group(1)
        m = re.search(rf'<meta\s+content="([^"]*)"\s+property="og:{name}"', content)
        if m:
            return m.group(1)
        m = re.search(rf'<meta\s+name="{name}"\s+content="([^"]*)"', content)
        if m:
            return m.group(1)
        m = re.search(rf'<meta\s+content="([^"]*)"\s+name="{name}"', content)
        if m:
            return m.group(1)
        return None

    # Title: og:title or <title> tag
    title = find_meta("title")
    if not title:
        m = re.search(r'<title>([^<]*)</title>', content)
        title = m.group(1).replace(" — MolTrust Blog", "").strip() if m else filename

    # Description
    desc = find_meta("description") or ""

    # Date: from article-meta or datePublished
    date_str = None
    m = re.search(r'"datePublished":\s*"([^"]*)"', content)
    if m:
        date_str = m.group(1)
    if not date_str:
        m = re.search(r'<time[^>]*>([^<]*)</time>', content)
        if m:
            date_str = m.group(1)
    if not date_str:
        m = re.search(r'<meta\s+itemprop="datePublished"\s+content="([^"]*)"', content)
        if m:
            date_str = m.group(1)
    if not date_str:
        # Fallback to file mtime
        date_str = datetime.fromtimestamp(os.path.getmtime(html_path)).strftime("%Y-%m-%d")

    # Parse date for sorting
    sort_date = None
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            sort_date = datetime.strptime(date_str.strip()[:19], fmt)
            break
        except ValueError:
            continue
    if not sort_date:
        sort_date = datetime.fromtimestamp(os.path.getmtime(html_path))

    # Display date
    display_date = sort_date.strftime("%B %d, %Y") if sort_date else date_str

    # Category: from article-tag span or keywords
    category = "Blog"
    m = re.search(r'<span class="article-tag">([^<]*)</span>', content)
    if m:
        category = m.group(1)

    # Read time: from existing span or estimate
    read_time = "5 min"
    m = re.search(r'<span class="read-time">([^<]*)</span>', content)
    if m:
        read_time = m.group(1)
    else:
        # Rough estimate: 200 words per minute
        text = re.sub(r'<[^>]+>', '', content)
        words = len(text.split())
        read_time = f"{max(2, words // 200)} min"

    # Clean description for HTML
    desc = desc.replace("&", "&amp;").replace('"', "&quot;")
    # Truncate description
    if len(desc) > 180:
        desc = desc[:177] + "..."

    return {
        "filename": filename,
        "title": title,
        "description": desc,
        "date": display_date,
        "sort_date": sort_date,
        "category": category,
        "read_time": read_time,
    }


def generate_posts_html(posts):
    """Generate the posts section HTML."""
    lines = []
    for p in posts:
        slug = p["filename"]
        lines.append(f"""
    <a href="/blog/{slug}">
      <article class="post-card">
        <div class="post-meta">
          <span class="post-tag">{p['category']}</span>
          <time>{p['date']}</time>
          <span class="read-time">{p['read_time']}</span>
        </div>
        <h2>{p['title']}</h2>
        <p>{p['description']}</p>
      </article>
    </a>""")
    return "\n".join(lines)


def main():
    # Read existing index for template
    if not os.path.exists(INDEX_FILE):
        print("ERROR: index.html not found")
        return

    index_content = open(INDEX_FILE, encoding='utf-8').read()

    # Find template boundaries
    before_idx = index_content.find(TEMPLATE_BEFORE_MARKER)
    if before_idx < 0:
        print("ERROR: posts-container marker not found")
        return

    # Find the closing </section> after posts-container
    after_idx = index_content.find(TEMPLATE_AFTER_MARKER, before_idx + len(TEMPLATE_BEFORE_MARKER))
    if after_idx < 0:
        print("ERROR: closing </section> not found")
        return

    template_before = index_content[:before_idx + len(TEMPLATE_BEFORE_MARKER)]
    template_after = index_content[after_idx:]
    if FRAME_FILE and os.path.abspath(FRAME_FILE) != os.path.abspath(INDEX_FILE):
        text, why = read_frame_source(FRAME_FILE)
        frame = split_frame(text) if text is not None else None
        if frame:
            template_before, template_after = frame
            print(f"frame: from {FRAME_FILE}")
        else:
            print(f"frame: kept the served frame ({why or 'post list markers missing in ' + FRAME_FILE})")

    # Scan all blog posts
    html_files = glob.glob(os.path.join(BLOG_DIR, "*.html"))
    posts = []
    for f in html_files:
        meta = extract_meta(f)
        if meta:
            posts.append(meta)

    # Sort by date (newest first)
    posts.sort(key=lambda p: p["sort_date"], reverse=True)

    print(f"Found {len(posts)} blog posts")
    for p in posts[:5]:
        print(f"  {p['sort_date'].strftime('%Y-%m-%d')} | {p['category']:12} | {p['title'][:60]}")
    if len(posts) > 5:
        print(f"  ... and {len(posts) - 5} more")

    # Generate new index
    posts_html = generate_posts_html(posts)
    new_index = template_before + "\n" + posts_html + "\n\n" + template_after

    # Write
    open(INDEX_FILE, 'w', encoding='utf-8').write(new_index)
    print(f"\nindex.html regenerated with {len(posts)} posts")


if __name__ == "__main__":
    main()
