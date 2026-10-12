/* QUALIHOME — Main JavaScript */

// ── Toast duration ───────────────────────────────────────────────
var SQH_TOAST_DURATION = 4500;

// ── Dismiss a toast element ──────────────────────────────────────
function dismissToast(toast) {
  if (toast.dataset.sqhDismissed) return;
  toast.dataset.sqhDismissed = '1';
  toast.classList.remove('sqh-toast--visible');
  toast.classList.add('sqh-toast--hiding');
  setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 350);
}

// ── Programmatic toast (usable from any page script) ────────────
function showToast(message, type, title) {
  type = type || 'danger';
  var container = document.getElementById('sqh-toast-container');
  if (!container) return;

  var icons  = { success: 'fa-check-circle', danger: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
  var titles = { success: 'Success', danger: 'Error', warning: 'Warning', info: 'Notice' };
  var t = title || titles[type] || 'Notice';

  var toast = document.createElement('div');
  toast.className = 'sqh-toast sqh-toast--' + type;
  toast.setAttribute('role', 'alert');
  toast.innerHTML =
    '<div class="sqh-toast-icon"><i class="fas ' + (icons[type] || 'fa-info-circle') + '"></i></div>' +
    '<div class="sqh-toast-body">' +
      '<div class="sqh-toast-title">' + t + '</div>' +
      '<div class="sqh-toast-msg">' + message + '</div>' +
    '</div>' +
    '<button class="sqh-toast-close" aria-label="Close"><i class="fas fa-times"></i></button>' +
    '<div class="sqh-toast-progress"><div class="sqh-toast-progress-fill"></div></div>';

  container.appendChild(toast);

  // Slide in
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { toast.classList.add('sqh-toast--visible'); });
  });

  // Progress bar
  var fill = toast.querySelector('.sqh-toast-progress-fill');
  if (fill) {
    fill.style.transition = 'none';
    fill.style.width = '100%';
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        fill.style.transition = 'width ' + SQH_TOAST_DURATION + 'ms linear';
        fill.style.width = '0%';
      });
    });
  }

  var timer = setTimeout(function () { dismissToast(toast); }, SQH_TOAST_DURATION);
  var closeBtn = toast.querySelector('.sqh-toast-close');
  if (closeBtn) {
    closeBtn.addEventListener('click', function () {
      clearTimeout(timer);
      dismissToast(toast);
    });
  }
}

