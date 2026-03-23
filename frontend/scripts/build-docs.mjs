import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
const docsRoot = path.join(projectRoot, 'docs-md');
const outRoot = path.join(projectRoot, 'public', 'docs');

const siteTitle = 'WorkTool Console 文档';
const siteBase = '/docs';

const pages = [
  { slug: 'overview', file: '01-overview.md', title: '概览', description: '平台定位、能力与目标' },
  { slug: 'quickstart', file: '02-quickstart.md', title: '快速开始', description: '5分钟跑通链路' },
  { slug: 'core-concepts', file: '03-core-concepts.md', title: '核心概念', description: '机器人、规则、引擎、回调' },
  { slug: 'robot-callback', file: '04-robot-callback.md', title: '机器人与回调', description: '机器人添加与回调绑定' },
  { slug: 'rules', file: '05-rules.md', title: '规则配置', description: '匹配模式、优先级与模板' },
  { slug: 'ai-provider', file: '06-ai-provider.md', title: 'AI回复引擎', description: '引擎配置、测试与常见问题' },
  { slug: 'forwarding', file: '07-forwarding.md', title: '消息转发', description: '转发规则与日志排查' },
  { slug: 'monitoring', file: '08-monitoring.md', title: '消息监控与排障', description: '监控视图与常见异常' },
  { slug: 'admin', file: '09-admin.md', title: '管理员手册', description: '用户管理与安全建议' },
  { slug: 'deploy', file: '10-deploy.md', title: '部署运维', description: '部署、时区、升级建议' },
  { slug: 'faq', file: '11-faq.md', title: '常见问题', description: '高频问题与处理方式' },
];

function esc(s = '') {
  return String(s)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function navHtml(current) {
  return pages
    .map((p) => `<a class=\"nav-item${p.slug === current ? ' active' : ''}\" href=\"${siteBase}/${p.slug}.html\">${esc(p.title)}</a>`)
    .join('');
}

function pagerHtml(index) {
  const prev = index > 0 ? pages[index - 1] : null;
  const next = index < pages.length - 1 ? pages[index + 1] : null;
  return `
  <div class=\"pager\">\n
    ${prev ? `<a class=\"pager-link\" href=\"${siteBase}/${prev.slug}.html\">← ${esc(prev.title)}</a>` : '<span></span>'}
    ${next ? `<a class=\"pager-link\" href=\"${siteBase}/${next.slug}.html\">${esc(next.title)} →</a>` : '<span></span>'}
  </div>`;
}

function renderPage({ title, description, slug, articleHtml, pageIndex, headings }) {
  const searchData = JSON.stringify(
    pages.map((p) => ({
      title: p.title,
      slug: p.slug,
      description: p.description,
    }))
  );
  const toc = headings
    .filter((h) => h.depth <= 3)
    .map((h) => `<a class=\"toc-item toc-${h.depth}\" href=\"#${esc(h.id)}\">${esc(h.text)}</a>`)
    .join('');

  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${esc(title)} - ${esc(siteTitle)}</title>
  <meta name="description" content="${esc(description)}" />
  <link rel="canonical" href="${siteBase}/${slug}.html" />
  <link rel="stylesheet" href="${siteBase}/assets/docs.css" />
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <a class="brand" href="${siteBase}/index.html">${esc(siteTitle)}</a>
      <input id="doc-search" class="search" type="search" placeholder="搜索文档..." />
      <div id="search-results" class="search-results"></div>
      <nav class="nav">${navHtml(slug)}</nav>
    </aside>
    <main class="main">
      <article class="doc">
        ${articleHtml}
      </article>
      ${pagerHtml(pageIndex)}
    </main>
    <aside class="toc-wrap">
      <div class="toc-title">本页目录</div>
      <nav class="toc">${toc || '<span class="toc-empty">无</span>'}</nav>
    </aside>
  </div>
  <script>
    const DOC_ITEMS = ${searchData};
    const input = document.getElementById('doc-search');
    const resultEl = document.getElementById('search-results');
    function renderResults(keyword) {
      const q = (keyword || '').trim().toLowerCase();
      if (!q) {
        resultEl.innerHTML = '';
        return;
      }
      const list = DOC_ITEMS
        .filter((d) => (d.title + ' ' + (d.description || '')).toLowerCase().includes(q))
        .slice(0, 8);
      if (!list.length) {
        resultEl.innerHTML = '<div class="result-empty">没有匹配结果</div>';
        return;
      }
      resultEl.innerHTML = list
        .map((d) => '<a class="result-item" href="${siteBase}/' + d.slug + '.html"><div class="result-title">' + d.title + '</div><div class="result-desc">' + (d.description || '') + '</div></a>')
        .join('');
    }
    input?.addEventListener('input', (e) => renderResults(e.target.value));
  </script>
</body>
</html>`;
}

async function main() {
  await fs.mkdir(path.join(outRoot, 'assets'), { recursive: true });

  const searchIndex = [];

  for (let i = 0; i < pages.length; i++) {
    const p = pages[i];
    const mdPath = path.join(docsRoot, p.file);
    const md = await fs.readFile(mdPath, 'utf8');
    const tokens = marked.lexer(md);
    const headings = tokens
      .filter((t) => t.type === 'heading')
      .map((t) => ({
        depth: t.depth,
        text: t.text,
        id: String(t.text)
          .trim()
          .toLowerCase()
          .replace(/\s+/g, '-')
          .replace(/[^\u4e00-\u9fa5a-z0-9\-_]/g, ''),
      }));

    const renderer = new marked.Renderer();
    renderer.heading = ({ tokens: headingTokens, depth }) => {
      const text = marked.Parser.parseInline(headingTokens);
      const plainText = headingTokens.map((x) => x.raw || '').join('').replace(/[#`*_~\[\]()]/g, '');
      const id = String(plainText)
        .trim()
        .toLowerCase()
        .replace(/\s+/g, '-')
        .replace(/[^\u4e00-\u9fa5a-z0-9\-_]/g, '');
      return `<h${depth} id="${esc(id)}">${text}</h${depth}>`;
    };

    const articleHtml = marked.parse(md, { renderer });
    const html = renderPage({
      title: p.title,
      description: p.description,
      slug: p.slug,
      articleHtml,
      pageIndex: i,
      headings,
    });

    await fs.writeFile(path.join(outRoot, `${p.slug}.html`), html, 'utf8');
    searchIndex.push({ slug: p.slug, title: p.title, description: p.description, headings: headings.map((h) => h.text) });
  }

  await fs.writeFile(path.join(outRoot, 'index.html'), `<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=${siteBase}/overview.html">`, 'utf8');
  await fs.writeFile(path.join(outRoot, 'search-index.json'), JSON.stringify(searchIndex, null, 2), 'utf8');

  const sitemap = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...pages.map((p) => `  <url><loc>${siteBase}/${p.slug}.html</loc></url>`),
    '</urlset>',
  ].join('\n');
  await fs.writeFile(path.join(outRoot, 'sitemap.xml'), sitemap, 'utf8');

  console.log(`docs built: ${pages.length} pages -> ${outRoot}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
