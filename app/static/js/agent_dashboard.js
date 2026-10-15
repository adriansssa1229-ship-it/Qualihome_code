/* ═══════════════════════════════════════════════════════════════
   Agent Dashboard — JavaScript
   ═══════════════════════════════════════════════════════════════ */

/* ── Toast helper ───────────────────────────────────────────── */
function showToast(message, type) {
  var container = document.getElementById('sqhToastContainer');
  if (!container) return;
  type = type || 'success';
  var icons  = { success: 'fa-check-circle', danger: 'fa-exclamation-circle', info: 'fa-info-circle', warning: 'fa-exclamation-triangle' };
  var colors = { success: 'var(--clr-accent-dk,#2e7d32)', danger: 'var(--clr-primary,#8b1a1a)', info: 'var(--clr-blue,#1a26a0)', warning: '#b36200' };
  var toast  = document.createElement('div');
  toast.className = 'sqh-toast sqh-toast-' + type;
  toast.innerHTML = '<i class="fas ' + (icons[type] || icons.success) + ' me-2" style="color:' + (colors[type] || colors.success) + ';flex-shrink:0;"></i><span>' + message + '</span><button class="sqh-toast-close" aria-label="Close">&times;</button>';
  toast.querySelector('.sqh-toast-close').addEventListener('click', function () { dismissToast(toast); });
  container.appendChild(toast);
  void toast.offsetWidth;
  toast.classList.add('sqh-toast-show');
  var timer = setTimeout(function () { dismissToast(toast); }, 4500);
  toast._sqhTimer = timer;
}
function dismissToast(toast) {
  clearTimeout(toast._sqhTimer);
  toast.classList.remove('sqh-toast-show');
  toast.classList.add('sqh-toast-hide');
  setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 400);
}

/* ── CSRF helper ────────────────────────────────────────────── */
function csrfToken() {
  var m = document.querySelector('meta[name="csrf-token"]');
  return m ? m.content : '';
}

function _agResetSelect(sel, placeholder) {
  if (!sel) return;
  sel.innerHTML = '';
  var opt = document.createElement('option');
  opt.value = '';
  opt.textContent = placeholder;
  sel.appendChild(opt);
}

function _agFillSelect(sel, items, placeholder, selectedCode) {
  if (!sel) return;
  _agResetSelect(sel, placeholder);
  (items || []).forEach(function (it) {
    var opt = document.createElement('option');
    opt.value = it.code || '';
    opt.textContent = it.name || '';
    sel.appendChild(opt);
  });
  if (selectedCode) sel.value = selectedCode;
}

function _agGetJSON(url) {
  return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      if (!res.ok || !res.data || !res.data.ok) {
        var apiBase = 'https://psgc.gitlab.io/api';
        var mReg = url.match(/\/api\/psgc\/provinces\?region_code=([^&]+)/);
        var mCity = url.match(/\/api\/psgc\/cities\?(?:province_code=([^&]+)|region_code=([^&]+))/);
        var mBrgy = url.match(/\/api\/psgc\/barangays\?city_mun_code=([^&]+)/);
        var directUrl = '';
        if (url.indexOf('/api/psgc/regions') === 0) directUrl = apiBase + '/regions/';
        else if (mReg) {
          var rCode = decodeURIComponent(mReg[1]);
          if (rCode === '130000000' || rCode.indexOf('13') === 0) {
            return Promise.resolve([{ code: '130000000', name: 'Metro Manila' }]);
          }
          directUrl = apiBase + '/regions/' + rCode + '/provinces/';
        }
        else if (mCity && mCity[1]) {
          var pCode = decodeURIComponent(mCity[1]);
          if (pCode === '130000000' || pCode.indexOf('13') === 0) {
            directUrl = apiBase + '/regions/' + pCode + '/cities-municipalities/';
          } else {
            directUrl = apiBase + '/provinces/' + pCode + '/cities-municipalities/';
          }
        }
        else if (mCity && mCity[2]) directUrl = apiBase + '/regions/' + decodeURIComponent(mCity[2]) + '/cities-municipalities/';
        else if (mBrgy) directUrl = apiBase + '/cities-municipalities/' + decodeURIComponent(mBrgy[1]) + '/barangays/';
        if (!directUrl) throw new Error((res.data && res.data.error) || 'Failed to load PSGC data');
        return fetch(directUrl).then(function (r2) {
          if (!r2.ok) throw new Error('Failed to load PSGC data');
          return r2.json();
        }).then(function (items2) {
          return Array.isArray(items2) ? items2.map(function (it) {
            return { code: String(it.code || ''), name: String(it.name || '') };
          }).filter(function (it) { return it.code && it.name; }) : [];
        });
      }
      return Array.isArray(res.data.items) ? res.data.items : [];
    });
}

function _agSyncSubmitLocation() {
  var regionSel = document.getElementById('sp_region_select');
  var provinceSel = document.getElementById('sp_province_select');
  var citySel = document.getElementById('sp_citymun_select');
  var brgySel = document.getElementById('sp_barangay_select');
  var lineEl = document.getElementById('sp_site_notes');
  var locEl = document.getElementById('sp_location');
  var regionEl = document.getElementById('sp_region');

  function txt(sel) {
    if (!sel || !sel.value || !sel.selectedOptions || !sel.selectedOptions.length) return '';
    return (sel.selectedOptions[0].textContent || '').trim();
  }

  var regionName = txt(regionSel);
  var provinceName = txt(provinceSel);
  var cityName = txt(citySel);
  var brgyName = txt(brgySel);
  var line = (lineEl && lineEl.value || '').trim();
  var tail = [brgyName, cityName, provinceName, regionName].filter(Boolean).join(', ');

  if (locEl) locEl.value = [line, tail].filter(Boolean).join(', ');
  if (regionEl) regionEl.value = regionName;
  var setVal = function (id, val) { var el = document.getElementById(id); if (el) el.value = val || ''; };
  setVal('sp_region_code', regionSel ? regionSel.value : '');
  setVal('sp_region_name', regionName);
  setVal('sp_province_code', provinceSel ? provinceSel.value : '');
  setVal('sp_province_name', provinceName);
  setVal('sp_citymun_code', citySel ? citySel.value : '');
  setVal('sp_citymun_name', cityName);
  setVal('sp_barangay_code', brgySel ? brgySel.value : '');
  setVal('sp_barangay_name', brgyName);
}

function initAgentSubmitPsgc() {
  var regionSel = document.getElementById('sp_region_select');
  var provinceSel = document.getElementById('sp_province_select');
  var citySel = document.getElementById('sp_citymun_select');
  var brgySel = document.getElementById('sp_barangay_select');
  if (!regionSel || !provinceSel || !citySel || !brgySel) return;

  function loadBarangays() {
    if (!citySel.value) {
      _agResetSelect(brgySel, '-- Select --');
      _agSyncSubmitLocation();
      return Promise.resolve();
    }
    return _agGetJSON('/api/psgc/barangays?city_mun_code=' + encodeURIComponent(citySel.value)).then(function (items) {
      _agFillSelect(brgySel, items, '-- Select --', '');
      _agSyncSubmitLocation();
    });
  }

  function loadCities() {
    if (!regionSel.value && !provinceSel.value) {
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncSubmitLocation();
      return Promise.resolve();
    }
    var q = provinceSel.value
      ? ('province_code=' + encodeURIComponent(provinceSel.value))
      : ('region_code=' + encodeURIComponent(regionSel.value));
    return _agGetJSON('/api/psgc/cities?' + q).then(function (items) {
      _agFillSelect(citySel, items, '-- Select --', '');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncSubmitLocation();
    });
  }

  function loadProvinces() {
    if (!regionSel.value) {
      _agResetSelect(provinceSel, '-- Select --');
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncSubmitLocation();
      return Promise.resolve();
    }
    return _agGetJSON('/api/psgc/provinces?region_code=' + encodeURIComponent(regionSel.value)).then(function (items) {
      _agFillSelect(provinceSel, items, '-- Select --', '');
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      if (items && items.length === 1) {
        provinceSel.value = items[0].code;
        return loadCities();
      }
      _agSyncSubmitLocation();
    });
  }

  regionSel.addEventListener('change', function () {
    loadProvinces().catch(function () { showToast('Unable to load provinces right now.', 'warning'); });
  });
  provinceSel.addEventListener('change', function () {
    loadCities().catch(function () { showToast('Unable to load cities right now.', 'warning'); });
  });
  citySel.addEventListener('change', function () {
    loadBarangays().catch(function () { showToast('Unable to load barangays right now.', 'warning'); });
  });
  brgySel.addEventListener('change', _agSyncSubmitLocation);
  var lineEl = document.getElementById('sp_site_notes');
  if (lineEl) lineEl.addEventListener('input', _agSyncSubmitLocation);

  _agGetJSON('/api/psgc/regions')
    .then(function (items) { _agFillSelect(regionSel, items, '-- Select --', ''); _agSyncSubmitLocation(); })
    .catch(function () { showToast('Unable to load PSGC regions.', 'warning'); });
}

initAgentSubmitPsgc();

function _agSyncEditLocation() {
  var regionSel = document.getElementById('ep_region_select');
  var provinceSel = document.getElementById('ep_province_select');
  var citySel = document.getElementById('ep_citymun_select');
  var brgySel = document.getElementById('ep_barangay_select');
  var lineEl = document.getElementById('ep_site_notes');
  var locEl = document.getElementById('ep_location');
  var regionEl = document.getElementById('ep_region');

  function txt(sel) {
    if (!sel || !sel.value || !sel.selectedOptions || !sel.selectedOptions.length) return '';
    return (sel.selectedOptions[0].textContent || '').trim();
  }

  var regionName = txt(regionSel);
  var provinceName = txt(provinceSel);
  var cityName = txt(citySel);
  var brgyName = txt(brgySel);
  var line = (lineEl && lineEl.value || '').trim();
  var tail = [brgyName, cityName, provinceName, regionName].filter(Boolean).join(', ');

  if (locEl) locEl.value = [line, tail].filter(Boolean).join(', ');
  if (regionEl) regionEl.value = regionName;
  var setVal = function (id, val) { var el = document.getElementById(id); if (el) el.value = val || ''; };
  setVal('ep_region_code', regionSel ? regionSel.value : '');
  setVal('ep_region_name', regionName);
  setVal('ep_province_code', provinceSel ? provinceSel.value : '');
  setVal('ep_province_name', provinceName);
  setVal('ep_citymun_code', citySel ? citySel.value : '');
  setVal('ep_citymun_name', cityName);
  setVal('ep_barangay_code', brgySel ? brgySel.value : '');
  setVal('ep_barangay_name', brgyName);
}

function initAgentEditPsgc() {
  var regionSel = document.getElementById('ep_region_select');
  var provinceSel = document.getElementById('ep_province_select');
  var citySel = document.getElementById('ep_citymun_select');
  var brgySel = document.getElementById('ep_barangay_select');
  if (!regionSel || !provinceSel || !citySel || !brgySel) return;

  function loadProvinces() {
    if (!regionSel.value) {
      _agResetSelect(provinceSel, '-- Select --');
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncEditLocation();
      return Promise.resolve();
    }
    return _agGetJSON('/api/psgc/provinces?region_code=' + encodeURIComponent(regionSel.value)).then(function (items) {
      _agFillSelect(provinceSel, items, '-- Select --', '');
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      if (items && items.length === 1) {
        provinceSel.value = items[0].code;
        return loadCities();
      }
      _agSyncEditLocation();
    });
  }

  function loadCities() {
    if (!regionSel.value && !provinceSel.value) {
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncEditLocation();
      return Promise.resolve();
    }
    var q = provinceSel.value
      ? ('province_code=' + encodeURIComponent(provinceSel.value))
      : ('region_code=' + encodeURIComponent(regionSel.value));
    return _agGetJSON('/api/psgc/cities?' + q).then(function (items) {
      _agFillSelect(citySel, items, '-- Select --', '');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncEditLocation();
    });
  }

  function loadBarangays() {
    if (!citySel.value) {
      _agResetSelect(brgySel, '-- Select --');
      _agSyncEditLocation();
      return Promise.resolve();
    }
    return _agGetJSON('/api/psgc/barangays?city_mun_code=' + encodeURIComponent(citySel.value)).then(function (items) {
      _agFillSelect(brgySel, items, '-- Select --', '');
      _agSyncEditLocation();
    });
  }

  regionSel.addEventListener('change', function () {
    loadProvinces().catch(function () { showToast('Unable to load provinces right now.', 'warning'); });
  });
  provinceSel.addEventListener('change', function () {
    loadCities().catch(function () { showToast('Unable to load cities right now.', 'warning'); });
  });
  citySel.addEventListener('change', function () {
    loadBarangays().catch(function () { showToast('Unable to load barangays right now.', 'warning'); });
  });
  brgySel.addEventListener('change', _agSyncEditLocation);
  var lineEl = document.getElementById('ep_site_notes');
  if (lineEl) lineEl.addEventListener('input', _agSyncEditLocation);

  _agGetJSON('/api/psgc/regions')
    .then(function (items) { _agFillSelect(regionSel, items, '-- Select --', ''); _agSyncEditLocation(); })
    .catch(function () {});
}

initAgentEditPsgc();

function _agPrefillEditPsgc(codes) {
  var regionSel = document.getElementById('ep_region_select');
  var provinceSel = document.getElementById('ep_province_select');
  var citySel = document.getElementById('ep_citymun_select');
  var brgySel = document.getElementById('ep_barangay_select');
  if (!regionSel || !provinceSel || !citySel || !brgySel) return;

  var regionCode = (codes && codes.regionCode) || '';
  var provinceCode = (codes && codes.provinceCode) || '';
  var citymunCode = (codes && codes.citymunCode) || '';
  var barangayCode = (codes && codes.barangayCode) || '';

  _agGetJSON('/api/psgc/regions').then(function (regions) {
    _agFillSelect(regionSel, regions, '-- Select --', regionCode);
    if (!regionCode) {
      _agResetSelect(provinceSel, '-- Select --');
      _agResetSelect(citySel, '-- Select --');
      _agResetSelect(brgySel, '-- Select --');
      _agSyncEditLocation();
      return Promise.resolve();
    }
    return _agGetJSON('/api/psgc/provinces?region_code=' + encodeURIComponent(regionCode)).then(function (provinces) {
      var effProvinceCode = provinceCode;
      if (!effProvinceCode && provinces && provinces.length === 1) {
        effProvinceCode = provinces[0].code;
      }
      _agFillSelect(provinceSel, provinces, '-- Select --', effProvinceCode);
      if (!effProvinceCode && !citymunCode) {
        _agResetSelect(citySel, '-- Select --');
        _agResetSelect(brgySel, '-- Select --');
        _agSyncEditLocation();
        return Promise.resolve();
      }
      var cityQ = effProvinceCode
        ? ('province_code=' + encodeURIComponent(effProvinceCode))
        : ('region_code=' + encodeURIComponent(regionCode));
      return _agGetJSON('/api/psgc/cities?' + cityQ).then(function (cities) {
        _agFillSelect(citySel, cities, '-- Select --', citymunCode);
        if (!citymunCode) {
          _agResetSelect(brgySel, '-- Select --');
          _agSyncEditLocation();
          return Promise.resolve();
        }
        return _agGetJSON('/api/psgc/barangays?city_mun_code=' + encodeURIComponent(citymunCode)).then(function (barangays) {
          _agFillSelect(brgySel, barangays, '-- Select --', barangayCode);
          _agSyncEditLocation();
        });
      });
    });
  }).catch(function () {
    _agSyncEditLocation();
  });
}

/* ── Confirm-action modal (reusable) ────────────────────────── */
var _confirmPending = { action: null, tripId: null, tripStatus: null, propId: null };

/* ── Submit Property ────────────────────────────────────────── */
document.getElementById('submitPropBtn') && document.getElementById('submitPropBtn').addEventListener('click', function () {
  var btn   = this;
  var errEl = document.getElementById('submitPropError');
  errEl.classList.add('d-none');

  var name     = document.getElementById('sp_name').value.trim();
  _agSyncSubmitLocation();
  var location = document.getElementById('sp_location').value.trim();
  var propType = document.getElementById('sp_prop_type').value;
  var price    = document.getElementById('sp_price').value.trim();

  if (!name || !location || !propType || !price) {
    errEl.textContent = 'Name, location, model type, and price are required.';
    errEl.classList.remove('d-none');
    return;
  }

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Submitting\u2026';

  var fd = new FormData();
  fd.append('name',           name);
  fd.append('location',       location);
  fd.append('region',         document.getElementById('sp_region').value.trim());
  fd.append('region_code',    document.getElementById('sp_region_code').value.trim());
  fd.append('region_name',    document.getElementById('sp_region_name').value.trim());
  fd.append('province_code',  document.getElementById('sp_province_code').value.trim());
  fd.append('province_name',  document.getElementById('sp_province_name').value.trim());
  fd.append('citymun_code',   document.getElementById('sp_citymun_code').value.trim());
  fd.append('citymun_name',   document.getElementById('sp_citymun_name').value.trim());
  fd.append('barangay_code',  document.getElementById('sp_barangay_code').value.trim());
  fd.append('barangay_name',  document.getElementById('sp_barangay_name').value.trim());
  fd.append('prop_type',      propType);
  fd.append('price',          price.replace(/,/g, ''));
  var spUnitEl = document.getElementById('sp_unit_id');
  fd.append('unit_id',        spUnitEl ? spUnitEl.value.trim() : '');
  fd.append('bedrooms',       document.getElementById('sp_bedrooms').value || '0');
  fd.append('bathrooms',      document.getElementById('sp_bathrooms').value || '0');
  fd.append('storeys',        document.getElementById('sp_storeys').value || '1');
  fd.append('floor_area',     document.getElementById('sp_floor_area').value.trim().replace(/,/g, ''));
  fd.append('lot_area',       document.getElementById('sp_lot_area').value.trim().replace(/,/g, ''));
  fd.append('subdivision_id', document.getElementById('sp_subdivision').value);
  var spAgentEl = document.getElementById('sp_agent_id');
  if (spAgentEl) fd.append('agent_id', spAgentEl.value || '');
  fd.append('description',    document.getElementById('sp_description').value.trim());
  _pendingSpFiles.filter(Boolean).forEach(function(f) { fd.append('images', f); });
  fd.append('csrf_token', csrfToken());

  fetch('/agent/property/submit', { method: 'POST', body: fd })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i> Submit for Review';
      if (!res.ok) {
        errEl.textContent = res.data.error || 'An error occurred.';
        errEl.classList.remove('d-none');
        return;
      }
      bootstrap.Modal.getInstance(document.getElementById('submitPropertyModal')).hide();
      _resetSubmitForm();
      showToast('Property submitted for admin review! It will appear after admin approval.', 'success');
      // Real-time: no full page reload required per FR-2; listings refresh on next navigation/fetch
    })
    .catch(function () {
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-paper-plane me-1"></i> Submit for Review';
      errEl.textContent = 'Network error. Please try again.';
      errEl.classList.remove('d-none');
    });
});

