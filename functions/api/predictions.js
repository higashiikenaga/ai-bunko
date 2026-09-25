// GET /api/predictions … 作品・話ごとの、人間による展開予想の票数 { "作品ID:話": [a, b, c] }
import { db, json, notConfigured } from "../../lib/db.js";

export async function onRequestGet({ env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  const { results } = await DB.prepare(
    "SELECT novel, chapter, choice, COUNT(*) AS c FROM pred_votes GROUP BY novel, chapter, choice",
  ).all();
  const votes = {};
  for (const r of results) {
    const key = `${r.novel}:${r.chapter}`;
    (votes[key] ||= [0, 0, 0])[r.choice] = r.c;
  }
  return json({ votes }, 200, { "cache-control": "public, max-age=30" });
}
