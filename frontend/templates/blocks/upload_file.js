({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  content: '',
  style: {
    background: 'linear-gradient(180deg, #1f2937 0%, #111827 100%)',
    color: '#f9fafb',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Inter", system-ui, sans-serif',
    borderRadius: '14px',
    border: '1px solid rgba(255,255,255,0.08)',
    boxShadow: '0 12px 40px rgba(0,0,0,0.45)',
    padding: '14px 18px',
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
    overflow: 'hidden',
  },
  publishes: ['__DOC_TOPIC__'],
  run(root, bus, cleanup) {
    var userId = (typeof localStorage !== 'undefined' && localStorage.getItem('bewithme_user_id')) || '';

    // Icon
    var icon = document.createElement('div');
    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
    icon.style.color = '#93c5fd';
    icon.style.flexShrink = '0';
    icon.style.display = 'flex';
    icon.style.alignItems = 'center';
    icon.style.justifyContent = 'center';
    icon.style.width = '36px';
    icon.style.height = '36px';
    icon.style.borderRadius = '10px';
    icon.style.background = 'rgba(59,130,246,0.15)';
    icon.style.border = '1px solid rgba(147,197,253,0.25)';

    // Text column
    var textCol = document.createElement('div');
    textCol.style.display = 'flex';
    textCol.style.flexDirection = 'column';
    textCol.style.flex = '1';
    textCol.style.minWidth = '0';
    var title = document.createElement('div');
    title.textContent = 'Upload a PDF';
    title.style.fontSize = '14px';
    title.style.fontWeight = '600';
    title.style.letterSpacing = '-0.01em';
    var status = document.createElement('div');
    status.textContent = 'No file chosen';
    status.style.fontSize = '12px';
    status.style.opacity = '0.6';
    status.style.marginTop = '2px';
    status.style.overflow = 'hidden';
    status.style.textOverflow = 'ellipsis';
    status.style.whiteSpace = 'nowrap';
    textCol.appendChild(title);
    textCol.appendChild(status);

    // Hidden native input + styled button label
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = 'application/pdf';
    input.style.display = 'none';

    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Choose file';
    button.style.padding = '8px 14px';
    button.style.fontSize = '13px';
    button.style.fontWeight = '500';
    button.style.fontFamily = 'inherit';
    button.style.color = '#0b1220';
    button.style.background = '#e5e7eb';
    button.style.border = '1px solid rgba(255,255,255,0.1)';
    button.style.borderRadius = '8px';
    button.style.cursor = 'pointer';
    button.style.flexShrink = '0';
    button.style.transition = 'background 0.15s ease';

    var hover = function () { button.style.background = '#f3f4f6'; };
    var unhover = function () { button.style.background = '#e5e7eb'; };
    button.addEventListener('mouseenter', hover);
    button.addEventListener('mouseleave', unhover);
    cleanup(function () {
      button.removeEventListener('mouseenter', hover);
      button.removeEventListener('mouseleave', unhover);
    });

    var openPicker = function () { input.click(); };
    button.addEventListener('click', openPicker);
    cleanup(function () { button.removeEventListener('click', openPicker); });

    root.appendChild(icon);
    root.appendChild(textCol);
    root.appendChild(button);
    root.appendChild(input);

    var setBusy = function (busy) {
      button.disabled = busy;
      button.style.opacity = busy ? '0.6' : '1';
      button.style.cursor = busy ? 'wait' : 'pointer';
    };

    var onChange = function (e) {
      var f = e.target.files && e.target.files[0];
      if (!f) return;
      status.textContent = f.name;
      title.textContent = 'Uploading…';
      setBusy(true);
      var fd = new FormData();
      fd.append('file', f);
      fetch('/api/documents/upload', {
        method: 'POST',
        headers: userId ? { 'X-User-Id': userId } : {},
        body: fd,
      })
        .then(function (res) {
          if (!res.ok) throw new Error('upload failed: ' + res.status);
          return res.json();
        })
        .then(function (json) {
          title.textContent = 'Ready';
          status.textContent = (json.title || json.filename || json.id) + ' · ' + (json.pages || '?') + ' pages';
          icon.style.color = '#86efac';
          icon.style.background = 'rgba(34,197,94,0.15)';
          icon.style.border = '1px solid rgba(134,239,172,0.3)';
          bus.publish('__DOC_TOPIC__', { id: json.id, title: json.title, pages: json.pages });
        })
        .catch(function (err) {
          title.textContent = 'Upload failed';
          status.textContent = err && err.message ? err.message : String(err);
          icon.style.color = '#fca5a5';
          icon.style.background = 'rgba(239,68,68,0.15)';
          icon.style.border = '1px solid rgba(252,165,165,0.3)';
        })
        .finally(function () { setBusy(false); });
    };
    input.addEventListener('change', onChange);
    cleanup(function () { input.removeEventListener('change', onChange); });
  },
})