function _resetSubmitForm() {
  ['sp_name','sp_site_notes','sp_location','sp_region','sp_price','sp_unit_id','sp_floor_area','sp_lot_area','sp_description',
   'sp_region_code','sp_region_name','sp_province_code','sp_province_name','sp_citymun_code','sp_citymun_name','sp_barangay_code','sp_barangay_name'].forEach(function (id) {
    var el = document.getElementById(id); if (el) el.value = '';
  });
  ['sp_prop_type','sp_bedrooms','sp_bathrooms','sp_storeys','sp_subdivision','sp_region_select','sp_province_select','sp_citymun_select','sp_barangay_select'].forEach(function (id) {
    var el = document.getElementById(id); if (el) el.selectedIndex = 0;
  });
  _agSyncSubmitLocation();
  var imgEl = document.getElementById('sp_images'); if (imgEl) imgEl.value = '';
  _pendingSpFiles = [];
  var spWrap = document.getElementById('sp_images_wrap'); if (spWrap) spWrap.innerHTML = '';
  var fnEl = document.getElementById('sp_images_filenames'); if (fnEl) fnEl.value = '';
}

document.getElementById('submitPropertyModal') && document.getElementById('submitPropertyModal').addEventListener('hidden.bs.modal', function () {
  _resetSubmitForm();
  var errEl = document.getElementById('submitPropError');
  if (errEl) {
    errEl.textContent = '';
    errEl.classList.add('d-none');
  }
});


/* ── Submit Property ───────────────────────────────────────── */
var _pendingSpFiles  = [];

/* ── Edit Property ──────────────────────────────────────────── */
var _editPropId      = null;
var _editPropIsSold  = false;
var _lemImages       = [];
var _lemIdx          = 0;
var _pendingNewFiles = [];
var _returnToEditAfterClientModal = false;
var _editModalScrollTop = 0;
var _editScrollSyncTimer = null;

function _getEditModalScrollEl() {
  var content = document.querySelector('#editPropertyModal .modal-content');
  if (content && content.scrollHeight > content.clientHeight) return content;
  return document.querySelector('#editPropertyModal .modal-body');
}

function _lemShowSlide(idx) {
  if (!_lemImages.length) return;
  _lemIdx = (idx + _lemImages.length) % _lemImages.length;
  var imgEl = document.getElementById('lemImg');
  if (!imgEl) return;
  // Swap only after the target decodes (no fade-to-blank); neighbors preload.
  SqhImageCache.swap(imgEl, _lemImages[_lemIdx], function () { imgEl.style.opacity = '1'; });
  SqhImageCache.preloadNeighbors(_lemImages, _lemIdx);
  document.querySelectorAll('#lemDots .sub-preview-dot').forEach(function (d, i) {
    d.classList.toggle('active', i === _lemIdx);
  });
}

function renderAgentPricingBreakdown(pricingJson) {
  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val;
  }
  function fmt(v) {
    var n = Number(v || 0);
    if (!isFinite(n)) n = 0;
    return '\u20b1' + n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function pct(v) {
    var n = Number(v || 0);
    if (!isFinite(n)) n = 0;
    return n.toFixed(2) + '%';
  }
  var ids = [
    'lemPvTotalSelling', 'lemPvPromoDiscount', 'lemPvNetSelling', 'lemPvVatAmount', 'lemPvVatRate',
    'lemPvLmfAmount', 'lemPvLmfRate', 'lemPvTotalContract', 'lemPvReservationFee', 'lemPvTotalDownpayment',
    'lemPvDownpaymentRate', 'lemPvMonthlyDownpayment', 'lemPvDownpaymentTerms', 'lemPvLoanableAmount',
    'lemPvLoanableRate', 'lemPvAnnualInterest', 'lemPvAmort5', 'lemPvAmort10', 'lemPvAmort15', 'lemPvAmort20',
    'lemPvReqIncome5', 'lemPvReqIncome10', 'lemPvReqIncome15', 'lemPvReqIncome20'
  ];
  ids.forEach(function (id) { setText(id, '\u2014'); });

  var pricingData = null;
  if (pricingJson) {
    try { pricingData = (typeof pricingJson === 'string') ? JSON.parse(pricingJson) : pricingJson; }
    catch (_) { pricingData = null; }
  }
  if (!pricingData || typeof pricingData !== 'object') return;

  setText('lemPvTotalSelling', fmt(pricingData.total_selling_price || pricingData.tcp || 0));
  setText('lemPvPromoDiscount', pct(pricingData.promo_discount_rate || 0));
  setText('lemPvNetSelling', 'Net Selling: ' + fmt(pricingData.net_selling_price || 0));
  setText('lemPvVatAmount', fmt(pricingData.vat_amount || 0));
  setText('lemPvVatRate', pct(pricingData.vat_rate || 0));
  setText('lemPvLmfAmount', fmt(pricingData.lmf_amount || 0));
  setText('lemPvLmfRate', pct(pricingData.lmf_rate || 0));
  setText('lemPvTotalContract', fmt(pricingData.total_contract_price || pricingData.fully_computed_house_price || 0));
  setText('lemPvReservationFee', fmt(pricingData.reservation_fee || 0));
  setText('lemPvTotalDownpayment', fmt(pricingData.total_downpayment || pricingData.down_payment || 0));
  setText('lemPvDownpaymentRate', pct(pricingData.downpayment_rate || 0));
  setText('lemPvMonthlyDownpayment', fmt(pricingData.monthly_downpayment || pricingData.equity_monthly || 0));
  setText('lemPvDownpaymentTerms', ((pricingData.downpayment_terms_months || pricingData.equity_months || 0)) + ' months');
  setText('lemPvLoanableAmount', fmt(pricingData.total_loanable_amount || pricingData.financed_amount || 0));
  setText('lemPvLoanableRate', pct(pricingData.loanable_percentage || 0));
  setText('lemPvAnnualInterest', pct(pricingData.annual_interest_rate || 0));

  var amort = pricingData.amortization || {};
  var req = pricingData.required_monthly_income || {};
  setText('lemPvAmort5', fmt(amort['5'] || 0));
  setText('lemPvAmort10', fmt(amort['10'] || 0));
  setText('lemPvAmort15', fmt(amort['15'] || 0));
  setText('lemPvAmort20', fmt(amort['20'] || 0));
  setText('lemPvReqIncome5', fmt(req['5'] || 0));
  setText('lemPvReqIncome10', fmt(req['10'] || 0));
  setText('lemPvReqIncome15', fmt(req['15'] || 0));
  setText('lemPvReqIncome20', fmt(req['20'] || 0));
}

function openEditPropertyModal(propId) {
  _editPropId = propId;
  _editPropIsSold = false;
  var isReadOnly = true;
  var errEl = document.getElementById('editPropError');
  if (errEl) {
    errEl.textContent = '';
    errEl.classList.add('d-none');
  }

  var card = document.querySelector('.listing-card[data-prop-id="' + propId + '"]');
  if (!card) return;
  function _cleanNumericText(v, fallback) {
    if (v === undefined || v === null || v === '') return fallback;
    return String(v).replace(/,/g, '');
  }
  function formatCurrencyField(val) {
    if (val === undefined || val === null || val === '') return '';
    var s = String(val).trim();
    if (s === '0' || s === '0.00') return '';
    if (/\.00$/.test(s)) s = s.slice(0, -3);
    return (typeof sqhFormatNumericValue === 'function') ? sqhFormatNumericValue(s) : s;
  }
  var listingStatus = (card.dataset.listingStatus || '').toLowerCase();
  _editPropIsSold = listingStatus === 'sold';
  renderAgentPricingBreakdown(card.dataset.pricingJson);

  document.getElementById('ep_name').value        = card.dataset.name        || '';
  var streetEl = document.getElementById('ep_street');
  if (streetEl) streetEl.value = card.dataset.street || '';
  var blockEl = document.getElementById('ep_block');
  if (blockEl) blockEl.value = card.dataset.block || '';
  var lotNoEl = document.getElementById('ep_lot_no');
  if (lotNoEl) lotNoEl.value = card.dataset.lotNo || '';
  var _fullLoc = (card.dataset.location || '').trim();
  var _tailParts = [card.dataset.barangayName, card.dataset.citymunName, card.dataset.provinceName, card.dataset.regionName]
    .filter(function (x) { return (x || '').trim(); })
    .map(function (x) { return (x || '').trim(); });
  var _tail = _tailParts.join(', ');
  var _lineOnly = _fullLoc;
  if (_tail) {
    var _fullLc = _fullLoc.toLowerCase();
    var _tailLc = _tail.toLowerCase();
    if (_fullLc === _tailLc) {
      _lineOnly = '';
    } else {
      var _suff = ', ' + _tail;
      if (_fullLc.endsWith(_suff.toLowerCase())) {
        _lineOnly = _fullLoc.slice(0, _fullLoc.length - _suff.length).trim();
      }
    }
  }
  document.getElementById('ep_site_notes').value = _lineOnly;
  document.getElementById('ep_location').value    = _fullLoc;
  document.getElementById('ep_region').value      = card.dataset.region      || '';
  document.getElementById('ep_region_code').value = card.dataset.regionCode || '';
  document.getElementById('ep_region_name').value = card.dataset.regionName || '';
  document.getElementById('ep_province_code').value = card.dataset.provinceCode || '';
  document.getElementById('ep_province_name').value = card.dataset.provinceName || '';
  document.getElementById('ep_citymun_code').value = card.dataset.citymunCode || '';
  document.getElementById('ep_citymun_name').value = card.dataset.citymunName || '';
  document.getElementById('ep_barangay_code').value = card.dataset.barangayCode || '';
  document.getElementById('ep_barangay_name').value = card.dataset.barangayName || '';
  ['ep_region_select','ep_province_select','ep_citymun_select','ep_barangay_select'].forEach(function(id){
    var el = document.getElementById(id); if (el) el.selectedIndex = 0;
  });
  _agPrefillEditPsgc({
    regionCode: card.dataset.regionCode || '',
    provinceCode: card.dataset.provinceCode || '',
    citymunCode: card.dataset.citymunCode || '',
    barangayCode: card.dataset.barangayCode || ''
  });
  document.getElementById('ep_price').value       = formatCurrencyField(card.dataset.price);
  document.getElementById('ep_bedrooms').value    = card.dataset.bedrooms    || '0';
  document.getElementById('ep_bathrooms').value   = card.dataset.bathrooms   || '0';
  document.getElementById('ep_storeys').value     = card.dataset.storeys     || '1';
  document.getElementById('ep_floor_area').value  = formatCurrencyField(card.dataset.floorArea);
  document.getElementById('ep_lot_area').value    = formatCurrencyField(card.dataset.lotArea);
  document.getElementById('ep_description').value = card.dataset.description || '';
  document.getElementById('ep_images').value      = '';
  _pendingNewFiles = [];
  var epFnEl = document.getElementById('ep_images_filenames'); if (epFnEl) epFnEl.value = '';

  var subSel = document.getElementById('ep_subdivision');
  if (subSel) subSel.value = card.dataset.subdivisionId || '';

  var propTypeSel = document.getElementById('ep_prop_type');
  if (propTypeSel) propTypeSel.value = card.dataset.propType || '';
  var unitTypeSel = document.getElementById('ep_unit_type');
  if (unitTypeSel) unitTypeSel.value = card.dataset.propUnitType || '';
  var unitIdEl = document.getElementById('ep_unit_id');
  if (unitIdEl) unitIdEl.value = card.dataset.unitId || '';
  var promoDiscountEl = document.getElementById('ep_promo_discount_rate');
  if (promoDiscountEl) promoDiscountEl.value = _cleanNumericText(card.dataset.promoDiscountRate, '0');
  var reservationFeeEl = document.getElementById('ep_reservation_fee');
  if (reservationFeeEl) reservationFeeEl.value = formatCurrencyField(card.dataset.reservationFee);
  var downpaymentRateEl = document.getElementById('ep_downpayment_rate');
  if (downpaymentRateEl) downpaymentRateEl.value = _cleanNumericText(card.dataset.downpaymentRate, '0');
  var downpaymentTermsEl = document.getElementById('ep_downpayment_terms_months');
  if (downpaymentTermsEl) downpaymentTermsEl.value = _cleanNumericText(card.dataset.downpaymentTermsMonths, '0');
  var loanablePctEl = document.getElementById('ep_loanable_percentage');
  if (loanablePctEl) loanablePctEl.value = _cleanNumericText(card.dataset.loanablePercentage, '0');
  var vatRateEl = document.getElementById('ep_vat_rate');
  if (vatRateEl) vatRateEl.value = _cleanNumericText(card.dataset.vatRate, '0');
  var lmfRateEl = document.getElementById('ep_lmf_rate');
  if (lmfRateEl) lmfRateEl.value = _cleanNumericText(card.dataset.lmfRate, '0');
  var epAgentSel = document.getElementById('ep_agent_id');
  if (epAgentSel) epAgentSel.value = card.dataset.agentId || '';

  var imgWrap = document.getElementById('ep_images_wrap');
  imgWrap.innerHTML = '';
  var images = (card.dataset.images || '').split(',').filter(Boolean);
  images.forEach(function (fname) {
    var tile = document.createElement('div');
    tile.className = 'sub-img-tile';
    tile.innerHTML =
      '<img src="' + sqhImgSrc(fname) + '" class="sub-img-tile-img" alt="">' +
      '<button type="button" class="sub-img-tile-del" data-fname="' + fname + '" title="Remove"><i class="fas fa-times"></i></button>' +
      '<input type="hidden" name="existing_img" value="' + fname + '">';
    imgWrap.appendChild(tile);
  });

  /* ── Carousel ── */
  _lemImages = (card.dataset.images || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  _lemIdx    = 0;
  var lemImgWrap    = document.getElementById('lemImgWrap');
  var lemImgEl      = document.getElementById('lemImg');
  var lemPlaceholder = document.getElementById('lemImgPlaceholder');
  var lemPrevBtn    = document.getElementById('lemPrev');
  var lemNextBtn    = document.getElementById('lemNext');
  var lemDotsEl     = document.getElementById('lemDots');
  if (_lemImages.length) {
    if (lemImgWrap)    { lemImgWrap.style.display = 'block'; }
    if (lemPlaceholder){ lemPlaceholder.style.display = 'none'; }
    if (lemImgEl)      { SqhImageCache.swap(lemImgEl, _lemImages[0], function () { lemImgEl.style.opacity = '1'; }); SqhImageCache.preloadAll(_lemImages); }
    if (_lemImages.length > 1) {
      if (lemPrevBtn) lemPrevBtn.classList.remove('d-none');
      if (lemNextBtn) lemNextBtn.classList.remove('d-none');
      if (lemDotsEl) {
        lemDotsEl.innerHTML = _lemImages.map(function (_, i) {
          return '<span class="sub-preview-dot' + (i === 0 ? ' active' : '') + '" data-idx="' + i + '"></span>';
        }).join('');
        lemDotsEl.querySelectorAll('.sub-preview-dot').forEach(function (dot) {
          dot.addEventListener('click', function () { _lemShowSlide(parseInt(this.dataset.idx)); });
        });
      }
    } else {
      if (lemPrevBtn) lemPrevBtn.classList.add('d-none');
      if (lemNextBtn) lemNextBtn.classList.add('d-none');
      if (lemDotsEl)  lemDotsEl.innerHTML = '';
    }
  } else {
    if (lemImgWrap)    { lemImgWrap.style.display = 'none'; }
    if (lemPlaceholder){ lemPlaceholder.style.display = 'flex'; }
    if (lemPrevBtn)    lemPrevBtn.classList.add('d-none');
    if (lemNextBtn)    lemNextBtn.classList.add('d-none');
    if (lemDotsEl)     lemDotsEl.innerHTML = '';
  }

  /* ── Status badge ── */
  var statusBadgeEl = document.getElementById('lemStatusBadge');
  if (statusBadgeEl) {
    var listingStatus = (card.dataset.listingStatus || '').toLowerCase();
    var listingBadge = listingStatus === 'sold'
      ? '<span class="sqh-badge badge-sold">Sold</span>'
      : '<span class="sqh-badge badge-qualified">Available</span>';
    statusBadgeEl.innerHTML = listingBadge;
  }

  var soldBuyerWrap = document.getElementById('lemSoldBuyerInfo');
  var soldBuyerNameEl = document.getElementById('lemBuyerName');
  var soldBuyerSoldAtEl = document.getElementById('lemBuyerSoldAt');
  var soldBuyerBtn = document.getElementById('lemViewBuyerBtn');
  var soldBuyerId = (card.dataset.buyerId || '').trim();
  var soldBuyerName = (card.dataset.buyerName || '').trim() || '\u2014';
  var soldAtLabel = (card.dataset.soldAt || '').trim();
  if (_editPropIsSold && !soldBuyerId) {
    var soldLookupCard = document.querySelector('#soldCardsGrid .listing-card[data-prop-id="' + String(propId || '') + '"]');
    if (soldLookupCard) {
      soldBuyerId = (soldLookupCard.dataset.buyerId || '').trim();
      if (!soldBuyerName || soldBuyerName === '\u2014') {
        soldBuyerName = (soldLookupCard.dataset.buyerName || '').trim() || '\u2014';
      }
      if (!soldAtLabel) {
        soldAtLabel = (soldLookupCard.dataset.soldAt || '').trim();
      }
    }
  }

  if (soldBuyerWrap) {
    soldBuyerWrap.classList.toggle('d-none', !_editPropIsSold);
  }
  if (soldBuyerNameEl) soldBuyerNameEl.textContent = soldBuyerName;
  if (soldBuyerSoldAtEl) soldBuyerSoldAtEl.textContent = soldAtLabel ? ('Bought on ' + soldAtLabel) : '';
  if (soldBuyerBtn) {
    soldBuyerBtn.disabled = !(_editPropIsSold && soldBuyerId);
    soldBuyerBtn.onclick = null;
    if (_editPropIsSold && soldBuyerId) {
      soldBuyerBtn.onclick = function () {
        var editModal = document.getElementById('editPropertyModal');
        _returnToEditAfterClientModal = true;
        bootstrap.Modal.getInstance(editModal)?.hide();
        setTimeout(function () {
          openClientModal(soldBuyerId);
        }, 160);
      };
    } else if (_editPropIsSold) {
      soldBuyerBtn.onclick = function () {
        showToast('Buyer details are unavailable for this sold model.', 'warning');
      };
    }
  }

  var formIds = [
    'ep_name', 'ep_site_notes', 'ep_street', 'ep_block', 'ep_lot_no', 'ep_region_select', 'ep_province_select', 'ep_citymun_select', 'ep_barangay_select',
    'ep_location', 'ep_region', 'ep_prop_type', 'ep_price', 'ep_bedrooms', 'ep_bathrooms',
    'ep_unit_type', 'ep_unit_id', 'ep_promo_discount_rate', 'ep_reservation_fee', 'ep_downpayment_rate',
    'ep_downpayment_terms_months', 'ep_loanable_percentage', 'ep_vat_rate', 'ep_lmf_rate',
    'ep_storeys', 'ep_floor_area', 'ep_lot_area', 'ep_subdivision', 'ep_description', 'ep_images'
  ];
  formIds.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) {
      el.disabled = isReadOnly;
      el.classList.toggle('sqh-disabled-field', !!el.disabled);
    }
  });
  var saveBtn = document.getElementById('editPropBtn');
  if (saveBtn) saveBtn.classList.add('d-none');
  var pendingWarn = document.getElementById('editPropPendingWarn');
  if (pendingWarn) pendingWarn.classList.remove('d-none');
  var deleteButtons = document.querySelectorAll('#ep_images_wrap .sub-img-tile-del');
  deleteButtons.forEach(function(btn) { btn.classList.add('d-none'); });

  /* ── Icon chips ── */
  var chips = '';
  var beds    = card.dataset.bedrooms  || '0';
  var baths   = card.dataset.bathrooms || '0';
  var storeys = card.dataset.storeys   || '1';
  var floorA  = parseFloat(card.dataset.floorArea || 0);
  var lotA    = parseFloat(card.dataset.lotArea || 0);
  if (parseInt(beds)    > 0) chips += '<span class="pvm-icon-chip"><i class="fas fa-bed me-1"></i>' + beds + ' Bed' + (parseInt(beds) > 1 ? 's' : '') + '</span>';
  if (parseInt(baths)   > 0) chips += '<span class="pvm-icon-chip"><i class="fas fa-bath me-1"></i>' + baths + ' Bath' + (parseInt(baths) > 1 ? 's' : '') + '</span>';
  if (parseInt(storeys) > 1) chips += '<span class="pvm-icon-chip"><i class="fas fa-layer-group me-1"></i>' + storeys + ' Storey' + (parseInt(storeys) > 1 ? 's' : '') + '</span>';
  if (floorA > 0) chips += '<span class="pvm-icon-chip"><i class="fas fa-ruler-combined me-1"></i>' + floorA + ' sqm Floor Area</span>';
  if (lotA > 0) chips += '<span class="pvm-icon-chip"><i class="fas fa-vector-square me-1"></i>' + lotA + ' sqm Lot Area</span>';
  if (card.dataset.unitId) chips += '<span class="pvm-icon-chip"><i class="fas fa-hashtag me-1"></i>Unit ' + card.dataset.unitId + '</span>';
  var chipsEl = document.getElementById('lemIconChips');
  if (chipsEl) chipsEl.innerHTML = chips;

  /* ── Delete button propId ── */
  var delBtn = document.getElementById('lemDeleteBtn');
  if (delBtn) {
    delBtn.dataset.propId = propId;
    var listingStatus = String(card.dataset.listingStatus || '').toLowerCase();
    if (listingStatus === 'sold') {
      delBtn.classList.add('d-none');
      delBtn.disabled = true;
    } else {
      delBtn.classList.remove('d-none');
      delBtn.disabled = false;
    }
  }

  var editModalEl = document.getElementById('editPropertyModal');
  if (typeof sqhBindNumericFormatting === 'function' && editModalEl) {
    sqhBindNumericFormatting(editModalEl);
  }
  if (typeof sqhBindPercentageInputs === 'function' && editModalEl) {
    sqhBindPercentageInputs(editModalEl);
  }

  bootstrap.Modal.getOrCreateInstance(editModalEl).show();
}

