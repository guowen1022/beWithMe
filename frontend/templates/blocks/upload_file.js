({
  id: '__BLOCK_ID__',
  grid: { x: __GRID_X__, y: __GRID_Y__, w: __GRID_W__, h: __GRID_H__ },
  content: '',
  style: {
    background: 'var(--bw-surface)',
    color: 'var(--bw-ink)',
    fontFamily: 'var(--bw-font-sans)',
    borderRadius: '0',
    border: '1px solid var(--bw-border)',
    padding: '14px 18px',
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
    overflow: 'hidden',
  },
  publishes: ['__DOC_TOPIC__'],
  run(root, bus, cleanup, helpers) {
    var report = helpers && helpers.reportState ? helpers.reportState : function () {};
    var backend = helpers && helpers.backend ? helpers.backend : null;
    var blockId = (helpers && helpers.blockId) || root.getAttribute('data-block-id') || '__BLOCK_ID__';
    // Back-compat fallback when helpers.backend isn't present.
    var userId = (typeof localStorage !== 'undefined' && localStorage.getItem('bewithme_user_id')) || '';

    // Icon
    var icon = document.createElement('div');
    icon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
    icon.style.cssText =
      'flex-shrink:0; display:flex; align-items:center; justify-content:center;' +
      'width:34px; height:34px; border-radius:0;' +
      'color:var(--bw-accent);' +
      'background:var(--bw-accent-soft);' +
      'border:1px solid var(--bw-accent);';

    // Text column
    var textCol = document.createElement('div');
    textCol.style.cssText =
      'display:flex; flex-direction:column; flex:1; min-width:0;';
    var title = document.createElement('div');
    title.textContent = 'Upload a PDF';
    title.style.cssText =
      'font-size:13px; font-weight:600; color:var(--bw-ink);' +
      'letter-spacing:-0.005em;';
    var status = document.createElement('div');
    status.textContent = 'No file chosen';
    status.style.cssText =
      'font-family:var(--bw-font-mono); font-size:10px;' +
      'color:var(--bw-ink-faint); margin-top:3px;' +
      'text-transform:uppercase; letter-spacing:.08em;' +
      'overflow:hidden; text-overflow:ellipsis; white-space:nowrap;';
    textCol.appendChild(title);
    textCol.appendChild(status);

    // Hidden native input + styled button label
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = 'application/pdf';
    input.style.display = 'none';

    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'CHOOSE FILE';
    button.style.cssText =
      'padding:8px 14px; border:none; border-radius:0;' +
      'font-family:var(--bw-font-mono); font-size:11px;' +
      'font-weight:500; letter-spacing:.1em;' +
      'color:#E8EEFA; background:var(--bw-accent);' +
      'cursor:pointer; flex-shrink:0;' +
      'transition:filter 0.15s ease;';

    var hover = function () { button.style.filter = 'brightness(1.1)'; };
    var unhover = function () { button.style.filter = 'none'; };
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
      console.log('[upload_file:trace] onChange fired, file:', f && f.name, 'size:', f && f.size);
      if (!f) { console.log('[upload_file:trace] no file in event, bailing'); return; }
      status.textContent = f.name;
      title.textContent = 'Uploading…';
      setBusy(true);
      console.log('[upload_file:trace] backend.upload available?', !!(backend && backend.upload));
      report({ kind: 'upload', content: 'Uploading ' + f.name });
      var fd = new FormData();
      fd.append('file', f);
      var p;
      if (backend && backend.upload) {
        // helpers.backend resolved from the template manifest. Already
        // injects auth + device headers + multipart Content-Type.
        p = backend.upload(fd).then(function (r) {
          if (!r.ok) throw new Error('upload failed: ' + r.status);
          return r.data;
        });
      } else {
        // Backward-compat path: helpers.backend not present (template
        // wasn't manifest-aware). Fall through to a direct fetch.
        p = fetch('/api/documents/upload', {
          method: 'POST',
          headers: userId ? { 'X-User-Id': userId } : {},
          body: fd,
        }).then(function (res) {
          if (!res.ok) throw new Error('upload failed: ' + res.status);
          return res.json();
        });
      }
      p.then(function (json) {
        console.log('[upload_file:trace] upload SUCCESS, response:', json);
        title.textContent = 'Ready';
        var summary = (json.title || json.filename || json.id) + ' · ' + (json.pages || '?') + ' pages';
        status.textContent = summary;
        // Semantic success — outside the theme accent on purpose. Status
        // colors stay green/red across themes for universal recognition.
        icon.style.color = '#7ED4A6';
        icon.style.background = 'rgba(126,212,166,0.12)';
        icon.style.border = '1px solid rgba(126,212,166,0.4)';
        console.log('[upload_file:trace] publishing documents.uploaded topic, id:', json.id);
        bus.publish('__DOC_TOPIC__', { id: json.id, title: json.title, pages: json.pages });
        // `completed: true` is the trigger signal for the teacher's
        // event-driven turn. The backend edge-detects the false→true
        // transition and wakes the tool loop with this state.
        report({
          kind: 'upload',
          content: 'Ready: ' + summary,
          completed: true,
          extra: {
            document_id: json.id,
            title: json.title,
            pages: json.pages,
            filename: json.filename,
          },
        });
        // Deterministically swap to the PDF reader and unmount this
        // upload widget. Don't leave it on the canvas crowding the user
        // — the upload step is done and the user shouldn't have to
        // wait for the teacher's tool loop to clean up. pdf_reader
        // subscribes to the same documents.uploaded topic; sticky
        // pub/sub means the value we just published is replayed to it
        // on subscribe, so no race on doc loading.
        console.log('[upload_file:trace] backend.mount_template available?', !!(backend && backend.mount_template), 'blockId:', blockId);
        if (backend && backend.mount_template) {
          console.log('[upload_file:trace] calling mount_template(pdf_reader, replace=[' + blockId + '])');
          backend.mount_template({
            template: 'pdf_reader',
            replace: [blockId],
          }).then(function (r) {
            console.log('[upload_file:trace] mount_template result:', r);
          }).catch(function (err) {
            console.warn('[upload_file:trace] mount_template FAILED:', err);
          });
        } else {
          console.warn('[upload_file:trace] DETERMINISTIC SWAP SKIPPED — backend.mount_template not in helpers. Manifest may be missing the entry.');
        }
      })
        .catch(function (err) {
          title.textContent = 'Upload failed';
          var msg = err && err.message ? err.message : String(err);
          status.textContent = msg;
          // Semantic error — outside the theme accent on purpose.
          icon.style.color = '#E5837C';
          icon.style.background = 'rgba(229,131,124,0.12)';
          icon.style.border = '1px solid rgba(229,131,124,0.4)';
          report({ kind: 'upload', content: 'Upload failed: ' + msg });
        })
        .finally(function () { setBusy(false); });
    };
    input.addEventListener('change', onChange);
    cleanup(function () { input.removeEventListener('change', onChange); });

    // Report idle state on mount so the teacher knows the picker is up
    // even before the user clicks Choose File. Every state transition
    // below (uploading / ready / failed) overwrites this with the
    // current status — the perception cache always reflects what the
    // user is actually seeing.
    report({
      kind: 'upload',
      content: 'Upload a PDF (no file chosen)',
      extra: { stage: 'idle' },
    });
  },
})
