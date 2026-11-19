/* ══════════════════════════════════════════════════════════════
   Computer Diagnoser — Frontend Application Logic
   ══════════════════════════════════════════════════════════════ */

const API = '';  // Same origin

// ─── SAFE HTML HELPERS ───────────────────────────────────────
// Everything that comes from the PC (file names, process names, startup commands,
// event-log text...) is attacker-controllable, so it must be escaped before going
// into innerHTML. A file named <img src=x onerror=...> used to run script that could
// call the delete/kill APIs. Row buttons now carry data-* attributes and are handled by
// one delegated click handler, instead of inlining values into onclick="" strings
// (which also mangled backslashes in registry paths and broke on apostrophes).

function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
const escapeHtml = esc;

// ─── API HELPERS ─────────────────────────────────────────────

async function api(url, options) {
    try {
        const res = await fetch(API + url, options);
        let data = null;
        try { data = await res.json(); } catch (_) { /* non-JSON body */ }
        return { ok: res.ok, status: res.status, data };
    } catch (err) {
        console.error(`Request failed: ${url}`, err);
        return { ok: false, status: 0, data: null };
    }
}

async function fetchJSON(url) {
    const { ok, data } = await api(url);
    return ok ? data : null;
}

async function postJSON(url, body) {
    const { ok, data } = await api(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    return ok ? data : null;
}

// ─── NAVIGATION ──────────────────────────────────────────────

let currentSection = 'overview';

function navigateTo(sectionId) {
    currentSection = sectionId;

    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.section === sectionId);
    });
    document.querySelectorAll('.content-section').forEach(section => {
        section.classList.toggle('active', section.id === `section-${sectionId}`);
    });

    loadSectionData(sectionId);
}

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        navigateTo(item.dataset.section);
    });
});

function loadSectionData(section) {
    switch (section) {
        case 'overview': loadOverview(); break;
        case 'storage': loadStorage(); break;
        case 'performance': loadPerformance(); break;
        case 'network': loadNetwork(); break;
        case 'health': loadHealth(); break;
        case 'security': loadSecurity(); break;
        case 'startup': loadStartup(); break;
        case 'diagnose': /* Chat is always ready */ break;
        case 'settings': loadSettings(); break;
    }
}

// ─── LIVE REFRESH ────────────────────────────────────────────
// The overview and performance pages are "live monitors": refresh them every few
// seconds, but only while they are visible, and never let requests pile up.

const LIVE_INTERVAL_MS = 3000;
let liveBusy = false;

setInterval(async () => {
    if (document.hidden || liveBusy) return;
    if (currentSection !== 'overview' && currentSection !== 'performance') return;
    liveBusy = true;
    try {
        if (currentSection === 'overview') await loadOverview();
        else await loadPerformance();
    } finally {
        liveBusy = false;
    }
}, LIVE_INTERVAL_MS);

// ─── OVERVIEW ────────────────────────────────────────────────

async function loadOverview() {
    const [sysInfo, cpuInfo, memInfo, drives, battery, processes] = await Promise.all([
        fetchJSON('/api/system/info'),
        fetchJSON('/api/system/cpu'),
        fetchJSON('/api/system/memory'),
        fetchJSON('/api/storage/drives'),
        fetchJSON('/api/system/battery'),
        fetchJSON('/api/system/processes?top_n=8'),
    ]);

    if (sysInfo) {
        setText('#chip-hostname .chip-value', sysInfo.hostname);
        setText('#chip-os .chip-value', sysInfo.os);
        setText('#chip-uptime .chip-value', sysInfo.uptime);
        setText('#chip-cpu-name .chip-value', truncate(sysInfo.processor, 40));
    }

    if (cpuInfo) {
        const cpuPct = cpuInfo.overall_percent;
        setText('#overview-cpu-value', `${cpuPct}%`);
        setBarWidth('#overview-cpu-bar', cpuPct);
        setText('#overview-cpu-detail', `${cpuInfo.physical_cores} cores / ${cpuInfo.logical_cores} threads • ${cpuInfo.current_freq_mhz} MHz`);
    }

    if (memInfo) {
        const ramPct = memInfo.ram.percent_used;
        setText('#overview-ram-value', `${ramPct}%`);
        setBarWidth('#overview-ram-bar', ramPct);
        setText('#overview-ram-detail', `${memInfo.ram.used_display} / ${memInfo.ram.total_display}`);
    }

    if (drives && drives.length > 0) {
        const primary = drives[0];
        setText('#overview-disk-value', `${primary.percent_used}%`);
        setBarWidth('#overview-disk-bar', primary.percent_used);
        setText('#overview-disk-detail', `${primary.used_display} / ${primary.total_display} (${primary.mountpoint})`);

        renderDrivesGrid('#overview-drives-grid', drives);
        populateDriveSelector(drives);
    }

    if (battery && battery.available) {
        setText('#overview-battery-value', `${battery.percent}%`);
        setBarWidth('#overview-battery-bar', battery.percent);
        setText('#overview-battery-detail', battery.power_plugged ? `Plugged in • ${battery.time_left}` : `On battery • ${battery.time_left}`);
    } else {
        setText('#overview-battery-value', 'N/A');
        setText('#overview-battery-detail', 'No battery detected');
    }

    if (processes) {
        renderProcessTable('#overview-processes-tbody', processes.slice(0, 8), false);
    }
}

// ─── STORAGE ─────────────────────────────────────────────────

let lastTempItems = [];
let lastLargeFiles = [];

async function loadStorage() {
    const [drives, tempFiles] = await Promise.all([
        fetchJSON('/api/storage/drives'),
        fetchJSON('/api/storage/temp-files'),
    ]);

    if (drives && drives.length > 0) {
        renderDrivesGrid('#storage-drives-grid', drives, true);
        populateDriveSelector(drives);
    }
    if (tempFiles) {
        renderTempFiles(tempFiles);
    }
}