// ── Image source resolution (Cloudinary-aware) ──────────────────
// Absolute URLs (Cloudinary) are used directly; legacy local
// filenames are served through /uploads/.
function sqhImgSrc(ref) {
  ref = String(ref == null ? '' : ref).trim();
  if (/^https?:\/\//i.test(ref)) return ref;
  return '/uploads/' + encodeURIComponent(ref);
}

// ── Numeric input formatting (thousands separators) ──────────────
function sqhCleanNumeric(str) {
  return String(str == null ? '' : str).replace(/[,\s\u20B1]/g, '');
}

function sqhFormatNumericValue(raw) {
  var s = String(raw == null ? '' : raw);
  var negative = s.charAt(0) === '-';
  s = s.replace(/[^\d.]/g, '');
  var firstDot = s.indexOf('.');
  if (firstDot !== -1) {
    s = s.slice(0, firstDot + 1) + s.slice(firstDot + 1).replace(/\./g, '');
  }
  if (!s || s === '.') return negative ? '-' : '';
  var parts = s.split('.');
  parts[0] = parts[0].replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (negative ? '-' : '') + parts.join('.');
}

function sqhBindNumericFormatting(root) {
  (root || document).querySelectorAll('input[data-commas]').forEach(function (el) {
    // WTForms DecimalField renders type="number" by default; commas are
    // invalid in number inputs so the browser blanks "1,000" on the 4th
    // digit. Force text BEFORE binding (and on re-bind) so formatting sticks.
    try { el.setAttribute('type', 'text'); } catch (_) {}
    try { if (el.type !== 'text') el.type = 'text'; } catch (_) {}

    if (el.value) {
      var raw = el.value.trim();
      if (raw === '0.00' || raw === '0') {
        if (el.placeholder) {
          el.value = '';
        }
      } else {
        if (/\.00$/.test(raw)) {
          raw = raw.slice(0, -3);
        }
        var formatted = sqhFormatNumericValue(raw);
        if (formatted !== el.value) {
          el.value = formatted;
        }
      }
    }

    if (el.dataset.sqhCommasBound) return;
    el.dataset.sqhCommasBound = '1';
    el.setAttribute('inputmode', 'decimal');
    el.addEventListener('input', function () {
      var tailLen = null;
      if (typeof el.selectionStart === 'number') {
        tailLen = el.value.length - el.selectionStart;
      }
      var formatted = sqhFormatNumericValue(el.value);
      if (formatted !== el.value) {
        el.value = formatted;
        if (tailLen !== null) {
          var pos = Math.max(0, el.value.length - tailLen);
          try { el.setSelectionRange(pos, pos); } catch (_) {}
        }
      }
    });
    el.addEventListener('blur', function () {
      if (!el.value) return;
      var v = el.value.trim();
      if (/\.00$/.test(v)) {
        el.value = v.slice(0, -3);
      } else if (/\.$/.test(v)) {
        el.value = v.slice(0, -1);
      }
    });
  });
}

// ── Percentage input formatting (clean decimals, NO commas) ─────
function sqhFormatPercentageValue(raw) {
  var s = String(raw == null ? '' : raw);
  // Keep only digits and decimal points (strip commas, %, letters, spaces)
  s = s.replace(/[^\d.]/g, '');
  var firstDot = s.indexOf('.');
  if (firstDot !== -1) {
    s = s.slice(0, firstDot + 1) + s.slice(firstDot + 1).replace(/\./g, '');
  }
  return s;
}

function sqhBindPercentageInputs(root) {
  (root || document).querySelectorAll('input[data-percent]').forEach(function (el) {
    try { el.setAttribute('type', 'text'); } catch (_) {}
    try { if (el.type !== 'text') el.type = 'text'; } catch (_) {}

    if (el.value) {
      var raw = el.value.trim();
      if ((raw === '0.00' || raw === '0') && el.placeholder && el.id !== 'acp_annual_interest_rate' && el.id !== 'ep_annual_interest_rate') {
        el.value = '';
      } else {
        var clean = sqhFormatPercentageValue(raw);
        if (clean !== el.value) {
          el.value = clean;
        }
      }
    }

    if (el.dataset.sqhPercentBound) return;
    el.dataset.sqhPercentBound = '1';
    el.setAttribute('inputmode', 'decimal');

    el.addEventListener('input', function () {
      var tailLen = null;
      if (typeof el.selectionStart === 'number') {
        tailLen = el.value.length - el.selectionStart;
      }
      var clean = sqhFormatPercentageValue(el.value);
      if (clean !== el.value) {
        el.value = clean;
        if (tailLen !== null) {
          var pos = Math.max(0, el.value.length - tailLen);
          try { el.setSelectionRange(pos, pos); } catch (_) {}
        }
      }
    });

    el.addEventListener('blur', function () {
      if (!el.value) return;
      var v = el.value.trim();
      if (/\.$/.test(v)) {
        el.value = v.slice(0, -1);
      }
    });
  });
}

window.sqhCleanNumeric = sqhCleanNumeric;
window.sqhFormatNumericValue = sqhFormatNumericValue;
window.sqhBindNumericFormatting = sqhBindNumericFormatting;
window.sqhFormatPercentageValue = sqhFormatPercentageValue;
window.sqhBindPercentageInputs = sqhBindPercentageInputs;

/* ── ZoomDragController — shared by all image preview modals ── */
// Single definition site: base.html loads main.js before all dashboard
// bundles, so admin/agent/client dashboards reuse this instead of
// duplicating zoom/drag state. ES5 prototype style to match the
// var-based dashboard JS. Null-guarded because admin_dashboard.js also
// loads on agent/client pages where its modal elements are absent.
window.ZoomDragController = (function () {
  function ZoomDragController(options) {
    options = options || {};
    this.frame   = options.frame;
    this.wrapper = options.wrapper;
    this.img     = options.img;
    this.zoomIn  = options.zoomIn;
    this.zoomOut = options.zoomOut;
    this.reset   = options.reset;
    this.scale   = 1;
    this.panX    = 0;
    this.panY    = 0;
    this.dragging    = false;
    this.dragStartX  = 0;
    this.dragStartY  = 0;
    this._bindButtons();
    this._bindPointer();
    this._bindWheel();
    if (this.img) this.img.addEventListener('load', () => this.resetZoom());
    window.addEventListener('resize', () => { this._clamp(); this._apply(); });
  }

  ZoomDragController.prototype._clamp = function () {
    if (!this.frame || !this.img) return;
    if (this.scale <= 1.0001) { this.panX = 0; this.panY = 0; return; }
    var fw = this.frame.clientWidth;
    var fh = this.frame.clientHeight;
    var sw = this.img.offsetWidth  * this.scale;
    var sh = this.img.offsetHeight * this.scale;
    var maxX = Math.max(0, (sw - fw) / 2);
    var maxY = Math.max(0, (sh - fh) / 2);
    this.panX = Math.max(-maxX, Math.min(maxX, this.panX));
    this.panY = Math.max(-maxY, Math.min(maxY, this.panY));
  };

  ZoomDragController.prototype._apply = function () {
    if (!this.wrapper) return;
    this.wrapper.style.transform = 'translate(' + this.panX + 'px,' + this.panY + 'px) scale(' + this.scale + ')';
    this.wrapper.style.transformOrigin = 'center center';
    this.wrapper.classList.toggle('is-dragging', this.dragging);
    if (this.reset) this.reset.textContent = Math.round(this.scale * 100) + '%';
  };

  ZoomDragController.prototype.setZoom = function (next) {
    this.scale = Math.max(1, Math.min(4, next));
    this._clamp(); this._apply();
  };

  ZoomDragController.prototype.resetZoom = function () {
    this.scale = 1; this.panX = 0; this.panY = 0; this.dragging = false;
    this._apply();
  };

  ZoomDragController.prototype.startDrag = function (cx, cy) {
    this.dragging = true;
    this.dragStartX = cx - this.panX;
    this.dragStartY = cy - this.panY;
    this._apply();
  };

  ZoomDragController.prototype.moveDrag = function (cx, cy) {
    if (!this.dragging) return;
    this.panX = cx - this.dragStartX;
    this.panY = cy - this.dragStartY;
    this._clamp(); this._apply();
  };

  ZoomDragController.prototype.endDrag = function () {
    if (!this.dragging) return;
    this.dragging = false; this._apply();
  };

  ZoomDragController.prototype._bindButtons = function () {
    var self = this;
    if (this.zoomIn)  this.zoomIn.addEventListener('click',  function () { self.setZoom(self.scale + 0.25); });
    if (this.zoomOut) this.zoomOut.addEventListener('click', function () { self.setZoom(self.scale - 0.25); });
    if (this.reset)   this.reset.addEventListener('click',   function () { self.resetZoom(); });
  };

  ZoomDragController.prototype._bindPointer = function () {
    var self = this;
    var isPinching = false, pinchStartDist = 0, pinchStartScale = 1;
    var pinchMidX = 0, pinchMidY = 0, pinchStartPanX = 0, pinchStartPanY = 0;

    function dist(t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); }
    function mid(t)  { return { x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 }; }

    if (!self.wrapper) return;
    this.wrapper.addEventListener('mousedown', function (e) {
      e.preventDefault(); self.startDrag(e.clientX, e.clientY);
    });
    this.wrapper.addEventListener('touchstart', function (e) {
      if (e.touches.length === 2) {
        isPinching = true; self.dragging = false;
        pinchStartDist  = dist(e.touches);
        pinchStartScale = self.scale;
        pinchStartPanX  = self.panX;
        pinchStartPanY  = self.panY;
        var rect = self.wrapper.getBoundingClientRect();
        var m = mid(e.touches);
        pinchMidX = m.x - rect.left - rect.width  / 2;
        pinchMidY = m.y - rect.top  - rect.height / 2;
      } else if (e.touches.length === 1 && !isPinching) {
        self.startDrag(e.touches[0].clientX, e.touches[0].clientY);
      }
    }, { passive: true });

    window.addEventListener('mousemove', function (e) { self.moveDrag(e.clientX, e.clientY); });
    window.addEventListener('mouseup',   function ()  { self.endDrag(); });

    window.addEventListener('touchmove', function (e) {
      if (e.touches.length === 2 && isPinching) {
        e.preventDefault();
        var newDist = dist(e.touches);
        if (!pinchStartDist) return;
        var ratio   = newDist / pinchStartDist;
        var newScale = Math.max(1, Math.min(4, pinchStartScale * ratio));
        var scaleRatio = newScale / pinchStartScale;
        self.scale = newScale;
        self.panX  = pinchMidX + scaleRatio * (pinchStartPanX - pinchMidX);
        self.panY  = pinchMidY + scaleRatio * (pinchStartPanY - pinchMidY);
        self._clamp(); self._apply();
      } else if (e.touches.length === 1 && !isPinching) {
        if (!e.touches.length) return;
        if (self.dragging) e.preventDefault();
        self.moveDrag(e.touches[0].clientX, e.touches[0].clientY);
      }
    }, { passive: false });

    window.addEventListener('touchend', function (e) {
      if (e.touches.length < 2) isPinching = false;
      if (e.touches.length === 0) self.endDrag();
    });
  };

  ZoomDragController.prototype._bindWheel = function () {
    var self = this;
    if (!self.wrapper) return;
    this.wrapper.addEventListener('wheel', function (e) {
      e.preventDefault();
      var rect  = self.wrapper.getBoundingClientRect();
      var cx    = e.clientX - rect.left  - rect.width  / 2;
      var cy    = e.clientY - rect.top   - rect.height / 2;
      var delta = e.deltaY > 0 ? -0.2 : 0.2;
      var prev  = self.scale;
      if (!prev) return;
      self.scale = Math.max(1, Math.min(4, prev + delta));
      var ratio = self.scale / prev;
      self.panX = cx + ratio * (self.panX - cx);
      self.panY = cy + ratio * (self.panY - cy);
      self._clamp(); self._apply();
    }, { passive: false });
  };

  return ZoomDragController;
}());