document.getElementById('lemPrev') && document.getElementById('lemPrev').addEventListener('click', function (e) {
  e.stopPropagation(); _lemShowSlide(_lemIdx - 1);
});
document.getElementById('lemNext') && document.getElementById('lemNext').addEventListener('click', function (e) {
  e.stopPropagation(); _lemShowSlide(_lemIdx + 1);
});

document.getElementById('ep_images_wrap') && document.getElementById('ep_images_wrap').addEventListener('click', function (e) {
  var btn = e.target.closest('.sub-img-tile-del');
  if (!btn) return;
  var tile = btn.closest('.sub-img-tile');
  // If this is a newly-added preview, remove from _pendingNewFiles
  var idx = tile ? tile.dataset.newIdx : null;
  if (idx !== null && idx !== undefined) {
    _pendingNewFiles[parseInt(idx)] = null;
  }
  tile.remove();
  var fnEl = document.getElementById('ep_images_filenames');
  if (fnEl) {
    var names = _pendingNewFiles.filter(Boolean).map(function (f) { return f.name; });
    fnEl.value = names.join(', ');
  }
});

document.getElementById('ep_images') && document.getElementById('ep_images').addEventListener('change', function () {
  var files = this.files;
  if (!files || !files.length) return;
  var imgWrap = document.getElementById('ep_images_wrap');
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    var arrIdx = _pendingNewFiles.length;
    _pendingNewFiles.push(f);
    var url = URL.createObjectURL(f);
    var tile = document.createElement('div');
    tile.className = 'sub-img-tile';
    tile.dataset.newIdx = arrIdx;
    tile.innerHTML =
      '<img src="' + url + '" class="sub-img-tile-img" alt="">' +
      '<button type="button" class="sub-img-tile-del" title="Remove"><i class="fas fa-times"></i></button>';
    imgWrap.appendChild(tile);
  }
  var fnEl = document.getElementById('ep_images_filenames');
  if (fnEl) {
    var names = _pendingNewFiles.filter(Boolean).map(function (f) { return f.name; });
    fnEl.value = names.join(', ');
  }
});

/* ── Submit Property: image preview ────────────────────────── */
document.getElementById('sp_images_wrap') && document.getElementById('sp_images_wrap').addEventListener('click', function (e) {
  var btn = e.target.closest('.sub-img-tile-del');
  if (!btn) return;
  var tile = btn.closest('.sub-img-tile');
  var idx = tile ? tile.dataset.newIdx : null;
  if (idx !== null && idx !== undefined) {
    _pendingSpFiles[parseInt(idx)] = null;
  }
  tile.remove();
  var fnEl = document.getElementById('sp_images_filenames');
  if (fnEl) {
    var names = _pendingSpFiles.filter(Boolean).map(function (f) { return f.name; });
    fnEl.value = names.join(', ');
  }
});

document.getElementById('sp_images') && document.getElementById('sp_images').addEventListener('change', function () {
  var files = this.files;
  if (!files || !files.length) return;
  var imgWrap = document.getElementById('sp_images_wrap');
  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    var arrIdx = _pendingSpFiles.length;
    _pendingSpFiles.push(f);
    var url = URL.createObjectURL(f);
    var tile = document.createElement('div');
    tile.className = 'sub-img-tile';
    tile.dataset.newIdx = arrIdx;
    tile.innerHTML =
      '<img src="' + url + '" class="sub-img-tile-img" alt="">' +
      '<button type="button" class="sub-img-tile-del" title="Remove"><i class="fas fa-times"></i></button>';
    imgWrap.appendChild(tile);
  }
  var fnEl = document.getElementById('sp_images_filenames');
  if (fnEl) {
    var names = _pendingSpFiles.filter(Boolean).map(function (f) { return f.name; });
    fnEl.value = names.join(', ');
  }
});

function _validateEditPropertyRequired() {
  _agSyncEditLocation();
  var name     = document.getElementById('ep_name').value.trim();
  var location = document.getElementById('ep_location').value.trim();
  var propType = document.getElementById('ep_prop_type').value;
  var price    = document.getElementById('ep_price').value.trim();
  return { ok: !!(name && location && propType && price), name: name, location: location, propType: propType, price: price };
}

function _titleCasePropType(v) {
  if (!v) return '--';
  return String(v).split('-').map(function (x) { return x ? x.charAt(0).toUpperCase() + x.slice(1) : x; }).join(' ');
}

function _applyEditedPropertyCard(propId, model) {
  var card = document.querySelector('.listing-card[data-prop-id="' + propId + '"]');
  if (!card) return;
  var col = card.closest('.listing-card-col');
  var subSel = document.getElementById('ep_subdivision');
  var subText = '';
  if (subSel && subSel.value) {
    var opt = subSel.options[subSel.selectedIndex];
    subText = opt ? opt.text : '';
    if ((subText || '').indexOf('None') !== -1) subText = '';
  }

  card.dataset.name = model.name;
  card.dataset.location = model.location;
  card.dataset.region = model.region || '';
  card.dataset.regionCode = document.getElementById('ep_region_code').value.trim();
  card.dataset.regionName = document.getElementById('ep_region_name').value.trim();
  card.dataset.provinceCode = document.getElementById('ep_province_code').value.trim();
  card.dataset.provinceName = document.getElementById('ep_province_name').value.trim();
  card.dataset.citymunCode = document.getElementById('ep_citymun_code').value.trim();
  card.dataset.citymunName = document.getElementById('ep_citymun_name').value.trim();
  card.dataset.barangayCode = document.getElementById('ep_barangay_code').value.trim();
  card.dataset.barangayName = document.getElementById('ep_barangay_name').value.trim();
  card.dataset.propType = model.propType || '';
  card.dataset.price = model.price;
  card.dataset.bedrooms = model.bedrooms;
  card.dataset.bathrooms = model.bathrooms;
  card.dataset.storeys = model.storeys;
  card.dataset.floorArea = model.floorArea;
  card.dataset.lotArea = model.lotArea;
  card.dataset.subdivisionId = model.subdivisionId || '';
  card.dataset.description = model.description || '';
  card.dataset.images = (model.keptExisting || []).join(',');

  if (col) {
    col.dataset.listingStatus = 'available';
    col.dataset.propName = model.name;
    col.dataset.propLoc = model.location;
    col.dataset.propSubdiv = subText;
  }

  var nameEl = card.querySelector('.prop-card-name');
  if (nameEl) nameEl.textContent = model.name;

  var locEl = card.querySelector('.prop-card-loc');
  if (locEl) locEl.innerHTML = '<i class="fas fa-map-marker-alt me-1"></i>' + model.location;

  var typeEl = card.querySelector('.prop-card-type');
  if (typeEl) typeEl.textContent = _titleCasePropType(model.propType);

  var priceEl = card.querySelector('.prop-card-price');
  if (priceEl) priceEl.textContent = '₱' + Number(model.price || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });

  var statusBadge = card.querySelector('.prop-card-header .sqh-badge');
  if (statusBadge) {
    statusBadge.className = 'sqh-badge badge-qualified';
    statusBadge.textContent = 'Available';
  }

  var iconsEl = card.querySelector('.prop-card-icons');
  if (iconsEl) {
    var icons = '';
    var beds = parseInt(model.bedrooms || 0, 10);
    var baths = parseInt(model.bathrooms || 0, 10);
    var storeys = parseInt(model.storeys || 0, 10);
    if (beds > 0) icons += '<span class="prop-card-icon-chip"><i class="fas fa-bed" style="color:var(--clr-accent-dk);"></i> ' + beds + '</span>';
    if (baths > 0) icons += '<span class="prop-card-icon-chip"><i class="fas fa-bath" style="color:var(--clr-blue);"></i> ' + baths + '</span>';
    if (storeys > 0) icons += '<span class="prop-card-icon-chip"><i class="fas fa-layer-group" style="color:var(--clr-primary);"></i> ' + storeys + '</span>';
    iconsEl.innerHTML = icons;
  }

  var metaCityEl = card.querySelector('.prop-card-meta span:first-child');
  if (metaCityEl) metaCityEl.innerHTML = '<i class="fas fa-city me-1"></i>' + (subText || '--');

  var imgWrap = card.querySelector('.prop-card-img-wrap');
  if (imgWrap) {
    var firstExisting = (model.keptExisting || [])[0] || '';
    var firstNew = (model.newFiles || [])[0] || null;
    var imgEl = imgWrap.querySelector('.prop-card-img');
    var phEl = imgWrap.querySelector('.prop-card-img-placeholder');
    var nextSrc = firstExisting ? sqhImgSrc(firstExisting) : (firstNew ? URL.createObjectURL(firstNew) : '');
    if (nextSrc) {
      if (!imgEl) {
        imgEl = document.createElement('img');
        imgEl.className = 'prop-card-img';
        imgEl.alt = model.name;
        if (phEl) phEl.remove();
        imgWrap.insertBefore(imgEl, imgWrap.firstChild);
      }
      imgEl.src = nextSrc;
      imgEl.alt = model.name;
    } else {
      if (imgEl) imgEl.remove();
      if (!phEl) {
        var newPh = document.createElement('div');
        newPh.className = 'prop-card-img-placeholder';
        newPh.innerHTML = '<i class="fas fa-home"></i>';
        imgWrap.insertBefore(newPh, imgWrap.firstChild);
      }
    }

    var totalImgs = (model.keptExisting || []).length + (model.newFiles || []).length;
    var countEl = imgWrap.querySelector('.prop-card-img-count');
    if (totalImgs > 1) {
      if (!countEl) {
        countEl = document.createElement('span');
        countEl.className = 'prop-card-img-count';
        imgWrap.appendChild(countEl);
      }
      countEl.innerHTML = '<i class="fas fa-images me-1"></i>' + totalImgs;
    } else if (countEl) {
      countEl.remove();
    }
  }
}

function _saveEditedProperty() {
  if (!_editPropId) return;
  if (_editPropIsSold) {
    showToast('Sold properties can no longer be edited.', 'warning');
    return;
  }
  var confirmBtn = document.getElementById('confirmEditPropSaveBtn');
  var triggerBtn = document.getElementById('editPropBtn');
  var errEl = document.getElementById('editPropError');
  errEl.classList.add('d-none');

  var v = _validateEditPropertyRequired();
  if (!v.ok) {
    errEl.textContent = 'Name, location, model type, and price are required.';
    errEl.classList.remove('d-none');
    return;
  }

  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving…';
  }

  var fd = new FormData();
  fd.append('name',           v.name);
  fd.append('location',       v.location);
  fd.append('region',         document.getElementById('ep_region').value.trim());
  fd.append('region_code',    document.getElementById('ep_region_code').value.trim());
  fd.append('region_name',    document.getElementById('ep_region_name').value.trim());
  fd.append('province_code',  document.getElementById('ep_province_code').value.trim());
  fd.append('province_name',  document.getElementById('ep_province_name').value.trim());
  fd.append('citymun_code',   document.getElementById('ep_citymun_code').value.trim());
  fd.append('citymun_name',   document.getElementById('ep_citymun_name').value.trim());
  fd.append('barangay_code',  document.getElementById('ep_barangay_code').value.trim());
  fd.append('barangay_name',  document.getElementById('ep_barangay_name').value.trim());
  fd.append('prop_type',      v.propType);
  fd.append('price',          v.price);
  fd.append('bedrooms',       document.getElementById('ep_bedrooms').value || '0');
  fd.append('bathrooms',      document.getElementById('ep_bathrooms').value || '0');
  fd.append('storeys',        document.getElementById('ep_storeys').value || '1');
  fd.append('floor_area',     document.getElementById('ep_floor_area').value.trim());
  fd.append('lot_area',       document.getElementById('ep_lot_area').value.trim());
  fd.append('subdivision_id', document.getElementById('ep_subdivision').value);
  var epAgentEl = document.getElementById('ep_agent_id');
  if (epAgentEl) fd.append('agent_id', epAgentEl.value || '');
  fd.append('description',    document.getElementById('ep_description').value.trim());

  var kept = [];
  document.querySelectorAll('#ep_images_wrap input[name="existing_img"]').forEach(function (inp) {
    kept.push(inp.value);
    fd.append('existing_images', inp.value);
  });

  var allOriginal = document.querySelector('#editPropertyModal').dataset.allImages || '';
  allOriginal.split(',').filter(Boolean).forEach(function (fname) {
    if (!kept.includes(fname)) fd.append('remove_images', fname);
  });

  var newFiles = _pendingNewFiles.filter(Boolean);
  newFiles.forEach(function (f) { fd.append('images', f); });
  fd.append('csrf_token', csrfToken());

  fetch('/agent/property/' + _editPropId + '/edit', { method: 'POST', body: fd })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save';
      }
      if (!res.ok) {
        errEl.textContent = res.data.error || 'An error occurred.';
        errEl.classList.remove('d-none');
        return;
      }

      _applyEditedPropertyCard(_editPropId, {
        name: v.name,
        location: v.location,
        region: document.getElementById('ep_region').value.trim(),
        propType: v.propType,
        price: v.price,
        bedrooms: document.getElementById('ep_bedrooms').value || '0',
        bathrooms: document.getElementById('ep_bathrooms').value || '0',
        storeys: document.getElementById('ep_storeys').value || '1',
        floorArea: document.getElementById('ep_floor_area').value.trim(),
        lotArea: document.getElementById('ep_lot_area').value.trim(),
        subdivisionId: document.getElementById('ep_subdivision').value,
        description: document.getElementById('ep_description').value.trim(),
        keptExisting: kept,
        newFiles: newFiles
      });

      if (triggerBtn) triggerBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes';
      var cm = bootstrap.Modal.getInstance(document.getElementById('editPropSaveConfirmModal'));
      if (cm) cm.hide();
      var em = bootstrap.Modal.getInstance(document.getElementById('editPropertyModal'));
      if (em) em.hide();
      showToast('Property updated.', 'success');
      _pendingNewFiles = [];
      var epInput = document.getElementById('ep_images'); if (epInput) epInput.value = '';
      var epFn = document.getElementById('ep_images_filenames'); if (epFn) epFn.value = '';
    })
    .catch(function () {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save';
      }
      errEl.textContent = 'Network error. Please try again.';
      errEl.classList.remove('d-none');
    });
}