const SCAN_BUTTON_HTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Scan Now`;

async function scanDrive() {
    const btn = document.getElementById('btn-scan-drive');
    const status = document.getElementById('scan-status');
    const path = document.getElementById('drive-selector').value;

    btn.disabled = true;
    btn.innerHTML = '<span class="loading-placeholder" style="padding:0;animation:shimmer 0.8s ease-in-out infinite;">Scanning...</span>';
    status.className = 'scan-status';
    status.textContent = 'Scanning… large drives can take up to a minute.';

    // One request, one pass over the disk (it used to be three separate full scans in parallel).
    const { ok, data, status: httpStatus } = await api(
        `/api/storage/scan?path=${encodeURIComponent(path)}&top_n=30&min_size_mb=50`);

    if (ok && data) {
        renderFileTypes(data.file_types);
        renderFolderSizes(data.folders);
        renderLargeFiles(data.large_files);

        const summary = `Scanned ${data.files_scanned.toLocaleString()} files (${data.total_display}) in ${data.elapsed_seconds}s.`;
        if (data.partial) {
            status.className = 'scan-status partial';
            status.textContent = `${summary} ⚠️ Time limit reached — results are partial (the biggest folders may be under-counted).`;
        } else {
            status.textContent = summary;
        }
    } else {
        status.className = 'scan-status partial';
        status.textContent = httpStatus === 400
            ? `Scan failed: ${(data && data.detail) || 'folder not found.'}`
            : 'Scan failed. Please try again.';
    }

    btn.disabled = false;
    btn.innerHTML = SCAN_BUTTON_HTML;
}

const CHART_COLORS = [
    'var(--accent-blue)', 'var(--accent-purple)', 'var(--accent-cyan)',
    'var(--accent-green)', 'var(--accent-amber)', 'var(--accent-red)',
    'var(--accent-pink)', '#64748b', '#a78bfa', '#34d399'
];

function renderFileTypes(types) {
    const container = document.getElementById('storage-types-content');
    const badge = document.getElementById('storage-types-badge');
    if (!types.length) {
        container.innerHTML = '<div class="loading-placeholder">No file type data found.</div>';
        return;
    }

    const maxSize = types[0].size_bytes || 1;
    badge.textContent = `${types.length} categories`;

    container.innerHTML = types.map((t, i) => `
        <div class="type-bar-container">
            <div class="type-bar-label">
                <span class="type-bar-name">
                    <span class="dot" style="background:${CHART_COLORS[i % CHART_COLORS.length]}"></span>
                    ${esc(t.category)} <span style="color:var(--text-muted);font-size:0.75rem">(${t.count.toLocaleString()} files)</span>
                </span>
                <span class="type-bar-size">${esc(t.size_display)}</span>
            </div>
            <div class="type-bar">
                <div class="type-bar-fill" style="width:${(t.size_bytes / maxSize * 100).toFixed(1)}%;background:${CHART_COLORS[i % CHART_COLORS.length]}"></div>
            </div>
        </div>
    `).join('');
}

function renderFolderSizes(folders) {
    const container = document.getElementById('storage-folders-content');
    if (!folders.length) {
        container.innerHTML = '<div class="loading-placeholder">No folder data found.</div>';
        return;
    }

    const maxSize = folders[0].size_bytes || 1;

    container.innerHTML = folders.map(f => `
        <div class="folder-item">
            <span class="folder-icon">📁</span>
            <span class="folder-name" title="${esc(f.path)}">${esc(f.name)}</span>
            <span class="folder-size">${esc(f.size_display)}</span>
            <div class="folder-bar-wrap">
                <div class="type-bar">
                    <div class="type-bar-fill" style="width:${(f.size_bytes / maxSize * 100).toFixed(1)}%;background:var(--gradient-cyan, var(--accent-cyan))"></div>
                </div>
            </div>
        </div>
    `).join('');
}

function renderLargeFiles(files) {
    lastLargeFiles = files;
    const tbody = document.getElementById('storage-large-files-tbody');
    if (!files.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-placeholder">No large files found (> 50 MB).</td></tr>';
        return;
    }

    tbody.innerHTML = files.map((f, i) => `
        <tr>
            <td title="${esc(f.name)}">${esc(truncate(f.name, 40))}</td>
            <td style="color:var(--accent-amber);font-weight:600">${esc(f.size_display)}</td>
            <td>${esc(f.extension || '—')}</td>
            <td title="${esc(f.path)}">${esc(truncate(f.path, 50))}</td>
            <td><button class="btn-action-delete js-delete-large" style="font-size:0.7rem;padding:4px 8px" data-index="${i}">Delete</button></td>
        </tr>
    `).join('');
}

function renderTempFiles(items) {
    lastTempItems = items;
    const container = document.getElementById('storage-temp-content');
    const badge = document.getElementById('temp-total-badge');
    const cleanAll = document.getElementById('btn-clean-all');

    if (!items.length) {
        container.innerHTML = '<div class="loading-placeholder">✅ Nothing to clean — no temp or cache files found.</div>';
        badge.textContent = '0 B';
        cleanAll.style.display = 'none';
        return;
    }

    const totalBytes = items.reduce((s, i) => s + i.size_bytes, 0);
    badge.textContent = formatBytesJS(totalBytes);
    cleanAll.style.display = '';

    container.innerHTML = items.map((t, i) => `
        <div class="temp-item">
            <div class="temp-info">
                <span class="temp-name">🗑️ ${esc(t.name)}${t.needs_admin ? '<span class="temp-admin-note">needs Administrator</span>' : ''}</span>
                <span class="temp-path">${esc(t.path)}</span>
            </div>
            <span class="temp-size">${esc(t.size_display)}</span>
            <button class="btn-action-delete js-clean-temp" data-index="${i}">🧹 Clean</button>
        </div>
    `).join('');
}

async function cleanTempItem(item) {
    const ok = await showConfirm({
        title: 'Clean Folder?',
        message: `Delete the contents of "${item.name}" (${item.size_display})?\n\nPath: ${item.path}\n\nFiles that are in use are skipped. The folder itself is kept.`,
        confirmText: 'Clean Folder',
    });
    if (!ok) return null;
    const result = await postJSON('/api/action/clean-folder', { path: item.path });
    if (!result) { showToast('error', 'Could not clean the folder.'); return null; }
    return result;
}

async function cleanAllTemp() {
    if (!lastTempItems.length) return;
    const total = formatBytesJS(lastTempItems.reduce((s, i) => s + i.size_bytes, 0));
    const ok = await showConfirm({
        title: 'Clean All Temp & Cache?',
        message: `Delete the contents of ${lastTempItems.length} temp/cache folders (about ${total})?\n\nFiles that are in use are skipped. Browser caches will be rebuilt as you browse.`,
        confirmText: 'Clean All',
    });
    if (!ok) return;

    const btn = document.getElementById('btn-clean-all');
    btn.disabled = true;
    let freed = 0, errors = 0;
    for (const item of lastTempItems) {
        const result = await postJSON('/api/action/clean-folder', { path: item.path });
        if (result && result.success) {
            freed += result.freed_bytes || 0;
            errors += result.errors || 0;
        }
    }
    btn.disabled = false;
    showToast('success', `Freed ${formatBytesJS(freed)}${errors ? ` (${errors} items in use were skipped)` : ''}`);
    loadStorage();
}

// ─── PERFORMANCE ─────────────────────────────────────────────

async function loadPerformance() {
    const [cpuInfo, memInfo, processes] = await Promise.all([
        fetchJSON('/api/system/cpu'),
        fetchJSON('/api/system/memory'),
        fetchJSON('/api/system/processes'),
    ]);

    if (cpuInfo) {
        document.getElementById('perf-cpu-details').innerHTML = `
            <div class="detail-item"><span class="detail-label">Overall Usage</span><span class="detail-value">${cpuInfo.overall_percent}%</span></div>
            <div class="detail-item"><span class="detail-label">Physical Cores</span><span class="detail-value">${cpuInfo.physical_cores}</span></div>
            <div class="detail-item"><span class="detail-label">Logical Threads</span><span class="detail-value">${cpuInfo.logical_cores}</span></div>
            <div class="detail-item"><span class="detail-label">Frequency</span><span class="detail-value">${cpuInfo.current_freq_mhz} MHz</span></div>
        `;

        const coresGrid = document.getElementById('perf-cpu-cores');
        coresGrid.innerHTML = cpuInfo.per_cpu_percent.map((pct, i) => {
            const level = pct < 30 ? 'low' : pct < 60 ? 'medium' : pct < 85 ? 'high' : 'critical';
            return `<div class="core-block ${level}" title="Core ${i}: ${pct}%">${pct}%</div>`;
        }).join('');
    }

    if (memInfo) {
        document.getElementById('perf-mem-details').innerHTML = `
            <div class="detail-item"><span class="detail-label">Total RAM</span><span class="detail-value">${esc(memInfo.ram.total_display)}</span></div>
            <div class="detail-item"><span class="detail-label">Used</span><span class="detail-value">${esc(memInfo.ram.used_display)} (${memInfo.ram.percent_used}%)</span></div>
            <div class="detail-item"><span class="detail-label">Available</span><span class="detail-value">${esc(memInfo.ram.available_display)}</span></div>
            <div class="detail-item"><span class="detail-label">Swap Used</span><span class="detail-value">${esc(memInfo.swap.used_display)} / ${esc(memInfo.swap.total_display)}</span></div>
        `;
    }

    if (processes) {
        renderProcessTable('#perf-processes-tbody', processes);
    }
}

async function refreshProcesses() {
    const processes = await fetchJSON('/api/system/processes');
    if (processes) {
        renderProcessTable('#perf-processes-tbody', processes);
    }
}

// ─── NETWORK ─────────────────────────────────────────────────

async function loadNetwork() {
    const [interfaces, connections] = await Promise.all([
        fetchJSON('/api/network/interfaces'),
        fetchJSON('/api/network/connections'),
    ]);

    if (interfaces) renderNetworkInterfaces(interfaces);
    if (connections) renderConnections(connections);
}

async function runPing() {
    const btn = document.getElementById('btn-ping');
    const host = document.getElementById('ping-host').value.trim() || '8.8.8.8';
    const output = document.getElementById('ping-output');

    btn.disabled = true;
    output.textContent = `Pinging ${host}...`;
    output.style.color = 'var(--text-muted)';

    const result = await fetchJSON(`/api/network/ping?host=${encodeURIComponent(host)}`);

    if (result) {
        output.textContent = result.output || (result.success ? 'Ping successful!' : 'Ping failed.');
        output.style.color = result.success ? 'var(--accent-green)' : 'var(--accent-red)';
    } else {
        output.textContent = 'Error running ping test.';
        output.style.color = 'var(--accent-red)';
    }

    btn.disabled = false;
}

function renderNetworkInterfaces(interfaces) {
    const container = document.getElementById('net-interfaces-content');
    container.innerHTML = interfaces.map(iface => {
        const ipv4 = iface.addresses.find(a => a.type === 'IPv4');
        return `
            <div class="net-iface">
                <div class="net-iface-header">
                    <span class="net-iface-name">${esc(iface.name)}</span>
                    <span class="net-iface-status ${iface.is_up ? 'up' : 'down'}">${iface.is_up ? '● UP' : '● DOWN'}</span>
                </div>
                <div class="net-iface-details">
                    ${ipv4 ? `<span>IP: ${esc(ipv4.address)}</span>` : '<span>No IPv4</span>'}
                    <span>Speed: ${iface.speed_mbps} Mbps</span>
                    <span>Sent: ${esc(iface.bytes_sent_display || '0 B')}</span>
                    <span>Received: ${esc(iface.bytes_recv_display || '0 B')}</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderConnections(connections) {
    const tbody = document.getElementById('net-connections-tbody');
    if (!connections.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-placeholder">No active connections.</td></tr>';
        return;
    }

    tbody.innerHTML = connections.map(c => {
        // Keep the underscore: the stylesheet defines .status-time_wait (it was being stripped).
        const statusClass = `status-${esc(String(c.status).toLowerCase())}`;
        return `
            <tr>
                <td>${esc(c.process || '—')}</td>
                <td>${esc(c.type)}</td>
                <td>${esc(c.local_addr)}</td>
                <td>${esc(c.remote_addr || '—')}</td>
                <td class="${statusClass}">${esc(c.status)}</td>
            </tr>
        `;
    }).join('');
}

