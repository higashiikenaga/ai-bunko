// GET /api/ratings … 全作品の人間による評価の平均と件数
import { db, json, notConfigured } from "../../lib/db.js";

export async function onRequestGet({ env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  const { results } = await DB.prepare(
    "SELECT novel, AVG(score) AS avg, COUNT(*) AS count FROM votes GROUP BY novel",
  ).all();
  const ratings = {};
  for (const r of results) ratings[r.novel] = { avg: Math.round(r.avg * 10) / 10, count: r.count };
  return json({ ratings }, 200, { "cache-control": "public, max-age=30" });
}