document.getElementById('editPropBtn') && document.getElementById('editPropBtn').addEventListener('click', function () {
  if (_editPropIsSold) return;
  var errEl = document.getElementById('editPropError');
  errEl.classList.add('d-none');
  var v = _validateEditPropertyRequired();
  if (!v.ok) {
    errEl.textContent = 'Name, location, model type, and price are required.';
    errEl.classList.remove('d-none');
    return;
  }
  var scrollEl = _getEditModalScrollEl();
  _editModalScrollTop = scrollEl ? scrollEl.scrollTop : 0;
  var confirmEl = document.getElementById('editPropSaveConfirmModal');
  var confirmModal = bootstrap.Modal.getOrCreateInstance(confirmEl, { backdrop: true, focus: false });
  confirmModal.show();
});

document.getElementById('confirmEditPropSaveBtn') && document.getElementById('confirmEditPropSaveBtn').addEventListener('click', function () {
  _saveEditedProperty();
});

document.getElementById('editPropSaveConfirmModal') && document.getElementById('editPropSaveConfirmModal').addEventListener('shown.bs.modal', function () {
  var modalEl = this;
  var backdrops = document.querySelectorAll('.modal-backdrop');
  var topBackdrop = backdrops.length ? backdrops[backdrops.length - 1] : null;
  var scrollEl = _getEditModalScrollEl();
  if (scrollEl) scrollEl.scrollTop = _editModalScrollTop || 0;
  clearInterval(_editScrollSyncTimer);
  _editScrollSyncTimer = setInterval(function () {
    var activeScrollEl = _getEditModalScrollEl();
    if (activeScrollEl) activeScrollEl.scrollTop = _editModalScrollTop || 0;
  }, 50);
  if (topBackdrop) topBackdrop.style.zIndex = '1085';
  modalEl.style.zIndex = '1090';
  document.body.classList.add('modal-open');
});

document.getElementById('editPropSaveConfirmModal') && document.getElementById('editPropSaveConfirmModal').addEventListener('hidden.bs.modal', function () {
  clearInterval(_editScrollSyncTimer);
  _editScrollSyncTimer = null;
  this.style.zIndex = '';
  var scrollEl = _getEditModalScrollEl();
  if (scrollEl) scrollEl.scrollTop = _editModalScrollTop || 0;
  var editModal = document.getElementById('editPropertyModal');
  if (editModal && editModal.classList.contains('show')) {
    document.body.classList.add('modal-open');
  }
});

document.getElementById('editPropertyModal') && document.getElementById('editPropertyModal').addEventListener('show.bs.modal', function () {
  if (!_editPropId) return;
  var card = document.querySelector('.listing-card[data-prop-id="' + _editPropId + '"]');
  this.dataset.allImages = card ? (card.dataset.images || '') : '';
});

/* ── Delete Property ────────────────────────────────────────── */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.prop-delete-btn');
  if (!btn) return;
  var card = btn.closest('.listing-card');
  var listingStatus = card ? String(card.dataset.listingStatus || '').toLowerCase() : '';
  if (listingStatus === 'sold') {
    showToast('Sold properties cannot be deleted.', 'warning');
    return;
  }
  _confirmPending.propId  = btn.dataset.propId;
  _confirmPending.action  = 'delete-prop';

  document.getElementById('agentConfirmIcon').innerHTML   = '<i class="fas fa-trash" style="color: var(--clr-primary);"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
  document.getElementById('agentConfirmTitle').textContent = 'Delete this property?';
  document.getElementById('agentConfirmDesc').textContent  = 'This listing will be permanently removed and cannot be recovered.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-crimson px-4';
  cb.innerHTML = '<i class="fas fa-trash me-1"></i> Delete';
  document.getElementById('agentConfirmNoteWrap').classList.add('d-none');

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
});

/* ── Trip Approve ───────────────────────────────────────────── */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.trip-approve-btn');
  if (!btn) return;
  _confirmPending.tripId = btn.dataset.tripId;
  _confirmPending.action = 'approve-trip';

  document.getElementById('agentConfirmIcon').innerHTML   = '<i class="fas fa-check-circle"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-accent-dk)';
  document.getElementById('agentConfirmTitle').textContent = 'Approve this tripping request?';
  document.getElementById('agentConfirmDesc').textContent  = 'The client will be notified that their property visit has been confirmed.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-lime px-4';
  cb.innerHTML = '<i class="fas fa-check me-1"></i> Approve';
  var nw = document.getElementById('agentConfirmNoteWrap');
  nw.classList.remove('d-none');
  document.getElementById('agentConfirmNote').value = '';

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
});

/* ── Trip Reject ────────────────────────────────────────────── */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.trip-reject-btn');
  if (!btn) return;
  _confirmPending.tripId = btn.dataset.tripId;
  _confirmPending.action = 'reject-trip';

  document.getElementById('agentConfirmIcon').innerHTML   = '<i class="fas fa-times-circle"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
  document.getElementById('agentConfirmTitle').textContent = 'Reject this tripping request?';
  document.getElementById('agentConfirmDesc').textContent  = 'The client will be notified. You may optionally leave a note explaining the reason.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-crimson px-4';
  cb.innerHTML = '<i class="fas fa-times me-1"></i> Reject';
  var nw = document.getElementById('agentConfirmNoteWrap');
  nw.classList.remove('d-none');
  document.getElementById('agentConfirmNote').value = '';

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
});

/* ── Trip Mark Sold ────────────────────────────────────────── */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.trip-bought-btn');
  if (!btn) return;
  _confirmPending.tripId = btn.dataset.tripId;
  _confirmPending.action = 'mark-bought';

  document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-handshake"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-blue)';
  document.getElementById('agentConfirmTitle').textContent = 'Mark this property as sold?';
  document.getElementById('agentConfirmDesc').textContent = 'This will mark the listing as sold and close other open requests for this property.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-outline-blue px-4';
  cb.innerHTML = '<i class="fas fa-handshake me-1"></i> Mark Sold';
  var nw = document.getElementById('agentConfirmNoteWrap');
  nw.classList.remove('d-none');
  document.getElementById('agentConfirmNote').value = '';

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
});

/* ── Trip Delete ────────────────────────────────────────────── */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.trip-delete-btn');
  if (!btn) return;
  _confirmPending.tripId = btn.dataset.tripId;
  _confirmPending.action = 'delete-trip';

  document.getElementById('agentConfirmIcon').innerHTML   = '<i class="fas fa-trash"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
  document.getElementById('agentConfirmTitle').textContent = 'Delete this tripping request?';
  document.getElementById('agentConfirmDesc').textContent  = 'This request will be permanently removed from your records.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-crimson px-4';
  cb.innerHTML = '<i class="fas fa-trash me-1"></i> Delete';
  document.getElementById('agentConfirmNoteWrap').classList.add('d-none');

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
});

function _openTripConfirmFromModal(action, tripId) {
  if (!tripId) return;
  _confirmPending.tripId = tripId;
  _confirmPending.action = action;

  if (action === 'approve-trip') {
    document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-check-circle"></i>';
    document.getElementById('agentConfirmIcon').style.color = 'var(--clr-accent-dk)';
    document.getElementById('agentConfirmTitle').textContent = 'Approve this tripping request?';
    document.getElementById('agentConfirmDesc').textContent = 'The client will be notified that their property visit has been confirmed.';
    var approveBtn = document.getElementById('agentConfirmBtn');
    approveBtn.className = 'btn btn-lime px-4';
    approveBtn.innerHTML = '<i class="fas fa-check me-1"></i> Approve';
    document.getElementById('agentConfirmNoteWrap').classList.remove('d-none');
    document.getElementById('agentConfirmNote').value = '';
  } else if (action === 'reject-trip') {
    document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-times-circle"></i>';
    document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
    document.getElementById('agentConfirmTitle').textContent = 'Reject this tripping request?';
    document.getElementById('agentConfirmDesc').textContent = 'The client will be notified. You may optionally leave a note explaining the reason.';
    var rejectBtn = document.getElementById('agentConfirmBtn');
    rejectBtn.className = 'btn btn-crimson px-4';
    rejectBtn.innerHTML = '<i class="fas fa-times me-1"></i> Reject';
    document.getElementById('agentConfirmNoteWrap').classList.remove('d-none');
    document.getElementById('agentConfirmNote').value = '';
  } else if (action === 'delete-trip') {
    document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-trash"></i>';
    document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
    document.getElementById('agentConfirmTitle').textContent = 'Delete this tripping request?';
    document.getElementById('agentConfirmDesc').textContent = 'Only approved, sold, or rejected requests can be deleted. This cannot be undone.';
    var deleteBtn = document.getElementById('agentConfirmBtn');
    deleteBtn.className = 'btn btn-crimson px-4';
    deleteBtn.innerHTML = '<i class="fas fa-trash me-1"></i> Delete';
    document.getElementById('agentConfirmNoteWrap').classList.add('d-none');
  } else if (action === 'mark-bought') {
    document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-handshake"></i>';
    document.getElementById('agentConfirmIcon').style.color = 'var(--clr-blue)';
    document.getElementById('agentConfirmTitle').textContent = 'Mark this property as sold?';
    document.getElementById('agentConfirmDesc').textContent = 'This will mark the listing as sold and close other open requests for this property.';
    var boughtBtn = document.getElementById('agentConfirmBtn');
    boughtBtn.className = 'btn btn-outline-blue px-4';
    boughtBtn.innerHTML = '<i class="fas fa-handshake me-1"></i> Mark Sold';
    document.getElementById('agentConfirmNoteWrap').classList.remove('d-none');
    document.getElementById('agentConfirmNote').value = '';
  } else {
    return;
  }

  var tripModal = bootstrap.Modal.getInstance(document.getElementById('tripRequestModal'));
  if (tripModal) tripModal.hide();
  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
}

function _openTripAutoDeleteConfirm(tripId, status) {
  if (!tripId) return;
  _confirmPending.tripId = tripId;
  _confirmPending.tripStatus = (status || '').toLowerCase();
  _confirmPending.action = 'auto-delete-trip';

  document.getElementById('agentConfirmIcon').innerHTML = '<i class="fas fa-trash"></i>';
  document.getElementById('agentConfirmIcon').style.color = 'var(--clr-danger)';
  document.getElementById('agentConfirmTitle').textContent = 'Delete this tripping request?';
  document.getElementById('agentConfirmDesc').textContent = 'Pending requests will be auto-rejected first, then removed from your dashboard.';
  var cb = document.getElementById('agentConfirmBtn');
  cb.className = 'btn btn-crimson px-4';
  cb.innerHTML = '<i class="fas fa-trash me-1"></i> Delete';
  document.getElementById('agentConfirmNoteWrap').classList.add('d-none');

  bootstrap.Modal.getOrCreateInstance(document.getElementById('agentConfirmModal')).show();
}

document.getElementById('trmApproveBtn') && document.getElementById('trmApproveBtn').addEventListener('click', function () {
  var modalEl = document.getElementById('tripRequestModal');
  var status = ((modalEl && modalEl.dataset.tripStatus) || '').toLowerCase();
  if (status !== 'pending') return;
  _openTripConfirmFromModal('approve-trip', modalEl ? modalEl.dataset.tripId : null);
});

document.getElementById('trmRejectBtn') && document.getElementById('trmRejectBtn').addEventListener('click', function () {
  var modalEl = document.getElementById('tripRequestModal');
  var status = ((modalEl && modalEl.dataset.tripStatus) || '').toLowerCase();
  if (status !== 'pending') return;
  _openTripConfirmFromModal('reject-trip', modalEl ? modalEl.dataset.tripId : null);
});

document.getElementById('trmMarkBoughtBtn') && document.getElementById('trmMarkBoughtBtn').addEventListener('click', function () {
  var modalEl = document.getElementById('tripRequestModal');
  var status = ((modalEl && modalEl.dataset.tripStatus) || '').toLowerCase();
  if (status !== 'approved') return;
  _openTripConfirmFromModal('mark-bought', modalEl ? modalEl.dataset.tripId : null);
});

document.getElementById('trmDeleteBtn') && document.getElementById('trmDeleteBtn').addEventListener('click', function () {
  var modalEl = document.getElementById('tripRequestModal');
  var status = ((modalEl && modalEl.dataset.tripStatus) || '').toLowerCase();
  if (status !== 'approved' && status !== 'rejected' && status !== 'sold') return;
  _openTripConfirmFromModal('delete-trip', modalEl ? modalEl.dataset.tripId : null);
});

document.addEventListener('click', function (e) {
  var btn = e.target.closest('.trip-auto-delete-btn');
  if (!btn) return;
  var row = btn.closest('tr');
  _openTripAutoDeleteConfirm(btn.dataset.tripId, row ? row.dataset.status : '');
});

document.getElementById('agentConfirmModal') && document.getElementById('agentConfirmModal').addEventListener('hidden.bs.modal', function () {
  var nw = document.getElementById('agentConfirmNoteWrap');
  if (nw) nw.classList.add('d-none');
  _confirmPending = { action: null, tripId: null, tripStatus: null, propId: null };
});

