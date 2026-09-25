// 人間による★評価の共通処理(Cloudflare Pages Functions + KV)。
// KV namespace を "RATINGS" という名前でPagesプロジェクトに紐づけると有効になる。
//
// 保存するもの:
//   aggs                      … 作品ごとの合計点と件数 { 作品ID: { sum, count } }
//   vote:<作品ID>:<ハッシュ>   … 同じ人の重複評価を防ぐための記録(30日で自動削除)
//     ハッシュは「接続元IP + 作品ID + 秘密の文字列」をSHA-256にしたもので、IPアドレスそのものは保存しない。
// 評価はサイトへの一方向の送信で、利用者同士のやりとりは発生しない。

export const NOVEL_ID = /^\d{8}-[0-9a-f]{6}$/;
const VOTE_TTL = 60 * 60 * 24 * 30;

export function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

export async function loadAggs(kv) {
  return (await kv.get("aggs", "json")) || {};
}

export function summarize(aggs) {
  const out = {};
  for (const [id, { sum, count }] of Object.entries(aggs)) {
    if (count > 0) out[id] = { avg: Math.round((sum / count) * 10) / 10, count };
  }
  return out;
}

async function voterHash(ip, novel, salt) {
  const data = new TextEncoder().encode(`${salt}:${novel}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function novelExists(env, requestUrl, novel) {
  const res = await env.ASSETS.fetch(new URL("/data/novels.json", requestUrl));
  if (!res.ok) return false;
  const novels = await res.json();
  return novels.some((n) => n.id === novel);
}

export async function rate(env, request, novel, score) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const key = `vote:${novel}:${await voterHash(ip, novel, env.RATING_SALT || "ai-bunko")}`;
  const prev = await env.RATINGS.get(key);
  const aggs = await loadAggs(env.RATINGS);
  const a = aggs[novel] || { sum: 0, count: 0 };
  if (prev) {
    a.sum += score - Number(prev); // 評価のつけ直し
  } else {
    a.sum += score;
    a.count += 1;
  }
  aggs[novel] = a;
  await env.RATINGS.put("aggs", JSON.stringify(aggs));
  await env.RATINGS.put(key, String(score), { expirationTtl: VOTE_TTL });
  return { avg: Math.round((a.sum / a.count) * 10) / 10, count: a.count, yours: score };
}