async function refreshConnections() {
    const connections = await fetchJSON('/api/network/connections');
    if (connections) renderConnections(connections);
}

// ─── STARTUP ─────────────────────────────────────────────────

let startupItems = [];

async function loadStartup() {
    const programs = await fetchJSON('/api/startup/programs');
    const badge = document.getElementById('startup-count-badge');
    const tbody = document.getElementById('startup-tbody');

    if (!programs || !programs.length) {
        startupItems = [];
        badge.textContent = '0';
        tbody.innerHTML = '<tr><td colspan="5" class="loading-placeholder">No startup programs detected.</td></tr>';
        return;
    }

    startupItems = programs;
    const enabledCount = programs.filter(p => p.enabled !== false).length;
    const disabledCount = programs.length - enabledCount;
    badge.textContent = disabledCount ? `${enabledCount} enabled · ${disabledCount} disabled` : `${programs.length} items`;

    tbody.innerHTML = programs.map((p, i) => {
        const enabled = p.enabled !== false;
        let action = '<span style="color:var(--text-muted)">—</span>';
        if (p.toggleable) {
            action = enabled
                ? `<button class="btn-action-delete js-startup" style="font-size:0.7rem;padding:4px 8px" data-index="${i}">Disable</button>`
                : `<button class="btn-action-enable js-startup" data-index="${i}">Enable</button>`;
        }
        return `
        <tr class="${enabled ? '' : 'row-disabled'}">
            <td style="font-weight:500">${esc(p.name)}${enabled ? '' : ' <span class="badge">Disabled</span>'}</td>
            <td><span class="badge">${esc(p.scope)}</span></td>
            <td>${esc(p.source)}</td>
            <td title="${esc(p.command)}">${esc(truncate(p.command, 50))}</td>
            <td>${action}</td>
        </tr>`;
    }).join('');
}