document.getElementById('agentConfirmBtn') && document.getElementById('agentConfirmBtn').addEventListener('click', function () {
  var btn    = this;
  var action = _confirmPending.action;
  if (!action) return;

  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>\u2026';

  if (action === 'delete-prop') {
    fetch('/agent/property/' + _confirmPending.propId + '/delete', { method: 'POST', headers: { 'X-CSRFToken': csrfToken() } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        bootstrap.Modal.getInstance(document.getElementById('agentConfirmModal')).hide();
        btn.disabled = false;
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('Model deleted.', 'info');
        var col = document.querySelector('.listing-card-col .listing-card[data-prop-id="' + _confirmPending.propId + '"]');
        if (col) col.closest('.listing-card-col').remove();
      })
      .catch(function () { btn.disabled = false; showToast('Network error.', 'danger'); });

  } else if (action === 'approve-trip' || action === 'reject-trip') {
    var note     = (document.getElementById('agentConfirmNote').value || '').trim();
    var endpoint = '/agent/trip/' + _confirmPending.tripId + '/' + (action === 'approve-trip' ? 'approve' : 'reject');
    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ note: note })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        bootstrap.Modal.getInstance(document.getElementById('agentConfirmModal')).hide();
        btn.disabled = false;
        if (data.error) { showToast(data.error, 'danger'); return; }
        var newStatus = data.status;
        var cls = newStatus === 'approved' ? 'badge-qualified' : 'badge-not-qualified';
        var label = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
        showToast('Trip request ' + newStatus + '.', newStatus === 'approved' ? 'success' : 'info');
        var tripId = _confirmPending.tripId;
        document.querySelectorAll('[data-trip-row="' + tripId + '"]').forEach(function (row) {
          row.dataset.status = newStatus;
          row.dataset.tripStatus = label;
          var badgeEl = row.querySelector('.trip-status-badge');
          if (badgeEl) badgeEl.innerHTML = '<span class="sqh-badge ' + cls + '">' + label + '</span>';
        });

        var tripModal = document.getElementById('tripRequestModal');
        if (tripModal && tripModal.dataset.tripId === String(tripId)) {
          tripModal.dataset.tripStatus = newStatus;
          _setTripModalStatus(newStatus);
          var trmApprove = document.getElementById('trmApproveBtn');
          var trmReject = document.getElementById('trmRejectBtn');
          var trmMarkBought = document.getElementById('trmMarkBoughtBtn');
          var trmDelete = document.getElementById('trmDeleteBtn');
          var isPending = newStatus === 'pending';
          var isApproved = newStatus === 'approved';
          var isRejected = newStatus === 'rejected';
          if (trmApprove) trmApprove.classList.toggle('d-none', !isPending);
          if (trmReject) trmReject.classList.toggle('d-none', !isPending);
          if (trmMarkBought) trmMarkBought.classList.toggle('d-none', !isApproved);
          if (trmDelete) trmDelete.classList.toggle('d-none', !isRejected);
        }
      })
      .catch(function () { btn.disabled = false; showToast('Network error.', 'danger'); });

  } else if (action === 'mark-bought') {
    var noteBought = (document.getElementById('agentConfirmNote').value || '').trim();
    var tripIdBought = _confirmPending.tripId;
    fetch('/agent/trip/' + tripIdBought + '/mark-bought', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ note: noteBought })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        bootstrap.Modal.getInstance(document.getElementById('agentConfirmModal')).hide();
        btn.disabled = false;
        if (data.error) { showToast(data.error, 'danger'); return; }

        document.querySelectorAll('[data-trip-row="' + tripIdBought + '"]').forEach(function (row) {
          row.dataset.status = 'sold';
          row.dataset.tripStatus = 'Sold';
          var badgeEl = row.querySelector('.trip-status-badge');
          if (badgeEl) badgeEl.innerHTML = '<span class="sqh-badge badge-sold">Sold</span>';
          var actionBoughtBtn = row.querySelector('.trip-bought-btn');
          if (actionBoughtBtn) actionBoughtBtn.remove();
        });

        var tripModal = document.getElementById('tripRequestModal');
        if (tripModal && tripModal.dataset.tripId === String(tripIdBought)) {
          tripModal.dataset.tripStatus = 'sold';
          _setTripModalStatus('sold');
          var trmApproveBtn = document.getElementById('trmApproveBtn');
          var trmRejectBtn = document.getElementById('trmRejectBtn');
          var trmMarkBtn = document.getElementById('trmMarkBoughtBtn');
          var trmDeleteBtn = document.getElementById('trmDeleteBtn');
          if (trmApproveBtn) trmApproveBtn.classList.add('d-none');
          if (trmRejectBtn) trmRejectBtn.classList.add('d-none');
          if (trmMarkBtn) trmMarkBtn.classList.add('d-none');
          if (trmDeleteBtn) trmDeleteBtn.classList.remove('d-none');
        }

        showToast('Property marked as sold.', 'success');
        setTimeout(function () { location.reload(); }, 800);
      })
      .catch(function () { btn.disabled = false; showToast('Network error.', 'danger'); });

  } else if (action === 'delete-trip') {
    var tripIdToDelete = _confirmPending.tripId;
    fetch('/agent/trip/' + tripIdToDelete + '/delete', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        bootstrap.Modal.getInstance(document.getElementById('agentConfirmModal')).hide();
        btn.disabled = false;
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('Tripping request deleted.', 'info');
        document.querySelectorAll('[data-trip-row="' + tripIdToDelete + '"]').forEach(function (row) { row.remove(); });
        var tripModal = document.getElementById('tripRequestModal');
        if (tripModal && tripModal.dataset.tripId === String(tripIdToDelete)) {
          var tripModalInstance = bootstrap.Modal.getInstance(tripModal);
          if (tripModalInstance) tripModalInstance.hide();
        }
      })
      .catch(function () { btn.disabled = false; showToast('Network error.', 'danger'); });

  } else if (action === 'auto-delete-trip') {
    var autoTripId = _confirmPending.tripId;
    var autoTripStatus = (_confirmPending.tripStatus || '').toLowerCase();
    var rejectFirst = Promise.resolve();

    if (autoTripStatus === 'pending') {
      rejectFirst = fetch('/agent/trip/' + autoTripId + '/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ note: 'Request removed by agent.' })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.error) throw new Error(d.error);
      });
    }

    rejectFirst
      .then(function () {
        return fetch('/agent/trip/' + autoTripId + '/delete', {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken() }
        });
      })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        bootstrap.Modal.getInstance(document.getElementById('agentConfirmModal')).hide();
        btn.disabled = false;
        if (data.error) { showToast(data.error, 'danger'); return; }
        showToast('Tripping request deleted.', 'info');
        document.querySelectorAll('[data-trip-row="' + autoTripId + '"]').forEach(function (row) { row.remove(); });
        var tripModal = document.getElementById('tripRequestModal');
        if (tripModal && tripModal.dataset.tripId === String(autoTripId)) {
          var tripModalInstance = bootstrap.Modal.getInstance(tripModal);
          if (tripModalInstance) tripModalInstance.hide();
        }
      })
      .catch(function (err) {
        btn.disabled = false;
        showToast((err && err.message) ? err.message : 'Network error.', 'danger');
      });

  }
});

/* ── Client Detail Modal ────────────────────────────────────── */
function openClientModal(userId) {
  var loadEl = document.getElementById('cdm-loading');
  var bodyEl = document.getElementById('cdm-body');
  loadEl.classList.remove('d-none');
  bodyEl.classList.add('d-none');
  loadEl.innerHTML = '<div class="spinner-border" style="color:var(--clr-primary);" role="status"><span class="visually-hidden">Loading\u2026</span></div>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('clientDetailModal')).show();

  fetch('/agent/client/' + userId + '/profile')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) {
        loadEl.innerHTML = '<p class="text-danger small"><i class="fas fa-exclamation-circle me-1"></i>' + d.error + '</p>';
        return;
      }
      var cdmAvatar = document.getElementById('cdm-avatar');
      if (cdmAvatar) {
        if (d.avatar_url) {
          cdmAvatar.innerHTML = '<img src="' + d.avatar_url + '?t=' + Date.now() + '" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">';
        } else {
          cdmAvatar.textContent = d.initials;
        }
      }
      document.getElementById('cdm-name').textContent   = d.full_name;

      var statusCls = '';
      if (d.assessment) {
        statusCls = d.assessment.status === 'Qualified' ? 'badge-qualified'
                  : d.assessment.status === 'Conditionally Qualified' ? 'badge-conditional'
                  : 'badge-not-qualified';
      }
      document.getElementById('cdm-meta').innerHTML =
        '<span class="sqh-badge" style="background:rgba(255,255,255,.18);color:#fff;border:1.5px solid rgba(255,255,255,.3);">' +
          '<i class="fas fa-user me-1"></i>Client</span>' +
        (d.assessment ? '<span class="sqh-badge ' + statusCls + '">' + d.assessment.status + '</span>' : '');

      var html = '<div class="form-section-title"><i class="fas fa-id-card me-2"></i>Identity</div>';
      html += '<div class="row g-3 mb-3">';
      html += _cdmField('First Name', d.first_name);
      html += _cdmField('Middle Name', d.middle_name || '—');
      html += _cdmField('Last Name', d.last_name);
      html += _cdmField('Username', d.username || '—');
      html += _cdmField('Email', d.email);
      html += _cdmField('Contact Number', d.contact_number);
      html += _cdmField('Joined', d.joined_at || d.joined);
      html += '</div>';

      if (d.profile) {
        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-user me-2"></i>Personal Information</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Civil Status', d.profile.civil_status);
        html += _cdmField('Citizenship', d.profile.citizenship);
        html += _cdmField('Gender', d.profile.gender);
        html += _cdmField('Dependents', d.profile.dependents);
        html += _cdmField('Birth Date', d.profile.birth_date);
        html += _cdmField('Birthplace', d.profile.birthplace);
        html += '</div>';

        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-map-marker-alt me-2"></i>Address Details</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Address Line', d.profile.address_line);
        html += _cdmField('Street', d.profile.street);
        html += _cdmField('Blk', d.profile.blk);
        html += _cdmField('Lot', d.profile.lot);
        html += _cdmField('Subdivision', d.profile.subdivision_name);
        html += _cdmField('Barangay', d.profile.home_barangay_name);
        html += _cdmField('City / Municipality', d.profile.home_citymun_name);
        html += _cdmField('Province', d.profile.home_province_name);
        html += _cdmField('Region', d.profile.home_region_name);
        html += _cdmField('Country', d.profile.country);
        html += _cdmField('Zip Code', d.profile.zip_code);
        html += '</div>';

        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-briefcase me-2"></i>Employment & Financial</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Employment Type', d.profile.employment_type);
        html += _cdmField('Employer Name', d.profile.employer_name);
        html += _cdmField('Employer Phone', d.profile.employer_phone);
        html += _cdmField('Employer Email', d.profile.employer_email);
        html += _cdmField('Business Address', d.profile.employer_business_address);
        html += _cdmField('Tenure (Months)', d.profile.tenure_months);
        html += _cdmField('SSS/GSIS/UMID', d.profile.sss_gsis_umid);
        html += _cdmField('TIN', d.profile.tin_no);
        html += _cdmField('Gross Income', d.profile.gross_income);
        html += _cdmField('Monthly Loans', d.profile.monthly_loans);
        html += _cdmField('Other Deductions', d.profile.other_deductions);
        html += _cdmField('Preferred Type', d.profile.preferred_type);
        html += _cdmField('Budget Min', d.profile.budget_min);
        html += _cdmField('Budget Max', d.profile.budget_max);
        html += '</div>';

        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-hashtag me-2"></i>Social Media</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Instagram', d.profile.social_instagram);
        html += _cdmField('Twitter / X', d.profile.social_twitter_x);
        html += _cdmField('Viber', d.profile.social_viber);
        html += _cdmField('WhatsApp', d.profile.social_whatsapp);
        html += '</div>';
      }

      if (d.documents) {
        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-id-card me-2"></i>Document Honesty Check</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Do you currently have one valid ID?', d.documents.has_valid_id || '\u2014');
        html += _cdmField('Do you currently have proof of income?', d.documents.has_income_proof || '\u2014');
        html += '</div>';
      }

      if (d.assessment) {
        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-brain me-2"></i>Latest Assessment</div>';
        html += '<div class="row g-3 mb-3">';
        html += _cdmField('Assessment Date', d.assessment.date);
        html += _cdmField('Status', d.assessment.status);
        html += _cdmField('DTI Ratio', d.assessment.dti);
        html += _cdmField('Max Loanable', d.assessment.max_loanable);
        html += _cdmField('Similarity', d.assessment.similarity);
        html += '</div>';
      }

      if (d.assessments && d.assessments.length) {
        html += '<hr class="my-3">';
        html += '<div class="form-section-title"><i class="fas fa-history me-2"></i>Recent Assessments</div>';
        html += '<div class="table-responsive cdm-assess-table-wrap"><table class="table sqh-table mb-0 small"><thead><tr><th>Date</th><th>Assessment Type</th><th>Result</th><th>DTI</th><th>Max Loanable</th><th>Similarity</th></tr></thead><tbody>';
        d.assessments.forEach(function (row) {
          var resultCls = row.status === 'Qualified' ? 'badge-qualified'
            : row.status === 'Conditionally Qualified' ? 'badge-conditional'
            : 'badge-not-qualified';
          html += '<tr>'
            + '<td>' + _cdmValue(row.date) + '</td>'
            + '<td>' + _cdmAssessmentTypeBadge(row.assessment_mode) + '</td>'
            + '<td><span class="sqh-badge ' + resultCls + '">' + _cdmValue(row.status) + '</span></td>'
            + '<td>' + _cdmValue(row.dti) + '</td>'
            + '<td>' + _cdmValue(row.max_loanable) + '</td>'
            + '<td>' + _cdmValue(row.similarity) + '</td>'
            + '</tr>';
        });
        html += '</tbody></table></div>';
      }

      if (!d.assessment) {
        html += '<p class="text-muted small mt-3"><i class="fas fa-info-circle me-1"></i>No qualification assessment on record.</p>';
      }

      bodyEl.innerHTML = html;
      loadEl.classList.add('d-none');
      bodyEl.classList.remove('d-none');
    })
    .catch(function () {
      loadEl.innerHTML = '<p class="text-danger small"><i class="fas fa-exclamation-circle me-1"></i>Failed to load client data.</p>';
    });
}

(function initClientDetailModalReturn() {
  var cdm = document.getElementById('clientDetailModal');
  if (!cdm) return;
  cdm.addEventListener('hidden.bs.modal', function () {
    if (!_returnToEditAfterClientModal) return;
    _returnToEditAfterClientModal = false;
    if (!_editPropId) return;
    setTimeout(function () {
      openEditPropertyModal(_editPropId);
    }, 120);
  });
})();

var _trmImages = [];
var _trmIndex = 0;

function _trmRenderImage() {
  var imgEl = document.getElementById('trmImg');
  var placeholderEl = document.getElementById('trmImgPlaceholder');
  if (!imgEl || !placeholderEl) return;

  if (!_trmImages.length) {
    imgEl.classList.add('d-none');
    placeholderEl.classList.remove('d-none');
    return;
  }

  var src = (_trmImages[_trmIndex] || '').trim();
  if (!src) {
    imgEl.classList.add('d-none');
    placeholderEl.classList.remove('d-none');
    return;
  }

  imgEl.src = sqhImgSrc(src);
  imgEl.classList.remove('d-none');
  placeholderEl.classList.add('d-none');
}

function _trmRenderDots() {
  var dotsEl = document.getElementById('trmDots');
  if (!dotsEl) return;
  dotsEl.innerHTML = '';
  if (_trmImages.length <= 1) return;
  _trmImages.forEach(function (_, i) {
    var dot = document.createElement('span');
    dot.className = 'trm-dot' + (i === _trmIndex ? ' active' : '');
    dot.dataset.idx = String(i);
    dotsEl.appendChild(dot);
  });
}

function _trmUpdateNav() {
  var prevBtn = document.getElementById('trmPrev');
  var nextBtn = document.getElementById('trmNext');
  var multi = _trmImages.length > 1;
  if (prevBtn) prevBtn.classList.toggle('d-none', !multi);
  if (nextBtn) nextBtn.classList.toggle('d-none', !multi);
}

function _setTripModalStatus(statusRaw) {
  var el = document.getElementById('trmStatus');
  if (!el) return;
  var s = (statusRaw || '').toLowerCase();
  var label = s ? (s.charAt(0).toUpperCase() + s.slice(1)) : 'Pending';
  el.textContent = label;
  el.classList.remove('status-pending', 'status-approved', 'status-rejected');
  if (s === 'approved' || s === 'sold') el.classList.add('status-approved');
  else if (s === 'rejected') el.classList.add('status-rejected');
  else el.classList.add('status-pending');
}

document.getElementById('trmPrev') && document.getElementById('trmPrev').addEventListener('click', function () {
  if (!_trmImages.length) return;
  _trmIndex = (_trmIndex - 1 + _trmImages.length) % _trmImages.length;
  _trmRenderImage();
  _trmRenderDots();
});

document.getElementById('trmNext') && document.getElementById('trmNext').addEventListener('click', function () {
  if (!_trmImages.length) return;
  _trmIndex = (_trmIndex + 1) % _trmImages.length;
  _trmRenderImage();
  _trmRenderDots();
});

document.getElementById('trmDots') && document.getElementById('trmDots').addEventListener('click', function (e) {
  var dot = e.target.closest('.trm-dot');
  if (!dot) return;
  var idx = parseInt(dot.dataset.idx, 10);
  if (isNaN(idx)) return;
  _trmIndex = idx;
  _trmRenderImage();
  _trmRenderDots();
});

function _cdmField(label, value) {
  return '<div class="col-md-6">'
    + '<div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--clr-muted);margin-bottom:.25rem;">' + label + '</div>'
    + '<div class="fw-semibold">' + _cdmValue(value) + '</div>'
    + '</div>';
}

