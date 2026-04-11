"""
update_procurement_templates.py
Removes inline <style> blocks from procurement templates and
replaces them with <link> references to static CSS files.
"""
import re, pathlib

BASE = pathlib.Path(r"c:\Users\BRADSOL\OneDrive - bradsol.com\Sridhar_Bradsol_Projects\Reconcilation_Project\3-way-po-recon")
T = BASE / "templates" / "procurement"

def read(p):
    return p.read_text(encoding="utf-8")

def write(p, content):
    p.write_text(content, encoding="utf-8")
    print(f"  Updated {p.name}")

# ── 1. proc_base.html: remove inline <style>...</style> block, add link tags ──
p = T / "proc_base.html"
html = read(p)
# Remove the entire <style>...</style> block between the extra_css endblock and </head>
# We keep {% block extra_head %}{% endblock %} and {% block extra_css %}{% endblock %} in place
# and insert the two link tags right after them.
old = re.search(r"(\s*\{%\s*block extra_css\s*%\}\{%\s*endblock\s*%\})\s*<style>.*?</style>",
                html, re.DOTALL)
if old:
    replacement = (
        "\n  {% block extra_css %}{% endblock %}"
        "\n  <link rel=\"stylesheet\" href=\"{% static 'css/procurement/theme.css' %}\">"
        "\n  <link rel=\"stylesheet\" href=\"{% static 'css/procurement/proc-layout.css' %}\">"
    )
    html = html[:old.start(1)] + replacement + html[old.end():]
    write(p, html)
else:
    print(f"  WARN: could not find style block in proc_base.html")

# ── 2. home.html: keep SweetAlert2 link, replace <style>...</style> with link ──
p = T / "home.html"
html = read(p)
pattern = r"({% block extra_css %}.*?<link[^>]+sweetalert2[^>]+>)\s*<style>.*?</style>\s*({% endblock %})"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        m.group(1) + "\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/home.css' %}\">\n"
        + m.group(2)
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    print(f"  WARN: could not find pattern in home.html")

# ── 3. request_workspace.html: extra_head -> extra_css, style -> link ──
p = T / "request_workspace.html"
html = read(p)
pattern = r"{% block extra_head %}\s*<style>.*?</style>\s*{% endblock %}"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        "{% block extra_css %}\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/workspace.css' %}\">\n"
        "{% endblock %}"
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    print(f"  WARN: could not find pattern in request_workspace.html")

# ── 4. request_list.html: extra_head -> extra_css, style -> link ──
p = T / "request_list.html"
html = read(p)
pattern = r"{% block extra_head %}\s*<style>.*?</style>\s*{% endblock %}"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        "{% block extra_css %}\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/request-list.css' %}\">\n"
        "{% endblock %}"
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    print(f"  WARN: could not find pattern in request_list.html")

# ── 5. configurations.html: remove <style>...</style> from extra_css block ──
p = T / "configurations.html"
html = read(p)
pattern = r"({% block extra_css %})\s*<style>.*?</style>\s*({% endblock %})"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        m.group(1) + "\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/configurations.css' %}\">\n"
        + m.group(2)
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    print(f"  WARN: could not find pattern in configurations.html")

# ── 6. procurement_dashboard.html: remove <style>...</style> from extra_css block ──
p = T / "procurement_dashboard.html"
html = read(p)
pattern = r"({% block extra_css %})\s*<style>.*?</style>\s*({% endblock %})"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        m.group(1) + "\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/procurement-dashboard.css' %}\">\n"
        + m.group(2)
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    print(f"  WARN: could not find pattern in procurement_dashboard.html")

# ── 7. request_create_hvac.html: keep SweetAlert2 link, replace <style> with link ──
p = T / "request_create_hvac.html"
html = read(p)
pattern = r"({% block extra_css %}\s*<!-- SweetAlert2 -->\s*<link[^>]+sweetalert2[^>]+>)\s*<style>.*?</style>\s*({% endblock %})"
m = re.search(pattern, html, re.DOTALL)
if m:
    new_block = (
        m.group(1) + "\n"
        "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/request-create.css' %}\">\n"
        + m.group(2)
    )
    html = html[:m.start()] + new_block + html[m.end():]
    write(p, html)
else:
    # Try without the comment
    pattern2 = r"({% block extra_css %}\s*<link[^>]+sweetalert2[^>]+>)\s*<style>.*?</style>\s*({% endblock %})"
    m2 = re.search(pattern2, html, re.DOTALL)
    if m2:
        new_block = (
            m2.group(1) + "\n"
            "<link rel=\"stylesheet\" href=\"{% static 'css/procurement/request-create.css' %}\">\n"
            + m2.group(2)
        )
        html = html[:m2.start()] + new_block + html[m2.end():]
        write(p, html)
    else:
        print(f"  WARN: could not find pattern in request_create_hvac.html")

print("Done.")
