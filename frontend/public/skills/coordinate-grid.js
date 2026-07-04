// coordinate-grid skill — plays a Manim-rendered animation video.
// Invoked by note.js dispatchSkills() when it finds <div data-skill="coordinate-grid">.
//
// Config shape (JSON from the ```skill:coordinate-grid fence, authored by
// the present_coordinate_grid canvas tool — never hand-written):
//   video_url: "/api/renders/<uuid>.mp4"
//
// The renders route is authenticated by the X-User-Id header (like every
// API call), which a bare <video src> can't send — so we fetch the mp4
// ourselves with the header and play it from a blob URL.

(function (element, config) {
  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  function showStatus(msg) {
    var existing = element.querySelector('[data-skill-status]');
    if (existing) { existing.textContent = msg; return; }
    var div = document.createElement('div');
    div.setAttribute('data-skill-status', '1');
    div.style.cssText = [
      'display:flex', 'align-items:center', 'justify-content:center',
      'color:' + cssVar('--bw-ink-muted', '#888'),
      'font-size:13px', 'font-family:' + cssVar('--bw-font-sans', 'sans-serif'),
      'height:360px', 'width:100%',
    ].join(';');
    div.textContent = msg;
    element.appendChild(div);
  }

  function clearStatus() {
    var existing = element.querySelector('[data-skill-status]');
    if (existing) element.removeChild(existing);
  }

  var url = config && config.video_url;
  if (typeof url !== 'string' || url.indexOf('/api/renders/') !== 0) {
    showStatus('[coordinate-grid] missing or invalid video_url');
    return;
  }

  var userId = null;
  try { userId = localStorage.getItem('bewithme_user_id'); } catch (_) {}
  if (!userId) {
    showStatus('[coordinate-grid] no signed-in user');
    return;
  }

  showStatus('Loading animation…');
  fetch(url, { headers: { 'X-User-Id': userId } })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.blob();
    })
    .then(function (blob) {
      clearStatus();
      var video = document.createElement('video');
      video.src = URL.createObjectURL(blob);
      video.autoplay = true;
      video.loop = true;
      video.muted = true;
      video.playsInline = true;
      video.controls = true;
      video.style.cssText = 'width:100%;max-width:100%;display:block;background:#000;';
      video.addEventListener('error', function () {
        showStatus('[coordinate-grid] video failed to play');
      });
      element.appendChild(video);
    })
    .catch(function (err) {
      showStatus('[coordinate-grid] failed to load video (' + (err && err.message || err) + ')');
    });
})(element, config);
