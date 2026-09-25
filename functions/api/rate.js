// POST /api/rate  { "novel": "20260925-173803", "score": 1〜5 } … 人間による★評価(つけ直し可)
import { NOVEL_ID, db, json, notConfigured, novelExists, readBody, sameOrigin, voterHash } from "../../lib/db.js";

export async function onRequestPost({ request, env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  if (!sameOrigin(request)) return json({ error: "不正なリクエストです" }, 403);

  const body = await readBody(request);
  const novel = String(body?.novel || "");
  const score = Number(body?.score);
  if (!NOVEL_ID.test(novel) || !Number.isInteger(score) || score < 1 || score > 5) {
    return json({ error: "評価は1〜5の整数で送ってください" }, 400);
  }
  if (!(await novelExists(env, request.url, novel))) return json({ error: "作品が見つかりません" }, 404);

  const voter = await voterHash(env, request, novel);
  await DB.prepare(
    `INSERT INTO votes (novel, voter, score, updated_at) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT(novel, voter) DO UPDATE SET score = excluded.score, updated_at = excluded.updated_at`,
  ).bind(novel, voter, score, Date.now()).run();

  const row = await DB.prepare("SELECT AVG(score) AS avg, COUNT(*) AS count FROM votes WHERE novel = ?1")
    .bind(novel).first();
  return json({ avg: Math.round(row.avg * 10) / 10, count: row.count, yours: score });
}