function _cdmValue(value) {
  var raw = (value !== undefined && value !== null) ? String(value) : '\u2014';
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _cdmAssessmentTypeBadge(mode) {
  if ((mode || '').toLowerCase() === 'new') {
    return '<span class="sqh-badge" style="background:rgba(40,167,69,.12);color:#1a7a35;">New</span>';
  }
  return '<span class="sqh-badge" style="background:rgba(26,38,153,.12);color:var(--clr-blue);">Re-Assess</span>';
}

function _cdmDocRow(doc) {
  var hasFile = !!(doc && doc.has_file);
  var statusHtml = hasFile
    ? '<span class="sqh-badge" style="background:rgba(40,167,69,.12);color:#1a7a35;">Uploaded</span>'
    : '<span class="sqh-badge" style="background:rgba(139,26,26,.08);color:var(--clr-primary);">Not Uploaded</span>';
  var actionHtml = hasFile && doc.view_url
    ? '<a href="' + doc.view_url + '" target="_blank" rel="noopener" class="btn btn-outline-blue btn-sm prof-doc-view-btn">View</a>'
    : '<button type="button" class="btn btn-outline-blue btn-sm prof-doc-view-btn" disabled>View</button>';
  return '<tr>'
    + '<td>' + _cdmValue(doc && doc.label ? doc.label : '\u2014') + '</td>'
    + '<td>' + _cdmValue(doc && doc.filename ? doc.filename : '\u2014') + '</td>'
    + '<td>' + statusHtml + '</td>'
    + '<td>' + actionHtml + '</td>'
    + '</tr>';
}

/* ── Profile — live field validation helpers ────────────────── */
function _profLvSet(inp, errId, msg) {
  inp.classList.remove('lv-valid', 'lv-invalid');
  var errEl = document.getElementById(errId);
  if (msg) {
    inp.classList.add('lv-invalid');
    if (errEl) { errEl.innerHTML = '<i class="fas fa-exclamation-circle"></i> ' + msg; errEl.classList.add('sqh-err-visible'); }
  } else {
    inp.classList.add('lv-valid');
    if (errEl) { errEl.innerHTML = ''; errEl.classList.remove('sqh-err-visible'); }
  }
}

/* ── Profile — pw checklist live update ─────────────────────── */
(function () {
  var pwInp = document.getElementById('prof_new_password');
  var cfInp = document.getElementById('prof_confirm_password');
  if (!pwInp) return;

  function updateChecklist(val) {
    var lenOk     = val.length >= 6;
    var upperOk   = /[A-Z]/.test(val);
    var numOk     = /[0-9]/.test(val);
    var specialOk = /[^A-Za-z0-9]/.test(val);
    function setItem(id, ok) {
      var el = document.getElementById(id); if (!el) return;
      el.classList.toggle('pw-ok', ok);
      el.querySelector('i').className = ok ? 'fas fa-check-circle' : 'fas fa-circle-dot';
    }
    setItem('profPwLen',     lenOk);
    setItem('profPwUpper',   upperOk);
    setItem('profPwNum',     numOk);
    setItem('profPwSpecial', specialOk);
  }

  pwInp.addEventListener('input', function () {
    var val = this.value;
    if (!val) {
      _profLvSet(this, 'prof_new_password_error', '');
      this.classList.remove('lv-valid', 'lv-invalid');
      updateChecklist('');
    } else if (val.length < 6) {
      _profLvSet(this, 'prof_new_password_error', 'Password must be at least 6 characters.');
      updateChecklist(val);
    } else {
      _profLvSet(this, 'prof_new_password_error', '');
      updateChecklist(val);
    }
    if (cfInp && cfInp.value) cfInp.dispatchEvent(new Event('input'));
  });

  if (cfInp) {
    cfInp.addEventListener('input', function () {
      var cf = this.value;
      var pw = pwInp.value;
      if (!cf) {
        _profLvSet(this, 'prof_confirm_password_error', '');
        this.classList.remove('lv-valid', 'lv-invalid');
      } else if (cf !== pw) {
        _profLvSet(this, 'prof_confirm_password_error', 'Passwords do not match.');
      } else {
        _profLvSet(this, 'prof_confirm_password_error', '');
      }
    });
  }
})();

/* ── Profile — Save button → open confirm modal ─────────────── */
document.getElementById('saveProfileBtn') && document.getElementById('saveProfileBtn').addEventListener('click', function () {
  var errEl     = document.getElementById('profileError');
  errEl.classList.add('d-none');

  var firstName = document.getElementById('prof_first_name').value.trim();
  var lastName  = document.getElementById('prof_last_name').value.trim();
  var username  = (document.getElementById('prof_username') ? document.getElementById('prof_username').value.trim() : '');
  if (!firstName || !lastName) {
    errEl.textContent = 'First name and last name are required.';
    errEl.classList.remove('d-none');
    return;
  }
  if (!username) {
    errEl.textContent = 'Username is required.';
    errEl.classList.remove('d-none');
    return;
  }
  if (username.length < 3) {
    errEl.textContent = 'Username must be at least 3 characters.';
    errEl.classList.remove('d-none');
    return;
  }
  if (!/^[\w.]+$/.test(username)) {
    errEl.textContent = 'Username may contain only letters, numbers, dots, and underscores.';
    errEl.classList.remove('d-none');
    return;
  }

  var oldPass     = (document.getElementById('prof_old_password') ? document.getElementById('prof_old_password').value : '');
  var newPass     = document.getElementById('prof_new_password').value;
  var confirmPass = document.getElementById('prof_confirm_password').value;
  if (newPass) {
    if (!oldPass) {
      errEl.textContent = 'Current password is required to set a new password.';
      errEl.classList.remove('d-none');
      return;
    }
    if (newPass.length < 6) {
      errEl.textContent = 'New password must be at least 6 characters.';
      errEl.classList.remove('d-none');
      return;
    }
    if (newPass !== confirmPass) {
      errEl.textContent = 'New password and confirmation do not match.';
      errEl.classList.remove('d-none');
      return;
    }
  }

  bootstrap.Modal.getOrCreateInstance(document.getElementById('saveProfileModal')).show();
});

/* ── Profile — Confirm modal → actually save ────────────────── */
document.getElementById('confirmSaveProfileBtn') && document.getElementById('confirmSaveProfileBtn').addEventListener('click', function () {
  var btn   = this;
  var errEl = document.getElementById('profileError');

  bootstrap.Modal.getInstance(document.getElementById('saveProfileModal')).hide();

  btn.disabled = true;
  var saveBtn = document.getElementById('saveProfileBtn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving\u2026'; }

  var oldPass = (document.getElementById('prof_old_password') ? document.getElementById('prof_old_password').value : '');
  var newPass = document.getElementById('prof_new_password').value;
  var payload = {
    first_name:     document.getElementById('prof_first_name').value.trim(),
    last_name:      document.getElementById('prof_last_name').value.trim(),
    email:          (document.getElementById('prof_email') ? document.getElementById('prof_email').value.trim() : ''),
    username:       (document.getElementById('prof_username') ? document.getElementById('prof_username').value.trim() : ''),
    contact_number: document.getElementById('prof_contact').value.trim(),
    license_no:     document.getElementById('prof_license').value.trim(),
    contact_no:     document.getElementById('prof_agent_contact').value.trim(),
    bio:            document.getElementById('prof_bio').value.trim(),
  };
  if (oldPass) payload.old_password = oldPass;
  if (newPass) payload.new_password = newPass;

  fetch('/agent/profile/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(payload)
  })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      btn.disabled = false;
      if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
      if (!res.ok) {
        errEl.textContent = res.data.error || 'An error occurred.';
        errEl.classList.remove('d-none');
        return;
      }
      if (document.getElementById('prof_old_password')) document.getElementById('prof_old_password').value = '';
      document.getElementById('prof_new_password').value     = '';
      document.getElementById('prof_confirm_password').value = '';
      // Reset lv states on pw fields
      ['prof_old_password', 'prof_new_password', 'prof_confirm_password'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.classList.remove('lv-valid', 'lv-invalid');
      });
      // Reset pw checklist
      ['profPwLen','profPwUpper','profPwNum','profPwSpecial'].forEach(function (id) {
        var el = document.getElementById(id); if (!el) return;
        el.classList.remove('pw-ok');
        el.querySelector('i').className = 'fas fa-circle-dot';
      });

      var fullName = res.data.full_name || '';
      document.querySelectorAll('.dash-topbar-name').forEach(function (el) { el.textContent = fullName; });
      var heroNameEl = document.getElementById('profHeroName');
      if (heroNameEl) heroNameEl.textContent = fullName;
      // Only update avatar text if no image is showing
      var avatarImg = document.getElementById('profAvatarImg');
      if (!avatarImg) {
        var avatarIcon = document.getElementById('profAvatarIcon');
        // keep icon, no text to update
        var topbarAvatar = document.querySelector('.dash-avatar');
        if (topbarAvatar) topbarAvatar.textContent = fullName.substring(0, 2).toUpperCase();
      }
      window._profDirty = false;
      if (typeof window._profRefreshSnapshot === 'function') window._profRefreshSnapshot();
      showToast('Profile saved successfully!', 'success');
    })
    .catch(function () {
      btn.disabled = false;
      if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
      errEl.textContent = 'Network error. Please try again.';
      errEl.classList.remove('d-none');
    });
});

/* ── Profile — Avatar upload ────────────────────────────────── */
document.getElementById('avatarFileInput') && document.getElementById('avatarFileInput').addEventListener('change', function () {
  var file = this.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('avatar', file);
  fd.append('csrf_token', csrfToken());
  fetch('/agent/profile/upload-avatar', { method: 'POST', body: fd })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      if (!res.ok) { showToast(res.data.error || 'Upload failed.', 'danger'); return; }
      var wrap = document.getElementById('profAvatarLg');
      if (!wrap) return;
      // Replace icon with image
      var existingImg  = document.getElementById('profAvatarImg');
      var existingIcon = document.getElementById('profAvatarIcon');
      if (existingIcon) existingIcon.remove();
      var freshUrl = res.data.url + '?t=' + Date.now();
      if (existingImg) {
        existingImg.src = freshUrl;
      } else {
        var img = document.createElement('img');
        img.id  = 'profAvatarImg';
        img.src = freshUrl;
        img.alt = 'Profile photo';
        // Insert before the upload label
        var label = wrap.querySelector('.prof-avatar-upload-btn');
        wrap.insertBefore(img, label);
      }
      // Show preview button
      var prevBtn = document.getElementById('avatarPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
      // Sync topbar avatar
      _syncTopbarAvatar(freshUrl);
      // Update preview modal image live (if modal is still open)
      var modalImg = document.getElementById('imgPreviewSrc');
      if (modalImg) modalImg.src = freshUrl;
      showToast('Profile photo updated!', 'success');
    })
    .catch(function () { showToast('Upload failed. Please try again.', 'danger'); });
});

/* ── Profile — Banner upload ────────────────────────────────── */
document.getElementById('bannerFileInput') && document.getElementById('bannerFileInput').addEventListener('change', function () {
  var file = this.files[0];
  if (!file) return;
  var fd = new FormData();
  fd.append('banner', file);
  fd.append('csrf_token', csrfToken());
  fetch('/agent/profile/upload-banner', { method: 'POST', body: fd })
    .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
    .then(function (res) {
      if (!res.ok) { showToast(res.data.error || 'Upload failed.', 'danger'); return; }
      var banner = document.getElementById('profHeroBanner');
      if (banner) {
        var freshBannerUrl = res.data.url + '?t=' + Date.now();
        banner.style.backgroundImage    = 'url(\'' + freshBannerUrl + '\')';
        banner.style.backgroundSize     = 'cover';
        banner.style.backgroundPosition = 'center';
        banner.dataset.bannerUrl        = freshBannerUrl;
      }
      // Show preview button
      var prevBtn = document.getElementById('bannerPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
      // Update preview modal image live (if modal is still open)
      var modalImg = document.getElementById('imgPreviewSrc');
      if (modalImg) modalImg.src = freshBannerUrl;
      showToast('Cover photo updated!', 'success');
    })
    .catch(function () { showToast('Upload failed. Please try again.', 'danger'); });
});

/* ── Profile — Image Preview Modal ──────────────────────────────────────── */
(function () {
  var _previewType = null; // 'avatar' or 'banner'
  var _previewRestoreModalId = null;

  function _applyDarkModalBackdrops() {
    var backdrops = document.querySelectorAll('.modal-backdrop');
    backdrops.forEach(function(el) { el.classList.add('sqh-dark-backdrop'); });
  }

  // ── Zoom/drag (delegated to shared controller in main.js) ──
  var agentZoomCtrl = new ZoomDragController({
    frame:   document.getElementById('imgPreviewImgFrame'),
    wrapper: document.getElementById('imgPreviewImgWrapper'),
    img:     document.getElementById('imgPreviewSrc'),
    zoomIn:  document.getElementById('imgPreviewZoomIn'),
    zoomOut: document.getElementById('imgPreviewZoomOut'),
    reset:   document.getElementById('imgPreviewZoomReset'),
  });

  function _setPreviewActionsVisible(visible) {
    var actionsEl = document.querySelector('#imgPreviewModal .img-preview-actions');
    var gradientEl = document.querySelector('#imgPreviewModal .img-preview-gradient');
    if (actionsEl) actionsEl.style.display = visible ? 'flex' : 'none';
    if (gradientEl) gradientEl.style.display = visible ? '' : 'none';
  }

  function openPreview(type, title, imgUrl) {
    _previewType = type;
    _previewRestoreModalId = null;
    var titleEl = document.getElementById('imgPreviewTitle');
    var imgEl   = document.getElementById('imgPreviewSrc');
    if (titleEl) titleEl.textContent = title;
    if (imgEl) {
      imgEl.src = sqhOptimizedSrc(imgUrl);
      SqhImageCache.swap(imgEl, imgUrl);
    }
    _setPreviewActionsVisible(true);
    agentZoomCtrl.resetZoom();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('imgPreviewModal')).show();
  }

  function openReadOnlyPreview(imgUrl, sourceModalId) {
    _previewType = null;
    var imgEl = document.getElementById('imgPreviewSrc');
    if (imgEl) {
      imgEl.src = sqhOptimizedSrc(imgUrl);
      SqhImageCache.swap(imgEl, imgUrl);
    }
    _setPreviewActionsVisible(false);
    agentZoomCtrl.resetZoom();
    var previewModalEl = document.getElementById('imgPreviewModal');
    var showPreview = function() {
      bootstrap.Modal.getOrCreateInstance(previewModalEl).show();
    };
    var sourceEl = sourceModalId ? document.getElementById(sourceModalId) : null;
    if (sourceEl && sourceEl.classList.contains('show')) {
      _previewRestoreModalId = sourceModalId;
      sourceEl.addEventListener('hidden.bs.modal', function onSourceHidden() {
        showPreview();
      }, { once: true });
      bootstrap.Modal.getOrCreateInstance(sourceEl).hide();
    } else {
      _previewRestoreModalId = null;
      showPreview();
    }
  }

  var previewModalEl = document.getElementById('imgPreviewModal');
  if (previewModalEl) {
    previewModalEl.addEventListener('show.bs.modal', function() {
      agentZoomCtrl.resetZoom();
    });
    previewModalEl.addEventListener('hidden.bs.modal', function() {
      agentZoomCtrl.resetZoom();
      if (!_previewRestoreModalId) return;
      var restoreEl = document.getElementById(_previewRestoreModalId);
      _previewRestoreModalId = null;
      if (restoreEl) bootstrap.Modal.getOrCreateInstance(restoreEl).show();
    });
  }

  (function initDarkBackdrops() {
    var darkBackdropModalIds = [
      'editPropertyModal',
      'imgPreviewModal'
    ];
    darkBackdropModalIds.forEach(function(modalId) {
      var modalEl = document.getElementById(modalId);
      if (!modalEl) return;
      modalEl.addEventListener('show.bs.modal', function() {
        setTimeout(_applyDarkModalBackdrops, 0);
      });
      modalEl.addEventListener('shown.bs.modal', function() {
        _applyDarkModalBackdrops();
      });
    });
  })();

  var propertyPreviewImg = document.getElementById('lemImg');
  if (propertyPreviewImg) {
    propertyPreviewImg.addEventListener('click', function(e) {
      var src = propertyPreviewImg.getAttribute('src') || '';
      if (!src) return;
      e.stopPropagation();
      openReadOnlyPreview(src, 'editPropertyModal');
    });
  }

  // Avatar preview button
  var avatarPrev = document.getElementById('avatarPreviewBtn');
  if (avatarPrev) {
    avatarPrev.addEventListener('click', function (e) {
      e.stopPropagation();
      var img = document.getElementById('profAvatarImg');
      if (img) openPreview('avatar', 'Profile Photo', img.src);
    });
  }

  // Banner preview button
  var bannerPrev = document.getElementById('bannerPreviewBtn');
  if (bannerPrev) {
    bannerPrev.addEventListener('click', function (e) {
      e.stopPropagation();
      var banner = document.getElementById('profHeroBanner');
      if (!banner) return;
      var url = sqhExtractBgUrl(banner);
      if (url) openPreview('banner', 'Cover Photo', url);
    });
  }

  // Delete button inside preview modal
  var replaceBtn = document.getElementById('imgPreviewReplaceBtn');
  if (replaceBtn) {
    replaceBtn.addEventListener('click', function () {
      if (!_previewType) return;
      var inputId = _previewType === 'avatar' ? 'avatarFileInput' : 'bannerFileInput';
      var el = document.getElementById(inputId);
      if (el) el.click();
    });
  }

  // Delete button inside preview modal
  var deleteBtn = document.getElementById('imgPreviewDeleteBtn');
  if (deleteBtn) {
    deleteBtn.addEventListener('click', function () {
      if (!_previewType) return;
      var url = _previewType === 'avatar' ? '/agent/profile/delete-avatar' : '/agent/profile/delete-banner';
      fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.success) { showToast(data.error || 'Delete failed.', 'danger'); return; }
          bootstrap.Modal.getInstance(document.getElementById('imgPreviewModal')).hide();
          if (_previewType === 'avatar') {
            // Remove img, restore icon
            var img  = document.getElementById('profAvatarImg');
            if (img) img.remove();
            var wrap = document.getElementById('profAvatarLg');
            if (wrap && !document.getElementById('profAvatarIcon')) {
              var icon = document.createElement('i');
              icon.className = 'fas fa-user';
              icon.id = 'profAvatarIcon';
              wrap.insertBefore(icon, wrap.firstChild);
            }
            var prevBtn = document.getElementById('avatarPreviewBtn');
            if (prevBtn) prevBtn.style.display = 'none';
            // Reset topbar avatar to initials
            _clearTopbarAvatar();
          } else {
            var banner = document.getElementById('profHeroBanner');
            if (banner) {
              banner.style.backgroundImage    = '';
              banner.style.backgroundSize     = '';
              banner.style.backgroundPosition = '';
              delete banner.dataset.bannerUrl;
            }
            var prevBtn = document.getElementById('bannerPreviewBtn');
            if (prevBtn) prevBtn.style.display = 'none';
          }
          showToast(_previewType === 'avatar' ? 'Profile photo removed.' : 'Cover photo removed.', 'success');
        })
        .catch(function () { showToast('Delete failed. Please try again.', 'danger'); });
    });
  }
}());

/* ── Profile — Topbar avatar sync helpers ─────────────────────────────── */
function _syncTopbarAvatar(url) {
  var ta = document.getElementById('topbarAvatar');
  if (!ta) return;
  var img = ta.querySelector('img');
  if (img) {
    img.src = url;
  } else {
    ta.textContent = '';
    var newImg = document.createElement('img');
    newImg.src = url;
    newImg.alt = '';
    ta.appendChild(newImg);
  }
}
function _clearTopbarAvatar() {
  var ta = document.getElementById('topbarAvatar');
  if (!ta) return;
  var nameEl = document.querySelector('.dash-topbar-name');
  var name   = nameEl ? nameEl.textContent.trim() : '';
  ta.innerHTML = name.substring(0, 2).toUpperCase();
}

