const $ = (id) => document.getElementById(id);
let photos = [], visible = [], limit = 60, current = 0, csrf = '', admin = false, loading = false;
let setupNeeded = false;
let hiddenOnly = false;
let aliases = {}, titles = {};
const selected = new Set();
let loadSequence = 0;
let route = { view: 'photos', album: null };
const text = (tag, value, cls) => { const el = document.createElement(tag); el.textContent = value; if (cls) el.className = cls; return el; };
const media = (name) => `${admin ? '/api/media/' : '/media/'}${encodeURIComponent(name)}`;
const albumName = (name) => name === null ? '未分類' : (titles[name] || name);
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'X-CSRF-Token': csrf, ...options.headers } });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || (response.status === 401 ? 'ログインし直してください。' : `処理に失敗しました (${response.status})`));
  return result;
}
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function readRoute() {
  const hash = location.hash.slice(1);
  route = { view: hash === 'albums' ? 'albums' : 'photos', album: null };
  if (hash.startsWith('album/')) {
    try { route = { view: 'album', album: decodeURIComponent(hash.slice(6)) || null }; }
    catch { location.hash = 'albums'; }
  }
  $('search').value = ''; selected.clear(); limit = 60; render();
}
function render() {
  const q = $('search').value.trim().toLowerCase();
  const filteringHidden = admin && hiddenOnly;
  const filteredPhotos = filteringHidden ? photos.filter(p => p.hidden) : photos;
  $('hidden-only').setAttribute('aria-pressed', String(filteringHidden));
  $('hidden-only').textContent = filteringHidden ? 'すべての画像に戻す' : '非表示のみ表示';
  const isAlbums = route.view === 'albums';
  if (route.view === 'album') route.album = aliases[route.album] || route.album;
  const historicalNames = name => Object.keys(aliases).filter(alias => aliases[alias] === name).join(' ');
  const title = isAlbums ? 'アルバム' : route.view === 'album' ? albumName(route.album) : 'すべての写真';
  $('title').textContent = title;
  $('search').placeholder = isAlbums ? 'MCIDでアルバムを検索' : 'MCID・ファイル名で検索';
  $('nav-photos').classList.toggle('active', route.view === 'photos');
  $('nav-albums').classList.toggle('active', route.view !== 'photos');
  $('nav-photos').setAttribute('aria-current', route.view === 'photos' ? 'page' : 'false');
  $('nav-albums').setAttribute('aria-current', route.view !== 'photos' ? 'page' : 'false');
  const grid = $('grid'); grid.replaceChildren(); grid.className = isAlbums ? 'album-grid' : 'photo-grid';
  let items;
  if (isAlbums) {
    visible = [];
    const grouped = new Map();
    for (const p of filteredPhotos) {
      if (!grouped.has(p.album)) grouped.set(p.album, { name: p.album, cover: p, count: 0 });
      grouped.get(p.album).count++;
    }
    items = [...grouped.values()].filter(a => `${albumName(a.name)} ${a.name || ''} ${historicalNames(a.name)}`.toLowerCase().includes(q));
    if ($('sort').value === 'name') items.sort((a, b) => albumName(a.name).localeCompare(albumName(b.name)));
    if ($('sort').value === 'old') items.reverse();
  } else {
    items = filteredPhotos.filter(p => (route.view !== 'album' || p.album === route.album) && `${albumName(p.album)} ${p.album || ''} ${historicalNames(p.album)} ${p.filename}`.toLowerCase().includes(q));
    if ($('sort').value === 'name') items.sort((a, b) => a.filename.localeCompare(b.filename));
    if ($('sort').value === 'old') items.reverse();
    visible = items;
  }
  $('count').textContent = `${items.length.toLocaleString()} ${isAlbums ? 'アルバム' : '枚'}`;
  for (const [index, item] of items.slice(0, limit).entries()) {
    const card = document.createElement(isAlbums ? 'a' : 'button');
    card.className = isAlbums ? 'album-card' : 'photo-card';
    const photo = isAlbums ? item.cover : item;
    const img = document.createElement('img'); img.src = media(photo.thumb); img.alt = isAlbums ? `${albumName(item.name)}のアルバム` : photo.filename;
    img.loading = index < 12 ? 'eager' : 'lazy'; img.decoding = 'async'; img.width = 320; img.height = 320;
    card.append(img);
    if (isAlbums) {
      card.href = `#album/${encodeURIComponent(item.name || '')}`;
      card.append(text('h2', albumName(item.name)), text('p', `${item.count.toLocaleString()} 枚の写真`));
    } else {
      card.append(text('span', photo.filename, 'caption'));
      card.addEventListener('click', () => openPhoto(index));
    }
    if (!isAlbums && admin) {
      const wrapper = document.createElement('div'); wrapper.className = 'photo-item';
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'photo-select';
      checkbox.setAttribute('aria-label', `${photo.filename}を選択`); checkbox.checked = selected.has(photo.id);
      checkbox.onchange = () => { if (checkbox.checked && selected.size >= 500) { checkbox.checked = false; notice('一度に選択できるのは500枚までです。'); return; } checkbox.checked ? selected.add(photo.id) : selected.delete(photo.id); updateSelection(); };
      wrapper.append(card, checkbox);
      if (photo.hidden) wrapper.append(text('span', '非表示', 'hidden-badge'));
      grid.append(wrapper);
    } else grid.append(card);
  }
  $('empty').hidden = items.length > 0;
  $('empty-title').textContent = loading ? '写真を読み込んでいます' : q ? '見つかりませんでした' : isAlbums ? 'まだアルバムがありません' : 'まだ写真がありません';
  if (!loading && !q && filteringHidden) $('empty-title').textContent = '非表示の画像はありません';
  $('empty-text').textContent = filteringHidden && !q ? '「すべての画像に戻す」で絞り込みを解除できます。' : q ? '別のMCIDやファイル名で検索してみてください。' : '写真が追加されると、ここに表示されます。';
  $('empty-back').hidden = !q && route.view === 'photos';
  $('more').hidden = items.length <= limit;
  updateMergeOptions();
  updateSelection();
}
async function load() {
  const sequence = ++loadSequence;
  loading = true; render();
  try {
    const response = await fetch(admin ? '/api/catalog' : '/catalog.json', { cache: 'reload' });
    if (!response.ok) throw new Error('写真の一覧を読み込めませんでした。ブラウザーでページを再読み込みしてください。');
    const result = await response.json(); if (sequence !== loadSequence) return; photos = result.photos; aliases = result.aliases || {}; titles = result.titles || {};
    if (sequence !== loadSequence) return;
    const present = new Set(photos.map(p => p.id)); for (const id of selected) if (!present.has(id)) selected.delete(id);
    notice('');
  } catch (error) { notice(error.message); }
  finally { if (sequence === loadSequence) { loading = false; render(); } }
}
function openPhoto(index) {
  current = index; resetZoom();
  const p = visible[index]; if (!p) return;
  $('full-image').src = media(p.original); $('full-image').alt = p.filename;
  $('viewer-name').textContent = p.filename;
  $('viewer-info').textContent = `${albumName(p.album)} · ${p.width} × ${p.height} · ${index + 1} / ${visible.length}`;
  $('original').href = media(p.original); $('delete').hidden = !admin; $('toggle-hidden').hidden = !admin; $('toggle-hidden').textContent = p.hidden ? '公開する' : '非表示にする';
  $('previous').disabled = index === 0; $('next').disabled = index === visible.length - 1;
  if (!$('viewer').open) $('viewer').showModal();
}
function adminUI() {
  $('admin-panel').hidden = !admin;
  if (!admin) hiddenOnly = false;
  updateMergeOptions();
  $('admin-link').textContent = admin ? '管理画面 ↗' : '管理者ログイン ↗';
}
async function sessionStatus() {
  try {
    const state = await api('/api/session'); publicBase = state.public_url || location.origin; admin = state.authenticated; csrf = state.csrf || ''; adminUI();
    setupNeeded = !state.configured;
    if (setupNeeded || (['/admin', '/setup'].includes(location.pathname) && !admin)) showLogin();
    const reason = new URLSearchParams(location.search).get('auth');
    const messages = { password: '保存されているパスワードと一致しません。入力を確認するか、サーバーで再設定してください。', limited: '試行回数が多すぎます。5分後に再試行してください。', origin: '公開URLまたはNPMのHTTPS転送設定が一致していません。', input: '入力内容を確認してください。パスワードは12文字以上です。', code: '初回設定コードが違います。', configured: '設定済みです。管理者パスワードでログインしてください。', unconfigured: '初回設定が必要です。', server: '認証処理に失敗しました。サーバーの状態を確認してください。' };
    if (reason && !admin) $('login-error').textContent = messages[reason] || 'ログインをやり直してください。';
  } catch { notice('管理機能に接続できません。閲覧はそのまま利用できます。'); }
}
function showLogin() {
  $('login-error').textContent = '';
  $('auth-title').textContent = setupNeeded ? 'はじめてのセットアップ' : '管理者ログイン';
  $('auth-description').textContent = setupNeeded ? '起動ログの設定コードと、新しい管理者パスワードを入力してください。' : '写真の追加・削除にはログインが必要です。';
  $('setup-code-label').hidden = !setupNeeded; $('confirm-label').hidden = !setupNeeded;
  const form = $('login-form');
  form.action = setupNeeded ? '/auth/setup' : '/auth/login';
  form.elements.setup_code.required = setupNeeded; form.elements.confirm.required = setupNeeded;
  form.elements.setup_code.disabled = !setupNeeded; form.elements.confirm.disabled = !setupNeeded;
  form.elements.password.minLength = setupNeeded ? 12 : 1;
  form.elements.password.autocomplete = setupNeeded ? 'new-password' : 'current-password';
  form.querySelector('button[type=submit]').textContent = setupNeeded ? '設定してはじめる' : 'ログイン';
  if (!$('login-dialog').open) $('login-dialog').showModal();
}
$('admin-link').onclick = event => { event.preventDefault(); if (admin) $('admin-panel').scrollIntoView({ behavior: 'smooth' }); else showLogin(); };
$('close-login').onclick = () => $('login-dialog').close();
$('login-form').onsubmit = event => {
  $('admin-password').type = 'password';
  // A native form submission and real navigation let password managers recognize success.
  if (setupNeeded && event.target.elements.password.value !== event.target.elements.confirm.value) {
    event.preventDefault(); $('login-error').textContent = 'パスワードが一致しません。';
  }
};
$('show-password').onclick = () => {
  const field = $('admin-password'), show = field.type === 'password';
  field.type = show ? 'text' : 'password'; $('show-password').setAttribute('aria-pressed', String(show));
  $('show-password').textContent = show ? 'パスワードを隠す' : 'パスワードを表示';
};
$('admin-password').addEventListener('keyup', event => { $('caps-lock').hidden = !event.getModifierState('CapsLock'); });
$('logout').onclick = async () => { try { await api('/api/logout', { method: 'POST' }); admin = false; csrf = ''; photos = []; aliases = {}; titles = {}; selected.clear(); $('viewer').close(); adminUI(); await load(); } catch (e) { notice(e.message); } };
$('files').onchange = async event => {
  const files = [...event.target.files]; if (!files.length) return;
  event.target.disabled = true; $('logout').disabled = true; $('upload-errors').replaceChildren();
  let success = 0, duplicates = 0, failed = 0;
  for (let i = 0; i < files.length; i++) {
    $('upload-status').textContent = `アップロード中 ${i + 1} / ${files.length}：${files[i].name}`;
    try {
      if (files[i].size > 20 * 1024 * 1024) throw new Error('20MBを超えています');
      const form = new FormData(); form.append('file', files[i]);
      const result = await api('/api/photos', { method: 'POST', body: form });
      result.duplicate ? duplicates++ : success++;
    } catch (error) { failed++; $('upload-errors').append(text('li', `${files[i].name}：${error.message}`)); }
  }
  $('upload-status').textContent = `完了：${success}枚追加・${duplicates}枚は登録済み・${failed}枚失敗。一覧への反映には最大60秒かかる場合があります。`;
  event.target.value = ''; event.target.disabled = false; $('logout').disabled = false; await load();
};
function updateMergeOptions() {
  if (!admin) return;
  const source = $('merge-source');
  const previous = source.value;
  const names = [...new Set(photos.map(p => p.album).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  source.replaceChildren(); $('album-names').replaceChildren();
  const placeholder = text('option', 'アルバムを選択'); placeholder.value = ''; source.append(placeholder);
  for (const name of names) {
    const option = text('option', name); option.value = name; source.append(option);
    const suggestion = document.createElement('option'); suggestion.value = name; $('album-names').append(suggestion);
  }
  source.value = names.includes(previous) ? previous : '';
  const editor = $('edit-album'), previousAlbum = editor.value;
  editor.replaceChildren(); const first = text('option', 'アルバムを選択'); first.value = ''; editor.append(first);
  for (const name of names) { const option = text('option', `${albumName(name)} (${name})`); option.value = name; editor.append(option); }
  editor.value = names.includes(previousAlbum) ? previousAlbum : '';
}
$('merge-form').onsubmit = async event => {
  event.preventDefault();
  const source = $('merge-source').value;
  const target = $('merge-target').value.trim();
  const count = photos.filter(p => p.album === source).length;
  if (!source || !target) return;
  if (!confirm(`「${source}」の${count}枚を「${target}」へ統合します。旧IDで追加する画像も統合先に入ります。この操作は画面から元に戻せません。続けますか？`)) return;
  const button = event.target.querySelector('button'); button.disabled = true;
  try {
    const result = await api('/api/albums/merge', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source, target }) });
    await load(); $('merge-target').value = '';
    $('merge-status').textContent = `${result.moved}枚を「${result.target}」へ統合しました。一般公開の一覧への反映には最大60秒かかります。`;
  } catch (error) { $('merge-status').textContent = error.message; }
  finally { button.disabled = false; }
};
$('delete').onclick = async () => {
  const p = visible[current]; if (!p || !confirm(`「${p.filename}」を削除しますか？`)) return;
  $('delete').disabled = true;
  try {
    const result = await api(`/api/photos/${p.id}`, { method: 'DELETE' }); $('viewer').close(); await load();
    cacheNotice('削除しました。', result.purge_paths);
  } catch (e) { notice(e.message); $('viewer').close(); }
  finally { $('delete').disabled = false; }
};
$('close-viewer').onclick = () => $('viewer').close();
$('viewer').addEventListener('close', () => { $('full-image').removeAttribute('src'); });
$('previous').onclick = () => { if (current > 0) openPhoto(current - 1); };
$('next').onclick = () => { if (current < visible.length - 1) openPhoto(current + 1); };
$('viewer').addEventListener('click', event => { if (event.target === $('viewer')) $('viewer').close(); });
$('search').oninput = () => { limit = 60; render(); };
$('sort').onchange = () => { limit = 60; render(); };
$('more').onclick = () => { limit += 60; render(); };
window.addEventListener('hashchange', readRoute);
window.addEventListener('keydown', event => {
  if ($('viewer').open) { if (event.key === 'ArrowLeft') $('previous').click(); if (event.key === 'ArrowRight') $('next').click(); }
  else if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && !$('login-dialog').open) { event.preventDefault(); $('search').focus(); }
});
function updateSelection() {
  $('selected-count').textContent = `${selected.size}枚選択`;
  for (const id of ['hide-selected', 'show-selected', 'delete-selected', 'clear-selection']) $(id).disabled = selected.size === 0;
  $('select-all').disabled = route.view === 'albums' || !visible.length;
}
function cacheNotice(message, paths = []) {
  notice(message + ' Cloudflareのキャッシュが残る場合があります。非表示・削除をすぐ反映する場合は下記URLをパージしてください。');
  if (!paths.length) return;
  const details = document.createElement('details'); details.append(text('summary', 'キャッシュ削除対象のURL'));
  const area = document.createElement('textarea'); area.readOnly = true; area.rows = 4;
  area.value = paths.map(path => (publicBase || location.origin) + path).join('\n');
  area.setAttribute('aria-label', 'キャッシュ削除対象のURL'); details.append(area); $('notice').append(details);
}
let publicBase = location.origin;
async function batchAction(action, ids) {
  if (!ids.length) return;
  const verb = { delete: '削除', hide: '非表示に変更', show: '公開' }[action];
  if (!confirm(`${ids.length}枚を${verb}しますか？${action === 'delete' ? '削除は元に戻せません。' : ''}`)) return;
  for (const id of ['delete-selected', 'hide-selected', 'show-selected', 'toggle-hidden']) $(id).disabled = true;
  try {
    const result = await api('/api/photos/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, ids }) });
    selected.clear(); $('viewer').close(); await load(); cacheNotice(`${result.changed}枚を${verb}しました。`, result.purge_paths);
  } catch (error) { notice(error.message); }
  finally { updateSelection(); $('toggle-hidden').disabled = false; }
}
$('hidden-only').onclick = () => {
  if (!admin) return;
  hiddenOnly = !hiddenOnly; selected.clear(); limit = 60; $('viewer').close(); render();
};
$('select-all').onclick = () => { selected.clear(); for (const p of visible.slice(0, 500)) selected.add(p.id); render(); };
$('clear-selection').onclick = () => { selected.clear(); render(); };
for (const [id, action] of [['hide-selected', 'hide'], ['show-selected', 'show'], ['delete-selected', 'delete']]) $(id).onclick = () => batchAction(action, [...selected]);
$('toggle-hidden').onclick = () => { const p = visible[current]; if (p) batchAction(p.hidden ? 'show' : 'hide', [p.id]); };
$('edit-album').onchange = () => { $('album-title').value = $('edit-album').value ? albumName($('edit-album').value) : ''; };
$('album-edit-form').onsubmit = async event => {
  event.preventDefault(); const button = event.target.querySelector('button[type=submit]'); button.disabled = true;
  try {
    await api('/api/albums/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ album: $('edit-album').value, title: $('album-title').value }) });
    await load(); $('album-status').textContent = '表示名を保存しました。';
  } catch (error) { $('album-status').textContent = error.message; }
  finally { button.disabled = false; }
};
$('delete-album').onclick = async () => {
  const album = $('edit-album').value; if (!album) return;
  const count = photos.filter(p => p.album === album).length;
  if (!confirm(`アルバム「${albumName(album)}」と、その中の${count}枚（非表示の画像を含む）を削除しますか？元には戻せません。`)) return;
  $('delete-album').disabled = true;
  try {
    const result = await api('/api/albums/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ album }) });
    $('album-title').value = ''; selected.clear(); await load();
    cacheNotice(`アルバムと${result.deleted}枚を削除しました。`, result.purge_paths);
  } catch (error) { $('album-status').textContent = error.message; }
  finally { $('delete-album').disabled = false; }
};
$('icon-file').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  event.target.disabled = true;
  try {
    if (file.size >= 2 * 1024 * 1024) throw new Error('2MB未満の画像を選択してください。');
    const form = new FormData(); form.append('file', file);
    await api('/api/icon', { method: 'POST', body: form });
    const url = `/site-icon.png?v=${Date.now()}`; $('favicon').href = url;
    $('icon-status').textContent = 'タブのアイコンを変更しました。';
  } catch (error) { $('icon-status').textContent = error.message; }
  finally { event.target.value = ''; event.target.disabled = false; }
};
$('brand').onclick = event => { event.preventDefault(); window.scrollTo({ top: 0, behavior: 'smooth' }); };
// Keep an accessible manual fallback while automatically loading near the bottom.
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && !loading && !$('more').hidden) { limit += 60; render(); }
  }, { rootMargin: '500px' });
  observer.observe($('load-sentinel'));
}
let zoom = 1, panX = 0, panY = 0, gesture = null;
const pointers = new Map();
function applyZoom() {
  const stage = $('zoom-stage'), img = $('full-image');
  const maxX = Math.max(0, (img.offsetWidth * zoom - stage.clientWidth) / 2);
  const maxY = Math.max(0, (img.offsetHeight * zoom - stage.clientHeight) / 2);
  panX = Math.max(-maxX, Math.min(maxX, panX)); panY = Math.max(-maxY, Math.min(maxY, panY));
  img.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  stage.classList.toggle('zoomed', zoom > 1); $('zoom-reset').textContent = `${Math.round(zoom * 100)}%`;
  $('zoom-out').disabled = zoom <= 1; $('zoom-in').disabled = zoom >= 8;
}
function resetZoom() { zoom = 1; panX = 0; panY = 0; pointers.clear(); gesture = null; applyZoom(); }
function zoomAt(next, x, y) {
  next = Math.max(1, Math.min(8, next));
  const rect = $('zoom-stage').getBoundingClientRect();
  const ax = (x ?? rect.left + rect.width / 2) - rect.left - rect.width / 2;
  const ay = (y ?? rect.top + rect.height / 2) - rect.top - rect.height / 2;
  panX = ax - (ax - panX) * next / zoom; panY = ay - (ay - panY) * next / zoom; zoom = next; applyZoom();
}
function pointerGeometry() {
  const points = [...pointers.values()]; if (!points.length) return null;
  if (points.length === 1) return { x: points[0].x, y: points[0].y, distance: 0, count: 1 };
  const [a, b] = points; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, distance: Math.hypot(a.x - b.x, a.y - b.y), count: 2 };
}
$('zoom-stage').addEventListener('wheel', event => {
  event.preventDefault(); zoomAt(zoom * Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.03 : 0.002)), event.clientX, event.clientY);
}, { passive: false });
$('zoom-stage').addEventListener('pointerdown', event => {
  if (event.pointerType === 'mouse' && event.button !== 0) return;
  event.preventDefault(); $('zoom-stage').setPointerCapture(event.pointerId);
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }); gesture = pointerGeometry();
});
$('zoom-stage').addEventListener('pointermove', event => {
  if (!pointers.has(event.pointerId)) return;
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }); const next = pointerGeometry();
  if (gesture && next && next.count === gesture.count) {
    panX += next.x - gesture.x; panY += next.y - gesture.y;
    if (next.count === 2 && gesture.distance > 0) zoomAt(zoom * next.distance / gesture.distance, next.x, next.y);
    else applyZoom();
  }
  gesture = next;
});
for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) $('zoom-stage').addEventListener(event, e => { pointers.delete(e.pointerId); gesture = pointerGeometry(); });
$('zoom-stage').ondblclick = event => { if (zoom > 1) resetZoom(); else zoomAt(2.5, event.clientX, event.clientY); };
$('full-image').onload = resetZoom;
$('zoom-in').onclick = () => zoomAt(zoom * 1.4);
$('zoom-out').onclick = () => zoomAt(zoom / 1.4);
$('zoom-reset').onclick = resetZoom;
$('viewer').addEventListener('close', resetZoom);
window.addEventListener('resize', applyZoom);

readRoute(); sessionStatus().then(load);
