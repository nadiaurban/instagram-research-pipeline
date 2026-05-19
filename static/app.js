// app.js

// ── Navigation ────────────────────────────────────────────────────────────────

document.querySelectorAll('.nav-item').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        const target = link.dataset.section;
        document.querySelectorAll('.nav-item').forEach(l => l.classList.remove('active'));
        document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
        link.classList.add('active');
        document.getElementById(`section-${target}`).classList.add('active');
    });
});

// ── Sidebar status ────────────────────────────────────────────────────────────

function refreshSidebarStatus() {
    fetch('/status')
        .then(r => r.json())
        .then(data => {
            document.getElementById('sb-posts').textContent    = data.posts    || '–';
            document.getElementById('sb-comments').textContent = data.comments || '–';
        });
}

// ── File upload ───────────────────────────────────────────────────────────────

function uploadFile(fileInput, kind) {
    const file = fileInput.files[0];
    if (!file) return;

    const statusEl = document.getElementById(`status-${kind}`);
    statusEl.className = 'upload-status';
    statusEl.textContent = 'Uploading…';

    const form = new FormData();
    form.append(kind, file);

    fetch('/upload', { method: 'POST', body: form })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                statusEl.className = 'upload-status error';
                statusEl.textContent = data.error;
                return;
            }
            const result = data[kind];
            if (!result) return;

            statusEl.textContent = `✓ ${file.name}`;

            if (kind === 'posts') {
                renderPostStats(result.stats);
                storePreview('posts', result.preview);
                updateMediaCounts(result.stats);
            } else {
                renderCommentStats(result.stats);
                storePreview('comments', result.preview);
            }

            refreshSidebarStatus();
        })
        .catch(err => {
            statusEl.className = 'upload-status error';
            statusEl.textContent = 'Upload failed';
            console.error(err);
        });
}

document.getElementById('file-posts').addEventListener('change', function() {
    uploadFile(this, 'posts');
});
document.getElementById('file-comments').addEventListener('change', function() {
    uploadFile(this, 'comments');
});

// ── Drag and drop ─────────────────────────────────────────────────────────────

['posts', 'comments'].forEach(kind => {
    const card = document.getElementById(`drop-${kind}`);
    const input = document.getElementById(`file-${kind}`);

    card.addEventListener('dragover', e => {
        e.preventDefault();
        card.classList.add('drag-over');
    });
    card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
    card.addEventListener('drop', e => {
        e.preventDefault();
        card.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (!file) return;
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        uploadFile(input, kind);
    });
});

// ── Stats rendering ───────────────────────────────────────────────────────────

function statCard(label, value, sub) {
    return `
        <div class="stat-card">
            <div class="stat-card-label">${label}</div>
            <div class="stat-card-value">${value}</div>
            ${sub ? `<div class="stat-card-sub">${sub}</div>` : ''}
        </div>`;
}

function labelCard(label, text) {
    return `
        <div class="stat-card">
            <div class="stat-card-label">${label}</div>
            <div class="stat-card-text">${text}</div>
        </div>`;
}

function renderPostStats(stats) {
    if (!stats) return;

    const typeCards = Object.entries(stats.media_types || {})
        .map(([k, v]) => statCard(k.replace(/_/g, ' ').toLowerCase(), v))
        .join('');

    const from = stats.date_range?.from?.slice(0, 10) ?? '–';
    const to   = stats.date_range?.to?.slice(0, 10)   ?? '–';

    document.getElementById('posts-stats').innerHTML =
        statCard('Total posts', stats.total) +
        statCard('AI labeled', stats.ai_labeled, 'posts with AI label') +
        statCard('Partials', stats.partials, 'incomplete records') +
        typeCards +
        labelCard('Date range', `${from} → ${to}`);

    populateMediaTypeFilter(stats.media_types || {});
}

function populateMediaTypeFilter(mediaTypes) {
    const sel = document.getElementById('mediatype-posts');
    if (!sel) return;
    const options = Object.keys(mediaTypes)
        .map(k => `<option value="${k}">${k.replace(/_/g, ' ').toLowerCase()}</option>`)
        .join('');
    sel.innerHTML = '<option value="">All types</option>' + options;
}

function renderCommentStats(stats) {
    if (!stats) return;
    document.getElementById('comments-stats').innerHTML =
        statCard('Total comments', stats.total) +
        statCard('Posts covered', stats.posts) +
        statCard('Avg likes', stats.avg_likes, 'per comment') +
        statCard('With replies', stats.with_replies, 'child comments exist');
}

// ── Table rendering with filter, sort, and row count ─────────────────────────

const previewData = { posts: null, comments: null };
const sortState   = {
    posts:    { col: null, dir: null },
    comments: { col: null, dir: null },
};

function storePreview(kind, preview) {
    previewData[kind] = preview;
    sortState[kind] = { col: null, dir: null };
    applyTableFilter(kind);
}

function toggleSort(kind, col) {
    const s = sortState[kind];
    if (s.col === col) {
        if (s.dir === 'asc')       s.dir = 'desc';
        else if (s.dir === 'desc') { s.col = null; s.dir = null; }
    } else {
        s.col = col;
        s.dir = 'asc';
    }
    applyTableFilter(kind);
}