/* ── Table Filters ──────────────────────────────────────────── */
(function () {
  var tripsSearch = document.getElementById('tripsSearch');
  var tripsFilter = document.getElementById('tripsFilterStatus');
  function filterTrips() {
    var q = tripsSearch ? tripsSearch.value.toLowerCase() : '';
    var s = tripsFilter ? tripsFilter.value : '';
    var visibleCount = 0;
    document.querySelectorAll('#tripsTable tbody tr[data-status]').forEach(function (row) {
      var text   = row.textContent.toLowerCase();
      var status = row.dataset.status || '';
      var show = (!q || text.includes(q)) && (!s || status === s);
      row.style.display = show ? '' : 'none';
      if (show) visibleCount++;
    });
    var noResults = document.querySelector('#page-trips #tripsNoResults');
    if (noResults) noResults.classList.toggle('d-none', visibleCount > 0);
  }
  if (tripsSearch) tripsSearch.addEventListener('input', filterTrips);
  if (tripsFilter) tripsFilter.addEventListener('change', filterTrips);

  var soldSearch = document.getElementById('soldSearch');
  var soldPriceFilter = document.getElementById('soldPriceFilter');
  function _soldPriceMatch(filterVal, price) {
    if (!filterVal) return true;
    if (filterVal === 'lt3000000') return price < 3000000;
    if (filterVal === '3000000-6000000') return price >= 3000000 && price <= 6000000;
    if (filterVal === 'gt6000000') return price > 6000000;
    return true;
  }
  function filterSold() {
    var q = soldSearch ? soldSearch.value.toLowerCase().trim() : '';
    var p = soldPriceFilter ? soldPriceFilter.value : '';
    var visibleCount = 0;
    document.querySelectorAll('#soldCardsGrid .sold-listing-card-col').forEach(function (col) {
      var prop = (col.dataset.propName || '').toLowerCase();
      var buyer = (col.dataset.buyerName || '').toLowerCase();
      var price = parseFloat(col.dataset.salePrice || 0);
      var textMatch = (!q || prop.includes(q) || buyer.includes(q));
      var priceMatch = _soldPriceMatch(p, price);
      var show = textMatch && priceMatch;
      col.style.display = show ? '' : 'none';
      if (show) visibleCount++;
    });
    var noResults = document.getElementById('soldNoResults');
    if (noResults) noResults.classList.toggle('d-none', visibleCount > 0);
  }
  if (soldSearch) soldSearch.addEventListener('input', filterSold);
  if (soldPriceFilter) soldPriceFilter.addEventListener('change', filterSold);

  var listingsSearch       = document.getElementById('listingsSearch');
  var listingsFilter       = document.getElementById('listingsFilterApproval');
  var listingsFilterSubdiv = document.getElementById('listingsFilterSubdivision');
  function filterListings() {
    var q      = listingsSearch       ? listingsSearch.value.trim().toLowerCase() : '';
    var s      = listingsFilter       ? listingsFilter.value                       : '';
    var subdiv = listingsFilterSubdiv ? listingsFilterSubdiv.value                 : '';
    var grid = document.getElementById('listingsCardsGrid');
    if (!grid) return;
    var visibleCount = 0;
    grid.querySelectorAll('.listing-card-col').forEach(function (col) {
      var name     = (col.getAttribute('data-prop-name')   || '').toLowerCase();
      var loc      = (col.getAttribute('data-prop-loc')    || '').toLowerCase();
      var listingStatus = (col.getAttribute('data-listing-status') || '').toLowerCase();
      var sub      = (col.getAttribute('data-prop-subdiv') || '');
      var textMatch   = !q      || name.includes(q) || loc.includes(q);
      var statusMatch = !s      || listingStatus === s;
      var subdivMatch = !subdiv || (subdiv === '__none__' ? sub === '' : sub === subdiv);
      var show = textMatch && statusMatch && subdivMatch;
      col.style.display = show ? '' : 'none';
      if (show) visibleCount++;
    });
    var emptyEl = document.getElementById('listingsFilterEmpty');
    if (emptyEl) emptyEl.classList.toggle('d-none', visibleCount > 0);
  }
  if (listingsSearch)       listingsSearch.addEventListener('input', filterListings);
  if (listingsFilter)       listingsFilter.addEventListener('change', filterListings);
  if (listingsFilterSubdiv) listingsFilterSubdiv.addEventListener('change', filterListings);

  // Card click → open edit modal (skip delete-btn clicks)
  document.addEventListener('click', function (e) {
    if (e.target.closest('.prop-delete-btn')) return;
    var btn = e.target.closest('.listing-view-btn');
    var card = btn ? btn.closest('.listing-card') : e.target.closest('.listing-card');
    if (card) {
      var propId = parseInt(card.dataset.propId, 10);
      if (propId) openEditPropertyModal(propId);
    }
  });


})();

/* ── Notification: mark as read (localStorage-backed) ─────── */
(function initAgentNotifReadState() {
  var body = document.getElementById('dashNotifBody');
  if (!body) return;
  var readAllBtn = document.getElementById('dashNotifReadAll');
  var layout = document.querySelector('.dashboard-layout');
  var userId = (layout && layout.dataset && layout.dataset.userId) || 'default';
  var lsKey = 'sqhAgentNotifRead:' + userId;
  var readSet = new Set();
  try {
    readSet = new Set(JSON.parse(localStorage.getItem(lsKey) || '[]'));
  } catch (_) {
    readSet = new Set();
  }

  function notifReadKey(item) {
    if (!item) return '';
    if (item.dataset.tripId) return 'trip-' + item.dataset.tripId;
    if (item.dataset.propNotifId) return 'prop-' + item.dataset.propNotifId;
    if (item.dataset.detailRequestId) {
      var stamp = item.dataset.detailUpdatedAt || '';
      return 'detail-' + item.dataset.detailRequestId + (stamp ? ('-' + stamp) : '');
    }
    return '';
  }
  function persistReadSet() {
    try {
      localStorage.setItem(lsKey, JSON.stringify(Array.from(readSet)));
    } catch (_) { /* silent */ }
  }

  function sortUnreadFirst() {
    var items = Array.prototype.slice.call(body.querySelectorAll('.dash-notif-item'));
    var labels = body.querySelectorAll('.dash-notif-section-label');
    labels.forEach(function(lbl) { lbl.remove(); });
    items.sort(function(a, b) {
      var ar = a.classList.contains('notif-read') ? 1 : 0;
      var br = b.classList.contains('notif-read') ? 1 : 0;
      return ar - br;
    });
    items.forEach(function(item) { body.appendChild(item); });
  }

  function updateAgentBadge() {
    var unread = body.querySelectorAll('.dash-notif-item:not(.notif-read)').length;
    var unreadTrips = body.querySelectorAll('.dash-notif-item[data-trip-id]:not(.notif-read)').length;
    var badgeEl     = document.getElementById('dashNotifBadge');
    var pillEl      = document.getElementById('dashNotifPill');
    var sideBadgeEl = document.getElementById('tripsSidebarBadge');
    if (badgeEl) { badgeEl.textContent = unread; unread === 0 ? badgeEl.classList.add('d-none') : badgeEl.classList.remove('d-none'); }
    if (pillEl)  { pillEl.textContent = unread + ' unread'; if (unread === 0) pillEl.style.display = 'none'; }
    if (sideBadgeEl) { sideBadgeEl.textContent = unreadTrips; sideBadgeEl.classList.toggle('d-none', unreadTrips === 0); }
    if (readAllBtn) readAllBtn.style.display = unread === 0 ? 'none' : '';
  }
  function markAgentItemRead(item, persist) {
    if (persist === undefined) persist = true;
    if (!item || item.classList.contains('notif-read')) return;
    item.classList.add('notif-read');
    if (persist) {
      var key = notifReadKey(item);
      if (key) {
        readSet.add(key);
        persistReadSet();
      }
    }
    var btn = item.querySelector('.dash-notif-read-btn');
    if (btn && btn.parentNode) {
      var badge = document.createElement('span');
      badge.className = 'dash-notif-read-badge';
      badge.innerHTML = '<i class="fas fa-check-double"></i>';
      btn.parentNode.replaceChild(badge, btn);
    }
  }

  function moveReadItemToReadBoundary(item) {
    if (!item || !item.parentNode) return;
    var lastUnread = null;
    body.querySelectorAll('.dash-notif-item').forEach(function (row) {
      if (row !== item && !row.classList.contains('notif-read')) lastUnread = row;
    });
    if (lastUnread) {
      if (lastUnread.nextSibling !== item) body.insertBefore(item, lastUnread.nextSibling);
      return;
    }
    var firstRow = body.querySelector('.dash-notif-item');
    if (firstRow && firstRow !== item) body.insertBefore(item, firstRow);
  }

  body.querySelectorAll('.dash-notif-item').forEach(function(item) {
    var key = notifReadKey(item);
    if (key && readSet.has(key)) markAgentItemRead(item, false);
  });
  sortUnreadFirst();
  updateAgentBadge();

  // Handle read button clicks via delegation
  body.addEventListener('click', function (e) {
    var readBtn = e.target.closest('.dash-notif-read-btn');
    if (!readBtn) return;
    e.stopImmediatePropagation();
    var item = readBtn.closest('.dash-notif-item');
    if (!item) return;
    var tripId = item.dataset.tripId || '';
    var propNotifId = item.dataset.propNotifId || '';
    var detailRequestId = item.dataset.detailRequestId || '';
    if (!tripId && !propNotifId && !detailRequestId) return;
    markAgentItemRead(item);
    moveReadItemToReadBoundary(item);
    updateAgentBadge();
    if (tripId) {
      fetch('/agent/notif/' + tripId + '/read', { method: 'POST', headers: { 'X-CSRFToken': csrfToken() } })
        .catch(function () { /* silent */ });
      return;
    }
    if (detailRequestId) return;
    fetch('/agent/property-notif/' + propNotifId + '/read', { method: 'POST', headers: { 'X-CSRFToken': csrfToken() } })
      .catch(function () { /* silent */ });
  });

  if (readAllBtn) {
    readAllBtn.addEventListener('click', function(e) {
      e.preventDefault();
      body.querySelectorAll('.dash-notif-item:not(.notif-read)').forEach(function(item) {
        markAgentItemRead(item);
      });
      sortUnreadFirst();
      updateAgentBadge();
      fetch('/agent/notif/read-all', { method: 'POST', headers: { 'X-CSRFToken': csrfToken() } })
        .catch(function () { /* silent */ });
    });
  }
})();

document.addEventListener('click', function (e) {
  var btn = e.target.closest('.open-trip-request-btn');
  if (!btn) return;
  e.preventDefault();
  var tripId = btn.dataset.tripId;
  var modalEl = document.getElementById('tripRequestModal');
  if (modalEl) modalEl.dataset.tripId = tripId || '';
  var row = btn.closest('tr');
  if (!row && tripId) row = document.querySelector('[data-trip-row="' + tripId + '"]');
  if (!row) return;
  var byId = function (id) { return document.getElementById(id); };
  var statusRaw = (row.dataset.status || '').toLowerCase();
  if (modalEl) modalEl.dataset.tripStatus = statusRaw;
  if (byId('trmClient')) byId('trmClient').textContent = row.dataset.clientName || '—';
  if (byId('trmProperty')) byId('trmProperty').textContent = row.dataset.propertyName || '—';
  if (byId('trmDate')) byId('trmDate').textContent = row.dataset.preferredDate || '—';
  if (byId('trmTime')) byId('trmTime').textContent = row.dataset.preferredTime || '—';
  if (byId('trmSubmitted')) byId('trmSubmitted').textContent = row.dataset.submittedDate || '—';
  _setTripModalStatus(statusRaw);
  if (byId('trmNote')) byId('trmNote').textContent = row.dataset.agentNote || 'No note provided.';

  _trmImages = (row.dataset.propertyImages || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  _trmIndex = 0;
  _trmRenderImage();
  _trmRenderDots();
  _trmUpdateNav();

  var approveBtn = byId('trmApproveBtn');
  var rejectBtn = byId('trmRejectBtn');
  var markBoughtBtn = byId('trmMarkBoughtBtn');
  var deleteBtn = byId('trmDeleteBtn');
  var isPending = statusRaw === 'pending';
  var isApproved = statusRaw === 'approved';
  var isRejected = statusRaw === 'rejected';
  var isBought = statusRaw === 'sold';
  if (approveBtn) approveBtn.classList.toggle('d-none', !isPending);
  if (rejectBtn) rejectBtn.classList.toggle('d-none', !isPending);
  if (markBoughtBtn) markBoughtBtn.classList.toggle('d-none', !isApproved);
  if (deleteBtn) deleteBtn.classList.toggle('d-none', !(isRejected || isBought));

  bootstrap.Modal.getOrCreateInstance(document.getElementById('tripRequestModal')).show();
});

document.getElementById('topbarAvatar') && document.getElementById('topbarAvatar').addEventListener('click', function () {
  if (typeof showPage === 'function') showPage('profile');
});

/* -- Data-attribute-based event delegation for client modal buttons -- */
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.open-client-modal-btn');
  if (btn) { openClientModal(parseInt(btn.dataset.clientId)); }
});
/* ── Profile — Unsaved Changes Guard ───────────────────────────────────────── */
(function () {
  window._profDirty = false;
  var _pendingNav   = null;

  // Field IDs that are saved/restored (password fields always reset to blank)
  var PROF_TEXT_FIELDS = ['prof_first_name', 'prof_last_name', 'prof_username', 'prof_contact', 'prof_license', 'prof_bio'];
  var PROF_PW_FIELDS   = ['prof_old_password', 'prof_new_password', 'prof_confirm_password'];

  // Snapshot saved values on load so we can restore on "Leave"
  var _profSnapshot = {};
  PROF_TEXT_FIELDS.forEach(function (id) {
    var el = document.getElementById(id);
    _profSnapshot[id] = el ? el.value : '';
  });

  // After a successful save, refresh the snapshot so "Leave" resets to the new saved state
  window._profRefreshSnapshot = function () {
    PROF_TEXT_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) _profSnapshot[id] = el.value;
    });
  };

  function _restoreProfileForm() {
    // Restore text fields to last-saved values
    PROF_TEXT_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) {
        el.value = _profSnapshot[id] || '';
        el.classList.remove('lv-valid', 'lv-invalid');
      }
    });
    // Always blank out password fields and reset their validation UI
    PROF_PW_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) {
        el.value = '';
        el.classList.remove('lv-valid', 'lv-invalid');
      }
    });
    // Clear password error messages
    ['prof_old_password_error', 'prof_new_password_error', 'prof_confirm_password_error'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) { el.textContent = ''; el.classList.remove('sqh-err-visible'); }
    });
    // Reset pw-checklist to unchecked state
    ['profPwLen', 'profPwUpper', 'profPwNum', 'profPwSpecial'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.classList.remove('pw-ok');
      var icon = el.querySelector('i');
      if (icon) icon.className = 'fas fa-circle-dot';
    });
  }

  // Mark dirty when any profile text/select/textarea changes
  var profPage = document.getElementById('page-profile');
  if (profPage) {
    profPage.querySelectorAll('input:not([type=file]), textarea, select').forEach(function (el) {
      el.addEventListener('input',  function () { window._profDirty = true; });
      el.addEventListener('change', function () { window._profDirty = true; });
    });
  }

  // Also mark dirty when a photo is uploaded
  ['avatarFileInput', 'bannerFileInput'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', function () { window._profDirty = true; });
  });

  // Hook called by admin_dashboard.js before switching pages
  window._navGuard = function (targetPage) {
    var activePage = document.querySelector('.dash-page:not(.d-none)');
    if (!activePage || activePage.id !== 'page-profile') return; // not on profile page
    if (!window._profDirty) return;                               // nothing changed
    _pendingNav = targetPage;
    var modalEl = document.getElementById('unsavedChangesModal');
    if (!modalEl) return;
    bootstrap.Modal.getOrCreateInstance(modalEl).show();
    return false; // block navigation
  };

  // "Leave anyway" button inside the modal
  var leaveBtn = document.getElementById('unsavedLeaveBtn');
  if (leaveBtn) {
    leaveBtn.addEventListener('click', function () {
      _restoreProfileForm();
      window._profDirty = false;
      var modalEl = document.getElementById('unsavedChangesModal');
      var modal   = bootstrap.Modal.getInstance(modalEl);
      if (modal) modal.hide();
      if (_pendingNav && typeof showPage === 'function') {
        showPage(_pendingNav);
        _pendingNav = null;
      }
    });
  }
}());