async function toggleStartup(item) {
    const enabling = item.enabled === false;
    const ok = await showConfirm({
        title: enabling ? 'Enable Startup Item?' : 'Disable Startup Item?',
        message: enabling
            ? `"${item.name}" will start with Windows again.`
            : `"${item.name}" will no longer start with Windows. You can turn it back on here at any time.`,
        confirmText: enabling ? 'Enable' : 'Disable',
        danger: !enabling,
    });
    if (!ok) return;

    const result = await postJSON('/api/startup/toggle', {
        name: item.name,
        action: enabling ? 'enable' : 'disable',
        scope: item.scope,
        location: item.location,
        source: item.source,
        key: item.key || item.name,
    });
    if (result && result.success) {
        showToast('success', result.message);
        loadStartup();
    } else {
        showToast('error', (result && result.error) || 'Could not change the startup item.');
    }
}

// ─── SHARED RENDERERS ────────────────────────────────────────

function renderDrivesGrid(selector, drives) {
    const container = document.querySelector(selector);

    container.innerHTML = drives.map(d => {
        const level = d.percent_used < 70 ? 'ok' : d.percent_used < 90 ? 'warning' : 'danger';
        return `
            <div class="drive-card">
                <div class="drive-header">
                    <span class="drive-name">${esc(d.mountpoint)}</span>
                    <span class="drive-percent ${level}">${d.percent_used}%</span>
                </div>
                <div class="drive-bar">
                    <div class="drive-bar-fill ${level}" style="width:${d.percent_used}%"></div>
                </div>
                <div class="drive-info">
                    <span>Used: ${esc(d.used_display)}</span>
                    <span>Free: ${esc(d.free_display)}</span>
                </div>
                <div class="drive-info" style="margin-top:4px">
                    <span>Total: ${esc(d.total_display)}</span>
                    <span>${esc(d.fstype)}</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderProcessTable(selector, processes, withKill = true) {
    const tbody = document.querySelector(selector);
    if (!processes.length) {
        tbody.innerHTML = `<tr><td colspan="${withKill ? 6 : 5}" class="loading-placeholder">No processes found.</td></tr>`;
        return;
    }

    tbody.innerHTML = processes.map(p => {
        const statusClass = `status-${esc(String(p.status).toLowerCase())}`;
        return `
            <tr>
                <td style="font-weight:500" title="${esc(p.name)}">${esc(truncate(p.name, 30))}</td>
                <td>${p.pid}</td>
                <td style="color:var(--accent-cyan)">${esc(p.memory_display)}</td>
                <td>${p.cpu_percent}%</td>
                <td class="${statusClass}">${esc(p.status)}</td>
                ${withKill ? `<td><button class="btn-action-delete js-kill" style="font-size:0.7rem;padding:4px 8px" data-pid="${p.pid}" data-name="${esc(p.name)}">Kill</button></td>` : ''}
            </tr>
        `;
    }).join('');
}

function populateDriveSelector(drives) {
    const selector = document.getElementById('drive-selector');
    if (!selector) return;

    const html = drives.map(d =>
        `<option value="${esc(d.mountpoint)}">${esc(d.mountpoint)} (${esc(d.free_display)} free)</option>`
    ).join('');
    // The live refresh calls this every few seconds; rebuilding identical options would
    // close an open dropdown and reset the user's choice.
    if (selector.dataset.signature === html) return;

    const currentValue = selector.value;
    selector.innerHTML = html;
    selector.dataset.signature = html;
    if (Array.from(selector.options).some(o => o.value === currentValue)) {
        selector.value = currentValue;
    }
}

// ─── HELPERS ─────────────────────────────────────────────────

function setText(selector, text) {
    const el = document.querySelector(selector);
    if (el) el.textContent = text;
}

function setBarWidth(selector, percent) {
    const el = document.querySelector(selector);
    if (el) {
        requestAnimationFrame(() => {
            el.style.width = `${Math.min(percent, 100)}%`;
        });
    }
}

function truncate(str, max) {
    if (!str) return '—';
    return str.length > max ? str.substring(0, max) + '…' : str;
}

function formatBytesJS(bytes) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) {
        size /= 1024;
        i++;
    }
    return i === 0 ? `${Math.round(size)} B` : `${size.toFixed(2)} ${units[i]}`;
}

// ─── CONFIRMATION MODAL ──────────────────────────────────────

let modalResolver = null;

function showConfirm({ title, message, confirmText = 'Confirm', danger = true }) {
    return new Promise(resolve => {
        if (modalResolver) modalResolver(false);
        modalResolver = resolve;
        document.getElementById('modal-title').textContent = title;
        document.getElementById('modal-message').textContent = message;
        const btn = document.getElementById('modal-confirm-btn');
        btn.textContent = confirmText;
        btn.className = danger ? 'btn-danger' : 'btn-primary';
        document.getElementById('modal-overlay').style.display = 'flex';
        btn.focus();
    });
}

function modalResolve(value) {
    document.getElementById('modal-overlay').style.display = 'none';
    const resolve = modalResolver;
    modalResolver = null;
    if (resolve) resolve(value);
}

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modalResolver) modalResolve(false);
});

document.getElementById('modal-overlay').addEventListener('click', (e) => {
    if (e.target.id === 'modal-overlay') modalResolve(false);
});

// ─── DELETE / CLEAN ACTIONS ──────────────────────────────────

async function deleteFile(file) {
    const ok = await showConfirm({
        title: 'Delete File?',
        message: `Are you sure you want to permanently delete "${file.name}" (${file.size})?\n\nPath: ${file.path}\n\nThis cannot be undone.`,
        confirmText: 'Delete File',
    });
    if (!ok) return null;
    const result = await postJSON('/api/action/delete-file', { path: file.path });
    if (!result) { showToast('error', 'Network error — could not complete action.'); return null; }
    return result;
}

// Run a chat action button (delete a file / clean a folder) and report in the chat.
async function runChatAction(action, button) {
    let result;
    if (action.type === 'delete_file') {
        result = await deleteFile(action);
    } else {
        result = await cleanTempItem({ name: action.name, size_display: action.size, path: action.path });
    }
    if (!result) return;

    if (result.success) {
        showToast('success', `${result.message} — freed ${result.freed}`);
        addChatBubble('bot',
            `Done! ${result.message}\nFreed: ${result.freed}${result.errors ? ` (${result.errors} files couldn't be deleted — may be in use)` : ''}`,
            'Action Complete');
        if (button) { button.disabled = true; button.classList.add('done'); }
    } else {
        showToast('error', result.error || 'Failed to delete.');
        addChatBubble('bot', `Failed: ${result.error}`, 'Error');
    }
}

// One delegated handler for every dynamically created button.
document.addEventListener('click', async (e) => {
    const target = e.target.closest('button');
    if (!target) return;

    if (target.classList.contains('js-kill')) {
        killProcess(Number(target.dataset.pid), target.dataset.name);
    } else if (target.classList.contains('js-startup')) {
        const item = startupItems[Number(target.dataset.index)];
        if (item) toggleStartup(item);
    } else if (target.classList.contains('js-clean-temp')) {
        const item = lastTempItems[Number(target.dataset.index)];
        if (!item) return;
        const result = await cleanTempItem(item);
        if (result && result.success) {
            showToast('success', `${result.message} — freed ${result.freed}`);
            loadStorage();
        } else if (result) {
            showToast('error', result.error || 'Could not clean the folder.');
        }
    } else if (target.classList.contains('js-delete-large')) {
        const file = lastLargeFiles[Number(target.dataset.index)];
        if (!file) return;
        const result = await deleteFile({ name: file.name, size: file.size_display, path: file.path });
        if (result && result.success) {
            showToast('success', `${result.message} — freed ${result.freed}`);
            lastLargeFiles = lastLargeFiles.filter(f => f.path !== file.path);
            renderLargeFiles(lastLargeFiles);
        } else if (result) {
            showToast('error', result.error || 'Failed to delete.');
        }
    } else if (target.classList.contains('js-chat-action')) {
        const action = chatActions[Number(target.dataset.index)];
        if (action) runChatAction(action, target);
    }
});

// ─── CHAT / DIAGNOSE ─────────────────────────────────────────

const chatActions = [];    // action objects referenced by button index
let chatHistory = [];      // recent turns, so follow-up questions work in AI mode
let chatBusy = false;

async function sendChat() {
    if (chatBusy) return;
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('btn-send-chat');
    const question = input.value.trim();
    if (!question) return;

    chatBusy = true;
    sendBtn.disabled = true;
    input.value = '';
    addChatBubble('user', question);
    addTypingIndicator();

    try {
        const res = await fetch(API + '/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, history: chatHistory.slice(-8) }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        removeTypingIndicator();
        addBotResponse(data);

        chatHistory.push({ role: 'user', content: question });
        chatHistory.push({ role: 'assistant', content: data.messages.join('\n').slice(0, 3000) });
        chatHistory = chatHistory.slice(-16);
    } catch (err) {
        removeTypingIndicator();
        addChatBubble('bot', 'Sorry, something went wrong. Please try again.', 'Error');
    } finally {
        chatBusy = false;
        sendBtn.disabled = false;
        input.focus();
    }
}

function askQuestion(q) {
    document.getElementById('chat-input').value = q;
    sendChat();
}

function addChatBubble(type, text, title = null) {
    const messages = document.getElementById('chat-messages');
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${type}`;

    const avatar = type === 'bot' ? 'D' : 'U';
    const label = type === 'bot' ? (title || 'Diagnoser') : 'You';

    bubble.innerHTML = `
        <div class="chat-avatar">${avatar}</div>
        <div class="chat-content">
            <strong>${esc(label)}</strong>
            <p>${esc(text)}</p>
        </div>
    `;

    messages.appendChild(bubble);
    scrollChatToBottom();
}

// Minimal, safe markdown: **bold**, `code`, "- " bullets, "# " headings, ``` fences.
function renderChatLines(rawLines) {
    let html = '';
    let inCode = false;
    let code = [];

    for (const raw of rawLines) {
        if (raw.trim().startsWith('```')) {
            if (inCode) {
                html += `<pre class="chat-code">${esc(code.join('\n'))}</pre>`;
                code = [];
            }
            inCode = !inCode;
            continue;
        }
        if (inCode) { code.push(raw); continue; }

        if (!raw.trim()) {
            html += '<span class="chat-line spacer"></span>';
            continue;
        }
        if (/^─+$/.test(raw.trim()) || /^-{3,}$/.test(raw.trim())) {
            html += '<hr style="border:none;border-top:1px solid var(--border-color);margin:8px 0">';
            continue;
        }

        let line = esc(raw)
            .replace(/^(\s*)[-*]\s+/, '$1• ')
            .replace(/^#{1,6}\s+(.*)$/, '**$1**')
            .replace(/\*\*(.+?)\*\*/g, '<b style="color:var(--text-primary)">$1</b>')
            .replace(/`([^`]+)`/g, '<code>$1</code>');
        html += `<span class="chat-line">${line}</span>`;
    }
    if (inCode && code.length) html += `<pre class="chat-code">${esc(code.join('\n'))}</pre>`;
    return html;
}

function addBotResponse(data) {
    const messages = document.getElementById('chat-messages');
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble bot';

    // Rule-engine replies are arrays of lines (some with a leading "\n" used as spacing);
    // AI replies are one long markdown string. Split both into lines.
    const lines = (data.messages || []).flatMap(m => String(m).split('\n'));
    // Drop empty lines for rule-engine answers (they rely on CSS spacing), keep them for AI text.
    const rendered = renderChatLines(data.ai_mode ? lines : lines.filter(l => l.trim()));

    let actionsHtml = '';
    if (data.actions && data.actions.length > 0) {
        const buttons = data.actions.map(action => {
            const index = chatActions.push(action) - 1;
            return `<button class="btn-action-delete js-chat-action" data-index="${index}">🗑️ ${esc(action.label)}</button>`;
        }).join('');
        actionsHtml = `<div class="chat-actions">${buttons}</div>`;
    }

    bubble.innerHTML = `
        <div class="chat-avatar">D</div>
        <div class="chat-content">
            <strong>${esc(data.title || 'Diagnoser')}</strong>
            ${rendered}
            ${actionsHtml}
        </div>
    `;

    messages.appendChild(bubble);
    scrollChatToBottom();
}

function addTypingIndicator() {
    const messages = document.getElementById('chat-messages');
    const indicator = document.createElement('div');
    indicator.className = 'chat-bubble bot';
    indicator.id = 'typing-indicator';
    indicator.innerHTML = `
        <div class="chat-avatar">D</div>
        <div class="chat-content">
            <div class="chat-loading">
                Analyzing
                <div class="typing-dots"><span></span><span></span><span></span></div>
            </div>
        </div>
    `;
    messages.appendChild(indicator);
    scrollChatToBottom();
}

function removeTypingIndicator() {
    const el = document.getElementById('typing-indicator');
    if (el) el.remove();
}

function scrollChatToBottom() {
    const container = document.getElementById('chat-container');
    setTimeout(() => { container.scrollTop = container.scrollHeight; }, 50);
}

// ─── TOAST NOTIFICATIONS ─────────────────────────────────────

function showToast(type, message, duration = 4000) {
    document.querySelectorAll('.toast').forEach(t => t.remove());

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => { toast.remove(); }, duration);
}

// ─── HEALTH SECTION ──────────────────────────────────────────

function renderCrashRows(crashes, emptyText) {
    const tbody = document.getElementById('crash-tbody');
    if (crashes.events && crashes.events.length > 0) {
        tbody.innerHTML = crashes.events.map(e => {
            const levelClass = e.level === 'Critical' ? 'color:var(--accent-red)' : 'color:var(--accent-amber)';
            return `<tr>
                <td>${esc(e.time)}</td>
                <td style="${levelClass};font-weight:600">${esc(e.level)}</td>
                <td>${esc(truncate(e.source, 30))}</td>
                <td title="${esc(e.message)}">${esc(truncate(e.message, 80))}</td>
            </tr>`;
        }).join('');
    } else {
        tbody.innerHTML = `<tr><td colspan="4" class="loading-placeholder">${esc(emptyText)}</td></tr>`;
    }
}

async function loadHealth() {
    const [temps, battery, disks, crashes, programs] = await Promise.all([
        fetchJSON('/api/health/temperatures'),
        fetchJSON('/api/health/battery'),
        fetchJSON('/api/health/disks'),
        fetchJSON('/api/health/crashes'),
        fetchJSON('/api/health/programs'),
    ]);

    if (temps) {
        setText('#health-temp-value', temps.cpu_temp !== null ? `${temps.cpu_temp}°C` : 'N/A');
        setText('#health-temp-detail', temps.message || 'Unknown');
    }

    if (battery) {
        if (battery.health_percent) {
            setText('#health-battery-value', `${battery.health_percent}%`);
            setBarWidth('#health-battery-bar', battery.health_percent);
        } else {
            setText('#health-battery-value', battery.available ? '—' : 'N/A');
        }
        setText('#health-battery-detail', battery.message || '');
    }

    if (crashes) {
        setText('#health-crash-value', String(crashes.crash_count || 0));
        setText('#health-crash-detail', crashes.message || '');
        renderCrashRows(crashes, '✅ No crashes or unexpected restarts in the last 30 days.');
    }

    if (disks) {
        const container = document.getElementById('health-disks-content');
        if (disks.length > 0) {
            container.innerHTML = disks.map(d => {
                const color = d.health_level === 'good' ? 'var(--accent-green)'
                            : d.health_level === 'warning' ? 'var(--accent-amber)' : 'var(--accent-red)';
                return `<div class="net-iface">
                    <div class="net-iface-header">
                        <span class="net-iface-name">${esc(d.name)}</span>
                        <span class="net-iface-status ${d.health_level === 'good' ? 'up' : 'down'}" style="color:${color}">
                            ● ${esc(d.health_status)}
                        </span>
                    </div>
                    <div class="net-iface-details">
                        <span>Type: ${esc(d.media_type)}</span>
                        <span>Size: ${esc(d.size_display)}</span>
                        <span>Bus: ${esc(d.bus_type)}</span>
                        <span>Status: ${esc(d.operational_status)}</span>
                    </div>
                </div>`;
            }).join('');
        } else {
            container.innerHTML = '<div class="loading-placeholder">Could not read disk health. Try running as Administrator.</div>';
        }
    }

    if (programs) {
        const tbody = document.getElementById('programs-tbody');
        const badge = document.getElementById('programs-count-badge');
        badge.textContent = `${programs.length} apps`;
        tbody.innerHTML = programs.map(p => `
            <tr>
                <td style="font-weight:500">${esc(truncate(p.name, 35))}</td>
                <td>${esc(p.version || '—')}</td>
                <td>${esc(truncate(p.publisher || '—', 25))}</td>
                <td style="color:var(--accent-amber);font-weight:600">${esc(p.size_display)}</td>
            </tr>
        `).join('');
    }
}

async function loadCrashLogs() {
    const crashes = await fetchJSON('/api/health/crashes');
    if (crashes) renderCrashRows(crashes, '✅ No crashes found.');
}

// ─── SECURITY SECTION ────────────────────────────────────────

function setStatusValue(id, text, color) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.style.color = color;
    el.style.webkitTextFillColor = color;
    el.style.background = 'none';
}

async function loadSecurity() {
    const security = await fetchJSON('/api/security/status');
    if (!security) return;

    const d = security.defender;
    const f = security.firewall;
    const u = security.updates;

    const defColor = d.status_level === 'good' ? 'var(--accent-green)'
                   : d.status_level === 'danger' ? 'var(--accent-red)' : 'var(--accent-amber)';
    setStatusValue('sec-defender-value',
        d.enabled ? (d.real_time_protection ? '✅ Protected' : '⚠️ Partial')
                  : (d.status_level === 'unknown' ? '❔ Unknown' : '❌ Disabled'),
        defColor);
    setText('#sec-defender-detail', d.message);

    const fwColor = f.status_level === 'good' ? 'var(--accent-green)'
                  : f.status_level === 'unknown' ? 'var(--accent-amber)' : 'var(--accent-red)';
    setStatusValue('sec-firewall-value',
        f.status_level === 'unknown' ? '❔ Unknown' : (f.all_enabled ? '✅ All ON' : '❌ Partial'),
        fwColor);
    setText('#sec-firewall-detail', f.message);

    const profilesContainer = document.getElementById('sec-firewall-profiles');
    if (f.profiles && f.profiles.length > 0) {
        profilesContainer.innerHTML = f.profiles.map(p => {
            const icon = p.enabled ? '🟢' : '🔴';
            return `<div class="net-iface">
                <div class="net-iface-header">
                    <span class="net-iface-name">${esc(p.name)}</span>
                    <span class="net-iface-status ${p.enabled ? 'up' : 'down'}">${icon} ${p.enabled ? 'Enabled' : 'DISABLED'}</span>
                </div>
            </div>`;
        }).join('');
    }

    const uColor = u.status_level === 'good' ? 'var(--accent-green)' : 'var(--accent-amber)';
    setStatusValue('sec-updates-value',
        u.status_level === 'good' ? '✅ Up to Date' : (u.status_level === 'unknown' ? '❔ Unknown' : '⚠️ Check Updates'),
        uColor);
    setText('#sec-updates-detail', u.message);

    const tbody = document.getElementById('sec-updates-tbody');
    if (u.recent_updates && u.recent_updates.length > 0) {
        tbody.innerHTML = u.recent_updates.map(upd => `
            <tr>
                <td style="font-weight:600;color:var(--accent-blue)">${esc(upd.id)}</td>
                <td>${esc(upd.description || '—')}</td>
                <td>${esc(upd.installed_on)}</td>
            </tr>
        `).join('');
    } else {
        tbody.innerHTML = '<tr><td colspan="3" class="loading-placeholder">No recent updates found.</td></tr>';
    }
}

// ─── SPEED TEST ──────────────────────────────────────────────

async function runSpeedTest() {
    const btn = document.getElementById('btn-speedtest');
    const resultDiv = document.getElementById('speed-test-result');
    btn.disabled = true;
    btn.textContent = '⏳ Testing...';
    resultDiv.style.display = 'block';
    resultDiv.className = 'speed-result';
    resultDiv.textContent = 'Running speed test... This may take up to 15 seconds.';

    const result = await fetchJSON('/api/network/speedtest');

    if (result && result.success) {
        resultDiv.className = 'speed-result success';
        resultDiv.innerHTML = `
            <div style="display:flex;gap:24px;align-items:center">
                <div><span style="font-size:2rem;font-weight:800;color:var(--accent-cyan)">${result.download_mbps}</span> <span style="color:var(--text-muted)">Mbps</span></div>
                <div><span style="font-size:1.2rem;font-weight:600">${result.latency_ms ?? '—'}</span> <span style="color:var(--text-muted)">ms latency</span></div>
            </div>
            <div style="margin-top:8px;color:var(--text-muted);font-size:0.8rem">Downloaded ${esc(result.download_size)} in ${result.download_time}s</div>
        `;
    } else {
        resultDiv.className = 'speed-result error';
        resultDiv.textContent = '❌ Speed test failed. Check your internet connection.';
    }

    btn.disabled = false;
    btn.textContent = '🌐 Speed Test';
}

// ─── KILL PROCESS ────────────────────────────────────────────

async function killProcess(pid, name) {
    const ok = await showConfirm({
        title: 'Kill Process?',
        message: `Terminate "${name}" (PID ${pid})?\n\nUnsaved work in that program will be lost.`,
        confirmText: 'Kill Process',
    });
    if (!ok) return;

    const result = await postJSON('/api/tools/kill-process', { pid });
    if (result && result.success) {
        showToast('success', result.message);
        refreshProcesses();
    } else {
        showToast('error', (result && result.error) || 'Could not kill process.');
    }
}

// ─── SYSTEM BOOST ────────────────────────────────────────────

async function systemBoost() {
    const btn = document.getElementById('btn-boost');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Boosting...'; }

    const result = await postJSON('/api/tools/system-boost');
    if (result && result.success) {
        showToast('success', result.message, 6000);
    } else {
        showToast('error', 'Boost failed.');
    }

    if (btn) { btn.disabled = false; btn.textContent = '⚡ System Boost'; }
}

// ─── SETTINGS ────────────────────────────────────────────────

let presetsData = {};

async function loadSettings() {
    const presets = await fetchJSON('/api/config/presets');
    if (presets) {
        presetsData = presets;
        const select = document.getElementById('settings-provider');
        select.innerHTML = '<option value="">-- Choose a provider --</option>';
        for (const [key, preset] of Object.entries(presets)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = preset.label;
            select.appendChild(opt);
        }
    }

    const config = await fetchJSON('/api/config');
    if (config) {
        if (config.ai_provider) {
            const providerKey = Object.keys(presetsData).find(
                k => presetsData[k].label === config.ai_provider
            ) || '';
            document.getElementById('settings-provider').value = providerKey;
        }
        document.getElementById('settings-endpoint').value = config.endpoint_url || '';
        document.getElementById('settings-model').value = config.model_name || '';
        document.getElementById('settings-format').value = config.api_format || 'openai';

        if (config.has_key) {
            document.getElementById('settings-api-key').placeholder = `Current: ${config.api_key_masked}`;
        }

        // ai_active is what the server really uses. Local models (Ollama / LM Studio) have no
        // key, so "enabled && has_key" wrongly showed them as "Not Configured".
        const badge = document.getElementById('settings-status-badge');
        if (config.ai_active) {
            badge.textContent = 'Active';
            badge.className = 'badge success';
        } else {
            badge.textContent = 'Not Configured';
            badge.className = 'badge';
        }

        updateAiBadge(config.ai_active, config.ai_provider);
    }
}

function onProviderChange() {
    const key = document.getElementById('settings-provider').value;
    if (!key || !presetsData[key]) return;

    const preset = presetsData[key];
    document.getElementById('settings-endpoint').value = preset.endpoint_url || '';
    document.getElementById('settings-model').value = preset.model_name || '';
    document.getElementById('settings-format').value = preset.api_format || 'openai';

    if (!preset.needs_key) {
        document.getElementById('settings-key-hint').textContent = 'No API key needed for this provider';
        document.getElementById('settings-api-key').placeholder = 'Not required';
    } else {
        document.getElementById('settings-key-hint').textContent = "Get your key from the provider's dashboard";
        document.getElementById('settings-api-key').placeholder = 'Paste your API key here';
    }
}

async function saveSettings() {
    const providerKey = document.getElementById('settings-provider').value;
    const providerLabel = providerKey && presetsData[providerKey]
        ? presetsData[providerKey].label
        : document.getElementById('settings-provider').selectedOptions[0]?.text || 'Custom';

    const apiKey = document.getElementById('settings-api-key').value.trim();
    const endpoint = document.getElementById('settings-endpoint').value.trim();
    const model = document.getElementById('settings-model').value.trim();
    const format = document.getElementById('settings-format').value;

    if (!endpoint) {
        showToast('error', 'Please enter an API endpoint URL.');
        return;
    }

    const body = {
        ai_provider: providerLabel,
        endpoint_url: endpoint,
        model_name: model,
        api_format: format,
        ai_enabled: true,
    };
    if (apiKey) body.api_key = apiKey;

    const result = await postJSON('/api/config', body);
    if (result && result.success) {
        showToast('success', 'Settings saved! AI mode is now active.');
        document.getElementById('settings-api-key').value = '';
        loadSettings();
    } else {
        showToast('error', (result && result.message) || 'Failed to save.');
    }
}

async function testConnection() {
    const btn = document.getElementById('btn-test-connection');
    const resultDiv = document.getElementById('settings-test-result');
    btn.textContent = 'Testing...';
    btn.disabled = true;
    resultDiv.style.display = 'none';

    const result = await postJSON('/api/config/test');

    resultDiv.style.display = 'block';
    if (result && result.success) {
        resultDiv.className = 'test-result success';
        resultDiv.textContent = `✅ ${result.message} — "${result.response}"`;
    } else if (result) {
        resultDiv.className = 'test-result error';
        resultDiv.textContent = `❌ ${result.error}`;
    } else {
        resultDiv.className = 'test-result error';
        resultDiv.textContent = '❌ Could not reach the server.';
    }

    btn.textContent = 'Test Connection';
    btn.disabled = false;
}

function toggleKeyVisibility() {
    const input = document.getElementById('settings-api-key');
    input.type = input.type === 'password' ? 'text' : 'password';
}

function updateAiBadge(isActive, providerName) {
    const badge = document.getElementById('ai-badge');
    if (!badge) return;
    if (isActive) {
        badge.className = 'ai-badge online';
        badge.textContent = `🤖 AI Mode: ${providerName || 'Active'}`;
    } else {
        badge.className = 'ai-badge offline';
        badge.textContent = '⚙️ Rule Engine (configure AI in Settings)';
    }
}

// ─── INIT ────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadOverview();
    fetchJSON('/api/config').then(config => {
        if (config) updateAiBadge(config.ai_active, config.ai_provider);
    });
    initVersionAndUpdates();
});

// ─── UPDATES ──────────────────────────────────────────────────

async function initVersionAndUpdates() {
    const data = await fetchJSON('/api/version');
    if (data && data.version) {
        document.querySelectorAll('#version-tag, #settings-current-version').forEach(tag => {
            tag.textContent = 'v' + data.version;
        });
    }
    checkForUpdates(false);  // background check (silent)
}

async function checkForUpdates(manual = false) {
    const btn = document.getElementById('btn-check-update');
    const badge = document.getElementById('update-status-badge');
    const latestContainer = document.getElementById('settings-latest-version-container');
    const latestText = document.getElementById('settings-latest-version');
    const installBtn = document.getElementById('btn-install-update');
    const resultDiv = document.getElementById('update-result');
    const banner = document.getElementById('update-banner');
    const bannerText = document.getElementById('update-banner-text');

    if (manual && btn) {
        btn.disabled = true;
        btn.textContent = 'Checking...';
    }
    if (badge) {
        badge.className = 'badge';
        badge.textContent = 'Checking...';
    }
    if (resultDiv) resultDiv.style.display = 'none';

    try {
        const data = await fetchJSON('/api/update/check');
        if (data && data.available) {
            if (badge) {
                badge.className = 'badge online';
                badge.textContent = 'Update Available';
            }
            if (latestContainer && latestText) {
                latestContainer.style.display = 'block';
                latestText.textContent = 'v' + data.latest_version;
            }
            if (installBtn) installBtn.style.display = 'inline-block';
            if (banner && bannerText) {
                bannerText.textContent = `🚀 A new version (v${data.latest_version}) is available!`;
                banner.style.display = 'flex';
            }
            if (manual) showToast('success', `Version v${data.latest_version} is available!`);
        } else if (!data || data.error) {
            // The check itself failed (offline, rate limit...). Do not claim "Up to Date".
            if (badge) {
                badge.className = 'badge offline';
                badge.textContent = 'Unavailable';
            }
            if (manual) showToast('error', (data && data.error) || 'Failed to check for updates.');
        } else {
            if (badge) {
                badge.className = 'badge';
                badge.textContent = 'Up to Date';
            }
            if (latestContainer) latestContainer.style.display = 'none';
            if (installBtn) installBtn.style.display = 'none';
            if (banner) banner.style.display = 'none';
            if (manual) showToast('info', 'You are running the latest version.');
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔄 Check for Updates';
        }
    }
}

async function installUpdate() {
    const installBtn = document.getElementById('btn-install-update');
    const bannerBtn = document.getElementById('btn-update-install');
    const resultDiv = document.getElementById('update-result');

    const updateButtons = [installBtn, bannerBtn];
    const restoreButtons = () => updateButtons.forEach(btn => {
        if (btn) {
            btn.disabled = false;
            btn.textContent = btn === installBtn ? '⬆ Install Update' : '⬆ Update Now';
        }
    });
    updateButtons.forEach(btn => {
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Downloading...';
        }
    });

    if (resultDiv) {
        resultDiv.style.display = 'block';
        resultDiv.className = 'test-result success';
        resultDiv.textContent = '📥 Downloading and installing update... The application will restart automatically.';
    }

    const result = await postJSON('/api/update/install');
    if (result && result.success) {
        showToast('success', 'Update downloaded! Restarting...');
        if (resultDiv) resultDiv.textContent = '✅ Update downloaded! Restarting...';
    } else {
        // A successful install shuts the server down, so a missing reply is only an error
        // when nothing at all came back before the update started.
        const message = (result && result.error) || 'Connection lost during update.';
        showToast('error', message);
        if (resultDiv) {
            resultDiv.className = 'test-result error';
            resultDiv.textContent = '❌ ' + message;
        }
        restoreButtons();
    }
}

function dismissUpdate() {
    const banner = document.getElementById('update-banner');
    if (banner) banner.style.display = 'none';
}
