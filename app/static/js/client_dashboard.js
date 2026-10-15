/* ============================================================
   CLIENT DASHBOARD — client_dashboard.js
   Depends on admin_dashboard.js (showPage, showToast)
   ============================================================ */

(function () {
  "use strict";

  /* ── Helpers ─────────────────────────────────────────────── */
  function toast(msg, type) {
    if (typeof showToast === "function") showToast(msg, type);
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
  }

  function csrfHeaders() {
    return {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    };
  }

  function csrfFormData(fd) {
    // Attach CSRF token to a FormData object for multipart uploads
    var token = csrfToken();
    if (token) fd.append('csrf_token', token);
    return fd;
  }

  function psgcLog(level, message, meta) {
    if (!window || !window.console) return;
    var payload = meta || {};
    if (level === 'error') {
      console.error('[PSGC]', message, payload);
      return;
    }
    if (level === 'warn') {
      console.warn('[PSGC]', message, payload);
      return;
    }
    console.info('[PSGC]', message, payload);
  }

  function _psgcResetSelect(selectEl, placeholder) {
    if (!selectEl) return;
    selectEl.innerHTML = '';
    var opt = document.createElement('option');
    opt.value = '';
    opt.textContent = placeholder;
    selectEl.appendChild(opt);
  }

  function _psgcFillSelect(selectEl, items, placeholder, selectedCode) {
    if (!selectEl) return;
    _psgcResetSelect(selectEl, placeholder);
    (items || []).forEach(function (item) {
      var opt = document.createElement('option');
      opt.value = item.code || '';
      opt.textContent = item.name || '';
      selectEl.appendChild(opt);
    });
    if (selectedCode) selectEl.value = selectedCode;
  }

  async function _psgcGet(path) {
    psgcLog('info', 'Fetching PSGC data', { path: path });
    var res = await fetch(path, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    var data = null;
    try {
      data = await res.json();
    } catch (err) {
      psgcLog('warn', 'Proxy response is not valid JSON; attempting direct PSGC fallback', {
        path: path,
        status: res.status,
        statusText: res.statusText,
        error: String(err || ''),
      });
      data = null;
    }
    if (res.ok && data && data.ok) {
      var proxyItems = Array.isArray(data.items) ? data.items : [];
      psgcLog('info', 'Proxy PSGC response received', { path: path, count: proxyItems.length });
      if (proxyItems.length > 0 || path.indexOf('/api/psgc/regions') !== 0) {
        return proxyItems;
      }
      psgcLog('warn', 'Proxy returned empty regions; attempting direct PSGC fallback', { path: path });
    }

    var apiBase = 'https://psgc.gitlab.io/api';
    var mReg = path.match(/\/api\/psgc\/provinces\?region_code=([^&]+)/);
    var mCity = path.match(/\/api\/psgc\/cities\?(?:province_code=([^&]+)|region_code=([^&]+))/);
    var mBrgy = path.match(/\/api\/psgc\/barangays\?city_mun_code=([^&]+)/);
    var directUrl = '';
    if (path.indexOf('/api/psgc/regions') === 0) directUrl = apiBase + '/regions/';
    else if (mReg) {
      var rCode = decodeURIComponent(mReg[1]);
      if (rCode === '130000000' || rCode.indexOf('13') === 0) {
        return [{ code: '130000000', name: 'Metro Manila' }];
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
    if (!directUrl) {
      psgcLog('error', 'Unable to derive direct PSGC URL', { path: path, proxyError: (data && data.error) || null });
      throw new Error((data && data.error) || 'Failed to load PSGC data');
    }
    psgcLog('info', 'Requesting direct PSGC fallback', { directUrl: directUrl });
    var directRes = await fetch(directUrl);
    if (!directRes.ok) {
      psgcLog('error', 'Direct PSGC fallback failed', {
        directUrl: directUrl,
        status: directRes.status,
        statusText: directRes.statusText,
      });
      throw new Error('Failed to load PSGC data');
    }
    var directData = await directRes.json();
    var mapped = Array.isArray(directData)
      ? directData.map(function (it) {
          return { code: String(it.code || ''), name: String(it.name || '') };
        }).filter(function (it) { return it.code && it.name; })
      : [];
    psgcLog('info', 'Direct PSGC fallback response received', { path: path, count: mapped.length });
    return mapped;
  }

  function _psgcComposeLocation(names) {
    var ordered = [names.barangay, names.citymun, names.province, names.region].filter(Boolean);
    return ordered.join(', ');
  }

  var _qualThresholdCache = null;

  function _getQualificationThresholds() {
    if (_qualThresholdCache) return _qualThresholdCache;
    var defaults = { qualifiedMax: 30, conditionalMax: 42 };
    var cfgEl = document.getElementById('qm-live-config');
    if (!cfgEl || !cfgEl.textContent) {
      _qualThresholdCache = defaults;
      return defaults;
    }
    try {
      var cfg = JSON.parse(cfgEl.textContent);
      var qMax = Number(cfg && cfg.dti_qualified_max);
      var cMax = Number(cfg && cfg.dti_conditional_max);
      if (!(qMax > 0)) qMax = defaults.qualifiedMax;
      if (!(cMax > qMax)) cMax = defaults.conditionalMax;
      _qualThresholdCache = { qualifiedMax: qMax, conditionalMax: cMax };
      return _qualThresholdCache;
    } catch (_) {
      _qualThresholdCache = defaults;
      return defaults;
    }
  }

  function _toNumber(value) {
    if (typeof value === 'number') return isFinite(value) ? value : 0;
    var s = String(value == null ? '' : value).trim();
    if (!s) return 0;
    s = s.replace(/,/g, '').replace(/[^\d.-]/g, '');
    var n = Number(s);
    return isFinite(n) ? n : 0;
  }

  function _getClientMonthlyIncome() {
    var inputEl = document.getElementById('pbGrossIncome');
    if (inputEl && String(inputEl.value || '').trim()) {
      return _toNumber(inputEl.value);
    }
    var buyerModal = document.getElementById('buyerInfoModal');
    return _toNumber((buyerModal && buyerModal.dataset.profileGrossIncome) || 0);
  }

  function _computeQualificationStatus(requiredIncome, clientIncome, fallbackQualified) {
    var req = _toNumber(requiredIncome || 0);
    var income = _toNumber(clientIncome || 0);

    if (req > 0 && income > 0) {
      var limits = _getQualificationThresholds();
      var qualifiedRatio = limits.qualifiedMax / limits.conditionalMax;
      var conditionalThreshold = req * qualifiedRatio;

      if (income >= req) {
        return { label: 'Qualified', badgeClass: 'badge-qualified' };
      }
      if (income >= conditionalThreshold) {
        return { label: 'Conditionally Qualified', badgeClass: 'badge-conditional' };
      }
      return { label: 'Not Qualified', badgeClass: 'badge-not-qualified' };
    }

    return fallbackQualified
      ? { label: 'Qualified', badgeClass: 'badge-qualified' }
      : { label: 'Not Qualified', badgeClass: 'badge-not-qualified' };
  }

  function _setBadgeStatus(el, status) {
    if (!el || !status) return;
    el.classList.remove('badge-qualified', 'badge-conditional', 'badge-not-qualified');
    el.classList.add(status.badgeClass);
    el.textContent = status.label;
  }

  function _selectedBrowseYear(filterValue) {
    if (filterValue === 'qualified_5') return '5';
    if (filterValue === 'qualified_10') return '10';
    if (filterValue === 'qualified_15') return '15';
    if (filterValue === 'qualified_20') return '20';
    if (filterValue === '5' || filterValue === '10' || filterValue === '15' || filterValue === '20') return filterValue;
    return '';
  }

  function _datasetYearValue(ds, baseName, year) {
    if (!ds) return '';
    var camelKey = baseName + year;
    if (Object.prototype.hasOwnProperty.call(ds, camelKey)) return ds[camelKey];
    // Backward compatibility for attributes like data-prop-qualified-5
    var legacyKey = baseName + '-' + year;
    if (Object.prototype.hasOwnProperty.call(ds, legacyKey)) return ds[legacyKey];
    return '';
  }

  function _psgcWireCascade(cfg) {
    var regionSel = document.getElementById(cfg.regionSelectId);
    var provinceSel = document.getElementById(cfg.provinceSelectId);
    var citySel = document.getElementById(cfg.citySelectId);
    var brgySel = document.getElementById(cfg.barangaySelectId);
    if (!regionSel || !provinceSel || !citySel || !brgySel) {
      psgcLog('warn', 'PSGC cascade selector(s) missing', {
        regionSelectId: cfg.regionSelectId,
        provinceSelectId: cfg.provinceSelectId,
        citySelectId: cfg.citySelectId,
        barangaySelectId: cfg.barangaySelectId,
      });
      return;
    }

    var hLoc = document.getElementById(cfg.hiddenLocationId);
    var lineInput = cfg.addressLineId ? document.getElementById(cfg.addressLineId) : null;
    var hRegionCode = document.getElementById(cfg.hiddenRegionCodeId);
    var hRegionName = document.getElementById(cfg.hiddenRegionNameId);
    var hProvinceCode = document.getElementById(cfg.hiddenProvinceCodeId);
    var hProvinceName = document.getElementById(cfg.hiddenProvinceNameId);
    var hCityCode = document.getElementById(cfg.hiddenCityCodeId);
    var hCityName = document.getElementById(cfg.hiddenCityNameId);
    var hBrgyCode = document.getElementById(cfg.hiddenBarangayCodeId);
    var hBrgyName = document.getElementById(cfg.hiddenBarangayNameId);

    var initial = {
      region: (hRegionCode && hRegionCode.value) || '',
      province: (hProvinceCode && hProvinceCode.value) || '',
      citymun: (hCityCode && hCityCode.value) || '',
      barangay: (hBrgyCode && hBrgyCode.value) || '',
    };

    function selectedText(selectEl) {
      if (!selectEl || !selectEl.value || !selectEl.selectedOptions || !selectEl.selectedOptions.length) return '';
      return (selectEl.selectedOptions[0].textContent || '').trim();
    }

    function syncHidden() {
      var regionName = selectedText(regionSel);
      var provinceName = selectedText(provinceSel);
      var cityName = selectedText(citySel);
      var brgyName = selectedText(brgySel);

      if (hRegionCode) hRegionCode.value = regionSel.value || '';
      if (hRegionName) hRegionName.value = regionName;
      if (hProvinceCode) hProvinceCode.value = provinceSel.value || '';
      if (hProvinceName) hProvinceName.value = provinceName;
      if (hCityCode) hCityCode.value = citySel.value || '';
      if (hCityName) hCityName.value = cityName;
      if (hBrgyCode) hBrgyCode.value = brgySel.value || '';
      if (hBrgyName) hBrgyName.value = brgyName;
      if (hLoc) {
        var locTail = _psgcComposeLocation({
          region: regionName,
          province: provinceName,
          citymun: cityName,
          barangay: brgyName,
        });
        var line = lineInput ? (lineInput.value || '').trim() : '';
        hLoc.value = [line, locTail].filter(Boolean).join(', ');
      }
    }

    async function loadCities(selectedCityCode) {
      var items = [];
      if (provinceSel.value) {
        items = await _psgcGet('/api/psgc/cities?province_code=' + encodeURIComponent(provinceSel.value));
      } else if (regionSel.value) {
        items = await _psgcGet('/api/psgc/cities?region_code=' + encodeURIComponent(regionSel.value));
      }
      _psgcFillSelect(citySel, items, '-- Select --', selectedCityCode || '');
      psgcLog('info', 'Cities loaded', {
        cascade: cfg.regionSelectId,
        regionCode: regionSel.value || '',
        provinceCode: provinceSel.value || '',
        count: items.length,
      });
      if (!citySel.value) {
        _psgcResetSelect(brgySel, '-- Select --');
      }
    }

    async function loadBarangays(selectedBrgyCode) {
      if (!citySel.value) {
        _psgcResetSelect(brgySel, '-- Select --');
        return;
      }
      var items = await _psgcGet('/api/psgc/barangays?city_mun_code=' + encodeURIComponent(citySel.value));
      _psgcFillSelect(brgySel, items, '-- Select --', selectedBrgyCode || '');
      psgcLog('info', 'Barangays loaded', {
        cascade: cfg.regionSelectId,
        cityCode: citySel.value || '',
        count: items.length,
      });
    }

    async function loadProvinces(selectedProvinceCode, selectedCityCode, selectedBrgyCode) {
      if (!regionSel.value) {
        _psgcResetSelect(provinceSel, '-- Select --');
        _psgcResetSelect(citySel, '-- Select --');
        _psgcResetSelect(brgySel, '-- Select --');
        syncHidden();
        return;
      }

      var provinces = await _psgcGet('/api/psgc/provinces?region_code=' + encodeURIComponent(regionSel.value));
      var effProvinceCode = selectedProvinceCode;
      if (!effProvinceCode && provinces && provinces.length === 1) {
        effProvinceCode = provinces[0].code;
      }
      _psgcFillSelect(provinceSel, provinces, '-- Select --', effProvinceCode || '');
      psgcLog('info', 'Provinces loaded', {
        cascade: cfg.regionSelectId,
        regionCode: regionSel.value || '',
        count: provinces.length,
      });
      await loadCities(selectedCityCode || '');
      await loadBarangays(selectedBrgyCode || '');
      syncHidden();
    }

    regionSel.addEventListener('change', function () {
      loadProvinces('', '', '').catch(function () {
        toast('Unable to load provinces/cities from PSGC right now.', 'warning');
      });
    });
    provinceSel.addEventListener('change', function () {
      loadCities('').then(function () {
        _psgcResetSelect(brgySel, '-- Select --');
        syncHidden();
      }).catch(function () {
        toast('Unable to load cities from PSGC right now.', 'warning');
      });
    });
    citySel.addEventListener('change', function () {
      loadBarangays('').then(syncHidden).catch(function () {
        toast('Unable to load barangays from PSGC right now.', 'warning');
      });
    });
    brgySel.addEventListener('change', syncHidden);
    if (lineInput) lineInput.addEventListener('input', syncHidden);

    _psgcGet('/api/psgc/regions').then(function (regions) {
      psgcLog('info', 'Regions loaded', {
        cascade: cfg.regionSelectId,
        count: regions.length,
      });
      _psgcFillSelect(regionSel, regions, '-- Select --', initial.region);
      return loadProvinces(initial.province, initial.citymun, initial.barangay);
    }).then(syncHidden).catch(function () {
      psgcLog('error', 'PSGC cascade initialization failed', { cascade: cfg.regionSelectId });
      toast('Unable to load PSGC location lists right now. Please refresh and try again.', 'warning');
    });
  }

  function initPsgcLocationSelectors() {
    _psgcWireCascade({
      regionSelectId: 'qm-region',
      provinceSelectId: 'qm-province',
      citySelectId: 'qm-citymun',
      barangaySelectId: 'qm-barangay',
      hiddenLocationId: 'qm-loc',
      hiddenRegionCodeId: 'qm-pref-region-code',
      hiddenRegionNameId: 'qm-pref-region-name',
      hiddenProvinceCodeId: 'qm-pref-province-code',
      hiddenProvinceNameId: 'qm-pref-province-name',
      hiddenCityCodeId: 'qm-pref-citymun-code',
      hiddenCityNameId: 'qm-pref-citymun-name',
      hiddenBarangayCodeId: 'qm-pref-barangay-code',
      hiddenBarangayNameId: 'qm-pref-barangay-name',
    });
    _psgcWireCascade({
      regionSelectId: 'prof_home_region_select',
      provinceSelectId: 'prof_home_province_select',
      citySelectId: 'prof_home_citymun_select',
      barangaySelectId: 'prof_home_barangay_select',
      hiddenLocationId: 'prof_address',
      hiddenRegionCodeId: 'prof_home_region_code',
      hiddenRegionNameId: 'prof_home_region_name',
      hiddenProvinceCodeId: 'prof_home_province_code',
      hiddenProvinceNameId: 'prof_home_province_name',
      hiddenCityCodeId: 'prof_home_citymun_code',
      hiddenCityNameId: 'prof_home_citymun_name',
      hiddenBarangayCodeId: 'prof_home_barangay_code',
      hiddenBarangayNameId: 'prof_home_barangay_name',
    });
    _psgcWireCascade({
      regionSelectId: 'prof_emp_region_select',
      provinceSelectId: 'prof_emp_province_select',
      citySelectId: 'prof_emp_citymun_select',
      barangaySelectId: 'prof_emp_barangay_select',
      hiddenLocationId: 'prof_employer_business_address',
      hiddenRegionCodeId: 'prof_emp_region_code',
      hiddenRegionNameId: 'prof_emp_region_name',
      hiddenProvinceCodeId: 'prof_emp_province_code',
      hiddenProvinceNameId: 'prof_emp_province_name',
      hiddenCityCodeId: 'prof_emp_citymun_code',
      hiddenCityNameId: 'prof_emp_citymun_name',
      hiddenBarangayCodeId: 'prof_emp_barangay_code',
      hiddenBarangayNameId: 'prof_emp_barangay_name',
    });
    _psgcWireCascade({
      regionSelectId: 'prof_birth_region_select',
      provinceSelectId: 'prof_birth_province_select',
      citySelectId: 'prof_birth_citymun_select',
      barangaySelectId: 'prof_birth_barangay_select',
      hiddenLocationId: 'prof_birthplace',
      hiddenRegionCodeId: 'prof_birth_region_code',
      hiddenRegionNameId: 'prof_birth_region_name',
      hiddenProvinceCodeId: 'prof_birth_province_code',
      hiddenProvinceNameId: 'prof_birth_province_name',
      hiddenCityCodeId: 'prof_birth_citymun_code',
      hiddenCityNameId: 'prof_birth_citymun_name',
      hiddenBarangayCodeId: 'prof_birth_barangay_code',
      hiddenBarangayNameId: 'prof_birth_barangay_name',
    });

    _psgcWireCascade({
      regionSelectId: 'pbHomeRegionSelect',
      provinceSelectId: 'pbHomeProvinceSelect',
      citySelectId: 'pbHomeCitySelect',
      barangaySelectId: 'pbHomeBarangaySelect',
      hiddenLocationId: 'pbHomeLocation',
      addressLineId: 'pbHomeStreet',
      hiddenRegionCodeId: 'pbHomeRegionCode',
      hiddenRegionNameId: 'pbHomeRegionName',
      hiddenProvinceCodeId: 'pbHomeProvinceCode',
      hiddenProvinceNameId: 'pbHomeProvinceName',
      hiddenCityCodeId: 'pbHomeCityCode',
      hiddenCityNameId: 'pbHomeCityName',
      hiddenBarangayCodeId: 'pbHomeBarangayCode',
      hiddenBarangayNameId: 'pbHomeBarangayName',
    });

    _psgcWireCascade({
      regionSelectId: 'pbEmpRegionSelect',
      provinceSelectId: 'pbEmpProvinceSelect',
      citySelectId: 'pbEmpCitySelect',
      barangaySelectId: 'pbEmpBarangaySelect',
      hiddenLocationId: 'pbEmpLocation',
      addressLineId: 'pbEmpStreet',
      hiddenRegionCodeId: 'pbEmpRegionCode',
      hiddenRegionNameId: 'pbEmpRegionName',
      hiddenProvinceCodeId: 'pbEmpProvinceCode',
      hiddenProvinceNameId: 'pbEmpProvinceName',
      hiddenCityCodeId: 'pbEmpCityCode',
      hiddenCityNameId: 'pbEmpCityName',
      hiddenBarangayCodeId: 'pbEmpBarangayCode',
      hiddenBarangayNameId: 'pbEmpBarangayName',
    });

    _psgcWireCascade({
      regionSelectId: 'pbMailRegionSelect',
      provinceSelectId: 'pbMailProvinceSelect',
      citySelectId: 'pbMailCitySelect',
      barangaySelectId: 'pbMailBarangaySelect',
      hiddenLocationId: 'pbMailLocation',
      addressLineId: 'pbMailStreet',
      hiddenRegionCodeId: 'pbMailRegionCode',
      hiddenRegionNameId: 'pbMailRegionName',
      hiddenProvinceCodeId: 'pbMailProvinceCode',
      hiddenProvinceNameId: 'pbMailProvinceName',
      hiddenCityCodeId: 'pbMailCityCode',
      hiddenCityNameId: 'pbMailCityName',
      hiddenBarangayCodeId: 'pbMailBarangayCode',
      hiddenBarangayNameId: 'pbMailBarangayName',
    });

    _psgcWireCascade({
      regionSelectId: 'spEmpRegionSelect',
      provinceSelectId: 'spEmpProvinceSelect',
      citySelectId: 'spEmpCitySelect',
      barangaySelectId: 'spEmpBarangaySelect',
      hiddenLocationId: 'spEmpLocation',
      addressLineId: 'spEmpStreet',
      hiddenRegionCodeId: 'spEmpRegionCode',
      hiddenRegionNameId: 'spEmpRegionName',
      hiddenProvinceCodeId: 'spEmpProvinceCode',
      hiddenProvinceNameId: 'spEmpProvinceName',
      hiddenCityCodeId: 'spEmpCityCode',
      hiddenCityNameId: 'spEmpCityName',
      hiddenBarangayCodeId: 'spEmpBarangayCode',
      hiddenBarangayNameId: 'spEmpBarangayName',
    });
  }

  function syncTopAvatar(url) {
    var media = document.getElementById('cpTopAvatarMedia');
    if (!media) return;

    var existingImg = document.getElementById('cpTopAvatarImg');
    var existingIcon = media.querySelector('i');

    if (url) {
      if (existingIcon) existingIcon.remove();
      if (existingImg) {
        existingImg.src = url;
      } else {
        var img = document.createElement('img');
        img.id = 'cpTopAvatarImg';
        img.src = url;
        img.alt = 'Profile photo';
        media.appendChild(img);
      }
    } else {
      if (existingImg) existingImg.remove();
      if (!existingIcon) {
        var icon = document.createElement('i');
        icon.className = 'fa fa-user';
        media.appendChild(icon);
      }
    }
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: csrfHeaders(),
      body: JSON.stringify(body),
    });
    return res.json();
  }

  /* ── Portal nav-link active state ───────────────────────── */
  function updatePortalNav(pageId) {
    document.querySelectorAll(".cp-nav-link[data-page]").forEach(function (link) {
      link.classList.toggle("active", link.getAttribute("data-page") === pageId);
    });
  }

  // Intercept cp-nav-link clicks to keep active state in sync
  document.addEventListener("click", function (e) {
    const navLink = e.target.closest(".cp-nav-link[data-page]");
    if (navLink) {
      e.preventDefault();
      const page = navLink.getAttribute("data-page");
      if (typeof window._navGuard === "function" && window._navGuard(page) === false) return;
      if (typeof showPage === "function") showPage(page);
      updatePortalNav(page);
      // Close mobile nav
      document.getElementById("cpNav")?.classList.remove("open");
    }
  });

  // Wrap showPage so portal nav stays in sync for all callers
  function bootstrapClientDashboard() {
    const _orig = window.showPage;
    if (typeof _orig === "function") {
      window.showPage = function (pageId) {
        _orig(pageId);
        updatePortalNav(pageId);
      };
    }
    // Restore active state from session storage
    try {
      const saved = sessionStorage.getItem("activeDashPage");
      if (saved) updatePortalNav(saved);
    } catch (e) {}

    // Honor ?page=xxx URL parameter and one-time modal flags.
    try {
      const params = new URLSearchParams(window.location.search);
      const pageParam = params.get("page");
      const openPurchaseTrip = params.get("open_purchase_trip");
      if (pageParam && typeof window.showPage === "function") {
        window.showPage(pageParam);
      }
      if (openPurchaseTrip) {
        openBuyerInfoModal(openPurchaseTrip);
      }
      if (pageParam || openPurchaseTrip) {
        window.history.replaceState({}, "", window.location.pathname);
      }
    } catch (e) {}

    initPsgcLocationSelectors();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapClientDashboard);
  } else {
    bootstrapClientDashboard();
  }

  /* ── "data-goto" links (within same page) ────────────────── */
  document.addEventListener("click", function (e) {
    if (e.target.closest('.cp-notif-read-btn')) return;
    const el = e.target.closest("[data-goto]");
    if (el) {
      e.preventDefault();
      const page = el.dataset.goto;
        if (typeof window._navGuard === "function" && window._navGuard(page) === false) return;
        if (typeof showPage === "function") showPage(page);
    }
    const el2 = e.target.closest("[data-goto-page]");
    if (el2) {
      const page = el2.dataset.gotoPage;
        if (typeof window._navGuard === "function" && window._navGuard(page) === false) return;
        if (typeof showPage === "function") showPage(page);
      const dd = document.getElementById("dashNotifDropdown");
      if (dd) dd.classList.remove("open");
    }
  });

  (function initClientNotifReadState() {
    var body = document.getElementById('dashNotifBody');
    if (!body) return;
    var readAllBtn = document.getElementById('dashNotifReadAll');
    var shell = document.querySelector('.cp-shell');
    var STORAGE_KEY = 'sqhClientNotifRead:' + ((shell && shell.dataset && shell.dataset.userId) || 'default');

    function getReadSet() {
      try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')); }
      catch (_) { return new Set(); }
    }
    function saveReadSet(set) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set)));
    }
    function updateBadge() {
      var unread = body.querySelectorAll('.cp-notif-item[data-notif-key]:not(.notif-read)').length;
      var badgeEl = document.getElementById('dashNotifBadge');
      var pillEl = document.getElementById('dashNotifPill');
      if (badgeEl) {
        badgeEl.textContent = String(unread);
        badgeEl.classList.toggle('d-none', unread === 0);
      }
      if (pillEl) {
        pillEl.textContent = unread + ' unread';
        pillEl.style.display = unread === 0 ? 'none' : '';
      }
      if (readAllBtn) readAllBtn.style.display = unread === 0 ? 'none' : '';
    }
    function sortUnreadFirst() {
      var items = Array.prototype.slice.call(body.querySelectorAll('.cp-notif-item[data-notif-key]'));
      items.sort(function(a, b) {
        var ar = a.classList.contains('notif-read') ? 1 : 0;
        var br = b.classList.contains('notif-read') ? 1 : 0;
        return ar - br;
      });
      items.forEach(function(item) { body.appendChild(item); });
    }
    function markItemRead(item, persist) {
      if (!item || item.classList.contains('notif-read')) return;
      item.classList.add('notif-read');
      var key = item.dataset.notifKey;
      if (persist && key) {
        var set = getReadSet();
        set.add(key);
        saveReadSet(set);
      }
      var btn = item.querySelector('.cp-notif-read-btn');
      if (btn && btn.parentNode) {
        var badge = document.createElement('span');
        badge.className = 'cp-notif-read-badge';
        badge.innerHTML = '<i class="fas fa-check-double"></i>';
        btn.parentNode.replaceChild(badge, btn);
      }
    }

    var readSet = getReadSet();
    body.querySelectorAll('.cp-notif-item[data-notif-key]').forEach(function(item) {
      if (readSet.has(item.dataset.notifKey)) markItemRead(item, false);
    });
    sortUnreadFirst();
    updateBadge();

    body.addEventListener('click', function(e) {
      var readBtn = e.target.closest('.cp-notif-read-btn');
      if (!readBtn) return;
      e.preventDefault();
      e.stopPropagation();
      markItemRead(readBtn.closest('.cp-notif-item'), true);
      sortUnreadFirst();
      updateBadge();
    });

    if (readAllBtn) {
      readAllBtn.addEventListener('click', function(e) {
        e.preventDefault();
        var set = getReadSet();
        body.querySelectorAll('.cp-notif-item[data-notif-key]').forEach(function(item) {
          var key = item.dataset.notifKey;
          if (key) set.add(key);
          markItemRead(item, false);
        });
        saveReadSet(set);
        sortUnreadFirst();
        updateBadge();
      });
    }
  })();

  /* ── Browse Properties — filters ────────────────────────── */
  (function initBrowseFilters() {
    const search  = document.getElementById("browseSearch");
    const typeF   = document.getElementById("browseTypeFilter");
    const budgetF = document.getElementById("browseBudgetFilter");
    const bedsF   = document.getElementById("browseBedsFilter");
    const qualF   = document.getElementById("browseQualifiedFilter");
    const grid    = document.getElementById("browseCardsGrid");
    const noRes   = document.getElementById("browseNoResults");
    if (!grid) return;

    function updateCardQualificationBadge(col, qualFilterValue, clientIncome) {
      var badgeEl = col.querySelector('.js-browse-qual-badge');
      if (!badgeEl) return;

      var year = _selectedBrowseYear(qualFilterValue);
      var isQualified = String(col.dataset.propQualified || '0') === '1';
      var isQualified5 = String(_datasetYearValue(col.dataset, 'propQualified', '5') || '0') === '1';
      var isQualified10 = String(_datasetYearValue(col.dataset, 'propQualified', '10') || '0') === '1';
      var isQualified15 = String(_datasetYearValue(col.dataset, 'propQualified', '15') || '0') === '1';
      var isQualified20 = String(_datasetYearValue(col.dataset, 'propQualified', '20') || '0') === '1';
      var req5 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '5') || 0);
      var req10 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '10') || 0);
      var req15 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '15') || 0);
      var req20 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '20') || 0);

      if (!year) {
        var reqs = [req5, req10, req15, req20].filter(function (v) { return v > 0; });
        if (reqs.length) {
          var statuses = reqs.map(function (req) {
            return _computeQualificationStatus(req, clientIncome, false);
          });
          var hasQualified = statuses.some(function (s) { return s.badgeClass === 'badge-qualified'; });
          var hasConditional = statuses.some(function (s) { return s.badgeClass === 'badge-conditional'; });
          if (hasQualified) {
            _setBadgeStatus(badgeEl, { label: 'Qualified', badgeClass: 'badge-qualified' });
          } else if (hasConditional) {
            _setBadgeStatus(badgeEl, { label: 'Conditionally Qualified', badgeClass: 'badge-conditional' });
          } else {
            _setBadgeStatus(badgeEl, { label: 'Not Qualified', badgeClass: 'badge-not-qualified' });
          }
        } else {
          _setBadgeStatus(badgeEl, _computeQualificationStatus(0, 0, isQualified));
        }
        return;
      }

      var requiredIncome = 0;
      var fallbackQualified = false;
      if (year === '5') {
        requiredIncome = req5;
        fallbackQualified = isQualified5;
      } else if (year === '10') {
        requiredIncome = req10;
        fallbackQualified = isQualified10;
      } else if (year === '15') {
        requiredIncome = req15;
        fallbackQualified = isQualified15;
      } else if (year === '20') {
        requiredIncome = req20;
        fallbackQualified = isQualified20;
      }

      _setBadgeStatus(badgeEl, _computeQualificationStatus(requiredIncome, clientIncome, fallbackQualified));
    }

    function applyFilters() {
      const q      = (search?.value || "").toLowerCase().trim();
      const type   = (typeF?.value || "").toLowerCase();
      const budget = parseFloat(budgetF?.value || "") || Infinity;
      const beds   = parseInt(bedsF?.value || "") || 0;
      const qual   = (qualF?.value || "").toLowerCase();
      const clientIncome = _getClientMonthlyIncome();

      function matchQualification(col, qualValue) {
        if (!qualValue) return true;

        var req5 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '5') || 0);
        var req10 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '10') || 0);
        var req15 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '15') || 0);
        var req20 = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '20') || 0);
        var isQualified = String(col.dataset.propQualified || '0') === '1';

        if (qualValue === 'qualified') {
          var reqs = [req5, req10, req15, req20].filter(function (v) { return v > 0; });
          if (reqs.length) {
            return reqs.some(function (req) {
              return _computeQualificationStatus(req, clientIncome, false).badgeClass === 'badge-qualified';
            });
          }
          return !!isQualified;
        }

        var year = _selectedBrowseYear(qualValue);
        if (!year) return true;

        var requiredIncome = 0;
        var fallbackQualified = false;
        if (year === '5') {
          requiredIncome = req5;
          fallbackQualified = String(_datasetYearValue(col.dataset, 'propQualified', '5') || '0') === '1';
        } else if (year === '10') {
          requiredIncome = req10;
          fallbackQualified = String(_datasetYearValue(col.dataset, 'propQualified', '10') || '0') === '1';
        } else if (year === '15') {
          requiredIncome = req15;
          fallbackQualified = String(_datasetYearValue(col.dataset, 'propQualified', '15') || '0') === '1';
        } else if (year === '20') {
          requiredIncome = req20;
          fallbackQualified = String(_datasetYearValue(col.dataset, 'propQualified', '20') || '0') === '1';
        }

        return _computeQualificationStatus(requiredIncome, clientIncome, fallbackQualified).badgeClass === 'badge-qualified';
      }

      function cardQualificationStatus(col, year, income) {
        var req = 0;
        var fallback = false;
        if (year === '5') {
          req = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '5') || 0);
          fallback = String(_datasetYearValue(col.dataset, 'propQualified', '5') || '0') === '1';
        } else if (year === '10') {
          req = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '10') || 0);
          fallback = String(_datasetYearValue(col.dataset, 'propQualified', '10') || '0') === '1';
        } else if (year === '15') {
          req = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '15') || 0);
          fallback = String(_datasetYearValue(col.dataset, 'propQualified', '15') || '0') === '1';
        } else if (year === '20') {
          req = _toNumber(_datasetYearValue(col.dataset, 'propReqIncome', '20') || 0);
          fallback = String(_datasetYearValue(col.dataset, 'propQualified', '20') || '0') === '1';
        }
        return _computeQualificationStatus(req, income, fallback);
      }

      function baseMatches(col) {
        const name   = (col.dataset.propName || "").toLowerCase();
        const loc    = (col.dataset.propLoc  || "").toLowerCase();
        const ptype  = (col.dataset.propType || "").toLowerCase();
        const price  = parseFloat(col.dataset.propPrice || 0);
        const pbeds  = parseInt(col.dataset.propBeds || 0);
        const matchQ    = !q    || name.includes(q) || loc.includes(q);
        const matchType = !type || ptype === type;
        const matchBudget = price <= budget;
        const matchBeds = beds === 0 ? true : beds === 4 ? pbeds >= 4 : pbeds === beds;
        return matchQ && matchType && matchBudget && matchBeds;
      }

      const browseSections = document.getElementById('browseSections');
      const secQGrid  = document.getElementById('browseSecQualifiedGrid');
      const secCGrid  = document.getElementById('browseSecConditionalGrid');
      const secNGrid  = document.getElementById('browseSecNotQualifiedGrid');
      const year = _selectedBrowseYear(qual);

      function _returnCardsToMainGrid() {
        if (!grid) return;
        [secQGrid, secCGrid, secNGrid].forEach(function (sec) {
          if (!sec) return;
          while (sec.firstElementChild) {
            grid.appendChild(sec.firstElementChild);
          }
        });
      }

      if (year) {
        // ── Year-split view: organize cards into 3 qualification sections ──
        // Safely return all cards from section grids back to the main grid.
        _returnCardsToMainGrid();
        if (browseSections) browseSections.classList.remove('d-none');
        if (grid) grid.classList.add('d-none');

        var qCount = 0, cCount = 0, nCount = 0;
        grid.querySelectorAll(".browse-card-col").forEach(function (col) {
          const show = baseMatches(col);
          col.classList.toggle("d-none", !show);
          if (!show) return;
          var status = cardQualificationStatus(col, year, clientIncome);
          updateCardQualificationBadge(col, qual, clientIncome);
          if (status.badgeClass === 'badge-qualified') {
            if (secQGrid) secQGrid.appendChild(col);
            qCount++;
          } else if (status.badgeClass === 'badge-conditional') {
            if (secCGrid) secCGrid.appendChild(col);
            cCount++;
          } else {
            if (secNGrid) secNGrid.appendChild(col);
            nCount++;
          }
        });

        var qSection = document.getElementById('browseSectionQualified');
        var cSection = document.getElementById('browseSectionConditional');
        var nSection = document.getElementById('browseSectionNotQualified');
        if (qSection) qSection.classList.toggle('d-none', qCount === 0);
        if (cSection) cSection.classList.toggle('d-none', cCount === 0);
        if (nSection) nSection.classList.toggle('d-none', nCount === 0);
        var qCountEl = document.getElementById('browseQualifiedCount');
        var cCountEl = document.getElementById('browseConditionalCount');
        var nCountEl = document.getElementById('browseNotQualifiedCount');
        if (qCountEl) qCountEl.textContent = qCount;
        if (cCountEl) cCountEl.textContent = cCount;
        if (nCountEl) nCountEl.textContent = nCount;

        if (noRes) noRes.classList.toggle("d-none", (qCount + cCount + nCount) > 0);
        return;
      }

      // ── Flat view: return all cards to the main grid sorted by qualification tier ──
      if (browseSections) browseSections.classList.add('d-none');
      if (grid) grid.classList.remove('d-none');
      _returnCardsToMainGrid();

      // Collect cols that pass base filters (search/type/budget/beds); qualification filter now sorts instead of hiding
      var flatCols = [];
      grid.querySelectorAll(".browse-card-col").forEach(function (col) {
        updateCardQualificationBadge(col, qual, clientIncome);
        var showBase = baseMatches(col);
        col.classList.toggle("d-none", !showBase);
        if (showBase) flatCols.push(col);
      });

      // When a qualification filter is active, sort all base-matching cards by tier: Qualified → Conditional → Not Qualified
      if (qual && flatCols.length > 1) {
        function qualTier(c) {
          var badge = c.querySelector('.js-browse-qual-badge');
          if (!badge) return 2;
          if (badge.classList.contains('badge-qualified')) return 0;
          if (badge.classList.contains('badge-conditional')) return 1;
          return 2;
        }
        flatCols.sort(function (a, b) { return qualTier(a) - qualTier(b); });
        flatCols.forEach(function (c) { grid.appendChild(c); });
      }

      var visible = 0;
      grid.querySelectorAll(".browse-card-col").forEach(function (col) {
        if (!col.classList.contains('d-none')) visible++;
      });

      if (noRes) noRes.classList.toggle("d-none", visible > 0);
    }

    [search, typeF, budgetF, bedsF, qualF].forEach(function (el) {
      if (el) el.addEventListener("input", applyFilters);
      if (el) el.addEventListener("change", applyFilters);
    });

    var grossIncomeEl = document.getElementById('pbGrossIncome');
    if (grossIncomeEl) {
      grossIncomeEl.addEventListener('input', applyFilters);
      grossIncomeEl.addEventListener('change', applyFilters);
    }

    // Re-run filters whenever the Browse page is shown so the card grid is
    // restored after year-split view rearranges the DOM.
    if (typeof window.showPage === 'function') {
      var _origBrowseShowPage = window.showPage;
      window.showPage = function (pageId) {
        var res = _origBrowseShowPage(pageId);
        if (String(pageId || '').toLowerCase() === 'browse') {
          setTimeout(applyFilters, 0);
        }
        return res;
      };
    }

    applyFilters();
  })();

  (function initHomeQualificationBadges() {
    var homePage = document.getElementById('page-home');
    if (!homePage) return;

    function updateHomeCardBadge(card, clientIncome) {
      if (!card) return;
      var badgeEl = card.querySelector('.js-home-qual-badge');
      if (!badgeEl) return;

      var pricing = null;
      try {
        pricing = card.dataset.pricingJson ? JSON.parse(card.dataset.pricingJson) : null;
      } catch (_) {
        pricing = null;
      }

      var req = (pricing && pricing.required_monthly_income && typeof pricing.required_monthly_income === 'object')
        ? pricing.required_monthly_income
        : {};
      var reqVals = [
        _toNumber(req['5']),
        _toNumber(req['10']),
        _toNumber(req['15']),
        _toNumber(req['20'])
      ].filter(function (v) { return v > 0; });

      if (reqVals.length) {
        var statuses = reqVals.map(function (v) {
          return _computeQualificationStatus(v, clientIncome, false);
        });
        var hasQualified = statuses.some(function (s) { return s.badgeClass === 'badge-qualified'; });
        var hasConditional = statuses.some(function (s) { return s.badgeClass === 'badge-conditional'; });
        if (hasQualified) {
          _setBadgeStatus(badgeEl, { label: 'Qualified', badgeClass: 'badge-qualified' });
        } else if (hasConditional) {
          _setBadgeStatus(badgeEl, { label: 'Conditionally Qualified', badgeClass: 'badge-conditional' });
        } else {
          _setBadgeStatus(badgeEl, { label: 'Not Qualified', badgeClass: 'badge-not-qualified' });
        }
        return;
      }

      var fallbackQualified = String(card.dataset.propQualified || '0') === '1';
      _setBadgeStatus(badgeEl, _computeQualificationStatus(0, 0, fallbackQualified));
    }

    function applyHomeBadges() {
      var clientIncome = _getClientMonthlyIncome();
      homePage.querySelectorAll('.prop-card').forEach(function (card) {
        updateHomeCardBadge(card, clientIncome);
      });

      var qualifiedGrid = document.getElementById('homeQualifiedGrid');
      var qualifiedEmpty = document.getElementById('homeQualifiedEmpty');
      if (qualifiedGrid && qualifiedEmpty) {
        // Qualified Models: Client must be fully qualified for the 5-year loan term (income >= req['5']).
        // Being qualified for 5 years guarantees qualification across 10, 15, and 20 years.
        var qVisible = 0;
        qualifiedGrid.querySelectorAll('.home-qualified-card-col').forEach(function (col) {
          var card = col.querySelector('.prop-card');
          var pricing = null;
          try {
            pricing = card && card.dataset.pricingJson ? JSON.parse(card.dataset.pricingJson) : null;
          } catch (_) {
            pricing = null;
          }
          var req = (pricing && pricing.required_monthly_income && typeof pricing.required_monthly_income === 'object')
            ? pricing.required_monthly_income
            : {};
          var req5 = _toNumber(req['5']);
          var isQualified5 = (req5 > 0 && clientIncome > 0)
            ? (clientIncome >= req5)
            : (String(card && card.dataset.propQualified || '0') === '1');

          var badge = col.querySelector('.js-home-qual-badge');
          if (badge) {
            _setBadgeStatus(badge, isQualified5 ? { label: 'Qualified', badgeClass: 'badge-qualified' } : { label: 'Not Qualified', badgeClass: 'badge-not-qualified' });
          }
          col.classList.toggle('d-none', !isQualified5);
          if (isQualified5) qVisible += 1;
        });
        qualifiedEmpty.classList.toggle('d-none', qVisible > 0);
      }

      var recommendedGrid = document.getElementById('homeRecommendedGrid');
      var recommendedEmpty = document.getElementById('homeRecommendedEmpty');
      if (recommendedGrid && recommendedEmpty) {
        // Hide Recommended cards that are Not Qualified on every loan term
        // (badge was recomputed by updateHomeCardBadge above). Show the
        // empty state when nothing remains visible.
        var rVisible = 0;
        recommendedGrid.querySelectorAll('.home-recommended-card-col, .col-12.col-sm-6.col-xl-4').forEach(function (col) {
          var badge = col.querySelector('.js-home-qual-badge');
          var isNotQualified = !!(badge && badge.classList.contains('badge-not-qualified'));
          col.classList.toggle('d-none', isNotQualified);
          if (!isNotQualified) rVisible += 1;
        });
        recommendedEmpty.classList.toggle('d-none', rVisible > 0);
      }

      var visitsGrid = document.getElementById('homeRecentVisitsGrid');
      var visitsEmpty = document.getElementById('homeRecentVisitsEmpty');
      if (visitsGrid && visitsEmpty) {
        var vCount = visitsGrid.querySelectorAll('.col-12.col-sm-6.col-lg-4').length;
        visitsEmpty.classList.toggle('d-none', vCount > 0);
      }
    }

    var grossIncomeEl = document.getElementById('pbGrossIncome');
    if (grossIncomeEl) {
      grossIncomeEl.addEventListener('input', applyHomeBadges);
      grossIncomeEl.addEventListener('change', applyHomeBadges);
    }

    applyHomeBadges();
  })();

  /* ── Trips page — search/filter ─────────────────────────── */
  (function initTripsFilter() {
    const search  = document.getElementById("tripsSearch");
    const statusF = document.getElementById("tripsStatusFilter");
    const grid    = document.getElementById("tripsCardGrid");
    const noRes   = document.getElementById("tripsNoResults");
    if (!grid) return;

    function applyFilters() {
      const q      = (search?.value || "").toLowerCase().trim();
      const status = (statusF?.value || "").toLowerCase();

      let visible = 0;
      grid.querySelectorAll(".trip-card-col").forEach(function (col) {
        const name   = (col.querySelector(".cp-trip-prop")?.textContent || "").toLowerCase();
        const st     = (col.dataset.tripStatus || "").toLowerCase();
        const showQ  = !q      || name.includes(q);
        const showSt = !status || st === status;
        const show   = showQ && showSt;
        col.classList.toggle("d-none", !show);
        if (show) visible++;
      });

      if (noRes) noRes.classList.toggle("d-none", visible > 0);
    }

    [search, statusF].forEach(function (el) {
      if (el) el.addEventListener("input", applyFilters);
    });
  })();

  /* ── Bought Properties page — search/filter ─────────────── */
  (function initBoughtFilters() {
    const search = document.getElementById("boughtSearch");
    const priceF = document.getElementById("boughtPriceFilter");
    const grid   = document.getElementById("boughtCardsGrid");
    const noRes  = document.getElementById("boughtNoResults");
    if (!grid) return;

    function matchesPrice(price, filterVal) {
      if (!filterVal) return true;
      if (filterVal === "lt3000000") return price < 3000000;
      if (filterVal === "3000000-6000000") return price >= 3000000 && price <= 6000000;
      if (filterVal === "gt6000000") return price > 6000000;
      return true;
    }

    function applyFilters() {
      const q = (search?.value || "").toLowerCase().trim();
      const pf = (priceF?.value || "").toLowerCase().trim();
      let visible = 0;

      grid.querySelectorAll(".bought-card-col").forEach(function (col) {
        const name = (col.dataset.propName || "").toLowerCase();
        const loc  = (col.dataset.propLoc || "").toLowerCase();
        const price = parseFloat(col.dataset.salePrice || 0);
        const showQ = !q || name.includes(q) || loc.includes(q);
        const showP = matchesPrice(price, pf);
        const show = showQ && showP;
        col.classList.toggle("d-none", !show);
        if (show) {
          visible += 1;
        }
      });

      if (noRes) noRes.classList.toggle("d-none", visible > 0);
    }

    [search, priceF].forEach(function (el) {
      if (el) el.addEventListener("input", applyFilters);
      if (el) el.addEventListener("change", applyFilters);
    });
  })();

  /* ── Property detail modal (admin-style) ─────────────────── */
  var _pvmImages = [];
  var _pvmIdx    = 0;
  var _pdmPropId = null;
  var _bvmImages = [];
  var _bvmIdx    = 0;
  var _tripPreviewImages = [];
  var _tripPreviewIdx = 0;
  var _pendingBuyerESignatureFile = null;
  var _buyerPayloadCacheByTrip = {};

  function gotoTripsPage(statusFilter) {
    if (typeof showPage === "function") showPage("trips");
    updatePortalNav("trips");
    document.getElementById("cpNav")?.classList.remove("open");
    if (statusFilter) {
      var statusSel = document.getElementById('tripsStatusFilter');
      if (statusSel) {
        statusSel.value = statusFilter;
        statusSel.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
  }

  function focusTripCard(tripId) {
    if (!tripId) return;
    var target = document.querySelector('.trip-card-col[data-trip-id="' + tripId + '"]');
    if (!target) return;
    document.querySelectorAll('.trip-card-col.is-trip-focus').forEach(function(el) {
      el.classList.remove('is-trip-focus');
    });
    target.classList.add('is-trip-focus');
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(function() {
      target.classList.remove('is-trip-focus');
    }, 2200);
  }

  function tripStatusBadge(status) {
    var normalized = String(status || '').toLowerCase();
    var badgeClass = normalized === 'sold'
      ? 'badge-sold'
      : normalized === 'approved'
        ? 'badge-qualified'
        : normalized === 'rejected'
          ? 'badge-not-qualified'
          : 'badge-conditional';
    var label = normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : 'Pending';
    return '<span class="sqh-badge ' + badgeClass + '">' + label + '</span>';
  }

  function _applyDarkModalBackdrops() {
    var backdrops = document.querySelectorAll('.modal-backdrop');
    backdrops.forEach(function(el) { el.classList.add('sqh-dark-backdrop'); });
  }

  function setBuyerFormSubmitButtonState(isSubmitted) {
    var submitBtn = document.getElementById('buyerFormSubmitBtn');
    if (!submitBtn) return;
    if (isSubmitted) {
      submitBtn.dataset.submitted = '1';
      submitBtn.disabled = true;
      submitBtn.classList.remove('btn-outline-lime');
      submitBtn.classList.add('btn-outline-blue');
      submitBtn.innerHTML = '<i class="fas fa-eye me-1"></i> View Purchase Form';
    } else {
      submitBtn.dataset.submitted = '0';
      submitBtn.disabled = false;
      submitBtn.classList.remove('btn-outline-blue');
      submitBtn.classList.add('btn-outline-lime');
      submitBtn.innerHTML = '<i class="fas fa-paper-plane me-1"></i> Submit to Admin';
    }
  }

  function setBuyerFormReadOnly(isReadOnly) {
    var modal = document.getElementById('buyerInfoModal');
    var form = document.getElementById('buyerInfoForm');
    if (!modal || !form) return;
    modal.dataset.readOnly = isReadOnly ? '1' : '0';

    var loanFieldIds = [
      'loanUnitId', 'loanSellingPrice', 'loanProcessingFee', 'loanAmount', 'loanDownpayment',
      'loanReservationFee', 'loanPromoDisc', 'loanOrPrNo', 'loanOrPrDate', 'loanBookingOfficer',
      'loanFinancing', 'loanDownpaymentTerm', 'loanTerm'
    ];
    var loanFieldMap = {};
    loanFieldIds.forEach(function (id) {
      loanFieldMap[id] = true;
    });

    form.querySelectorAll('input, select, textarea').forEach(function (el) {
      var type = String(el.type || '').toLowerCase();
      if (type === 'hidden') return;
      var isLoanField = !!loanFieldMap[el.id] || !!el.closest('.buyer-loan-readonly');
      if (isLoanField) {
        if (el.tagName === 'SELECT' || type === 'checkbox' || type === 'radio' || type === 'date') {
          el.disabled = true;
        } else {
          el.readOnly = true;
          el.disabled = false;
        }
        el.classList.add('sqh-disabled-field');
        return;
      }
      if (isReadOnly) {
        if (el.tagName === 'SELECT' || type === 'checkbox' || type === 'radio' || type === 'date') {
          el.disabled = true;
        } else {
          el.readOnly = true;
        }
      } else {
        el.disabled = false;
        el.readOnly = false;
      }
    });

    var submitBtn = document.getElementById('buyerFormSubmitBtn');
    if (submitBtn) submitBtn.classList.toggle('d-none', isReadOnly);
    var consentEl = document.getElementById('buyerConsentAccepted');
    if (consentEl) consentEl.disabled = isReadOnly;
    var browseBtn = document.getElementById('buyerESignatureBrowseBtn');
    if (browseBtn) browseBtn.disabled = isReadOnly;

    form.querySelectorAll('input, textarea').forEach(function (el) {
      if (!el || !el.tagName) return;
      var type = String(el.type || '').toLowerCase();
      if (type === 'hidden' || type === 'file' || type === 'button' || type === 'submit' || type === 'reset') return;
      if (!Object.prototype.hasOwnProperty.call(el.dataset, 'defaultPlaceholder')) {
        el.dataset.defaultPlaceholder = el.placeholder || '';
      }
      if (isReadOnly && !String(el.value || '').trim()) {
        el.placeholder = '—';
      } else {
        el.placeholder = el.dataset.defaultPlaceholder || '';
      }
    });

    form.querySelectorAll('select').forEach(function (sel) {
      if (!sel || !sel.options || !sel.options.length) return;
      var first = sel.options[0];
      if (!first || String(first.value || '') !== '') return;
      if (!Object.prototype.hasOwnProperty.call(first.dataset, 'defaultEmptyLabel')) {
        first.dataset.defaultEmptyLabel = first.textContent || '-- Select --';
      }
      if (isReadOnly && !String(sel.value || '').trim()) {
        first.textContent = '—';
      } else {
        first.textContent = first.dataset.defaultEmptyLabel || '-- Select --';
      }
    });
  }

  function setTripPurchaseSubmittedUi(tripId) {
    var payloadArg = arguments.length > 1 ? arguments[1] : null;
    if (!tripId) return;
    if (payloadArg && typeof payloadArg === 'object') {
      _buyerPayloadCacheByTrip[String(tripId)] = payloadArg;
    }
    document.querySelectorAll('.trip-preview-trigger[data-trip-id="' + tripId + '"]').forEach(function(card) {
      card.dataset.tripPurchaseFormSubmitted = '1';
      card.dataset.tripPurchaseStatus = 'pending';
      if (payloadArg && typeof payloadArg === 'object') {
        try { card.dataset.tripPurchaseFormData = JSON.stringify(payloadArg); } catch (_) {}
      }
    });

    document.querySelectorAll('.trip-continue-btn[data-trip-id="' + tripId + '"]').forEach(function(btn) {
      btn.dataset.purchaseFormSubmitted = '1';
      btn.disabled = false;
      btn.classList.remove('btn-outline-lime');
      btn.classList.add('btn-outline-blue');
      btn.innerHTML = '<i class="fas fa-eye me-1"></i> View Purchase Form';
    });

    var previewBtn = document.getElementById('tripPreviewContinueBtn');
    if (previewBtn && String(previewBtn.dataset.tripId || '') === String(tripId)) {
      previewBtn.dataset.purchaseFormSubmitted = '1';
      previewBtn.disabled = false;
      previewBtn.classList.remove('btn-outline-lime');
      previewBtn.classList.add('btn-outline-blue');
      previewBtn.innerHTML = '<i class="fas fa-eye me-1"></i> View Purchase Form';
      previewBtn.classList.remove('d-none');
    }

    var consentEl = document.getElementById('buyerConsentAccepted');
    if (consentEl) consentEl.checked = true;

    setBuyerFormSubmitButtonState(true);
  }

  function _extractCurrentPurchasePayload(payload) {
    if (!payload || typeof payload !== 'object') return {};
    var clean = {};
    Object.keys(payload).forEach(function (k) {
      if (String(k).indexOf('_purchase_form_') === 0) return;
      clean[k] = payload[k];
    });
    return clean;
  }

  function _clearBuyerFormForFreshEntry() {
    var form = document.getElementById('buyerInfoForm');
    if (!form) return;
    var loanFieldMap = {
      loanUnitId: 1, loanSellingPrice: 1, loanProcessingFee: 1, loanAmount: 1,
      loanDownpayment: 1, loanReservationFee: 1, loanPromoDisc: 1, loanOrPrNo: 1,
      loanOrPrDate: 1, loanBookingOfficer: 1, loanFinancing: 1,
      loanDownpaymentTerm: 1, loanTerm: 1
    };

    form.querySelectorAll('input, select, textarea').forEach(function (el) {
      if (!el || !el.id) return;
      var type = String(el.type || '').toLowerCase();
      if (type === 'hidden') return;
      if (loanFieldMap[el.id]) return;
      if (type === 'checkbox' || type === 'radio') {
        el.checked = false;
        return;
      }
      if (el.tagName === 'SELECT') {
        el.selectedIndex = 0;
        return;
      }
      if (type !== 'file') el.value = '';
    });

    var consentEl = document.getElementById('buyerConsentAccepted');
    if (consentEl) consentEl.checked = false;
    var esigPickerEl = document.getElementById('buyerESignaturePicker');
    if (esigPickerEl) esigPickerEl.value = '';
    var esigField = document.getElementById('buyerESignatureFile');
    if (esigField) esigField.value = '';
    _pendingBuyerESignatureFile = null;
  }

  function _isBlankFieldValue(v) {
    return v == null || String(v).trim() === '';
  }

  function _applyBuyerAutofillDefaults(params) {
    var opts = params || {};
    var existingPayload = opts.existingPayload || null;
    var card = opts.card || null;
    var modal = opts.modal || null;

    var bookingOfficerEl = document.getElementById('loanBookingOfficer');
    if (bookingOfficerEl && _isBlankFieldValue(bookingOfficerEl.value)) {
      var payloadOfficer = existingPayload && !_isBlankFieldValue(existingPayload.loanBookingOfficer)
        ? String(existingPayload.loanBookingOfficer)
        : '';
      var tripAgent = card ? String(card.dataset.tripAgent || '').trim() : '';
      var fallbackOfficer = payloadOfficer || tripAgent;
      if (fallbackOfficer) bookingOfficerEl.value = fallbackOfficer;
    }

    var grossIncomeEl = document.getElementById('pbGrossIncome');
    if (grossIncomeEl && _isBlankFieldValue(grossIncomeEl.value)) {
      var payloadGrossIncome = existingPayload && !_isBlankFieldValue(existingPayload.pbGrossIncome)
        ? existingPayload.pbGrossIncome
        : '';
      var profileGrossIncome = modal ? String(modal.dataset.profileGrossIncome || '').trim() : '';
      var fallbackGrossIncome = !_isBlankFieldValue(payloadGrossIncome) ? payloadGrossIncome : profileGrossIncome;
      if (!_isBlankFieldValue(fallbackGrossIncome)) grossIncomeEl.value = String(fallbackGrossIncome);
    }
  }

  function applyBuyerFormPayload(payload) {
    if (!payload || typeof payload !== 'object') return;
    Object.keys(payload).forEach(function (key) {
      var val = payload[key];
      var el = document.getElementById(key);
      if (!el) {
        el = document.querySelector('[name="' + String(key).replace(/"/g, '\\"') + '"]');
      }
      if (!el) return;
      var type = String(el.type || '').toLowerCase();
      if (type === 'checkbox') {
        el.checked = !!val;
      } else if (type === 'radio') {
        var radio = document.querySelector('[name="' + el.name + '"][value="' + String(val).replace(/"/g, '\\"') + '"]');
        if (radio) radio.checked = true;
      } else if (val !== null && val !== undefined) {
        el.value = String(val);
        if (el.id === 'pbBankName') {
          el.dataset.pendingValue = String(val || '').trim();
        }
      }
    });

    var depSel = document.getElementById('pbDependentsYesNo');
    if (depSel) depSel.dispatchEvent(new Event('change'));
    var bankSel = document.getElementById('hasBankAccount');
    if (bankSel) bankSel.dispatchEvent(new Event('change'));
    _syncBuyerSpouseSections();
    _wireEmploymentOthersToggle();
  }

  function _wireBuyerDependentsToggle() {
    var yesNo = document.getElementById('pbDependentsYesNo');
    if (!yesNo) return;
    var dependentIds = ['pbDependentsType', 'pbDependentsChildren', 'pbDependentsParents', 'pbDependentsOthers'];
    var applyState = function () {
      var enabled = String(yesNo.value || '').toLowerCase() === 'yes';
      dependentIds.forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.disabled = !enabled;
        el.classList.toggle('sqh-disabled-field', !enabled);
        var group = el.closest('.col-12, .col-md-2, .col-md-3, .col-md-4, .col-md-6') || el.parentElement;
        if (group) group.classList.toggle('buyer-disabled-group', !enabled);
        if (!enabled && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) el.value = '';
        if (!enabled && el.tagName === 'SELECT') el.selectedIndex = 0;
      });
    };
    yesNo.addEventListener('change', applyState);
    applyState();
  }

  function _syncBuyerConditionalFieldStates() {
    var depSel = document.getElementById('pbDependentsYesNo');
    if (depSel) depSel.dispatchEvent(new Event('change'));
    var bankSel = document.getElementById('hasBankAccount');
    if (bankSel) bankSel.dispatchEvent(new Event('change'));
  }

  function _initBuyerNumericGuards() {
    function digitsOnly(v) {
      return String(v || '').replace(/\D+/g, '');
    }

    function bindDigitsOnlyElement(el, maxLen) {
      if (!el) return;
      el.addEventListener('input', function () {
        var raw = digitsOnly(el.value);
        el.value = maxLen ? raw.slice(0, maxLen) : raw;
      });
    }

    function bindDigitsOnly(id, maxLen) {
      bindDigitsOnlyElement(document.getElementById(id), maxLen);
    }

    function bindTinFormatter(el) {
      if (!el) return;
      el.addEventListener('input', function () {
        var raw = digitsOnly(el.value).slice(0, 12);
        var parts = [];
        for (var i = 0; i < raw.length; i += 3) parts.push(raw.slice(i, i + 3));
        el.value = parts.join('-');
      });
    }

    function bindUmidFormatter(el) {
      if (!el) return;
      el.addEventListener('input', function () {
        var raw = digitsOnly(el.value).slice(0, 10);
        if (raw.length <= 2) {
          el.value = raw;
          return;
        }
        if (raw.length <= 9) {
          el.value = raw.slice(0, 2) + '-' + raw.slice(2);
          return;
        }
        el.value = raw.slice(0, 2) + '-' + raw.slice(2, 9) + '-' + raw.slice(9);
      });
    }

    var tinEl = document.getElementById('pbTin');
    bindTinFormatter(tinEl);

    var umidEl = document.getElementById('pbUmid');
    bindUmidFormatter(umidEl);

    bindTinFormatter(document.getElementById('spTin'));
    bindUmidFormatter(document.getElementById('spUmid'));

    bindDigitsOnly('pbTelephone', 12);
    bindDigitsOnly('pbMobile', 11);
    bindDigitsOnly('pbViber', 11);
    bindDigitsOnly('pbWhatsApp', 11);
    bindDigitsOnly('pbHomeNo', 6);
    bindDigitsOnly('pbHomeZip', 5);
    bindDigitsOnly('pbEmployerTelephone', 12);
    bindDigitsOnly('pbEmpAddressNo', 6);
    bindDigitsOnly('pbMailNo', 6);
    bindDigitsOnly('pbMailZip', 5);
    bindDigitsOnly('spTelephone', 12);
    bindDigitsOnly('spMobile', 11);
    bindDigitsOnly('spEmpTelephone', 12);
    bindDigitsOnly('spEmpAddressNo', 6);
    bindDigitsOnly('spEmpZip', 5);
    bindDigitsOnly('spEmpTenure', 3);
    bindDigitsOnly('pbDependentsChildren', 2);
    bindDigitsOnly('pbDependentsParents', 2);
    bindDigitsOnly('pbDependentsOthers', 2);
    bindDigitsOnly('pbLengthOfStay', 3);

    var form = document.getElementById('buyerInfoForm');
    if (form) {
      form.querySelectorAll('input').forEach(function (input) {
        var label = (input.closest('.col-12, .col-md-2, .col-md-3, .col-md-4, .col-md-6') || input.parentElement || form).querySelector('label.form-label');
        var lt = String(label ? label.textContent : '').toLowerCase();
        if (!lt) return;

        if (lt.indexOf('mobile no') !== -1) {
          input.placeholder = input.placeholder || '09171234567';
          input.setAttribute('inputmode', 'numeric');
          if (!input.getAttribute('maxlength')) input.setAttribute('maxlength', '11');
          bindDigitsOnlyElement(input, 11);
          return;
        }

        if (lt.indexOf('telephone no') !== -1 || lt.indexOf('tel no') !== -1) {
          input.placeholder = input.placeholder || '0212345678';
          input.setAttribute('inputmode', 'numeric');
          if (!input.getAttribute('maxlength')) input.setAttribute('maxlength', '12');
          bindDigitsOnlyElement(input, 12);
          return;
        }

        if (lt === 'tin' || lt.indexOf('tax identification no') !== -1) {
          input.placeholder = input.placeholder || '000-000-000-000';
          input.setAttribute('inputmode', 'numeric');
          if (!input.getAttribute('maxlength')) input.setAttribute('maxlength', '15');
          bindTinFormatter(input);
          return;
        }

        if (lt.indexOf('sss/gsis/umid') !== -1) {
          input.placeholder = input.placeholder || '00-0000000-0';
          input.setAttribute('inputmode', 'numeric');
          if (!input.getAttribute('maxlength')) input.setAttribute('maxlength', '13');
          bindUmidFormatter(input);
          return;
        }

        if (lt.indexOf('length of stay') !== -1) {
          input.placeholder = input.placeholder || 'e.g. 5';
          input.setAttribute('inputmode', 'numeric');
          if (!input.getAttribute('maxlength')) input.setAttribute('maxlength', '3');
          bindDigitsOnlyElement(input, 3);
        }
      });
    }

    var grossIncomeEl = document.getElementById('pbGrossIncome');
    if (grossIncomeEl) {
      grossIncomeEl.addEventListener('input', function () {
        var clean = String(grossIncomeEl.value || '').replace(/[^0-9.]/g, '');
        var parts = clean.split('.');
        if (parts.length > 2) clean = parts[0] + '.' + parts.slice(1).join('');
        grossIncomeEl.value = clean;
      });
    }

    var spouseGrossEl = document.getElementById('spGrossIncome');
    if (spouseGrossEl) {
      spouseGrossEl.addEventListener('input', function () {
        var clean = String(spouseGrossEl.value || '').replace(/[^0-9.]/g, '');
        var parts = clean.split('.');
        if (parts.length > 2) clean = parts[0] + '.' + parts.slice(1).join('');
        spouseGrossEl.value = clean;
      });
    }
  }

  function _initBuyerBanks() {
    var hasBankAccountEl = document.getElementById('hasBankAccount');
    var bankSelectEl = document.getElementById('pbBankName');
    if (!hasBankAccountEl || !bankSelectEl) return;

    var fallbackBanks = [];

    function fillBanks(items) {
      var existing = String(bankSelectEl.value || '').trim() || String(bankSelectEl.dataset.pendingValue || '').trim();
      bankSelectEl.innerHTML = '<option value="">-- Select --</option>';
      (items || []).forEach(function (name) {
        var clean = String(name || '').trim();
        if (!clean) return;
        var opt = document.createElement('option');
        opt.value = clean;
        opt.textContent = clean;
        bankSelectEl.appendChild(opt);
      });
      if (existing) bankSelectEl.value = existing;
      if (bankSelectEl.value === existing) {
        bankSelectEl.dataset.pendingValue = '';
      }
    }

    function applyBankEnabledState() {
      var hasBank = String(hasBankAccountEl.value || '').toLowerCase() === 'yes';
      bankSelectEl.disabled = !hasBank;
      if (!hasBank) bankSelectEl.value = '';
    }

    hasBankAccountEl.addEventListener('change', applyBankEnabledState);
    applyBankEnabledState();

    var banksCfgEl = document.getElementById('buyer-banks-config');
    if (banksCfgEl && String(banksCfgEl.textContent || '').trim()) {
      try {
        var injectedBanks = JSON.parse(banksCfgEl.textContent);
        if (Array.isArray(injectedBanks) && injectedBanks.length) {
          fillBanks(injectedBanks);
          return;
        }
      } catch (_) {}
    }

    fetch('/api/reference/ph-banks', {
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      cache: 'no-store'
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var rows = data && data.ok && Array.isArray(data.banks) ? data.banks : fallbackBanks;
        fillBanks(rows);
      })
      .catch(function () {
        fillBanks(fallbackBanks);
      });
  }

  function _copyHomeAddressToMailing() {
    var pairs = [
      ['pbHomeNo', 'pbMailNo'],
      ['pbHomeStreet', 'pbMailStreet'],
      ['pbHomeSubdivision', 'pbMailSubdivision'],
      ['pbHomeCountry', 'pbMailCountry'],
      ['pbHomeZip', 'pbMailZip'],
      ['pbHomeLocation', 'pbMailLocation'],
      ['pbHomeRegionCode', 'pbMailRegionCode'],
      ['pbHomeRegionName', 'pbMailRegionName'],
      ['pbHomeProvinceCode', 'pbMailProvinceCode'],
      ['pbHomeProvinceName', 'pbMailProvinceName'],
      ['pbHomeCityCode', 'pbMailCityCode'],
      ['pbHomeCityName', 'pbMailCityName'],
      ['pbHomeBarangayCode', 'pbMailBarangayCode'],
      ['pbHomeBarangayName', 'pbMailBarangayName']
    ];
    pairs.forEach(function (pair) {
      var from = document.getElementById(pair[0]);
      var to = document.getElementById(pair[1]);
      if (from && to) to.value = from.value || '';
    });

    var regionSelect = document.getElementById('pbMailRegionSelect');
    var provinceSelect = document.getElementById('pbMailProvinceSelect');
    var citySelect = document.getElementById('pbMailCitySelect');
    var barangaySelect = document.getElementById('pbMailBarangaySelect');
    var homeRegionCode = (document.getElementById('pbHomeRegionCode') || {}).value || '';
    var homeProvinceCode = (document.getElementById('pbHomeProvinceCode') || {}).value || '';
    var homeCityCode = (document.getElementById('pbHomeCityCode') || {}).value || '';
    var homeBarangayCode = (document.getElementById('pbHomeBarangayCode') || {}).value || '';

    function selectWhenReady(selectEl, value, cb, retries) {
      if (!selectEl || !value) {
        if (typeof cb === 'function') cb();
        return;
      }
      var maxRetries = typeof retries === 'number' ? retries : 20;
      var attempt = 0;
      (function applyValue() {
        var hasOption = Array.from(selectEl.options || []).some(function (opt) { return opt.value === value; });
        if (hasOption) {
          selectEl.value = value;
          selectEl.dispatchEvent(new Event('change'));
          if (typeof cb === 'function') cb();
          return;
        }
        if (attempt >= maxRetries) {
          if (typeof cb === 'function') cb();
          return;
        }
        attempt += 1;
        setTimeout(applyValue, 120);
      })();
    }

    selectWhenReady(regionSelect, homeRegionCode, function () {
      selectWhenReady(provinceSelect, homeProvinceCode, function () {
        selectWhenReady(citySelect, homeCityCode, function () {
          selectWhenReady(barangaySelect, homeBarangayCode);
        });
      });
    });
  }

  function _wireBuyerMailAddressSource() {
    var addressSourceEl = document.getElementById('pbMailPref');
    if (!addressSourceEl) return;

    function isHomeSource() {
      var source = String(addressSourceEl.value || '').toLowerCase();
      return source === 'home' || source === 'residence';
    }

    function syncWhenHomeSource() {
      if (!isHomeSource()) return;
      _copyHomeAddressToMailing();
    }

    addressSourceEl.addEventListener('change', function () {
      syncWhenHomeSource();
    });

    [
      'pbHomeNo', 'pbHomeStreet', 'pbHomeSubdivision', 'pbHomeCountry', 'pbHomeZip',
      'pbHomeRegionCode', 'pbHomeProvinceCode', 'pbHomeCityCode', 'pbHomeBarangayCode',
      'pbHomeRegionSelect', 'pbHomeProvinceSelect', 'pbHomeCitySelect', 'pbHomeBarangaySelect'
    ].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var evt = el.tagName === 'SELECT' ? 'change' : 'input';
      el.addEventListener(evt, syncWhenHomeSource);
    });

    setTimeout(syncWhenHomeSource, 120);
  }

  function _initBuyerCountryInputs() {
    var form = document.getElementById('buyerInfoForm');
    if (!form) return;

    var cfgEl = document.getElementById('buyer-countries-config');
    var countries = [];
    if (cfgEl && cfgEl.textContent) {
      try {
        var parsed = JSON.parse(cfgEl.textContent);
        if (Array.isArray(parsed)) countries = parsed;
      } catch (_) {}
    }
    if (!countries.length) return;

    var listId = 'buyerCountryList';
    var datalist = document.getElementById(listId);
    if (!datalist) {
      datalist = document.createElement('datalist');
      datalist.id = listId;
      document.body.appendChild(datalist);
    }

    datalist.innerHTML = countries.map(function (row) {
      var name = String((row && row.name) || '').trim();
      if (!name) return '';
      return '<option value="' + name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') + '"></option>';
    }).join('');

    form.querySelectorAll('input').forEach(function (input) {
      var col = input.closest('.col-12, .col-md-2, .col-md-3, .col-md-4, .col-md-6') || input.parentElement;
      var labelEl = col ? col.querySelector('label.form-label') : null;
      var label = String((labelEl && labelEl.textContent) || '').trim().toLowerCase();
      if (label !== 'country') return;
      input.setAttribute('list', listId);
      input.setAttribute('autocomplete', 'off');
      if (!input.placeholder) input.placeholder = 'Select country';
    });
  }

  function _wireEmploymentOthersToggle() {
    var occupationEl = document.getElementById('pbOccupationProfession');
    var positionEl = document.getElementById('pbOccupationalPosition');
    var othersEl = document.getElementById('pbEmploymentOthers');
    if (!occupationEl || !positionEl || !othersEl) return;

    function syncState() {
      var hasOccupation = String(occupationEl.value || '').trim().length > 0;
      var hasPosition = String(positionEl.value || '').trim().length > 0;
      var lockOthers = hasOccupation && hasPosition;
      othersEl.disabled = lockOthers;
      othersEl.classList.toggle('sqh-disabled-field', lockOthers);
      if (lockOthers) othersEl.value = '';
    }

    occupationEl.addEventListener('input', syncState);
    positionEl.addEventListener('input', syncState);
    syncState();
  }

  function _syncBuyerSpouseSections() {
    var hasSpouseEl = document.getElementById('pbHasSpouse');
    if (!hasSpouseEl) return;

    var hasSpouse = String(hasSpouseEl.value || '').toLowerCase() === 'yes';
    ['.buyer-form-employment', '.buyer-form-semployment'].forEach(function (selector) {
      var section = document.querySelector(selector);
      if (!section) return;

      section.querySelectorAll('input, select, textarea').forEach(function (el) {
        if (!el || el.id === 'pbHasSpouse' || String(el.type || '').toLowerCase() === 'hidden') return;

        if (hasSpouse) {
          el.disabled = false;
          el.readOnly = false;
          el.classList.remove('sqh-disabled-field');
          return;
        }

        if (el.tagName === 'SELECT') {
          el.selectedIndex = 0;
        } else if (String(el.type || '').toLowerCase() === 'checkbox' || String(el.type || '').toLowerCase() === 'radio') {
          el.checked = false;
        } else if (String(el.type || '').toLowerCase() !== 'file') {
          el.value = '';
        }
        el.disabled = true;
        el.readOnly = true;
        el.classList.add('sqh-disabled-field');
      });
    });
  }

  function _wireBuyerSpouseToggle() {
    var hasSpouseEl = document.getElementById('pbHasSpouse');
    if (!hasSpouseEl) return;
    hasSpouseEl.addEventListener('change', _syncBuyerSpouseSections);
    _syncBuyerSpouseSections();
  }

  function openBuyerInfoModal(tripId, options) {
    var opts = options || {};
    var freshMode = !!opts.fresh;
    var modal = document.getElementById('buyerInfoModal');
    if (!modal) return;
    var refEl = document.getElementById('buyerInfoTripRef');
    if (refEl) refEl.textContent = tripId ? ('Trip #' + String(tripId)) : '—';
    var hiddenTripEl = document.getElementById('buyerSubmitTripId');
    if (hiddenTripEl) hiddenTripEl.value = tripId ? String(tripId) : '';

    var card = tripId ? document.querySelector('.trip-preview-trigger[data-trip-id="' + String(tripId) + '"]') : null;
    var isSubmitted = card ? String(card.dataset.tripPurchaseFormSubmitted || '0') === '1' : false;
    var tripStatus = card ? String(card.dataset.tripStatus || '').toLowerCase() : '';
    var purchaseStatus = card ? String(card.dataset.tripPurchaseStatus || '').toLowerCase() : '';
    if (!purchaseStatus) {
      purchaseStatus = isSubmitted ? 'pending' : 'none';
    }
    if (purchaseStatus === 'approved' || tripStatus === 'sold') {
      isSubmitted = true;
      purchaseStatus = 'approved';
    }
    var forceReadOnly = !!opts.readOnly;
    if (purchaseStatus === 'rejected' && !freshMode) {
      forceReadOnly = true;
    }
    var existingPayloadRaw = card ? String(card.dataset.tripPurchaseFormData || '').trim() : '';
    var existingPayload = null;
    if (existingPayloadRaw) {
      try { existingPayload = JSON.parse(existingPayloadRaw); } catch (_) { existingPayload = null; }
    }
    if (!existingPayload && tripId && _buyerPayloadCacheByTrip[String(tripId)]) {
      existingPayload = _buyerPayloadCacheByTrip[String(tripId)];
    }
    var unitId = card ? String(card.dataset.tripUnitId || '').trim() : '';
    var sellingPrice = card ? parseFloat(card.dataset.tripPrice || '0') : 0;
    var reservationFee = card ? parseFloat(card.dataset.tripReservationFee || '0') : 0;
    var downpaymentRate = card ? parseFloat(card.dataset.tripDownpaymentRate || '0') : 0;
    var promoDiscountRate = card ? parseFloat(card.dataset.tripPromoDiscountRate || '0') : 0;
    var loanablePct = card ? parseFloat(card.dataset.tripLoanablePct || '0') : 0;
    var downpayment = sellingPrice > 0 && downpaymentRate > 0 ? (sellingPrice * (downpaymentRate / 100)) : 0;
    var loanAmount = sellingPrice > 0
      ? (loanablePct > 0 ? (sellingPrice * (loanablePct / 100)) : Math.max(0, sellingPrice - downpayment))
      : 0;

    function fmtMoney(v) {
      var n = Number(v || 0);
      return n > 0 ? ('\u20B1' + n.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })) : '';
    }

    var unitEl = document.getElementById('loanUnitId');
    var sellingEl = document.getElementById('loanSellingPrice');
    var processingEl = document.getElementById('loanProcessingFee');
    var loanEl = document.getElementById('loanAmount');
    var downEl = document.getElementById('loanDownpayment');
    var reservationEl = document.getElementById('loanReservationFee');
    var promoEl = document.getElementById('loanPromoDisc');

    if (unitEl) unitEl.value = unitId;
    if (sellingEl) sellingEl.value = fmtMoney(sellingPrice);
    if (processingEl && !processingEl.value) processingEl.value = '';
    if (loanEl) loanEl.value = fmtMoney(loanAmount);
    if (downEl) downEl.value = fmtMoney(downpayment);
    if (reservationEl) reservationEl.value = fmtMoney(reservationFee || 20000);
    if (promoEl) {
      var promoAmount = (sellingPrice > 0 && promoDiscountRate > 0)
        ? (sellingPrice * (promoDiscountRate / 100))
        : 0;
      promoEl.value = promoAmount > 0 ? fmtMoney(promoAmount) : '';
    }

    var consentEl = document.getElementById('buyerConsentAccepted');
    if (consentEl && !existingPayload) consentEl.checked = false;
    var esigPickerEl = document.getElementById('buyerESignaturePicker');
    if (esigPickerEl) esigPickerEl.value = '';
    _pendingBuyerESignatureFile = null;

    var othersEl = document.getElementById('pbEmploymentOthers');
    if (othersEl) othersEl.required = false;

    if (freshMode) {
      _clearBuyerFormForFreshEntry();
      existingPayload = null;
      purchaseStatus = 'none';
    } else if (existingPayload) {
      var cleanPayload = _extractCurrentPurchasePayload(existingPayload);
      applyBuyerFormPayload(cleanPayload);
      var bankSelectEl = document.getElementById('pbBankName');
      if (bankSelectEl && typeof cleanPayload.pbBankName !== 'undefined') {
        bankSelectEl.dataset.pendingValue = String(cleanPayload.pbBankName || '').trim();
      }
      var sourceSel = document.getElementById('pbMailPref');
      var sourceVal = String((sourceSel && sourceSel.value) || '').toLowerCase();
      if (sourceVal === 'home' || sourceVal === 'residence') {
        setTimeout(_copyHomeAddressToMailing, 120);
      }
    }

    if (consentEl) {
      var hasConsentInPayload = !!(existingPayload && Object.prototype.hasOwnProperty.call(existingPayload, 'buyerConsentAccepted'));
      if (hasConsentInPayload) {
        consentEl.checked = !!existingPayload.buyerConsentAccepted;
      } else if (isSubmitted || forceReadOnly) {
        consentEl.checked = true;
      } else {
        consentEl.checked = false;
      }
    }

    _applyBuyerAutofillDefaults({ existingPayload: existingPayload, card: card, modal: modal });

    var statusBadgeEl = document.getElementById('buyerInfoPurchaseStatusBadge');
    if (statusBadgeEl) {
      statusBadgeEl.className = 'sqh-badge';
      if (purchaseStatus === 'approved') {
        statusBadgeEl.classList.add('badge-qualified');
        statusBadgeEl.textContent = 'Approved';
        statusBadgeEl.classList.remove('d-none');
      } else if (purchaseStatus === 'rejected') {
        statusBadgeEl.classList.add('badge-not-qualified');
        statusBadgeEl.textContent = 'Rejected';
        statusBadgeEl.classList.remove('d-none');
      } else {
        statusBadgeEl.classList.add('d-none');
        statusBadgeEl.textContent = '—';
      }
    }

    var submitNewBtn = document.getElementById('buyerSubmitNewFormBtn');
    if (submitNewBtn) {
      var showSubmitNew = purchaseStatus === 'rejected' && !freshMode;
      submitNewBtn.classList.toggle('d-none', !showSubmitNew);
      submitNewBtn.dataset.tripId = String(tripId || '');
    }

    _syncBuyerSpouseSections();

    var finalReadOnly = forceReadOnly || isSubmitted;
    setBuyerFormReadOnly(finalReadOnly);
    setBuyerFormSubmitButtonState(isSubmitted && !freshMode);
    if (!finalReadOnly) {
      _syncBuyerConditionalFieldStates();
    }

    bootstrap.Modal.getOrCreateInstance(modal).show();
    setTimeout(_applyDarkModalBackdrops, 0);
  }

  function _buyerInputIcon(labelText) {
    var t = String(labelText || '').toLowerCase();
    if (t.indexOf('citizenship') !== -1) return 'fas fa-flag';
    if (t.indexOf('gender') !== -1) return 'fas fa-venus-mars';
    if (t.indexOf('civil status') !== -1) return 'fas fa-heart';
    if (t.indexOf('dependent') !== -1) return 'fas fa-child';
    if (t.indexOf('facebook') !== -1) return 'fab fa-facebook-f';
    if (t.indexOf('instagram') !== -1) return 'fab fa-instagram';
    if (t.indexOf('twitter') !== -1) return 'fab fa-x-twitter';
    if (t.indexOf('viber') !== -1) return 'fab fa-viber';
    if (t.indexOf('whatsapp') !== -1) return 'fab fa-whatsapp';
    if (t.indexOf('employment') !== -1 || t.indexOf('occupation') !== -1 || t.indexOf('position') !== -1 || t.indexOf('tenure') !== -1) return 'fas fa-briefcase';
    if (t.indexOf('region') !== -1 || t.indexOf('province') !== -1 || t.indexOf('municipality') !== -1 || t.indexOf('brgy') !== -1 || t.indexOf('barangay') !== -1 || t.indexOf('city') !== -1) return 'fas fa-map-location-dot';
    if (t === 'no.' || t === 'no') return 'fas fa-hashtag';
    if (t.indexOf('e-mail') !== -1 || t.indexOf('email') !== -1) return 'fas fa-envelope';
    if (t.indexOf('date') !== -1 || t.indexOf('birthday') !== -1) return 'fas fa-calendar-day';
    if (t.indexOf('mobile') !== -1 || t.indexOf('telephone') !== -1 || t.indexOf('tel') !== -1) return 'fas fa-phone';
    if (t.indexOf('income') !== -1 || t.indexOf('price') !== -1 || t.indexOf('fee') !== -1 || t.indexOf('amount') !== -1 || t.indexOf('downpayment') !== -1) return 'fas fa-coins';
    if (t.indexOf('address') !== -1 || t.indexOf('street') !== -1 || t.indexOf('city') !== -1 || t.indexOf('province') !== -1 || t.indexOf('brgy') !== -1 || t.indexOf('municipality') !== -1 || t.indexOf('country') !== -1 || t.indexOf('zip') !== -1) return 'fas fa-location-dot';
    if (t.indexOf('name') !== -1 || t.indexOf('occupation') !== -1 || t.indexOf('position') !== -1) return 'fas fa-user';
    if (t.indexOf('tin') !== -1 || t.indexOf('umid') !== -1 || t.indexOf('sss') !== -1 || t.indexOf('gsis') !== -1 || t.indexOf('id') !== -1) return 'fas fa-id-card';
    return 'fas fa-circle-dot';
  }

  function decorateBuyerInfoInputs() {
    var form = document.getElementById('buyerInfoForm');
    if (!form) return;
    form.querySelectorAll('.buyer-icon-input').forEach(function (input) {
      if (input.closest('.input-group')) return;
      var parent = input.parentElement;
      if (!parent) return;
      var label = parent.querySelector('label.form-label');
      var iconClass = _buyerInputIcon(label ? label.textContent : '');
      var wrap = document.createElement('div');
      wrap.className = 'input-group sqh-input-group';
      var icon = document.createElement('span');
      icon.className = 'input-group-text sqh-ig-text';
      icon.innerHTML = '<i class="' + iconClass + '"></i>';
      parent.insertBefore(wrap, input);
      wrap.appendChild(icon);
      wrap.appendChild(input);
    });
  }

  function ensureBuyerFormPlaceholders() {
    var form = document.getElementById('buyerInfoForm');
    if (!form) return;

    form.querySelectorAll('input, textarea').forEach(function (el) {
      if (!el || !el.tagName) return;
      var type = String(el.type || '').toLowerCase();
      if (type === 'hidden' || type === 'button' || type === 'submit' || type === 'reset') return;
      if (el.placeholder && String(el.placeholder).trim()) return;
      var parent = el.closest('.col-12, .col-md-2, .col-md-3, .col-md-4, .col-md-6') || el.parentElement;
      var label = parent ? parent.querySelector('label.form-label') : null;
      var labelText = label ? String(label.textContent || '').replace(/\s+/g, ' ').trim() : '';
      if (!labelText) return;
      el.placeholder = 'Enter ' + labelText;
    });

    form.querySelectorAll('select').forEach(function (sel) {
      if (!sel || !sel.options || !sel.options.length) return;
      var first = sel.options[0];
      if (first && String(first.value || '') === '') first.textContent = '-- Select --';
    });
  }

  function collectBuyerFormPayload() {
    var form = document.getElementById('buyerInfoForm');
    var payload = {};
    if (!form) return payload;
    var autoSeen = {};
    function autoKeyFor(el) {
      var host = el.closest('.col-12, .col-md-2, .col-md-3, .col-md-4, .col-md-6, td') || el.parentElement;
      var labelEl = host ? host.querySelector('label.form-label') : null;
      var raw = String((labelEl && labelEl.textContent) || 'field').trim().toLowerCase();
      var base = raw.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'field';
      autoSeen[base] = (autoSeen[base] || 0) + 1;
      return 'auto_' + base + '_' + autoSeen[base];
    }
    form.querySelectorAll('input, select, textarea').forEach(function (el) {
      var key = el.name || el.id || autoKeyFor(el);
      if (!key) return;
      var type = String(el.type || '').toLowerCase();
      if (type === 'radio') {
        if (el.checked) payload[key] = el.value;
        return;
      }
      if (type === 'checkbox') {
        payload[key] = !!el.checked;
        return;
      }
      payload[key] = el.value;
    });
    return payload;
  }

  function uploadBuyerESignatureNow(tripId, file) {
    var fd = new FormData();
    fd.append('esignature', file);
    return _fetchJson('/trip/' + encodeURIComponent(tripId) + '/buyer-signature-upload', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      body: fd
    }).then(function (res) {
      if (!res.ok) throw new Error((res.data && res.data.error) || 'E-signature upload failed.');
      return res.data || {};
    });
  }

  decorateBuyerInfoInputs();
  ensureBuyerFormPlaceholders();
  _wireBuyerDependentsToggle();
  _initBuyerNumericGuards();
  _initBuyerBanks();
  _initBuyerCountryInputs();
  _wireEmploymentOthersToggle();
  _wireBuyerMailAddressSource();
  _wireBuyerSpouseToggle();

  (function initBuyerESignaturePicker() {
    var picker = document.getElementById('buyerESignaturePicker');
    var browseBtn = document.getElementById('buyerESignatureBrowseBtn');
    var fileText = document.getElementById('buyerESignatureFile');
    if (!picker || !browseBtn || !fileText) return;

    browseBtn.addEventListener('click', function () {
      picker.click();
    });

    picker.addEventListener('change', function () {
      var file = (picker.files && picker.files[0]) ? picker.files[0] : null;
      if (!file) return;
      _pendingBuyerESignatureFile = file;
      fileText.value = file.name || '';
      toast('E-signature selected. It will be uploaded when you submit.', 'info');
    });
  })();

  (function initBuyerFormSubmitFlow() {
    var submitBtn = document.getElementById('buyerFormSubmitBtn');
    var confirmBtn = document.getElementById('confirmBuyerFormSubmitBtn');
    var confirmModalEl = document.getElementById('buyerFormSubmitConfirmModal');
    if (!submitBtn || !confirmBtn || !confirmModalEl) return;

    submitBtn.addEventListener('click', function () {
      var buyerModal = document.getElementById('buyerInfoModal');
      if (buyerModal && buyerModal.dataset.readOnly === '1') {
        toast('View-only mode: submitted purchase form.', 'info');
        return;
      }
      if (submitBtn.disabled || String(submitBtn.dataset.submitted || '0') === '1') {
        toast('View-only mode: submitted purchase form.', 'info');
        return;
      }
      var tripId = (document.getElementById('buyerSubmitTripId') || {}).value || '';
      if (!tripId) {
        toast('Trip reference is missing. Please reopen the buyer form from a trip card.', 'warning');
        return;
      }
      var consentAccepted = !!((document.getElementById('buyerConsentAccepted') || {}).checked);
      if (!consentAccepted) {
        toast('Please agree to the Consent & Authorization Clause before submitting.', 'warning');
        return;
      }
      var esigValue = String(((document.getElementById('buyerESignatureFile') || {}).value || '')).trim();
      if (!_pendingBuyerESignatureFile && !esigValue) {
        toast('Please upload your e-signature photo before submitting.', 'warning');
        return;
      }
      bootstrap.Modal.getOrCreateInstance(confirmModalEl).show();
      setTimeout(function () {
        _applyDarkModalBackdrops();
        var buyerModalEl = document.getElementById('buyerInfoModal');
        if (buyerModalEl) buyerModalEl.style.zIndex = '1060';
        confirmModalEl.style.zIndex = '1090';
        var backdrops = document.querySelectorAll('.modal-backdrop');
        if (backdrops.length) backdrops[backdrops.length - 1].style.zIndex = '1080';
      }, 0);
    });

    confirmBtn.addEventListener('click', async function () {
      var tripId = (document.getElementById('buyerSubmitTripId') || {}).value || '';
      if (!tripId) {
        toast('Trip reference is missing. Please reopen the buyer form from a trip card.', 'warning');
        return;
      }

      confirmBtn.disabled = true;
      var oldHtml = confirmBtn.innerHTML;
      confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Submitting...';
      try {
        if (_pendingBuyerESignatureFile) {
          var uploadData = await uploadBuyerESignatureNow(tripId, _pendingBuyerESignatureFile);
          var esigField = document.getElementById('buyerESignatureFile');
          if (esigField) esigField.value = (uploadData.filename || _pendingBuyerESignatureFile.name || '').trim();
          var esigPicker = document.getElementById('buyerESignaturePicker');
          if (esigPicker) esigPicker.value = '';
          _pendingBuyerESignatureFile = null;
        }

        var payloadSnapshot = collectBuyerFormPayload();
        payloadSnapshot.buyerConsentAccepted = !!((document.getElementById('buyerConsentAccepted') || {}).checked);
        var bankSelectEl = document.getElementById('pbBankName');
        if (bankSelectEl && !payloadSnapshot.pbBankName) {
          payloadSnapshot.pbBankName = String(bankSelectEl.dataset.pendingValue || '').trim();
        }
        var data = await postJSON('/trip/' + encodeURIComponent(tripId) + '/buyer-form-submit', {
          consent_accepted: !!((document.getElementById('buyerConsentAccepted') || {}).checked),
          form_data: payloadSnapshot,
        });
        if (!data || !data.ok) {
          toast((data && data.error) || 'Unable to submit buyer form.', 'danger');
          return;
        }

        bootstrap.Modal.getInstance(confirmModalEl)?.hide();
        bootstrap.Modal.getInstance(document.getElementById('buyerInfoModal'))?.hide();
        toast('Buyer Information Form submitted to Admin.', 'success');
        setTripPurchaseSubmittedUi(tripId, payloadSnapshot);
      } catch (_) {
        toast('Network error while submitting buyer form.', 'danger');
      } finally {
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = oldHtml;
      }
    });
  })();

  (function initBuyerNewFormSubmitFlow() {
    var newBtn = document.getElementById('buyerSubmitNewFormBtn');
    var confirmModalEl = document.getElementById('buyerNewPurchaseFormConfirmModal');
    var confirmBtn = document.getElementById('confirmBuyerNewPurchaseFormBtn');
    if (!newBtn || !confirmModalEl || !confirmBtn) return;

    newBtn.addEventListener('click', function () {
      bootstrap.Modal.getOrCreateInstance(confirmModalEl).show();
    });

    confirmBtn.addEventListener('click', function () {
      var tripId = newBtn.dataset.tripId || (document.getElementById('buyerSubmitTripId') || {}).value || '';
      if (!tripId) {
        toast('Trip reference is missing. Please reopen the form.', 'warning');
        return;
      }
      bootstrap.Modal.getInstance(confirmModalEl)?.hide();
      openBuyerInfoModal(tripId, { fresh: true, readOnly: false });
    });
  })();

  function showTripPreviewSlide(idx) {
    var imgEl = document.getElementById('tripPreviewImg');
    if (!imgEl || !_tripPreviewImages.length) return;
    _tripPreviewIdx = (idx + _tripPreviewImages.length) % _tripPreviewImages.length;
    // Swap only after the target decodes (no fade-to-blank); neighbors preload.
    SqhImageCache.swap(imgEl, _tripPreviewImages[_tripPreviewIdx], function () { imgEl.style.opacity = '1'; });
    SqhImageCache.preloadNeighbors(_tripPreviewImages, _tripPreviewIdx);
    document.querySelectorAll('#tripPreviewDots .sub-preview-dot').forEach(function(dot, dotIdx) {
      dot.classList.toggle('active', dotIdx === _tripPreviewIdx);
    });
  }

  function openTripPreview(card) {
    if (!card) return;
    var modal = document.getElementById('tripPreviewModal');
    if (!modal) return;

    var tripId = card.dataset.tripId || '';
    var name = card.dataset.tripName || '—';
    var location = card.dataset.tripLocation || '—';
    var status = card.dataset.tripStatus || 'pending';
    var canContinuePurchase = String(card.dataset.tripCanPurchase || '0') === '1';
    var isPurchaseFormSubmitted = String(card.dataset.tripPurchaseFormSubmitted || '0') === '1';
    var assignedAgent = String(card.dataset.tripAgent || '').trim();
    var date = card.dataset.tripDate || '—';
    var time = card.dataset.tripTime || 'Not set';
    var submitted = card.dataset.tripSubmitted || '—';
    var note = card.dataset.tripNote || '';
    var images = (card.dataset.tripImages || '').split(',').map(function(item) { return item.trim(); }).filter(Boolean);

    modal.dataset.tripId = tripId;
    modal.dataset.tripStatus = status;
    document.getElementById('tripPreviewName').textContent = name;
    document.getElementById('tripPreviewLocation').innerHTML = '<i class="fas fa-map-marker-alt me-2" style="color:var(--clr-primary);"></i>' + location;
    document.getElementById('tripPreviewStatusBadge').innerHTML = tripStatusBadge(status);
    var dateLabelEl = document.getElementById('tripPreviewDateLabel');
    var timeLabelEl = document.getElementById('tripPreviewTimeLabel');
    var isVisited = String(status || '').toLowerCase() === 'visited';
    if (dateLabelEl) dateLabelEl.textContent = isVisited ? 'Visitted Date' : 'Visit Date';
    if (timeLabelEl) timeLabelEl.textContent = isVisited ? 'Visitted Time' : 'Visit Time';
    document.getElementById('tripPreviewDate').textContent = date;
    document.getElementById('tripPreviewTime').textContent = time || 'Not set';
    document.getElementById('tripPreviewSubmitted').textContent = submitted;
    var assignedAgentCard = document.getElementById('tripPreviewAgentCard');
    var assignedAgentEl = document.getElementById('tripPreviewAgent');
    if (assignedAgentCard && assignedAgentEl) {
      var normalizedStatus = String(status || '').toLowerCase();
      if ((normalizedStatus === 'approved' || normalizedStatus === 'visited' || normalizedStatus === 'sold') && assignedAgent) {
        assignedAgentEl.textContent = assignedAgent;
        assignedAgentCard.classList.remove('d-none');
      } else {
        assignedAgentEl.textContent = '—';
        assignedAgentCard.classList.add('d-none');
      }
    }

    var continueBtn = document.getElementById('tripPreviewContinueBtn');
    if (continueBtn) {
      var isSoldStatus = String(status || '').toLowerCase() === 'sold';
      var hasPurchasePayload = !!String(card.dataset.tripPurchaseFormData || '').trim();
      var canViewSubmitted = isPurchaseFormSubmitted || (isSoldStatus && hasPurchasePayload);
      if (canContinuePurchase || canViewSubmitted) {
        continueBtn.dataset.tripId = tripId;
        continueBtn.dataset.purchaseFormSubmitted = canViewSubmitted ? '1' : '0';
        continueBtn.disabled = false;
        continueBtn.classList.remove('opacity-50');
        if (canViewSubmitted) {
          continueBtn.classList.remove('btn-outline-lime');
          continueBtn.classList.add('btn-outline-blue');
          continueBtn.innerHTML = '<i class="fas fa-eye me-1"></i> View Purchase Form';
        } else {
          continueBtn.classList.remove('btn-outline-lime');
          continueBtn.classList.add('btn-outline-blue');
          continueBtn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Continue to Purchase';
        }
        continueBtn.classList.remove('d-none');
      } else {
        continueBtn.dataset.tripId = '';
        continueBtn.dataset.purchaseFormSubmitted = '0';
        continueBtn.classList.remove('opacity-50');
        continueBtn.classList.add('d-none');
      }
    }

    var noteWrap = document.getElementById('tripPreviewNoteWrap');
    var noteEl = document.getElementById('tripPreviewNote');
    if (note) {
      noteEl.textContent = note;
      noteWrap.classList.remove('d-none');
    } else {
      noteWrap.classList.add('d-none');
      noteEl.textContent = '';
    }

    var cancelBtn = document.getElementById('tripPreviewCancelBtn');
    if (cancelBtn) {
      cancelBtn.dataset.tripId = tripId;
      var isSoldPreview = String(status || '').toLowerCase() === 'sold';
      cancelBtn.classList.toggle('d-none', isSoldPreview);
      cancelBtn.disabled = isSoldPreview;
    }

    _tripPreviewImages = images;
    var imgWrap = document.getElementById('tripPreviewImgWrap');
    var placeholder = document.getElementById('tripPreviewPlaceholder');
    var prevBtn = document.getElementById('tripPreviewPrev');
    var nextBtn = document.getElementById('tripPreviewNext');
    var dotsEl = document.getElementById('tripPreviewDots');
    var imgEl = document.getElementById('tripPreviewImg');

    if (_tripPreviewImages.length) {
      imgWrap.style.display = 'block';
      placeholder.style.display = 'none';
      // Swap only after decode; warm the cache for instant next/back.
      SqhImageCache.swap(imgEl, _tripPreviewImages[0], function () { imgEl.style.opacity = '1'; });
      SqhImageCache.preloadAll(_tripPreviewImages);
      _tripPreviewIdx = 0;
      if (_tripPreviewImages.length > 1) {
        prevBtn.classList.remove('d-none');
        nextBtn.classList.remove('d-none');
        dotsEl.innerHTML = _tripPreviewImages.map(function(_, index) {
          return '<span class="sub-preview-dot' + (index === 0 ? ' active' : '') + '" data-idx="' + index + '"></span>';
        }).join('');
        dotsEl.querySelectorAll('.sub-preview-dot').forEach(function(dot) {
          dot.addEventListener('click', function() {
            showTripPreviewSlide(parseInt(this.dataset.idx, 10));
          });
        });
      } else {
        prevBtn.classList.add('d-none');
        nextBtn.classList.add('d-none');
        dotsEl.innerHTML = '';
      }
    } else {
      imgWrap.style.display = 'none';
      placeholder.style.display = 'flex';
      prevBtn.classList.add('d-none');
      nextBtn.classList.add('d-none');
      dotsEl.innerHTML = '';
    }

    bootstrap.Modal.getOrCreateInstance(modal).show();
    setTimeout(_applyDarkModalBackdrops, 0);
  }

  function _setRequestedButtonState(btn) {
    if (!btn) return;
    btn.dataset.requested = '1';
    btn.classList.remove('btn-lime');
    btn.classList.add('btn-outline-crimson');
    btn.innerHTML = '<i class="fas fa-eye me-1"></i> View Request';
  }

  function markPropertyAsRequested(propId) {
    document.querySelectorAll('.prop-card-clickable[data-prop-id="' + propId + '"]').forEach(function(card) {
      card.dataset.hasRequest = '1';
      _setRequestedButtonState(card.querySelector('.prop-card-request-btn'));
    });
    if (String(_pdmPropId || '') === String(propId)) {
      var reqBtn = document.getElementById('pvmRequestTripBtn');
      if (reqBtn) {
        reqBtn.dataset.mode = 'view';
        reqBtn.classList.remove('btn-lime');
        reqBtn.classList.add('btn-outline-crimson');
        reqBtn.innerHTML = '<i class="fas fa-eye me-1"></i> View Request';
      }
    }
  }

  function _pvmShowSlide(idx) {
    var imgEl = document.getElementById('pvmImg');
    if (!imgEl || !_pvmImages.length) return;
    _pvmIdx = (idx + _pvmImages.length) % _pvmImages.length;
    // Swap only after the target decodes (no fade-to-blank); neighbors preload.
    SqhImageCache.swap(imgEl, _pvmImages[_pvmIdx], function () { imgEl.style.opacity = '1'; });
    SqhImageCache.preloadNeighbors(_pvmImages, _pvmIdx);
    document.querySelectorAll('#pvmDots .sub-preview-dot').forEach(function(d, i) {
      d.classList.toggle('active', i === _pvmIdx);
    });
  }

  function formatPhp(value) {
    var n = _toNumber(value || 0);
    return '\u20b1' + n.toLocaleString('en-PH', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  }

  function formatPercent(value, fixed) {
    var n = Number(value || 0);
    return n.toFixed(typeof fixed === 'number' ? fixed : 2) + '%';
  }

  function renderPricingBreakdown(detailStatus, pricingData) {
    var panel = document.getElementById('pvmPricingBreakdown');
    var hint = document.getElementById('pvmPricingRequestHint');
    if (!panel || !hint) return;

    var isApproved = String(detailStatus || '').toLowerCase() === 'approved';
    panel.classList.toggle('d-none', !isApproved);
    hint.classList.toggle('d-none', isApproved);

    if (!isApproved || !pricingData || typeof pricingData !== 'object') return;

    var setText = function(id, val) {
      var el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    var downRate = Number(pricingData.downpayment_rate || pricingData.down_payment_rate || 0);
    var vatRate = Number(pricingData.vat_rate || 0);
    var lmfRate = Number(pricingData.lmf_rate || 0);
    var downMonths = Number(pricingData.downpayment_terms_months || pricingData.equity_months || 0);
    var loanableRate = Number(pricingData.loanable_percentage || 0);
    var processingFee = String(pricingData.loanProcessingFee || pricingData.loan_processing_fee || '').trim();
    var orPrNo = String(pricingData.loanOrPrNo || pricingData.loan_or_pr_no || '').trim();
    var orPrDate = String(pricingData.loanOrPrDate || pricingData.loan_or_pr_date || '').trim();
    var downpaymentTerm = String(pricingData.loanDownpaymentTerm || pricingData.loan_downpayment_term || '').trim();
    var loanTerm = String(pricingData.loanTerm || pricingData.loan_term || '').trim();
    var amort = pricingData.amortization || {};
    var reqIncome = pricingData.required_monthly_income || {};
    var clientIncome = _getClientMonthlyIncome();

    setText('pvmTotalSellingPrice', formatPhp(pricingData.total_selling_price || pricingData.tcp));
    setText('pvmPromoDiscountRate', formatPercent(pricingData.promo_discount_rate || 0, 2));
    setText('pvmNetSellingPrice', 'Net Selling: ' + formatPhp(pricingData.net_selling_price || 0));
    setText('pvmVatAmount', formatPhp(pricingData.vat_amount || 0));
    setText('pvmVatRate', formatPercent(vatRate, 2));
    setText('pvmLmfAmount', formatPhp(pricingData.lmf_amount || 0));
    setText('pvmLmfRate', formatPercent(lmfRate, 2));
    setText('pvmTotalContractPrice', formatPhp(pricingData.total_contract_price || pricingData.fully_computed_house_price || 0));
    setText('pvmReservationFee', formatPhp(pricingData.reservation_fee || 0));
    setText('pvmTotalDownpayment', formatPhp(pricingData.total_downpayment || pricingData.down_payment || 0));
    setText('pvmDownpaymentRate', formatPercent(downRate, 2));
    setText('pvmMonthlyDownpayment', formatPhp(pricingData.monthly_downpayment || pricingData.equity_monthly || 0));
    setText('pvmDownpaymentTerms', downMonths + ' months');
    setText('pvmTotalLoanableAmount', formatPhp(pricingData.total_loanable_amount || pricingData.financed_amount || 0));
    setText('pvmLoanableRate', formatPercent(loanableRate, 2));
    setText('pvmRate', formatPercent(pricingData.annual_interest_rate || 0, 2));
    setText('pvmAmort5', formatPhp(amort['5'] || 0));
    setText('pvmAmort10', formatPhp(amort['10'] || 0));
    setText('pvmAmort15', formatPhp(amort['15'] || 0));
    setText('pvmAmort20', formatPhp(amort['20'] || 0));
    setText('pvmReqIncome5', formatPhp(reqIncome['5'] || 0));
    setText('pvmReqIncome10', formatPhp(reqIncome['10'] || 0));
    setText('pvmReqIncome15', formatPhp(reqIncome['15'] || 0));
    setText('pvmReqIncome20', formatPhp(reqIncome['20'] || 0));
    _setBadgeStatus(
      document.getElementById('pvmReqIncomeBadge5'),
      _computeQualificationStatus(reqIncome['5'] || 0, clientIncome, false)
    );
    _setBadgeStatus(
      document.getElementById('pvmReqIncomeBadge10'),
      _computeQualificationStatus(reqIncome['10'] || 0, clientIncome, false)
    );
    _setBadgeStatus(
      document.getElementById('pvmReqIncomeBadge15'),
      _computeQualificationStatus(reqIncome['15'] || 0, clientIncome, false)
    );
    _setBadgeStatus(
      document.getElementById('pvmReqIncomeBadge20'),
      _computeQualificationStatus(reqIncome['20'] || 0, clientIncome, false)
    );
  }

  function openPropDetail(card, options) {
    options = options || {};
    var modal = document.getElementById('propDetailModal');
    if (!modal) return;

    _pdmPropId = card.dataset.propId || null;
    var name    = card.dataset.propName    || '';
    var loc     = card.dataset.propLoc     || '';
    var type    = card.dataset.propType    || '';
    var unitType = card.dataset.propUnitType || '';
    var price   = parseFloat(card.dataset.propPrice || 0);
    var beds    = card.dataset.propBeds;
    var baths   = card.dataset.propBaths;
    var storeys = card.dataset.propStoreys;
    var floor   = card.dataset.propFloor;
    var lot     = card.dataset.propLot;
    var desc    = card.dataset.propDesc    || '';
    var subdivision = card.dataset.propSubdivision || '';
    var listingStatus = card.dataset.propListingStatus || '';
    var dateAdded = card.dataset.propAdded || '';
    var dateSold = card.dataset.soldAt || '';
    var isBoughtModel = options.bought === true || String(card.dataset.bought || '0') === '1';
    var forceSold = options.forceSold === true || isBoughtModel;
    var hasRequest = String(card.dataset.hasRequest || '0') === '1';
    var isVisitedModel = String(card.dataset.visited || '0') === '1';
    var detailStatus = String(card.dataset.detailRequestStatus || 'none').toLowerCase();
    var effectiveDetailStatus = isBoughtModel ? 'approved' : detailStatus;
    if (detailStatus === 'approved' && !isBoughtModel && card.dataset.propId) {
      var _checkPid = card.dataset.propId;
      fetch('/qualify/property/' + encodeURIComponent(_checkPid) + '/detail-status')
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d && d.ok && !d.approved) {
            document.querySelectorAll('.prop-card-clickable[data-prop-id="' + _checkPid + '"]').forEach(function(el) {
              el.dataset.detailRequestStatus = 'none';
              el.dataset.pricingApproved = '0';
              var btn = el.querySelector('.prop-card-details-btn');
              if (btn) {
                btn.dataset.detailStatus = 'none';
                btn.className = 'btn btn-outline-blue w-100 mt-2 prop-card-details-btn';
                btn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Request Full Details';
              }
            });
            renderPricingBreakdown('none', null);
            var detailsBtn = document.getElementById('pvmRequestDetailsBtn');
            if (detailsBtn) {
              detailsBtn.dataset.detailStatus = 'none';
              detailsBtn.className = 'btn btn-outline-blue';
              detailsBtn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Request Full Details';
              detailsBtn.classList.remove('d-none');
            }
          }
        })
        .catch(function() {});
    }
    var pricingData = null;
    try {
      pricingData = card.dataset.pricingJson ? JSON.parse(card.dataset.pricingJson) : null;
    } catch (_) {
      pricingData = null;
    }
    if ((isBoughtModel || forceSold) && (!pricingData || typeof pricingData !== 'object' || !Object.keys(pricingData).length)) {
      var basePrice = Number(card.dataset.propPrice || 0);
      var soldPrice = Number(card.dataset.salePrice || 0);
      var fallbackTotal = soldPrice > 0 ? soldPrice : basePrice;
      var fallbackPromoRate = Number(card.dataset.propPromoDiscountRate || 0);
      var fallbackReservationFee = Number(card.dataset.propReservationFee || 0);
      var fallbackDownRate = Number(card.dataset.propDownpaymentRate || 0);
      var fallbackDownMonths = Number(card.dataset.propDownpaymentTermsMonths || 0);
      var fallbackLoanableRate = Number(card.dataset.propLoanablePercentage || 0);
      var fallbackVatRate = Number(card.dataset.propVatRate || 0);
      var fallbackLmfRate = Number(card.dataset.propLmfRate || 0);
      var fallbackNet = fallbackTotal * (1 - (fallbackPromoRate / 100));
      var fallbackVatAmount = fallbackNet * (fallbackVatRate / 100);
      var fallbackLmfAmount = fallbackNet * (fallbackLmfRate / 100);
      var fallbackContractPrice = fallbackNet + fallbackVatAmount + fallbackLmfAmount;
      var fallbackTotalDown = fallbackContractPrice * (fallbackDownRate / 100);
      var fallbackMonthlyDown = fallbackDownMonths > 0
        ? Math.max(fallbackTotalDown - fallbackReservationFee, 0) / fallbackDownMonths
        : 0;
      var fallbackLoanableAmount = fallbackContractPrice * (fallbackLoanableRate / 100);
      pricingData = {
        total_selling_price: fallbackTotal,
        promo_discount_rate: fallbackPromoRate,
        net_selling_price: fallbackNet,
        vat_rate: fallbackVatRate,
        vat_amount: fallbackVatAmount,
        lmf_rate: fallbackLmfRate,
        lmf_amount: fallbackLmfAmount,
        total_contract_price: fallbackContractPrice,
        reservation_fee: fallbackReservationFee,
        downpayment_rate: fallbackDownRate,
        total_downpayment: fallbackTotalDown,
        downpayment_terms_months: fallbackDownMonths,
        monthly_downpayment: fallbackMonthlyDown,
        loanable_percentage: fallbackLoanableRate,
        total_loanable_amount: fallbackLoanableAmount,
        annual_interest_rate: 0,
        amortization: { '5': 0, '10': 0, '15': 0, '20': 0 },
        required_monthly_income: { '5': 0, '10': 0, '15': 0, '20': 0 }
      };
    }
    var imgs    = (card.dataset.propImages || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean);

    function titleCase(str) {
      return str ? str.replace(/-/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); }) : '—';
    }

    function unitTypeBadgeClass(value) {
      var key = String(value || '').trim().toLowerCase();
      if (key === 'pre-selling') return 'prop-type-badge-pre-selling';
      if (key === 'ready-for-occupancy') return 'prop-type-badge-ready-for-occupancy';
      if (key === 'resale') return 'prop-type-badge-resale';
      return '';
    }

    document.getElementById('pvmName').textContent = name || '—';
    document.getElementById('pvmLocation').innerHTML =
      '<i class="fas fa-map-marker-alt me-2" style="color:var(--clr-primary);"></i>' + (loc || '—');
    var status = forceSold ? 'sold' : (listingStatus ? String(listingStatus).trim().toLowerCase() : '');
    var listingBadgeClass = status === 'sold' ? 'badge-sold' : 'badge-available';
    var listingBadgeLabel = status === 'sold' ? 'Sold' : 'Available';
    var listingBadgeHtml = '<span class="sqh-badge ' + listingBadgeClass + '">' + listingBadgeLabel + '</span>';
    var typeBadgeHtml = unitType
      ? '<span class="prop-card-type ' + unitTypeBadgeClass(unitType) + '">' + titleCase(unitType) + '</span>'
      : '';
    document.getElementById('pvmStatusBadge').innerHTML =
      '<div class="d-inline-flex align-items-center gap-2">' + listingBadgeHtml + typeBadgeHtml + '</div>';

    // Images / carousel
    _pvmImages = imgs;
    var imgWrap   = document.getElementById('pvmImgWrap');
    var imgHolder = document.getElementById('pvmImgPlaceholder');
    var prevBtn   = document.getElementById('pvmPrev');
    var nextBtn   = document.getElementById('pvmNext');
    var dotsEl    = document.getElementById('pvmDots');
    var imgEl     = document.getElementById('pvmImg');

    if (_pvmImages.length) {
      imgWrap.style.display   = 'block';
      imgHolder.style.display = 'none';
      // Swap only after decode; warm the cache for instant next/back.
      SqhImageCache.swap(imgEl, _pvmImages[0], function () { imgEl.style.opacity = '1'; });
      SqhImageCache.preloadAll(_pvmImages);
      _pvmIdx = 0;
      if (_pvmImages.length > 1) {
        prevBtn.classList.remove('d-none');
        nextBtn.classList.remove('d-none');
        dotsEl.innerHTML = _pvmImages.map(function(_, i) {
          return '<span class="sub-preview-dot' + (i === 0 ? ' active' : '') + '" data-idx="' + i + '"></span>';
        }).join('');
        dotsEl.querySelectorAll('.sub-preview-dot').forEach(function(dot) {
          dot.addEventListener('click', function() { _pvmShowSlide(parseInt(this.dataset.idx)); });
        });
      } else {
        prevBtn.classList.add('d-none');
        nextBtn.classList.add('d-none');
        dotsEl.innerHTML = '';
      }
    } else {
      imgWrap.style.display   = 'none';
      imgHolder.style.display = 'flex';
      if (prevBtn) prevBtn.classList.add('d-none');
      if (nextBtn) nextBtn.classList.add('d-none');
      if (dotsEl)  dotsEl.innerHTML = '';
    }

    // Icon chips (matching card chip colour scheme)
    var chips = '';
    if (beds)    chips += '<span class="pvm-icon-chip"><i class="fas fa-bed chip-icon-lime"></i> '    + beds    + ' Bed'  + (beds    > 1 ? 's' : '') + '</span>';
    if (baths)   chips += '<span class="pvm-icon-chip"><i class="fas fa-bath chip-icon-blue"></i> '   + baths   + ' Bath' + (baths   > 1 ? 's' : '') + '</span>';
    if (storeys) chips += '<span class="pvm-icon-chip"><i class="fas fa-layer-group chip-icon-crimson"></i> ' + storeys + ' Stor' + (storeys > 1 ? 'eys' : 'ey') + '</span>';
    if (floor)   chips += '<span class="pvm-icon-chip"><i class="fas fa-ruler-combined"></i> '  + Math.round(floor) + ' sqm floor</span>';
    if (lot)     chips += '<span class="pvm-icon-chip"><i class="fas fa-vector-square"></i> '   + Math.round(lot)   + ' sqm lot</span>';
    document.getElementById('pvmIconChips').innerHTML = chips;

    // Details grid
    function pvmField(label, value, full) {
      var val = (value !== undefined && value !== null && value !== '') ? value : '—';
      var col = full ? 'col-12' : 'col-6 col-md-4';
      return '<div class="' + col + '">'
        + '<div class="pvm-detail-label">' + label + '</div>'
        + '<div class="pvm-detail-value">' + val + '</div>'
        + '</div>';
    }
    var detailHtml = '';
    detailHtml += pvmField('Model Type', titleCase(type));
    detailHtml += pvmField('Unit Type', titleCase(unitType));
    detailHtml += pvmField('TCP', price ? '₱' + price.toLocaleString('en-PH', {maximumFractionDigits:0}) : '—');
    detailHtml += pvmField('Project', subdivision || '—');
    detailHtml += pvmField('Listing Status', status ? titleCase(status) : '—');
    detailHtml += pvmField(isBoughtModel ? 'Date Sold' : 'Date Added', isBoughtModel ? (dateSold || '—') : (dateAdded || '—'));
    document.getElementById('pvmDetails').innerHTML = detailHtml;

    // Description
    var descWrapper = document.getElementById('pvmDescWrapper');
    var descEl      = document.getElementById('pvmDescription');
    if (desc) {
      descEl.textContent = desc;
      descWrapper.classList.remove('d-none');
    } else {
      descWrapper.classList.add('d-none');
    }

    var reqBtn = document.getElementById('pvmRequestTripBtn');
    if (reqBtn) {
      if (isBoughtModel) {
        reqBtn.classList.add('d-none');
        reqBtn.dataset.mode = '';
      } else {
        reqBtn.classList.remove('d-none');
        reqBtn.dataset.mode = isVisitedModel ? 'visit' : (hasRequest ? 'view' : 'request');
        reqBtn.classList.remove('btn-lime', 'btn-outline-crimson', 'btn-outline-blue');
        if (isVisitedModel) {
          reqBtn.classList.add('btn-outline-blue');
          reqBtn.innerHTML = '<i class="fas fa-calendar-check me-1"></i> View Visit';
        } else if (hasRequest) {
          reqBtn.classList.add('btn-outline-crimson');
          reqBtn.innerHTML = '<i class="fas fa-eye me-1"></i> View Request';
        } else {
          reqBtn.classList.add('btn-lime');
          reqBtn.innerHTML = '<i class="fas fa-calendar-plus me-1"></i> Request Visit';
        }
      }
    }

    var detailsBtn = document.getElementById('pvmRequestDetailsBtn');
    if (detailsBtn) {
      detailsBtn.dataset.detailStatus = effectiveDetailStatus;
      detailsBtn.classList.remove('d-none');
      detailsBtn.classList.remove('btn-outline-blue', 'btn-outline-crimson', 'btn-outline-lime');
      if (isBoughtModel || effectiveDetailStatus === 'approved') {
        detailsBtn.classList.add('d-none');
      } else if (effectiveDetailStatus === 'pending') {
        detailsBtn.classList.add('btn-outline-crimson');
        detailsBtn.innerHTML = '<i class="fas fa-hourglass-half me-1"></i> Details Requested';
      } else if (effectiveDetailStatus === 'rejected') {
        detailsBtn.classList.add('btn-outline-blue');
        detailsBtn.innerHTML = '<i class="fas fa-redo me-1"></i> Request Full Details Again';
      } else {
        detailsBtn.classList.add('btn-outline-blue');
        detailsBtn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Request Full Details';
      }
    }

    renderPricingBreakdown(effectiveDetailStatus, pricingData);

    // ── Qualification by selected loan year ──
    var qualWrap = document.getElementById('pvmQualWrap');
    var qualYearSel = document.getElementById('pvmQualYearSelect');
    var qualBadge = document.getElementById('pvmQualStatusBadge');
    var condBtn = document.getElementById('pvmConditionalBtn');
    var agentContact = null;
    try {
      agentContact = card.dataset.agentContact ? JSON.parse(card.dataset.agentContact) : null;
    } catch (_) {
      agentContact = null;
    }

    function updatePvmQualification() {
      if (!qualWrap || !qualYearSel || !qualBadge) return;
      var clientIncome = _getClientMonthlyIncome();
      if (!pricingData || !pricingData.required_monthly_income || clientIncome <= 0) {
        qualWrap.classList.add('d-none');
        return;
      }
      var year = qualYearSel.value;
      var req = _toNumber((pricingData.required_monthly_income[year] || 0));
      var status = _computeQualificationStatus(req, clientIncome, false);
      _setBadgeStatus(qualBadge, status);
      if (condBtn) condBtn.classList.toggle('d-none', status.badgeClass !== 'badge-conditional');
      qualWrap.classList.remove('d-none');
    }
    if (qualYearSel) qualYearSel.onchange = updatePvmQualification;
    if (condBtn) {
      condBtn.onclick = function () {
        openConditionalRequirements(agentContact);
      };
    }
    updatePvmQualification();

    bootstrap.Modal.getOrCreateInstance(modal).show();
    setTimeout(_applyDarkModalBackdrops, 0);
  }

  function openConditionalRequirements(agentContact) {
    var modalEl = document.getElementById('conditionalReqModal');
    if (!modalEl) return;
    var ac = (agentContact && typeof agentContact === 'object') ? agentContact : {};
    var phone = String(ac.phone || '').trim();
    var agentName = String(ac.name || 'Ann Regar').trim();

    var nameEl = modalEl.querySelector('#crAgentName');
    if (nameEl) nameEl.textContent = agentName;

    // Synchronize contact card links & values
    var fbCard = modalEl.querySelector('#crContactFbCard');
    if (fbCard) {
      fbCard.href = 'https://web.facebook.com/annregarrealty1017/?_rdc=1&_rdr#';
    }

    var emailCard = modalEl.querySelector('#crContactEmailCard');
    if (emailCard) {
      emailCard.href = 'mailto:annregar.realty@gmail.com';
    }

    var phoneCard = modalEl.querySelector('#crContactPhoneCard');
    var phoneValEl = modalEl.querySelector('#crContactPhoneVal');
    var activePhone = phone || '0917 861 7688';
    if (phoneCard) {
      var phoneDigits = activePhone.replace(/\D/g, '');
      phoneCard.href = 'tel:' + (phoneDigits ? phoneDigits : '09178617688');
    }
    if (phoneValEl) {
      phoneValEl.textContent = activePhone;
    }

    bootstrap.Modal.getOrCreateInstance(modalEl).show();
  }

  function _bvmShowSlide(idx) {
    var imgEl = document.getElementById('bvmImg');
    if (!imgEl || !_bvmImages.length) return;
    _bvmIdx = (idx + _bvmImages.length) % _bvmImages.length;
    // Swap only after the target decodes (no fade-to-blank); neighbors preload.
    SqhImageCache.swap(imgEl, _bvmImages[_bvmIdx], function () { imgEl.style.opacity = '1'; });
    SqhImageCache.preloadNeighbors(_bvmImages, _bvmIdx);
    document.querySelectorAll('#bvmDots .sub-preview-dot').forEach(function(d, i) {
      d.classList.toggle('active', i === _bvmIdx);
    });
  }

  function openBoughtPropDetail(card) {
    openPropDetail(card, { bought: true, forceSold: true });
  }

  function renderBoughtPricingBreakdown(pricingData) {
    var setText = function(id, val) {
      var el = document.getElementById(id);
      if (el) el.textContent = val;
    };
    if (!pricingData || typeof pricingData !== 'object') {
      [
        'bvmTotalSellingPrice', 'bvmPromoDiscountRate', 'bvmNetSellingPrice', 'bvmVatAmount', 'bvmVatRate',
        'bvmLmfAmount', 'bvmLmfRate', 'bvmTotalContractPrice', 'bvmReservationFee', 'bvmTotalDownpayment',
        'bvmDownpaymentRate', 'bvmMonthlyDownpayment', 'bvmDownpaymentTerms', 'bvmTotalLoanableAmount',
        'bvmLoanableRate', 'bvmRate', 'bvmAmort5', 'bvmAmort10', 'bvmAmort15', 'bvmAmort20',
        'bvmReqIncome5', 'bvmReqIncome10', 'bvmReqIncome15', 'bvmReqIncome20'
      ].forEach(function (id) { setText(id, '—'); });
      return;
    }

    var downRate = Number(pricingData.downpayment_rate || pricingData.down_payment_rate || 0);
    var vatRate = Number(pricingData.vat_rate || 0);
    var lmfRate = Number(pricingData.lmf_rate || 0);
    var downMonths = Number(pricingData.downpayment_terms_months || pricingData.equity_months || 0);
    var loanableRate = Number(pricingData.loanable_percentage || 0);
    var amort = pricingData.amortization || {};
    var reqIncome = pricingData.required_monthly_income || {};

    setText('bvmTotalSellingPrice', formatPhp(pricingData.total_selling_price || pricingData.tcp));
    setText('bvmPromoDiscountRate', formatPercent(pricingData.promo_discount_rate || 0, 2));
    setText('bvmNetSellingPrice', 'Net Selling: ' + formatPhp(pricingData.net_selling_price || 0));
    setText('bvmVatAmount', formatPhp(pricingData.vat_amount || 0));
    setText('bvmVatRate', formatPercent(vatRate, 2));
    setText('bvmLmfAmount', formatPhp(pricingData.lmf_amount || 0));
    setText('bvmLmfRate', formatPercent(lmfRate, 2));
    setText('bvmTotalContractPrice', formatPhp(pricingData.total_contract_price || pricingData.fully_computed_house_price || 0));
    setText('bvmReservationFee', formatPhp(pricingData.reservation_fee || 0));
    setText('bvmTotalDownpayment', formatPhp(pricingData.total_downpayment || pricingData.down_payment || 0));
    setText('bvmDownpaymentRate', formatPercent(downRate, 2));
    setText('bvmMonthlyDownpayment', formatPhp(pricingData.monthly_downpayment || pricingData.equity_monthly || 0));
    setText('bvmDownpaymentTerms', downMonths > 0 ? (downMonths + ' months') : '—');
    setText('bvmTotalLoanableAmount', formatPhp(pricingData.total_loanable_amount || pricingData.financed_amount || 0));
    setText('bvmLoanableRate', formatPercent(loanableRate, 2));
    setText('bvmRate', formatPercent(pricingData.annual_interest_rate || 0, 2));
    setText('bvmAmort5', formatPhp(amort['5'] || 0));
    setText('bvmAmort10', formatPhp(amort['10'] || 0));
    setText('bvmAmort15', formatPhp(amort['15'] || 0));
    setText('bvmAmort20', formatPhp(amort['20'] || 0));
    setText('bvmReqIncome5', formatPhp(reqIncome['5'] || 0));
    setText('bvmReqIncome10', formatPhp(reqIncome['10'] || 0));
    setText('bvmReqIncome15', formatPhp(reqIncome['15'] || 0));
    setText('bvmReqIncome20', formatPhp(reqIncome['20'] || 0));
  }

  // Carousel prev / next + Request Visit button
  (function() {
    var prev = document.getElementById('pvmPrev');
    var next = document.getElementById('pvmNext');
    if (prev) prev.addEventListener('click', function(e) { e.stopPropagation(); _pvmShowSlide(_pvmIdx - 1); });
    if (next) next.addEventListener('click', function(e) { e.stopPropagation(); _pvmShowSlide(_pvmIdx + 1); });

    var reqBtn = document.getElementById('pvmRequestTripBtn');
    if (reqBtn) {
      reqBtn.addEventListener('click', function() {
        if (reqBtn.dataset.mode === 'visit') {
          bootstrap.Modal.getInstance(document.getElementById('propDetailModal'))?.hide();
          gotoTripsPage('visited');
          return;
        }
        if (reqBtn.dataset.mode === 'view') {
          bootstrap.Modal.getInstance(document.getElementById('propDetailModal'))?.hide();
          gotoTripsPage();
          return;
        }
        bootstrap.Modal.getInstance(document.getElementById('propDetailModal'))?.hide();
        if (_pdmPropId) {
          var card = document.querySelector('[data-prop-id="' + _pdmPropId + '"]');
          if (card) openTripModal(card);
        }
      });
    }
  })();

  (function() {
    var prev = document.getElementById('bvmPrev');
    var next = document.getElementById('bvmNext');
    if (prev) prev.addEventListener('click', function(e) { e.stopPropagation(); _bvmShowSlide(_bvmIdx - 1); });
    if (next) next.addEventListener('click', function(e) { e.stopPropagation(); _bvmShowSlide(_bvmIdx + 1); });
  })();

  // Open prop detail on card click or eye-icon button
  document.addEventListener("click", function (e) {
    if (e.target.closest('.prop-view-btn-icon')) {
      var icard = e.target.closest('.prop-card-clickable');
      if (icard) {
        e.stopPropagation();
        if (String(icard.dataset.bought || '0') === '1') openBoughtPropDetail(icard);
        else openPropDetail(icard);
      }
      return;
    }
    const card = e.target.closest(".prop-card-clickable");
    if (card && !card.dataset.tripCard && !card.classList.contains('trip-preview-trigger') && !e.target.closest("button")) {
      if (String(card.dataset.bought || '0') === '1') openBoughtPropDetail(card);
      else openPropDetail(card);
    }
  });

  /* ── Tripping Request Modal ──────────────────────────────── */
  function openTripModal(card) {
    const modal = document.getElementById("requestTripModal");
    if (!modal) return;

    document.getElementById("tripModalPropId").value = card.dataset.propId || "";
    document.getElementById("tripModalPropName").textContent  = card.dataset.propName || "";
    document.getElementById("tripModalPropLoc").innerHTML =
      `<i class="fas fa-map-marker-alt me-1"></i>${card.dataset.propLoc || ""}`;

    const imgs = (card.dataset.propImages || "").split(",").map(s => s.trim()).filter(Boolean);
    const banner = document.getElementById("tripModalPropBanner");
    if (banner) {
      if (imgs.length > 0) {
        banner.style.backgroundImage = `url(${sqhImgSrc(imgs[0])})`;
        banner.style.backgroundSize  = 'cover';
        banner.style.backgroundPosition = 'center';
        banner.querySelector('.cd-prop-banner-placeholder').style.display = 'none';
      } else {
        banner.style.backgroundImage = '';
        banner.querySelector('.cd-prop-banner-placeholder').style.display = '';
      }
    }

    // Reset fields
    var tripDateEl = document.getElementById("tripDate");
    if (tripDateEl) {
      tripDateEl.value = "";
      var now = new Date();
      var localToday = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
      tripDateEl.min = localToday;
    }
    document.getElementById("tripTime").value  = "";
    const errEl = document.getElementById("tripModalError");
    if (errEl) errEl.classList.add("d-none");

    bootstrap.Modal.getOrCreateInstance(modal).show();
  }

  // "New Request" / "Request Visit" cards that go directly to trip modal
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".prop-card-request-btn");
    if (btn) {
      e.preventDefault();
      e.stopPropagation();
      if (String(btn.dataset.visitMode || '0') === '1') {
        gotoTripsPage('visited');
        return;
      }
      if (String(btn.dataset.requested || '0') === '1') {
        gotoTripsPage();
        return;
      }
      const card = btn.closest(".prop-card-clickable");
      if (card) openTripModal(card);
    }
  });

  var _pendingFullDetailsCard = null;

  function _ensureModalInBody(modalId) {
    var el = document.getElementById(modalId);
    if (!el) return null;
    if (el.parentElement !== document.body) document.body.appendChild(el);
    return el;
  }

  _ensureModalInBody('requestFullDetailsConfirmModal');

  function _closePropertyDetailsThen(callback) {
    var propModalEl = document.getElementById('propDetailModal');
    var boughtModalEl = document.getElementById('boughtPropDetailModal');
    var targetEl = null;

    if (propModalEl && propModalEl.classList.contains('show')) targetEl = propModalEl;
    else if (boughtModalEl && boughtModalEl.classList.contains('show')) targetEl = boughtModalEl;

    if (!targetEl) {
      callback();
      return;
    }

    var instance = bootstrap.Modal.getInstance(targetEl) || bootstrap.Modal.getOrCreateInstance(targetEl);
    targetEl.addEventListener('hidden.bs.modal', function () {
      callback();
    }, { once: true });
    instance.hide();
  }

  function openFullDetailsConfirm(card) {
    if (!card) return;
    var status = String(card.dataset.detailRequestStatus || 'none').toLowerCase();
    if (status === 'approved') {
      openPropDetail(card);
      return;
    }
    var modalEl = _ensureModalInBody('requestFullDetailsConfirmModal');
    if (!modalEl) {
      requestFullDetailsForCard(card);
      return;
    }
    _closePropertyDetailsThen(function () {
      _pendingFullDetailsCard = card;
      modalEl.dataset.propId = card.dataset.propId || '';
      modalEl.dataset.detailStatus = status;
      var propName = card.dataset.propName || card.dataset.name || 'this property';
      var noteEl = document.getElementById('fullDetailsConfirmPropName');
      if (noteEl) {
        noteEl.textContent = status === 'rejected'
          ? 'Previous request for ' + propName + ' was rejected. Send a new request to the assigned agent?'
          : 'Send a full details request for ' + propName + '?';
      }
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
    });
  }

  async function requestFullDetailsForCard(card) {
    var modalEl = _ensureModalInBody('requestFullDetailsConfirmModal');
    var propId = card && card.dataset ? card.dataset.propId : '';
    var status = card && card.dataset ? String(card.dataset.detailRequestStatus || 'none').toLowerCase() : 'none';
    if (!propId && modalEl) propId = modalEl.dataset.propId || '';
    if ((status === 'none' || !status) && modalEl) status = String(modalEl.dataset.detailStatus || 'none').toLowerCase();
    if (!propId) return;
    if (status === 'approved') {
      fetch('/qualify/property/' + encodeURIComponent(propId) + '/detail-status')
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d && d.ok && !d.approved) {
            document.querySelectorAll('.prop-card-clickable[data-prop-id="' + propId + '"]').forEach(function(el) {
              el.dataset.detailRequestStatus = 'none';
              el.dataset.pricingApproved = '0';
              var btn = el.querySelector('.prop-card-details-btn');
              if (btn) {
                btn.dataset.detailStatus = 'none';
                btn.className = 'btn btn-outline-blue w-100 mt-2 prop-card-details-btn';
                btn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Request Full Details';
              }
            });
            var detailsBtn = document.getElementById('pvmRequestDetailsBtn');
            if (detailsBtn) {
              detailsBtn.dataset.detailStatus = 'none';
              detailsBtn.className = 'btn btn-outline-blue';
              detailsBtn.innerHTML = '<i class="fas fa-file-signature me-1"></i> Request Full Details';
              detailsBtn.classList.remove('d-none');
            }
            toast('Your full details access for this property has been reset by admin. You may request details again.', 'info');
          } else {
            if (card) openPropDetail(card);
          }
        })
        .catch(function() {
          if (card) openPropDetail(card);
        });
      return;
    }
    if (status === 'pending') {
      toast('Your request is pending agent approval.', 'info');
      return;
    }
    try {
      var res = await fetch('/qualify/property/' + propId + '/request-full-details', {
        method: 'POST',
        headers: csrfHeaders(),
        body: JSON.stringify({})
      });
      var data = await res.json();
      if (!res.ok || !data.ok) {
        toast((data && data.error) || 'Unable to submit full-details request.', 'danger');
        return;
      }

      document.querySelectorAll('.prop-card-clickable[data-prop-id="' + propId + '"]').forEach(function(el) {
        el.dataset.detailRequestStatus = data.status || 'pending';
        var btn = el.querySelector('.prop-card-details-btn');
        if (btn) {
          btn.dataset.detailStatus = data.status || 'pending';
          btn.classList.remove('btn-outline-blue', 'btn-outline-lime');
          btn.classList.add('btn-outline-crimson');
          btn.innerHTML = '<i class="fas fa-hourglass-half me-1"></i> Details Requested';
        }
      });
      var detailsBtn = document.getElementById('pvmRequestDetailsBtn');
      if (detailsBtn) {
        detailsBtn.dataset.detailStatus = data.status || 'pending';
        detailsBtn.classList.remove('btn-outline-blue', 'btn-outline-lime');
        detailsBtn.classList.add('btn-outline-crimson');
        detailsBtn.innerHTML = '<i class="fas fa-hourglass-half me-1"></i> Details Requested';
      }
      toast('Full details request sent. An agent will review it.', 'success');
    } catch (_) {
      toast('Network error while sending full-details request.', 'danger');
    }
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.prop-card-details-btn');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var card = btn.closest('.prop-card-clickable');
    openFullDetailsConfirm(card);
  });

  // Submit trip request
  const submitTripBtn = document.getElementById("submitTripBtn");
  if (submitTripBtn) {
    submitTripBtn.addEventListener("click", async function () {
      const propId = document.getElementById("tripModalPropId").value;
      const date   = document.getElementById("tripDate").value;
      const time   = document.getElementById("tripTime").value;
      const errEl  = document.getElementById("tripModalError");

      if (!propId || !date) {
        if (errEl) { errEl.textContent = "Please select a preferred date."; errEl.classList.remove("d-none"); }
        return;
      }

      var now = new Date();
      var localToday = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
      if (date < localToday) {
        if (errEl) { errEl.textContent = "Preferred date cannot be in the past. Please select today or a future date."; errEl.classList.remove("d-none"); }
        return;
      }

      submitTripBtn.disabled = true;
      submitTripBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Submitting…';

      try {
        const data = await postJSON("/trip/request", {
          property_id: parseInt(propId),
          preferred_date: date,
          preferred_time: time || null,
        });
        if (data.ok) {
          bootstrap.Modal.getInstance(document.getElementById("requestTripModal"))?.hide();
          toast("Tripping request submitted! An agent will confirm shortly.", "success");
          markPropertyAsRequested(propId);
        } else {
          if (errEl) { errEl.textContent = data.error || "Failed to submit request."; errEl.classList.remove("d-none"); }
        }
      } catch {
        if (errEl) { errEl.textContent = "Network error. Please try again."; errEl.classList.remove("d-none"); }
      } finally {
        submitTripBtn.disabled = false;
        submitTripBtn.innerHTML = '<i class="fas fa-calendar-plus me-1"></i> Submit Request';
      }
    });
  }

  var reqDetailsBtn = document.getElementById('pvmRequestDetailsBtn');
  if (reqDetailsBtn) {
    reqDetailsBtn.addEventListener('click', function() {
      if (!_pdmPropId) return;
      var card = document.querySelector('.prop-card-clickable[data-prop-id="' + _pdmPropId + '"]');
      openFullDetailsConfirm(card);
    });
  }

  var confirmFullDetailsBtn = document.getElementById('confirmRequestFullDetailsBtn');
  if (confirmFullDetailsBtn) {
    confirmFullDetailsBtn.addEventListener('click', async function () {
      var card = _pendingFullDetailsCard;
      if (!card) {
        var modalEl = _ensureModalInBody('requestFullDetailsConfirmModal');
        var propId = modalEl ? modalEl.dataset.propId : '';
        if (propId) card = document.querySelector('.prop-card-clickable[data-prop-id="' + propId + '"]');
      }
      confirmFullDetailsBtn.disabled = true;
      confirmFullDetailsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Sending…';
      await requestFullDetailsForCard(card);
      confirmFullDetailsBtn.disabled = false;
      confirmFullDetailsBtn.innerHTML = '<i class="fas fa-paper-plane me-1"></i> Send';
      bootstrap.Modal.getInstance(document.getElementById('requestFullDetailsConfirmModal'))?.hide();
      _pendingFullDetailsCard = null;
    });
    var reqConfirmModalEl = _ensureModalInBody('requestFullDetailsConfirmModal');
    if (reqConfirmModalEl) {
      reqConfirmModalEl.addEventListener('hidden.bs.modal', function () {
        _pendingFullDetailsCard = null;
        confirmFullDetailsBtn.disabled = false;
        confirmFullDetailsBtn.innerHTML = '<i class="fas fa-paper-plane me-1"></i> Send';
      });
    }
  }

  /* ── Cancel Trip ─────────────────────────────────────────── */
  var _cancelTripId = null;
  var _cancelTripModalId = "cancelTripModal";

  function openTripCancelModal(tripId, tripStatus) {
    _cancelTripId = tripId || null;
    var st = (tripStatus || '').toLowerCase();
    var isDecided = st === 'approved' || st === 'visited' || st === 'rejected' || st === 'sold';
    _cancelTripModalId = isDecided ? 'cancelTripDecidedModal' : 'cancelTripModal';
    bootstrap.Modal.getOrCreateInstance(document.getElementById(_cancelTripModalId)).show();
  }

  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".trip-cancel-btn");
    if (!btn) return;
    openTripCancelModal(btn.dataset.tripId, btn.dataset.tripStatus);
  });

  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".trip-delete-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var card = btn.closest('.trip-preview-trigger');
    var status = btn.dataset.tripStatus || (card ? card.dataset.tripStatus : '');
    openTripCancelModal(btn.dataset.tripId, status);
  });

  (function initTripPreviewModal() {
    var prev = document.getElementById('tripPreviewPrev');
    var next = document.getElementById('tripPreviewNext');
    var cancelBtn = document.getElementById('tripPreviewCancelBtn');
    var continueBtn = document.getElementById('tripPreviewContinueBtn');
    if (prev) prev.addEventListener('click', function(e) { e.stopPropagation(); showTripPreviewSlide(_tripPreviewIdx - 1); });
    if (next) next.addEventListener('click', function(e) { e.stopPropagation(); showTripPreviewSlide(_tripPreviewIdx + 1); });
    if (continueBtn) {
      continueBtn.addEventListener('click', function(e) {
        e.preventDefault();
        var modal = document.getElementById('tripPreviewModal');
        var tripId = this.dataset.tripId || (modal ? modal.dataset.tripId : '') || '';
        var isSubmitted = String(this.dataset.purchaseFormSubmitted || '0') === '1';
        bootstrap.Modal.getInstance(modal)?.hide();
        setTimeout(function () { openBuyerInfoModal(tripId, { readOnly: isSubmitted }); }, 180);
      });
    }
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function() {
        var modal = document.getElementById('tripPreviewModal');
        var tripId = this.dataset.tripId || modal.dataset.tripId || null;
        var status = modal.dataset.tripStatus || '';
        bootstrap.Modal.getInstance(modal)?.hide();
        openTripCancelModal(tripId, status);
      });
    }
  })();

  document.addEventListener('click', function (e) {
    var continueBtn = e.target.closest('.trip-continue-btn');
    if (continueBtn) {
      e.preventDefault();
      e.stopPropagation();
      var isSubmitted = String(continueBtn.dataset.purchaseFormSubmitted || '0') === '1';
      openBuyerInfoModal(continueBtn.getAttribute('data-trip-id') || '', { readOnly: isSubmitted });
      return;
    }

    var trigger = e.target.closest('.trip-preview-trigger, .trip-preview-btn');
    if (!trigger) return;
    if (e.target.closest('.trip-cancel-btn')) return;
    if (e.target.closest('a, button') && !e.target.closest('.trip-preview-btn')) return;
    var card = trigger.classList.contains('trip-preview-trigger') ? trigger : trigger.closest('.trip-preview-trigger');
    if (!card) return;
    e.preventDefault();
    if (!e.target.closest('.trip-preview-btn') && e.target.closest('button') && !e.target.closest('.trip-preview-btn')) return;
    openTripPreview(card);
  });

  document.addEventListener('keydown', function (e) {
    var card = e.target.closest('.trip-preview-trigger');
    if (!card) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    openTripPreview(card);
  });

  document.addEventListener('click', function (e) {
    var card = e.target.closest('#page-home .cp-trip-card--home-clickable');
    if (!card || e.target.closest('button, a')) return;
    var tripId = card.dataset.tripId || '';
    var target = document.querySelector('.trip-preview-trigger[data-trip-id="' + tripId + '"]');
    if (target) {
      openTripPreview(target);
    }
  });

  document.addEventListener('keydown', function (e) {
    var card = e.target.closest('#page-home .cp-trip-card--home-clickable');
    if (!card) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    var tripId = card.dataset.tripId || '';
    var target = document.querySelector('.trip-preview-trigger[data-trip-id="' + tripId + '"]');
    if (target) {
      openTripPreview(target);
    }
  });

  function bindTripCancelConfirm(buttonId, busyText, idleText) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;
    btn.addEventListener("click", async function () {
      if (!_cancelTripId) return;
      btn.disabled = true;
      btn.textContent = busyText;

      try {
        const data = await postJSON(`/qualify/trip/${_cancelTripId}/cancel`, {});
        if (data.ok) {
          bootstrap.Modal.getInstance(document.getElementById(_cancelTripModalId))?.hide();
          toast("Tripping request removed.", "info");
          setTimeout(() => location.reload(), 1500);
        } else {
          toast(data.error || "Could not remove request.", "error");
        }
      } catch {
        toast("Network error. Please try again.", "error");
      } finally {
        btn.disabled = false;
        btn.textContent = idleText;
        _cancelTripId = null;
      }
    });
  }

  bindTripCancelConfirm("confirmCancelTripBtn", "Cancelling…", "Yes, Cancel");
  bindTripCancelConfirm("confirmCancelTripDecidedBtn", "Removing…", "Yes, Remove");

  /* ── Profile — password toggle ──────────────────────────── */
  function handlePasswordToggle(e) {
    var btn = e.target.closest('.toggle-password');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    var targetId = btn.getAttribute('data-target');
    var input    = targetId ? document.getElementById(targetId) : null;
    if (!input) return;
    var isHidden = input.type === 'password';
    input.type   = isHidden ? 'text' : 'password';
    var icon = btn.querySelector('i');
    if (icon) {
      icon.className = isHidden ? 'fas fa-eye-slash' : 'fas fa-eye';
    }
  }

  // Capture phase keeps this working even if another container listener stops bubbling.
  document.addEventListener('click', handlePasswordToggle, true);

  /* ── Profile — pw checklist ──────────────────────────────── */
  (function () {
    var pwInput = document.getElementById('prof_new_password');
    var cfInput = document.getElementById('prof_confirm_password');
    var cfErr   = document.getElementById('prof_confirm_password_error');
    if (!pwInput) return;
    function check(id, pass) {
      var el = document.getElementById(id); if (!el) return;
      el.classList.toggle('pw-ok', pass);
      el.querySelector('i').className = pass ? 'fas fa-check-circle' : 'fas fa-circle-dot';
    }
    pwInput.addEventListener('input', function () {
      var v = pwInput.value;
      check('profPwLen',     v.length >= 6);
      check('profPwUpper',   /[A-Z]/.test(v));
      check('profPwNum',     /[0-9]/.test(v));
      check('profPwSpecial', /[^A-Za-z0-9]/.test(v));
      if (cfInput && cfInput.value) cfInput.dispatchEvent(new Event('input'));
    });

    if (cfInput) {
      cfInput.addEventListener('input', function () {
        var cf = cfInput.value;
        var pw = pwInput ? pwInput.value : '';
        if (!cf) {
          cfInput.classList.remove('lv-valid', 'lv-invalid');
          if (cfErr) { cfErr.innerHTML = ''; cfErr.classList.remove('sqh-err-visible'); }
        } else if (cf !== pw) {
          cfInput.classList.remove('lv-valid');
          cfInput.classList.add('lv-invalid');
          if (cfErr) {
            cfErr.innerHTML = '<i class="fas fa-exclamation-circle"></i> Passwords do not match.';
            cfErr.classList.add('sqh-err-visible');
          }
        } else {
          cfInput.classList.remove('lv-invalid');
          cfInput.classList.add('lv-valid');
          if (cfErr) { cfErr.innerHTML = ''; cfErr.classList.remove('sqh-err-visible'); }
        }
      });
    }
  })();

  /* ── Profile — Save button → show confirmation modal ────── */
  function _digitsOnly(value) {
    return String(value || '').replace(/\D/g, '');
  }

  function _formatUmid(value) {
    var d = _digitsOnly(value).slice(0, 10);
    var p1 = d.slice(0, 2);
    var p2 = d.slice(2, 9);
    var p3 = d.slice(9, 10);
    return [p1, p2, p3].filter(Boolean).join('-');
  }

  function _formatTin(value) {
    var d = _digitsOnly(value).slice(0, 12);
    var p1 = d.slice(0, 3);
    var p2 = d.slice(3, 6);
    var p3 = d.slice(6, 9);
    var p4 = d.slice(9, 12);
    return [p1, p2, p3, p4].filter(Boolean).join('-');
  }

  function _isUmidComplete(value) {
    return /^\d{2}-\d{7}-\d$/.test(String(value || '').trim());
  }

  function _isTinComplete(value) {
    return /^\d{3}-\d{3}-\d{3}-\d{3}$/.test(String(value || '').trim());
  }

  var _profUmidInput = document.getElementById('prof_umid');
  if (_profUmidInput) {
    _profUmidInput.addEventListener('input', function () {
      this.value = _formatUmid(this.value);
    });
    _profUmidInput.addEventListener('blur', function () {
      this.value = _formatUmid(this.value);
    });
    _profUmidInput.value = _formatUmid(_profUmidInput.value);
  }

  var _profTinInput = document.getElementById('prof_tin');
  if (_profTinInput) {
    _profTinInput.addEventListener('input', function () {
      this.value = _formatTin(this.value);
    });
    _profTinInput.addEventListener('blur', function () {
      this.value = _formatTin(this.value);
    });
    _profTinInput.value = _formatTin(_profTinInput.value);
  }

  var _saveProfileBtnEl = document.getElementById('saveProfileBtn');
  if (_saveProfileBtnEl) {
    _saveProfileBtnEl.addEventListener('click', function () {
      var errEl = document.getElementById('profileError');
      if (errEl) errEl.classList.add('d-none');

      var username = (document.getElementById('prof_username') || {}).value || '';
      username = username.trim();
      if (!username) {
        if (errEl) { errEl.textContent = 'Username is required.'; errEl.classList.remove('d-none'); }
        return;
      }
      if (username.length < 3) {
        if (errEl) { errEl.textContent = 'Username must be at least 3 characters.'; errEl.classList.remove('d-none'); }
        return;
      }
      if (!/^[\w.]+$/.test(username)) {
        if (errEl) { errEl.textContent = 'Username may contain only letters, numbers, dots, and underscores.'; errEl.classList.remove('d-none'); }
        return;
      }

      var umidVal = (document.getElementById('prof_umid') || {}).value || '';
      umidVal = umidVal.trim();
      if (umidVal && !_isUmidComplete(umidVal)) {
        if (errEl) { errEl.textContent = 'SSS/GSIS/UMID must follow 00-0000000-0 format.'; errEl.classList.remove('d-none'); }
        return;
      }

      var tinVal = (document.getElementById('prof_tin') || {}).value || '';
      tinVal = tinVal.trim();
      if (tinVal && !_isTinComplete(tinVal)) {
        if (errEl) { errEl.textContent = 'TIN must follow 000-000-000-000 format.'; errEl.classList.remove('d-none'); }
        return;
      }

      var oldPass     = (document.getElementById('prof_old_password')     || {}).value || '';
      var newPass     = (document.getElementById('prof_new_password')     || {}).value || '';
      var confirmPass = (document.getElementById('prof_confirm_password') || {}).value || '';
      if (newPass) {
        if (!oldPass) {
          if (errEl) { errEl.textContent = 'Current password is required to set a new password.'; errEl.classList.remove('d-none'); }
          return;
        }
        if (newPass.length < 6) {
          if (errEl) { errEl.textContent = 'New password must be at least 6 characters.'; errEl.classList.remove('d-none'); }
          return;
        }
        if (newPass !== confirmPass) {
          if (errEl) { errEl.textContent = 'Passwords do not match.'; errEl.classList.remove('d-none'); }
          return;
        }
      }
      bootstrap.Modal.getOrCreateInstance(document.getElementById('saveClientProfileModal')).show();
    });
  }

  /* ── Profile — Confirm modal → actually save ─────────────── */
  var _confirmSaveClientBtn = document.getElementById('confirmSaveClientProfileBtn');
  var _pendingProfileAssets = {
    avatarFile: null,
    bannerFile: null,
    deleteAvatar: false,
    deleteBanner: false,
    docs: {
      'valid-id': { file: null, delete: false },
      'income-proof': { file: null, delete: false }
    }
  };
  var _pendingDocRemoveKind = null;

  function _setPendingLabel(id, text) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || '';
  }

  function _markProfileDirtyState() {
    window._profDirty = true;
    if (typeof window._profUpdateSaveCta === 'function') window._profUpdateSaveCta();
  }

  function _fetchJson(url, options) {
    return fetch(url, options || {}).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, data: d || {} }; });
    });
  }

  function _uploadDocNow(kind, file) {
    var fd = new FormData();
    fd.append('doc_kind', kind);
    fd.append('document', file);
    return _fetchJson('/profile/upload-document', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      body: fd
    }).then(function (res) {
      if (!res.ok) throw new Error(res.data.error || 'Document upload failed.');
      var freshUrl = (res.data.url || '#') + '?t=' + Date.now();
      setDocCardState(kind, true, res.data.filename || file.name || 'Uploaded document', freshUrl);
    });
  }

  function _deleteDocNow(kind) {
    return _fetchJson('/profile/delete-document/' + encodeURIComponent(kind), {
      method: 'POST',
      headers: csrfHeaders()
    }).then(function (res) {
      if (!res.ok || !res.data.success) throw new Error((res.data && res.data.error) || 'Delete failed.');
      setDocCardState(kind, false, '', '#');
    });
  }

  function _uploadAvatarNow(file) {
    var fd = new FormData();
    fd.append('avatar', file);
    return _fetchJson('/profile/upload-avatar', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      body: fd
    }).then(function (res) {
      if (!res.ok) throw new Error(res.data.error || 'Profile photo upload failed.');
      var wrap = document.getElementById('profAvatarLg');
      if (!wrap) return;
      var freshUrl = res.data.url + '?t=' + Date.now();
      var existingIcon = document.getElementById('profAvatarIcon');
      if (existingIcon) existingIcon.remove();
      var existingImg = document.getElementById('profAvatarImg');
      if (existingImg) {
        existingImg.src = freshUrl;
      } else {
        var img = document.createElement('img');
        img.id = 'profAvatarImg';
        img.src = freshUrl;
        img.alt = 'Profile photo';
        var label = wrap.querySelector('.prof-avatar-upload-btn');
        wrap.insertBefore(img, label);
      }
      syncTopAvatar(freshUrl);
      var prevBtn = document.getElementById('avatarPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
    });
  }

  function _uploadBannerNow(file) {
    var fd = new FormData();
    fd.append('banner', file);
    return _fetchJson('/profile/upload-banner', {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
      body: fd
    }).then(function (res) {
      if (!res.ok) throw new Error(res.data.error || 'Cover photo upload failed.');
      var banner = document.getElementById('profHeroBanner');
      if (!banner) return;
      var freshUrl = res.data.url + '?t=' + Date.now();
      banner.style.backgroundImage = 'url(\'' + freshUrl + '\')';
      banner.style.backgroundSize = 'cover';
      banner.style.backgroundPosition = 'center';
      banner.dataset.bannerUrl = freshUrl;
      var prevBtn = document.getElementById('bannerPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
    });
  }

  function _deleteAvatarNow() {
    return _fetchJson('/profile/delete-avatar', { method: 'POST', headers: csrfHeaders() }).then(function (res) {
      if (!res.ok || !res.data.success) throw new Error((res.data && res.data.error) || 'Delete failed.');
      var img = document.getElementById('profAvatarImg');
      if (img) img.remove();
      var wrap = document.getElementById('profAvatarLg');
      if (wrap && !document.getElementById('profAvatarIcon')) {
        var icon = document.createElement('i');
        icon.className = 'fas fa-user';
        icon.id = 'profAvatarIcon';
        wrap.insertBefore(icon, wrap.firstChild);
      }
      syncTopAvatar(null);
      var pb = document.getElementById('avatarPreviewBtn');
      if (pb) pb.style.display = 'none';
    });
  }

  function _deleteBannerNow() {
    return _fetchJson('/profile/delete-banner', { method: 'POST', headers: csrfHeaders() }).then(function (res) {
      if (!res.ok || !res.data.success) throw new Error((res.data && res.data.error) || 'Delete failed.');
      var bannerEl = document.getElementById('profHeroBanner');
      if (bannerEl) {
        bannerEl.style.backgroundImage = '';
        bannerEl.style.backgroundSize = '';
        bannerEl.style.backgroundPosition = '';
        delete bannerEl.dataset.bannerUrl;
      }
      var pb = document.getElementById('bannerPreviewBtn');
      if (pb) pb.style.display = 'none';
    });
  }

  function _applyPendingProfileAssets() {
    var chain = Promise.resolve();

    if (_pendingProfileAssets.deleteAvatar) {
      chain = chain.then(function () { return _deleteAvatarNow(); });
    }
    if (_pendingProfileAssets.avatarFile) {
      chain = chain.then(function () { return _uploadAvatarNow(_pendingProfileAssets.avatarFile); });
    }

    if (_pendingProfileAssets.deleteBanner) {
      chain = chain.then(function () { return _deleteBannerNow(); });
    }
    if (_pendingProfileAssets.bannerFile) {
      chain = chain.then(function () { return _uploadBannerNow(_pendingProfileAssets.bannerFile); });
    }

    ['valid-id', 'income-proof'].forEach(function (kind) {
      if (_pendingProfileAssets.docs[kind].delete) {
        chain = chain.then(function () { return _deleteDocNow(kind); });
      }
      if (_pendingProfileAssets.docs[kind].file) {
        chain = chain.then(function () { return _uploadDocNow(kind, _pendingProfileAssets.docs[kind].file); });
      }
    });

    return chain.then(function () {
      _pendingProfileAssets.avatarFile = null;
      _pendingProfileAssets.bannerFile = null;
      _pendingProfileAssets.deleteAvatar = false;
      _pendingProfileAssets.deleteBanner = false;
      _pendingProfileAssets.docs['valid-id'].file = null;
      _pendingProfileAssets.docs['valid-id'].delete = false;
      _pendingProfileAssets.docs['income-proof'].file = null;
      _pendingProfileAssets.docs['income-proof'].delete = false;
      _setPendingLabel('avatarPendingFileName', '');
      _setPendingLabel('bannerPendingFileName', '');
    });
  }

  if (_confirmSaveClientBtn) {
    _confirmSaveClientBtn.addEventListener('click', function () {
      var btn    = this;
      var errEl  = document.getElementById('profileError');
      bootstrap.Modal.getInstance(document.getElementById('saveClientProfileModal')).hide();

      btn.disabled = true;
      var saveBtn = document.getElementById('saveProfileBtn');
      if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Saving\u2026'; }

      var oldPass = (document.getElementById('prof_old_password') || {}).value || '';
      var newPass = (document.getElementById('prof_new_password') || {}).value || '';
      function gv(id) { var el = document.getElementById(id); return el ? el.value : ''; }

      var payload = {
        first_name:          gv('prof_first_name'),
        middle_name:         gv('prof_middle_name'),
        last_name:           gv('prof_last_name'),
        email:               gv('prof_email'),
        username:            gv('prof_username'),
        contact_number:      gv('prof_contact'),
        civil_status:        gv('prof_civil_status'),
        citizenship:         gv('prof_citizenship'),
        gender:              gv('prof_gender'),
        dependents:          gv('prof_dependents'),
        birth_date:          gv('prof_birth_date'),
        birthplace:          gv('prof_birthplace'),
        age:                 gv('prof_age'),
        employment_type:     gv('prof_employment_type'),
        employer_name:       gv('prof_employer_name'),
        employer_phone:      gv('prof_employer_phone'),
        employer_email:      gv('prof_employer_email'),
        tenure_months:       gv('prof_tenure_months'),
        employer_business_address: gv('prof_employer_business_address'),
        employer_region_code: gv('prof_emp_region_code'),
        employer_region_name: gv('prof_emp_region_name'),
        employer_province_code: gv('prof_emp_province_code'),
        employer_province_name: gv('prof_emp_province_name'),
        employer_citymun_code: gv('prof_emp_citymun_code'),
        employer_citymun_name: gv('prof_emp_citymun_name'),
        employer_barangay_code: gv('prof_emp_barangay_code'),
        employer_barangay_name: gv('prof_emp_barangay_name'),
        birth_region_code: gv('prof_birth_region_code'),
        birth_region_name: gv('prof_birth_region_name'),
        birth_province_code: gv('prof_birth_province_code'),
        birth_province_name: gv('prof_birth_province_name'),
        birth_citymun_code: gv('prof_birth_citymun_code'),
        birth_citymun_name: gv('prof_birth_citymun_name'),
        birth_barangay_code: gv('prof_birth_barangay_code'),
        birth_barangay_name: gv('prof_birth_barangay_name'),
        address:             gv('prof_address'),
        street:              gv('prof_street'),
        blk:                 gv('prof_blk'),
        lot:                 gv('prof_lot'),
        country:             gv('prof_country'),
        zip_code:            gv('prof_zip_code'),
        subdivision_name:    gv('prof_subdivision_name'),
        sss_gsis_umid:       gv('prof_umid'),
        tin_no:              gv('prof_tin'),
        social_instagram:    gv('prof_social_instagram'),
        social_twitter_x:    gv('prof_social_twitter_x'),
        social_viber:        gv('prof_social_viber'),
        social_whatsapp:     gv('prof_social_whatsapp'),
        has_valid_id:        gv('prof_has_valid_id'),
        has_income_proof:    gv('prof_has_income_proof'),
        home_region_code: gv('prof_home_region_code'),
        home_region_name: gv('prof_home_region_name'),
        home_province_code: gv('prof_home_province_code'),
        home_province_name: gv('prof_home_province_name'),
        home_citymun_code: gv('prof_home_citymun_code'),
        home_citymun_name: gv('prof_home_citymun_name'),
        home_barangay_code: gv('prof_home_barangay_code'),
        home_barangay_name: gv('prof_home_barangay_name'),
      };
      if (oldPass) payload.old_password = oldPass;
      if (newPass) payload.new_password = newPass;

      fetch('/profile/save', {
        method: 'POST',
        headers: csrfHeaders(),
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          if (!res.ok) {
            btn.disabled = false;
            if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
            if (errEl) { errEl.textContent = res.data.error || 'An error occurred.'; errEl.classList.remove('d-none'); }
            return;
          }
          // Clear password fields
          ['prof_old_password', 'prof_new_password', 'prof_confirm_password'].forEach(function (id) {
            var el = document.getElementById(id); if (el) el.value = '';
          });
          // Reset checklist
          ['profPwLen','profPwUpper','profPwNum','profPwSpecial'].forEach(function (id) {
            var el = document.getElementById(id); if (!el) return;
            el.classList.remove('pw-ok');
            el.querySelector('i').className = 'fas fa-circle-dot';
          });
          // Update hero name
          var fullName = res.data.full_name || '';
          var heroNameEl = document.getElementById('profHeroName');
          if (heroNameEl && fullName) heroNameEl.textContent = fullName;
          document.querySelectorAll('.dash-topbar-name').forEach(function (el) { if (fullName) el.textContent = fullName; });

          _applyPendingProfileAssets()
            .then(function () {
              btn.disabled = false;
              if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
              window._profDirty = false;
              if (typeof window._profRefreshSnapshot === 'function') window._profRefreshSnapshot();
              toast('Profile saved successfully.', 'success');
            })
            .catch(function (assetErr) {
              btn.disabled = false;
              if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
              var msg = (assetErr && assetErr.message) ? assetErr.message : 'Profile saved, but one or more file changes failed.';
              if (errEl) { errEl.textContent = msg; errEl.classList.remove('d-none'); }
              toast(msg, 'warning');
            });
        })
        .catch(function () {
          btn.disabled = false;
          if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="fas fa-save me-1"></i> Save Changes'; }
          if (errEl) { errEl.textContent = 'Network error. Please try again.'; errEl.classList.remove('d-none'); }
        });
    });
  }

  /* ── Profile — Avatar upload ──────────────────────────────── */
  var _avatarFileInput = document.getElementById('avatarFileInput');
  if (_avatarFileInput) {
    _avatarFileInput.addEventListener('change', function () {
      var file = this.files[0]; if (!file) return;
      _pendingProfileAssets.avatarFile = file;
      _pendingProfileAssets.deleteAvatar = false;
      _setPendingLabel('avatarPendingFileName', file.name || '');
      var wrap = document.getElementById('profAvatarLg');
      if (wrap) {
        var localUrl = URL.createObjectURL(file);
        var existingIcon = document.getElementById('profAvatarIcon');
        if (existingIcon) existingIcon.remove();
        var existingImg = document.getElementById('profAvatarImg');
        if (existingImg) {
          existingImg.src = localUrl;
        } else {
          var img = document.createElement('img');
          img.id = 'profAvatarImg';
          img.src = localUrl;
          img.alt = 'Profile photo';
          var label = wrap.querySelector('.prof-avatar-upload-btn');
          wrap.insertBefore(img, label);
        }
        syncTopAvatar(localUrl);
      }
      var prevBtn = document.getElementById('avatarPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
      _markProfileDirtyState();
      toast('Profile photo selected. Click Save Changes to apply.', 'info');
    });
  }

  /* ── Profile — Banner upload ──────────────────────────────── */
  var _bannerFileInput = document.getElementById('bannerFileInput');
  if (_bannerFileInput) {
    _bannerFileInput.addEventListener('change', function () {
      var file = this.files[0]; if (!file) return;
      _pendingProfileAssets.bannerFile = file;
      _pendingProfileAssets.deleteBanner = false;
      _setPendingLabel('bannerPendingFileName', file.name || '');
      var banner = document.getElementById('profHeroBanner');
      if (banner) {
        var localUrl = URL.createObjectURL(file);
        banner.style.backgroundImage = 'url(\'' + localUrl + '\')';
        banner.style.backgroundSize = 'cover';
        banner.style.backgroundPosition = 'center';
      }
      var prevBtn = document.getElementById('bannerPreviewBtn');
      if (prevBtn) prevBtn.style.display = '';
      _markProfileDirtyState();
      toast('Cover photo selected. Click Save Changes to apply.', 'info');
    });
  }

  /* ── Profile — Documentation uploads (Valid ID / Proof of Income) ───────── */
  function setDocCardState(kind, hasFile, filename, fileUrl, state) {
    var isValid = kind === 'valid-id';
    var fileEl = document.getElementById(isValid ? 'docUploadValidId' : 'docUploadIncomeProof');
    var viewEl = document.getElementById(isValid ? 'docViewValidId' : 'docViewIncomeProof');
    var delEl = document.getElementById(isValid ? 'docDeleteValidId' : 'docDeleteIncomeProof');
    var cardEl = document.getElementById(isValid ? 'docCardValidId' : 'docCardIncomeProof');
    var badgeEl = cardEl ? cardEl.querySelector('.prof-doc-head .sqh-badge') : null;
    var resolvedState = state || (hasFile ? 'uploaded' : 'none');

    if (fileEl) {
      fileEl.value = hasFile ? (filename || 'Uploaded document') : '';
    }
    if (viewEl) {
      viewEl.href = hasFile ? fileUrl : '#';
      viewEl.classList.toggle('disabled', !hasFile);
    }
    if (delEl) delEl.classList.toggle('d-none', !hasFile);
    if (badgeEl) {
      badgeEl.classList.remove('doc-badge-pre-selling', 'doc-badge-ready-for-occupancy', 'doc-badge-resale');
      if (resolvedState === 'pending') {
        badgeEl.textContent = 'Pending Save';
        badgeEl.classList.add('doc-badge-ready-for-occupancy');
      } else if (resolvedState === 'pending-remove') {
        badgeEl.textContent = 'Pending Removal';
        badgeEl.classList.add('doc-badge-resale');
      } else if (hasFile) {
        badgeEl.textContent = 'Uploaded';
        badgeEl.classList.add('doc-badge-pre-selling');
      } else {
        badgeEl.textContent = 'Not Uploaded';
        badgeEl.classList.add('doc-badge-resale');
      }
    }
  }

  function stageDocFile(kind, file) {
    if (!file) return;
    _pendingProfileAssets.docs[kind].file = file;
    _pendingProfileAssets.docs[kind].delete = false;
    setDocCardState(kind, true, file.name || 'Selected file', '#', 'pending');
    var viewEl = document.getElementById(kind === 'valid-id' ? 'docViewValidId' : 'docViewIncomeProof');
    if (viewEl) {
      viewEl.href = '#';
      viewEl.classList.add('disabled');
    }
    _markProfileDirtyState();
    toast('Document selected. Click Save Changes to apply.', 'info');
  }

  var validDocPicker = document.getElementById('docUploadValidIdPicker');
  var validDocBrowseBtn = document.getElementById('docUploadValidIdBtn');
  if (validDocBrowseBtn && validDocPicker) {
    validDocBrowseBtn.addEventListener('click', function () {
      validDocPicker.click();
    });
    validDocPicker.addEventListener('change', function () {
      stageDocFile('valid-id', this.files[0]);
    });
  }

  var incomeDocPicker = document.getElementById('docUploadIncomeProofPicker');
  var incomeDocBrowseBtn = document.getElementById('docUploadIncomeProofBtn');
  if (incomeDocBrowseBtn && incomeDocPicker) {
    incomeDocBrowseBtn.addEventListener('click', function () {
      incomeDocPicker.click();
    });
    incomeDocPicker.addEventListener('change', function () {
      stageDocFile('income-proof', this.files[0]);
    });
  }

  var _docRemoveConfirmBtn = document.getElementById('docRemoveConfirmBtn');

  document.querySelectorAll('.prof-doc-delete-btn[data-doc-kind]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var kind = btn.getAttribute('data-doc-kind');
      _pendingDocRemoveKind = kind;
      var msgEl = document.getElementById('docRemoveConfirmText');
      if (msgEl) msgEl.textContent = 'Remove this document? Click Save Changes to finalize.';
      bootstrap.Modal.getOrCreateInstance(document.getElementById('docRemoveConfirmModal')).show();
    });
  });

  if (_docRemoveConfirmBtn) {
    _docRemoveConfirmBtn.addEventListener('click', function () {
      if (!_pendingDocRemoveKind) return;
      var kind = _pendingDocRemoveKind;
      _pendingDocRemoveKind = null;
      bootstrap.Modal.getInstance(document.getElementById('docRemoveConfirmModal')).hide();
      _pendingProfileAssets.docs[kind].file = null;
      _pendingProfileAssets.docs[kind].delete = true;
      var pickerEl = document.getElementById(kind === 'valid-id' ? 'docUploadValidIdPicker' : 'docUploadIncomeProofPicker');
      if (pickerEl) pickerEl.value = '';
      setDocCardState(kind, false, '', '#', 'pending-remove');
      _markProfileDirtyState();
      toast('Document marked for removal. Click Save Changes to apply.', 'warning');
    });
  }

  /* ── Profile — Image Preview Modal ───────────────────────── */
  (function () {
    var _previewType = null;
    var _previewRestoreModalId = null;

    function _applyDarkModalBackdrops() {
      var backdrops = document.querySelectorAll('.modal-backdrop');
      if (backdrops && backdrops.forEach) backdrops.forEach(function(el) { el.classList.add('sqh-dark-backdrop'); });
    }

    // ── Zoom/drag (delegated to shared controller in main.js) ──
    var clientZoomCtrl = new ZoomDragController({
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
      var imgEl = document.getElementById('imgPreviewSrc');
      if (imgEl) {
        imgEl.src = sqhOptimizedSrc(imgUrl);
        SqhImageCache.swap(imgEl, imgUrl);
      }
      _setPreviewActionsVisible(true);
      clientZoomCtrl.resetZoom();
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
      clientZoomCtrl.resetZoom();
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
        clientZoomCtrl.resetZoom();
      });
      previewModalEl.addEventListener('hidden.bs.modal', function() {
        clientZoomCtrl.resetZoom();
        if (!_previewRestoreModalId) return;
        var restoreEl = document.getElementById(_previewRestoreModalId);
        _previewRestoreModalId = null;
        if (restoreEl) bootstrap.Modal.getOrCreateInstance(restoreEl).show();
      });
    }

    ['pvmImg', 'bvmImg', 'tripPreviewImg'].forEach(function(id) {
      var img = document.getElementById(id);
      if (!img) return;
      img.addEventListener('click', function(e) {
        var src = img.getAttribute('src') || '';
        if (!src) return;
        e.stopPropagation();
        var sourceModal = img.closest('.modal');
        openReadOnlyPreview(src, sourceModal ? sourceModal.id : null);
      });
    });

    (function initDarkBackdrops() {
      var darkBackdropModalIds = [
        'propDetailModal',
        'boughtPropDetailModal',
        'tripPreviewModal',
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

    var avatarPrev = document.getElementById('avatarPreviewBtn');
    if (avatarPrev) {
      avatarPrev.addEventListener('click', function (e) {
        e.stopPropagation();
        var img = document.getElementById('profAvatarImg');
        if (img) openPreview('avatar', 'Profile Photo', img.src);
      });
    }

    var bannerPrev = document.getElementById('bannerPreviewBtn');
    if (bannerPrev) {
      bannerPrev.addEventListener('click', function (e) {
        e.stopPropagation();
        var bannerEl = document.getElementById('profHeroBanner'); if (!bannerEl) return;
        var url = sqhExtractBgUrl(bannerEl);
        if (url) openPreview('banner', 'Cover Photo', url);
      });
    }

    var replaceInput = document.getElementById('imgPreviewReplaceInput');
    if (replaceInput) {
      replaceInput.addEventListener('change', function () {
        if (!_previewType) return;
        var file = this.files[0]; if (!file) return;
        var localUrl = URL.createObjectURL(file);
        var modalImg = document.getElementById('imgPreviewSrc');
        if (modalImg) modalImg.src = localUrl;
        if (_previewType === 'avatar') {
          _pendingProfileAssets.avatarFile = file;
          _pendingProfileAssets.deleteAvatar = false;
          _setPendingLabel('avatarPendingFileName', file.name || '');
          var existingIcon = document.getElementById('profAvatarIcon');
          if (existingIcon) existingIcon.remove();
          var existingImg = document.getElementById('profAvatarImg');
          if (existingImg) {
            existingImg.src = localUrl;
          } else {
            var wrap = document.getElementById('profAvatarLg');
            if (wrap) {
              var img = document.createElement('img');
              img.id = 'profAvatarImg';
              img.src = localUrl;
              img.alt = 'Profile photo';
              wrap.insertBefore(img, wrap.querySelector('.prof-avatar-upload-btn'));
            }
          }
          syncTopAvatar(localUrl);
          var pb = document.getElementById('avatarPreviewBtn'); if (pb) pb.style.display = '';
        } else {
          _pendingProfileAssets.bannerFile = file;
          _pendingProfileAssets.deleteBanner = false;
          _setPendingLabel('bannerPendingFileName', file.name || '');
          var bannerEl = document.getElementById('profHeroBanner');
          if (bannerEl) {
            bannerEl.style.backgroundImage = 'url(\'' + localUrl + '\')';
            bannerEl.style.backgroundSize = 'cover';
            bannerEl.style.backgroundPosition = 'center';
            bannerEl.dataset.bannerUrl = localUrl;
          }
          var pb2 = document.getElementById('bannerPreviewBtn'); if (pb2) pb2.style.display = '';
        }
        _markProfileDirtyState();
        toast('Image selected. Click Save Changes to apply.', 'info');
      });
    }

    var deleteBtn = document.getElementById('imgPreviewDeleteBtn');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', function () {
        if (!_previewType) return;
        if (!window.confirm('Remove this photo? Click Save Changes to finalize.')) return;
        bootstrap.Modal.getInstance(document.getElementById('imgPreviewModal')).hide();
        if (_previewType === 'avatar') {
          _pendingProfileAssets.avatarFile = null;
          _pendingProfileAssets.deleteAvatar = true;
          _setPendingLabel('avatarPendingFileName', '(Will be removed on save)');
          var img = document.getElementById('profAvatarImg');
          if (img) img.remove();
          var wrap = document.getElementById('profAvatarLg');
          if (wrap && !document.getElementById('profAvatarIcon')) {
            var icon = document.createElement('i');
            icon.className = 'fas fa-user';
            icon.id = 'profAvatarIcon';
            wrap.insertBefore(icon, wrap.firstChild);
          }
          syncTopAvatar(null);
          var pb = document.getElementById('avatarPreviewBtn'); if (pb) pb.style.display = 'none';
        } else {
          _pendingProfileAssets.bannerFile = null;
          _pendingProfileAssets.deleteBanner = true;
          _setPendingLabel('bannerPendingFileName', '(Will be removed on save)');
          var bannerEl = document.getElementById('profHeroBanner');
          if (bannerEl) {
            bannerEl.style.backgroundImage = '';
            bannerEl.style.backgroundSize = '';
            bannerEl.style.backgroundPosition = '';
            delete bannerEl.dataset.bannerUrl;
          }
          var pb2 = document.getElementById('bannerPreviewBtn'); if (pb2) pb2.style.display = 'none';
        }
        _markProfileDirtyState();
        toast(_previewType === 'avatar' ? 'Profile photo marked for removal. Click Save Changes.' : 'Cover photo marked for removal. Click Save Changes.', 'warning');
      });
    }
  })();

  /* ── Avatar chip → Profile navigation ────────────────────── */
  (function initAvatarChip() {
    const avatarChip = document.querySelector('.cp-avatar-chip');
    if (avatarChip) {
      avatarChip.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (typeof showPage === 'function') showPage('profile');
      });
    }
  })();

  /* ── Mobile nav toggle ───────────────────────────────────── */
  (function initMobileNav() {
    const toggleBtn = document.getElementById("cpMenuToggle");
    const nav       = document.getElementById("cpNav");
    if (!toggleBtn || !nav) return;

    toggleBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      nav.classList.toggle("open");
    });

    document.addEventListener("click", function (e) {
      if (
        nav.classList.contains("open") &&
        !nav.contains(e.target) &&
        e.target !== toggleBtn
      ) {
        nav.classList.remove("open");
      }
    });

    // Keep nav state clean when a modal opens on mobile.
    document.addEventListener("show.bs.modal", function () {
      nav.classList.remove("open");
    });
  })();

    /* ── Profile — Unsaved Changes Guard ─────────────────────── */
    (function () {
      window._profDirty = false;
      var _pendingNav   = null;

      var CLIENT_PROF_FIELDS = [
        'prof_first_name', 'prof_middle_name', 'prof_last_name', 'prof_email', 'prof_username', 'prof_contact',
        'prof_civil_status', 'prof_citizenship', 'prof_gender', 'prof_dependents', 'prof_birth_date', 'prof_birthplace', 'prof_age',
        'prof_birth_region_code', 'prof_birth_region_name', 'prof_birth_province_code', 'prof_birth_province_name',
        'prof_birth_citymun_code', 'prof_birth_citymun_name', 'prof_birth_barangay_code', 'prof_birth_barangay_name',
        'prof_birth_region_select', 'prof_birth_province_select', 'prof_birth_citymun_select', 'prof_birth_barangay_select',
        'prof_employment_type', 'prof_tenure_months', 'prof_employer_name', 'prof_employer_phone', 'prof_employer_email', 'prof_employer_business_address',
        'prof_emp_region_code', 'prof_emp_region_name', 'prof_emp_province_code', 'prof_emp_province_name',
        'prof_emp_citymun_code', 'prof_emp_citymun_name', 'prof_emp_barangay_code', 'prof_emp_barangay_name',
        'prof_emp_region_select', 'prof_emp_province_select', 'prof_emp_citymun_select', 'prof_emp_barangay_select',
        'prof_address',
        'prof_street', 'prof_blk', 'prof_lot', 'prof_country', 'prof_zip_code', 'prof_subdivision_name',
        'prof_social_instagram', 'prof_social_twitter_x', 'prof_social_viber', 'prof_social_whatsapp',
        'prof_home_region_code', 'prof_home_region_name', 'prof_home_province_code',
        'prof_home_province_name', 'prof_home_citymun_code', 'prof_home_citymun_name',
        'prof_home_barangay_code', 'prof_home_barangay_name',
        'prof_home_region_select', 'prof_home_province_select', 'prof_home_citymun_select', 'prof_home_barangay_select'
      ];
      var CLIENT_PROF_PW_FIELDS = ['prof_old_password', 'prof_new_password', 'prof_confirm_password'];

      // Snapshot field values on load so "Leave" can restore them
      var _profSnapshot = {};
      CLIENT_PROF_FIELDS.forEach(function (id) {
        var el = document.getElementById(id);
        _profSnapshot[id] = el ? el.value : '';
      });

      // Called after a successful save to keep snapshot in sync with new saved state
      window._profRefreshSnapshot = function () {
        CLIENT_PROF_FIELDS.forEach(function (id) {
          var el = document.getElementById(id);
          if (el) _profSnapshot[id] = el.value;
        });
      };

      function _restoreProfileForm() {
        CLIENT_PROF_FIELDS.forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = _profSnapshot[id] || '';
        });
        CLIENT_PROF_PW_FIELDS.forEach(function (id) {
          var el = document.getElementById(id);
          if (el) el.value = '';
        });
        ['profPwLen', 'profPwUpper', 'profPwNum', 'profPwSpecial'].forEach(function (id) {
          var el = document.getElementById(id);
          if (!el) return;
          el.classList.remove('pw-ok');
          var icon = el.querySelector('i');
          if (icon) icon.className = 'fas fa-circle-dot';
        });
      }

      // Mark dirty when any profile input/select/textarea changes
      var profPage = document.getElementById('page-profile');
      if (profPage) {
        profPage.querySelectorAll('input:not([type=file]), textarea, select').forEach(function (el) {
          el.addEventListener('input',  function () { window._profDirty = true; });
          el.addEventListener('change', function () { window._profDirty = true; });
        });
      }
      // Also mark dirty on photo uploads
      ['avatarFileInput', 'bannerFileInput'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('change', function () { window._profDirty = true; });
      });

      // Hook called before any intra-dashboard navigation
      window._navGuard = function (targetPage) {
        var activePage = document.querySelector('.cp-page:not(.d-none)');
        if (!activePage || activePage.id !== 'page-profile') return;
        if (!window._profDirty) return;
        _pendingNav = targetPage;
        var modalEl = document.getElementById('clientUnsavedChangesModal');
        if (!modalEl) return;
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
        return false; // block navigation
      };

      // "Leave anyway" — restore form fields and navigate to pending target
      var leaveBtn = document.getElementById('clientUnsavedLeaveBtn');
      if (leaveBtn) {
        leaveBtn.addEventListener('click', function () {
          _restoreProfileForm();
          window._profDirty = false;
          var modalEl = document.getElementById('clientUnsavedChangesModal');
          var modal   = bootstrap.Modal.getInstance(modalEl);
          if (modal) modal.hide();
          if (_pendingNav && typeof showPage === 'function') {
            showPage(_pendingNav);
            _pendingNav = null;
          }
        });
      }
    })();

})();
