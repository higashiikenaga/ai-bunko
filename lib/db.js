// 人間による★評価と閲覧数(Cloudflare Pages Functions + D1)。
// D1データベースを "DB" という名前でPagesプロジェクトに紐づけると有効になる。表は初回アクセス時に自動作成する。
//
// 保存するもの(いずれも個人を特定できる情報は含まない):
//   votes      … 作品ごとの★評価。重複評価を防ぐため、評価者は「接続元IP + 作品ID + 秘密の文字列」の
//                SHA-256ハッシュ(元のIPには戻せない)で区別する
//   views      … 作品ごとの日別の閲覧数(日本時間)
//   view_seen  … 同じ人の同じ日の閲覧を二重に数えないための記録(上と同じハッシュ)。2日で削除
//   pred_votes … 展開予想の票(上と同じハッシュで1人1票)
//   odai       … お題箱に届いたお題と件数(誰が送ったかは保存しない)
//   odai_seen  … お題の投稿を1人1日1回にするための記録(上と同じハッシュ)。2日で削除
//   arena_votes … アリーナ(2つのAIの掌編の読み比べ)の票(上と同じハッシュで1対戦1人1票)
// 評価・閲覧はサイトへの一方向の送信で、利用者同士のやりとりは発生しない。

export const NOVEL_ID = /^\d{8}-[0-9a-f]{6}$/;

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS votes (novel TEXT NOT NULL, voter TEXT NOT NULL, score INTEGER NOT NULL,
     updated_at INTEGER NOT NULL, PRIMARY KEY (novel, voter))`,
  `CREATE TABLE IF NOT EXISTS views (novel TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
     PRIMARY KEY (novel, day))`,
  `CREATE TABLE IF NOT EXISTS view_seen (novel TEXT NOT NULL, voter TEXT NOT NULL, day TEXT NOT NULL,
     PRIMARY KEY (novel, voter, day))`,
  // 展開予想: 作品・話ごとに1人1票(つけ直し可)
  `CREATE TABLE IF NOT EXISTS pred_votes (novel TEXT NOT NULL, chapter INTEGER NOT NULL, voter TEXT NOT NULL,
     choice INTEGER NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (novel, chapter, voter))`,
  // お題箱: 同じお題はまとめて数える。投稿者は1日1回まで(odai_seen で判定し、2日で消す)
  `CREATE TABLE IF NOT EXISTS odai (word TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 1,
     first_at INTEGER NOT NULL, last_at INTEGER NOT NULL)`,
  `CREATE TABLE IF NOT EXISTS odai_seen (voter TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY (voter, day))`,
  // アリーナ: 対戦ごとに1人1票(つけ直し可)
  `CREATE TABLE IF NOT EXISTS arena_votes (match TEXT NOT NULL, voter TEXT NOT NULL, choice TEXT NOT NULL,
     updated_at INTEGER NOT NULL, PRIMARY KEY (match, voter))`,
];
let schemaReady = false;

export async function db(env) {
  if (!env.DB) return null;
  if (!schemaReady) {
    await env.DB.batch(SCHEMA.map((s) => env.DB.prepare(s)));
    schemaReady = true;
  }
  return env.DB;
}

export function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

export const notConfigured = () => json({ error: "この機能は未設定です" }, 503);

export function sameOrigin(request) {
  const origin = request.headers.get("Origin");
  return !!origin && new URL(origin).host === new URL(request.url).host;
}

export async function readBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

export async function voterHash(env, request, novel) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const data = new TextEncoder().encode(`${env.RATING_SALT || "ai-bunko"}:${novel}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].slice(0, 16).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function novelExists(env, requestUrl, novel) {
  const res = await env.ASSETS.fetch(new URL("/data/novels.json", requestUrl));
  if (!res.ok) return false;
  return (await res.json()).some((n) => n.id === novel);
}

// 日本時間の日付 "YYYY-MM-DD"(offsetDays 日前)
export function jstDay(offsetDays = 0) {
  const d = new Date(Date.now() + 9 * 3600 * 1000 - offsetDays * 86400 * 1000);
  return d.toISOString().slice(0, 10);
}
