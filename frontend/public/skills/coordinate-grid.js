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

(function (element, config, helpers) {
  // cssVar / showStatus / clearStatus are provided by note.js dispatchSkills
  // (bound to this container) — no longer copied into each skill.
  var cssVar = helpers.cssVar, showStatus = helpers.showStatus, clearStatus = helpers.clearStatus;

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
      // note.js re-dispatches skills on every content push; without this
      // revoke, each re-render pins another multi-MB blob for the life of
      // the document. Revoke once the element has the data (it keeps playing
      // and looping after the URL is gone) and on failure.
      var objectUrl = URL.createObjectURL(blob);
      video.src = objectUrl;
      video.autoplay = true;
      video.loop = true;
      video.muted = true;
      video.playsInline = true;
      video.controls = true;
      video.style.cssText = 'width:100%;max-width:100%;display:block;background:#000;';
      video.addEventListener('loadeddata', function () {
        URL.revokeObjectURL(objectUrl);
      });
      video.addEventListener('error', function () {
        URL.revokeObjectURL(objectUrl);
        showStatus('[coordinate-grid] video failed to play');
      });
      element.appendChild(video);
    })
    .catch(function (err) {
      showStatus('[coordinate-grid] failed to load video (' + (err && err.message || err) + ')');
    });
})(element, config, helpers);
