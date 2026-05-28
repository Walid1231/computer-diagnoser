/* ══════════════════════════════════════════════════════════════
   Computer Diagnoser — Frontend Application Logic
   ══════════════════════════════════════════════════════════════ */

const API = '';  // Same origin

// ─── NAVIGATION ──────────────────────────────────────────────

function navigateTo(sectionId) {
    // Update nav items
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.section === sectionId);
    });

    // Update sections
    document.querySelectorAll('.content-section').forEach(section => {
        section.classList.toggle('active', section.id === `section-${sectionId}`);
    });

    // Load section data
    loadSectionData(sectionId);
}

document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
        e.preventDefault();
        navigateTo(item.dataset.section);
    });
});

// ─── DATA LOADING ────────────────────────────────────────────

async function fetchJSON(url) {
    try {
        const res = await fetch(API + url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (err) {
        console.error(`Fetch error: ${url}`, err);
        return null;
    }
}

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

// ─── OVERVIEW ────────────────────────────────────────────────

async function loadOverview() {
    const [sysInfo, cpuInfo, memInfo, drives, battery, processes] = await Promise.all([
        fetchJSON('/api/system/info'),
        fetchJSON('/api/system/cpu'),
        fetchJSON('/api/system/memory'),
        fetchJSON('/api/storage/drives'),
        fetchJSON('/api/system/battery'),
        fetchJSON('/api/system/processes'),
    ]);

    // System info chips
    if (sysInfo) {
        setText('#chip-hostname .chip-value', sysInfo.hostname);
        setText('#chip-os .chip-value', sysInfo.os);
        setText('#chip-uptime .chip-value', sysInfo.uptime);
        setText('#chip-cpu-name .chip-value', truncate(sysInfo.processor, 40));
    }

    // CPU card
    if (cpuInfo) {
        const cpuPct = cpuInfo.overall_percent;
        setText('#overview-cpu-value', `${cpuPct}%`);
        setBarWidth('#overview-cpu-bar', cpuPct);
        setText('#overview-cpu-detail', `${cpuInfo.physical_cores} cores / ${cpuInfo.logical_cores} threads • ${cpuInfo.current_freq_mhz} MHz`);
    }

    // RAM card
    if (memInfo) {
        const ramPct = memInfo.ram.percent_used;
        setText('#overview-ram-value', `${ramPct}%`);
        setBarWidth('#overview-ram-bar', ramPct);
        setText('#overview-ram-detail', `${memInfo.ram.used_display} / ${memInfo.ram.total_display}`);
    }

    // Disk card (primary drive)
    if (drives && drives.length > 0) {
        const primary = drives[0];
        setText('#overview-disk-value', `${primary.percent_used}%`);
        setBarWidth('#overview-disk-bar', primary.percent_used);
        setText('#overview-disk-detail', `${primary.used_display} / ${primary.total_display} (${primary.mountpoint})`);

        renderDrivesGrid('#overview-drives-grid', drives);
        populateDriveSelector(drives);
    }

    // Battery card
    if (battery && battery.available) {
        setText('#overview-battery-value', `${battery.percent}%`);
        setBarWidth('#overview-battery-bar', battery.percent);
        setText('#overview-battery-detail', battery.power_plugged ? `Plugged in • ${battery.time_left}` : `On battery • ${battery.time_left}`);
    } else {
        setText('#overview-battery-value', 'N/A');
        setText('#overview-battery-detail', 'No battery detected');
    }

    // Processes
    if (processes) {
        renderProcessTable('#overview-processes-tbody', processes.slice(0, 8));
    }
}

// ─── STORAGE ─────────────────────────────────────────────────

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

async function scanDrive() {
    const btn = document.getElementById('btn-scan-drive');
    const path = document.getElementById('drive-selector').value;

    btn.disabled = true;
    btn.innerHTML = '<span class="loading-placeholder" style="padding:0;animation:shimmer 0.8s ease-in-out infinite;">Scanning...</span>';

    const [fileTypes, folders, largeFiles] = await Promise.all([
        fetchJSON(`/api/storage/file-types?path=${encodeURIComponent(path)}`),
        fetchJSON(`/api/storage/folder-sizes?path=${encodeURIComponent(path)}`),
        fetchJSON(`/api/storage/large-files?path=${encodeURIComponent(path)}&top_n=30&min_size_mb=50`),
    ]);

    if (fileTypes) renderFileTypes(fileTypes);
    if (folders) renderFolderSizes(folders);
    if (largeFiles) renderLargeFiles(largeFiles);

    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> Scan Now`;
}

function renderFileTypes(types) {
    const container = document.getElementById('storage-types-content');
    const badge = document.getElementById('storage-types-badge');
    if (!types.length) {
        container.innerHTML = '<div class="loading-placeholder">No file type data found.</div>';
        return;
    }

    const maxSize = types[0].size_bytes;
    const colors = [
        'var(--accent-blue)', 'var(--accent-purple)', 'var(--accent-cyan)',
        'var(--accent-green)', 'var(--accent-amber)', 'var(--accent-red)',
        'var(--accent-pink)', '#64748b', '#a78bfa', '#34d399'
    ];

    badge.textContent = `${types.length} categories`;

    container.innerHTML = types.map((t, i) => `
        <div class="type-bar-container">
            <div class="type-bar-label">
                <span class="type-bar-name">
                    <span class="dot" style="background:${colors[i % colors.length]}"></span>
                    ${t.category} <span style="color:var(--text-muted);font-size:0.75rem">(${t.count.toLocaleString()} files)</span>
                </span>
                <span class="type-bar-size">${t.size_display}</span>
            </div>
            <div class="type-bar">
                <div class="type-bar-fill" style="width:${(t.size_bytes / maxSize * 100).toFixed(1)}%;background:${colors[i % colors.length]}"></div>
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

    const maxSize = folders[0].size_bytes;

    container.innerHTML = folders.map(f => `
        <div class="folder-item">
            <span class="folder-icon">📁</span>
            <span class="folder-name" title="${f.path}">${f.name}</span>
            <span class="folder-size">${f.size_display}</span>
            <div class="folder-bar-wrap">
                <div class="type-bar">
                    <div class="type-bar-fill" style="width:${(f.size_bytes / maxSize * 100).toFixed(1)}%;background:var(--gradient-cyan, var(--accent-cyan))"></div>
                </div>
            </div>
        </div>
    `).join('');
}

function renderLargeFiles(files) {
    const tbody = document.getElementById('storage-large-files-tbody');
    if (!files.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading-placeholder">No large files found (> 50 MB).</td></tr>';
        return;
    }

    tbody.innerHTML = files.map(f => `
        <tr>
            <td title="${f.name}">${truncate(f.name, 40)}</td>
            <td style="color:var(--accent-amber);font-weight:600">${f.size_display}</td>
            <td>${f.extension || '—'}</td>
            <td title="${f.path}">${truncate(f.path, 50)}</td>
        </tr>
    `).join('');
}

function renderTempFiles(items) {
    const container = document.getElementById('storage-temp-content');
    const badge = document.getElementById('temp-total-badge');

    if (!items.length) {
        container.innerHTML = '<div class="loading-placeholder">No temp directories detected.</div>';
        badge.textContent = '0 B';
        return;
    }

    const totalBytes = items.reduce((s, i) => s + i.size_bytes, 0);
    badge.textContent = formatBytesJS(totalBytes);

    container.innerHTML = items.map(t => `
        <div class="temp-item">
            <div class="temp-info">
                <span class="temp-name">🗑️ ${t.name}</span>
                <span class="temp-path">${t.path}</span>
            </div>
            <span class="temp-size">${t.size_display}</span>
        </div>
    `).join('');
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

        // Per-core blocks
        const coresGrid = document.getElementById('perf-cpu-cores');
        coresGrid.innerHTML = cpuInfo.per_cpu_percent.map((pct, i) => {
            const level = pct < 30 ? 'low' : pct < 60 ? 'medium' : pct < 85 ? 'high' : 'critical';
            return `<div class="core-block ${level}" title="Core ${i}: ${pct}%">${pct}%</div>`;
        }).join('');
    }

    if (memInfo) {
        document.getElementById('perf-mem-details').innerHTML = `
            <div class="detail-item"><span class="detail-label">Total RAM</span><span class="detail-value">${memInfo.ram.total_display}</span></div>
            <div class="detail-item"><span class="detail-label">Used</span><span class="detail-value">${memInfo.ram.used_display} (${memInfo.ram.percent_used}%)</span></div>
            <div class="detail-item"><span class="detail-label">Available</span><span class="detail-value">${memInfo.ram.available_display}</span></div>
            <div class="detail-item"><span class="detail-label">Swap Used</span><span class="detail-value">${memInfo.swap.used_display} / ${memInfo.swap.total_display}</span></div>
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
    const host = document.getElementById('ping-host').value || '8.8.8.8';
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
                    <span class="net-iface-name">${iface.name}</span>
                    <span class="net-iface-status ${iface.is_up ? 'up' : 'down'}">${iface.is_up ? '● UP' : '● DOWN'}</span>
                </div>
                <div class="net-iface-details">
                    ${ipv4 ? `<span>IP: ${ipv4.address}</span>` : '<span>No IPv4</span>'}
                    <span>Speed: ${iface.speed_mbps} Mbps</span>
                    <span>Sent: ${iface.bytes_sent_display || '0 B'}</span>
                    <span>Received: ${iface.bytes_recv_display || '0 B'}</span>
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
        const statusClass = `status-${c.status.toLowerCase().replace('_', '')}`;
        return `
            <tr>
                <td>${c.process || '—'}</td>
                <td>${c.type}</td>
                <td>${c.local_addr}</td>
                <td>${c.remote_addr || '—'}</td>
                <td class="${statusClass}">${c.status}</td>
            </tr>
        `;
    }).join('');
}

async function refreshConnections() {
    const connections = await fetchJSON('/api/network/connections');
    if (connections) renderConnections(connections);
}

// ─── STARTUP ─────────────────────────────────────────────────

async function loadStartup() {
    const programs = await fetchJSON('/api/startup/programs');
    const badge = document.getElementById('startup-count-badge');
    const tbody = document.getElementById('startup-tbody');

    if (!programs || !programs.length) {
        badge.textContent = '0';
        tbody.innerHTML = '<tr><td colspan="4" class="loading-placeholder">No startup programs detected.</td></tr>';
        return;
    }

    badge.textContent = `${programs.length} items`;
    tbody.innerHTML = programs.map(p => `
        <tr>
            <td style="font-weight:500">${p.name}</td>
            <td><span class="badge">${p.scope}</span></td>
            <td>${p.source}</td>
            <td title="${p.command}">${truncate(p.command, 50)}</td>
            <td>${p.source === 'Registry' && (p.scope === 'Current User' || p.scope === 'All Users')
                ? `<button class="btn-action-delete" style="font-size:0.7rem;padding:4px 8px" onclick="toggleStartup('${escapeHtml(p.name)}', 'disable', '${escapeHtml(p.scope)}', '${escapeHtml(p.location)}')">Disable</button>`
                : '<span style="color:var(--text-muted)">—</span>'}</td>
        </tr>
    `).join('');
}

// ─── SHARED RENDERERS ────────────────────────────────────────

function renderDrivesGrid(selector, drives, large = false) {
    const container = document.querySelector(selector);

    container.innerHTML = drives.map(d => {
        const level = d.percent_used < 70 ? 'ok' : d.percent_used < 90 ? 'warning' : 'danger';
        return `
            <div class="drive-card">
                <div class="drive-header">
                    <span class="drive-name">${d.mountpoint}</span>
                    <span class="drive-percent ${level}">${d.percent_used}%</span>
                </div>
                <div class="drive-bar">
                    <div class="drive-bar-fill ${level}" style="width:${d.percent_used}%"></div>
                </div>
                <div class="drive-info">
                    <span>Used: ${d.used_display}</span>
                    <span>Free: ${d.free_display}</span>
                </div>
                <div class="drive-info" style="margin-top:4px">
                    <span>Total: ${d.total_display}</span>
                    <span>${d.fstype}</span>
                </div>
            </div>
        `;
    }).join('');
}

function renderProcessTable(selector, processes) {
    const tbody = document.querySelector(selector);
    if (!processes.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="loading-placeholder">No processes found.</td></tr>';
        return;
    }

    tbody.innerHTML = processes.map(p => {
        const statusClass = `status-${p.status.toLowerCase()}`;
        return `
            <tr>
                <td style="font-weight:500">${truncate(p.name, 30)}</td>
                <td>${p.pid}</td>
                <td style="color:var(--accent-cyan)">${p.memory_display}</td>
                <td>${p.cpu_percent}%</td>
                <td class="${statusClass}">${p.status}</td>
                <td><button class="btn-action-delete" style="font-size:0.7rem;padding:4px 8px" onclick="killProcess(${p.pid}, '${escapeHtml(p.name)}')">Kill</button></td>
            </tr>
        `;
    }).join('');
}

function populateDriveSelector(drives) {
    const selector = document.getElementById('drive-selector');
    if (!selector) return;

    const currentValue = selector.value;
    selector.innerHTML = drives.map(d =>
        `<option value="${d.mountpoint}">${d.mountpoint} (${d.free_display} free)</option>`
    ).join('');

    // Preserve previous selection if still valid
    const options = Array.from(selector.options).map(o => o.value);
    if (options.includes(currentValue)) {
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
    return `${size.toFixed(2)} ${units[i]}`;
}

// ─── CHAT / DIAGNOSE ─────────────────────────────────────────

async function sendChat() {
    const input = document.getElementById('chat-input');
    const question = input.value.trim();
    if (!question) return;

    input.value = '';
    addChatBubble('user', question);
    addTypingIndicator();

    try {
        const res = await fetch(API + '/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });
        const data = await res.json();
        removeTypingIndicator();
        addBotResponse(data);
    } catch (err) {
        removeTypingIndicator();
        addChatBubble('bot', 'Sorry, something went wrong. Please try again.', 'Error');
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
            <strong>${label}</strong>
            <p>${escapeHtml(text)}</p>
        </div>
    `;

    messages.appendChild(bubble);
    scrollChatToBottom();
}

function addBotResponse(data) {
    const messages = document.getElementById('chat-messages');
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble bot';

    // Render messages with basic markdown-like formatting
    const renderedLines = data.messages.map(line => {
        let html = escapeHtml(line);
        // Bold: **text**
        html = html.replace(/\*\*(.+?)\*\*/g, '<b style="color:var(--text-primary)">$1</b>');
        // Code: `text`
        html = html.replace(/`(.+?)`/g, '<code>$1</code>');
        // Horizontal rule
        if (/^─+$/.test(line.trim())) {
            return '<hr style="border:none;border-top:1px solid var(--border-color);margin:8px 0">';
        }
        return `<span class="chat-line">${html}</span>`;
    }).join('');

    // Render action buttons
    let actionsHtml = '';
    if (data.actions && data.actions.length > 0) {
        const buttons = data.actions.map((action, i) => {
            const actionId = `action-${Date.now()}-${i}`;
            const dataAttr = escapeHtml(JSON.stringify(action));
            return `<button class="btn-action-delete" id="${actionId}" onclick='promptDelete(${JSON.stringify(action).replace(/'/g, "&#39;")})'>
                🗑️ ${escapeHtml(action.label)}
            </button>`;
        }).join('');
        actionsHtml = `<div class="chat-actions">${buttons}</div>`;
    }

    bubble.innerHTML = `
        <div class="chat-avatar">D</div>
        <div class="chat-content">
            <strong>${data.title || 'Diagnoser'}</strong>
            ${renderedLines}
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

// ─── DELETE / CLEAN ACTIONS ──────────────────────────────────

let pendingAction = null;

function promptDelete(action) {
    pendingAction = action;
    const modal = document.getElementById('modal-overlay');
    const title = document.getElementById('modal-title');
    const message = document.getElementById('modal-message');
    const btn = document.getElementById('modal-confirm-btn');

    if (action.type === 'delete_file') {
        title.textContent = 'Delete File?';
        message.textContent = `Are you sure you want to permanently delete "${action.name}" (${action.size})?\n\nPath: ${action.path}\n\nThis cannot be undone.`;
        btn.textContent = 'Delete File';
    } else if (action.type === 'delete_folder_contents' || action.type === 'delete_folder') {
        title.textContent = 'Clean Folder?';
        message.textContent = `Are you sure you want to clean all contents of "${action.name}" (${action.size})?\n\nPath: ${action.path}\n\nThis will delete all files inside this folder.`;
        btn.textContent = 'Clean Folder';
    }

    modal.style.display = 'flex';
}

function closeModal() {
    document.getElementById('modal-overlay').style.display = 'none';
    pendingAction = null;
}

async function confirmModalAction() {
    if (!pendingAction) return;

    const action = pendingAction;
    closeModal();

    let endpoint, body;

    if (action.type === 'delete_file') {
        endpoint = '/api/action/delete-file';
        body = { path: action.path };
    } else {
        endpoint = '/api/action/clean-folder';
        body = { path: action.path };
    }

    try {
        const res = await fetch(API + endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await res.json();

        if (result.success) {
            showToast('success', `${result.message} — freed ${result.freed}`);
            // Update the button that triggered this
            addChatBubble('bot',
                `Done! ${result.message}\nFreed: ${result.freed}${result.errors ? ` (${result.errors} files couldn't be deleted — may be in use)` : ''}`,
                'Action Complete'
            );
        } else {
            showToast('error', result.error || 'Failed to delete.');
            addChatBubble('bot', `Failed: ${result.error}`, 'Error');
        }
    } catch (err) {
        showToast('error', 'Network error — could not complete action.');
    }
}

// ─── TOAST NOTIFICATIONS ─────────────────────────────────────

function showToast(type, message) {
    // Remove existing toasts
    document.querySelectorAll('.toast').forEach(t => t.remove());

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => { toast.remove(); }, 4000);
}

// ─── HELPERS ─────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ─── HEALTH SECTION ──────────────────────────────────────────

async function loadHealth() {
    const [temps, battery, disks, crashes, programs] = await Promise.all([
        fetchJSON('/api/health/temperatures'),
        fetchJSON('/api/health/battery'),
        fetchJSON('/api/health/disks'),
        fetchJSON('/api/health/crashes'),
        fetchJSON('/api/health/programs'),
    ]);

    // Temperature card
    if (temps) {
        if (temps.cpu_temp !== null) {
            setText('#health-temp-value', `${temps.cpu_temp}°C`);
        } else {
            setText('#health-temp-value', 'N/A');
        }
        setText('#health-temp-detail', temps.message || 'Unknown');
    }

    // Battery health card
    if (battery) {
        if (battery.health_percent) {
            setText('#health-battery-value', `${battery.health_percent}%`);
            setBarWidth('#health-battery-bar', battery.health_percent);
        } else {
            setText('#health-battery-value', battery.available ? '—' : 'N/A');
        }
        setText('#health-battery-detail', battery.message || '');
    }

    // Crash count card
    if (crashes) {
        const count = crashes.crash_count || 0;
        setText('#health-crash-value', count.toString());
        setText('#health-crash-detail', crashes.message || '');

        // Crash table
        const tbody = document.getElementById('crash-tbody');
        if (crashes.events && crashes.events.length > 0) {
            tbody.innerHTML = crashes.events.map(e => {
                const levelClass = e.level === 'Critical' ? 'color:var(--accent-red)' : 'color:var(--accent-amber)';
                return `<tr>
                    <td>${e.time}</td>
                    <td style="${levelClass};font-weight:600">${e.level}</td>
                    <td>${truncate(e.source, 30)}</td>
                    <td title="${escapeHtml(e.message)}">${truncate(e.message, 80)}</td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="loading-placeholder">✅ No critical errors found in the last 30 days.</td></tr>';
        }
    }

    // Disk health
    if (disks) {
        const container = document.getElementById('health-disks-content');
        if (disks.length > 0) {
            container.innerHTML = disks.map(d => {
                const color = d.health_level === 'good' ? 'var(--accent-green)'
                            : d.health_level === 'warning' ? 'var(--accent-amber)' : 'var(--accent-red)';
                return `<div class="net-iface">
                    <div class="net-iface-header">
                        <span class="net-iface-name">${d.name}</span>
                        <span class="net-iface-status ${d.health_level === 'good' ? 'up' : 'down'}" style="color:${color}">
                            ● ${d.health_status}
                        </span>
                    </div>
                    <div class="net-iface-details">
                        <span>Type: ${d.media_type}</span>
                        <span>Size: ${d.size_display}</span>
                        <span>Bus: ${d.bus_type}</span>
                        <span>Status: ${d.operational_status}</span>
                    </div>
                </div>`;
            }).join('');
        } else {
            container.innerHTML = '<div class="loading-placeholder">Could not read disk health. Try running as Administrator.</div>';
        }
    }

    // Installed programs
    if (programs) {
        const tbody = document.getElementById('programs-tbody');
        const badge = document.getElementById('programs-count-badge');
        badge.textContent = `${programs.length} apps`;
        tbody.innerHTML = programs.map(p => `
            <tr>
                <td style="font-weight:500">${truncate(p.name, 35)}</td>
                <td>${p.version || '—'}</td>
                <td>${truncate(p.publisher || '—', 25)}</td>
                <td style="color:var(--accent-amber);font-weight:600">${p.size_display}</td>
            </tr>
        `).join('');
    }
}

