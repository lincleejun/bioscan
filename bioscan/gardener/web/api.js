/* Gardener · HTTP API 客户端（docs/gardener/contract.md §6）。所有业务数据都来自服务；浏览器只保存 UI 偏好。 */
'use strict';

const API = '/api/v1';

class ApiError extends Error {
  constructor(status, body) {
    super((body && body.error) || `HTTP ${status}`);
    this.status = status;
    this.body = body || {};
  }
}

async function api(method, path, body, query) {
  const q = query ? Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '') : [];
  const url = API + path + (q.length ? '?' + new URLSearchParams(q).toString() : '');
  const opt = { method, headers: { 'X-Gardener-Actor': 'ui', Accept: 'application/json' } };
  if (body !== undefined && body !== null) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let r;
  try { r = await fetch(url, opt); } catch (e) { throw new ApiError(0, { error: '无法连接 Gardener 服务', code: 'unreachable' }); }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}

/* 分页取完一个筛选的全部结果（轮询 changed_after 时用；量小）。 */
async function apiAll(query) {
  let offset = 0, items = [], total = 0;
  do {
    const page = await api('GET', '/assets', null, Object.assign({}, query, { limit: 200, offset }));
    items = items.concat(page.items);
    total = page.total;
    offset += page.items.length;
    if (!page.items.length) break;
  } while (offset < total);
  return items;
}

const mediaUrl = (id, kind) => `${API}/assets/${encodeURIComponent(id)}/${kind}`;

/* 只保存 UI 偏好（上次页面、网格尺寸），键统一以 gardener.ui. 开头；不可用时忽略。 */
const prefs = {
  get(k, d) { try { const v = localStorage.getItem('gardener.ui.' + k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('gardener.ui.' + k, String(v)); } catch (e) { /* 存储不可用 */ } },
};