/* ── Optimized image delivery + slide cache ── */
// Rewrites absolute Cloudinary URLs to the f_auto,q_auto:best delivery
// variant (optimal format, near-original quality). Local /uploads refs
// pass through untouched. These on-the-fly transforms are CDN-cached
// after the first hit, so no upload-time or backend change is needed.
function sqhOptimizedSrc(ref) {
  var url = (typeof sqhImgSrc === 'function') ? sqhImgSrc(ref) : String(ref == null ? '' : ref);
  if (!/^https?:\/\/res\.cloudinary\.com\//i.test(url)) return url;
  return url.replace(/(\/image\/upload\/)(?!f_auto)/, '$1f_auto,q_auto:best/');
}

// In-memory preloader for carousel slides: preloads neighbors so
// next/back swaps hit the browser cache, and swaps an <img> only after
// the target has decoded (no fade-to-blank flash).
window.SqhImageCache = (function () {
  var ready = {};
  var pending = {};
  function preload(ref) {
    var url = sqhOptimizedSrc(ref);
    if (!url || ready[url] || pending[url]) return;
    pending[url] = true;
    var im = new Image();
    im.onload = im.onerror = function () { delete pending[url]; ready[url] = true; };
    im.src = url;
  }
  function preloadAll(list) {
    (list || []).forEach(function (ref) { preload(ref); });
  }
  function preloadNeighbors(list, idx) {
    if (!list || !list.length) return;
    var n = list.length;
    preload(list[(idx + 1) % n]);
    preload(list[(idx - 1 + n) % n]);
  }
  function swap(imgEl, ref, done) {
    var url = sqhOptimizedSrc(ref);
    if (!imgEl || !url) { if (typeof done === 'function') done(); return; }
    if (imgEl.getAttribute('src') === url) { if (typeof done === 'function') done(); return; }
    var im = new Image();
    im.onload = function () {
      ready[url] = true;
      imgEl.src = url;
      if (typeof done === 'function') done();
    };
    im.onerror = function () { if (typeof done === 'function') done(); };
    im.src = url;
  }
  function isReady(ref) { return !!ready[sqhOptimizedSrc(ref)]; }
  return {
    preload: preload,
    preloadAll: preloadAll,
    preloadNeighbors: preloadNeighbors,
    swap: swap,
    isReady: isReady,
    optimize: sqhOptimizedSrc
  };
}());

document.addEventListener('DOMContentLoaded', function () {

  // ── Comma formatting for numeric inputs ───────────────────────
  sqhBindNumericFormatting();
  sqhBindPercentageInputs();

  // ── Activate existing flash toasts ──────────────────────────────
  document.querySelectorAll('.sqh-toast').forEach(function (toast) {
    // Slide in
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        toast.classList.add('sqh-toast--visible');
      });
    });

    // Progress bar countdown
    var fill = toast.querySelector('.sqh-toast-progress-fill');
    if (fill) {
      fill.style.transition = 'none';
      fill.style.width = '100%';
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          fill.style.transition = 'width ' + SQH_TOAST_DURATION + 'ms linear';
          fill.style.width = '0%';
        });
      });
    }

    // Auto-dismiss
    var timer = setTimeout(function () { dismissToast(toast); }, SQH_TOAST_DURATION);

    // Manual close
    var closeBtn = toast.querySelector('.sqh-toast-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        clearTimeout(timer);
        dismissToast(toast);
      });
    }
  });

  // ── Activate current nav-link based on URL ─────────────────────
  var currentPath = window.location.pathname;
  document.querySelectorAll('.sqh-navbar .nav-link').forEach(function (link) {
    if (link.getAttribute('href') === currentPath) {
      link.classList.add('active');
    }
  });

  // ── Password toggle (show/hide) ───────────────────────────────
  document.querySelectorAll('.toggle-password').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var targetId = btn.dataset.target;
      var input    = document.getElementById(targetId);
      var icon     = btn.querySelector('i');
      if (!input) return;
      if (input.type === 'password') {
        input.type = 'text';
        icon.classList.replace('fa-eye', 'fa-eye-slash');
      } else {
        input.type = 'password';
        icon.classList.replace('fa-eye-slash', 'fa-eye');
      }
    });
  });

  // ── Confirm action modal trigger ──────────────────────────────
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      var msg = el.dataset.confirm || 'Are you sure?';
      if (!window.confirm(msg)) e.preventDefault();
    });
  });

});