function applyTableFilter(kind) {
    const data = previewData[kind];
    if (!data || data.columns.length === 0) return;

    const filterVal  = document.getElementById(`filter-${kind}`)?.value.toLowerCase() || '';
    const rowLimit   = parseInt(document.getElementById(`rows-${kind}`).value);
    const typeFilter = kind === 'posts'
        ? (document.getElementById('mediatype-posts')?.value || '')
        : '';

    let rows = data.rows;

    if (typeFilter) {
        const ci = data.columns.indexOf('media_type');
        if (ci >= 0) rows = rows.filter(row => row[ci] === typeFilter);
    }

    if (filterVal) {
        rows = rows.filter(row =>
            row.some(cell => String(cell).toLowerCase().includes(filterVal))
        );
    }

    const { col, dir } = sortState[kind];
    if (col && dir) {
        const ci = data.columns.indexOf(col);
        if (ci >= 0) {
            rows = [...rows].sort((a, b) => {
                const av = a[ci], bv = b[ci];
                const an = parseFloat(av), bn = parseFloat(bv);
                const cmp = !isNaN(an) && !isNaN(bn)
                    ? an - bn
                    : String(av).localeCompare(String(bv));
                return dir === 'asc' ? cmp : -cmp;
            });
        }
    }

    const filtered = rows.length;
    if (rowLimit > 0) rows = rows.slice(0, rowLimit);

    renderTable(kind, { columns: data.columns, rows });

    const parts = [];
    if (typeFilter) parts.push(typeFilter.replace(/_/g, ' ').toLowerCase());
    if (filterVal)  parts.push(`matching "${filterVal}"`);
    if (col)        parts.push(`sorted by ${col} ${dir === 'asc' ? '▲' : '▼'}`);

    const label = parts.length ? ` · ${parts.join(' · ')}` : '';
    document.getElementById(`${kind}-preview-info`).textContent =
        `Showing ${rows.length} of ${filtered} rows (${data.rows.length} total)${label}`;
}

function renderTable(kind, preview) {
    if (!preview || preview.columns.length === 0) return;
    const container = document.getElementById(`${kind}-table`);
    const { col: sortCol, dir: sortDir } = sortState[kind];

    const headers = preview.columns.map(c => {
        const cls = c === sortCol ? ` class="col-sorted-${sortDir}"` : '';
        return `<th${cls} onclick="toggleSort('${kind}','${c}')" title="Sort by ${c}">${c}</th>`;
    }).join('');

    const bodyRows = preview.rows
        .map(row => `<tr>${row.map(cell => `<td title="${cell}">${cell}</td>`).join('')}</tr>`)
        .join('');

    container.innerHTML = `
        <table>
            <thead><tr>${headers}</tr></thead>
            <tbody>${bodyRows}</tbody>
        </table>`;
}

// ── Media counts ──────────────────────────────────────────────────────────────

function updateMediaCounts(stats) {
    document.getElementById('media-image-count').textContent    = stats?.image_count    ?? '–';
    document.getElementById('media-video-count').textContent    = stats?.video_count    ?? '–';
    document.getElementById('media-carousel-count').textContent = stats?.carousel_count ?? '–';
}

// ── Media download (SSE progress) ────────────────────────────────────────────

function downloadMedia() {
    const btn         = document.getElementById('btn-media');
    const progressWrap = document.getElementById('media-progress');
    const bar         = document.getElementById('media-progress-bar');
    const text        = document.getElementById('media-progress-text');
    const errorEl     = document.getElementById('media-error');

    btn.disabled             = true;
    progressWrap.style.display = 'block';
    bar.style.width          = '0%';
    text.textContent         = 'Starting…';
    errorEl.style.display    = 'none';

    const es = new EventSource('/download/media/progress');

    es.onmessage = e => {
        const data = JSON.parse(e.data);

        if (data.error) {
            es.close();
            errorEl.textContent      = data.error;
            errorEl.style.display    = 'block';
            btn.disabled             = false;
            progressWrap.style.display = 'none';
            return;
        }

        if (data.done) {
            es.close();
            bar.style.width  = '100%';
            const errNote    = data.errors > 0 ? ` · ${data.errors} failed` : '';
            text.textContent = `Done — ${data.downloaded} files downloaded${errNote}`;

            const a    = document.createElement('a');
            a.href     = `/download/media/result/${data.token}`;
            a.download = '';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);

            setTimeout(() => {
                btn.disabled             = false;
                progressWrap.style.display = 'none';
            }, 2000);
            return;
        }

        const pct    = data.total > 0 ? Math.round(data.downloaded / data.total * 100) : 0;
        bar.style.width  = `${pct}%`;
        text.textContent = `${data.downloaded} / ${data.total} files`;
    };

    es.onerror = () => {
        es.close();
        errorEl.textContent      = 'Connection lost during download.';
        errorEl.style.display    = 'block';
        btn.disabled             = false;
        progressWrap.style.display = 'none';
    };
}

// ── Init ──────────────────────────────────────────────────────────────────────

refreshSidebarStatus();
