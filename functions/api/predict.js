// POST /api/predict  { "novel": "...", "chapter": 5, "choice": 0〜2 } … 次の話の展開予想に投票(つけ直し可)
import { NOVEL_ID, db, json, notConfigured, readBody, sameOrigin, voterHash } from "../../lib/db.js";

export async function onRequestPost({ request, env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return json({ error: "不正なリクエストです" }, 403);

  const body = await readBody(request);
  const novel = String(body?.novel || "");
  const chapter = Number(body?.chapter);
  const choice = Number(body?.choice);
  if (!NOVEL_ID.test(novel) || !Number.isInteger(chapter) || !Number.isInteger(choice)) {
    return json({ error: "不正なリクエストです" }, 400);
  }
  // 受付中の予想か(作品データの prediction と一致するか)を確かめる
  const res = await env.ASSETS.fetch(new URL("/data/novels.json", request.url));
  const n = res.ok ? (await res.json()).find((x) => x.id === novel) : null;
  const p = n && n.prediction;
  if (!p || p.chapter !== chapter || choice < 0 || choice >= p.options.length) {
    return json({ error: "この予想は受付を終了しました" }, 409);
  }

  const voter = await voterHash(env, request, `${novel}:${chapter}:predict`);
  await DB.prepare(
    `INSERT INTO pred_votes (novel, chapter, voter, choice, updated_at) VALUES (?1, ?2, ?3, ?4, ?5)
     ON CONFLICT(novel, chapter, voter) DO UPDATE SET choice = excluded.choice, updated_at = excluded.updated_at`,
  ).bind(novel, chapter, voter, choice, Date.now()).run();
  return json({ counts: await counts(DB, novel, chapter, p.options.length) });
}

export async function counts(DB, novel, chapter, size = 3) {
  const { results } = await DB.prepare(
    "SELECT choice, COUNT(*) AS c FROM pred_votes WHERE novel = ?1 AND chapter = ?2 GROUP BY choice",
  ).bind(novel, chapter).all();
  const out = Array(size).fill(0);
  for (const r of results) if (r.choice < size) out[r.choice] = r.c;
  return out;
}
