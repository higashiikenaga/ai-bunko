// GET /api/views … 人間による閲覧数(日間=今日 / 週間=直近7日 / 累計)
import { db, jstDay, json, notConfigured } from "../../lib/db.js";

export async function onRequestGet({ env }) {
  const DB = await db(env);
  if (!DB) return notConfigured();
  const { results } = await DB.prepare(
    `SELECT novel,
            SUM(CASE WHEN day = ?1 THEN count ELSE 0 END) AS day,
            SUM(CASE WHEN day >= ?2 THEN count ELSE 0 END) AS week,
            SUM(count) AS total
       FROM views GROUP BY novel`,
  ).bind(jstDay(), jstDay(6)).all();
  const views = {};
  for (const r of results) views[r.novel] = { day: r.day, week: r.week, total: r.total };
  return json({ views }, 200, { "cache-control": "public, max-age=60" });
}