async function loadCrashLogs() {
    const crashes = await fetchJSON('/api/health/crashes');
    if (crashes) {
        const tbody = document.getElementById('crash-tbody');
        if (crashes.events && crashes.events.length > 0) {
            tbody.innerHTML = crashes.events.map(e => {
                const levelClass = e.level === 'Critical' ? 'color:var(--accent-red)' : 'color:var(--accent-amber)';
                return `<tr>
                    <td>${e.time}</td>
                    <td style="${levelClass};font-weight:600">${e.level}</td>
                    <td>${truncate(e.source, 30)}</td>
                    <td title="${escapeHtml(e.message)}">${truncate(e.message, 80)}</td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="4" class="loading-placeholder">✅ No critical errors found.</td></tr>';
        }
    }
}

// ─── SECURITY SECTION ────────────────────────────────────────

async function loadSecurity() {
    const security = await fetchJSON('/api/security/status');
    if (!security) return;

    const d = security.defender;
    const f = security.firewall;
    const u = security.updates;

    // Defender card
    const defColor = d.status_level === 'good' ? 'var(--accent-green)'
                   : d.status_level === 'danger' ? 'var(--accent-red)' : 'var(--accent-amber)';
    const defEl = document.getElementById('sec-defender-value');
    defEl.textContent = d.enabled ? (d.real_time_protection ? '✅ Protected' : '⚠️ Partial') : '❌ Disabled';
    defEl.style.color = defColor;
    defEl.style.webkitTextFillColor = defColor;
    defEl.style.background = 'none';
    setText('#sec-defender-detail', d.message);

    // Firewall card
    const fwColor = f.status_level === 'good' ? 'var(--accent-green)' : 'var(--accent-red)';
    const fwEl = document.getElementById('sec-firewall-value');
    fwEl.textContent = f.all_enabled ? '✅ All ON' : '❌ Partial';
    fwEl.style.color = fwColor;
    fwEl.style.webkitTextFillColor = fwColor;
    fwEl.style.background = 'none';
    setText('#sec-firewall-detail', f.message);

    // Firewall profiles
    const profilesContainer = document.getElementById('sec-firewall-profiles');
    if (f.profiles && f.profiles.length > 0) {
        profilesContainer.innerHTML = f.profiles.map(p => {
            const icon = p.enabled ? '🟢' : '🔴';
            return `<div class="net-iface">
                <div class="net-iface-header">
                    <span class="net-iface-name">${p.name}</span>
                    <span class="net-iface-status ${p.enabled ? 'up' : 'down'}">${icon} ${p.enabled ? 'Enabled' : 'DISABLED'}</span>
                </div>
            </div>`;
        }).join('');
    }

    // Updates card
    const uColor = u.status_level === 'good' ? 'var(--accent-green)' : 'var(--accent-amber)';
    const uEl = document.getElementById('sec-updates-value');
    uEl.textContent = u.status_level === 'good' ? '✅ Up to Date' : '⚠️ Check Updates';
    uEl.style.color = uColor;
    uEl.style.webkitTextFillColor = uColor;
    uEl.style.background = 'none';
    setText('#sec-updates-detail', u.message);

    // Recent updates table
    const tbody = document.getElementById('sec-updates-tbody');
    if (u.recent_updates && u.recent_updates.length > 0) {
        tbody.innerHTML = u.recent_updates.map(upd => `
            <tr>
                <td style="font-weight:600;color:var(--accent-blue)">${upd.id}</td>
                <td>${upd.description || '—'}</td>
                <td>${upd.installed_on}</td>
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
    resultDiv.textContent = 'Running speed test... This may take a few seconds.';

    const result = await fetchJSON('/api/network/speedtest');

    if (result && result.success) {
        resultDiv.className = 'speed-result success';
        resultDiv.innerHTML = `
            <div style="display:flex;gap:24px;align-items:center">
                <div><span style="font-size:2rem;font-weight:800;color:var(--accent-cyan)">${result.download_mbps}</span> <span style="color:var(--text-muted)">Mbps</span></div>
                <div><span style="font-size:1.2rem;font-weight:600">${result.latency_ms || '—'}</span> <span style="color:var(--text-muted)">ms latency</span></div>
            </div>
            <div style="margin-top:8px;color:var(--text-muted);font-size:0.8rem">Downloaded ${result.download_size} in ${result.download_time}s</div>
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
    if (!confirm(`Kill process "${name}" (PID: ${pid})?`)) return;

    try {
        const res = await fetch(API + '/api/tools/kill-process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pid }),
        });
        const result = await res.json();
        if (result.success) {
            showToast('success', result.message);
            refreshProcesses();
        } else {
            showToast('error', result.error);
        }
    } catch (err) {
        showToast('error', 'Could not kill process.');
    }
}

// ─── SYSTEM BOOST ────────────────────────────────────────────

async function systemBoost() {
    const btn = document.getElementById('btn-boost');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Boosting...'; }

    try {
        const res = await fetch(API + '/api/tools/system-boost', { method: 'POST' });
        const result = await res.json();
        if (result.success) {
            showToast('success', result.message);
        } else {
            showToast('error', 'Boost failed.');
        }
    } catch (err) {
        showToast('error', 'Could not run system boost.');
    }

    if (btn) { btn.disabled = false; btn.textContent = '⚡ System Boost'; }
}

// ─── STARTUP TOGGLE ──────────────────────────────────────────

async function toggleStartup(name, action, scope, location) {
    if (!confirm(`${action === 'disable' ? 'Disable' : 'Enable'} startup item "${name}"?`)) return;

    try {
        const res = await fetch(API + '/api/startup/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, action, scope, location }),
        });
        const result = await res.json();
        if (result.success) {
            showToast('success', result.message);
            loadStartup();
        } else {
            showToast('error', result.error);
        }
    } catch (err) {
        showToast('error', 'Could not toggle startup item.');
    }
}

// ─── INIT ────────────────────────────────────────────────────
// ─── SETTINGS ────────────────────────────────────────────────

let presetsData = {};

async function loadSettings() {
    // Load presets
    const presets = await fetchJSON('/api/config/presets');
    if (presets) {
        presetsData = presets;
        const select = document.getElementById('settings-provider');
        // Keep the default option
        select.innerHTML = '<option value="">-- Choose a provider --</option>';
        for (const [key, preset] of Object.entries(presets)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = preset.label;
            select.appendChild(opt);
        }
    }

    // Load current config
    const config = await fetchJSON('/api/config');
    if (config) {
        if (config.ai_provider) {
            // Try to match to a preset
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

        const badge = document.getElementById('settings-status-badge');
        if (config.ai_enabled && config.has_key) {
            badge.textContent = 'Active';
            badge.className = 'badge success';
        } else {
            badge.textContent = 'Not Configured';
            badge.className = 'badge';
        }

        // Update AI badge in chat section
        updateAiBadge(config.ai_enabled && config.has_key, config.ai_provider);
    }
}

function onProviderChange() {
    const select = document.getElementById('settings-provider');
    const key = select.value;
    if (!key || !presetsData[key]) return;

    const preset = presetsData[key];
    document.getElementById('settings-endpoint').value = preset.endpoint_url || '';
    document.getElementById('settings-model').value = preset.model_name || '';
    document.getElementById('settings-format').value = preset.api_format || 'openai';

    // Show/hide API key field based on needs_key
    const keyGroup = document.getElementById('settings-key-group');
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

    try {
        const res = await fetch(API + '/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await res.json();
        if (result.success) {
            showToast('success', 'Settings saved! AI mode is now active.');
            loadSettings(); // Refresh UI
        } else {
            showToast('error', result.message || 'Failed to save.');
        }
    } catch (err) {
        showToast('error', 'Could not save settings.');
    }
}

async function testConnection() {
    const btn = document.getElementById('btn-test-connection');
    const resultDiv = document.getElementById('settings-test-result');
    btn.textContent = 'Testing...';
    btn.disabled = true;
    resultDiv.style.display = 'none';

    try {
        const res = await fetch(API + '/api/config/test', { method: 'POST' });
        const result = await res.json();

        resultDiv.style.display = 'block';
        if (result.success) {
            resultDiv.className = 'test-result success';
            resultDiv.textContent = `✅ ${result.message} — "${result.response}"`;
        } else {
            resultDiv.className = 'test-result error';
            resultDiv.textContent = `❌ ${result.error}`;
        }
    } catch (err) {
        resultDiv.style.display = 'block';
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
    // Load settings status for AI badge
    fetchJSON('/api/config').then(config => {
        if (config) {
            updateAiBadge(config.ai_enabled && config.has_key, config.ai_provider);
        }
    });
    // Init version and check for updates
    initVersionAndUpdates();
});

// ─── UPDATES ──────────────────────────────────────────────────
let currentAppVersion = "2.0.0"; // default fallback

async function initVersionAndUpdates() {
    try {
        const data = await fetchJSON('/api/version');
        if (data && data.version) {
            currentAppVersion = data.version;
            const versionTags = document.querySelectorAll('#version-tag, #settings-current-version');
            versionTags.forEach(tag => {
                tag.textContent = 'v' + currentAppVersion;
            });
        }
    } catch (e) {
        console.error("Failed to fetch version", e);
    }
    
    // Background check for updates (silent)
    checkForUpdates(false);
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
            
            // Show top banner
            if (banner && bannerText) {
                bannerText.textContent = `🚀 A new version (v${data.latest_version}) is available!`;
                banner.style.display = 'flex';
            }
            
            if (manual) {
                showToast('success', `Version v${data.latest_version} is available!`);
            }
        } else {
            if (badge) {
                badge.className = 'badge';
                badge.textContent = 'Up to Date';
            }
            if (latestContainer) latestContainer.style.display = 'none';
            if (installBtn) installBtn.style.display = 'none';
            if (banner) banner.style.display = 'none';
            
            if (manual) {
                showToast('info', 'You are running the latest version.');
            }
        }
    } catch (err) {
        if (badge) {
            badge.className = 'badge offline';
            badge.textContent = 'Error';
        }
        if (manual) {
            showToast('error', 'Failed to check for updates.');
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

    try {
        const res = await fetch(API + '/api/update/install', { method: 'POST' });
        const result = await res.json();
        
        if (result.success) {
            showToast('success', 'Update downloaded! Restarting...');
            if (resultDiv) {
                resultDiv.textContent = '✅ Update complete! Restarting...';
            }
        } else {
            showToast('error', result.error || 'Failed to install update.');
            if (resultDiv) {
                resultDiv.className = 'test-result error';
                resultDiv.textContent = '❌ Error: ' + result.error;
            }
            updateButtons.forEach(btn => {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = btn === installBtn ? '⬆ Install Update' : 'Update Now';
                }
            });
        }
    } catch (err) {
        showToast('error', 'Connection lost during update.');
        if (resultDiv) {
            resultDiv.className = 'test-result error';
            resultDiv.textContent = '❌ Connection lost.';
        }
        updateButtons.forEach(btn => {
            if (btn) {
                btn.disabled = false;
                btn.textContent = btn === installBtn ? '⬆ Install Update' : 'Update Now';
            }
        });
    }
}

function dismissUpdate() {
    const banner = document.getElementById('update-banner');
    if (banner) banner.style.display = 'none';
}
