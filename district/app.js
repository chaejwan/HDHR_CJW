/* 행정구역 지도 뷰어.
 *
 * 네이버지도·카카오맵은 한 번에 한 구역만 검색해 보여준다. 이 페이지는 같은 원천의
 * 경계 데이터를 써서 여러 구역을 동시에 띄우고, 상위 구역(구 전체)도 함께 볼 수 있게 한다.
 *
 * 데이터는 tools/build_data.py 가 만든 data/manifest.json 을 기점으로 읽는다. */

(function () {
  'use strict';

  var PALETTE_KEYS = ['--c0', '--c1', '--c2', '--c3', '--c4', '--c5', '--c6', '--c7'];

  var BASEMAPS = {
    light: {
      tiles: ['https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
              'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png',
              'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png'],
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · © <a href="https://carto.com/attributions">CARTO</a>'
    },
    dark: {
      tiles: ['https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · © <a href="https://carto.com/attributions">CARTO</a>'
    },
    none: null
  };

  var CHOSUNG = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ',
                 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'];

  /** 한글 문자열의 초성만 뽑는다. "수내동" → "ㅅㄴㄷ" */
  function toChosung(text) {
    var out = '';
    for (var i = 0; i < text.length; i++) {
      var code = text.charCodeAt(i);
      out += (code >= 0xac00 && code <= 0xd7a3) ? CHOSUNG[Math.floor((code - 0xac00) / 588)] : text[i];
    }
    return out;
  }

  var IS_CHOSUNG_ONLY = /^[ㄱ-ㅎ]+$/;

  var $ = function (id) { return document.getElementById(id); };

  var state = {
    manifest: null,
    level: 'hjd',
    /** 체계별 선택 코드 집합. 비어 있으면 '전부 표시' 로 동작한다. */
    selected: { hjd: new Set(), bjd: new Set() },
    whole: false,
    labels: true,
    onlySelected: false,
    outline: true,
    basemap: 'light',
    query: '',
    hovered: null,
    loaded: {}
  };

  var map = null;
  var labelNodes = [];
  var readingHash = false;

  // ------------------------------------------------------------------ 색

  function palette() {
    var css = getComputedStyle(document.documentElement);
    return PALETTE_KEYS.map(function (key) { return css.getPropertyValue(key).trim() || '#888'; });
  }

  function colorExpression() {
    var pal = palette();
    var expr = ['match', ['get', 'color']];
    pal.forEach(function (color, i) { expr.push(i, color); });
    expr.push(pal[0]);
    return expr;
  }

  // ------------------------------------------------------------------ 상태

  function layerMeta() {
    return state.manifest.layers[state.level] || null;
  }

  function items() {
    var meta = layerMeta();
    return meta ? meta.items : [];
  }

  function selectedSet() {
    return state.selected[state.level];
  }

  /** 아무것도 고르지 않았으면 전부를 '보고 있는 것' 으로 친다.
   *  처음 열었을 때 빈 지도 대신 전체 구역이 보이는 게 이 페이지의 목적에 맞다. */
  function activeCodes() {
    var chosen = selectedSet();
    if (chosen.size) return chosen;
    return new Set(items().map(function (it) { return it.code; }));
  }

  function isExplicit() {
    return selectedSet().size > 0;
  }

  // ------------------------------------------------------------------ 지도

  function baseStyle() {
    return {
      version: 8,
      sources: {},
      layers: [{
        id: 'bg',
        type: 'background',
        paint: { 'background-color': getComputedStyle(document.documentElement).getPropertyValue('--map-bg').trim() || '#eceff3' }
      }]
    };
  }

  function initMap() {
    map = new maplibregl.Map({
      container: 'map',
      style: baseStyle(),
      center: [127.11, 37.36],
      zoom: 11.4,
      minZoom: 8,
      maxZoom: 17,
      attributionControl: false
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: 'metric' }), 'bottom-left');
    return new Promise(function (resolve) { map.on('load', resolve); });
  }

  function addBoundaryLayers() {
    map.addSource('regions', { type: 'geojson', data: emptyFC(), promoteId: 'code' });
    map.addSource('outline', { type: 'geojson', data: emptyFC() });

    map.addLayer({
      id: 'regions-fill',
      type: 'fill',
      source: 'regions',
      paint: {
        'fill-color': colorExpression(),
        'fill-opacity': [
          'case',
          ['boolean', ['feature-state', 'active'], false], 0.45,
          0.07
        ]
      }
    });

    map.addLayer({
      id: 'regions-line',
      type: 'line',
      source: 'regions',
      paint: {
        'line-color': colorExpression(),
        'line-width': ['case', ['boolean', ['feature-state', 'active'], false], 1.8, 0.7],
        'line-opacity': ['case', ['boolean', ['feature-state', 'active'], false], 0.95, 0.35]
      }
    });

    map.addLayer({
      id: 'regions-hover',
      type: 'line',
      source: 'regions',
      filter: ['==', ['get', 'code'], ''],
      paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': 0.9 }
    });

    map.addLayer({
      id: 'outline-fill',
      type: 'fill',
      source: 'outline',
      layout: { visibility: 'none' },
      paint: { 'fill-color': '#2f6df6', 'fill-opacity': 0.16 }
    });

    map.addLayer({
      id: 'outline-line',
      type: 'line',
      source: 'outline',
      paint: {
        'line-color': getComputedStyle(document.documentElement).getPropertyValue('--fg').trim() || '#16181d',
        'line-width': 2.4,
        'line-opacity': 0.75
      }
    });

    map.on('mousemove', 'regions-fill', onHover);
    map.on('mouseleave', 'regions-fill', clearHover);
    map.on('click', 'regions-fill', onMapClick);
  }

  function emptyFC() {
    return { type: 'FeatureCollection', features: [] };
  }

  function applyBasemap() {
    if (map.getLayer('basemap')) map.removeLayer('basemap');
    if (map.getSource('basemap')) map.removeSource('basemap');
    var conf = BASEMAPS[state.basemap];
    if (!conf) return;
    map.addSource('basemap', {
      type: 'raster',
      tiles: conf.tiles,
      tileSize: 256,
      maxzoom: 19,
      attribution: conf.attribution
    });
    map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap', paint: { 'raster-opacity': 0.9 } }, 'regions-fill');
  }

  // ------------------------------------------------------------------ 데이터

  function loadLevel(level) {
    if (state.loaded[level]) return Promise.resolve(state.loaded[level]);
    var meta = state.manifest.layers[level];
    if (!meta) return Promise.resolve(null);
    return fetch('data/' + meta.file)
      .then(function (res) {
        if (!res.ok) throw new Error(meta.file + ' 을(를) 불러오지 못했습니다 (HTTP ' + res.status + ')');
        return res.json();
      })
      .then(function (geojson) {
        state.loaded[level] = geojson;
        return geojson;
      });
  }

  // ------------------------------------------------------------------ 렌더

  function refreshMapData() {
    var geojson = state.loaded[state.level];
    if (!geojson) return;
    map.getSource('regions').setData(geojson);
    map.once('idle', syncFeatureStates);
    syncFeatureStates();
    applyVisibilityFilter();
    renderLabels();
  }

  function syncFeatureStates() {
    var active = activeCodes();
    items().forEach(function (it) {
      map.setFeatureState({ source: 'regions', id: it.code }, { active: active.has(it.code) });
    });
  }

  function applyVisibilityFilter() {
    var filter = null;
    if (state.onlySelected && isExplicit()) {
      filter = ['in', ['get', 'code'], ['literal', Array.from(selectedSet())]];
    }
    ['regions-fill', 'regions-line'].forEach(function (id) {
      map.setFilter(id, filter);
    });
    map.setLayoutProperty('outline-line', 'visibility', state.outline ? 'visible' : 'none');
    map.setLayoutProperty('outline-fill', 'visibility', state.whole ? 'visible' : 'none');
  }

  // ------------------------------------------------------------------ 라벨

  function renderLabels() {
    var host = $('labels');
    host.textContent = '';
    labelNodes = [];
    if (!state.labels) return;

    var active = activeCodes();
    var visibleOnly = state.onlySelected && isExplicit();

    items().forEach(function (it) {
      if (visibleOnly && !selectedSet().has(it.code)) return;
      var node = document.createElement('div');
      node.className = 'map-label' + (active.has(it.code) ? '' : ' dim');
      node.textContent = it.name;
      host.appendChild(node);
      labelNodes.push({ node: node, item: it });
    });
    positionLabels();
  }

  function positionLabels() {
    if (!labelNodes.length) return;
    var width = map.getContainer().clientWidth;
    var height = map.getContainer().clientHeight;

    labelNodes.forEach(function (entry) {
      var bbox = entry.item.bbox;
      var sw = map.project([bbox[0], bbox[1]]);
      var ne = map.project([bbox[2], bbox[3]]);
      // 화면에서 구역이 너무 작으면 글자가 서로 겹쳐 읽을 수 없다.
      var px = Math.min(Math.abs(ne.x - sw.x), Math.abs(sw.y - ne.y));
      var point = map.project(entry.item.label);
      var offscreen = point.x < -60 || point.y < -30 || point.x > width + 60 || point.y > height + 30;
      if (px < 46 || offscreen) {
        entry.node.style.display = 'none';
        return;
      }
      entry.node.style.display = '';
      entry.node.style.left = point.x + 'px';
      entry.node.style.top = point.y + 'px';
    });
  }

  // ------------------------------------------------------------------ 목록

  function matches(item, query) {
    if (!query) return true;
    var name = item.name;
    if (name.indexOf(query) !== -1) return true;
    if (IS_CHOSUNG_ONLY.test(query)) return toChosung(name).indexOf(query) !== -1;
    return false;
  }

  function highlight(name, query) {
    if (!query || IS_CHOSUNG_ONLY.test(query)) return document.createTextNode(name);
    var at = name.indexOf(query);
    if (at === -1) return document.createTextNode(name);
    var frag = document.createDocumentFragment();
    frag.appendChild(document.createTextNode(name.slice(0, at)));
    var mark = document.createElement('mark');
    mark.textContent = name.slice(at, at + query.length);
    frag.appendChild(mark);
    frag.appendChild(document.createTextNode(name.slice(at + query.length)));
    return frag;
  }

  function renderList() {
    var list = $('list');
    list.textContent = '';

    var meta = layerMeta();
    if (!meta) {
      var empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '이 체계의 경계 데이터가 아직 없습니다.';
      list.appendChild(empty);
      return;
    }

    var pal = palette();
    var chosen = selectedSet();

    list.appendChild(buildRow({
      whole: true,
      checked: state.whole,
      name: state.manifest.sgg + ' 전체',
      area: null,
      swatch: null,
      onToggle: function () {
        state.whole = !state.whole;
        applyVisibilityFilter();
        renderList();
        writeHash();
      }
    }));

    var shown = items().filter(function (it) { return matches(it, state.query); });
    if (!shown.length) {
      var none = document.createElement('li');
      none.className = 'empty';
      none.textContent = '"' + state.query + '" 과(와) 일치하는 구역이 없습니다.';
      list.appendChild(none);
    }

    shown.forEach(function (it) {
      list.appendChild(buildRow({
        checked: chosen.has(it.code),
        name: it.name,
        query: state.query,
        area: it.area_km2,
        swatch: pal[it.color % pal.length],
        onToggle: function () { toggleCode(it.code); },
        onHover: function (on) { setHover(on ? it.code : null); }
      }));
    });

    $('count-badge').textContent = chosen.size + ' / ' + items().length;
    renderSummary();
  }

  function buildRow(opts) {
    var li = document.createElement('li');
    li.className = 'item' + (opts.whole ? ' whole' : '') + (opts.checked ? ' selected' : '');
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', String(!!opts.checked));

    var box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!opts.checked;
    box.tabIndex = -1;
    li.appendChild(box);

    if (opts.swatch) {
      var swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = opts.swatch;
      li.appendChild(swatch);
    }

    var name = document.createElement('span');
    name.className = 'item-name';
    name.appendChild(opts.query ? highlight(opts.name, opts.query) : document.createTextNode(opts.name));
    li.appendChild(name);

    if (opts.area != null) {
      var area = document.createElement('span');
      area.className = 'item-area';
      area.textContent = opts.area.toFixed(2) + ' km²';
      li.appendChild(area);
    }

    li.addEventListener('click', function (event) {
      event.preventDefault();
      opts.onToggle();
    });
    if (opts.onHover) {
      li.addEventListener('mouseenter', function () { opts.onHover(true); });
      li.addEventListener('mouseleave', function () { opts.onHover(false); });
    }
    return li;
  }

  function renderSummary() {
    var chosen = selectedSet();
    var pool = items();
    var counted = chosen.size ? pool.filter(function (it) { return chosen.has(it.code); }) : pool;
    var area = counted.reduce(function (sum, it) { return sum + it.area_km2; }, 0);
    var head = chosen.size ? '선택 ' + chosen.size + '개' : '전체 ' + pool.length + '개';
    $('summary').innerHTML = '<b>' + head + '</b> · 합계 면적 ' + area.toFixed(2) + ' km²';
  }

  // ------------------------------------------------------------------ 조작

  function toggleCode(code) {
    var chosen = selectedSet();
    if (chosen.has(code)) chosen.delete(code); else chosen.add(code);
    afterSelectionChange();
  }

  function afterSelectionChange() {
    syncFeatureStates();
    applyVisibilityFilter();
    renderLabels();
    renderList();
    writeHash();
  }

  function setHover(code) {
    state.hovered = code;
    map.setFilter('regions-hover', ['==', ['get', 'code'], code || '']);
  }

  function onHover(event) {
    var feature = event.features && event.features[0];
    if (!feature) return;
    var code = feature.properties.code;
    if (code !== state.hovered) setHover(code);
    map.getCanvas().style.cursor = 'pointer';

    var tip = $('tooltip');
    tip.hidden = false;
    tip.textContent = feature.properties.name + ' · ' + Number(feature.properties.area_km2).toFixed(2) + ' km²';
    tip.style.left = event.point.x + 'px';
    tip.style.top = event.point.y + 'px';
  }

  function clearHover() {
    setHover(null);
    map.getCanvas().style.cursor = '';
    $('tooltip').hidden = true;
  }

  function onMapClick(event) {
    var feature = event.features && event.features[0];
    if (feature) toggleCode(feature.properties.code);
  }

  function fitToActive() {
    var active = activeCodes();
    var pool = items().filter(function (it) { return active.has(it.code); });
    if (!pool.length) return;
    var bounds = new maplibregl.LngLatBounds(
      [pool[0].bbox[0], pool[0].bbox[1]],
      [pool[0].bbox[2], pool[0].bbox[3]]
    );
    pool.forEach(function (it) {
      bounds.extend([it.bbox[0], it.bbox[1]]);
      bounds.extend([it.bbox[2], it.bbox[3]]);
    });
    map.fitBounds(bounds, { padding: 56, duration: 550 });
  }

  // ------------------------------------------------------------------ 체계 전환

  function switchLevel(level) {
    if (level === state.level) return;
    if (!state.manifest.layers[level]) return;
    state.level = level;
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.setAttribute('aria-selected', String(tab.dataset.level === level));
    });
    loadLevel(level).then(function () {
      refreshMapData();
      renderList();
      writeHash();
    });
  }

  // ------------------------------------------------------------------ URL

  function writeHash() {
    if (readingHash) return;
    var parts = ['level=' + state.level];
    var chosen = Array.from(selectedSet());
    if (chosen.length) parts.push('sel=' + chosen.join(','));
    if (state.whole) parts.push('whole=1');
    if (!state.labels) parts.push('labels=0');
    if (state.onlySelected) parts.push('only=1');
    if (!state.outline) parts.push('outline=0');
    if (state.basemap !== 'light') parts.push('base=' + state.basemap);
    history.replaceState(null, '', '#' + parts.join('&'));
  }

  function readHash() {
    var raw = location.hash.replace(/^#/, '');
    if (!raw) return;
    readingHash = true;
    raw.split('&').forEach(function (chunk) {
      var pair = chunk.split('=');
      var key = pair[0];
      var value = decodeURIComponent(pair[1] || '');
      if (key === 'level' && state.manifest.layers[value]) state.level = value;
      else if (key === 'sel' && value) value.split(',').forEach(function (code) { state.selected[state.level].add(code); });
      else if (key === 'whole') state.whole = value === '1';
      else if (key === 'labels') state.labels = value !== '0';
      else if (key === 'only') state.onlySelected = value === '1';
      else if (key === 'outline') state.outline = value !== '0';
      else if (key === 'base' && BASEMAPS.hasOwnProperty(value)) state.basemap = value;
    });
    readingHash = false;
  }

  // ------------------------------------------------------------------ 안내

  function showMissingLayerNotice() {
    if (state.manifest.layers.bjd) return;
    var box = $('notice');
    box.hidden = false;
    box.innerHTML =
      '<strong>법정동 경계는 아직 비어 있습니다.</strong><br>' +
      '행정동을 합쳐서 법정동을 만들 수는 없어(한 행정동이 여러 법정동을 관할하는 경우가 있음) ' +
      '공식 원천이 따로 필요합니다. 내려받은 뒤 ' +
      '<code>python3 tools/build_data.py --bjd-file &lt;파일&gt;</code> 을 실행하면 이 탭이 켜집니다. ' +
      '자세한 절차는 <code>tools/README.md</code> 를 보세요.';
  }

  function renderCredit() {
    var lines = [];
    Object.keys(state.manifest.layers).forEach(function (key) {
      var meta = state.manifest.layers[key];
      if (meta.source) lines.push(meta.label + ' 경계: ' + meta.source);
    });
    $('credit').textContent = lines.join(' / ');
  }

  // ------------------------------------------------------------------ 이벤트

  function wireEvents() {
    document.querySelectorAll('.tab').forEach(function (tab) {
      if (!state.manifest.layers[tab.dataset.level]) tab.disabled = true;
      tab.addEventListener('click', function () { switchLevel(tab.dataset.level); });
    });

    $('search').addEventListener('input', function (event) {
      state.query = event.target.value.trim();
      $('search-clear').hidden = !state.query;
      renderList();
    });

    $('search-clear').addEventListener('click', function () {
      $('search').value = '';
      state.query = '';
      $('search-clear').hidden = true;
      renderList();
      $('search').focus();
    });

    $('select-all').addEventListener('click', function () {
      var chosen = selectedSet();
      // 검색 중이면 걸러진 것만 담는 편이 자연스럽다.
      items().filter(function (it) { return matches(it, state.query); })
             .forEach(function (it) { chosen.add(it.code); });
      afterSelectionChange();
    });

    $('select-none').addEventListener('click', function () {
      selectedSet().clear();
      afterSelectionChange();
    });

    $('opt-labels').addEventListener('change', function (event) {
      state.labels = event.target.checked;
      renderLabels();
      writeHash();
    });

    $('opt-only-selected').addEventListener('change', function (event) {
      state.onlySelected = event.target.checked;
      applyVisibilityFilter();
      renderLabels();
      writeHash();
    });

    $('opt-outline').addEventListener('change', function (event) {
      state.outline = event.target.checked;
      applyVisibilityFilter();
      writeHash();
    });

    $('basemap').addEventListener('change', function (event) {
      state.basemap = event.target.value;
      applyBasemap();
      writeHash();
    });

    $('fit-btn').addEventListener('click', fitToActive);

    $('panel-toggle').addEventListener('click', function () {
      var collapsed = document.getElementById('app').classList.toggle('panel-collapsed');
      this.setAttribute('aria-expanded', String(!collapsed));
      this.textContent = collapsed ? '▶' : '◀';
      setTimeout(function () { map.resize(); }, 60);
    });

    map.on('move', positionLabels);
    map.on('zoom', positionLabels);
    map.on('resize', positionLabels);

    if (window.matchMedia) {
      var scheme = window.matchMedia('(prefers-color-scheme: dark)');
      var onScheme = function () {
        map.setPaintProperty('regions-fill', 'fill-color', colorExpression());
        map.setPaintProperty('regions-line', 'line-color', colorExpression());
        renderList();
      };
      if (scheme.addEventListener) scheme.addEventListener('change', onScheme);
    }
  }

  function syncControls() {
    $('opt-labels').checked = state.labels;
    $('opt-only-selected').checked = state.onlySelected;
    $('opt-outline').checked = state.outline;
    $('basemap').value = state.basemap;
    document.querySelectorAll('.tab').forEach(function (tab) {
      tab.setAttribute('aria-selected', String(tab.dataset.level === state.level));
    });
  }

  // ------------------------------------------------------------------ 시작

  function fail(message) {
    var box = $('notice');
    box.hidden = false;
    box.innerHTML = '<strong>데이터를 불러오지 못했습니다.</strong><br>' + message;
  }

  fetch('data/manifest.json')
    .then(function (res) {
      if (!res.ok) throw new Error('manifest.json (HTTP ' + res.status + ')');
      return res.json();
    })
    .then(function (manifest) {
      state.manifest = manifest;
      if (!manifest.layers[state.level]) {
        state.level = Object.keys(manifest.layers)[0];
      }
      $('scope-label').textContent = manifest.sido + ' ' + manifest.sgg;
      document.title = '행정구역 지도 — ' + manifest.sgg;
      readHash();
      return initMap();
    })
    .then(function () {
      addBoundaryLayers();
      applyBasemap();
      return Promise.all([
        loadLevel(state.level),
        fetch('data/' + state.manifest.outline).then(function (r) { return r.json(); })
      ]);
    })
    .then(function (results) {
      map.getSource('outline').setData(results[1]);
      wireEvents();
      syncControls();
      refreshMapData();
      renderList();
      renderCredit();
      showMissingLayerNotice();
      fitToActive();
    })
    .catch(function (error) {
      fail(String(error && error.message ? error.message : error));
      if (window.console) console.error(error);
    });
})();