(function () {
  var calendarGrid = document.getElementById('availCalendarGrid');
  var monthLabel = document.getElementById('availMonthLabel');
  var prevBtn = document.getElementById('availPrevMonthBtn');
  var nextBtn = document.getElementById('availNextMonthBtn');
  var selectedDateLabel = document.getElementById('availSelectedDateLabel');
  var selectionMeta = document.getElementById('availSelectionMeta');
  var clearSelectionBtn = document.getElementById('availClearSelectionBtn');
  var statusInput = document.getElementById('availStatus');
  var startWrap = document.getElementById('availStartWrap');
  var endWrap = document.getElementById('availEndWrap');
  var addBtn = document.getElementById('availAddBtn');
  var startInput = document.getElementById('availStart');
  var endInput = document.getElementById('availEnd');
  var startErrorEl = document.getElementById('availStartError');
  var endErrorEl = document.getElementById('availEndError');
  var noteInput = document.getElementById('availNote');
  var noteLabel = document.getElementById('availNoteLabel');
  var entriesList = document.getElementById('availEntriesList');
  var entryCount = document.getElementById('availDateEntryCount');
  var errorEl = document.getElementById('availError');
  var confirmModalEl = document.getElementById('availConfirmModal');
  var confirmBtn = document.getElementById('availConfirmBtn');
  var confirmTitle = document.getElementById('availConfirmTitle');
  var confirmLabel = document.getElementById('availConfirmLabel');
  var confirmDesc = document.getElementById('availConfirmDesc');

  if (!calendarGrid || !monthLabel || !prevBtn || !nextBtn || !addBtn || !startInput || !endInput || !noteInput || !statusInput) return;

  var now = new Date();
  var state = {
    year: now.getFullYear(),
    month: now.getMonth(),
    selectedDate: toIsoDate(now),
    selectedDates: new Set([toIsoDate(now)]),
    multiSelectEnabled: false,
    entries: [],
    pendingAction: null,
  };

  function getCsrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    if (m && m.getAttribute('content')) return m.getAttribute('content');
    var i = document.querySelector('input[name="csrf_token"]');
    return i ? i.value : '';
  }

  function showError(message) {
    if (!errorEl) return;
    if (!message) {
      errorEl.classList.add('d-none');
      errorEl.textContent = '';
      return;
    }
    errorEl.textContent = message;
    errorEl.classList.remove('d-none');
  }

  function showInlineError(el, message) {
    if (!el) return;
    if (message) {
      el.innerHTML = '<i class="fas fa-exclamation-circle"></i> ' + message;
      el.classList.add('sqh-err-visible');
      return;
    }
    el.innerHTML = '';
    el.classList.remove('sqh-err-visible');
  }

  function clearTimeErrors() {
    showInlineError(startErrorEl, '');
    showInlineError(endErrorEl, '');
  }

  function validateTimeFields() {
    clearTimeErrors();
    if ((statusInput.value || 'available') !== 'available') return true;

    var startValue = (startInput.value || '').trim();
    var endValue = (endInput.value || '').trim();
    var ok = true;

    if (!startValue) {
      showInlineError(startErrorEl, 'Start time is required.');
      ok = false;
    }
    if (!endValue) {
      showInlineError(endErrorEl, 'End time is required.');
      ok = false;
    }

    if (startValue && endValue && startValue >= endValue) {
      showInlineError(endErrorEl, 'End time must be later than start time.');
      ok = false;
    }

    return ok;
  }

  function toIsoDate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function formatDate(isoDate) {
    var d = new Date(isoDate + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return isoDate;
    return d.toLocaleDateString('en-PH', { month: 'short', day: '2-digit', year: 'numeric' });
  }

  function formatLongDate(isoDate) {
    var d = new Date(isoDate + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return isoDate;
    return d.toLocaleDateString('en-PH', { weekday: 'long', month: 'long', day: '2-digit', year: 'numeric' });
  }

  function formatTime(hhmm) {
    var parts = String(hhmm || '').split(':');
    if (parts.length < 2) return hhmm || '';
    var h = parseInt(parts[0], 10);
    var m = parts[1];
    if (Number.isNaN(h)) return hhmm || '';
    var ampm = h >= 12 ? 'PM' : 'AM';
    var hour12 = h % 12 || 12;
    return hour12 + ':' + m + ' ' + ampm;
  }

  function statusLabel(v) {
    return v === 'not_available' ? 'Not Available' : 'Available';
  }

  function esc(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function groupedByDate() {
    var grouped = {};
    state.entries.forEach(function (item) {
      if (!grouped[item.available_date]) grouped[item.available_date] = [];
      grouped[item.available_date].push(item);
    });
    return grouped;
  }

  function getDateStatus(isoDate) {
    var grouped = groupedByDate();
    var rows = grouped[isoDate] || [];
    if (!rows.length) return 'none';
    if (rows.some(function (r) { return (r.availability_status || 'available') === 'not_available'; })) {
      return 'not_available';
    }
    return 'available';
  }

  function renderSelectedDate() {
    var todayIso = toIsoDate(new Date());
    var selectedDates = getSelectedDates();
    if (!selectedDates.length) {
      state.selectedDate = todayIso;
      state.selectedDates = new Set([state.selectedDate]);
      selectedDates = [state.selectedDate];
    }
    if (!state.selectedDates.has(state.selectedDate) || state.selectedDate < todayIso) {
      state.selectedDate = selectedDates[0] || todayIso;
    }

    if (selectedDates.length === 1) {
      selectedDateLabel.textContent = formatLongDate(state.selectedDate);
    } else {
      selectedDateLabel.textContent = selectedDates.length + ' dates selected';
    }

    if (selectionMeta) {
      if (!state.multiSelectEnabled) {
        selectionMeta.textContent = '1 date selected (single-select mode)';
      } else {
        selectionMeta.textContent = selectedDates.length === 1
          ? '1 date selected (multi-select mode)'
          : (selectedDates.length + ' dates selected (previewing ' + formatDate(state.selectedDate) + ')');
      }
    }

    if (addBtn) {
      addBtn.innerHTML = selectedDates.length === 1
        ? '<i class="fas fa-plus me-1"></i>Add Entry for Selected Date'
        : '<i class="fas fa-layer-group me-1"></i>Mass Update ' + selectedDates.length + ' Dates';
      addBtn.disabled = selectedDates.length === 0 || selectedDates.some(function (d) { return d < todayIso; });
    }

    if (clearSelectionBtn) {
      if (state.multiSelectEnabled) {
        clearSelectionBtn.className = 'btn btn-sm btn-outline-crimson mt-2';
        clearSelectionBtn.innerHTML = '<i class="fas fa-eraser me-1"></i>Reset to focused date';
      } else {
        clearSelectionBtn.className = 'btn btn-sm btn-outline-blue mt-2';
        clearSelectionBtn.innerHTML = '<i class="fas fa-layer-group me-1"></i>Multi Select';
      }
    }

    renderEntriesForDate(state.selectedDate);
    renderCalendar();
  }

  function getSelectedDates() {
    var todayIso = toIsoDate(new Date());
    return Array.from(state.selectedDates || [])
      .filter(function (d) { return d >= todayIso; })
      .sort();
  }

  function renderEntriesForDate(isoDate) {
    var grouped = groupedByDate();
    var rows = (grouped[isoDate] || []).slice().sort(function (a, b) {
      return String(a.start_time || '').localeCompare(String(b.start_time || ''));
    });

    if (entryCount) {
      entryCount.textContent = rows.length + ' entr' + (rows.length === 1 ? 'y' : 'ies');
    }

    if (!entriesList) return;
    if (!rows.length) {
      entriesList.innerHTML = '<div class="text-muted small">No entries for this date.</div>';
      return;
    }

    entriesList.innerHTML = rows.map(function (row) {
      var status = (row.availability_status || 'available');
      var badgeClass = status === 'not_available' ? 'badge-not-qualified' : 'badge-qualified';
      var timeHtml = status === 'not_available'
        ? '<div class="small text-muted fw-bold">Full day blocked</div>'
        : '<div class="small text-muted fw-bold">' + formatTime(row.start_time) + ' - ' + formatTime(row.end_time) + '</div>';
      var noteHtml = ''
        + '<div class="cp-trip-note cp-trip-note-home mt-2">'
        + '  <div class="cp-trip-note-header d-flex align-items-center justify-content-between">'
        + '    <span class="cp-trip-note-label"><i class="fas fa-comment-dots me-1"></i>Agent Note</span>'
        + '    <button type="button" class="btn btn-sm btn-outline-crimson avail-delete-btn" data-slot-id="' + row.id + '" title="Delete entry"><i class="fas fa-trash"></i></button>'
        + '  </div>'
        + '  <span class="cp-trip-note-text">' + esc(row.notes || 'None') + '</span>'
        + '</div>';
      return ''
        + '<div class="avail-entry-card" data-slot-id="' + row.id + '">'
        + '  <div class="d-flex flex-column">'
        + '    <div class="d-flex align-items-center justify-content-between gap-2">'
        + '      <span class="sqh-badge ' + badgeClass + '">' + statusLabel(status) + '</span>'
        +        timeHtml
        + '    </div>'
        +      noteHtml
        + '  </div>'
        + '</div>';
    }).join('');
  }

  function renderCalendar() {
    var first = new Date(state.year, state.month, 1);
    var startDay = first.getDay();
    var daysInMonth = new Date(state.year, state.month + 1, 0).getDate();
    var prevDays = new Date(state.year, state.month, 0).getDate();
    var monthDate = new Date(state.year, state.month, 1);

    monthLabel.textContent = monthDate.toLocaleDateString('en-PH', { month: 'long', year: 'numeric' });
    calendarGrid.innerHTML = '';

    for (var i = startDay - 1; i >= 0; i--) {
      var dayNumPrev = prevDays - i;
      var cellPrev = document.createElement('button');
      cellPrev.type = 'button';
      cellPrev.className = 'avail-day-cell is-muted';
      cellPrev.disabled = true;
      cellPrev.innerHTML = '<span class="avail-day-number">' + dayNumPrev + '</span>';
      calendarGrid.appendChild(cellPrev);
    }

    var todayIso = toIsoDate(new Date());
    for (var day = 1; day <= daysInMonth; day++) {
      var d = new Date(state.year, state.month, day);
      var iso = toIsoDate(d);
      var isPast = iso < todayIso;
      var status = getDateStatus(iso);
      var cls = 'avail-day-cell';
      if (status === 'none') cls += ' has-none';
      if (state.selectedDates.has(iso) && !isPast) cls += ' is-selected';
      if (iso === todayIso) cls += ' is-today';
      if (isPast) cls += ' is-past';
      if (status === 'available') cls += ' has-available';
      if (status === 'not_available') cls += ' has-not-available';

      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = cls;
      btn.setAttribute('data-date', iso);
      if (isPast) {
        btn.setAttribute('data-past', 'true');
        btn.setAttribute('aria-disabled', 'true');
        btn.setAttribute('title', 'Past date (locked - cannot be selected)');
      }
      btn.innerHTML = ''
        + '<span class="avail-day-number">' + day + '</span>'
        + '<span class="avail-day-dot"></span>';
      calendarGrid.appendChild(btn);
    }

    var totalCells = startDay + daysInMonth;
    var trailing = (7 - (totalCells % 7)) % 7;
    for (var t = 1; t <= trailing; t++) {
      var cellNext = document.createElement('button');
      cellNext.type = 'button';
      cellNext.className = 'avail-day-cell is-muted';
      cellNext.disabled = true;
      cellNext.innerHTML = '<span class="avail-day-number">' + t + '</span>';
      calendarGrid.appendChild(cellNext);
    }
  }

  function updateFormMode() {
    var status = statusInput.value;
    var isAvailable = status === 'available';
    if (startWrap) startWrap.classList.toggle('d-none', !isAvailable);
    if (endWrap) endWrap.classList.toggle('d-none', !isAvailable);
    if (noteLabel) noteLabel.textContent = isAvailable ? 'Note (optional)' : 'Reason for Not Availability';
    noteInput.placeholder = isAvailable ? 'Add context for this available time' : 'State why you are not available on this date';
    clearTimeErrors();
  }

  function openConfirmModal(mode, payload) {
    if (!confirmModalEl || !confirmBtn) return;
    state.pendingAction = { mode: mode, payload: payload || {} };
    if (mode === 'add') {
      var dateCount = (payload && payload.available_dates && payload.available_dates.length) || 1;
      if (confirmTitle) confirmTitle.textContent = 'Confirm Availability';
      if (confirmLabel) {
        confirmLabel.textContent = dateCount === 1
          ? 'Add this availability entry?'
          : ('Mass update ' + dateCount + ' selected dates?');
      }
      if (confirmDesc) {
        confirmDesc.textContent = dateCount === 1
          ? 'This will save the selected date availability details.'
          : 'This will apply the same availability details to all selected dates.';
      }
      confirmBtn.className = 'btn btn-lime px-4';
      confirmBtn.innerHTML = dateCount === 1
        ? '<i class="fas fa-plus me-1"></i> Add Entry'
        : '<i class="fas fa-layer-group me-1"></i> Mass Update';
    } else {
      if (confirmTitle) confirmTitle.textContent = 'Delete Availability Entry';
      if (confirmLabel) confirmLabel.textContent = 'Delete this entry?';
      if (confirmDesc) confirmDesc.textContent = 'This action cannot be undone.';
      confirmBtn.className = 'btn btn-crimson px-4';
      confirmBtn.innerHTML = '<i class="fas fa-trash me-1"></i> Delete';
    }
    bootstrap.Modal.getOrCreateInstance(confirmModalEl).show();
  }

  function executeAdd(payload) {
    addBtn.disabled = true;
    confirmBtn && (confirmBtn.disabled = true);
    fetch('/agent/availability', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data || !res.data.ok) {
          showError((res.data && res.data.error) || 'Unable to add availability slot.');
          return;
        }
        noteInput.value = '';
        var createdCount = parseInt((res.data && res.data.created_count) || 0, 10);
        var skippedCount = (res.data && Array.isArray(res.data.skipped)) ? res.data.skipped.length : 0;
        if (createdCount > 1) {
          showToast('Availability updated for ' + createdCount + ' dates.', 'success');
        } else {
          showToast('Availability entry added.', 'success');
        }
        if (skippedCount > 0) {
          showToast(skippedCount + ' date(s) were skipped due to conflicts.', 'warning');
        }
        bootstrap.Modal.getInstance(confirmModalEl)?.hide();
        loadAvailability();
      })
      .catch(function () {
        showError('Network error. Please try again.');
      })
      .finally(function () {
        addBtn.disabled = false;
        confirmBtn && (confirmBtn.disabled = false);
      });
  }

  function executeDelete(slotId) {
    confirmBtn && (confirmBtn.disabled = true);
    fetch('/agent/availability/' + slotId + '/delete', {
      method: 'POST',
      headers: { 'X-CSRFToken': getCsrfToken() }
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data || !res.data.ok) {
          showError((res.data && res.data.error) || 'Unable to delete availability slot.');
          return;
        }
        state.entries = state.entries.filter(function (item) { return String(item.id) !== String(slotId); });
        renderSelectedDate();
        showToast('Availability entry deleted.', 'info');
        bootstrap.Modal.getInstance(confirmModalEl)?.hide();
      })
      .catch(function () {
        showError('Network error. Please try again.');
      })
      .finally(function () {
        confirmBtn && (confirmBtn.disabled = false);
      });
  }

  function loadAvailability() {
    fetch('/agent/availability', {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data || !res.data.ok) return;
        state.entries = (res.data.items || []);
        renderSelectedDate();
      })
      .catch(function () {});
  }

  prevBtn.addEventListener('click', function () {
    state.month -= 1;
    if (state.month < 0) {
      state.month = 11;
      state.year -= 1;
    }
    renderCalendar();
  });

  nextBtn.addEventListener('click', function () {
    state.month += 1;
    if (state.month > 11) {
      state.month = 0;
      state.year += 1;
    }
    renderCalendar();
  });

  calendarGrid.addEventListener('click', function (e) {
    var cell = e.target.closest('.avail-day-cell[data-date]');
    if (!cell) return;
    var isoDate = cell.getAttribute('data-date');
    if (!isoDate) return;

    var todayIso = toIsoDate(new Date());
    if (isoDate < todayIso) {
      showToast('Past dates are locked and cannot be selected.', 'warning');
      return;
    }

    if (!state.multiSelectEnabled) {
      state.selectedDate = isoDate;
      state.selectedDates = new Set([isoDate]);
      showError('');
      renderSelectedDate();
      return;
    }

    state.selectedDate = isoDate;
    if (state.selectedDates.has(isoDate)) {
      if (state.selectedDates.size > 1) {
        state.selectedDates.delete(isoDate);
        if (!state.selectedDates.has(state.selectedDate)) {
          state.selectedDate = getSelectedDates()[0] || state.selectedDate;
        }
      }
    } else {
      state.selectedDates.add(isoDate);
    }
    showError('');
    renderSelectedDate();
  });

  clearSelectionBtn && clearSelectionBtn.addEventListener('click', function () {
    var todayIso = toIsoDate(new Date());
    var baseDate = state.selectedDate >= todayIso ? state.selectedDate : todayIso;
    if (!state.multiSelectEnabled) {
      state.multiSelectEnabled = true;
      state.selectedDate = baseDate;
      state.selectedDates = new Set([baseDate]);
      showToast('Multi-select enabled.', 'info');
      renderSelectedDate();
      return;
    }

    state.multiSelectEnabled = false;
    state.selectedDate = baseDate;
    state.selectedDates = new Set([baseDate]);
    showToast('Selection reset to focused date.', 'info');
    renderSelectedDate();
  });

  statusInput.addEventListener('change', function () {
    updateFormMode();
  });

  startInput.addEventListener('input', validateTimeFields);
  endInput.addEventListener('input', validateTimeFields);
  startInput.addEventListener('change', validateTimeFields);
  endInput.addEventListener('change', validateTimeFields);

  addBtn.addEventListener('click', function () {
    showError('');
    var todayIso = toIsoDate(new Date());
    var selectedDates = getSelectedDates();
    var status = (statusInput.value || 'available').trim();
    var payload = {
      available_dates: selectedDates,
      availability_status: status,
      start_time: (startInput.value || '').trim(),
      end_time: (endInput.value || '').trim(),
      notes: (noteInput.value || '').trim(),
    };

    if (!selectedDates.length || selectedDates.some(function (d) { return d < todayIso; })) {
      showError('Please select at least one future or current date from the calendar.');
      return;
    }

    if (status === 'available' && !validateTimeFields()) {
      return;
    }

    if (status === 'not_available' && !payload.notes) {
      showError('Please provide a reason for not availability.');
      return;
    }

    openConfirmModal('add', payload);
  });

  entriesList && entriesList.addEventListener('click', function (e) {
    var btn = e.target.closest('.avail-delete-btn');
    if (!btn) return;
    e.preventDefault();
    var slotId = btn.getAttribute('data-slot-id');
    if (!slotId) return;
    openConfirmModal('delete', { slotId: slotId });
  });

  confirmBtn && confirmBtn.addEventListener('click', function () {
    if (!state.pendingAction || !state.pendingAction.mode) return;
    if (state.pendingAction.mode === 'add') {
      executeAdd(state.pendingAction.payload || {});
      return;
    }
    if (state.pendingAction.mode === 'delete') {
      executeDelete(state.pendingAction.payload && state.pendingAction.payload.slotId);
    }
  });

  confirmModalEl && confirmModalEl.addEventListener('hidden.bs.modal', function () {
    state.pendingAction = null;
    if (confirmBtn) confirmBtn.disabled = false;
  });

  updateFormMode();
  renderSelectedDate();
  loadAvailability();
}());